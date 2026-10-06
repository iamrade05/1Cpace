import pytest
from datetime import date

from onecpase.collections_decision import (
    DECISION_CONDITIONS, DECISION_ELIGIBLE, DECISION_MANAGER, DECISION_NO_BALANCE,
    DECISION_NO_DISCOUNT, decide_offer,
)
from onecpase.database import get_db

POLICY = {"recovery_minimum_amount": 1500.0, "recovery_minimum_from_months": 7}


def offer(arrears, months, **kw):
    kw.setdefault("policy", POLICY)
    return decide_offer(arrears=arrears, months_owing=months, **kw)


def test_one_month_owing_has_no_discount():
    r = offer(500, 1)
    assert (r["decision"], r["discount_percent"], r["total_to_collect"]) == (DECISION_NO_DISCOUNT, 0, 500)


def test_nothing_owing():
    assert offer(0, 3)["decision"] == DECISION_NO_BALANCE


@pytest.mark.parametrize("months,percent", [(2, 25), (3, 35), (4, 35), (5, 40), (6, 50)])
def test_discount_tiers(months, percent):
    r = offer(1000, months)
    assert r["discount_percent"] == percent
    assert r["discount_amount"] == round(1000 * percent / 100, 2)
    assert r["total_to_collect"] == 1000 - r["discount_amount"]


def test_panel_uses_the_same_tiers_as_the_live_engine():
    from onecpase.collections_engine import arrears_discount_policy

    for months in range(0, 10):
        live = arrears_discount_policy(months)
        r = offer(1000, months, policy={"recovery_minimum_amount": 0, "recovery_minimum_from_months": 99})
        assert r["discount_percent"] == live["discount_percent"], months


def test_four_months_and_over_need_a_manager_like_the_live_policy():
    ok = offer(1000, 3, upfront_offered=100, upfront_received=100, debicheck_status="approved")
    assert ok["decision"] == DECISION_ELIGIBLE
    for months in (4, 5, 6):
        r = offer(1000, months, upfront_offered=100, upfront_received=100, debicheck_status="approved")
        assert r["decision"] == DECISION_MANAGER


def test_upfront_is_whatever_the_client_can_offer():
    # No required percentage: any verified amount (or none) is acceptable.
    for amount in (0, 1, 80, 600):
        r = offer(1000, 2, upfront_offered=amount, upfront_received=amount, debicheck_status="approved")
        assert r["decision"] == DECISION_ELIGIBLE, amount
        assert r["remaining_to_collect"] == 750 - amount
    assert "upfront_required" not in offer(1000, 2)


def test_conditions_name_exactly_what_is_missing():
    pending = offer(1000, 2, upfront_offered=225, debicheck_status="submitted")
    assert pending["decision"] == DECISION_CONDITIONS
    assert "Upfront payment not verified" in pending["conditions"]
    assert "DebiCheck awaiting authorisation" in pending["conditions"]
    assert "DebiCheck not set up" in offer(1000, 2)["conditions"]
    assert "DebiCheck failed - a new mandate is needed" in offer(1000, 2, debicheck_status="failed")["conditions"]
    assert "Bank statement review outstanding" in offer(1000, 2, returning_member=True)["conditions"]


def test_a_confirmed_debicheck_clears_its_condition():
    r = offer(1000, 2, debicheck_status="approved")
    assert not any("DebiCheck" in c for c in r["conditions"])


@pytest.mark.parametrize("arrears,total", [(3000, 1500), (5000, 2500), (6000, 3000)])
def test_recovery_accounts_keep_the_minimum(arrears, total):
    assert offer(arrears, 7)["total_to_collect"] == total


def test_discount_never_takes_recovery_below_the_minimum():
    r = offer(2000, 8)  # 50% would leave R1,000
    assert r["total_to_collect"] == 1500 and r["discount_amount"] == 500


def test_balance_below_the_minimum_is_flagged_and_never_over_demanded():
    r = offer(1200, 9)
    assert r["total_to_collect"] == 1200 and r["decision"] == DECISION_MANAGER
    assert "Balance below the recovery minimum" in r["conditions"]


def test_upfront_counts_toward_the_recovery_total():
    r = offer(5000, 7, upfront_offered=750, upfront_received=750)
    assert r["total_to_collect"] == 2500 and r["remaining_to_collect"] == 1750


def test_remaining_never_goes_negative():
    assert offer(1000, 2, upfront_received=5000)["remaining_to_collect"] == 0


def test_six_months_can_start_the_minimum_when_configured():
    six = {"recovery_minimum_amount": 1500.0, "recovery_minimum_from_months": 6}
    assert offer(2000, 6, policy=six)["total_to_collect"] == 1500
    assert offer(2000, 6)["total_to_collect"] == 1000


def test_returning_member_needs_debicheck_and_a_bank_statement_review():
    r = offer(1000, 2, returning_member=True)
    assert "Returning member needs a confirmed DebiCheck" in r["conditions"]
    done = offer(1000, 2, returning_member=True, debicheck_status="active", bank_statement_status="reviewed")
    assert done["decision"] == DECISION_ELIGIBLE


def test_settle_in_full_needs_the_whole_total():
    short = offer(1000, 2, intention="settle", upfront_received=500)
    assert "Full settlement payment not received" in short["conditions"]
    assert offer(1000, 2, intention="settle", upfront_received=750)["decision"] == DECISION_ELIGIBLE


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
    dispute = offer(1000, 2, flags={"open_dispute": True}, **base)
    assert dispute["decision"] == DECISION_CONDITIONS
    assert any(c.startswith("Dispute under review") for c in dispute["conditions"])
    claim = offer(1000, 2, flags={"unverified_payment_claim": True}, **base)
    assert "Client's payment claim not yet verified" in claim["conditions"]
    for flag in ("broken_ptp", "legal_referral"):
        assert offer(1000, 2, flags={flag: True}, **base)["decision"] == DECISION_MANAGER
    no_statement = offer(1000, 2, flags={"bank_statement_on_file": False}, **base)
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
    with app.test_client() as client:
        _login(client, task)
        client.post(f"/collections/call-queue/{task['id']}/result", data={
            "outcome": "promised_to_pay", "intention": "instalments", "upfront_offered": "75",
            "notes": "will pay Friday"})
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT notes, member_id FROM collection_call_tasks WHERE id=?", (task["id"],)).fetchone()
        assert row["notes"].startswith("Conditional offer [") and "will pay Friday" in row["notes"]
        bal = db.execute(
            "SELECT outstanding_balance, amount_paid FROM collections WHERE member_id=?", (row["member_id"],)
        ).fetchone()
        assert (bal["outstanding_balance"], bal["amount_paid"]) == (250, 0)


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


def test_no_second_mandate_is_requested_when_one_is_already_in_progress(app, client):
    mid = _member_with_bank_details(app, mandate_status="pending")
    _admin(client)
    resp = _post_ptp(client, mid)
    assert resp.status_code == 302 and "/debicheck/add" not in resp.headers["Location"]


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
