from onecpase.database import get_db
from datetime import date, timedelta

from onecpase.queries import (
    calculate_cancellation_fee, calculate_cancellation_quote, cancellation_payment_dates,
)

AGREED = (date.today() + timedelta(days=7)).isoformat()


def _login(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "testuser"
        session["full_name"] = "Test User"
        session["role"] = "admin"
        session["permissions"] = []


def _insert_member(db, **overrides):
    fields = {
        "first_name": "Ada", "last_name": "Lovelace", "id_number": "8001015009087",
        "email": "ada@example.com", "package": "Premium", "member_status": "Active",
        "contract_duration": "36 Months", "monthly_installment": 500,
    }
    fields.update(overrides)
    cursor = db.execute(
        f"INSERT INTO members ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
        tuple(fields.values()),
    )
    db.commit()
    return cursor.lastrowid


# ── calculate_cancellation_fee — 12/24/36-month penalty schedule (§11.3) ──────

def test_fee_36_month_contract_penalty_tiers():
    assert calculate_cancellation_fee(36, 10, 500) == (3000, 6)   # <=17 months: 6x
    assert calculate_cancellation_fee(36, 17, 500) == (3000, 6)   # boundary
    assert calculate_cancellation_fee(36, 18, 500) == (2000, 4)   # 18-26: 4x
    assert calculate_cancellation_fee(36, 26, 500) == (2000, 4)   # boundary
    assert calculate_cancellation_fee(36, 27, 500) == (1000, 2)   # >26: 2x


def test_fee_24_month_contract_penalty_tiers():
    assert calculate_cancellation_fee(24, 5, 500) == (3000, 6)    # <=11: 6x
    assert calculate_cancellation_fee(24, 11, 500) == (3000, 6)   # boundary
    assert calculate_cancellation_fee(24, 12, 500) == (2000, 4)   # 12-17: 4x
    assert calculate_cancellation_fee(24, 17, 500) == (2000, 4)   # boundary
    assert calculate_cancellation_fee(24, 18, 500) == (1000, 2)   # >17: 2x


def test_fee_12_month_contract_penalty_tiers():
    assert calculate_cancellation_fee(12, 3, 500) == (2000, 4)    # <6 months: 4x
    assert calculate_cancellation_fee(12, 5, 500) == (2000, 4)    # boundary
    assert calculate_cancellation_fee(12, 6, 500) == (1000, 2)    # >=6 months: 2x
    assert calculate_cancellation_fee(12, 11, 500) == (1000, 2)


def test_fee_unrecognised_contract_length_has_no_penalty():
    assert calculate_cancellation_fee(6, 3, 500) == (0, 0)


# ── calculate_cancellation_quote — agreed discount bands (24 Oct 2026) ───────

def test_quote_agreed_worked_example():
    # 36-month contract, R302.25/month, 10 completed, 6-month penalty, 4 unpaid.
    fee, penalty = calculate_cancellation_fee(36, 10, 302.25)
    assert (fee, penalty) == (1813.5, 6)
    quote = calculate_cancellation_quote(fee, 4 * 302.25)
    assert quote["gross_total"] == 3022.5
    assert quote["band"] == "above_3000"
    assert quote["cash"]["discount_pct"] == 35
    assert quote["cash"]["discount_amount"] == 1057.88
    assert quote["cash"]["net_total"] == 1964.62
    assert quote["instalments"]["discount_pct"] == 20
    assert quote["instalments"]["discount_amount"] == 604.5
    assert quote["instalments"]["net_total"] == 2418.0
    assert quote["instalments"]["payments"] == [403.0] * 6
    assert round(quote["instalments"]["net_total"] - quote["cash"]["net_total"], 2) == 453.38


def test_quote_band_boundaries_for_both_options():
    cases = [
        (1999.99, 0, 0), (2000.00, 20, 10), (2000.01, 20, 10),
        (2999.99, 20, 10), (3000.00, 20, 10), (3000.01, 35, 20),
    ]
    for gross, cash_pct, instalment_pct in cases:
        quote = calculate_cancellation_quote(gross, 0)
        assert quote["cash"]["discount_pct"] == cash_pct, gross
        assert quote["instalments"]["discount_pct"] == instalment_pct, gross


def test_quote_applies_the_bands_with_no_arrears():
    quote = calculate_cancellation_quote(3600, 0)
    assert quote["gross_total"] == 3600
    assert quote["cash"]["net_total"] == 2340.0          # 35% off
    assert quote["instalments"]["net_total"] == 2880.0   # 20% off


def test_quote_adds_the_outstanding_balance_once_and_ignores_a_credit():
    assert calculate_cancellation_quote(1000, 1500)["gross_total"] == 2500
    assert calculate_cancellation_quote(1000, -200)["gross_total"] == 1000


def test_quote_rounds_discount_to_cents_and_schedule_sums_to_net():
    # 20% of 2000.03 = 400.006 -> 400.01; net 1600.02 for cash.
    quote = calculate_cancellation_quote(2000.03, 0)
    assert quote["cash"]["discount_amount"] == 400.01
    assert quote["cash"]["net_total"] == 1600.02
    # 10% of 2000.03 = 200.003 -> 200.00; net 1800.03 over six payments.
    inst = quote["instalments"]
    assert inst["discount_amount"] == 200.0
    assert inst["net_total"] == 1800.03
    assert inst["payments"] == [300.0] * 5 + [300.03]
    assert inst["final_adjustment"] == 0.03
    assert round(sum(inst["payments"]), 2) == inst["net_total"]


# ── Rendering smoke checks ────────────────────────────────────────────────────

def test_add_query_form_renders_on_get(client):
    _login(client)
    resp = client.get("/queries/add")
    assert resp.status_code == 200
    assert b"Cancellation Details" in resp.data


def test_cancellation_query_detail_page_renders_the_breakdown(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())
    client.post("/queries/add", data={
        "category": "cancellation",
        "description": "Detail page render check.",
        "member_id": str(member_id),
        "cancellation_contract_months": "24",
        "cancellation_months_completed": "5",
        "cancellation_monthly_fee": "600",
        "cancellation_payment_method": "new_debit",
        "cancellation_first_payment_date": AGREED,
    })
    with app.app_context():
        qid = get_db().execute("SELECT id FROM queries WHERE member_id=?", (member_id,)).fetchone()["id"]

    resp = client.get(f"/queries/{qid}")
    assert resp.status_code == 200
    assert b"Cancellation Fee" in resp.data
    assert b"3600.00" in resp.data  # 24-month, 5 completed -> 6x600


# ── Integration: the /queries/add route computes and stores the real numbers ─

def test_add_cancellation_query_computes_and_stores_the_settlement(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())

    resp = client.post("/queries/add", data={
        "category": "cancellation",
        "description": "Member wants to cancel due to relocation.",
        "member_id": str(member_id),
        "cancellation_contract_months": "36",
        "cancellation_months_completed": "10",
        "cancellation_monthly_fee": "500",
        "cancellation_payment_method": "lumpsum",
    })
    assert resp.status_code == 302

    with app.app_context():
        db = get_db()
        qry = db.execute("SELECT * FROM queries WHERE member_id=?", (member_id,)).fetchone()
        assert qry["category"] == "cancellation"
        assert qry["cancellation_contract_months"] == 36
        assert qry["cancellation_penalty_months"] == 6
        assert qry["cancellation_fee"] == 3000.0
        # R3,000 with no balance owing sits in the middle band: 20% off for cash.
        assert qry["cancellation_settlement_rule"] == "bands_cash"
        assert qry["cancellation_discount_pct"] == 20
        assert qry["cancellation_settlement_amount"] == 2400.0
        quote = db.execute("SELECT * FROM cancellation_quotes WHERE query_id=?", (qry["id"],)).fetchone()
        assert quote["version"] == 1
        assert quote["gross_total"] == 3000.0
        assert quote["cash_total"] == 2400.0
        assert quote["instalment_total"] == 2700.0
        assert quote["selected_option"] == "cash"
        assert qry["member_cancelled"] == 0

        member = db.execute("SELECT member_status FROM members WHERE id=?", (member_id,)).fetchone()
        assert member["member_status"] == "Active"  # not flipped — checkbox wasn't ticked


def test_add_cancellation_query_with_cancel_now_does_not_cancel_at_intake(client, app):
    """Cancelling straight away needs a paid cash settlement against the saved
    quote, so the intake form can no longer cancel the account."""
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())

    resp = client.post("/queries/add", data={
        "category": "cancellation",
        "description": "Immediate cancellation requested.",
        "member_id": str(member_id),
        "cancellation_contract_months": "12",
        "cancellation_months_completed": "8",
        "cancellation_monthly_fee": "450",
        "cancellation_payment_method": "lumpsum",
        "cancel_member": "1",
    })
    assert resp.status_code == 302

    with app.app_context():
        db = get_db()
        qry = db.execute("SELECT * FROM queries WHERE member_id=?", (member_id,)).fetchone()
        assert qry["cancellation_fee"] == 900.0  # 12-month, >=6 completed -> 2x monthly fee
        assert qry["member_cancelled"] == 0
        member = db.execute("SELECT member_status FROM members WHERE id=?", (member_id,)).fetchone()
        assert member["member_status"] == "Active"


