import pytest
from datetime import date

from onecpase.collections_decision import (
    DECISION_CONDITIONS, DECISION_ELIGIBLE, DECISION_MANAGER, DECISION_NO_BALANCE,
    DECISION_NO_DISCOUNT, decide_offer,
)
from onecpase.database import get_db

POLICY = {}


def offer(arrears, months, **kw):
    kw.setdefault("monthly_affordable", 5000)
    kw.setdefault("mandate_state", "active" if kw.get("debicheck_status") in ("approved", "active") else "none")
    return decide_offer(arrears=arrears, months_owing=months, **kw)


def test_under_three_months_owing_has_no_discount():
    for months in (1, 2):
        r = offer(500, months)
        assert (r["decision"], r["discount_percent"], r["total_to_collect"]) == (DECISION_NO_DISCOUNT, 0, 500)


def test_nothing_owing():
    assert offer(0, 3)["decision"] == DECISION_NO_BALANCE


@pytest.mark.parametrize("arrears,upfront,percent", [
    (1000, 0, 25), (1000, 500, 50),      # category 1
    (2000, 0, 35), (2000, 1000, 65),     # category 2
    (4000, 0, 30),                       # category 3 without upfront
    (1000, 100, 25),                     # under 50% upfront: the no-upfront rate
])
def test_category_discounts(arrears, upfront, percent):
    r = offer(arrears, 3, upfront_offered=upfront, upfront_received=upfront)
    assert r["discount_percent"] == percent
    assert r["discount_amount"] == round((arrears - upfront) * percent / 100, 2)
    assert r["total_to_collect"] == arrears - r["discount_amount"]
    assert r["ptp_balance"] == round(arrears - upfront - r["discount_amount"], 2)


def test_the_discount_depends_on_months_owing_not_the_old_tiers():
    for months in (3, 6, 9, 16):
        assert offer(1000, months)["discount_percent"] == 25


def test_no_manager_discretion_for_old_accounts():
    r = offer(5000, 16, upfront_offered=2600, upfront_received=2600, debicheck_status="approved")
    assert r["decision"] == DECISION_ELIGIBLE
    assert r["category"] == 3 and r["ptp_balance"] == 0


def test_no_recovery_floor_applies():
    r = offer(700, 3, upfront_offered=350, upfront_received=350, debicheck_status="approved")
    assert r["total_to_collect"] == 525 and r["ptp_balance"] == 175


def test_the_screenshot_case():
    r = offer(4489.28, 16, upfront_offered=2600, upfront_received=2600, returning_member=True,
              bank_statement_status="reviewed")
    assert (r["discount_percent"], r["total_to_collect"], r["remaining_to_collect"]) == (50, 2600, 0)
    assert "DebiCheck not set up" in r["conditions"]  # still needed with R0 left


def test_upfront_is_whatever_the_client_can_offer():
    for amount in (0, 1, 80, 600):
        r = offer(1000, 3, upfront_offered=amount, upfront_received=amount, debicheck_status="approved")
        assert r["decision"] == DECISION_ELIGIBLE, amount
        assert r["remaining_to_collect"] == r["ptp_balance"]
    assert "upfront_required" not in offer(1000, 3)


def test_conditions_name_exactly_what_is_missing():
    pending = offer(1000, 3, upfront_offered=225, debicheck_status="submitted")
    assert pending["decision"] == DECISION_CONDITIONS
    assert "Upfront payment not verified" in pending["conditions"]
    assert "DebiCheck awaiting authorisation" in pending["conditions"]
    assert "DebiCheck not set up" in offer(1000, 3)["conditions"]
    assert "DebiCheck failed - a new mandate is needed" in offer(1000, 3, debicheck_status="failed")["conditions"]
    assert "Bank statement review outstanding" in offer(1000, 3, returning_member=True)["conditions"]


def test_a_confirmed_debicheck_clears_its_condition():
    r = offer(1000, 3, debicheck_status="approved")
    assert not any("DebiCheck" in c for c in r["conditions"])


def test_remaining_never_goes_negative():
    assert offer(1000, 3, upfront_received=5000)["remaining_to_collect"] == 0


def test_returning_member_needs_debicheck_and_a_bank_statement_review():
    r = offer(1000, 3, returning_member=True)
    assert "Returning member needs a confirmed DebiCheck" in r["conditions"]
    done = offer(1000, 3, returning_member=True, debicheck_status="active", bank_statement_status="reviewed")
    assert done["decision"] == DECISION_ELIGIBLE


