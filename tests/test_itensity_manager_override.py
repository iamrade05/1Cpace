from pathlib import Path
from unittest.mock import patch

import pytest

from onecpase.applications import _background_itensity_push, can_override_push
from onecpase.database import get_db
from onecpase.tenants import TENANT_COOKIE_NAME
from tests.test_itensity_auto_push import (
    _decline_debicheck, _ready, _remove_contract, _remove_fee, _remove_id_copy,
    _remove_statement, _statuses, _sweep,
)

MANAGER_ID, CONSULTANT_ID = 2, 3


def _staff(app):
    with app.app_context():
        db = get_db()
        db.execute("INSERT OR IGNORE INTO users(id,username,password_hash,full_name,role,department,active) VALUES (?,'push-manager','x','Push Manager','manager','Sales',1)", (MANAGER_ID,))
        db.execute("INSERT OR IGNORE INTO users(id,username,password_hash,full_name,role,department,active) VALUES (?,'push-consultant','x','Push Consultant','staff','Sales',1)", (CONSULTANT_ID,))
        db.commit()


def _login_as(client, user_id, role):
    client.set_cookie(TENANT_COOKIE_NAME, "elev8")
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["username"] = f"user-{user_id}"
        session["role"] = role
        session["permissions"] = ["add_member", "all_members"]
        session["tenant_slug"] = "elev8"


CHECKED = {"override": "1", "statement_checked": "1", "statement_note": "Salary lands on the 25th, the reader missed it."}


def _click_override(app, application_id, user_id=MANAGER_ID, role="manager", data=None, open_statement_of=None):
    _staff(app)
    with app.test_client() as client:
        _login_as(client, user_id, role)
        if open_statement_of:
            _open_statement(app, client, open_statement_of)
        with patch("onecpase.applications.threading") as threading_mock:
            client.post(f"/applications/{application_id}/push", data=CHECKED if data is None else data)
    return threading_mock.Thread


def _open_statement(app, client, member_id):
    with app.app_context():
        document_id = get_db().execute(
            "SELECT id FROM member_documents WHERE member_id=? AND document_type='Bank Statement' ORDER BY id DESC LIMIT 1",
            (member_id,),
        ).fetchone()[0]
    folder = Path(app.config["UPLOAD_FOLDER"]) / "members" / str(member_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "test.pdf").write_bytes(b"%PDF-1.4")
    response = client.get(f"/members/{member_id}/documents/{document_id}/download")
    assert response.status_code == 200


def _fail_affordability(db, application_id, member_id):
    db.execute("UPDATE member_documents SET analysis_json=? WHERE member_id=? AND document_type='Bank Statement'",
               ('{"estimated_income":10000,"rating":"poor","verification_qualified":false,"cashflow":{"months":2,"enough_months":true}}', member_id))
    db.execute("UPDATE statement_analyses SET verification_qualified=0 WHERE application_id=?", (application_id,))


def _short_statement(db, application_id, member_id):
    db.execute("UPDATE member_documents SET analysis_json=? WHERE member_id=? AND document_type='Bank Statement'",
               ('{"estimated_income":10000,"rating":"good","verification_qualified":true,"cashflow":{"months":1,"enough_months":false}}', member_id))


def _guardian_outstanding(db, application_id, member_id):
    db.execute("UPDATE compliance_checklists SET guardian_required='required', guardian_status='pending' WHERE application_id=?", (application_id,))


def _manager_approval_pending(db, application_id, member_id):
    db.execute("UPDATE compliance_checklists SET manager_approval_status='required' WHERE application_id=?", (application_id,))


def _change(app, change, application_id, member_id):
    with app.app_context():
        db = get_db()
        change(db, application_id, member_id)
        db.commit()


def _remove(app, remove, member_id):
    with app.app_context():
        db = get_db()
        remove(db, member_id)
        db.commit()


@pytest.mark.parametrize("change", [_fail_affordability, _short_statement, _guardian_outstanding, _manager_approval_pending])
def test_the_automatic_push_holds_back_until_a_person_decides(app, change):
    application_id, member_id, db_path = _ready(app)
    _change(app, change, application_id, member_id)

    started, pushed = _sweep(app, db_path)

    assert started == []
    pushed.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


@pytest.mark.parametrize("change", [_fail_affordability, _short_statement, _guardian_outstanding, _manager_approval_pending])
def test_a_manager_can_override_it(app, change):
    application_id, member_id, _ = _ready(app)
    _change(app, change, application_id, member_id)

    thread = _click_override(app, application_id, open_statement_of=member_id)

    thread.assert_called_once()
    with patch("onecpase.applications.push_member_to_itensity", return_value=(True, "IT-99", None)) as pushed:
        _background_itensity_push(*thread.call_args.kwargs["args"])
    pushed.assert_called_once()
    assert _statuses(app, application_id) == ("active", "pushed")
    with app.app_context():
        db = get_db()
        requested = db.execute("SELECT user_id FROM audit_logs WHERE application_id=? AND action='itensity_manager_override_requested'",
                               (application_id,)).fetchone()
        assert requested["user_id"] == MANAGER_ID
        assert db.execute("SELECT 1 FROM audit_logs WHERE application_id=? AND action='itensity_push_overridden'", (application_id,)).fetchone()


@pytest.mark.parametrize("remove", [_remove_contract, _remove_fee, _remove_id_copy, _remove_statement, _decline_debicheck])
def test_a_manager_cannot_override_the_five_requirements(app, remove):
    application_id, member_id, _ = _ready(app)
    _remove(app, remove, member_id)

    thread = _click_override(app, application_id)

    thread.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


