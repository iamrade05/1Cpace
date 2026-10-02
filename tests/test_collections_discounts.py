import pytest

from onecpase.collections_engine import (
    add_collection_note,
    apply_case_ownership,
    create_collection_exception,
    decide_collection_exception,
    record_discount_request,
    resubmit_discount_request,
    return_collection_exception,
    return_discount_request,
    settle_discount_request,
    supersede_open_ptps,
    sync_collection_case,
)
from onecpase.database import get_db


def _member(db, mid, join="2025-01-01", consultant=None):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, join_date, gym_access_status)
           VALUES (?, 'Disc', ?, ?, ?, 'Active', ?, 'allowed')""",
        (f"DSC-{mid}", f"T{mid}", f"ID-DSC-{mid}", f"0710000{mid:03d}", join),
    )
    member_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    if consultant:
        db.execute("INSERT INTO membership_applications (member_id, created_by) VALUES (?, ?)", (member_id, consultant))
    return member_id


def _ptp(db, mid, pct=25, approval="pending", arrears=1000):
    discount = arrears * pct / 100
    cursor = db.execute(
        """INSERT INTO ptp_agreements (member_id, arrears_amount, promise_amount, promise_date, payment_method,
               ptp_status, manager_approval_status, discount_pct, discount_amount, discounted_balance,
               upfront_amount, created_by)
           VALUES (?, ?, 500, '2999-01-01', 'eft', 'pending', ?, ?, ?, ?, 225, 1)""",
        (mid, arrears, approval, pct, discount, arrears - discount))
    return cursor.lastrowid


def _status(db, ptp_id):
    return db.execute("SELECT status FROM collection_discount_requests WHERE ptp_id=?", (ptp_id,)).fetchone()["status"]


def _login(client, role="admin"):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = role
        session["username"] = "admin"


# ── Notes ────────────────────────────────────────────────────────────────────

def test_notes_are_typed_and_system_notes_cannot_be_forged(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 1)
        case_id = sync_collection_case(db, mid, failed_debit_date="2026-10-01", arrears_amount=100, months_owing=1)
        note = add_collection_note(db, mid, "dispute", "Says the debit was cancelled", staff_id=1)
        row = db.execute("SELECT * FROM collection_notes WHERE id=?", (note,)).fetchone()
        assert (row["note_type"], row["staff_id"], row["case_id"]) == ("DISPUTE", 1, case_id)
        with pytest.raises(ValueError):
            add_collection_note(db, mid, "SYSTEM", "Automatically approved", staff_id=1)   # staff cannot write SYSTEM
        with pytest.raises(ValueError):
            add_collection_note(db, mid, "nonsense", "x", staff_id=1)
        with pytest.raises(ValueError):
            add_collection_note(db, mid, "GENERAL", "   ", staff_id=1)
        system = add_collection_note(db, mid, "HANDOVER", "Moved", staff_id=None)
        assert db.execute("SELECT staff_id, note_type FROM collection_notes WHERE id=?", (system,)).fetchone()[:] == (None, "HANDOVER")


def test_handover_writes_a_system_note(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 2, join="2026-08-20", consultant=1)
        db.execute("""INSERT INTO collections (member_id, outstanding_balance, amount_paid, collection_date, status, notes)
                      VALUES (?, 500, 0, '2026-09-01', 'failed', 'type=Recurring Fee')""", (mid,))
        case_id = sync_collection_case(db, mid, failed_debit_date="2026-09-01", arrears_amount=500, months_owing=1)
        apply_case_ownership(db, case_id, months_owing=2)
        note = db.execute("SELECT * FROM collection_notes WHERE case_id=? AND note_type='HANDOVER'", (case_id,)).fetchone()
        assert note and note["staff_id"] is None and "second-month arrears threshold" in note["note_text"]


# ── Discount requests ────────────────────────────────────────────────────────

def test_request_keeps_original_balance_beside_the_adjusted_one(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 3)
        ptp = _ptp(db, mid, pct=25)
        rid = record_discount_request(db, ptp)
        assert record_discount_request(db, ptp) == rid                    # idempotent
        row = db.execute("SELECT * FROM collection_discount_requests WHERE id=?", (rid,)).fetchone()
        assert (row["original_balance"], row["discount_amount"], row["adjusted_balance"]) == (1000, 250, 750)
        assert (row["requested_percentage"], row["proposed_upfront"], row["status"]) == (25, 225, "PENDING")
        assert record_discount_request(db, _ptp(db, mid, pct=0)) is None  # no discount, no request


def test_auto_approved_discount_is_recorded_as_approved(app):
    with app.app_context():
        db = get_db()
        ptp = _ptp(db, _member(db, 5), approval="not_required")
        record_discount_request(db, ptp)
        assert _status(db, ptp) == "APPROVED"


def test_manager_decisions_settle_the_request_with_who_and_why(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 6)
        ptp = _ptp(db, mid)
        record_discount_request(db, ptp)
        exc = create_collection_exception(db, mid, "Rule 7", ptp_id=ptp)
        decide_collection_exception(db, exc, "declined", 9, "Not affordable")
        row = db.execute("SELECT * FROM collection_discount_requests WHERE ptp_id=?", (ptp,)).fetchone()
        assert (row["status"], row["decided_by"], row["decision_reason"]) == ("REJECTED", 9, "Not affordable")
        note = db.execute("SELECT note_text FROM collection_notes WHERE member_id=? AND note_type='APPROVAL'", (mid,)).fetchone()
        assert "rejected" in note["note_text"].lower()
        # A decided request is not decided twice.
        assert settle_discount_request(db, ptp, "approved", 9, "late") == 0
        assert _status(db, ptp) == "REJECTED"


def test_return_for_information_and_resubmit(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 7)
        ptp = _ptp(db, mid)
        rid = record_discount_request(db, ptp)
        exc = create_collection_exception(db, mid, "Rule 7", ptp_id=ptp)
        with pytest.raises(ValueError):
            return_collection_exception(db, exc, 9, "  ")
        return_collection_exception(db, exc, 9, "Send proof of income")
        assert _status(db, ptp) == "RETURNED"
        assert db.execute("SELECT manager_approval_status FROM ptp_agreements WHERE id=?", (ptp,)).fetchone()[0] == "returned"
        assert db.execute("SELECT status FROM collection_exceptions WHERE id=?", (exc,)).fetchone()[0] == "returned"

        assert resubmit_discount_request(db, rid, by=1) is True
        assert _status(db, ptp) == "PENDING"
        assert db.execute("SELECT manager_approval_status FROM ptp_agreements WHERE id=?", (ptp,)).fetchone()[0] == "pending"
        assert db.execute("SELECT status FROM collection_exceptions WHERE id=?", (exc,)).fetchone()[0] == "pending"
        assert resubmit_discount_request(db, rid, by=1) is False          # only a returned request


def test_new_promise_cancels_the_old_promises_open_request(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 8)
        old = _ptp(db, mid)
        record_discount_request(db, old)
        new = _ptp(db, mid)
        supersede_open_ptps(db, mid, new)
        assert _status(db, old) == "CANCELLED"


# ── Routes ───────────────────────────────────────────────────────────────────

def test_manager_can_return_an_exception_only_with_a_reason(app, client):
    with app.app_context():
        db = get_db()
        mid = _member(db, 9)
        ptp = _ptp(db, mid)
        record_discount_request(db, ptp)
        exc = create_collection_exception(db, mid, "Rule 7", ptp_id=ptp)
        db.commit()
    _login(client)
    client.post(f"/collections/exceptions/{exc}/decide", data={"decision": "returned", "notes": ""})
    with app.app_context():
        assert get_db().execute("SELECT status FROM collection_exceptions WHERE id=?", (exc,)).fetchone()[0] == "pending"
    client.post(f"/collections/exceptions/{exc}/decide", data={"decision": "returned", "notes": "Need payslip"})
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT status FROM collection_exceptions WHERE id=?", (exc,)).fetchone()[0] == "returned"
        assert _status(db, ptp) == "RETURNED"


def test_profile_page_notes_and_resubmit_routes(app, client):
    with app.app_context():
        db = get_db()
        mid = _member(db, 10)
        ptp = _ptp(db, mid)
        rid = record_discount_request(db, ptp)
        return_discount_request(db, ptp, 9, "Need ID copy")
        db.commit()
    _login(client)
    client.post(f"/collections/member/{mid}/notes", data={"note_type": "DISPUTE", "note_text": "Disputes the amount"})
    client.post(f"/collections/member/{mid}/notes", data={"note_type": "SYSTEM", "note_text": "forged"})
    page = client.get(f"/collections/member/{mid}").data
    assert b"Disputes the amount" in page and b"forged" not in page
    assert b"Resubmit" in page and b"Need ID copy" in page and b"Discount requested" in page
    client.post(f"/collections/discount-requests/{rid}/resubmit")
    with app.app_context():
        assert _status(get_db(), ptp) == "PENDING"
