from onecpase.collections_engine import (
    determine_collection_stage,
    determine_next_action,
    determine_priority,
    log_collection_communication,
    refresh_case_workflow,
    sync_collection_case,
)
from onecpase.database import get_db


def test_stage_precedence():
    stage = lambda **kw: determine_collection_stage(**{"months_owing": 1, **kw})
    assert stage() == "CONTACT_REQUIRED"
    assert stage(contact_attempts=2) == "CONTACTING"
    assert stage(contact_attempts=2, successful_contact=True) == "CONTACTED"
    assert stage(months_owing=2, successful_contact=True) == "ARRANGEMENT_REQUIRED"
    assert stage(ptp_status="pending") == "PTP_ACTIVE"
    assert stage(ptp_status="pending", ptp_due=True) == "PTP_DUE"
    assert stage(ptp_status="broken", approval_pending=True) == "PTP_BROKEN"
    assert stage(approval_pending=True, ptp_status="pending") == "APPROVAL_PENDING"
    assert stage(access_status="BLOCKED", ptp_status="pending") == "ACCESS_RESTRICTED"
    assert stage(months_owing=7) == "INACTIVE"
    assert stage(resolved=True, months_owing=9) == "RESOLVED"


def test_ageing_and_stage_are_independent():
    """3 months owing with an active PTP is PTP_ACTIVE, not just '3 months'."""
    assert determine_collection_stage(months_owing=3, ptp_status="pending") == "PTP_ACTIVE"


def test_priority_and_next_action():
    assert determine_priority("PTP_BROKEN", 3) == 1
    assert determine_priority("CONTACT_REQUIRED", 1) == 2
    assert determine_priority("PTP_ACTIVE", 3) == 2      # 2+ months stays high
    assert determine_priority("PTP_ACTIVE", 1) == 3
    assert determine_priority("INACTIVE", 8) == 3
    assert determine_next_action("PTP_BROKEN") == "FOLLOW_BROKEN_PTP"
    assert determine_next_action("MADE_UP") == "NO_ACTION"


def _member(db, mid):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, join_date, gym_access_status)
           VALUES (?, 'Stg', ?, ?, ?, 'Active', '2025-01-01', 'allowed')""",
        (f"STG-{mid}", f"T{mid}", f"ID-STG-{mid}", f"0740000{mid:03d}"),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _stages(db, case_id):
    return [r["to_stage"] for r in db.execute(
        "SELECT to_stage FROM collection_stage_history WHERE case_id=? ORDER BY id", (case_id,))]


def test_case_stage_is_recorded_and_follows_contacts_and_ptp(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 1)
        case_id = sync_collection_case(db, mid, failed_debit_date="2026-10-01",
                                       arrears_amount=324, months_owing=1)
        row = db.execute("SELECT * FROM collections_cases WHERE id=?", (case_id,)).fetchone()
        assert (row["collection_stage"], row["priority"], row["next_action"]) == ("CONTACT_REQUIRED", 2, "CALL_MEMBER")

        log_collection_communication(db, mid, "call", case_id=case_id, outcome="no_answer")
        assert db.execute("SELECT collection_stage FROM collections_cases WHERE id=?", (case_id,)).fetchone()[0] == "CONTACTING"

        log_collection_communication(db, mid, "call", case_id=case_id, outcome="promised_to_pay")
        assert db.execute("SELECT collection_stage FROM collections_cases WHERE id=?", (case_id,)).fetchone()[0] == "CONTACTED"

        db.execute(
            """INSERT INTO ptp_agreements (member_id, promise_amount, promise_date, payment_method, ptp_status)
               VALUES (?, 324, '2999-01-01', 'eft', 'pending')""", (mid,))
        refresh_case_workflow(db, case_id)
        db.execute("UPDATE ptp_agreements SET ptp_status='broken' WHERE member_id=?", (mid,))
        refresh_case_workflow(db, case_id, changed_by=5, source="ptp_monitor", reason="Promise missed")
        db.commit()

        assert _stages(db, case_id) == [
            "CONTACT_REQUIRED", "CONTACTING", "CONTACTED", "PTP_ACTIVE", "PTP_BROKEN"]
        last = db.execute(
            "SELECT * FROM collection_stage_history WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
        assert (last["from_stage"], last["changed_by"], last["source"]) == ("PTP_ACTIVE", 5, "ptp_monitor")
        assert db.execute("SELECT priority FROM collections_cases WHERE id=?", (case_id,)).fetchone()[0] == 1


def test_unchanged_stage_does_not_add_history(app):
    with app.app_context():
        db = get_db()
        case_id = sync_collection_case(db, _member(db, 2), failed_debit_date="2026-10-01",
                                       arrears_amount=100, months_owing=1)
        refresh_case_workflow(db, case_id)
        refresh_case_workflow(db, case_id)
        assert _stages(db, case_id) == ["CONTACT_REQUIRED"]
