"""A signed TT3 form is evidence; only NuPay's own answer moves the DebiCheck gate."""
import io

import pytest

from onecpase import nupay_driver, tt3
from onecpase.applications import ensure_member_application
from onecpase.collections import mandate_state_for
from onecpase.database import get_db
from onecpase.encryption import encrypt, hash_for_lookup
from onecpase.members import TT3_DOCUMENT

ID_NUMBER = "8001015009087"


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(tt3, "_spawn", lambda *args: calls.append(args))
    return calls


def _member(app, id_number=ID_NUMBER):
    with app.test_request_context():
        db = get_db()
        member_id = db.execute(
            """INSERT INTO members (member_ref, first_name, last_name, id_number, id_number_hash,
                   contact, join_date, member_status)
               VALUES ('ELE-0000000002', 'Tee', 'Three', ?, ?, '0712345678', '2026-01-01', 'Active')""",
            (encrypt(id_number), hash_for_lookup(id_number)),
        ).lastrowid
        application_id = ensure_member_application(db, member_id)
        db.execute("INSERT OR IGNORE INTO compliance_checklists (application_id) VALUES (?)", (application_id,))
        db.commit()
    return member_id


def _login(client):
    with client.session_transaction() as sess:
        sess.update({"user_id": 1, "username": "u", "role": "admin"})


def _upload(client, member_id, doc_type=TT3_DOCUMENT):
    return client.post(
        f"/members/{member_id}/documents",
        data={"document_type": doc_type, "document": (io.BytesIO(b"%PDF-1.4 signed form"), "tt3.pdf")},
        content_type="multipart/form-data", follow_redirects=True,
    )


def _nupay_row(status="Active"):
    return {
        "client_reference": ID_NUMBER, "contract_reference": "DCPRD000TT3", "status": status,
        "debtor_id": ID_NUMBER, "debtor_name": "Tee Three", "debtor_account_number": "123456789",
        "debtor_branch_number": "051001", "instalment_amount": "599.00", "frequency": "MONTHLY",
        "first_collection_date": "2026-03-01", "mandate_acceptance_date": "2026-02-01 10:00:00",
    }


def _run(app, monkeypatch, member_id, rows, message=""):
    monkeypatch.setattr(nupay_driver, "fetch_mandate_report_rows", lambda mandate=None: (rows, message))
    with app.app_context():
        db = get_db()
        check_id = db.execute(
            "INSERT INTO nupay_mandate_checks (member_id, state, checked_by) VALUES (?, ?, 1)",
            (member_id, tt3.CHECKING),
        ).lastrowid
        db.commit()
        path = db.execute("PRAGMA database_list").fetchone()["file"]
    tt3.run_check(app, path, check_id, member_id, 1)
    return check_id


def _one(app, sql, *params):
    with app.app_context():
        return get_db().execute(sql, params).fetchone()


def _gate(app, member_id):
    return _one(
        app,
        """SELECT cc.debit_check_status AS status FROM compliance_checklists cc
           JOIN membership_applications a ON a.id = cc.application_id WHERE a.member_id=?""",
        member_id,
    )["status"]


def test_uploading_a_tt3_stores_it_and_starts_a_nupay_check(app, client, spawned):
    member_id = _member(app)
    _login(client)

    page = _upload(client, member_id).get_data(as_text=True)

    assert "Checking NuPay" in page
    assert len(spawned) == 1
    assert _one(app, "SELECT document_type FROM member_documents WHERE member_id=?", member_id)["document_type"] == TT3_DOCUMENT
    assert _one(app, "SELECT state FROM nupay_mandate_checks WHERE member_id=?", member_id)["state"] == tt3.CHECKING


def test_the_upload_alone_never_passes_the_debicheck_gate(app, client, spawned):
    member_id = _member(app)
    _login(client)

    _upload(client, member_id)

    assert _gate(app, member_id) != "approved"
    assert _one(app, "SELECT COUNT(*) AS n FROM debicheck_mandates")["n"] == 0


