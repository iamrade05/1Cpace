"""A manager may send a client who failed affordability to NuPay, after checking the statement."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from onecpase.applications import STATEMENT_QUALIFYING_BLOCKER, can_override_nupay
from onecpase.database import get_db
from onecpase.statement_parser import analyse_pdf
from onecpase.tenants import TENANT_COOKIE_NAME
from tests.test_debicheck_routes import _login, _member_and_mandate
from tests.test_statement_parser import _fake_pdf

CHECKED = {"override": "1", "statement_checked": "1", "statement_note": "Top-ups arrive on the 1st; debit day moved."}
FAILED = {"estimated_income": 0, "rating": "unknown", "verification_qualified": False,
          "disputed_debits": 1, "cashflow": {"months": 3, "enough_months": True, "avg_money_in": 10490.58}}


def _as_role(client, role):
    _login(client, role)
    client.set_cookie(TENANT_COOKIE_NAME, "elev8")
    with client.application.app_context():
        db = get_db()
        db.execute("INSERT OR IGNORE INTO user_permissions(user_id,permission) VALUES(1,'all_members')")
        db.commit()
    with client.session_transaction() as session:
        session["tenant_slug"] = "elev8"


def _manager(client):
    _as_role(client, "manager")


def _failed_statement(client, result=FAILED):
    with client.application.app_context():
        db = get_db()
        member_id, mandate_id = _member_and_mandate(db)
        document_id = db.execute(
            "INSERT INTO member_documents(member_id,document_type,original_name,stored_name,analysis_json) "
            "VALUES(?,'Bank Statement','s.pdf','s.pdf',?)",
            (member_id, json.dumps(result)),
        ).lastrowid
        db.commit()
    return member_id, mandate_id, document_id


def _open(client, member_id, document_id):
    folder = Path(client.application.config["UPLOAD_FOLDER"]) / "members" / str(member_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "s.pdf").write_bytes(b"%PDF-1.4")
    assert client.get(f"/members/{member_id}/documents/{document_id}/download").status_code == 200


@pytest.fixture
def sent(monkeypatch):
    import onecpase.debicheck_routes as routes
    calls = []
    monkeypatch.setattr(routes, "push_mandate_to_nupay", lambda mandate: (calls.append(mandate) or True, "Accepted"))
    return calls


def test_a_manager_who_checked_the_statement_can_send_it(client, sent):
    _manager(client)
    member_id, mandate_id, document_id = _failed_statement(client)
    _open(client, member_id, document_id)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    assert len(sent) == 1
    with client.application.app_context():
        record = get_db().execute("SELECT new_value FROM audit_logs WHERE action='nupay_manager_override'").fetchone()["new_value"]
    assert CHECKED["statement_note"] in record


@pytest.mark.parametrize("opened, data", [
    (False, CHECKED),
    (True, {"override": "1", "statement_note": "Fine"}),
    (True, {"override": "1", "statement_checked": "1", "statement_note": " "}),
    (True, {"statement_checked": "1", "statement_note": "Fine"}),
])
def test_it_stays_blocked_until_the_check_is_complete(client, sent, opened, data):
    _manager(client)
    member_id, mandate_id, document_id = _failed_statement(client)
    if opened:
        _open(client, member_id, document_id)

    client.post(f"/debicheck/{mandate_id}/push", data=data)

    assert not sent


def test_under_two_months_stays_blocked_for_a_manager(client, sent):
    _manager(client)
    member_id, mandate_id, document_id = _failed_statement(client, {**FAILED, "cashflow": {"months": 1, "enough_months": False}})
    _open(client, member_id, document_id)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    assert not sent


SHORT = {"estimated_income": 0, "rating": "good", "verification_qualified": True, "money_in_months": 2,
         "failed_debits": 1, "cashflow": {"months": 1, "enough_months": False, "avg_money_in": 40875.13}}


def test_a_manager_can_accept_a_short_statement_with_r3000_in_two_months(client, sent):
    _manager(client)
    member_id, mandate_id, document_id = _failed_statement(client, SHORT)
    _open(client, member_id, document_id)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    assert len(sent) == 1


def test_a_short_statement_still_needs_the_managers_check(client, sent):
    _manager(client)
    member_id, mandate_id, _ = _failed_statement(client, SHORT)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    assert not sent


def test_ordinary_staff_cannot_send_a_short_statement(client, sent):
    _as_role(client, "staff")
    member_id, mandate_id, document_id = _failed_statement(client, SHORT)
    _open(client, member_id, document_id)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    assert not sent


def test_the_manager_sees_the_override_for_a_short_statement(client):
    _manager(client)
    _, mandate_id, _ = _failed_statement(client, SHORT)

    page = client.get(f"/debicheck/{mandate_id}").get_data(as_text=True)

    assert "Check the statement before overriding" in page
    assert "Override &amp; Push Automatically" in page


def test_one_check_carries_the_statement_through_to_itensity(client, sent):
    from onecpase.applications import STATEMENT_MONTHS_BLOCKER, _itensity_hard_blockers, statement_accepted
    _manager(client)
    member_id, mandate_id, document_id = _failed_statement(client, SHORT)
    with client.application.app_context():
        db = get_db()
        application_id = db.execute("INSERT INTO membership_applications(member_id) VALUES(?)", (member_id,)).lastrowid
        db.commit()
        assert STATEMENT_MONTHS_BLOCKER in _itensity_hard_blockers(db, application_id, member_id)
    _open(client, member_id, document_id)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    with client.application.app_context():
        db = get_db()
        assert statement_accepted(db, member_id) is True
        assert STATEMENT_MONTHS_BLOCKER not in _itensity_hard_blockers(db, application_id, member_id)
        # A newer statement has not been checked, so it asks again.
        db.execute("INSERT INTO member_documents(member_id,document_type,original_name,stored_name,analysis_json) VALUES(?,'Bank Statement','new.pdf','new.pdf',?)",
                   (member_id, json.dumps(SHORT)))
        db.commit()
        assert statement_accepted(db, member_id) is False
        assert STATEMENT_MONTHS_BLOCKER in _itensity_hard_blockers(db, application_id, member_id)


def test_a_blocked_attempt_does_not_accept_the_statement(client, sent):
    from onecpase.applications import statement_accepted
    _manager(client)
    member_id, mandate_id, _ = _failed_statement(client, SHORT)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    with client.application.app_context():
        assert statement_accepted(get_db(), member_id) is False


def _activity(client, member_id):
    with client.application.app_context():
        return [row[0] for row in get_db().execute(
            "SELECT note FROM member_notes WHERE member_id=? ORDER BY id", (member_id,)).fetchall()]


def test_the_note_says_it_went_via_override_and_what_was_wrong(client, sent):
    _manager(client)
    member_id, mandate_id, document_id = _failed_statement(client, SHORT)
    _open(client, member_id, document_id)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    note = next(entry for entry in _activity(client, member_id) if "via Override" in entry)
    assert note.startswith("Pushed to NuPay via Override")
    assert "covers only about 1 month(s), not two" in note
    assert "1 failed debit order(s)" in note
    assert CHECKED["statement_note"] in note


def test_the_note_names_a_failed_money_in_test(client, sent):
    _manager(client)
    member_id, mandate_id, document_id = _failed_statement(client)
    _open(client, member_id, document_id)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    note = next(entry for entry in _activity(client, member_id) if "via Override" in entry)
    assert "no salary was found and R3,000 did not come in during two months" in note


def test_no_override_note_if_the_push_fails(client, monkeypatch):
    import onecpase.debicheck_routes as routes
    monkeypatch.setattr(routes, "push_mandate_to_nupay", lambda mandate: (False, "Rejected"))
    _manager(client)
    member_id, mandate_id, document_id = _failed_statement(client, SHORT)
    _open(client, member_id, document_id)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    assert not [entry for entry in _activity(client, member_id) if "via Override" in entry]


def test_a_mandate_with_nothing_wrong_gets_no_override_note(client, sent):
    _manager(client)
    good = {"estimated_income": 9000, "rating": "good", "verification_qualified": True,
            "cashflow": {"months": 2, "enough_months": True}}
    member_id, mandate_id, _ = _failed_statement(client, good)

    client.post(f"/debicheck/{mandate_id}/push")

    assert len(sent) == 1
    assert not [entry for entry in _activity(client, member_id) if "via Override" in entry]


def test_other_staff_cannot_override(client, sent):
    _as_role(client, "staff")
    member_id, mandate_id, document_id = _failed_statement(client)
    _open(client, member_id, document_id)

    client.post(f"/debicheck/{mandate_id}/push", data=CHECKED)

    assert not sent


def test_the_manager_sees_the_check_with_what_the_system_read(client):
    _manager(client)
    _, mandate_id, _ = _failed_statement(client)

    page = client.get(f"/debicheck/{mandate_id}").get_data(as_text=True)

    assert "Check the statement before overriding" in page
    assert "R10490.58" in page
    assert "Disputed debit orders: 1" in page


def test_the_manual_review_worker_accepts_a_managers_override(client):
    import onecpase.debicheck_routes as routes
    _manager(client)
    member_id, mandate_id, _ = _failed_statement(client)
    with client.application.app_context():
        db = get_db()
        db_path = db.execute("PRAGMA database_list").fetchone()["file"]

    with patch.object(routes, "push_mandate_to_nupay_manual_review", return_value=(True, "Done")) as sent:
        routes._background_manual_review_push(client.application, db_path, mandate_id, {}, member_id, overridden=True, user_id=1,
                                              override_note="Pushed to NuPay via Override by Test.")

    sent.assert_called_once()
    assert "Pushed to NuPay via Override by Test." in _activity(client, member_id)


@pytest.mark.parametrize("role, blockers, allowed", [
    ("manager", [], False),
    ("manager", [STATEMENT_QUALIFYING_BLOCKER], True),
    ("admin", [STATEMENT_QUALIFYING_BLOCKER], True),
    ("staff", [STATEMENT_QUALIFYING_BLOCKER], False),
    ("manager", ["Upload a bank statement before activating DebitCheck.", STATEMENT_QUALIFYING_BLOCKER], False),
])
def test_who_may_override_what(role, blockers, allowed):
    assert can_override_nupay(role, blockers) is allowed


def test_capitec_months_and_disputes_are_read(tmp_path):
    text = "\n".join([
        "30/09/2026 Eft Debit Order (3464788631): Vodacom (I9123684) Cellphone -184.01 -3.00 31.75",
        "01/10/2026 Payment Received: Dispute Vodacom I9123684 260930 3464788631 Other Income 184.01 -6.00 600.27",
    ])
    statement = tmp_path / "statement.pdf"
    statement.write_bytes(b"%PDF-1.4")

    with patch("onecpase.statement_parser.pdfplumber.open", return_value=_fake_pdf(text)):
        result = analyse_pdf(str(statement), monthly_installment=334.75)

    assert result["disputed_debits"] == 1
    assert result["months_seen"] == 1
    assert "disputed debit order" in result["summary"]
