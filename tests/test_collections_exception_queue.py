"""Management exception queue.

Rule §7: a non-standard arrangement at three or more months owing is a
manager's decision, not reception's. These tests pin that the escalations the
rule engine produces are visible to a manager and decidable with an audit
trail — and invisible to everyone else.
"""
from datetime import date, timedelta

from onecpase.collections_engine import create_collection_exception
from onecpase.database import get_db

# Two weeks out, so an arrangement never runs into the past as the calendar moves on.
PROMISE_DATE = (date.today() + timedelta(days=14)).isoformat()

# Permissions are rebuilt from the user row, so a manager sign-in needs a real manager user.
MANAGER_ID = 99


def _seed(app):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO users (id, username, password_hash, full_name, role, active)
               VALUES (1, 'mgr', 'x', 'Manager One', 'manager', 1),
                      (2, 'recep', 'x', 'Reception One', 'reception', 1)"""
        )
        member_id = db.execute(
            """INSERT INTO members (member_ref, first_name, last_name, id_number,
                   contact, member_status)
               VALUES ('ELE-0000000001', 'Escalate', 'Member', 'ID0001', '0710000001', 'Active')"""
        ).lastrowid
        ptp_id = db.execute(
            """INSERT INTO ptp_agreements (member_id, created_by, arrears_amount,
                   promise_amount, promise_date, payment_method, arrangement_type,
                   ptp_status, manager_approval_status, discount_pct)
               VALUES (?, 2, 1500, 500, '2026-10-01', 'cash', 'partial',
                       'pending', 'pending', 60)""",
            (member_id,),
        ).lastrowid
        exception_id = create_collection_exception(
            db, member_id,
            "Three months owing with a non-standard instalment request",
            requested_action="Accept R300/month over 5 months",
            created_by=2,
        )
        db.commit()
    return {"member_id": member_id, "ptp_id": ptp_id, "exception_id": exception_id}


def _sign_in(client, role="manager", user_id=1, permissions=None):
    with client.session_transaction() as session:
        session.update({
            "user_id": user_id,
            "username": "u",
            "role": role,
            "permissions": permissions or [],
        })


def test_queue_shows_both_kinds_of_escalation(app, client):
    ids = _seed(app)
    _sign_in(client)

    response = client.get("/collections/exceptions")

    assert response.status_code == 200
    body = response.data
    assert b"Escalate Member" in body
    # The PTP tier that could not auto-approve, and the engine exception.
    assert b"60% discount with no qualifying DebiCheck" in body
    assert b"Accept R300/month over 5 months" in body


def test_reception_cannot_open_the_queue(app, client):
    _seed(app)
    _sign_in(client, role="reception", user_id=2)

    response = client.get("/collections/exceptions", follow_redirects=False)

    assert response.status_code == 302
    assert "/collections/exceptions" not in response.headers["Location"]


def test_manager_decision_is_recorded_with_its_condition(app, client):
    ids = _seed(app)
    _sign_in(client)

    response = client.post(
        f"/collections/exceptions/{ids['exception_id']}/decide",
        data={"decision": "approved", "notes": "Allowed on condition the first R300 clears by Friday"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT * FROM collection_exceptions WHERE id = ?", (ids["exception_id"],)
        ).fetchone()
        assert row["status"] == "approved"
        assert row["decided_by"] == 1
        assert row["decided_at"]
        assert "first R300 clears" in row["notes"]

        note = get_db().execute(
            """SELECT note FROM member_notes WHERE member_id = ?
               ORDER BY id DESC LIMIT 1""",
            (ids["member_id"],),
        ).fetchone()
        assert "approved by manager" in note["note"]


def test_declining_without_a_reason_is_refused(app, client):
    ids = _seed(app)
    _sign_in(client)

    client.post(
        f"/collections/exceptions/{ids['exception_id']}/decide",
        data={"decision": "declined", "notes": ""},
    )

    with app.app_context():
        row = get_db().execute(
            "SELECT status FROM collection_exceptions WHERE id = ?", (ids["exception_id"],)
        ).fetchone()
        assert row["status"] == "pending"


def test_an_already_decided_exception_cannot_be_decided_again(app, client):
    ids = _seed(app)
    _sign_in(client)
    payload = {"decision": "approved", "notes": "First decision"}
    client.post(f"/collections/exceptions/{ids['exception_id']}/decide", data=payload)

    client.post(
        f"/collections/exceptions/{ids['exception_id']}/decide",
        data={"decision": "declined", "notes": "Second decision"},
    )

    with app.app_context():
        row = get_db().execute(
            "SELECT status, notes FROM collection_exceptions WHERE id = ?",
            (ids["exception_id"],),
        ).fetchone()
        assert row["status"] == "approved"
        assert row["notes"] == "First decision"


def test_approving_from_the_queue_returns_to_the_queue(app, client):
    ids = _seed(app)
    _sign_in(client)

    response = client.post(
        f"/members/{ids['member_id']}/ptp/{ids['ptp_id']}/approve",
        data={"decision": "approved", "approval_notes": "Signed off", "return_to": "exception_queue"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/collections/exceptions")
    with app.app_context():
        row = get_db().execute(
            "SELECT manager_approval_status FROM ptp_agreements WHERE id = ?", (ids["ptp_id"],)
        ).fetchone()
        assert row["manager_approval_status"] == "approved"


# ── Rule §7 producer ──────────────────────────────────────────────────────────

def _seed_arrears_member(app, months, *, approved_mandate=False):
    """A member owing *months* whole billing cycles, optionally with the
    approved DebiCheck mandate that makes an arrangement standard."""
    from datetime import date

    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO users (id, username, password_hash, full_name, role, active)
               VALUES (1, 'recep', 'x', 'Reception One', 'reception', 1),
                      (99, 'mgr99', 'x', 'Manager Ninety-Nine', 'manager', 1)"""
        )
        member_id = db.execute(
            """INSERT INTO members (member_ref, first_name, last_name, id_number,
                   contact, member_status, monthly_installment, gym_access_status)
               VALUES ('ELE-0000000009', 'Deep', 'Arrears', 'ID0009', '0710000009',
                       'Active', 500, 'blocked')"""
        ).lastrowid
        today = date.today()
        for offset in range(1, months + 1):
            index = today.year * 12 + today.month - 1 - offset
            year, zero_month = divmod(index, 12)
            db.execute(
                """INSERT INTO collections (member_id, outstanding_balance, amount_paid,
                       collection_date, status, notes)
                   VALUES (?, 500, 0, ?, 'failed', 'type=Recurring Fee')""",
                (member_id, f"{year}-{zero_month + 1:02d}-01"),
            )
        # Bank-statement evidence is part of what lets reception's arrangement stand
        # without a manager, so the seeded member has a qualifying statement on file.
        db.execute(
            """INSERT INTO member_documents (member_id, document_type, original_name, stored_name, analysis_json)
               VALUES (?, 'Bank Statement', 'statement.pdf', 'statement.pdf', '{"verification_qualified": true}')""",
            (member_id,),
        )
        if approved_mandate:
            db.execute(
                """INSERT INTO debicheck_mandates (member_id, merchant_id, auth_type,
                       client_ref1, client_ref2, account_name, account_type,
                       account_number, branch_code, instalment_amount, submit_date, status)
                   VALUES (?, 'm1', 'cell', 'ref1', '1234', 'Deep Arrears', '1',
                           '123456789', '123456', 500, '2026-08-01', 'approved')""",
                (member_id,),
            )
        db.commit()
    return member_id


