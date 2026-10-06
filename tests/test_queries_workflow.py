"""Queries phase: merged decision panel, approval workflow, PTP link and the
guards that stop a query being closed or a member cancelled around them."""
import re
from datetime import date

import pytest

from onecpase.database import get_db
from onecpase.permissions import effective_permissions_for
from onecpase import queries as q

ADMIN, RECEPTION, MANAGER, MANAGER2, TRAINER = 1, 2, 3, 4, 5


def _seed(app):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO users (id, username, password_hash, full_name, role, department, active)
               VALUES (1, 'admin1', 'x', 'Admin One', 'admin', 'Admin', 1),
                      (2, 'recep1', 'x', 'Recep One', 'reception', 'Reception', 1),
                      (3, 'mgr1', 'x', 'Manager One', 'manager', 'Management', 1),
                      (4, 'mgr2', 'x', 'Manager Two', 'manager', 'Management', 1),
                      (5, 'trainer1', 'x', 'Trainer One', 'trainer', 'Fitness', 1)"""
        )
        ids = {}
        for key, months in (("m0", 0), ("m2", 2), ("m6", 6), ("m10", 10)):
            cur = db.execute(
                """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
                       member_status, gym_access_status, join_date, contract_duration,
                       monthly_installment, package)
                   VALUES (?, 'Mem', ?, ?, '0710000000', 'Active', 'allowed',
                           '2025-01-15', '24 Months', 400, 'Gold')""",
                (f"ELE-{key}", key.upper(), f"ID{key}"),
            )
            mid = cur.lastrowid
            ids[key] = mid
            for i in range(months):
                year, month = divmod(i, 12)
                db.execute(
                    """INSERT INTO collections (member_id, outstanding_balance, amount_paid,
                           collection_date, status, notes)
                       VALUES (?, 400, 0, ?, 'failed', 'type=Recurring Fee')""",
                    (mid, f"{2025 + year}-{month + 1:02d}-01"),
                )
        db.commit()
    return ids


def _login(client, user_id):
    role, dept = {
        ADMIN: ("admin", "Admin"), RECEPTION: ("reception", "Reception"),
        MANAGER: ("manager", "Management"), MANAGER2: ("manager", "Management"),
        TRAINER: ("trainer", "Fitness"),
    }[user_id]
    with client.session_transaction() as sess:
        sess.clear()
        sess["user_id"] = user_id
        sess["username"] = f"u{user_id}"
        sess["role"] = role
        sess["permissions"] = sorted(effective_permissions_for(role, dept))


def _query(app, qid):
    with app.app_context():
        return dict(get_db().execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone())


def _last_query(app):
    with app.app_context():
        return dict(get_db().execute("SELECT * FROM queries ORDER BY id DESC LIMIT 1").fetchone())


def _add(client, **form):
    data = {"category": "payments", "description": "Member called about arrears"}
    data.update(form)
    return client.post("/queries/add", data=data)


@pytest.fixture
def ids(app):
    return _seed(app)


# ── Configuration consistency ────────────────────────────────────────────────

def test_every_hint_matches_a_query_type():
    for category, hints in q.QUERY_TYPE_HINTS.items():
        assert category in q.QUERY_TYPES
        missing = set(hints) - set(q.QUERY_TYPES[category])
        assert not missing, (category, missing)


def test_arrangements_live_under_collections_not_payments():
    payments = set(q.QUERY_TYPES["payments"])
    assert not payments & {"Promise to pay", "Broken promise to pay", "Payment arrangement",
                           "Settlement discount"}
    assert "New PTP arrangement" in q.QUERY_TYPES["ptp_collections"]


def test_ptp_queries_get_the_payment_statuses():
    for category in ("payments", "ptp_collections"):
        keys = [k for k, _ in q.status_options_for(category)]
        assert "ptp_created" in keys and "payment_verified" in keys
        assert "closed" not in keys and "pending_approval" not in keys
    assert "ptp_created" not in [k for k, _ in q.status_options_for("membership")]


def test_contract_status_from_join_date_and_term():
    status = q._contract_status("2025-01-15", "24 Months")
    assert status["contract_end_date"] == "2027-01-15"
    assert status["contract_months"] == 24
    assert status["months_completed"] + status["remaining_months"] == 24
    assert q._contract_status("2025-01-15", "Month-to-month") is None
    assert q._contract_status(None, "12") is None


def test_template_fields_are_all_in_the_member_summary(app, client, ids):
    """Every data.<field> the Add form reads must come back from the endpoint."""
    _login(client, RECEPTION)
    data = client.get(f"/queries/member-summary/{ids['m6']}").get_json()
    assert data["ok"]
    with open("onecpase/templates/queries/add.html", encoding="utf-8") as fh:
        wanted = set(re.findall(r"data\.([a-z_]+)", fh.read())) - {"ok"}
    assert not wanted - set(data), wanted - set(data)
    # Banking details never leave the server through this endpoint.
    assert not {"bank", "account_masked", "branch_code", "member"} & set(data)
    assert data["arrears_months"] == 6
    assert data["discount_range_label"] == "60%–75%"
    assert data["contract_end_date"] == "2027-01-15"


# ── Permissions ──────────────────────────────────────────────────────────────

def test_trainers_cannot_open_queries(app, client, ids):
    _login(client, TRAINER)
    resp = client.get("/queries/")
    assert resp.status_code in (302, 403)
    resp = client.get(f"/queries/member-summary/{ids['m6']}")
    assert resp.status_code in (302, 403)


def test_managers_can_open_queries(app, client, ids):
    _login(client, MANAGER)
    assert client.get("/queries/").status_code == 200


# ── Capture and approval ─────────────────────────────────────────────────────

def test_discount_within_staff_limit_needs_no_approval(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="ptp_collections", member_id=ids["m2"], proposed_discount_pct="25")
    qry = _last_query(app)
    assert qry["status"] == "open"
    assert qry["approval_status"] == "not_required"
    assert qry["discount_percent_snapshot"] == 25


def test_discount_above_the_tier_is_refused(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="ptp_collections", member_id=ids["m2"], proposed_discount_pct="40")
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 0


def test_discount_above_fifty_goes_to_pending_approval(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="ptp_collections", member_id=ids["m6"], proposed_discount_pct="75",
         approval_reason="Can pay cash Friday")
    qry = _last_query(app)
    assert qry["status"] == "pending_approval"
    assert qry["approval_status"] == "pending"
    assert qry["proposed_discount_pct"] == 75
    assert "Can pay cash Friday" in qry["approval_reason"]


def test_nine_months_and_over_is_always_a_manager_decision(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="payments", member_id=ids["m10"], proposed_discount_pct="0")
    assert _last_query(app)["approval_status"] == "pending"


def test_pending_approval_blocks_close_update_and_ptp(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="ptp_collections", member_id=ids["m6"], proposed_discount_pct="75")
    qid = _last_query(app)["id"]
    client.post(f"/queries/{qid}/close", data={"closure_reason": "resolved", "resolution_notes": "x"})
    client.post(f"/queries/{qid}/update", data={"status": "resolved"})
    resp = client.post(f"/queries/{qid}/convert-ptp")
    assert "/members/" not in resp.headers["Location"]
    assert _query(app, qid)["status"] == "pending_approval"


def test_only_a_manager_decides_and_not_their_own_request(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="ptp_collections", member_id=ids["m6"], proposed_discount_pct="75")
    qid = _last_query(app)["id"]

    client.post(f"/queries/{qid}/decide", data={"decision": "approved"})
    assert _query(app, qid)["approval_status"] == "pending"

    _login(client, MANAGER)
    client.post(f"/queries/{qid}/decide", data={"decision": "rejected"})  # no reason
    assert _query(app, qid)["approval_status"] == "pending"
    client.post(f"/queries/{qid}/decide", data={"decision": "approved", "approval_notes": "ok"})
    qry = _query(app, qid)
    assert qry["approval_status"] == "approved"
    assert qry["status"] == "in_progress"
    assert qry["manager_approved_by"] == MANAGER


def test_manager_cannot_approve_own_request(app, client, ids):
    _login(client, MANAGER)
    _add(client, category="payments", member_id=ids["m2"], proposed_discount_pct="25")
    qid = _last_query(app)["id"]
    client.post(f"/queries/{qid}/request-approval",
                data={"proposed_discount_pct": "25", "approval_reason": "check"})
    assert _query(app, qid)["approval_status"] == "pending"
    client.post(f"/queries/{qid}/decide", data={"decision": "approved"})
    assert _query(app, qid)["approval_status"] == "pending"
    _login(client, MANAGER2)
    client.post(f"/queries/{qid}/decide", data={"decision": "approved"})
    assert _query(app, qid)["approval_status"] == "approved"


# ── Cancellation ─────────────────────────────────────────────────────────────

def _cancel_form(member_id, **extra):
    form = {
        "category": "cancellation", "description": "Wants to cancel",
        "member_id": member_id, "cancellation_contract_months": "24",
        "cancellation_months_completed": "10", "cancellation_monthly_fee": "400",
        "cancellation_payment_method": "lumpsum",
    }
    form.update(extra)
    return form


def _member_status(app, mid):
    with app.app_context():
        return get_db().execute("SELECT member_status FROM members WHERE id=?", (mid,)).fetchone()[0]


def test_staff_cannot_cancel_a_member_without_a_manager(app, client, ids):
    _login(client, RECEPTION)
    client.post("/queries/add", data=_cancel_form(ids["m0"], cancel_member="1"))
    qry = _last_query(app)
    assert qry["approval_status"] == "pending" and qry["cancel_member_requested"] == 1
    assert _member_status(app, ids["m0"]) == "Active"

    _login(client, MANAGER)
    client.post(f"/queries/{qry['id']}/decide", data={"decision": "approved"})
    assert _member_status(app, ids["m0"]) == "Cancelled"
    assert _query(app, qry["id"])["member_cancelled"] == 1


def test_rejected_cancellation_leaves_the_member_active(app, client, ids):
    _login(client, RECEPTION)
    client.post("/queries/add", data=_cancel_form(ids["m0"], cancel_member="1"))
    qid = _last_query(app)["id"]
    _login(client, MANAGER)
    client.post(f"/queries/{qid}/decide", data={"decision": "rejected", "approval_notes": "Retention offer first"})
    assert _member_status(app, ids["m0"]) == "Active"
    assert _query(app, qid)["approval_status"] == "rejected"


def test_blank_months_completed_is_refused_not_maximum_penalty(app, client, ids):
    _login(client, RECEPTION)
    client.post("/queries/add", data=_cancel_form(ids["m0"], cancellation_months_completed=""))
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 0


def test_owing_cancellation_cannot_be_resolved_through_update(app, client, ids):
    _login(client, MANAGER)
    client.post("/queries/add", data=_cancel_form(ids["m2"]))
    qid = _last_query(app)["id"]
    client.post(f"/queries/{qid}/update", data={"status": "resolved"})
    assert _query(app, qid)["status"] == "open"
    client.post(f"/queries/{qid}/update", data={"status": "closed"})
    assert _query(app, qid)["status"] == "open"
    client.post(f"/queries/{qid}/close", data={"closure_reason": "resolved", "resolution_notes": "done"})
    assert _query(app, qid)["status"] == "open"
    client.post(f"/queries/{qid}/close", data={"closure_reason": "resolved",
                                               "resolution_notes": "done", "owing_ack": "1"})
    qry = _query(app, qid)
    assert qry["status"] == "closed" and qry["closure_reason"] == "resolved"


def test_close_needs_a_reason_and_notes(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="general", description="Parking question")
    qid = _last_query(app)["id"]
    client.post(f"/queries/{qid}/close", data={"resolution_notes": "told them"})
    assert _query(app, qid)["status"] == "open"
    client.post(f"/queries/{qid}/close", data={"closure_reason": "no_action_needed"})
    assert _query(app, qid)["status"] == "open"
    client.post(f"/queries/{qid}/close", data={"closure_reason": "no_action_needed",
                                               "resolution_notes": "told them"})
    assert _query(app, qid)["status"] == "closed"


# ── PTP link ─────────────────────────────────────────────────────────────────

def _create_ptp(client, mid, qid, discount):
    return client.post(f"/members/{mid}/ptp/create", data={
        "promise_amount": "500", "promise_date": date.today().isoformat(),
        "payment_method": "cash", "arrangement_type": "full", "notes": "Agreed on call",
        "arrears_amount": "2400", "discount_pct": str(discount), "query_id": str(qid),
    })


def test_ptp_created_from_a_query_is_linked_and_followed(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="ptp_collections", member_id=ids["m6"], proposed_discount_pct="75")
    qid = _last_query(app)["id"]
    _login(client, MANAGER)
    client.post(f"/queries/{qid}/decide", data={"decision": "approved"})

    _login(client, RECEPTION)
    resp = client.post(f"/queries/{qid}/convert-ptp")
    assert f"from_query={qid}" in resp.headers["Location"]
    page = client.get(f"/members/{ids['m6']}?from_query={qid}").get_data(as_text=True)
    assert f'name="query_id" value="{qid}"' in page

    _create_ptp(client, ids["m6"], qid, 75)
    qry = _query(app, qid)
    assert qry["ptp_id"] and qry["status"] == "ptp_created"
    with app.app_context():
        ptp = get_db().execute("SELECT * FROM ptp_agreements WHERE id=?", (qry["ptp_id"],)).fetchone()
    # The manager's approval on the query covers the discount.
    assert ptp["discount_basis"] == "query_approval"

    _login(client, ADMIN)
    client.post(f"/members/{ids['m6']}/ptp/{qry['ptp_id']}/status", data={"ptp_status": "broken"})
    qry = _query(app, qid)
    assert qry["status"] == "escalated" and qry["priority"] == "high"


def test_unapproved_discount_on_a_linked_ptp_still_needs_a_manager(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="ptp_collections", member_id=ids["m6"], proposed_discount_pct="60")
    qid = _last_query(app)["id"]
    _login(client, MANAGER)
    client.post(f"/queries/{qid}/decide", data={"decision": "approved"})
    _login(client, RECEPTION)
    _create_ptp(client, ids["m6"], qid, 75)   # more than the manager approved
    with app.app_context():
        ptp = get_db().execute("SELECT * FROM ptp_agreements ORDER BY id DESC LIMIT 1").fetchone()
    assert ptp["discount_basis"] != "query_approval"
    assert ptp["manager_approval_status"] == "pending"


def test_ptp_cannot_be_linked_to_another_members_query(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="ptp_collections", member_id=ids["m2"], proposed_discount_pct="25")
    qid = _last_query(app)["id"]
    _create_ptp(client, ids["m6"], qid, 0)
    assert _query(app, qid)["ptp_id"] is None


# ── Index ────────────────────────────────────────────────────────────────────

def test_index_filters_by_member_and_pending_approval(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="general", member_id=ids["m0"], description="Locker")
    _add(client, category="ptp_collections", member_id=ids["m6"], proposed_discount_pct="75")
    a, b = (r["reference"] for r in _rows(app))
    client.get("/queries/")  # consume the "logged" flash messages
    page = client.get(f"/queries/?member_id={ids['m0']}").get_data(as_text=True)
    assert a in page and b not in page
    page = client.get("/queries/?approval=pending").get_data(as_text=True)
    assert b in page and a not in page


def _rows(app):
    with app.app_context():
        return get_db().execute("SELECT reference FROM queries ORDER BY id").fetchall()


def test_detail_page_renders_for_every_state(app, client, ids):
    _login(client, RECEPTION)
    client.post("/queries/add", data=_cancel_form(ids["m2"], cancel_member="1"))
    _add(client, category="ptp_collections", member_id=ids["m6"], proposed_discount_pct="75")
    _add(client, category="membership", member_id=ids["m0"], query_type="Transfer")
    with app.app_context():
        qids = [r[0] for r in get_db().execute("SELECT id FROM queries").fetchall()]
    for qid in qids:
        assert client.get(f"/queries/{qid}").status_code == 200
    _login(client, MANAGER)
    for qid in qids:
        assert client.get(f"/queries/{qid}").status_code == 200
    assert _query(app, qids[-1])["department"] == "Admin"


def test_add_form_renders_with_and_without_a_member(app, client, ids):
    _login(client, RECEPTION)
    assert client.get("/queries/add").status_code == 200
    page = client.get(f"/queries/add?member_id={ids['m6']}").get_data(as_text=True)
    assert 'name="proposed_discount_pct"' in page


def test_reports_overview_links_to_queries(app, client, ids):
    _login(client, RECEPTION)
    _add(client, category="general", member_id=ids["m0"], description="Locker")
    _login(client, ADMIN)
    resp = client.get("/reports/overview?tab=queries")
    assert b"/queries/" in resp.data
    assert resp.status_code == 200


def test_member_page_hides_queries_before_phase_three(tmp_path):
    from tests.test_phases import build_app

    app = build_app(tmp_path, "2")
    ids = _seed(app)
    with app.test_client() as client:
        _login(client, ADMIN)
        page = client.get(f"/members/{ids['m0']}").get_data(as_text=True)
        assert "/queries/add" not in page
    app3 = build_app(tmp_path / "p3", "3")
    ids3 = _seed(app3)
    with app3.test_client() as client:
        _login(client, ADMIN)
        page = client.get(f"/members/{ids3['m0']}").get_data(as_text=True)
        assert "/queries/add" in page
