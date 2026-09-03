from onecpase.database import get_db
from onecpase.queries import calculate_cancellation_fee, calculate_cancellation_settlement


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


# ── calculate_cancellation_settlement — threshold + payment-method rules ─────

def test_settlement_no_balance_only_applies_manual_discount_to_the_fee():
    result = calculate_cancellation_settlement(3000, 0, "lumpsum", manual_discount_pct=10)
    assert result == {"combined_total": 3000, "discount_pct": 10, "settlement_amount": 2700.0, "rule": "none"}


def test_settlement_below_threshold_ignores_payment_method_auto_rules():
    # fee + balance = 1500, below the 2000 threshold, even though lumpsum is chosen
    result = calculate_cancellation_settlement(1000, 500, "lumpsum", manual_discount_pct=0)
    assert result["rule"] == "none"
    assert result["settlement_amount"] == 1000.0  # only the fee, balance still owed separately


def test_settlement_above_threshold_lumpsum_gets_50pct_off_combined_total():
    result = calculate_cancellation_settlement(1000, 1500, "lumpsum")
    assert result["combined_total"] == 2500
    assert result["rule"] == "discount_50"
    assert result["discount_pct"] == 50
    assert result["settlement_amount"] == 1250.0


def test_settlement_above_threshold_installment_pays_flat_amount():
    for method in ("existing_debit", "new_debit"):
        result = calculate_cancellation_settlement(1000, 1500, method)
        assert result["rule"] == "flat_1500"
        assert result["settlement_amount"] == 1500.0


def test_settlement_manual_discount_is_clamped_to_0_100():
    result = calculate_cancellation_settlement(1000, 0, "cash", manual_discount_pct=150)
    assert result["discount_pct"] == 100.0
    assert result["settlement_amount"] == 0.0


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
        "cancellation_discount_pct": "0",
    })
    assert resp.status_code == 302

    with app.app_context():
        db = get_db()
        qry = db.execute("SELECT * FROM queries WHERE member_id=?", (member_id,)).fetchone()
        assert qry["category"] == "cancellation"
        assert qry["cancellation_contract_months"] == 36
        assert qry["cancellation_penalty_months"] == 6
        assert qry["cancellation_fee"] == 3000.0
        assert qry["cancellation_settlement_rule"] == "none"  # no outstanding balance
        assert qry["cancellation_settlement_amount"] == 3000.0
        assert qry["member_cancelled"] == 0

        member = db.execute("SELECT member_status FROM members WHERE id=?", (member_id,)).fetchone()
        assert member["member_status"] == "Active"  # not flipped — checkbox wasn't ticked


def test_add_cancellation_query_with_cancel_now_updates_member_status(client, app):
    _login(client)
    with app.app_context():
        member_id = _insert_member(get_db())

    resp = client.post("/queries/add", data={
        "category": "cancellation",
        "description": "Immediate cancellation, fee waived by manager.",
        "member_id": str(member_id),
        "cancellation_contract_months": "12",
        "cancellation_months_completed": "8",
        "cancellation_monthly_fee": "450",
        "cancellation_payment_method": "cash",
        "cancel_member": "1",
    })
    assert resp.status_code == 302

    with app.app_context():
        db = get_db()
        qry = db.execute("SELECT * FROM queries WHERE member_id=?", (member_id,)).fetchone()
        assert qry["cancellation_fee"] == 900.0  # 12-month, >=6 completed -> 2x monthly fee
        assert qry["member_cancelled"] == 1
        member = db.execute("SELECT member_status FROM members WHERE id=?", (member_id,)).fetchone()
        assert member["member_status"] == "Cancelled"


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