def _create_ptp(client, member_id):
    return client.post(
        f"/members/{member_id}/ptp/create",
        data={
            "promise_amount": "300", "promise_date": PROMISE_DATE,
            "payment_method": "cash", "arrangement_type": "partial",
            "notes": "Client offered R300 a month", "arrears_amount": "3000",
        },
        follow_redirects=False,
    )


def _assert_ptp_recorded(app, member_id):
    """A "no escalation" assertion is only meaningful if the arrangement was
    actually created — an unauthorised POST also creates no exception."""
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM ptp_agreements WHERE member_id = ?", (member_id,)
        ).fetchone()[0] >= 1


def _open_exceptions(app, member_id):
    with app.app_context():
        return get_db().execute(
            "SELECT * FROM collection_exceptions WHERE member_id = ? AND status = 'pending'",
            (member_id,),
        ).fetchall()


def test_non_standard_request_at_three_months_is_escalated(app, client):
    member_id = _seed_arrears_member(app, 3)
    _sign_in(client, role="reception", user_id=1, permissions=["all_collections"])

    assert _create_ptp(client, member_id).status_code == 302

    rows = _open_exceptions(app, member_id)
    assert len(rows) == 1
    assert "no qualifying DebiCheck" in rows[0]["reason"]
    assert f"R300.00 by {PROMISE_DATE}" in rows[0]["requested_action"]
    with app.app_context():
        ptp = get_db().execute(
            "SELECT manager_approval_status FROM ptp_agreements WHERE member_id = ?",
            (member_id,),
        ).fetchone()
        # Reception must not be able to settle this alone.
        assert ptp["manager_approval_status"] == "pending"


