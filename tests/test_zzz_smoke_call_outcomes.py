"""Temporary smoke test for the call-outcome workflow feature.
Not part of the permanent suite -- deleted after manual verification."""
from datetime import date, timedelta

from onecpase.collections import CALL_OUTCOMES
from onecpase.database import get_db


def _seed(app):
    db = get_db()
    db.execute(
        """INSERT INTO users (id, username, password_hash, full_name, role, department, active,
               is_collections_caller)
           VALUES (1, 'admin1', 'x', 'Admin One', 'admin', 'Admin', 1, 0),
                  (2, 'caller1', 'x', 'Caller One', 'reception', 'Reception', 1, 1)"""
    )
    cur = db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact, email,
               member_status, gym_access_status)
           VALUES ('ELE-0000000001','Test','MemberA','ID0001','0710000001','a@example.com','Active','allowed')"""
    )
    mid_a = cur.lastrowid
    for month in ("2026-06", "2026-07"):
        db.execute(
            """INSERT INTO collections (member_id, outstanding_balance, amount_paid,
                   collection_date, status, notes)
               VALUES (?, 500, 0, ?, 'failed', 'type=Recurring Fee')""",
            (mid_a, f"{month}-01"),
        )
    db.commit()

    def make_task(queue_type="old_owing"):
        cur = db.execute(
            """INSERT INTO collection_call_tasks (task_date, member_id, queue_type, priority, assigned_to)
               VALUES (?, ?, ?, 1, 2)""",
            (date.today().isoformat(), mid_a, queue_type),
        )
        db.commit()
        return cur.lastrowid

    cur = db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, gym_access_status)
           VALUES ('ELE-0000000002','Test','MemberB','ID0002','0710000002','Active','allowed')"""
    )
    mid_b = cur.lastrowid
    db.commit()

    return {"mid_a": mid_a, "mid_b": mid_b, "make_task": make_task}


def _login(client, uid=2, role="reception"):
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "caller1"
        sess["role"] = role
        sess["permissions"] = ["collections_call_queue", "all_collections"]


def test_call_outcomes_merged(app):
    keys = {k for k, _, _ in CALL_OUTCOMES}
    assert "payment_arrangement" not in keys
    assert dict((k, l) for k, l, _ in CALL_OUTCOMES)["promised_to_pay"] == "Promise to Pay"


def test_refused_requires_notes(app):
    with app.app_context():
        ctx = _seed(app)
        task_id = ctx["make_task"]()
    with app.test_client() as client:
        _login(client)
        resp = client.post(f"/collections/call-queue/{task_id}/result", data={"outcome": "refused", "notes": ""})
        assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute("SELECT status FROM collection_call_tasks WHERE id=?", (task_id,)).fetchone()
        assert row["status"] == "pending"


def test_asked_callback_creates_future_task(app):
    with app.app_context():
        ctx = _seed(app)
        task_id = ctx["make_task"]()
    future = (date.today() + timedelta(days=3)).isoformat()
    with app.test_client() as client:
        _login(client)
        resp = client.post(
            f"/collections/call-queue/{task_id}/result",
            data={"outcome": "asked_callback", "notes": "call me Friday", "callback_date": future},
        )
        assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT * FROM collection_call_tasks WHERE queue_type='callback' AND task_date=?", (future,)
        ).fetchone()
        assert row is not None
        assert row["member_id"] == ctx["mid_a"]
        assert row["priority"] == 1


def test_disputed_creates_query(app):
    with app.app_context():
        ctx = _seed(app)
        task_id = ctx["make_task"]()
    with app.test_client() as client:
        _login(client)
        resp = client.post(
            f"/collections/call-queue/{task_id}/result",
            data={"outcome": "disputed", "notes": "Says amount is wrong"},
        )
        assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT * FROM queries WHERE member_id=? AND query_type='Debit order dispute'", (ctx["mid_a"],)
        ).fetchone()
        assert row is not None
        assert row["category"] == "payments"


def test_already_paid_creates_query(app):
    with app.app_context():
        ctx = _seed(app)
        task_id = ctx["make_task"]()
    with app.test_client() as client:
        _login(client)
        resp = client.post(
            f"/collections/call-queue/{task_id}/result",
            data={"outcome": "already_paid", "notes": "Paid via EFT yesterday"},
        )
        assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT * FROM queries WHERE member_id=? AND query_type='Proof of payment'", (ctx["mid_a"],)
        ).fetchone()
        assert row is not None


def test_no_answer_does_not_error_without_mail_configured(app):
    with app.app_context():
        ctx = _seed(app)
        task_id = ctx["make_task"]()
    with app.test_client() as client:
        _login(client)
        resp = client.post(f"/collections/call-queue/{task_id}/result", data={"outcome": "no_answer", "notes": ""})
        assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute("SELECT email_sent, status FROM collection_call_tasks WHERE id=?", (task_id,)).fetchone()
        assert row["status"] == "completed"
        assert row["email_sent"] == 0  # MAIL_USERNAME not configured in test app


