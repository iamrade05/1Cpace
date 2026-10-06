from onecpase.collections_engine import (
    apply_case_ownership,
    determine_owner,
    sync_collection_case,
    transfer_due_sales_cases,
)
from onecpase.database import get_db

CONSULTANT = 1  # the seeded admin user stands in for the signing consultant


def _member(db, mid, join_date="2026-09-20"):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, join_date, gym_access_status)
           VALUES (?, 'Own', ?, ?, ?, 'Active', ?, 'allowed')""",
        (f"OWN-{mid}", f"T{mid}", f"ID-OWN-{mid}", f"0720000{mid:03d}", join_date),
    )
    member_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        "INSERT INTO membership_applications (member_id, created_by) VALUES (?, ?)",
        (member_id, CONSULTANT),
    )
    return member_id


def _case(db, member_id, failed="2026-10-01", months=1):
    case_id = sync_collection_case(
        db, member_id, failed_debit_date=failed, arrears_amount=324, months_owing=months,
    )
    db.commit()
    return db.execute("SELECT * FROM collections_cases WHERE id=?", (case_id,)).fetchone()


def test_determine_owner_rules():
    assert determine_owner(months_owing=1, first_debit_failure=True,
                           original_sales_consultant_id=7)[:2] == ("SALES", 7)
    assert determine_owner(months_owing=2, first_debit_failure=True,
                           original_sales_consultant_id=7)[0] == "RECEPTION"
    assert determine_owner(months_owing=1, first_debit_failure=False,
                           original_sales_consultant_id=7)[0] == "RECEPTION"
    # No identifiable consultant: never leave the case unowned.
    assert determine_owner(months_owing=1, first_debit_failure=True,
                           original_sales_consultant_id=None)[0] == "RECEPTION"


def test_new_member_first_debit_failure_goes_to_signing_consultant(app):
    with app.app_context():
        db = get_db()
        case = _case(db, _member(db, 1))
        assert case["owner_type"] == "SALES"
        assert case["owner_staff_id"] == CONSULTANT
        assert case["original_sales_consultant_id"] == CONSULTANT
        history = db.execute(
            "SELECT * FROM collection_assignment_history WHERE case_id=?", (case["id"],)
        ).fetchall()
        assert [h["reason"] for h in history] == ["FIRST_MONTH_FAILED_DEBIT"]


def test_old_member_one_month_behind_does_not_go_to_sales(app):
    with app.app_context():
        db = get_db()
        case = _case(db, _member(db, 2, join_date="2025-01-10"))
        assert case["owner_type"] == "RECEPTION"
        assert case["assignment_reason"] == "MEMBERSHIP_OVER_FIRST_DEBIT_WINDOW"


def test_member_with_earlier_recurring_payment_is_not_first_debit(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 3)
        db.execute(
            """INSERT INTO collections (member_id, outstanding_balance, amount_paid,
                   collection_date, status, notes)
               VALUES (?, 324, 324, '2026-09-25', 'paid', 'type=Recurring Fee')""",
            (mid,),
        )
        assert _case(db, mid)["owner_type"] == "RECEPTION"


def test_second_month_transfers_same_case_and_keeps_history(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 4)
        case = _case(db, mid)
        assert apply_case_ownership(db, case["id"], months_owing=2) == "RECEPTION"
        db.commit()
        after = db.execute("SELECT * FROM collections_cases WHERE id=?", (case["id"],)).fetchone()
        assert after["owner_type"] == "RECEPTION"
        assert after["original_sales_consultant_id"] == CONSULTANT
        reasons = [h["reason"] for h in db.execute(
            "SELECT reason FROM collection_assignment_history WHERE case_id=? ORDER BY id",
            (case["id"],)).fetchall()]
        assert reasons == ["FIRST_MONTH_FAILED_DEBIT", "SECOND_MONTH_ARREARS"]
        assert db.execute("SELECT COUNT(*) FROM collections_cases WHERE member_id=?", (mid,)).fetchone()[0] == 1


def test_case_never_returns_to_sales_after_transfer(app):
    with app.app_context():
        db = get_db()
        case = _case(db, _member(db, 5))
        apply_case_ownership(db, case["id"], months_owing=2)
        assert apply_case_ownership(db, case["id"], months_owing=1) == "RECEPTION"


def test_daily_sweep_transfers_only_cases_at_threshold(app):
    with app.app_context():
        db = get_db()
        due = _case(db, _member(db, 6))
        stay = _case(db, _member(db, 7))
        moved = transfer_due_sales_cases(
            db, arrears_by_member={due["member_id"]: {"months_in_arrears": 2},
                                   stay["member_id"]: {"months_in_arrears": 1}},
        )
        assert moved == 1
        owners = {r["id"]: r["owner_type"] for r in db.execute("SELECT id, owner_type FROM collections_cases")}
        assert owners[due["id"]] == "RECEPTION" and owners[stay["id"]] == "SALES"


def test_age_transfer_uses_today_not_historical_failure_date(app):
    with app.app_context():
        db = get_db()
        case = _case(db, _member(db, 8))
        assert apply_case_ownership(db, case['id'], months_owing=1, today='2026-11-04') == 'SALES'
        assert transfer_due_sales_cases(db, {case['member_id']: {'months_in_arrears': 1}},
                                        today='2026-11-05') == 1
        reason = db.execute('SELECT assignment_reason FROM collections_cases WHERE id=?',
                            (case['id'],)).fetchone()[0]
        assert reason == 'MEMBERSHIP_OVER_FIRST_DEBIT_WINDOW'


def test_second_scheduled_debit_transfers_even_with_one_month_owing(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 9)
        db.execute("UPDATE members SET debit_order_date='25' WHERE id=?", (mid,))
        case = _case(db, mid, failed='2026-09-25')
        assert apply_case_ownership(db, case['id'], months_owing=1, today='2026-10-24') == 'SALES'
        assert apply_case_ownership(db, case['id'], months_owing=1, today='2026-10-25') == 'RECEPTION'
        assert db.execute('SELECT assignment_reason FROM collections_cases WHERE id=?',
                          (case['id'],)).fetchone()[0] == 'SECOND_SCHEDULED_DEBIT'


def test_second_debit_month_end_and_join_after_debit_day():
    from datetime import date
    from onecpase.collections_engine import second_scheduled_debit
    assert second_scheduled_debit(date(2026, 1, 31), 31) == date(2026, 2, 28)
    assert second_scheduled_debit(date(2026, 1, 26), 25) == date(2026, 3, 25)
    assert second_scheduled_debit(date(2026, 1, 1), None) is None
