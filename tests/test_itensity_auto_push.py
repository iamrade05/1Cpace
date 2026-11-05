from unittest.mock import patch

import pytest

from onecpase.applications import _itensity_hard_blockers, push_ready_applications
from onecpase.database import get_db
from onecpase.scheduler import _job_itensity_auto_push
from tests.test_itensity import _fully_ready_application, _login


def _ready(app, **changes):
    app.config["ITENSITY_PUSH_ALLOWED"] = "all"
    changes.setdefault("created_by", 1)
    with app.app_context():
        db = get_db()
        application_id, member_id = _fully_ready_application(db)
        for column, value in changes.items():
            db.execute(f"UPDATE membership_applications SET {column}=? WHERE id=?", (value, application_id))
        db.commit()
        return application_id, member_id, db.execute("PRAGMA database_list").fetchone()["file"]


def _sweep(app, db_path, from_date="2000-01-01", result=(True, "IT-99", None)):
    with patch("onecpase.applications.push_member_to_itensity", return_value=result) as pushed:
        started = push_ready_applications(app, db_path, from_date)
    return started, pushed


def _statuses(app, application_id):
    with app.app_context():
        row = get_db().execute("SELECT application_status,push_status FROM membership_applications WHERE id=?",
                               (application_id,)).fetchone()
        return row["application_status"], row["push_status"]


def _remove_contract(db, member_id):
    db.execute("DELETE FROM member_documents WHERE member_id=? AND document_type='Signed Contract'", (member_id,))


def _remove_fee(db, member_id):
    db.execute("DELETE FROM collections WHERE member_id=?", (member_id,))


def _remove_id_copy(db, member_id):
    db.execute("DELETE FROM member_documents WHERE member_id=? AND document_type='SA ID Copy'", (member_id,))


def _remove_statement(db, member_id):
    db.execute("DELETE FROM member_documents WHERE member_id=? AND document_type='Bank Statement'", (member_id,))


def _decline_debicheck(db, member_id):
    db.execute("UPDATE debicheck_mandates SET status='pending' WHERE member_id=?", (member_id,))


def test_it_pushes_by_itself_once_everything_is_in_place(app):
    application_id, member_id, db_path = _ready(app)

    started, pushed = _sweep(app, db_path)

    assert started == [application_id]
    pushed.assert_called_once()
    assert _statuses(app, application_id) == ("active", "pushed")
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT itensity_ref FROM members WHERE id=?", (member_id,)).fetchone()[0] == "IT-99"
        assert db.execute("SELECT 1 FROM audit_logs WHERE application_id=? AND action='itensity_auto_push_started'",
                          (application_id,)).fetchone()


def test_a_second_sweep_does_not_push_again(app):
    application_id, _, db_path = _ready(app)
    _sweep(app, db_path)

    started, pushed = _sweep(app, db_path)

    assert started == []
    pushed.assert_not_called()


@pytest.mark.parametrize("remove, blocker", [
    (_remove_contract, "Contract signing not confirmed"),
    (_remove_fee, "Registration fee not fully paid"),
    (_remove_id_copy, "ID copy not uploaded"),
    (_remove_statement, "Upload a bank statement before activating DebitCheck."),
    (_decline_debicheck, "DebiCheck approval not confirmed for the latest mandate"),
])
def test_each_requirement_holds_the_push_back(app, remove, blocker):
    application_id, member_id, db_path = _ready(app)
    with app.app_context():
        from flask import g
        g.tenant_slug = "elev8"
        db = get_db()
        remove(db, member_id)
        db.commit()
        assert blocker in _itensity_hard_blockers(db, application_id, member_id)

    started, pushed = _sweep(app, db_path)

    assert started == []
    pushed.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


def test_the_checklist_alone_is_not_enough_to_push(app):
    application_id, _, db_path = _ready(app)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE compliance_checklists SET contract_status='pending' WHERE application_id=?", (application_id,))
        db.commit()

    started, pushed = _sweep(app, db_path)

    assert started == []
    pushed.assert_not_called()


def test_the_push_button_also_follows_the_rule(app):
    application_id, member_id, _ = _ready(app)
    with app.app_context():
        db = get_db()
        _remove_fee(db, member_id)
        db.commit()

    with app.test_client() as client:
        _login(client)
        with patch("onecpase.applications.threading") as threading_mock:
            response = client.post(f"/applications/{application_id}/push")

    assert response.status_code == 302
    assert not threading_mock.Thread.called
    assert _statuses(app, application_id)[1] == "not_pushed"


def _short_failing_statement(db, application_id, member_id):
    db.execute("UPDATE member_documents SET analysis_json=? WHERE member_id=? AND document_type='Bank Statement'",
               ('{"estimated_income":0,"rating":"unknown","verification_qualified":false,"cashflow":{"months":1,"enough_months":false}}', member_id))
    db.execute("UPDATE statement_analyses SET verification_qualified=0 WHERE application_id=?", (application_id,))
    db.commit()


def test_it_pushes_by_itself_after_a_manager_accepted_the_statement(app):
    from onecpase.applications import mark_statement_accepted
    application_id, member_id, db_path = _ready(app)
    with app.app_context():
        db = get_db()
        _short_failing_statement(db, application_id, member_id)
    started, pushed = _sweep(app, db_path)
    assert started == []
    pushed.assert_not_called()

    with app.app_context():
        db = get_db()
        mark_statement_accepted(db, member_id)
        db.commit()
    started, pushed = _sweep(app, db_path)

    assert started == [application_id]
    pushed.assert_called_once()
    assert _statuses(app, application_id) == ("active", "pushed")