def test_one_of_the_five_missing_blocks_a_manager_even_with_affordability_also_failing(app):
    application_id, member_id, _ = _ready(app)
    _change(app, _fail_affordability, application_id, member_id)
    _remove(app, _remove_fee, member_id)

    thread = _click_override(app, application_id)

    thread.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


def test_a_requirement_lost_after_the_click_stops_a_managers_push(app):
    application_id, member_id, _ = _ready(app)
    _change(app, _fail_affordability, application_id, member_id)
    thread = _click_override(app, application_id, open_statement_of=member_id)
    thread.assert_called_once()
    _remove(app, _remove_fee, member_id)

    with patch("onecpase.applications.push_member_to_itensity", return_value=(True, "IT-99", None)) as pushed:
        _background_itensity_push(*thread.call_args.kwargs["args"])

    pushed.assert_not_called()
    assert _statuses(app, application_id)[1] == "push_failed"


def test_a_manager_must_ask_for_the_override(app):
    application_id, member_id, _ = _ready(app)
    _change(app, _fail_affordability, application_id, member_id)

    thread = _click_override(app, application_id, data={}, open_statement_of=member_id)

    thread.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


def test_a_manager_must_open_the_statement_before_overriding_affordability(app):
    application_id, member_id, _ = _ready(app)
    _change(app, _fail_affordability, application_id, member_id)

    thread = _click_override(app, application_id)

    thread.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


@pytest.mark.parametrize("data", [
    {"override": "1", "statement_note": "Looks fine"},
    {"override": "1", "statement_checked": "1", "statement_note": "   "},
])
def test_a_manager_must_confirm_and_write_what_they_found(app, data):
    application_id, member_id, _ = _ready(app)
    _change(app, _fail_affordability, application_id, member_id)

    thread = _click_override(app, application_id, data=data, open_statement_of=member_id)

    thread.assert_not_called()


def test_another_managers_look_at_the_statement_does_not_count(app):
    application_id, member_id, _ = _ready(app)
    _change(app, _fail_affordability, application_id, member_id)
    _staff(app)
    with app.test_client() as client:
        _login_as(client, 1, "admin")
        _open_statement(app, client, member_id)

    thread = _click_override(app, application_id)

    thread.assert_not_called()


def test_a_newer_statement_must_be_opened_again(app):
    application_id, member_id, _ = _ready(app)
    _change(app, _fail_affordability, application_id, member_id)
    _staff(app)
    with app.test_client() as client:
        _login_as(client, MANAGER_ID, "manager")
        _open_statement(app, client, member_id)
    with app.app_context():
        db = get_db()
        db.execute("""INSERT INTO member_documents (member_id, document_type, original_name, stored_name, analysis_json)
                      SELECT member_id, document_type, 'new.pdf', 'new.pdf', analysis_json FROM member_documents
                      WHERE member_id=? AND document_type='Bank Statement'""", (member_id,))
        db.commit()

    thread = _click_override(app, application_id)

    thread.assert_not_called()


def test_what_the_manager_found_is_recorded(app):
    application_id, member_id, _ = _ready(app)
    _change(app, _fail_affordability, application_id, member_id)

    _click_override(app, application_id, open_statement_of=member_id)

    with app.app_context():
        record = get_db().execute(
            "SELECT new_value FROM audit_logs WHERE application_id=? AND action='itensity_manager_override_requested'",
            (application_id,),
        ).fetchone()["new_value"]
    assert CHECKED["statement_note"] in record


def test_guardian_consent_needs_no_statement_check(app):
    application_id, member_id, _ = _ready(app)
    _change(app, _guardian_outstanding, application_id, member_id)

    thread = _click_override(app, application_id, data={"override": "1"})

    thread.assert_called_once()


def test_other_staff_cannot_override(app):
    application_id, member_id, _ = _ready(app)
    _change(app, _fail_affordability, application_id, member_id)

    thread = _click_override(app, application_id, user_id=CONSULTANT_ID, role="staff")

    thread.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


def test_an_admin_can_still_override_anything(app):
    application_id, member_id, _ = _ready(app)
    _remove(app, _remove_fee, member_id)

    thread = _click_override(app, application_id, user_id=1, role="admin")

    thread.assert_called_once()


@pytest.mark.parametrize("remove, shown", [(None, True), (_remove_fee, False)])
def test_a_manager_only_sees_the_button_when_it_would_work(app, remove, shown):
    application_id, member_id, _ = _ready(app)
    _change(app, _fail_affordability, application_id, member_id)
    if remove:
        _remove(app, remove, member_id)
    _staff(app)

    with app.test_client() as client:
        _login_as(client, MANAGER_ID, "manager")
        page = client.get(f"/applications/{application_id}", follow_redirects=True).get_data(as_text=True)

    assert ("Override &amp; Push to Itensity" in page) is shown
    assert ("Check the statement before overriding" in page) is shown


@pytest.mark.parametrize("role, missing, allowed", [
    ("manager", [], False),
    ("admin", [], False),
    ("manager", ["Guardian consent not approved"], True),
    ("manager", ["Registration fee not fully paid"], False),
    ("admin", ["Registration fee not fully paid"], True),
    ("staff", ["Guardian consent not approved"], False),
])
def test_who_may_override_what(role, missing, allowed):
    assert can_override_push(role, missing) is allowed
