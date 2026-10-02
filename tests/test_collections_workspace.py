from onecpase.collections_engine import (
    log_collection_communication,
    refresh_case_workflow,
    sync_collection_case,
)
from onecpase.collections_workspace import QUEUE_FILTERS, case_queue, dashboard_metrics, queue_counts
from onecpase.database import get_db


def _member(db, mid, name="Wk", join="2025-01-01"):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, join_date, gym_access_status)
           VALUES (?, ?, ?, ?, ?, 'Active', ?, 'allowed')""",
        (f"WK-{mid}", name, f"T{mid}", f"ID-WK-{mid}", f"0770000{mid:03d}", join),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _case(db, mid, amount=500, months=1):
    db.execute(
        """INSERT INTO collections (member_id, outstanding_balance, amount_paid,
               collection_date, status, notes)
           VALUES (?, ?, 0, '2026-09-01', 'failed', 'type=Recurring Fee')""", (mid, amount))
    case_id = sync_collection_case(db, mid, failed_debit_date="2026-09-01",
                                   arrears_amount=amount, months_owing=months)
    db.commit()
    return case_id


def test_queue_is_ordered_by_priority_then_balance(app):
    with app.app_context():
        db = get_db()
        small_new = _case(db, _member(db, 1), amount=100)        # contact required -> high
        big_new = _case(db, _member(db, 2), amount=900)
        calm = _case(db, _member(db, 3), amount=5000)
        log_collection_communication(db, db.execute("SELECT member_id FROM collections_cases WHERE id=?", (calm,)).fetchone()[0],
                                     "call", case_id=calm, outcome="no_answer")   # contacting -> normal
        db.commit()
        order = [row["id"] for row in case_queue(db, "all")]
        assert order == [big_new, small_new, calm]


def test_filters_pick_the_right_cases(app):
    with app.app_context():
        db = get_db()
        sales_member = _member(db, 4, join="2026-08-20")                   # first debit -> Sales
        db.execute("INSERT INTO membership_applications (member_id, created_by) VALUES (?, 1)", (sales_member,))
        sales = _case(db, sales_member)
        refresh = _case(db, _member(db, 5), months=3)                     # old, 3 months
        quiet = _case(db, _member(db, 6))
        log_collection_communication(db, 6, "call", case_id=quiet, outcome="no_answer")
        db.commit()

        ids = lambda key, **kw: {r["id"] for r in case_queue(db, key, **kw)}
        assert refresh in ids("reception_2plus")
        assert sales not in ids("reception_2plus")
        assert sales in ids("sales_first_debit") and refresh not in ids("sales_first_debit")
        assert quiet not in ids("no_contact") and refresh in ids("no_contact")
        assert ids("mine", user_id=None) == set()
        assert set(QUEUE_FILTERS) == set(queue_counts(db))


def test_my_queue_includes_cases_the_user_owns(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 7, join="2026-08-20")
        db.execute("INSERT INTO membership_applications (member_id, created_by) VALUES (?, 1)", (mid,))
        case = _case(db, mid)
        assert case in {r["id"] for r in case_queue(db, "mine", user_id=1)}
        assert case not in {r["id"] for r in case_queue(db, "mine", user_id=2)}


def test_dashboard_metrics_add_up(app):
    with app.app_context():
        db = get_db()
        _case(db, _member(db, 8), amount=300)
        _case(db, _member(db, 9), amount=700)
        metrics = dashboard_metrics(db, user_id=1)
        assert metrics["financial"]["total_outstanding"] == 1000
        assert metrics["financial"]["members_in_arrears"] == 2
        assert metrics["financial"]["failed_debits"] == 2
        assert sum(metrics["workload"]["by_next_action"].values()) == 2
        assert metrics["manager"]["open_by_owner"]["RECEPTION"] == 2


def test_dashboard_and_queue_pages_render(app, client):
    with app.app_context():
        db = get_db()
        _case(db, _member(db, 10), amount=250)
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["username"] = "admin"
    dash = client.get("/collections/dashboard")
    assert dash.status_code == 200 and b"Total outstanding" in dash.data and b"R250.00" in dash.data
    queue = client.get("/collections/queue?filter=no_contact")
    assert queue.status_code == 200 and b"Wk T10" in queue.data
    assert client.get("/collections/queue?filter=nonsense").status_code == 200