def test_add_cancellation_query_rejects_missing_member(client):
    _login(client)
    resp = client.post("/queries/add", data={
        "category": "cancellation",
        "description": "No member linked.",
        "cancellation_contract_months": "12",
        "cancellation_months_completed": "8",
        "cancellation_monthly_fee": "450",
    }, follow_redirects=True)
    assert b"select the member" in resp.data.lower()


def test_add_cancellation_query_rejects_invalid_contract_length(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())
    resp = client.post("/queries/add", data={
        "category": "cancellation",
        "description": "Bad contract type.",
        "member_id": str(member_id),
        "cancellation_contract_months": "18",
        "cancellation_months_completed": "8",
        "cancellation_monthly_fee": "450",
    }, follow_redirects=True)
    assert b"valid cancellation contract type" in resp.data.lower()


def test_add_cancellation_query_rejects_zero_monthly_fee(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())
    resp = client.post("/queries/add", data={
        "category": "cancellation",
        "description": "No fee entered.",
        "member_id": str(member_id),
        "cancellation_contract_months": "12",
        "cancellation_months_completed": "8",
        "cancellation_monthly_fee": "0",
    }, follow_redirects=True)
    assert b"monthly membership fee" in resp.data.lower()


# ── Quote preview and versioned revisions ────────────────────────────────────

