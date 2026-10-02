import pytest

from onecpase.collections_engine import (
    find_duplicate_collection,
    log_collection_communication,
    sync_collection_case,
)
from onecpase.database import get_db


def _member(db, mid):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, join_date, gym_access_status)
           VALUES (?, 'Dup', ?, ?, ?, 'Active', '2025-01-01', 'allowed')""",
        (f"DUP-{mid}", f"T{mid}", f"ID-DUP-{mid}", f"0780000{mid:03d}"),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _login(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["username"] = "admin"


def test_syncing_the_same_failed_debit_twice_keeps_one_open_case(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 1)
        first = sync_collection_case(db, mid, failed_debit_date="2026-10-01", arrears_amount=324, months_owing=1)
        second = sync_collection_case(db, mid, failed_debit_date="2026-10-01", arrears_amount=324, months_owing=1)
        db.commit()
        assert first == second
        assert db.execute("SELECT COUNT(*) FROM collections_cases WHERE member_id=?", (mid,)).fetchone()[0] == 1


def test_database_refuses_a_second_open_case_for_the_same_member(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 2)
        sync_collection_case(db, mid, failed_debit_date="2026-10-01", arrears_amount=100, months_owing=1)
        db.commit()
        with pytest.raises(Exception):
            db.execute("INSERT INTO collections_cases (member_id, status) VALUES (?, 'open')", (mid,))
        # A closed case does not count: a later failure may open a new one.
        db.execute("UPDATE collections_cases SET status='closed' WHERE member_id=?", (mid,))
        db.execute("INSERT INTO collections_cases (member_id, status) VALUES (?, 'open')", (mid,))


def test_same_contact_logged_twice_in_a_row_is_stored_once(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 3)
        case_id = sync_collection_case(db, mid, failed_debit_date="2026-10-01", arrears_amount=100, months_owing=1)
        a = log_collection_communication(db, mid, "call", case_id=case_id, outcome="no_answer", created_by=1)
        b = log_collection_communication(db, mid, "call", case_id=case_id, outcome="no_answer", created_by=1)
        c = log_collection_communication(db, mid, "call", case_id=case_id, outcome="busy", created_by=1)
        db.commit()
        assert a == b and c != a
        assert db.execute("SELECT COUNT(*) FROM collection_communications WHERE case_id=?", (case_id,)).fetchone()[0] == 2


def test_find_duplicate_collection_matches_identical_records_only(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 4)
        db.execute(
            """INSERT INTO collections (member_id, outstanding_balance, amount_paid, collection_date,
                   method, status, notes) VALUES (?, 324, 0, '2026-10-01', 'debicheck', 'failed', 'x')""", (mid,))
        args = (mid, "2026-10-01", "324", "0", "debicheck", "failed", "x")
        assert find_duplicate_collection(db, *args) is not None
        assert find_duplicate_collection(db, mid, "2026-10-01", "324", "100", "debicheck", "failed", "x") is None
        assert find_duplicate_collection(db, mid, "2026-10-02", "324", "0", "debicheck", "failed", "x") is None
        assert find_duplicate_collection(db, mid, "2026-10-01", "abc", "0", "debicheck", "failed", "x") is None


def test_resubmitting_the_add_collection_form_does_not_duplicate_the_record(app, client):
    with app.app_context():
        mid = _member(get_db(), 5)
        get_db().commit()
    _login(client)
    form = {"member_id": str(mid), "outstanding_balance": "324", "discount_pct": "0", "amount_paid": "0",
            "collection_date": "2026-10-01", "method": "debicheck", "status": "failed", "notes": "Debit failed"}
    client.post("/collections/add", data=form)
    again = client.post("/collections/add", data=form)
    assert again.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM collections WHERE member_id=?", (mid,)).fetchone()[0] == 1
        assert get_db().execute("SELECT COUNT(*) FROM collections_cases WHERE member_id=?", (mid,)).fetchone()[0] == 1
