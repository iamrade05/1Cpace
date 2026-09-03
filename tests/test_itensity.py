from unittest.mock import patch

from onecpase.applications import _background_itensity_push
from onecpase.database import get_db
from onecpase.integrations import push_member_to_itensity


def _ready_application(db):
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
    db.execute(
        """INSERT INTO statement_analyses
           (application_id, income_detected, income_months, verification_qualified)
           VALUES (?, 'yes', 2, 1)""",
        (application_id,),
    )
    db.commit()
    return application_id, member_id


def _login(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "testuser"
        session["role"] = "admin"


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
        application_id, member_id = _ready_application(get_db())

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
        application_id, member_id = _ready_application(get_db())
        db_path = get_db().execute("PRAGMA database_list").fetchone()["file"]

    with patch(
        "onecpase.applications.push_member_to_itensity",
        return_value=(True, "IT-99", None),
    ):
        _background_itensity_push(
            app, db_path, application_id, member_id, None, False, [], None,
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


def test_background_push_marks_failed_and_stays_retryable(app):
    with app.app_context():
        application_id, member_id = _ready_application(get_db())
        db_path = get_db().execute("PRAGMA database_list").fetchone()["file"]

    with patch(
        "onecpase.applications.push_member_to_itensity",
        return_value=(False, None, "service unavailable"),
    ):
        _background_itensity_push(
            app, db_path, application_id, member_id, None, False, [], None,
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
        # push_status is 'push_failed' (not the pre-push 'not_pushed') so the
        # UI can show what happened, but application_status never advanced —
        # the push can simply be retried.
        assert application["application_status"] == "ready_to_push"
        assert application["push_status"] == "push_failed"
        assert application["pushed_at"] is None
        assert member["itensity_ref"] is None
        assert member["member_status"] == "Unverified"