def _log_cancellation(client, app, member_id, **extra):
    form = {
        "category": "cancellation",
        "description": "Quote versioning check.",
        "member_id": str(member_id),
        "cancellation_contract_months": "36",
        "cancellation_months_completed": "10",
        "cancellation_monthly_fee": "302.25",
    }
    form.update(extra)
    client.post("/queries/add", data=form)
    with app.app_context():
        return get_db().execute("SELECT id FROM queries WHERE member_id=?", (member_id,)).fetchone()["id"]


def test_quote_preview_matches_the_saved_quote(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db(), monthly_installment=302.25)
    preview = client.get("/queries/cancellation-quote-preview", query_string={
        "member_id": member_id, "cancellation_contract_months": "36",
        "cancellation_months_completed": "10", "cancellation_monthly_fee": "302.25",
        "cancellation_payment_method": "new_debit",
        "cancellation_first_payment_date": AGREED,
    }).get_json()
    assert preview["ok"] and preview["selected_option"] == "instalments"
    assert preview["cancellation_fee"] == 1813.5
    qid = _log_cancellation(client, app, member_id, cancellation_payment_method="new_debit",
                            cancellation_first_payment_date=AGREED)
    with app.app_context():
        saved = get_db().execute("SELECT * FROM cancellation_quotes WHERE query_id=?", (qid,)).fetchone()
    assert saved["gross_total"] == preview["gross_total"]
    assert saved["cash_total"] == preview["cash"]["net_total"]
    assert saved["instalment_total"] == preview["instalments"]["net_total"]
    assert saved["policy_version"] == preview["policy_version"]
    assert saved["first_payment_date"] == AGREED
    assert len(preview["instalment_dates"]) == 6 and preview["instalment_dates"][0] == AGREED


def test_quote_preview_reports_missing_inputs(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())
    resp = client.get("/queries/cancellation-quote-preview", query_string={
        "member_id": member_id, "cancellation_contract_months": "36",
        "cancellation_months_completed": "", "cancellation_monthly_fee": "500",
    })
    assert resp.status_code == 400
    assert "months of the contract" in resp.get_json()["error"]


def test_revising_a_quote_keeps_the_original_version(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db(), monthly_installment=302.25)
    qid = _log_cancellation(client, app, member_id)

    resp = client.post(f"/queries/{qid}/cancellation-quote", data={
        "cancellation_contract_months": "24", "cancellation_months_completed": "12",
        "cancellation_monthly_fee": "302.25", "cancellation_payment_method": "lumpsum",
        "correction_reason": "Contract is 24 months",
    })
    assert resp.status_code == 302

    with app.app_context():
        db = get_db()
        versions = db.execute(
            "SELECT * FROM cancellation_quotes WHERE query_id=? ORDER BY version", (qid,)).fetchall()
        assert [v["version"] for v in versions] == [1, 2]
        assert versions[0]["contract_months"] == 36 and versions[0]["cancellation_fee"] == 1813.5
        assert versions[1]["contract_months"] == 24 and versions[1]["penalty_months"] == 4
        assert versions[1]["correction_reason"] == "Contract is 24 months"
        qry = db.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
        assert qry["cancellation_contract_months"] == 24
        assert qry["cancellation_fee"] == 1209.0
        assert qry["cancellation_settlement_rule"] == "bands_cash"
        assert qry["cancellation_settlement_amount"] == 1209.0   # below R2,000: no discount
        note = db.execute("SELECT note FROM query_notes WHERE query_id=? AND action_type='quote' "
                          "ORDER BY id DESC", (qid,)).fetchone()["note"]
        assert "v2" in note and "contract 36 → 24" in note and "Contract is 24 months" in note
        member = db.execute("SELECT member_status FROM members WHERE id=?", (member_id,)).fetchone()
        assert member["member_status"] == "Active"

    page = client.get(f"/queries/{qid}")
    assert b"Quote v2" in page.data and b"Earlier quote versions (1)" in page.data


