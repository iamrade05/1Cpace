from unittest.mock import patch

from onecpase.applications import _background_itensity_push
from onecpase.database import get_db
from onecpase.integrations import push_member_to_itensity


def _ready_application(db):
    db.execute("INSERT OR IGNORE INTO users(id,username,password_hash,full_name,role,active) VALUES (1,'itensity-admin','x','Itensity Admin','admin',1)")
    member_id = db.execute(
        """INSERT INTO members
           (first_name, last_name, id_number, email, package, member_status)
           VALUES ('Ada', 'Lovelace', 'ITENSITY-001', 'ada@example.com',
                   'Premium', 'Unverified')"""
    ).lastrowid
    application_id = db.execute(
        """INSERT INTO membership_applications
           (member_id, package, application_status, push_status)
           VALUES (?, 'Premium', 'ready_to_push', 'not_pushed')""",
        (member_id,),
    ).lastrowid
    db.execute(
        """INSERT INTO compliance_checklists
           (application_id, contract_status, debit_check_status,
            bank_statement_status, id_copy_status, pos_status,
            guardian_required, manager_approval_status)
           VALUES (?, 'signed', 'approved', 'analysed', 'verified', 'paid',
                   'not_required', 'not_required')""",
        (application_id,),
    )
    for kind in ('SA ID Copy', 'Bank Statement'):
        db.execute(
            "INSERT INTO member_documents (member_id, document_type, original_name, stored_name) VALUES (?, ?, 'test.pdf', 'test.pdf')",
            (member_id, kind),
        )
    db.execute("UPDATE member_documents SET analysis_json=? WHERE member_id=? AND document_type='Bank Statement'", ('{"estimated_income":10000,"rating":"good","verification_qualified":true,"cashflow":{"months":2,"enough_months":true}}', member_id))
    db.execute(
        """INSERT INTO debicheck_mandates
           (member_id, merchant_id, client_ref1, client_ref2, account_name,
            account_type, account_number, branch_code, submit_date, status)
           VALUES (?, 'TEST', 'ITENSITY-001', 'TEST', 'Ada Lovelace', '1',
                   '123456', '123456', '2026-09-01', 'approved')""",
        (member_id,),
    )
    db.execute(
        """INSERT INTO statement_analyses
           (application_id, income_detected, income_months, verification_qualified)
           VALUES (?, 'yes', 2, 1)""",
        (application_id,),
    )
    db.commit()
    return application_id, member_id


def _fully_ready_application(db):
    """Every requirement in place: _ready_application plus the contract and paid reg fee."""
    application_id, member_id = _ready_application(db)
    db.execute(
        "INSERT INTO member_documents (member_id, document_type, original_name, stored_name) VALUES (?, 'Signed Contract', 'contract.pdf', 'contract.pdf')",
        (member_id,),
    )
    db.execute(
        """INSERT INTO collections (member_id, amount_paid, outstanding_balance, collection_date, method, status, payment_purpose)
           VALUES (?, 200, 0, '2026-10-30', 'card', 'paid', 'registration_fee')""",
        (member_id,),
    )
    db.commit()
    return application_id, member_id


def _login(client):
    from onecpase.tenants import TENANT_COOKIE_NAME
    client.set_cookie(TENANT_COOKIE_NAME,'elev8')
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "testuser"
        session["role"] = "admin"
        session['tenant_slug']='elev8'


def test_push_rejects_third_party_debit_order(app):
    # The driver assumes the payer is always the member — this early-return
    # guard is pure logic and doesn't need the Playwright driver mocked.
    with app.app_context():
        success, reference, error = push_member_to_itensity(
            {"first_name": "Ada", "last_name": "Lovelace", "payment_type": "Third-Party Debit Order"}
        )

    assert success is False
    assert reference is None
    assert "third-party" in error.lower()


def test_push_reports_missing_driver(app):
    # RADEPULSE_PATH points at the sibling app's driver scripts. When that
    # folder isn't present (e.g. a standalone deployment), the push should
    # fail with a clear error rather than raise.
    app.config.update(RADEPULSE_PATH=str(app.config["DATABASE_PATH"]) + "-no-such-dir")

    with app.app_context():
        from flask import g
        g.tenant_slug='elev8'
        success, reference, error = push_member_to_itensity(
            {"first_name": "Ada", "last_name": "Lovelace", "payment_type": "Debit Order"}
        )

    assert success is False
    assert reference is None
    assert "driver not found" in error.lower()


def test_push_application_starts_background_push(app):
    # The route itself only has to flip push_status and hand off to a
    # background thread — the actual Itensity call can take minutes, so it
    # must not block the request. Patch applications.py's own `threading`
    # name (not threading.Thread on the real shared module — that would
    # mutate global state and break anything else in-process that also
    # constructs a Thread, e.g. Flask-Limiter's internal timer).
    with app.app_context():
        application_id, member_id = _fully_ready_application(get_db())

    with app.test_client() as client:
        _login(client)
        with patch("onecpase.applications.threading") as threading_mock:
            response = client.post(f"/applications/{application_id}/push")

    assert response.status_code == 302
    assert threading_mock.Thread.called
    assert threading_mock.Thread.return_value.start.called
    with app.app_context():
        application = get_db().execute(
            "SELECT push_status FROM membership_applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        assert application["push_status"] == "pushing"


def test_background_push_activates_application_on_success(app):
    # This is what the thread started above eventually runs. Tested directly
    # rather than through the route so the assertion isn't racing a real
    # background thread.
    with app.app_context():
        application_id, member_id = _fully_ready_application(get_db())
        db_path = get_db().execute("PRAGMA database_list").fetchone()["file"]

    with patch(
        "onecpase.applications.push_member_to_itensity",
        return_value=(True, "IT-99", None),
    ):
        _background_itensity_push(
            app, db_path, application_id, member_id, None, False, [], 1,
        )

    with app.app_context():
        db = get_db()
        application = db.execute(
            "SELECT application_status, push_status, pushed_at "
            "FROM membership_applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        member = db.execute(
            "SELECT itensity_ref, member_status FROM members WHERE id = ?", (member_id,)
        ).fetchone()
        assert application["application_status"] == "active"
        assert application["push_status"] == "pushed"
        assert application["pushed_at"] is not None
        assert member["itensity_ref"] == "IT-99"
        assert member["member_status"] == "Active"


def test_background_push_requires_review_after_unconfirmed_external_failure(app):
    with app.app_context():
        application_id, member_id = _fully_ready_application(get_db())
        db_path = get_db().execute("PRAGMA database_list").fetchone()["file"]

    with patch(
        "onecpase.applications.push_member_to_itensity",
        return_value=(False, None, "service unavailable"),
    ):
        _background_itensity_push(
            app, db_path, application_id, member_id, None, False, [], 1,
        )

    with app.app_context():
        db = get_db()
        application = db.execute(
            "SELECT application_status, push_status, pushed_at "
            "FROM membership_applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        member = db.execute(
            "SELECT itensity_ref, member_status FROM members WHERE id = ?", (member_id,)
        ).fetchone()
        # An unconfirmed external failure cannot safely be retried as a create.
        # Local activation stays unchanged while an administrator reviews it.
        assert application["application_status"] == "ready_to_push"
        assert application["push_status"] == "push_unknown"
        assert application["pushed_at"] is None
        assert member["itensity_ref"] is None
        assert member["member_status"] == "Unverified"
