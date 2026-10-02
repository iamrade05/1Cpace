from onecpase.collections_engine import reconcile_open_cases, sync_collection_case
from onecpase.database import get_db
from onecpase.ptp import _run_collections_automations


def _member(db, mid):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, join_date, gym_access_status)
           VALUES (?, 'Res', ?, ?, ?, 'Active', '2025-01-01', 'allowed')""",
        (f"RES-{mid}", f"T{mid}", f"ID-RES-{mid}", f"0760000{mid:03d}"),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _charge(db, mid, month, amount=500, paid=0, status="failed"):
    cursor = db.execute(
        """INSERT INTO collections (member_id, outstanding_balance, amount_paid,
               collection_date, status, notes)
           VALUES (?, ?, ?, ?, ?, 'type=Recurring Fee')""",
        (mid, amount, paid, f"{month}-01", status),
    )
    return cursor.lastrowid


def _case(db, mid, months=1, amount=500):
    case_id = sync_collection_case(db, mid, failed_debit_date="2026-09-01",
                                   arrears_amount=amount, months_owing=months)
    db.commit()
    return case_id


def _row(db, case_id):
    return db.execute("SELECT * FROM collections_cases WHERE id=?", (case_id,)).fetchone()


def test_payment_that_clears_the_balance_resolves_the_case(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 1)
        charge = _charge(db, mid, "2026-09")
        case_id = _case(db, mid)
        db.execute("UPDATE collections SET amount_paid=500, status='paid' WHERE id=?", (charge,))

        assert reconcile_open_cases(db, [mid], changed_by=7) == 1
        db.commit()

        case = _row(db, case_id)
        assert case["status"] == "closed"
        assert case["resolution_type"] == "PAID_IN_FULL"
        assert case["resolved_by"] == 7 and case["closed_at"]
        assert case["collection_stage"] == "RESOLVED"
        last = db.execute(
            "SELECT to_stage, source FROM collection_stage_history WHERE case_id=? ORDER BY id DESC LIMIT 1",
            (case_id,)).fetchone()
        assert (last["to_stage"], last["source"]) == ("RESOLVED", "payment")


def test_partial_payment_keeps_the_case_open_with_the_new_balance(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 2)
        charge = _charge(db, mid, "2026-09")
        case_id = _case(db, mid)
        db.execute("UPDATE collections SET amount_paid=200, status='partial' WHERE id=?", (charge,))

        assert reconcile_open_cases(db, [mid]) == 0
        case = _row(db, case_id)
        assert case["status"] == "open"
        assert case["arrears_amount"] == 300


def test_resolved_case_is_kept_and_a_new_failure_opens_a_new_case(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 3)
        charge = _charge(db, mid, "2026-09")
        first = _case(db, mid)
        db.execute("UPDATE collections SET amount_paid=500, status='paid' WHERE id=?", (charge,))
        reconcile_open_cases(db, [mid])
        _charge(db, mid, "2026-10")
        second = _case(db, mid)
        assert second != first
        assert _row(db, first)["status"] == "closed"
        assert _row(db, second)["status"] == "open"


def test_daily_sweep_resolves_cases_paid_outside_the_app(app):
    """Imports and POS pushes change payments without touching the case."""
    with app.app_context():
        db = get_db()
        mid = _member(db, 4)
        charge = _charge(db, mid, "2026-09")
        case_id = _case(db, mid)
        db.execute("UPDATE collections SET amount_paid=500, status='paid' WHERE id=?", (charge,))
        db.commit()

        counts = _run_collections_automations(db, "2026-10-05")
        assert counts["cases_resolved"] == 1
        assert _row(db, case_id)["status"] == "closed"


def test_sales_case_still_transfers_when_recalculation_shows_two_months(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 5)
        db.execute("INSERT INTO membership_applications (member_id, created_by) VALUES (?, 1)", (mid,))
        db.execute("UPDATE members SET join_date='2026-08-20' WHERE id=?", (mid,))
        _charge(db, mid, "2026-08")
        case_id = _case(db, mid)
        assert _row(db, case_id)["owner_type"] == "SALES"
        _charge(db, mid, "2026-09")
        reconcile_open_cases(db, [mid])
        case = _row(db, case_id)
        assert case["months_owing"] >= 2
        assert case["owner_type"] == "RECEPTION"