def test_settle_in_full_needs_the_whole_total():
    short = offer(1000, 3, intention="settle", upfront_received=500)
    assert "Full settlement payment not received" in short["conditions"]
    assert offer(1000, 3, intention="settle", upfront_received=750)["decision"] == DECISION_ELIGIBLE


def test_debicheck_approval_lapses_after_one_day():
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 7, 12, 0)
    for sent, lapsed in ((now - timedelta(hours=23), False), (now - timedelta(hours=25), True)):
        r = offer(1000, 2, debicheck_status="submitted", debicheck_submitted_at=sent.isoformat(timespec="seconds"), now=now)
        expired = "DebiCheck approval expired after 1 day - send it again" in r["conditions"]
        assert expired is lapsed
        assert ("DebiCheck awaiting authorisation" in r["conditions"]) is (not lapsed)
    # a confirmed mandate never lapses
    ok = offer(1000, 2, debicheck_status="approved", debicheck_submitted_at="2020-01-01T00:00:00", now=now)
    assert not any("DebiCheck" in c for c in ok["conditions"])


def test_saved_but_unsent_mandate_is_called_out():
    assert "DebiCheck saved but not yet sent to the bank" in offer(1000, 2, debicheck_status="pending")["conditions"]


def test_every_open_item_has_an_owner_and_a_next_step():
    r = offer(1000, 2, upfront_offered=100, debicheck_status="submitted")
    items = {c["key"]: c for c in r["checklist"]}
    assert items["upfront"]["owner"] == "receptionist" and items["upfront"]["action"]
    assert items["debicheck"]["owner"] == "client" and "banking app" in items["debicheck"]["action"]
    assert all(not c["met"] for c in items.values())
    done = {c["key"]: c for c in offer(1000, 2, debicheck_status="approved")["checklist"]}
    assert done["debicheck"]["met"] is True


def test_account_flags_surface_before_the_client_agrees():
    base = dict(upfront_offered=0, debicheck_status="approved")
    dispute = offer(1000, 3, flags={"open_dispute": True}, **base)
    assert dispute["decision"] == DECISION_CONDITIONS
    assert any(c.startswith("Dispute under review") for c in dispute["conditions"])
    claim = offer(1000, 3, flags={"unverified_payment_claim": True}, **base)
    assert "Client's payment claim not yet verified" in claim["conditions"]
    for flag in ("broken_ptp", "legal_referral"):
        assert offer(1000, 3, flags={flag: True}, **base)["decision"] == DECISION_MANAGER
    no_statement = offer(1000, 3, flags={"bank_statement_on_file": False}, **base)
    assert no_statement["decision"] == DECISION_ELIGIBLE  # a warning, not a blocker
    assert any(c["key"] == "bank_on_file" and not c["blocking"] for c in no_statement["checklist"])


def test_a_lapsed_mandate_is_offered_again_after_a_ptp(app, client):
    mid = _member_with_bank_details(app, mandate_status="submitted")
    with app.app_context():
        get_db().execute("UPDATE debicheck_mandates SET submitted_at='2020-01-01T00:00:00' WHERE member_id=?", (mid,))
        get_db().commit()
    _admin(client)
    assert "/debicheck/add" in _post_ptp(client, mid).headers["Location"]


# ── Call profile wiring ────────────────────────────────────────────────────
def _caller_task(app):
    from tests.test_collections_call_queue import _seed_callers_and_members

    with app.app_context():
        _seed_callers_and_members(2)
        db = get_db()
        member = db.execute("SELECT id FROM members ORDER BY id LIMIT 1").fetchone()
        cursor = db.execute(
            """INSERT INTO collection_call_tasks (task_date, member_id, queue_type, assigned_to, next_channel)
               VALUES (?, ?, 'failed_debit', 10, 'CALL')""",
            (date.today().isoformat(), member["id"]),
        )
        db.commit()
        return {"id": cursor.lastrowid, "assigned_to": 10}


def _login(client, task):
    with client.session_transaction() as sess:
        sess["user_id"] = task["assigned_to"]
        sess["username"] = "caller"
        sess["role"] = "reception"
        sess["permissions"] = ["collections_call_queue"]


