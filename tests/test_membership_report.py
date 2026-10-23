from datetime import date, timedelta

from onecpase import reports
from onecpase.database import get_db
from onecpase.encryption import encrypt


def _login_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"


def _member(db, ref, first, last, status="Active", payment_type="Debit Order",
            fee=499.0, join="2026-09-02", id_number="9001015800085", sold_by=None):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, join_date, package, monthly_installment, payment_type, sold_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'Gold', ?, ?, ?)""",
        (ref, first, last, encrypt(id_number), f"07900{ref[-5:]}", status, join, fee, payment_type, sold_by),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _no_external_visits(monkeypatch, visits=None, error="not on the gym network"):
    monkeypatch.setattr(reports, "_itensity_last_visits", lambda: (visits or {}, error))


def test_id_numbers_are_masked():
    assert reports._mask_id("9001015800085") == "900101 •••• 085"
    assert reports._mask_id("") == ""


def test_register_row_has_every_requested_column(app, monkeypatch):
    stale = (date.today() - timedelta(days=45)).isoformat()
    _no_external_visits(monkeypatch, {"790000001": stale}, None)
    with app.app_context():
        db = get_db()
        a = _member(db, "MR-00001", "Thandi", "Mokoena", sold_by="Consultant A")
        db.execute("INSERT INTO collections (member_id, amount_paid, collection_date, status) "
                   "VALUES (?, 499, '2026-09-20', 'paid')", (a,))
        db.execute(
            "INSERT INTO debicheck_mandates (member_id, merchant_id, client_ref1, client_ref2, account_name, "
            "account_type, account_number, branch_code, submit_date, status) "
            "VALUES (?, 'M1', 'R1', 'R2', 'Test', 'cheque', '123', '250655', '2026-09-01', 'authorised')", (a,))
        db.commit()
        ctx = reports._load_register(db)
    row = ctx["register"][0]
    assert row["name"] == "Thandi Mokoena"
    assert row["id_masked"].startswith("900101") and row["id_masked"].endswith("085")
    assert "5800" not in row["id_masked"]
    assert row["monthly_installment"] == 499.0
    assert row["signed_by"] == "Consultant A"
    assert row["last_visit"] == stale and row["days_since_visit"] == 45
    assert row["package"] == "Gold" and row["member_status"] == "Active"
    assert row["last_paid"] == "2026-09-20"
    assert row["mandate"] == "Authorised" and row["mandate_ok"] is True
    assert row["not_attending"] is True
    assert ctx["summary"]["not_attending"] == 1


def test_mandate_states_and_no_visit_data_is_never_guessed(app, monkeypatch):
    _no_external_visits(monkeypatch)
    with app.app_context():
        db = get_db()
        _member(db, "MR-00002", "No", "Mandate")
        _member(db, "MR-00003", "Eft", "Payer", payment_type="EFT/Cash")
        _member(db, "MR-00004", "Old", "Member", status="Inactive")
        db.commit()
        ctx = reports._load_register(db)
        by = {d["member_ref"]: d for d in ctx["register"]}
        assert by["MR-00002"]["mandate"] == "No mandate on file" and by["MR-00002"]["mandate_ok"] is False
        assert by["MR-00003"]["mandate_ok"] is None
        assert all(d["last_visit"] is None and d["not_attending"] is False for d in ctx["register"])
        assert ctx["summary"]["has_visit_data"] is False
        assert ctx["summary"]["no_mandate"] == 1
        assert ctx["summary"]["reactivate"] == 1
        assert [d["member_ref"] for d in reports._load_register(db, segment="reactivate")["register"]] == ["MR-00004"]
        assert len(reports._load_register(db, status="Inactive")["register"]) == 1
        assert len(reports._load_register(db, search="eft")["register"]) == 1


def test_joins_by_month_counts_and_adds_premium(app, monkeypatch):
    _no_external_visits(monkeypatch)
    this_month = date.today().replace(day=1).isoformat()
    with app.app_context():
        db = get_db()
        _member(db, "MR-00005", "A", "One", join=this_month, fee=400)
        _member(db, "MR-00006", "B", "Two", join=this_month, fee=600, status="Inactive")
        db.commit()
        month = this_month[:7]
        row = next(j for j in reports._load_register(db)["joins_by_month"] if j["month"] == month)
    assert row["joined"] == 2 and row["still_active"] == 1 and row["premium"] == 1000


def test_report_page_and_csv_render(app, monkeypatch):
    _no_external_visits(monkeypatch)
    with app.app_context():
        db = get_db()
        _member(db, "MR-00007", "Page", "Check")
        db.commit()
    with app.test_client() as client:
        _login_admin(client)
        page = client.get("/reports/membership-report")
        assert page.status_code == 200
        assert b"Membership report" in page.data and b"Page Check" in page.data
        assert b"Gym visit data is not available" in page.data
        assert b"9001015800085" not in page.data
        csv_resp = client.get("/reports/membership-report?format=csv")
        assert csv_resp.mimetype == "text/csv"
        assert b"Member,Member ref,ID number,Premium,Signed by,Last visit" in csv_resp.data
        assert b"9001015800085" not in csv_resp.data
