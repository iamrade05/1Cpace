import io
import zipfile
from datetime import date

import pytest

from onecpase import nupay_reconciliation as rec
from onecpase.database import get_db
from tests.test_nupay_transactions import AMOUNT, FOUR_SUCCESSES, _charge, _member, _row

TODAY = date(2026, 10, 6)
HEADER = ("Mandate ID,Debtor ID,Instalment Amount,Action Date,Status,Client Reference,Instalment,"
          "Total Instalments,Cycle Date,Contract Reference\n")


def _csv(rows):
    lines = [HEADER]
    for r in rows:
        lines.append(",".join([r["Mandate ID"], r["Debtor ID"] or "", r["Instalment Amount"], r["Action Date"], r["Status"],
                               r["Client Reference"], r["Instalment"], r["Total Instalments"], r["Cycle Date"],
                               r["Contract Reference"]]) + "\n")
    return "".join(lines).encode("utf-8")


def _snapshot(db, roster=None):
    result = rec.rebuild_snapshot(db, roster, today=TODAY)
    db.commit()
    return result


def _group(db, mid):
    return db.execute("SELECT * FROM reconciliation_snapshot WHERE member_id=?", (mid,)).fetchone()


def _sign_in(client, role="manager", user_id=1, permissions=("all_collections", "all_members")):
    with client.session_transaction() as s:
        s.update({"user_id": user_id, "username": "u", "role": role, "permissions": list(permissions)})


def test_read_upload_takes_csv_files_and_zips_and_skips_the_rest():
    inner = _csv(FOUR_SUCCESSES[:2])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("Success_1.csv", inner)
        z.writestr("readme.txt", "ignore me")
    rows = rec.read_upload([("a.csv", inner), ("b.zip", buffer.getvalue()), ("c.pdf", b"%PDF")])
    assert len(rows) == 4


def test_storing_an_upload_keeps_each_debit_once(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 1, "1950298")
        rows = rec.read_upload([("a.csv", _csv(FOUR_SUCCESSES))])
        first = rec.store_upload(db, rows, ["a.csv"], None)
        again = rec.store_upload(db, rows, ["a.csv"], None)
        db.commit()
        assert first["new"] == 4 and again.get("new", 0) == 0 and again["already_stored"] == 4
        assert db.execute("SELECT COUNT(*) FROM nupay_transactions WHERE member_id=?", (mid,)).fetchone()[0] == 4


def test_unmatched_and_ignored_rows_are_counted_not_lost(app):
    with app.app_context():
        db = get_db()
        _member(db, 2, "1950298")
        rows = rec.read_upload([("a.csv", _csv([_row("9999999", "2026-09-18", 4), _row("1950298", "2026-10-20", 5, status="Future")]))])
        stats = rec.store_upload(db, rows, ["a.csv"], None)
        assert stats["unmatched"] == 1 and stats["ignored"] == 1


def test_comparing_never_writes_to_collections(app):
    with app.app_context():
        db = get_db()
        _member(db, 3, "1950298")
        rec.store_upload(db, rec.read_upload([("a.csv", _csv(FOUR_SUCCESSES))]), ["a.csv"], None)
        db.commit()
        before = db.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
        _snapshot(db)
        assert db.execute("SELECT COUNT(*) FROM collections").fetchone()[0] == before == 0


def test_a_paying_member_with_wrong_arrears_is_found_and_the_after_balance_is_zero(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 4, "1950298")
        for month in ("2026-06", "2026-07", "2026-08", "2026-09"):
            _charge(db, mid, f"{month}-20")
        db.commit()
        rec.store_upload(db, rec.read_upload([("a.csv", _csv(FOUR_SUCCESSES))]), ["a.csv"], None)
        _snapshot(db)
        snap = _group(db, mid)
        assert snap["grp"] == "paying_arrears_clear"
        assert round(snap["arrears_now"], 2) == round(4 * AMOUNT, 2) and snap["arrears_after"] == 0


def test_a_failing_member_is_a_collection_candidate(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 5, "1111111")
        _charge(db, mid, "2026-09-20")
        db.commit()
        rows = [_row("1111111", "2026-09-18", 4, status="Failed", cycle="2026-09-20")]
        rec.store_upload(db, rec.read_upload([("a.csv", _csv(rows))]), ["a.csv"], None)
        _snapshot(db)
        assert _group(db, mid)["grp"] == "failing_debits"


