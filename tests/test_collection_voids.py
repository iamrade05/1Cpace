from datetime import date, timedelta
import json

import pytest
from bs4 import BeautifulSoup

from onecpase.collections import _sync_application_payment
from onecpase.database import _backfill_member_applications, get_db
from onecpase.financial_reporting import cash_received, totals
from onecpase.ptp import bulk_member_arrears


@pytest.fixture
def ledger(app, client):
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES"
                   "(1,'admin','x','Test Manager','admin',1),(2,'clerk','x','Test Clerk','staff',1)")
        db.execute("INSERT INTO user_permissions(user_id,permission) VALUES(2,'new_collection')")
        member = db.execute("INSERT INTO members(first_name,last_name,id_number) "
                            "VALUES('Ledger','Member','collection-void-test')").lastrowid
        day = (date.today() - timedelta(days=7)).isoformat()
        charge = db.execute("INSERT INTO collections(member_id,outstanding_balance,collection_date,status,notes) "
                            "VALUES(?,100,?,'failed','type=Recurring Fee')", (member, day)).lastrowid
        payment = db.execute("INSERT INTO collections(member_id,amount_paid,collection_date,status,notes,created_by) "
                             "VALUES(?,100,?,'paid','Duplicate cash entry',2)", (member, day)).lastrowid
        db.execute("INSERT INTO collections_cases(member_id,collection_id,status,resolution_type) "
                   "VALUES(?,?,'closed','PAID_IN_FULL')", (member, charge))
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=1, role="admin")
    return member, charge, payment


def test_void_preserves_history_and_recalculates_balance_and_case(app, client, ledger):
    member, charge, payment = ledger
    with app.app_context():
        db = get_db()
        original = dict(db.execute("SELECT * FROM collections WHERE id=?", (payment,)).fetchone())
        assert cash_received(db) == 100
        assert bulk_member_arrears(db, [member])[member]["total_arrears"] == 0

    path = f"/collections/{payment}/void"
    assert client.post(path, data={"reason": "Duplicate posting"}).status_code == 302
    assert client.post(path, data={"reason": "Repeated request"}).status_code == 302
    assert client.post(f"/collections/{payment}/edit", data={"amount_paid": "200"}).status_code == 302
    with app.app_context():
        db = get_db()
        assert dict(db.execute("SELECT * FROM collections WHERE id=?", (payment,)).fetchone()) == original
        audit = db.execute("SELECT collection_id,actor_id,reason,voided_at FROM collection_voids").fetchall()
        assert len(audit) == 1
        assert tuple(audit[0])[:3] == (payment, 1, "Duplicate posting")
        assert audit[0]["voided_at"]
        assert cash_received(db) == 0
        assert bulk_member_arrears(db, [member])[member]["total_arrears"] == 100
        assert totals(db.execute("SELECT * FROM active_collections"))["total_billed"] == 100
        cases = db.execute("SELECT status,arrears_amount FROM collections_cases WHERE member_id=? ORDER BY id", (member,))
        assert [tuple(case) for case in cases] == [("closed", 0), ("open", 100)]
        assert db.execute("SELECT collection_id FROM collections_cases WHERE status='closed'").fetchone()[0] == charge
    history = client.get("/collections/?status=voided")
    assert history.status_code == 200
    assert b"Duplicate posting" in history.data and b"Test Manager" in history.data


def test_void_requires_manager_reason_and_unlinked_local_entry(app, client, ledger):
    _, charge, payment = ledger
    path = f"/collections/{payment}/delete"
    with client.session_transaction() as session:
        session.update(user_id=2, role="staff")
    denied = client.post(path, data={"reason": "Clerk attempt"})
    assert denied.status_code == 302 and "/collections/" not in denied.location

    with client.session_transaction() as session:
        session.update(user_id=1, role="admin")
    assert client.post(path, data={"reason": " "}).status_code == 302
    assert client.post(f"/collections/{charge}/void", data={"reason": "Provider charge"}).status_code == 302
    with app.app_context():
        db = get_db()
        db.execute("UPDATE collections SET itensity_pos_requested=1 WHERE id=?", (payment,))
        db.commit()
    assert client.post(path, data={"reason": "Linked POS receipt"}).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT count(*) FROM collection_voids").fetchone()[0] == 0