def test_acceptance_does_not_cover_the_other_requirements(app):
    from onecpase.applications import mark_statement_accepted
    application_id, member_id, db_path = _ready(app)
    with app.app_context():
        db = get_db()
        _short_failing_statement(db, application_id, member_id)
        mark_statement_accepted(db, member_id)
        _remove_fee(db, member_id)
        db.commit()

    started, pushed = _sweep(app, db_path)

    assert started == []
    pushed.assert_not_called()


def test_older_clients_are_left_alone(app):
    application_id, _, db_path = _ready(app, created_at="2026-09-04 08:00:00")

    started, pushed = _sweep(app, db_path, from_date="2026-11-05")

    assert started == []
    pushed.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


@pytest.mark.parametrize("push_status", ["push_failed", "push_unknown", "pushing", "reconcile_required"])
def test_a_stopped_push_waits_for_a_person(app, push_status):
    _, _, db_path = _ready(app, push_status=push_status)

    started, pushed = _sweep(app, db_path)

    assert started == []
    pushed.assert_not_called()


def test_a_failed_push_is_not_tried_again(app):
    application_id, _, db_path = _ready(app)
    _sweep(app, db_path, result=(False, None, "Itensity stopped"))
    assert _statuses(app, application_id)[1] == "push_unknown"

    started, pushed = _sweep(app, db_path)

    assert started == []
    pushed.assert_not_called()


def test_a_member_already_on_itensity_is_not_pushed(app):
    _, member_id, db_path = _ready(app)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE members SET itensity_ref='1527701' WHERE id=?", (member_id,))
        db.commit()

    started, pushed = _sweep(app, db_path)

    assert started == []
    pushed.assert_not_called()


def test_the_write_switch_still_applies(app):
    application_id, _, db_path = _ready(app)
    app.config["ITENSITY_PUSH_ALLOWED"] = str(application_id + 1)

    started, pushed = _sweep(app, db_path)

    assert started == []
    pushed.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


def _staff_member(app, user_id, name, active=1):
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES (?,?,'x',?,'staff','Sales',?)",
                   (user_id, f"loader{user_id}", name, active))
        db.commit()


@pytest.mark.parametrize("created_by", [99999, None])
def test_a_client_with_nobody_able_to_push_waits_without_being_marked(app, created_by):
    application_id, _, db_path = _ready(app, created_by=created_by)

    started, pushed = _sweep(app, db_path)

    assert started == []
    pushed.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


def test_someone_who_left_only_holds_back_their_own_client(app):
    _staff_member(app, 40, "Left Already", active=0)
    held, _, db_path = _ready(app, created_by=40)
    with app.app_context():
        db = get_db()
        second = db.execute("INSERT INTO members(first_name,last_name,id_number,member_status) VALUES('Other','Client','AUTO-2','Unverified')").lastrowid
        db.commit()
        for table, column in (("member_documents", "member_id"), ("debicheck_mandates", "member_id"), ("collections", "member_id")):
            columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})") if row[1] != "id"]
            names = ",".join(columns)
            picks = ",".join("?" if name == column else name for name in columns)
            db.execute(f"INSERT INTO {table}({names}) SELECT {picks} FROM {table} WHERE {column}=(SELECT member_id FROM membership_applications WHERE id=?)",
                       (second, held))
        other = db.execute("INSERT INTO membership_applications(member_id,package,application_status,push_status,created_by) VALUES(?,'Premium','ready_to_push','not_pushed',1)", (second,)).lastrowid
        for table in ("compliance_checklists", "statement_analyses"):
            columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})") if row[1] != "id"]
            names = ",".join(columns)
            picks = ",".join("?" if name == "application_id" else name for name in columns)
            db.execute(f"INSERT INTO {table}({names}) SELECT {picks} FROM {table} WHERE application_id=?", (other, held))
        db.commit()

    started, _ = _sweep(app, db_path)

    assert started == [other]
    assert _statuses(app, held)[1] == "not_pushed"


def test_it_is_recorded_under_and_shows_the_person_who_loaded_the_client(app):
    _staff_member(app, 41, "Terry Loader")
    application_id, member_id, db_path = _ready(app, created_by=41)

    _, pushed = _sweep(app, db_path)

    assert pushed.call_args.args[0]["sales_person"] == "Terry Loader"
    with app.app_context():
        row = get_db().execute("SELECT user_id FROM audit_logs WHERE application_id=? AND action='itensity_auto_push_started'",
                               (application_id,)).fetchone()
        assert row["user_id"] == 41


def test_the_member_record_names_the_loader_when_the_application_does_not(app):
    _staff_member(app, 42, "Member Loader")
    application_id, member_id, db_path = _ready(app, created_by=None)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE members SET uploaded_by_id=42 WHERE id=?", (member_id,))
        db.commit()

    _, pushed = _sweep(app, db_path)

    assert pushed.call_args.args[0]["sales_person"] == "Member Loader"


def test_the_job_is_off_until_a_start_date_is_given(app):
    application_id, _, _ = _ready(app)
    app.config.update(ITENSITY_AUTO_PUSH_FROM="")

    with patch("onecpase.applications.push_member_to_itensity") as pushed:
        _job_itensity_auto_push(app)

    pushed.assert_not_called()
    assert _statuses(app, application_id)[1] == "not_pushed"


def test_the_job_pushes_when_switched_on(app):
    application_id, _, _ = _ready(app)
    app.config.update(ITENSITY_AUTO_PUSH_FROM="2000-01-01")

    with patch("onecpase.applications.push_member_to_itensity", return_value=(True, "IT-99", None)):
        _job_itensity_auto_push(app)

    assert _statuses(app, application_id) == ("active", "pushed")