def test_qualifying_debicheck_arrangement_is_not_escalated(app, client):
    member_id = _seed_arrears_member(app, 3, approved_mandate=True)
    _sign_in(client, role="reception", user_id=1, permissions=["all_collections"])

    assert _create_ptp(client, member_id).status_code == 302

    _assert_ptp_recorded(app, member_id)
    assert _open_exceptions(app, member_id) == []


def test_two_months_owing_is_not_a_rule_7_escalation(app, client):
    member_id = _seed_arrears_member(app, 2)
    _sign_in(client, role="reception", user_id=1, permissions=["all_collections"])

    assert _create_ptp(client, member_id).status_code == 302

    _assert_ptp_recorded(app, member_id)
    assert _open_exceptions(app, member_id) == []


def test_repeat_requests_do_not_stack_duplicate_exceptions(app, client):
    member_id = _seed_arrears_member(app, 3)
    _sign_in(client, role="reception", user_id=1, permissions=["all_collections"])

    _create_ptp(client, member_id)
    _create_ptp(client, member_id)

    assert len(_open_exceptions(app, member_id)) == 1


def test_escalated_request_reaches_the_manager_queue(app, client):
    member_id = _seed_arrears_member(app, 3)
    _sign_in(client, role="reception", user_id=1, permissions=["all_collections"])
    _create_ptp(client, member_id)

    _sign_in(client, role="manager", user_id=MANAGER_ID)
    response = client.get("/collections/exceptions")

    assert response.status_code == 200
    assert b"Deep Arrears" in response.data
    assert f"R300.00 by {PROMISE_DATE}".encode() in response.data


# ── An approved exception grants time-limited access ─────────────────────────
#
# Approving a Rule §7 exception is the manager override the rule reserves to
# management. The rule engine is the single access authority (the turnstile
# re-evaluates it live), so the approval has to be visible to it - a flag on the
# member would be overruled at the door. Access runs to the promise date of the
# arrangement the exception was raised for, and ends early if it is broken.