def test_void_updates_registration_evidence_and_rolls_back_on_error(app, client, ledger, monkeypatch):
    member, _, payment = ledger
    with app.test_request_context():
        db = get_db()
        db.execute("UPDATE collections SET payment_purpose='registration_fee' WHERE id=?", (payment,))
        db.execute("INSERT INTO collections(member_id,amount_paid,status,notes) "
                   "VALUES(?,10,'paid','Unrelated payment')", (member,))
        _sync_application_payment(db, member)
        db.commit()
        assert db.execute("SELECT pos_status FROM compliance_checklists").fetchone()[0] == "paid"

    def fail(*args):
        raise RuntimeError("Simulated application update failure")

    path = f"/collections/{payment}/void"
    with monkeypatch.context() as patch:
        patch.setattr("onecpase.collections._sync_application_payment", fail)
        with pytest.raises(RuntimeError, match="Simulated application update failure"):
            client.post(path, data={"reason": "Wrong member"})
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT count(*) FROM collection_voids").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM member_notes WHERE note LIKE 'Voided collection%'").fetchone()[0] == 0

    assert client.post(path, data={"reason": "Wrong member"}).status_code == 302
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT pos_status FROM compliance_checklists").fetchone()[0] == "pending"
        _backfill_member_applications(db)
        assert db.execute("SELECT pos_status FROM compliance_checklists").fetchone()[0] == "pending"


def _correction_token(client, payment):
    page = BeautifulSoup(client.get(f"/collections/{payment}/edit").data, "html.parser")
    return page.select_one('input[name="correction_token"]')["value"]


def test_clerk_can_save_notes_and_record_new_partial_payments(app, client, ledger):
    _, _, payment = ledger
    with app.app_context():
        db = get_db()
        db.execute("UPDATE collections SET itensity_pos_requested=1 WHERE id=?", (payment,))
        member = db.execute("INSERT INTO members(first_name,last_name,id_number) "
                            "VALUES('Partial','Payer','collection-partial-test')").lastrowid
        day = date.today().isoformat()
        db.execute("INSERT INTO collections(member_id,outstanding_balance,collection_date,status,notes) "
                   "VALUES(?,150,?,'pending','type=Recurring Fee')", (member, day))
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=2, role="staff")

    path = f"/collections/{payment}/edit"
    edit = client.get(path)
    assert b"Save staff note" in edit.data and b"Save correction" not in edit.data
    assert client.post(path, data={"amount_paid": "0", "status": "reversed", "notes": "Bypass"}).status_code == 403
    denied = client.post(f"/collections/{payment}/correct", data={"amount_paid": "0", "reason": "Bypass"})
    assert denied.status_code == 302 and "/collections/" not in denied.location
    assert client.post(path, data={"notes": "Client will follow up"}).status_code == 302
    assert b"Client will follow up" in client.get(path).data

    response = client.post("/collections/add", data={"member_id": member, "amount_paid": "100",
                                                      "collection_date": day, "status": "partial", "method": "cash"})
    assert response.status_code == 302 and response.location.endswith("#payments")
    with app.app_context():
        db = get_db()
        original = db.execute("SELECT amount_paid,status,notes,staff_notes,itensity_pos_requested FROM collections WHERE id=?",
                              (payment,)).fetchone()
        assert tuple(original) == (100, "paid", "Duplicate cash entry", "Client will follow up", 1)
        assert db.execute("SELECT count(*) FROM collection_corrections").fetchone()[0] == 0
        assert bulk_member_arrears(db, [member])[member]["total_arrears"] == 50
        assert cash_received(db) == 200


