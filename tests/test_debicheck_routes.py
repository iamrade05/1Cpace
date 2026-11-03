"""NuPay uses the member ID and requires documents and qualifying analysis."""

from types import SimpleNamespace

from onecpase.database import get_db


def _login(client, role="staff"):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = role
        session["permissions"] = ["new_debicheck", "debicheck_mandates"]
    with client.application.app_context():
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO users
               (id, username, password_hash, full_name, role, active)
               VALUES (1, 'debicheck-actor', 'x', 'DebiCheck Actor', ?, 1)""",
            (role,),
        )
        db.commit()


def _member_and_mandate(db, *, itensity_ref=None):
    member_id = db.execute(
        """INSERT INTO members (first_name, last_name, id_number, monthly_installment)
           VALUES ('Test', 'Member', '9001015009087', 500)"""
    ).lastrowid
    if itensity_ref:
        db.execute("UPDATE members SET itensity_ref=? WHERE id=?", (itensity_ref, member_id))
    mandate_id = db.execute(
        """INSERT INTO debicheck_mandates
           (member_id, merchant_id, client_ref1, client_ref2, account_name, account_type,
            account_number, branch_code, frequency, no_installments, tracking, submit_date,
            id_type, id_number, instalment_rands, instalment_cents, instalment_amount,
            payer_type, status)
           VALUES (?, 'M1', 'FIT-1', '1234', 'T Member', '1', '123456', '123456', '3', 12,
                   '14', '2099-01-01', '0', '9001015009087', 500, 0, 500.0, 'self', 'pending')""",
        (member_id,),
    ).lastrowid
    db.commit()
    return member_id, mandate_id


def test_nupay_push_is_blocked_without_application_evidence(client, monkeypatch):
    import onecpase.debicheck_routes as routes
    monkeypatch.setattr(routes, "push_mandate_to_nupay", lambda mandate: (_ for _ in ()).throw(AssertionError("Must not submit")))
    _login(client)
    with client.application.app_context():
        db = get_db()
        _member_id, mandate_id = _member_and_mandate(db)

    response = client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert b"NuPay submission blocked" in response.data
    assert b"Upload a bank statement" in response.data
    with client.application.app_context():
        row = get_db().execute(
            "SELECT status FROM debicheck_mandates WHERE id=?", (mandate_id,)
        ).fetchone()
    assert row["status"] == "pending"  # unchanged - never reached the NuPay driver


def test_nupay_manual_review_is_blocked_without_application_evidence(client, monkeypatch):
    import onecpase.debicheck_routes as routes
    monkeypatch.setattr(routes, "push_mandate_to_nupay_manual_review", lambda mandate: (_ for _ in ()).throw(AssertionError("Must not submit")))
    _login(client, role="manager")
    with client.application.app_context():
        db = get_db()
        _member_id, mandate_id = _member_and_mandate(db)

    response = client.post(f"/debicheck/{mandate_id}/review-on-nupay", follow_redirects=True)
    assert b"NuPay submission blocked" in response.data
    assert b"Upload a bank statement" in response.data
    with client.application.app_context():
        row = get_db().execute(
            "SELECT status FROM debicheck_mandates WHERE id=?", (mandate_id,)
        ).fetchone()
    assert row["status"] == "pending"  # never flipped to 'reviewing'


def test_nupay_push_uses_member_id_after_evidence_gate_passes(client, monkeypatch):
    _login(client)
    with client.application.app_context():
        db = get_db()
        member_id, mandate_id = _member_and_mandate(db)
        aid = db.execute("INSERT INTO membership_applications (member_id) VALUES (?)", (member_id,)).lastrowid
        db.execute("INSERT INTO compliance_checklists (application_id, id_copy_status, bank_statement_status) VALUES (?, 'not_uploaded', 'analysed')", (aid,))
        db.execute("INSERT INTO statement_analyses (application_id, verification_qualified) VALUES (?, 1)", (aid,))
        for kind in ('Bank Statement',):
            db.execute("INSERT INTO member_documents (member_id, document_type, original_name, stored_name) VALUES (?, ?, 'test.pdf', 'test.pdf')", (member_id, kind))
        db.execute("UPDATE member_documents SET analysis_json=? WHERE member_id=? AND document_type='Bank Statement'", ('{"estimated_income":10000,"rating":"good","verification_qualified":true,"cashflow":{"months":2,"enough_months":true}}', member_id))
        db.commit()

    import onecpase.debicheck_routes as debicheck_routes
    submitted = []
    def accept(mandate):
        submitted.append(mandate)
        return True, "Mandate accepted"
    monkeypatch.setattr(
        debicheck_routes, "push_mandate_to_nupay",
        accept,
    )

    response = client.post(f"/debicheck/{mandate_id}/push", follow_redirects=True)
    assert b"Mandate sent to NuPay" in response.data
    assert len(submitted) == 1
    assert submitted[0]['client_ref1'] == '9001015009087'
    with client.application.app_context():
        row = get_db().execute(
            "SELECT status FROM debicheck_mandates WHERE id=?", (mandate_id,)
        ).fetchone()
    assert row["status"] == "submitted"


def _ready_to_submit(client):
    with client.application.app_context():
        db = get_db()
        member_id, mandate_id = _member_and_mandate(db)
        aid = db.execute("INSERT INTO membership_applications (member_id) VALUES (?)", (member_id,)).lastrowid
        db.execute("INSERT INTO compliance_checklists (application_id, id_copy_status, bank_statement_status) VALUES (?, 'not_uploaded', 'analysed')", (aid,))
        db.execute("INSERT INTO statement_analyses (application_id, verification_qualified) VALUES (?, 1)", (aid,))
        db.execute("INSERT INTO member_documents (member_id, document_type, original_name, stored_name, analysis_json) VALUES (?, 'Bank Statement', 'test.pdf', 'test.pdf', ?)", (member_id, '{"estimated_income":10000,"income_detected":true,"avg_balance":2000,"max_balance":5000,"returned_debits":0,"months_seen":2,"rating":"good","verification_qualified":true,"cashflow":{"months":2,"enough_months":true}}'))
        db.commit()
    return member_id, mandate_id


def test_only_managers_see_review_and_submit_on_nupay(client):
    member_id, mandate_id = _ready_to_submit(client)
    mandate_link = f'href="/debicheck/{mandate_id}"'.encode()

    def sign_in(role):
        _login(client, role=role)
        with client.session_transaction() as session:
            session["permissions"] = session["permissions"] + ["all_members"]

    sign_in("staff")
    for page in (f"/debicheck/{mandate_id}", f"/members/{member_id}"):
        response = client.get(page)
        assert response.status_code == 200
        assert b"Review &amp; Submit on NuPay" not in response.data
    # Sales still complete the application: the profile leads to the mandate, which can be sent.
    assert mandate_link in client.get(f"/members/{member_id}").data
    assert b"Resend Automatically" in client.get(f"/debicheck/{mandate_id}").data

    for role in ("manager", "admin"):
        sign_in(role)
        for page in (f"/debicheck/{mandate_id}", f"/members/{member_id}"):
            response = client.get(page)
            assert response.status_code == 200
            assert b"Review &amp; Submit on NuPay" in response.data


def test_review_and_submit_on_nupay_refuses_everyone_but_managers(client, monkeypatch):
    import onecpase.debicheck_routes as routes
    threads = []
    monkeypatch.setattr(routes, "threading", SimpleNamespace(Thread=lambda **kwargs: threads.append(kwargs) or SimpleNamespace(start=lambda: None)))
    _member_id, mandate_id = _ready_to_submit(client)

    def status():
        with client.application.app_context():
            return get_db().execute("SELECT status FROM debicheck_mandates WHERE id=?", (mandate_id,)).fetchone()["status"]

    _login(client, role="staff")
    client.post(f"/debicheck/{mandate_id}/review-on-nupay", follow_redirects=True)
    assert threads == [] and status() == "pending"

    _login(client, role="manager")
    client.post(f"/debicheck/{mandate_id}/review-on-nupay", follow_redirects=True)
    assert len(threads) == 1 and status() == "reviewing"


# ── Mandate coverage: which Debit Order members have no mandate at all ──────
#
# debicheck.index() only ever lists mandate rows (JOIN, not LEFT JOIN), so a
# member with zero mandates never appears there. coverage() exists to answer
# "which Debit Order members are missing a mandate" directly.

def test_coverage_splits_debit_order_members_by_whether_they_have_a_mandate(client):
    # Names deliberately avoid the words "No"/"Mandate" - the page heading
    # itself contains that phrase regardless of who's listed under it, so a
    # test member named after it would pass even if the query were broken.
    _login(client)
    with client.application.app_context():
        db = get_db()
        has_id, _ = _member_and_mandate(db, itensity_ref="ITN-1")
        db.execute(
            "INSERT INTO members (first_name, last_name, id_number, payment_type) "
            "VALUES ('Gapless', 'Coverage', '8001015009087', 'Debit Order')"
        )
        db.commit()

    response = client.get("/debicheck/coverage")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Test Member" in html        # has a mandate
    assert "Gapless Coverage" in html   # has none


def test_coverage_ignores_members_who_do_not_pay_by_debit_order(client):
    _login(client)
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO members (first_name, last_name, id_number, payment_type) "
            "VALUES ('Cash', 'Payer', '7001015009087', 'EFT/Cash')"
        )
        db.commit()

    response = client.get("/debicheck/coverage")
    html = response.get_data(as_text=True)
    assert "Cash" not in html or "Payer" not in html


def test_coverage_search_filters_by_name(client):
    _login(client)
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO members (first_name, last_name, id_number, payment_type) "
            "VALUES ('Findme', 'Person', '6001015009087', 'Debit Order'), "
            "       ('Other', 'Person', '6001015009088', 'Debit Order')"
        )
        db.commit()

    response = client.get("/debicheck/coverage?q=Findme")
    html = response.get_data(as_text=True)
    assert "Findme" in html
    assert "Other Person" not in html


def test_coverage_requires_the_debicheck_permission(client):
    _login(client)
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "staff"
        session["permissions"] = []
    response = client.get("/debicheck/coverage", follow_redirects=True)
    assert b"don&#39;t have permission" in response.data or b"don't have permission" in response.data


def test_admin_can_override_other_evidence_after_two_month_analysis(client,monkeypatch):
    import onecpase.debicheck_routes as routes
    _login(client,'admin')
    with client.application.app_context():
        db = get_db()
        member_id,mandate_id=_member_and_mandate(db)
        db.execute("INSERT INTO member_documents(member_id,document_type,original_name,stored_name,analysis_json) VALUES(?,'Bank Statement','two.pdf','two.pdf',?)", (member_id,'{"estimated_income":10000,"rating":"good","avg_balance":1000,"max_balance":2000,"returned_debits":0,"cashflow":{"months":2,"enough_months":true}}'))
        db.commit()
    calls=[]
    monkeypatch.setattr(routes,'push_mandate_to_nupay',lambda mandate:(calls.append(mandate) or True,'Accepted'))
    # Being admin alone does not bypass the requirement.
    client.post(f'/debicheck/{mandate_id}/push')
    assert not calls
    response=client.post(f'/debicheck/{mandate_id}/push',data={'override':'1'},follow_redirects=True)
    assert len(calls)==1
    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM audit_logs WHERE action='nupay_admin_override'").fetchone()[0]==1


# Two-month coverage is a mandatory submission prerequisite, even with override.
import pytest
import json

@pytest.mark.parametrize('path', ['push', 'review-on-nupay'])
@pytest.mark.parametrize('months', [0, 1, 2])
def test_two_month_gate_for_all_submission_paths(client, monkeypatch, path, months):
    _login(client, role='admin')
    with client.application.app_context():
        db = get_db()
        member_id, mandate_id = _member_and_mandate(db)
        result = {'estimated_income': 10000, 'rating': 'good', 'cashflow': {'months': months, 'enough_months': months >= 2}}
        db.execute("INSERT INTO member_documents(member_id,document_type,original_name,stored_name,analysis_json) VALUES(?,'Bank Statement','statement.pdf','statement.pdf',?)", (member_id,json.dumps(result)))
        db.commit()
        from onecpase.applications import nupay_submission_blockers, authorize_nupay_override, STATEMENT_MONTHS_BLOCKER
        blockers = nupay_submission_blockers(db,member_id)
        assert (STATEMENT_MONTHS_BLOCKER in blockers) == (months < 2)
    if months >= 2:
        return
    import onecpase.debicheck_routes as routes
    def forbidden(*args, **kwargs):
        pytest.fail('Must not reach NuPay with fewer than two months')
    monkeypatch.setattr(routes,'push_mandate_to_nupay',forbidden)
    monkeypatch.setattr(routes,'push_mandate_to_nupay_manual_review',forbidden)
    response=client.post(f'/debicheck/{mandate_id}/{path}',data={'override':'1'},follow_redirects=True)
    assert b'NuPay submission blocked' in response.data
    assert b'At least two statement months' in response.data
    with client.application.app_context():
        assert get_db().execute('SELECT status FROM debicheck_mandates WHERE id=?',(mandate_id,)).fetchone()['status']=='pending'


def test_checklist_shows_every_requirement_on_both_screens(client):
    _login(client)
    with client.application.app_context():
        member_id,mandate_id=_member_and_mandate(get_db())
    for url in [f'/debicheck/add?member_id={member_id}', f'/debicheck/{mandate_id}']:
        response=client.get(url)
        assert response.status_code==200
        assert b'ID copy uploaded or verified' not in response.data
        for label in [b'NuPay submission checklist',b'At least two statement months analysed',b'Qualifying affordability analysis']:
            assert label in response.data
    response=client.get(f'/debicheck/requirements/{member_id}')
    assert response.status_code==200
    assert not response.json['months_ready']
    assert not response.json['ready']



def test_nupay_evidence_requires_only_bank_statement_not_id_or_application(client):
    import json
    from onecpase.applications import nupay_submission_blockers, nupay_submission_checklist
    with client.application.app_context():
        db = get_db()
        member_id, _ = _member_and_mandate(db)
        result = {'estimated_income':10000, 'rating':'good', 'verification_qualified':True,
                  'cashflow':{'months':2,'enough_months':True}}
        db.execute("INSERT INTO member_documents(member_id,document_type,original_name,stored_name,analysis_json) VALUES(?,'Bank Statement','bank.pdf','bank.pdf',?)",(member_id,json.dumps(result)))
        assert nupay_submission_blockers(db,member_id) == []
        checklist = nupay_submission_checklist(db,member_id)
        assert len(checklist) == 4
        assert all(item['complete'] for item in checklist)
        assert not any('ID' in item['label'] for item in checklist)