def _escalate_three_months_owing(app, client, *, days_ahead=7):
    """A 3-month member with no DebiCheck whose arrangement was escalated.

    Returns (member_id, exception_id, ptp_id, promise_date). The promise date is
    relative to today so these tests do not depend on the calendar.
    """
    from datetime import date, timedelta

    member_id = _seed_arrears_member(app, 3)
    promise_date = date.today() + timedelta(days=days_ahead)
    _sign_in(client, role="reception", user_id=1, permissions=["all_collections"])
    response = client.post(
        f"/members/{member_id}/ptp/create",
        data={
            "promise_amount": "300", "promise_date": promise_date.isoformat(),
            "payment_method": "cash", "arrangement_type": "partial",
            "notes": "Client offered R300 a month", "arrears_amount": "3000",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    exceptions = _open_exceptions(app, member_id)
    assert len(exceptions) == 1
    with app.app_context():
        ptp_id = get_db().execute(
            "SELECT id FROM ptp_agreements WHERE member_id = ?", (member_id,)
        ).fetchone()["id"]
    return member_id, exceptions[0]["id"], ptp_id, promise_date


def _decide_exception(client, exception_id, decision, notes="Manager decision"):
    _sign_in(client, role="manager", user_id=MANAGER_ID)
    response = client.post(
        f"/collections/exceptions/{exception_id}/decide",
        data={"decision": decision, "notes": notes},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _access(app, member_id, today=None):
    from datetime import date

    from onecpase.collections_engine import evaluate_member_access

    with app.app_context():
        return evaluate_member_access(get_db(), member_id, today=today or date.today())["access"]


def _arrangement_approval(app, ptp_id):
    with app.app_context():
        return get_db().execute(
            "SELECT manager_approval_status FROM ptp_agreements WHERE id = ?", (ptp_id,)
        ).fetchone()["manager_approval_status"]


def _exception_status(app, exception_id):
    with app.app_context():
        return get_db().execute(
            "SELECT status FROM collection_exceptions WHERE id = ?", (exception_id,)
        ).fetchone()["status"]


def test_pending_exception_does_not_grant_access(app, client):
    member_id, *_ = _escalate_three_months_owing(app, client)

    assert _access(app, member_id) == "BLOCKED"


def test_declined_exception_does_not_grant_access(app, client):
    member_id, exception_id, *_ = _escalate_three_months_owing(app, client)

    _decide_exception(client, exception_id, "declined", "No basis for an exception")

    assert _access(app, member_id) == "BLOCKED"


def test_approved_exception_grants_access_through_the_promise_date_only(app, client):
    from datetime import timedelta

    member_id, exception_id, _, promise_date = _escalate_three_months_owing(app, client)

    _decide_exception(client, exception_id, "approved", "Allowed while the R300 plan runs")

    assert _access(app, member_id) == "ALLOWED"
    assert _access(app, member_id, today=promise_date) == "ALLOWED"
    assert _access(app, member_id, today=promise_date + timedelta(days=1)) == "BLOCKED"


def test_approved_exception_ends_when_the_arrangement_is_broken(app, client):
    member_id, exception_id, ptp_id, _ = _escalate_three_months_owing(app, client)
    _decide_exception(client, exception_id, "approved", "Allowed while the R300 plan runs")
    assert _access(app, member_id) == "ALLOWED"

    with app.app_context():
        db = get_db()
        db.execute("UPDATE ptp_agreements SET ptp_status = 'broken' WHERE id = ?", (ptp_id,))
        db.commit()

    assert _access(app, member_id) == "BLOCKED"


# The same request raises an arrangement approval and an exception. They are
# two cards in the queue for one decision, so deciding either must settle both.

def test_deciding_the_exception_also_decides_the_linked_arrangement(app, client):
    _, exception_id, ptp_id, _ = _escalate_three_months_owing(app, client)

    _decide_exception(client, exception_id, "approved")

    assert _arrangement_approval(app, ptp_id) == "approved"


def test_declining_the_exception_also_declines_the_linked_arrangement(app, client):
    _, exception_id, ptp_id, _ = _escalate_three_months_owing(app, client)

    _decide_exception(client, exception_id, "declined", "No basis for an exception")

    assert _arrangement_approval(app, ptp_id) == "declined"


def test_approving_the_arrangement_also_decides_the_linked_exception(app, client):
    member_id, exception_id, ptp_id, _ = _escalate_three_months_owing(app, client)

    _sign_in(client, role="manager", user_id=MANAGER_ID)
    client.post(
        f"/members/{member_id}/ptp/{ptp_id}/approve",
        data={"decision": "approved", "approval_notes": "Signed off", "return_to": "exception_queue"},
    )

    assert _exception_status(app, exception_id) == "approved"
    assert _access(app, member_id) == "ALLOWED"


def test_second_arrangement_during_an_approved_exception_is_escalated_again(app, client):
    from datetime import date, timedelta

    member_id, exception_id, _, promise_date = _escalate_three_months_owing(app, client)
    _decide_exception(client, exception_id, "approved", "Allowed while the R300 plan runs")
    assert _access(app, member_id) == "ALLOWED"

    _sign_in(client, role="reception", user_id=1, permissions=["all_collections"])
    response = client.post(
        f"/members/{member_id}/ptp/create",
        data={
            "promise_amount": "300", "promise_date": (date.today() + timedelta(days=14)).isoformat(),
            "payment_method": "cash", "arrangement_type": "partial",
            "notes": "Asked to push the date out", "arrears_amount": "3000",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    # Reception cannot stretch access by stacking arrangements: the new one
    # needs its own decision, and the approved window does not move meanwhile.
    assert len(_open_exceptions(app, member_id)) == 1
    assert _access(app, member_id, today=promise_date + timedelta(days=1)) == "BLOCKED"


# The engine saying ALLOWED is not enough on its own: the turnstile also refuses
# a stored 'blocked' status, and the 15-minute sweep decides access separately
# and would put the block straight back.

def test_turnstile_admits_a_member_with_an_approved_exception(app, client):
    from onecpase.turnstile import evaluate_access

    member_id, exception_id, *_ = _escalate_three_months_owing(app, client)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE members SET access_credential = 'CRED-EX-1' WHERE id = ?", (member_id,))
        db.commit()
        assert evaluate_access(db, "CRED-EX-1")[1] == "denied"

    _decide_exception(client, exception_id, "approved", "Allowed while the R300 plan runs")

    with app.app_context():
        assert evaluate_access(get_db(), "CRED-EX-1")[1] == "granted"


def test_scheduled_sweep_does_not_re_block_an_approved_exception(app, client):
    from datetime import date

    from onecpase.ptp import _run_collections_automations

    member_id, exception_id, *_ = _escalate_three_months_owing(app, client)
    _decide_exception(client, exception_id, "approved", "Allowed while the R300 plan runs")

    with app.app_context():
        db = get_db()
        _run_collections_automations(db, date.today().isoformat())
        db.commit()
        stored = db.execute(
            "SELECT gym_access_status FROM members WHERE id = ?", (member_id,)
        ).fetchone()["gym_access_status"]

    assert stored == "temp_unblocked"


# ── Dashboard signals ─────────────────────────────────────────────────────────

def test_dashboard_surfaces_the_pending_manager_queue(app, client):
    """A manager needs a signal that something is waiting; before this the
    queue could only be found by navigating to it directly."""
    _seed(app)
    _sign_in(client, permissions=["all_collections"])

    response = client.get("/ptp/")

    assert response.status_code == 200
    assert b"Awaiting Manager" in response.data
    assert b"Contact Rate" in response.data
    assert b"Calls Today" in response.data


def test_dashboard_call_kpis_match_the_call_queue_definitions(app):
    from datetime import date

    from onecpase.collections import daily_call_progress

    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO users (id, username, password_hash, full_name, role, active,
                   is_collections_caller)
               VALUES (7, 'caller', 'x', 'Caller One', 'reception', 1, 1)"""
        )
        today = date.today().isoformat()
        # One task per member per day, so each outcome needs its own member.
        for index, (status, contacted) in enumerate(
            (("completed", 1), ("completed", 0), ("pending", 0))
        ):
            member_id = db.execute(
                """INSERT INTO members (member_ref, first_name, last_name, id_number,
                       contact, member_status)
                   VALUES (?, 'Queue', ?, ?, ?, 'Active')""",
                (
                    f"ELE-{index + 21:010d}",
                    f"Member {index}",
                    f"ID00{index + 21}",
                    f"07100000{index + 21}",
                ),
            ).lastrowid
            task_id = db.execute(
                """INSERT INTO collection_call_tasks
                   (task_date, member_id, queue_type, priority, assigned_to, status,
                    successful_contact)
                   VALUES (?, ?, 'old_owing', ?, 7, ?, ?)""",
                (today, member_id, index + 1, status, contacted),
            ).lastrowid
            if contacted:
                db.execute(
                    """INSERT INTO pbx_call_logs (external_call_id, collection_task_id, direction, talk_duration)
                       VALUES (?, ?, 'outbound', 80)""",
                    (f"pbx-{task_id}", task_id),
                )
        db.commit()

        progress = daily_call_progress(db)

    assert progress["assigned"] == 3
    assert progress["calls"] == 2
    assert progress["contacts"] == 1
    # One caller against the configured 100-call target.
    assert progress["daily_target"] == progress["target_per_caller"]
    assert progress["contact_rate_pct"] == 50.0
    assert progress["queue_counts"]["old_owing"] == 3


def test_call_kpis_are_safe_with_no_callers_configured(app):
    from onecpase.collections import daily_call_progress

    with app.app_context():
        progress = daily_call_progress(get_db())

    assert progress["daily_target"] == 0
    assert progress["target_progress_pct"] == 0.0
    assert progress["contact_rate_pct"] == 0.0


def test_discounted_promise_opens_a_discount_request_that_follows_the_manager(app, client):
    member_id = _seed_arrears_member(app, 3)
    _sign_in(client, role="reception", user_id=1, permissions=["all_collections"])
    response = client.post(
        f"/members/{member_id}/ptp/create",
        data={"promise_amount": "300", "promise_date": PROMISE_DATE, "payment_method": "cash",
              "arrangement_type": "partial", "notes": "R300 a month", "arrears_amount": "1500",
              "discount_pct": "25"},
    )
    assert response.status_code == 302

    with app.app_context():
        db = get_db()
        ptp = db.execute("SELECT * FROM ptp_agreements WHERE member_id=?", (member_id,)).fetchone()
        assert float(ptp["discount_pct"] or 0) == 25
        request_row = db.execute(
            "SELECT * FROM collection_discount_requests WHERE ptp_id=?", (ptp["id"],)).fetchone()
        assert request_row["status"] == "PENDING"
        assert request_row["original_balance"] == ptp["arrears_amount"]
        assert request_row["adjusted_balance"] == ptp["discounted_balance"]
        exception_id = db.execute(
            "SELECT id FROM collection_exceptions WHERE member_id=? AND status='pending'", (member_id,)).fetchone()["id"]

    _sign_in(client, role="manager", user_id=2, permissions=["all_collections"])
    client.post(f"/collections/exceptions/{exception_id}/decide", data={"decision": "approved", "notes": "OK"})
    with app.app_context():
        decided = get_db().execute(
            "SELECT status, decision_reason FROM collection_discount_requests WHERE ptp_id=?", (ptp["id"],)).fetchone()
        assert (decided["status"], decided["decision_reason"]) == ("APPROVED", "OK")
