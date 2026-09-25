"""_run_collections_automations' broken-PTP detection (onecpase/ptp.py).

Used to be entirely raw SQL. It now fetches the candidate rows and asks
collections_engine.evaluate_ptp() (previously dead code - zero callers
anywhere) for the actual BROKEN/ACTIVE/PAID decision, one row at a time.
"""
from datetime import date

from onecpase.database import get_db, member_activity  # noqa: F401  (member_activity used by ptp.py internally)
from onecpase.ptp import _run_collections_automations


def _seed_overdue_unpaid_ptp(db):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact, member_status, gym_access_status)
           VALUES ('T-PTP', 'Test', 'Member', 'IDPTP', '0710000002', 'Active', 'allowed')"""
    )
    mid = db.execute("SELECT id FROM members WHERE member_ref='T-PTP'").fetchone()["id"]
    cur = db.execute(
        """INSERT INTO ptp_agreements
           (member_id, created_by, promise_amount, promise_date, payment_method,
            ptp_status, auto_block_if_failed)
           VALUES (?, 1, 500, '2026-09-01', 'cash', 'pending', 0)""",
        (mid,),
    )
    db.commit()
    return mid, cur.lastrowid


def test_broken_ptp_detection_delegates_to_evaluate_ptp(app, monkeypatch):
    from onecpase import ptp as ptp_module

    with app.app_context():
        db = get_db()
        mid, ptp_id = _seed_overdue_unpaid_ptp(db)

        # Today's real evaluate_ptp would call this BROKEN (promise date has
        # passed, no verified receipt). Force it to say ACTIVE instead and
        # confirm the automation actually asked it, rather than deciding for
        # itself in SQL.
        monkeypatch.setattr(ptp_module, "evaluate_ptp", lambda *a, **k: "ACTIVE")
        counts = ptp_module._run_collections_automations(db, "2026-09-22")
        assert counts["broken_count"] == 0
        status = db.execute("SELECT ptp_status FROM ptp_agreements WHERE id=?", (ptp_id,)).fetchone()
        assert status["ptp_status"] == "pending"


def test_broken_ptp_detection_still_marks_broken_for_real(app):
    # Behavioral regression guard, unpatched: the wiring change must not
    # break the actual outcome for a genuinely overdue, unpaid PTP.
    with app.app_context():
        db = get_db()
        mid, ptp_id = _seed_overdue_unpaid_ptp(db)

        counts = _run_collections_automations(db, "2026-09-22")
        assert counts["broken_count"] == 1
        status = db.execute("SELECT ptp_status FROM ptp_agreements WHERE id=?", (ptp_id,)).fetchone()
        assert status["ptp_status"] == "broken"


def test_dry_run_still_makes_no_writes(app):
    with app.app_context():
        db = get_db()
        mid, ptp_id = _seed_overdue_unpaid_ptp(db)

        counts = _run_collections_automations(db, "2026-09-22", dry_run=True)
        assert counts["broken_count"] == 1
        status = db.execute("SELECT ptp_status FROM ptp_agreements WHERE id=?", (ptp_id,)).fetchone()
        assert status["ptp_status"] == "pending"  # unchanged - dry run only counts