def test_overview_adds_up(app):
    with app.app_context():
        db = get_db()
        assert rec.overview(db)["ready"] is False
        mid = _member(db, 6, "1950298")
        _charge(db, mid, "2026-09-20")
        db.commit()
        rec.store_upload(db, rec.read_upload([("a.csv", _csv(FOUR_SUCCESSES))]), ["a.csv"], None)
        _snapshot(db)
        data = rec.overview(db)
        assert data["ready"] and data["members"] == 1
        assert sum(g["members"] for g in data["table"]) == 1
        assert data["wrong_count"] == 1 and data["runs"]


def test_member_view_explains_what_recording_would_change(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 7, "1950298")
        rec.store_upload(db, rec.read_upload([("a.csv", _csv(FOUR_SUCCESSES))]), ["a.csv"], None)
        _snapshot(db)
        view = rec.member_view(db, mid)
        assert view["paid_count"] == 4 and view["failed_count"] == 0
        assert round(view["paid_amount"], 2) == round(4 * AMOUNT, 2) and view["last_success"] == "2026-09-18"
        assert any("Record 4 NuPay payment" in text for _, text in view["changes"])
        assert rec.member_view(db, mid + 99) is None


# ── Screens ──────────────────────────────────────────────────────────────────

def test_overview_page_shows_the_upload_prompt_before_any_report(app, client):
    _sign_in(client)
    page = client.get("/collections/reconciliation")
    assert page.status_code == 200 and b"No NuPay report loaded yet" in page.data and b"Upload and check" in page.data


def test_a_manager_can_upload_and_the_pages_fill_in(app, client):
    with app.app_context():
        db = get_db()
        mid = _member(db, 8, "1950298")
        _charge(db, mid, "2026-09-20")
        db.commit()
    _sign_in(client)
    response = client.post(
        "/collections/reconciliation/upload",
        data={"reports": (io.BytesIO(_csv(FOUR_SUCCESSES)), "Success_1.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200 and b"Checked 1 members" in response.data
    assert b"Paying on NuPay, arrears clear" in response.data
    listing = client.get("/collections/reconciliation/members?group=paying_arrears_clear")
    assert listing.status_code == 200 and b"ELE-0000000008" in listing.data
    profile = client.get(f"/members/{mid}")
    assert profile.status_code == 200 and b"NuPay Payments" in profile.data and b"NuPay &bull; 4 paid" in profile.data


def test_only_managers_can_change_data(app, client):
    _sign_in(client, role="reception")
    response = client.post(
        "/collections/reconciliation/upload",
        data={"reports": (io.BytesIO(_csv(FOUR_SUCCESSES)), "Success_1.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Only a manager can upload" in response.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM nupay_transactions").fetchone()[0] == 0
    assert client.post("/collections/reconciliation/rebuild", follow_redirects=True).status_code == 200


def test_people_without_collections_access_are_turned_away(app, client):
    _sign_in(client, role="reception", permissions=())
    assert client.get("/collections/reconciliation").status_code == 302
    assert client.get("/collections/reconciliation/members").status_code == 302


def test_profile_has_no_nupay_section_until_nupay_has_something_to_say(app, client):
    with app.app_context():
        mid = _member(get_db(), 9, "1950298")
        get_db().commit()
    _sign_in(client)
    page = client.get(f"/members/{mid}")
    assert page.status_code == 200 and b"NuPay Payments" not in page.data


def test_member_list_pages_and_counts(app):
    with app.app_context():
        db = get_db()
        for n in range(1, 4):
            _member(db, 50 + n, f"77000{n}")
        db.commit()
        rec.store_upload(db, rec.read_upload([("a.csv", _csv([_row("770001", "2026-09-18", 4)]))]), ["a.csv"], None)
        _snapshot(db)
        rows, total = rec.member_list(db)
        assert total == 3 and len(rows) == 3
        rows, total = rec.member_list(db, group="paying_arrears_clear")
        assert total == 1 and len(rows) == 1
        rows, total = rec.member_list(db, page=2)
        assert total == 3 and rows == []