def test_other_documents_do_not_start_a_nupay_check(app, client, spawned):
    member_id = _member(app)
    _login(client)

    _upload(client, member_id, "Debit Mandate")

    assert spawned == []
    assert _one(app, "SELECT COUNT(*) AS n FROM nupay_mandate_checks")["n"] == 0


def test_an_active_mandate_on_nupay_is_recorded_and_passes_the_gate(app, monkeypatch):
    member_id = _member(app)

    check_id = _run(app, monkeypatch, member_id, [_nupay_row()])

    check = _one(app, "SELECT * FROM nupay_mandate_checks WHERE id=?", check_id)
    assert check["state"] == "active"
    assert check["contract_reference"] == "DCPRD000TT3"
    mandate = _one(app, "SELECT * FROM debicheck_mandates WHERE member_id=?", member_id)
    assert mandate["imported_from_nupay"] == 1
    assert _gate(app, member_id) == "approved"


def test_nothing_on_nupay_leaves_the_gate_alone(app, monkeypatch):
    member_id = _member(app)

    check_id = _run(app, monkeypatch, member_id, [])

    assert _one(app, "SELECT state FROM nupay_mandate_checks WHERE id=?", check_id)["state"] == "none"
    assert _gate(app, member_id) != "approved"
    assert _one(app, "SELECT COUNT(*) AS n FROM debicheck_mandates")["n"] == 0


def test_a_mandate_that_is_not_active_leaves_the_gate_alone(app, monkeypatch):
    member_id = _member(app)

    check_id = _run(app, monkeypatch, member_id, [_nupay_row("Pending")])

    assert _one(app, "SELECT state FROM nupay_mandate_checks WHERE id=?", check_id)["state"] == "inactive"
    assert _gate(app, member_id) != "approved"


def test_nupay_being_unavailable_is_reported_and_not_read_as_no_mandate(app, monkeypatch):
    member_id = _member(app)

    check_id = _run(app, monkeypatch, member_id, None, "NuPay login failed")

    check = _one(app, "SELECT * FROM nupay_mandate_checks WHERE id=?", check_id)
    assert check["state"] == "error"
    assert "NuPay login failed" in check["message"]
    assert _gate(app, member_id) != "approved"
    with app.app_context():
        assert mandate_state_for(get_db(), member_id, None) == "unknown"


def test_a_check_in_progress_is_not_read_by_collections_as_a_result(app, client, spawned):
    member_id = _member(app)
    _login(client)
    _upload(client, member_id)

    with app.app_context():
        assert mandate_state_for(get_db(), member_id, None) == "unknown"


def test_the_profile_shows_the_check_and_lets_staff_ask_again(app, client, spawned):
    member_id = _member(app)
    _login(client)
    assert "Check NuPay for TT3" not in client.get(f"/members/{member_id}").get_data(as_text=True)

    _upload(client, member_id)
    page = client.get(f"/members/{member_id}").get_data(as_text=True)

    assert "Checking NuPay now" in page
    assert "Check NuPay for TT3" in page


def test_only_one_check_runs_at_a_time(app, client, spawned):
    member_id = _member(app)
    _login(client)
    _upload(client, member_id)

    page = client.post(f"/members/{member_id}/check-tt3", follow_redirects=True).get_data(as_text=True)

    assert "already running" in page
    assert len(spawned) == 1


def test_a_check_killed_by_a_restart_does_not_block_a_new_one(app, client, spawned):
    member_id = _member(app)
    _login(client)
    _upload(client, member_id)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE nupay_mandate_checks SET checked_at='2000-01-01 00:00:00'")
        db.commit()

    client.post(f"/members/{member_id}/check-tt3")

    assert len(spawned) == 2


def test_asking_again_needs_a_tt3_on_file(app, client, spawned):
    member_id = _member(app)
    _login(client)

    assert client.post(f"/members/{member_id}/check-tt3").status_code == 404
    assert spawned == []
