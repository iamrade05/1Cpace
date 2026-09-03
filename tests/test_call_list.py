import io

import pytest

from onecpase.call_list_parser import map_response, normalize_phone, parse_ocr_lines
from onecpase.database import get_db


def _login(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "testuser"
        session["full_name"] = "Test User"
        session["role"] = "admin"
        session["permissions"] = []


# ── Pure parsing logic — no OCR engine involved ──────────────────────────────

def test_normalize_phone_corrects_common_ocr_character_errors():
    assert normalize_phone("O82 l23 4567") == "0821234567"   # O->0, l->1
    assert normalize_phone("+27 82 123 4567") == "0821234567"
    assert normalize_phone("821234567") == "0821234567"       # missing leading 0


def test_normalize_phone_rejects_unusable_input():
    assert normalize_phone("not a number") == ""
    assert normalize_phone("12") == ""


def test_map_response_recognises_common_outcomes():
    assert map_response("Not interested")[0] == "not_interested"
    assert map_response("No answer")[0] == "no_answer"
    assert map_response("left a voicemail")[0] == "voicemail"
    assert map_response("wrong number")[0] == "wrong_number"
    assert map_response("call back later")[0] == "call_back_later"
    assert map_response("random illegible text")[0] == "other"


def test_map_response_extracts_relative_appointment_dates():
    import datetime
    today = datetime.date(2026, 8, 25)
    outcome, appt_date = map_response("Appointment tomorrow", today=today)
    assert outcome == "appointment_set"
    assert appt_date == "2026-08-26"


def test_parse_ocr_lines_extracts_name_phone_and_response():
    lines = ["Jane Doe 0821234567 Not interested"]
    rows = parse_ocr_lines(lines)
    assert len(rows) == 1
    assert rows[0].client_name == "Jane Doe"
    assert rows[0].contact == "0821234567"
    assert rows[0].mapped_outcome == "not_interested"


def test_parse_ocr_lines_deduplicates_same_contact_and_name():
    lines = ["Jane Doe 0821234567 no answer", "Jane Doe 0821234567 no answer"]
    rows = parse_ocr_lines(lines)
    assert len(rows) == 1


def test_parse_ocr_lines_skips_lines_without_a_phone_number():
    rows = parse_ocr_lines(["Name Contact Response header row with no number"])
    assert rows == []


# ── Real OCR end-to-end (uses the actual Tesseract engine installed on this
#    machine — skipped automatically if it's ever unavailable) ───────────────

def _tesseract_available() -> bool:
    try:
        from onecpase.call_list_parser import _configure_tesseract_path
        _configure_tesseract_path()
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _make_call_list_image(path):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 200), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    draw.text((20, 20), "Ada Lovelace 0821234567 Appointment tomorrow 14:00", fill="black", font=font)
    img.save(path)


@pytest.mark.skipif(not _tesseract_available(), reason="Tesseract OCR engine not available on this machine")
def test_extract_call_list_reads_a_real_synthetic_image(tmp_path):
    from onecpase.call_list_parser import extract_call_list

    image_path = tmp_path / "call_list.png"
    _make_call_list_image(image_path)

    result = extract_call_list(image_path)
    assert result.page_count == 1
    assert len(result.rows) >= 1
    row = result.rows[0]
    assert "Lovelace" in row.client_name
    assert row.contact == "0821234567"
    assert row.mapped_outcome == "appointment_set"


# ── Upload -> review -> import, end to end through the real routes ──────────

@pytest.mark.skipif(not _tesseract_available(), reason="Tesseract OCR engine not available on this machine")
def test_upload_review_and_import_flow_creates_a_lead(client, app, tmp_path):
    _login(client)
    image_path = tmp_path / "call_list.png"
    _make_call_list_image(image_path)

    with open(image_path, "rb") as fh:
        resp = client.post(
            "/calls/upload",
            data={"document": (io.BytesIO(fh.read()), "call_list.png")},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 302
    assert "/calls/" in resp.headers["Location"]

    with app.app_context():
        db = get_db()
        upload_row = db.execute("SELECT * FROM call_list_uploads").fetchone()
        assert upload_row["status"] == "reviewing"
        assert upload_row["row_count"] >= 1
        staged = db.execute("SELECT * FROM call_list_upload_rows WHERE upload_id=?", (upload_row["id"],)).fetchall()
        assert len(staged) == upload_row["row_count"]
        row_id = staged[0]["id"]
        upload_id = upload_row["id"]

    resp = client.get(f"/calls/{upload_id}/review")
    assert resp.status_code == 200
    assert b"Lovelace" in resp.data

    resp = client.post(f"/calls/{upload_id}/import", data={"row_id": [str(row_id)]})
    assert resp.status_code == 302

    with app.app_context():
        db = get_db()
        staged_row = db.execute("SELECT * FROM call_list_upload_rows WHERE id=?", (row_id,)).fetchone()
        assert staged_row["imported_lead_id"] is not None
        lead = db.execute("SELECT * FROM leads WHERE id=?", (staged_row["imported_lead_id"],)).fetchone()
        assert "Lovelace" in lead["full_name"]
        assert lead["phone_normalized"] == "27821234567"
        activity = db.execute(
            "SELECT * FROM lead_activities WHERE lead_id=?", (lead["id"],)
        ).fetchone()
        assert activity["activity_type"] == "call"
        assert activity["outcome"] == "appointment_set"

        upload_row = db.execute("SELECT * FROM call_list_uploads WHERE id=?", (upload_id,)).fetchone()
        assert upload_row["imported_count"] == 1


def test_upload_rejects_unsupported_file_type(client):
    _login(client)
    resp = client.post(
        "/calls/upload",
        data={"document": (io.BytesIO(b"not a real doc"), "notes.txt")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"unsupported file type" in resp.data.lower()


def test_import_requires_at_least_one_selected_row(client, app):
    _login(client)
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO call_list_uploads (original_filename, stored_filename, file_type, file_sha256, status) "
            "VALUES ('x.png', 'x.png', 'png', 'deadbeef', 'reviewing')"
        )
        db.commit()
        upload_id = db.execute("SELECT id FROM call_list_uploads").fetchone()["id"]

    resp = client.post(f"/calls/{upload_id}/import", data={}, follow_redirects=True)
    assert b"select at least one row" in resp.data.lower()


def test_uploading_the_same_file_twice_reuses_the_existing_upload(client, tmp_path):
    _login(client)
    from PIL import Image
    image_path = tmp_path / "dup.png"
    Image.new("RGB", (100, 60), color="white").save(image_path)

    with open(image_path, "rb") as fh:
        content = fh.read()

    first = client.post(
        "/calls/upload", data={"document": (io.BytesIO(content), "dup.png")}, content_type="multipart/form-data"
    )
    first_location = first.headers["Location"]

    second = client.post(
        "/calls/upload", data={"document": (io.BytesIO(content), "dup.png")}, content_type="multipart/form-data"
    )
    assert second.headers["Location"] == first_location
