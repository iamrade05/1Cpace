"""The Members list shows who signed the client, the premium, Itensity ref and NuPay contract ref."""
from onecpase.database import get_db
from onecpase.members import nupay_contract_references


def _login(client):
    with client.session_transaction() as s:
        s.update(user_id=1, role="admin", full_name="Admin", permissions=["all_members"])
    with client.application.app_context():
        db = get_db()
        db.execute("INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, active) "
                   "VALUES (1, 'lister', 'x', 'Admin', 'admin', 1)")
        db.execute("INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, active) "
                   "VALUES (7, 'loader7', 'x', 'Loaded In App', 'staff', 1)")
        db.commit()


def _mandate(db, member_id, status, ref=None, response=None):
    db.execute(
        """INSERT INTO debicheck_mandates (member_id, merchant_id, client_ref1, client_ref2, account_name,
           account_type, account_number, branch_code, submit_date, status, contract_reference, nupay_response)
           VALUES (?, 'M', 'R', '1001', 'A', '1', '1', '1', '2026-12-01', ?, ?, ?)""",
        (member_id, status, ref, response),
    )


def test_contract_reference_prefers_the_active_mandate_and_reads_old_response_text(app):
    with app.app_context():
        db = get_db()
        a = db.execute("INSERT INTO members (first_name, last_name, id_number) VALUES ('A','One','x1')").lastrowid
        b = db.execute("INSERT INTO members (first_name, last_name, id_number) VALUES ('B','Two','x2')").lastrowid
        _mandate(db, a, "active", ref="DCPRD000ACTIVE")
        _mandate(db, a, "rejected", ref="DCPRD000NEWER")  # newer, but not collecting
        _mandate(db, b, "submitted", response="Mandate created successfully. Contract Reference: DCPRD00TEXT01")
        db.commit()
        assert nupay_contract_references(db) == {a: "DCPRD000ACTIVE", b: "DCPRD00TEXT01"}


def test_members_list_shows_the_new_columns(client):
    _login(client)
    with client.application.app_context():
        db = get_db()
        loaded = db.execute(
            "INSERT INTO members (first_name, last_name, id_number, monthly_installment, itensity_ref, sold_by, uploaded_by_id) "
            "VALUES ('Lindi','Loaded','y1', 302.25, 'EHF002182714', 'Itensity Seller', 7)").lastrowid
        db.execute(
            "INSERT INTO members (first_name, last_name, id_number, monthly_installment, itensity_ref, sold_by) "
            "VALUES ('Ivan','Imported','y2', 450, '2180011', 'Itensity Seller')")
        _mandate(db, loaded, "active", ref="DCPRD0002DY0HJ")
        db.commit()
    page = client.get("/members").get_data(as_text=True)
    for heading in ("Signed By", "Premium", "Itensity Ref", "NuPay Contract Ref"):
        assert f"<th>{heading}</th>" in page
    assert "Loaded In App" in page          # the person who loaded the client wins
    assert "Itensity Seller" in page        # imported members fall back to Itensity's sold-by
    assert "R302.25" in page and "R450.00" in page
    assert "EHF002182714" in page and "DCPRD0002DY0HJ" in page
    assert "Lindi" in client.get("/members?q=DCPRD0002DY0HJ").get_data(as_text=True)