def test_promised_to_pay_redirects_to_prefilled_ptp_tab(app):
    with app.app_context():
        ctx = _seed(app)
        task_id = ctx["make_task"]()
    with app.test_client() as client:
        _login(client)
        resp = client.post(
            f"/collections/call-queue/{task_id}/result",
            data={"outcome": "promised_to_pay", "notes": "will pay"},
        )
        assert resp.status_code == 302
        assert "open_ptp=1" in resp.headers["Location"]
        assert "promise_amount=" in resp.headers["Location"]


def test_toggle_escalation_marks_whatsapp_sent(app):
    with app.app_context():
        ctx = _seed(app)
        task_id = ctx["make_task"]()
    with app.test_client() as client:
        _login(client)
        client.post(f"/collections/call-queue/{task_id}/result", data={"outcome": "busy", "notes": ""})
        resp = client.post(f"/collections/call-queue/{task_id}/escalation", data={"channel": "whatsapp"})
        assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute("SELECT whatsapp_sent FROM collection_call_tasks WHERE id=?", (task_id,)).fetchone()
        assert row["whatsapp_sent"] == 1


def test_ptp_discount_with_mandate_auto_approves(app):
    with app.app_context():
        ctx = _seed(app)
        db = get_db()
        db.execute(
            """INSERT INTO debicheck_mandates (member_id, merchant_id, auth_type, client_ref1, client_ref2,
                   account_name, account_type, account_number, branch_code, instalment_amount,
                   submit_date, status)
               VALUES (?, 'm1', 'cell', 'ref1', 'ref2', 'Test MemberA', '1', '123456789', '123456', 500,
                       '2026-08-01', 'approved')""",
            (ctx["mid_a"],),
        )
        db.commit()
    with app.test_client() as client:
        _login(client, uid=1, role="admin")
        resp = client.post(
            f"/members/{ctx['mid_a']}/ptp/create",
            data={
                "promise_amount": "500", "promise_date": "2026-09-01", "payment_method": "debit_order",
                "arrangement_type": "full", "notes": "test", "arrears_amount": "1000", "discount_pct": "25",
            },
        )
        assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT discount_basis, manager_approval_status FROM ptp_agreements WHERE member_id=? ORDER BY id DESC LIMIT 1",
            (ctx["mid_a"],),
        ).fetchone()
        assert row["discount_basis"] == "debicheck_ptp"
        assert row["manager_approval_status"] == "not_required"


def test_ptp_discount_without_mandate_forces_manager_approval(app):
    with app.app_context():
        ctx = _seed(app)
    with app.test_client() as client:
        _login(client, uid=1, role="admin")
        resp = client.post(
            f"/members/{ctx['mid_b']}/ptp/create",
            data={
                "promise_amount": "300", "promise_date": "2026-09-01", "payment_method": "eft",
                "arrangement_type": "full", "notes": "test", "arrears_amount": "500", "discount_pct": "25",
            },
        )
        assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT discount_basis, manager_approval_status FROM ptp_agreements WHERE member_id=? ORDER BY id DESC LIMIT 1",
            (ctx["mid_b"],),
        ).fetchone()
        assert row["discount_basis"] is None
        assert row["manager_approval_status"] == "pending"


def test_ptp_cash_upfront_discount_auto_approves_up_to_50(app):
    with app.app_context():
        ctx = _seed(app)
    with app.test_client() as client:
        _login(client, uid=1, role="admin")
        resp = client.post(
            f"/members/{ctx['mid_b']}/ptp/create",
            data={
                "promise_amount": "250", "promise_date": "2026-09-01", "payment_method": "cash",
                "arrangement_type": "full", "notes": "test", "arrears_amount": "500", "discount_pct": "50",
            },
        )
        assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT discount_basis, manager_approval_status, discount_amount FROM ptp_agreements WHERE member_id=? ORDER BY id DESC LIMIT 1",
            (ctx["mid_b"],),
        ).fetchone()
        assert row["discount_basis"] == "cash_upfront"
        assert row["manager_approval_status"] == "not_required"
        assert row["discount_amount"] == 250.0


def test_ptp_cash_discount_above_ceiling_forces_approval(app):
    with app.app_context():
        ctx = _seed(app)
    with app.test_client() as client:
        _login(client, uid=1, role="admin")
        resp = client.post(
            f"/members/{ctx['mid_b']}/ptp/create",
            data={
                "promise_amount": "100", "promise_date": "2026-09-01", "payment_method": "cash",
                "arrangement_type": "full", "notes": "test", "arrears_amount": "500", "discount_pct": "60",
            },
        )
        assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT discount_basis, manager_approval_status FROM ptp_agreements WHERE member_id=? ORDER BY id DESC LIMIT 1",
            (ctx["mid_b"],),
        ).fetchone()
        assert row["discount_basis"] is None
        assert row["manager_approval_status"] == "pending"
