from onecpase.collections_engine import (
    apply_case_ownership,
    reconcile_open_cases,
    sync_collection_case,
)
from onecpase.collections_workspace import (
    financial_report,
    performance_report,
    report_month,
    sales_quality_report,
)
from onecpase.database import get_db

MONTH = "2026-09"


def _member(db, mid, join="2026-09-02", consultant=None):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, join_date, gym_access_status)
           VALUES (?, 'Rep', ?, ?, ?, 'Active', ?, 'allowed')""",
        (f"REP-{mid}", f"T{mid}", f"ID-REP-{mid}", f"0790000{mid:03d}", join),
    )
    member_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    if consultant:
        db.execute("INSERT INTO membership_applications (member_id, created_by) VALUES (?, ?)", (member_id, consultant))
    return member_id


def _failed_debit(db, mid, month="2026-09", amount=500):
    db.execute(
        """INSERT INTO collections (member_id, outstanding_balance, amount_paid, collection_date, status, notes)
           VALUES (?, ?, 0, ?, 'failed', 'type=Recurring Fee')""", (mid, amount, f"{month}-05"))
    case_id = sync_collection_case(db, mid, failed_debit_date=f"{month}-05", arrears_amount=amount, months_owing=1)
    db.commit()
    return case_id


def _stamp(db, case_id, created="2026-09-06 09:00:00"):
    """Put a case's own timestamps inside the report month."""
    db.execute("UPDATE collections_cases SET created_at=? WHERE id=?", (created, case_id))
    db.execute("UPDATE collection_assignment_history SET transferred_at=? WHERE case_id=?", (created, case_id))
    db.commit()


def test_report_month_falls_back_on_bad_input():
    assert report_month("2026-09") == "2026-09"
    for bad in (None, "", "2026-13", "garbage", "26-09", "2026/09"):
        assert len(report_month(bad)) == 7 and report_month(bad)[4] == "-"


def test_financial_report_counts_the_month(app):
    with app.app_context():
        db = get_db()
        a = _failed_debit(db, _member(db, 1), amount=500)
        b = _failed_debit(db, _member(db, 2), amount=300)
        _stamp(db, a)
        _stamp(db, b)
        db.execute("""INSERT INTO collections (member_id, outstanding_balance, amount_paid, collection_date, status, notes)
                      VALUES (1, 100, 100, '2026-09-10', 'paid', 'type=Recurring Fee')""")
        db.commit()
        report = financial_report(db, MONTH)
        assert report["new_arrears"] == 800
        assert report["collected"] == 100
        assert report["open_cases"] == 2
        assert financial_report(db, "2025-01")["new_arrears"] == 0


def test_sales_quality_failure_rate_recovery_and_transfer(app):
    with app.app_context():
        db = get_db()
        # Consultant 1 signed three members in September; two first debits failed.
        m1 = _member(db, 10, consultant=1)
        m2 = _member(db, 11, consultant=1)
        _member(db, 12, consultant=1)
        c1 = _failed_debit(db, m1)
        c2 = _failed_debit(db, m2)
        for case in (c1, c2):
            _stamp(db, case)
        # m1 pays in full and stays with Sales; m2 reaches two months and moves to Reception.
        db.execute("UPDATE collections SET amount_paid=500, status='paid' WHERE member_id=?", (m1,))
        reconcile_open_cases(db, [m1])
        db.execute("UPDATE collections_cases SET closed_at='2026-09-20 10:00:00' WHERE id=?", (c1,))
        apply_case_ownership(db, c2, months_owing=2)
        db.execute("UPDATE collection_assignment_history SET transferred_at='2026-09-25 10:00:00' "
                   "WHERE case_id=? AND reason='SECOND_MONTH_ARREARS'", (c2,))
        db.commit()

        rows = sales_quality_report(db, MONTH)["rows"]
        mine = next(r for r in rows if r["id"] == 1)
        assert mine["new_members"] == 3
        assert mine["failed"] == 2
        assert mine["failure_rate"] == 66.7
        assert mine["recovered"] == 1
        assert mine["transferred"] == 1
        assert mine["unresolved"] == 0


def test_performance_report_totals(app):
    with app.app_context():
        db = get_db()
        db.execute("INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, department) "
                   "VALUES (5, 'caller5', 'x', 'Caller Five', 'reception', 'Reception')")
        mid = _member(db, 20)
        for day, status, contact in (("2026-09-01", "completed", 1), ("2026-09-02", "completed", 0), ("2026-09-03", "pending", 0)):
            db.execute("""INSERT INTO collection_call_tasks (task_date, member_id, queue_type, assigned_to, status, successful_contact)
                          VALUES (?, ?, 'failed_debit', 5, ?, ?)""", (day, mid, status, contact))
            mid = _member(db, 21 + int(day[-1]))
        db.execute("""INSERT INTO ptp_agreements (member_id, promise_amount, promise_date, payment_method, ptp_status,
                          created_at, updated_at, discount_pct)
                      VALUES (?, 500, '2026-09-30', 'eft', 'broken', '2026-09-10 08:00:00', '2026-09-30 08:00:00', 25)""", (mid,))
        db.commit()
        report = performance_report(db, MONTH)
        totals = report["totals"]
        assert (totals["calls"], totals["contacts"], totals["contact_rate"]) == (2, 1, 50.0)
        assert totals["ptps_created"] == 1 and totals["ptps_broken"] == 1 and totals["discount_requests"] == 1
        assert report["callers"][0]["name"] == "Caller Five" and report["callers"][0]["assigned"] == 3


def test_reports_page_renders_and_survives_a_bad_month(app, client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["username"] = "admin"
    page = client.get("/collections/reports?month=2026-09")
    assert page.status_code == 200
    assert b"Sales quality" in page.data and b"Reception calls" in page.data
    assert client.get("/collections/reports?month=nonsense").status_code == 200
