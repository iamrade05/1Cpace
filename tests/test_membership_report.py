from datetime import date, timedelta

from onecpase import reports
from onecpase.database import get_db
from onecpase.encryption import encrypt


def _login_admin(client):
    with client.application.app_context():
        db=get_db()
        db.execute("INSERT OR IGNORE INTO users(id,username,password_hash,full_name,role,active) VALUES(1,'report-admin','x','Report Admin','admin',1)")
        db.commit()
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


def test_consultant_book_preserves_original_sale_credit_and_drills_into_losses(app, monkeypatch):
    from onecpase import consultant_book

    monkeypatch.setattr(consultant_book, "today", lambda: date(2026, 11, 6))
    with app.test_client() as client:
        _login_admin(client)
        with app.app_context():
            db = get_db()
            for uid, name in ((21, "Original Seller"), (22, "New Owner")):
                db.execute(
                    "INSERT INTO users(id,username,password_hash,full_name,is_sales_consultant) VALUES(?,?,?,?,1)",
                    (uid, f"book-{uid}", "x", name),
                )
            db.execute("INSERT INTO hr_profiles(user_id,start_date) VALUES(21,'2026-01-01')")
            lost = _member(db, "BOOK-1", "Lost", "Member", status="Cancelled", fee=500)
            _member(db, "BOOK-2", "Active", "Member", sold_by=" original   seller ",
                    id_number="9001015800086", fee=550)
            _member(db, "BOOK-3", "Earlier", "Member", sold_by="Original Seller",
                    id_number="9001015800087", join="2025-12-31")
            lead = db.execute(
                "INSERT INTO leads(full_name,assigned_to) VALUES('Lost Member',22)"
            ).lastrowid
            db.execute("INSERT INTO membership_applications(member_id,lead_id,created_by) VALUES(?,?,22)",
                       (lost, lead))
            db.execute(
                """INSERT INTO stage_events(entity_type,entity_id,lead_id,field,to_value,owner_id)
                   VALUES('lead',?,?,'sales_stage','JOINED',21)""", (lead, lead),
            )
            db.execute(
                """INSERT INTO queries(member_id,category,query_type,description,member_cancelled,
                                       closed_at,cancellation_monthly_fee)
                   VALUES(?,'cancellation','Relocation','Request',1,'2026-10-04',425)""", (lost,),
            )
            db.commit()
            context = consultant_book.load_consultant_book(db, "21", "lost")
            book = context["selected"]
            assert (book["signed"], book["lost"], book["active"]) == (2, 1, 1)
            assert (book["signed_value"], book["lost_value"], book["active_value"]) == (1050, 425, 550)
            assert [(m["member_ref"], m["exit_date"]) for m in context["members"]] == [
                ("BOOK-1", date(2026, 10, 4))
            ]
            assert next(b for b in context["books"] if b["id"] == "22")["signed"] == 0

        page = client.get("/reports/consultant-book?consultant=21&segment=lost")
        assert page.status_code == 200
        assert b"50.0%" in page.data and b"Relocation" in page.data
        assert b"BOOK-1" in page.data and b"BOOK-2" not in page.data


def test_analytics_reports_render_and_financial_reports_reconcile(app, monkeypatch):
    from onecpase import analytics

    monkeypatch.setattr(analytics, "today", lambda: date(2026, 11, 6))
    with app.test_client() as client:
        _login_admin(client)
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO users(id,username,password_hash,full_name,is_sales_consultant) "
                       "VALUES(25,'analytics-sales','x','Test Consultant',1)")
            member = _member(db, "AN-001", "=Test", "Member", sold_by="Test Consultant", join="2026-10-16")
            db.execute("""INSERT INTO leads(full_name,created_at,assigned_to,lead_status,converted_member_id)
                       VALUES('Cohort Member','2026-10-16',25,'joined',?)""", (member,))
            for purpose, amount, status, when, notes in (
                ("registration_fee", 100, "paid", "2026-10-16", ""),
                ("general", 400, "reversed", "2026-10-17", ""),
                ("general", 50, "paid", "2026-10-18", "type=Discount"),
                ("general", 500, "paid", "2026-10-19", ""),
            ):
                db.execute("""INSERT INTO collections(member_id,payment_purpose,amount_paid,status,
                                                      collection_date,method,notes)
                           VALUES(?,?,?,?,?,'debicheck',?)""", (member, purpose, amount, status, when, notes))
            db.commit()
        dates = "?from=2026-10-15&to=2026-11-06"
        for report in analytics.TITLES:
            response = client.get(f"/reports/analytics/{report}{dates}")
            assert response.status_code == 200, report
            assert analytics.TITLES[report].encode() in response.data
        master = client.get(f"/reports/analytics/master{dates}&format=csv&member=AN-001")
        assert b"Current arrears (R)" in master.data
        assert b"AN-001,'=Test,Member" in master.data
        commissions = client.get(f"/reports/analytics/commission{dates}&rate=10&format=csv")
        assert b"2026-10-19,500.0,10,50.00,Payment recorded" in commissions.data
        debit = client.get(f"/reports/analytics/reception{dates}&format=csv&table=3&member=AN-001")
        assert b"reversed" in debit.data and b"paid,0,500.0" in debit.data
        assert client.get(f"/reports/analytics/master{dates}&format=csv&table=99").status_code == 404


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