def test_offer_preview_endpoint_uses_the_verified_account(app):
    task = _caller_task(app)
    with app.test_client() as client:
        _login(client, task)
        url = f"/collections/call-queue/{task['id']}/offer-preview"
        data = client.get(url + "?upfront_offered=100&intention=instalments").get_json()
        assert data["original_arrears"] == 250 and data["months_owing"] == 1
        # arrears can never be posted in: a forged value is ignored
        assert client.get(url + "?arrears=1&months_owing=9").get_json()["original_arrears"] == 250


def test_promise_to_pay_records_the_offer_without_changing_the_balance(app):
    task = _caller_task(app)
    with app.app_context():
        db = get_db()
        mid = db.execute("SELECT member_id FROM collection_call_tasks WHERE id=?", (task["id"],)).fetchone()[0]
        db.execute("INSERT INTO nupay_mandate_checks (member_id, state, message) VALUES (?, 'none', 'x')", (mid,))
        db.commit()
    with app.test_client() as client:
        _login(client, task)
        client.post(f"/collections/call-queue/{task['id']}/result", data={
            "outcome": "promised_to_pay", "intention": "instalments", "upfront_offered": "75",
            "monthly_affordable": "500", "notes": "will pay Friday"})
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT notes, member_id FROM collection_call_tasks WHERE id=?", (task["id"],)).fetchone()
        assert row["notes"].startswith("Conditional offer [") and "will pay Friday" in row["notes"]
        bal = db.execute(
            "SELECT outstanding_balance, amount_paid FROM collections WHERE member_id=?", (row["member_id"],)
        ).fetchone()
        assert (bal["outstanding_balance"], bal["amount_paid"]) == (250, 0)


def test_arrangement_amount_comes_from_what_the_client_can_afford():
    r = offer(2000, 3, upfront_offered=1000, upfront_received=1000, normal_instalment=300,
              monthly_affordable=387.5)
    plan = r["arrangement"]
    assert r["ptp_balance"] == 350
    assert (plan["payments"], plan["monthly_arrears"], plan["total_monthly"]) == (4, 87.5, 387.5)


def test_affordable_amount_must_cover_more_than_the_normal_instalment():
    r = offer(2000, 3, upfront_offered=1000, upfront_received=1000, normal_instalment=300,
              monthly_affordable=300)
    assert r["arrangement"] is None
    assert any("does not cover" in c for c in r["conditions"])
    assert any("above the normal instalment" in c for c in offer(2000, 3, monthly_affordable=0)["conditions"])


def test_an_unchecked_mandate_position_asks_for_the_nupay_check():
    r = offer(2000, 3, mandate_state="unknown")
    assert "Check NuPay for an existing mandate" in r["conditions"]


def test_active_mandate_loads_one_mandate_and_none_loads_two():
    active = offer(2000, 3, upfront_offered=1000, normal_instalment=300, mandate_state="active")
    none = offer(2000, 3, upfront_offered=1000, normal_instalment=300, mandate_state="none")
    assert [m["purpose"] for m in active["mandates_to_load"]] == ["Arrangement (PTP)"]
    assert [m["purpose"] for m in none["mandates_to_load"]] == ["Normal instalment", "Arrangement (PTP)"]


def test_long_terms_need_a_manager():
    r = offer(2000, 3, upfront_offered=1000, upfront_received=1000, debicheck_status="approved",
              normal_instalment=300, monthly_affordable=330)  # R30 a month over R350 = 12 payments
    assert r["arrangement"]["payments"] == 12 and r["decision"] != DECISION_MANAGER
    r = offer(2000, 3, upfront_offered=1000, upfront_received=1000, debicheck_status="approved",
              normal_instalment=300, monthly_affordable=325)
    assert r["arrangement"]["payments"] > 12 and r["decision"] == DECISION_MANAGER


def _call_task_for_arrangement(app, check_state=None):
    task = _caller_task(app)
    with app.app_context():
        db = get_db()
        mid = db.execute("SELECT member_id FROM collection_call_tasks WHERE id=?", (task["id"],)).fetchone()[0]
        db.execute("UPDATE members SET monthly_installment=150 WHERE id=?", (mid,))
        if check_state:
            db.execute("INSERT INTO nupay_mandate_checks (member_id, state, message) VALUES (?,?, 'x')",
                       (mid, check_state))
        db.commit()
    task["member_id"] = mid
    return task


