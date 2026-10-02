from onecpase.collections_engine import (
    classify_ptp,
    refresh_case_workflow,
    supersede_open_ptps,
    sync_collection_case,
)
from onecpase.database import get_db
from onecpase.ptp import _run_collections_automations


def test_classify_ptp():
    day = "2026-10-10"
    assert classify_ptp(500, 0, day, today="2026-10-05") == "ACTIVE"
    assert classify_ptp(500, 0, day, today="2026-10-10") == "DUE"
    assert classify_ptp(500, 0, day, today="2026-10-11") == "BROKEN"
    assert classify_ptp(500, 200, day, today="2026-10-11") == "PARTIALLY_HONOURED"
    assert classify_ptp(500, 500, day, today="2026-10-05") == "HONOURED"
    assert classify_ptp(500, 600, day, today="2026-10-20") == "HONOURED"


def _member(db, mid):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, join_date, gym_access_status)
           VALUES (?, 'Ptp', ?, ?, ?, 'Active', '2025-01-01', 'allowed')""",
        (f"PTP-{mid}", f"T{mid}", f"ID-PTP-{mid}", f"0750000{mid:03d}"),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _ptp(db, mid, promise_date, status="pending", amount=500):
    cursor = db.execute(
        """INSERT INTO ptp_agreements (member_id, promise_amount, promise_date, payment_method, ptp_status)
           VALUES (?, ?, ?, 'eft', ?)""",
        (mid, amount, promise_date, status),
    )
    return cursor.lastrowid


def test_new_promise_supersedes_open_ones_and_keeps_history(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 1)
        old_pending = _ptp(db, mid, "2999-01-01")
        old_partial = _ptp(db, mid, "2999-01-01", status="partially_paid")
        old_broken = _ptp(db, mid, "2020-01-01", status="broken")
        new = _ptp(db, mid, "2999-02-01")
        assert supersede_open_ptps(db, mid, new) == 2
        statuses = {r["id"]: r["ptp_status"] for r in db.execute("SELECT id, ptp_status FROM ptp_agreements")}
        assert statuses == {old_pending: "superseded", old_partial: "superseded",
                            old_broken: "broken", new: "pending"}


def test_superseded_promise_is_not_broken_by_the_daily_sweep(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 2)
        old = _ptp(db, mid, "2020-01-01")
        new = _ptp(db, mid, "2999-01-01")
        supersede_open_ptps(db, mid, new)
        db.commit()
        _run_collections_automations(db, "2026-10-05")
        statuses = {r["id"]: r["ptp_status"] for r in db.execute("SELECT id, ptp_status FROM ptp_agreements")}
        assert statuses[old] == "superseded" and statuses[new] == "pending"


def test_daily_sweep_breaks_missed_promise_and_moves_the_case(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 3)
        case_id = sync_collection_case(db, mid, failed_debit_date="2026-09-01",
                                       arrears_amount=500, months_owing=1)
        _ptp(db, mid, "2026-09-20")
        refresh_case_workflow(db, case_id)
        assert db.execute("SELECT collection_stage FROM collections_cases WHERE id=?", (case_id,)).fetchone()[0] == "PTP_DUE"
        db.commit()

        _run_collections_automations(db, "2026-10-05")
        db.commit()

        assert db.execute("SELECT ptp_status FROM ptp_agreements WHERE member_id=?", (mid,)).fetchone()[0] == "broken"
        case = db.execute("SELECT collection_stage, priority, next_action FROM collections_cases WHERE id=?", (case_id,)).fetchone()
        assert (case["collection_stage"], case["priority"], case["next_action"]) == ("PTP_BROKEN", 1, "FOLLOW_BROKEN_PTP")
        history = db.execute(
            "SELECT to_stage, source FROM collection_stage_history WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,)
        ).fetchone()
        assert (history["to_stage"], history["source"]) == ("PTP_BROKEN", "ptp_monitor")