def test_revising_a_quote_requires_a_reason(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())
    qid = _log_cancellation(client, app, member_id)
    client.post(f"/queries/{qid}/cancellation-quote", data={
        "cancellation_contract_months": "24", "cancellation_months_completed": "12",
        "cancellation_monthly_fee": "500", "correction_reason": "",
    })
    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) FROM cancellation_quotes WHERE query_id=?", (qid,)).fetchone()[0]
    assert count == 1


def test_quote_detail_shows_both_options(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db(), monthly_installment=302.25)
    qid = _log_cancellation(client, app, member_id)
    page = client.get(f"/queries/{qid}")
    assert b"Cash \xe2\x80\x94 full payment upfront" in page.data
    assert b"Six monthly instalments" in page.data
    assert b"Payment option: <strong>Not chosen yet" in page.data


def test_preview_fields_used_by_the_add_form_come_back_from_the_endpoint(client, app):
    import re
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())
    quote = client.get("/queries/cancellation-quote-preview", query_string={
        "member_id": member_id, "cancellation_contract_months": "12",
        "cancellation_months_completed": "3", "cancellation_monthly_fee": "500",
    }).get_json()
    with open("onecpase/templates/queries/add.html", encoding="utf-8") as fh:
        wanted = set(re.findall(r"quote\.([a-z_]+)", fh.read())) - {"ok", "error"}
    assert wanted and not wanted - set(quote), wanted - set(quote)


def test_legacy_cancellation_query_keeps_its_original_breakdown(client, app):
    """Queries quoted under the earlier rule have no saved quote versions;
    their stored amounts are shown unchanged, not recalculated."""
    _login(client)
    with app.app_context():
        db = get_db()
        member_id = _insert_member(db)
        db.execute(
            """INSERT INTO queries (reference, member_id, category, description, status,
                   cancellation_contract_months, cancellation_months_completed,
                   cancellation_monthly_fee, cancellation_fee, cancellation_penalty_months,
                   cancellation_payment_method, cancellation_outstanding,
                   cancellation_settlement_amount, cancellation_settlement_rule)
               VALUES ('Q-LEGACY', ?, 'cancellation', 'Old quote', 'open',
                   36, 10, 500, 3000, 6, 'lumpsum', 1500, 2250, 'discount_50')""",
            (member_id,))
        db.commit()
        qid = db.execute("SELECT id FROM queries WHERE reference='Q-LEGACY'").fetchone()["id"]
    page = client.get(f"/queries/{qid}")
    assert page.status_code == 200
    assert b"50% lump-sum discount" in page.data
    assert b"2250.00" in page.data
    assert b"Quoted under the earlier settlement rule" in page.data



# ── Agreed first payment date and the six-payment schedule ───────────────────

def test_payment_dates_are_monthly_from_the_agreed_date():
    assert cancellation_payment_dates(date(2026, 11, 25)) == [
        date(2026, 11, 25), date(2026, 12, 25), date(2027, 1, 25),
        date(2027, 2, 25), date(2027, 3, 25), date(2027, 4, 25),
    ]


def test_payment_dates_use_the_last_day_of_a_shorter_month():
    assert cancellation_payment_dates(date(2027, 1, 31))[:3] == [
        date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31),
    ]


def test_six_instalments_need_an_agreed_first_payment_date(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())
    resp = client.post("/queries/add", data={
        "category": "cancellation", "description": "No date agreed.",
        "member_id": str(member_id), "cancellation_contract_months": "12",
        "cancellation_months_completed": "3", "cancellation_monthly_fee": "500",
        "cancellation_payment_method": "existing_debit",
    }, follow_redirects=True)
    assert b"first payment date the member agreed" in resp.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 0


def test_first_payment_date_cannot_be_in_the_past(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())
    resp = client.get("/queries/cancellation-quote-preview", query_string={
        "member_id": member_id, "cancellation_contract_months": "12",
        "cancellation_months_completed": "3", "cancellation_monthly_fee": "500",
        "cancellation_payment_method": "new_debit",
        "cancellation_first_payment_date": (date.today() - timedelta(days=1)).isoformat(),
    })
    assert resp.status_code == 400
    assert "past" in resp.get_json()["error"]


def test_detail_shows_the_agreed_payment_dates(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db(), monthly_installment=302.25)
    qid = _log_cancellation(client, app, member_id, cancellation_payment_method="new_debit",
                            cancellation_first_payment_date=AGREED)
    page = client.get(f"/queries/{qid}").get_data(as_text=True)
    assert "Payment dates: " + AGREED in page