def _save_arrangement_call(app, task):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"], sess["username"], sess["role"] = task["assigned_to"], "caller", "admin"
        return client.post(f"/collections/call-queue/{task['id']}/result", data={
            "outcome": "promised_to_pay", "intention": "instalments", "upfront_offered": "75",
            "monthly_affordable": "500", "notes": "will pay"})


def test_saving_the_call_without_the_nupay_check_is_refused(app):
    task = _call_task_for_arrangement(app)
    resp = _save_arrangement_call(app, task)
    assert resp.status_code == 302 and "/debicheck/add" not in resp.headers["Location"]
    with app.app_context():
        row = get_db().execute("SELECT status FROM collection_call_tasks WHERE id=?", (task["id"],)).fetchone()
        assert row["status"] != "completed"
        assert get_db().execute("SELECT COUNT(*) FROM ptp_agreements").fetchone()[0] == 0


def test_saving_the_call_goes_straight_to_two_mandates_when_nupay_has_none(app):
    task = _call_task_for_arrangement(app, "none")
    resp = _save_arrangement_call(app, task)
    location = resp.headers["Location"]
    assert "/debicheck/add" in location and "Normal+instalment:150.00" in location
    assert "Arrangement+(PTP):175.00" in location
    with app.app_context():
        db = get_db()
        ptp = db.execute("SELECT * FROM ptp_agreements").fetchone()
        assert ptp["grant_status"] == "awaiting_debicheck"
        assert db.execute("SELECT COUNT(*) FROM ptp_mandate_links WHERE ptp_id=?", (ptp["id"],)).fetchone()[0] == 2


def test_an_active_nupay_mandate_means_only_the_arrangement_mandate(app):
    task = _call_task_for_arrangement(app, "active")
    location = _save_arrangement_call(app, task).headers["Location"]
    assert "Arrangement+(PTP):175.00" in location and "Normal" not in location


def test_the_nupay_check_endpoint_records_what_nupay_says(app, monkeypatch):
    task = _call_task_for_arrangement(app)
    monkeypatch.setattr("onecpase.debicheck.check_member_on_nupay", lambda member: {
        "state": "active", "status": "approved", "contract_reference": "C1", "message": "NuPay mandate C1: Active."})
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"], sess["role"] = task["assigned_to"], "admin"
        data = client.post(f"/collections/call-queue/{task['id']}/nupay-check").get_json()
    assert data["state"] == "active"
    with app.app_context():
        row = get_db().execute("SELECT state FROM nupay_mandate_checks WHERE member_id=?",
                               (task["member_id"],)).fetchone()
        assert row["state"] == "active"


# ── PTP -> DebiCheck hand-off ──────────────────────────────────────────────
def _member_with_bank_details(app, mandate_status=None):
    with app.app_context():
        db = get_db()
        mid = db.execute(
            """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
                   member_status, monthly_installment, bank, account_number, branch_code, account_type)
               VALUES ('ELE-0000000999', 'Thandi', 'Nkosi', '9001010000000', '0710000999',
                   'Active', 399, 'FNB', '62000000001', '250655', 'Cheque/Current')"""
        ).lastrowid
        db.execute(
            "INSERT INTO collections (member_id, outstanding_balance, amount_paid, collection_date, status, notes) "
            "VALUES (?, 1000, 0, '2026-07-15', 'failed', 'type=Recurring Fee')", (mid,))
        if mandate_status:
            db.execute(
                """INSERT INTO debicheck_mandates (member_id, merchant_id, auth_type,
                       client_ref1, client_ref2, account_name, account_type,
                       account_number, branch_code, instalment_amount, submit_date, status)
                   VALUES (?, 'm1', 'cell', 'ref1', '1234', 'Thandi Nkosi', '1',
                           '123456789', '123456', 500, '2026-08-01', ?)""",
                (mid, mandate_status))
        db.commit()
        return mid


def _admin(client):
    from test_staff_passwords import _make_admin
    _make_admin(client)
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["role"] = "admin"
        sess["username"] = "admin"


def _post_ptp(client, mid):
    return client.post(f"/members/{mid}/ptp/create", data={
        "promise_amount": "300", "promise_date": "2026-12-01", "payment_method": "debicheck",
        "arrangement_type": "partial", "notes": "will pay", "arrears_amount": "1000"})