def test_manager_correction_keeps_original_values_and_recalculates_arrears(app, client, ledger):
    member, _, payment = ledger
    with app.app_context():
        db = get_db()
        db.execute("UPDATE users SET role='manager' WHERE id=1")
        db.execute("INSERT INTO user_permissions(user_id,permission) VALUES(1,'new_collection')")
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=1, role="manager")

    path = f"/collections/{payment}/correct"
    form = {"correction_token": _correction_token(client, payment), "amount_paid": "60", "status": "partial"}
    assert client.post(path, data=form).status_code == 302
    with app.app_context():
        assert cash_received(get_db()) == 100

    form["reason"] = "Receipt confirms a lower amount"
    assert client.post(path, data=form).status_code == 302
    assert client.post(path, data={**form, "amount_paid": "80"}).status_code == 302
    with app.app_context():
        db = get_db()
        rows = db.execute("SELECT * FROM collection_corrections WHERE collection_id=?", (payment,)).fetchall()
        assert len(rows) == 1
        audit = rows[0]
        assert (audit["actor_id"], audit["reason"]) == (1, form["reason"])
        assert json.loads(audit["before_values"])["amount_paid"] == 100
        assert json.loads(audit["after_values"])["amount_paid"] == 60
        assert cash_received(db) == 60
        assert bulk_member_arrears(db, [member])[member]["total_arrears"] == 40
        cases = db.execute("SELECT status,arrears_amount FROM collections_cases WHERE member_id=? ORDER BY id", (member,))
        assert [tuple(row) for row in cases] == [("closed", 0), ("open", 40)]
    history = client.get(f"/collections/{payment}/edit")
    assert b"Correction history" in history.data and form["reason"].encode() in history.data


def test_correction_checks_provider_links_and_rolls_back_as_one_transaction(app, client, ledger, monkeypatch):
    _, _, payment = ledger
    path = f"/collections/{payment}/correct"
    form = {"correction_token": _correction_token(client, payment), "amount_paid": "70", "reason": "Audit test"}
    with app.app_context():
        db = get_db()
        db.execute("UPDATE collections SET itensity_pos_requested=1 WHERE id=?", (payment,))
        db.commit()
    assert client.post(path, data=form).status_code == 302
    with app.app_context():
        db = get_db()
        assert cash_received(db) == 100
        db.execute("UPDATE collections SET itensity_pos_requested=0 WHERE id=?", (payment,))
        db.commit()

    def fail(*args):
        raise RuntimeError("Simulated reconciliation failure")

    monkeypatch.setattr("onecpase.collections._sync_application_payment", fail)
    with pytest.raises(RuntimeError, match="Simulated reconciliation failure"):
        client.post(path, data=form)
    with app.app_context():
        db = get_db()
        assert cash_received(db) == 100
        assert db.execute("SELECT count(*) FROM collection_corrections").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM member_notes WHERE note LIKE 'Corrected collection%'").fetchone()[0] == 0


def test_corrected_registration_fee_stays_pending_after_startup_backfill(app, client, ledger):
    member, _, payment = ledger
    with app.test_request_context():
        db = get_db()
        db.execute("UPDATE collections SET payment_purpose='registration_fee' WHERE id=?", (payment,))
        _sync_application_payment(db, member)
        db.commit()
        assert db.execute("SELECT pos_status FROM compliance_checklists").fetchone()[0] == "paid"

    form = {"correction_token": _correction_token(client, payment), "payment_purpose": "general",
            "reason": "Receipt was for the membership balance"}
    assert client.post(f"/collections/{payment}/correct", data=form).status_code == 302
    with app.app_context():
        db = get_db()
        _backfill_member_applications(db)
        assert db.execute("SELECT pos_status FROM compliance_checklists").fetchone()[0] == "pending"
