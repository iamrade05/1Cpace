from pathlib import Path
from unittest.mock import MagicMock, patch

from pdfminer.pdfdocument import PDFPasswordIncorrect

from onecpase.database import get_db
from onecpase.statement_parser import _is_password_error, analyse_pdf


def _login(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "testuser"
        session["role"] = "admin"
        session["permissions"] = []


def _fake_pdf(text: str):
    """A pdfplumber.open(...) context-manager stand-in with one page of text."""
    page = MagicMock()
    page.extract_text.return_value = text
    pdf = MagicMock()
    pdf.pages = [page]
    pdf.__enter__.return_value = pdf
    pdf.__exit__.return_value = False
    return pdf


# ── Password detection — the actual gap this port closed ────────────────────

def test_is_password_error_recognises_the_real_pdfminer_exception():
    assert _is_password_error(PDFPasswordIncorrect()) is True


def test_is_password_error_recognises_a_wrapped_exception():
    wrapped = RuntimeError("could not decrypt")
    wrapped.__cause__ = PDFPasswordIncorrect()
    assert _is_password_error(wrapped) is True


def test_is_password_error_false_for_unrelated_exceptions():
    assert _is_password_error(ValueError("not a password issue")) is False


def test_analyse_pdf_reports_friendly_message_when_password_required(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    with patch("onecpase.statement_parser.pdfplumber.open", side_effect=PDFPasswordIncorrect()):
        result = analyse_pdf(str(pdf_path))

    assert result["error"] is not None
    assert "password-protected" in result["error"].lower()


def test_analyse_pdf_reports_incorrect_password_when_one_was_supplied(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    with patch("onecpase.statement_parser.pdfplumber.open", side_effect=PDFPasswordIncorrect()):
        result = analyse_pdf(str(pdf_path), pdf_password="wrong-guess")

    assert result["error"] is not None
    assert "incorrect" in result["error"].lower()


def test_analyse_pdf_passes_the_password_through_to_pdfplumber(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    with patch("onecpase.statement_parser.pdfplumber.open", return_value=_fake_pdf("no useful text")) as opener:
        analyse_pdf(str(pdf_path), pdf_password="correct-horse")

    assert opener.call_args.kwargs.get("password") == "correct-horse"


def test_analyse_pdf_unrelated_read_errors_are_not_mistaken_for_a_password_prompt(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    with patch("onecpase.statement_parser.pdfplumber.open", side_effect=ValueError("corrupt PDF")):
        result = analyse_pdf(str(pdf_path))

    assert "password" not in result["error"].lower()


# ── Affordability scoring — real logic, no coverage existed before ──────────
#
# Amounts use a thousands separator (e.g. "7,500.00") deliberately: _AMT_RE's
# digit-group pattern only allows more than 3 digits before the decimal point
# when they're comma/space-separated, matching real SA statement formatting.
# A plain "7500.00" would parse as "500.00" — not a bug, just not the shape
# the regex (in both this file and the reference app, verified identical) was
# ever built to expect.

_STATEMENT_TEXT = """\
Standard Bank Statement
01 Jan 24 Opening Balance 5,000.00
05 Jan 24 Salary
IB PAYMENT FROM EMPLOYER
7,500.00 12,500.00
10 Jan 24 DEBIT ORDER GYM 500.00 12,000.00
15 Jan 24 POS PURCHASE 300.00 11,700.00
"""


def _fake_pdf_path(tmp_path):
    path = tmp_path / "statement.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return path


def test_analyse_pdf_detects_salary_and_rates_a_small_installment_good(tmp_path):
    pdf_path = _fake_pdf_path(tmp_path)

    with patch("onecpase.statement_parser.pdfplumber.open", return_value=_fake_pdf(_STATEMENT_TEXT)):
        result = analyse_pdf(str(pdf_path), monthly_installment=300)

    assert result["error"] is None
    assert result["income_detected"] is True
    assert result["estimated_income"] == 7500.0
    assert result["rating"] == "good"
    assert result["can_afford"] is True


def test_analyse_pdf_rates_a_large_installment_as_risk(tmp_path):
    pdf_path = _fake_pdf_path(tmp_path)

    with patch("onecpase.statement_parser.pdfplumber.open", return_value=_fake_pdf(_STATEMENT_TEXT)):
        result = analyse_pdf(str(pdf_path), monthly_installment=3000)  # 40% of income

    assert result["rating"] == "risk"
    assert result["can_afford"] is False


def test_analyse_pdf_moderate_band_tolerates_up_to_two_returned_debits(tmp_path):
    pdf_path = _fake_pdf_path(tmp_path)
    text = _STATEMENT_TEXT + "20 Jan 24 DEBIT ORDER RETURNED - INSUFFICIENT FUNDS 500.00 11,200.00\n"

    with patch("onecpase.statement_parser.pdfplumber.open", return_value=_fake_pdf(text)):
        result = analyse_pdf(str(pdf_path), monthly_installment=1500)  # ratio 0.20 -> moderate band

    assert result["returned_debits"] == 1
    assert result["rating"] == "moderate"
    assert result["can_afford"] is True


def test_analyse_pdf_moderate_band_becomes_risk_past_two_returned_debits(tmp_path):
    pdf_path = _fake_pdf_path(tmp_path)
    text = _STATEMENT_TEXT + (
        "20 Jan 24 DEBIT ORDER RETURNED - INSUFFICIENT FUNDS 500.00 11,200.00\n"
        "21 Jan 24 DEBIT ORDER UNPAID 500.00 10,700.00\n"
        "22 Jan 24 DEBIT ORDER DISHONOURED 500.00 10,700.00\n"
    )

    with patch("onecpase.statement_parser.pdfplumber.open", return_value=_fake_pdf(text)):
        result = analyse_pdf(str(pdf_path), monthly_installment=1500)  # same ratio, more risk events

    assert result["returned_debits"] == 3
    assert result["rating"] == "risk"
    assert result["can_afford"] is False


def test_analyse_pdf_handles_no_extractable_text(tmp_path):
    pdf_path = _fake_pdf_path(tmp_path)

    with patch("onecpase.statement_parser.pdfplumber.open", return_value=_fake_pdf("")):
        result = analyse_pdf(str(pdf_path))

    assert result["error"] is not None
    assert "scanned" in result["error"].lower() or "no text" in result["error"].lower()


# ── The member document-analysis route passes the password through ──────────

def test_member_analyse_doc_route_forwards_the_submitted_password(client, app):
    _login(client)
    with app.app_context():
        db = get_db()
        member_id = db.execute(
            "INSERT INTO members (first_name, last_name, id_number, email, member_status) "
            "VALUES ('Ada', 'Lovelace', '8001015009087', 'ada@example.com', 'Active')"
        ).lastrowid
        upload_dir = Path(app.config["UPLOAD_FOLDER"]) / "members" / str(member_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        (upload_dir / "statement.pdf").write_bytes(b"%PDF-1.4 fake")
        doc_id = db.execute(
            "INSERT INTO member_documents (member_id, document_type, original_name, stored_name, file_size) "
            "VALUES (?, 'Bank Statement', 'statement.pdf', 'statement.pdf', 10)",
            (member_id,),
        ).lastrowid
        db.commit()

    with patch(
        "onecpase.statement_parser.analyse_pdf",
        return_value={"error": None, "estimated_income": 5000, "rating": "good"},
    ) as mock_analyse:
        resp = client.post(
            f"/members/{member_id}/documents/{doc_id}/analyse",
            data={"pdf_password": "secret123"},
        )

    assert resp.status_code == 302
    assert mock_analyse.call_args.kwargs.get("pdf_password") == "secret123"