def test_saving_a_ptp_opens_debicheck_for_a_client_without_a_mandate(app, client):
    mid = _member_with_bank_details(app)
    _admin(client)
    resp = _post_ptp(client, mid)
    assert resp.status_code == 302
    assert f"/debicheck/add?member_id={mid}" in resp.headers["Location"]


def test_debicheck_form_arrives_filled_from_the_clients_record(app, client):
    mid = _member_with_bank_details(app)
    _admin(client)
    page = client.get(f"/debicheck/add?member_id={mid}").get_data(as_text=True)
    assert f'value="{mid}" selected' in page  # the client is already chosen
    assert 'data-bank="FNB"' in page and 'data-account-number="62000000001"' in page
    assert 'data-branch="250655"' in page and 'data-amount="399' in page
    assert "prefillStandalone();" in page  # and the page fills the editable fields


def test_a_pending_mandate_does_not_stand_in_for_the_arrangement_mandate(app, client):
    mid = _member_with_bank_details(app, mandate_status="pending")
    _admin(client)
    resp = _post_ptp(client, mid)
    assert resp.status_code == 302 and "/debicheck/add" in resp.headers["Location"]
    with app.app_context():
        row = get_db().execute("SELECT grant_status FROM ptp_agreements").fetchone()
        assert row["grant_status"] == "awaiting_debicheck"


def _linked_ptp(app, client, mandate_status):
    """A PTP whose only requirement is satisfied by a mandate in this status."""
    from onecpase.ptp_grant import link_mandate, evaluate_grant

    mid = _member_with_bank_details(app)
    _admin(client)
    _post_ptp(client, mid)
    with app.app_context():
        db = get_db()
        ptp_id = db.execute("SELECT id FROM ptp_agreements").fetchone()["id"]
        db.execute("UPDATE ptp_agreements SET manager_approval_status='not_required', "
                   "upfront_amount=0 WHERE id=?", (ptp_id,))
        db.execute("DELETE FROM ptp_mandate_links WHERE ptp_id=?", (ptp_id,))
        db.execute("INSERT INTO ptp_mandate_links (ptp_id, purpose, amount) VALUES (?,?,?)",
                   (ptp_id, "Arrangement (PTP)", 300))
        mandate_id = db.execute(
            """INSERT INTO debicheck_mandates (member_id, merchant_id, auth_type, client_ref1,
                   client_ref2, account_name, account_type, account_number, branch_code,
                   instalment_amount, submit_date, status)
               VALUES (?, 'm1', 'cell', 'ref1', '1234', 'T N', '1', '123456789', '123456',
                       300, '2026-08-01', ?)""", (mid, mandate_status)).lastrowid
        assert link_mandate(db, ptp_id, "Arrangement (PTP)", mandate_id)
        status = evaluate_grant(db, ptp_id)
        db.commit()
        return status


def test_upfront_must_be_paid_and_verified_before_the_ptp_is_granted(app, client):
    from onecpase.ptp_grant import evaluate_grant

    mid = _member_with_bank_details(app)
    _admin(client)
    _post_ptp(client, mid)
    with app.app_context():
        db = get_db()
        ptp_id = db.execute("SELECT id FROM ptp_agreements").fetchone()["id"]
        db.execute("UPDATE ptp_agreements SET manager_approval_status='not_required', "
                   "upfront_amount=500 WHERE id=?", (ptp_id,))
        db.execute("DELETE FROM ptp_mandate_links WHERE ptp_id=?", (ptp_id,))
        mandate_id = db.execute(
            """INSERT INTO debicheck_mandates (member_id, merchant_id, auth_type, client_ref1,
                   client_ref2, account_name, account_type, account_number, branch_code,
                   instalment_amount, submit_date, status)
               VALUES (?, 'm1', 'cell', 'ref1', '1234', 'T N', '1', '123456789', '123456',
                       300, '2026-08-01', 'approved')""", (mid,)).lastrowid
        db.execute("INSERT INTO ptp_mandate_links (ptp_id, purpose, amount, mandate_id) "
                   "VALUES (?,?,?,?)", (ptp_id, "Arrangement (PTP)", 300, mandate_id))
        # Approved mandate alone is not enough while the upfront is unpaid.
        assert evaluate_grant(db, ptp_id) == "awaiting_prerequisites"
        rid = db.execute(
            """INSERT INTO ptp_receipts (ptp_id, member_id, original_name, stored_name, amount_paid,
                   payment_date, payment_method) VALUES (?,?,?,?,?,?,?)""",
            (ptp_id, mid, "r.pdf", "r.pdf", 500, "2026-10-12", "eft")).lastrowid
        db.commit()
    client.post(f"/members/{mid}/ptp/receipt/{rid}/verify", data={"decision": "verified"})
    with app.app_context():
        row = get_db().execute(
            "SELECT grant_status, upfront_verified, ptp_status FROM ptp_agreements WHERE id=?",
            (ptp_id,)).fetchone()
        assert row["grant_status"] == "granted" and row["upfront_verified"] == 1
        assert row["ptp_status"] == "pending"  # the upfront is not a repayment of the promise


def test_reception_recording_the_upfront_updates_the_payment_profile(app, client):
    mid = _member_with_bank_details(app)
    _admin(client)
    _post_ptp(client, mid)
    with app.app_context():
        db = get_db()
        ptp_id = db.execute("SELECT id FROM ptp_agreements").fetchone()["id"]
        db.execute("UPDATE ptp_agreements SET upfront_amount=500 WHERE id=?", (ptp_id,))
        db.commit()
    url = f"/members/{mid}/ptp/{ptp_id}/upfront"
    client.post(url, data={"amount_paid": "200", "payment_method": "cash"})
    with app.app_context():
        db = get_db()
        ptp = db.execute("SELECT upfront_verified, grant_status FROM ptp_agreements WHERE id=?",
                         (ptp_id,)).fetchone()
        paid = db.execute("SELECT SUM(amount_paid) AS t FROM collections WHERE member_id=?",
                          (mid,)).fetchone()["t"]
    assert paid == 200 and not ptp["upfront_verified"]  # part paid: not covered yet
    # More than is still due is refused.
    client.post(url, data={"amount_paid": "400", "payment_method": "cash"})
    client.post(url, data={"amount_paid": "300", "payment_method": "cash"})
    with app.app_context():
        db = get_db()
        ptp = db.execute("SELECT upfront_verified, grant_status FROM ptp_agreements WHERE id=?",
                         (ptp_id,)).fetchone()
        paid = db.execute("SELECT SUM(amount_paid) AS t FROM collections WHERE member_id=?",
                          (mid,)).fetchone()["t"]
    assert paid == 500 and ptp["upfront_verified"] == 1
    assert ptp["grant_status"] != "granted"  # DebiCheck is still outstanding


def test_submitted_mandate_does_not_grant_the_ptp(app, client):
    assert _linked_ptp(app, client, "submitted") == "awaiting_debicheck"


def test_declined_mandate_declines_the_ptp(app, client):
    assert _linked_ptp(app, client, "rejected") == "declined"


def test_approved_mandate_grants_the_ptp_once(app, client):
    from onecpase.ptp_grant import evaluate_grant

    assert _linked_ptp(app, client, "approved") == "granted"
    with app.app_context():
        db = get_db()
        ptp_id = db.execute("SELECT id FROM ptp_agreements").fetchone()["id"]
        first = db.execute("SELECT granted_at FROM ptp_agreements WHERE id=?", (ptp_id,)).fetchone()
        assert evaluate_grant(db, ptp_id) == "granted"
        again = db.execute("SELECT granted_at FROM ptp_agreements WHERE id=?", (ptp_id,)).fetchone()
        assert first["granted_at"] == again["granted_at"]


def test_a_failed_mandate_is_offered_again(app, client):
    mid = _member_with_bank_details(app, mandate_status="failed")
    _admin(client)
    assert "/debicheck/add" in _post_ptp(client, mid).headers["Location"]


def test_users_who_cannot_create_mandates_stay_on_the_member_page(app, client):
    mid = _member_with_bank_details(app)
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["role"] = "reception"
        sess["username"] = "reception"
        sess["permissions"] = ["all_collections"]
    resp = _post_ptp(client, mid)
    assert resp.status_code == 302 and "/debicheck/add" not in resp.headers["Location"]


def test_category_3_upfront_gives_half_off_the_whole_arrears():
    r = offer(3239.20, 10, upfront_offered=1000, upfront_received=1000)
    assert r['discount_percent'] == 50
    assert r['total_to_collect'] == 1619.60 and r['remaining_to_collect'] == 619.60
