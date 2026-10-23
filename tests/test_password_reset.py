"""Forgotten password: an emailed code proves who is asking."""
from unittest.mock import patch

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from onecpase.admin import DEFAULT_STAFF_PASSWORD
from onecpase.database import get_db


class _Mail:
    def __init__(self):
        self.sent = []

    def __call__(self, to, name, code):
        self.sent.append((to, code))
        return True


@pytest.fixture
def mailbox():
    box = _Mail()
    with patch("onecpase.auth._send_reset_email", box):
        yield box


def _user(app, uid=70, username="reset.person", email="reset@example.com", active=1):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO users (id, username, password_hash, full_name, role, active, email, must_change_password)
               VALUES (?, ?, ?, 'Reset Person', 'staff', ?, ?, 1)""",
            (uid, username, generate_password_hash("Old-Password-1"), active, email),
        )
        db.commit()
    return uid


def _hash(app, uid):
    with app.app_context():
        return get_db().execute("SELECT password_hash FROM users WHERE id=?", (uid,)).fetchone()[0]


def _request(client, username):
    client.post("/select-tenant", data={"tenant_slug": "elev8"})
    return client.post("/forgot-password", data={"username": username}, follow_redirects=True)


def _reset(client, code, pw="Brand-New-Pass-9", confirm=None):
    return client.post("/reset-password", data={
        "code": code, "new_password": pw, "confirm_password": confirm or pw}, follow_redirects=True)


def test_login_page_offers_the_reset_link(client):
    client.post("/select-tenant", data={"tenant_slug": "elev8"})
    assert b"Forgot password?" in client.get("/login").data


def test_code_goes_to_the_account_email_and_resets_the_password(app, client, mailbox):
    uid = _user(app)
    _request(client, "Reset.Person")
    assert mailbox.sent and mailbox.sent[-1][0] == "reset@example.com"

    _reset(client, mailbox.sent[-1][1])

    new = _hash(app, uid)
    assert check_password_hash(new, "Brand-New-Pass-9")
    with app.app_context():
        assert get_db().execute("SELECT must_change_password FROM users WHERE id=?", (uid,)).fetchone()[0] == 0
        assert get_db().execute("SELECT COUNT(*) FROM login_otp_challenges").fetchone()[0] == 0


def test_wrong_code_changes_nothing_and_locks_after_five_tries(app, client, mailbox):
    uid = _user(app)
    before = _hash(app, uid)
    _request(client, "reset.person")
    real = mailbox.sent[-1][1]
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        _reset(client, wrong)
    _reset(client, real)  # correct code, but the attempts are used up
    assert _hash(app, uid) == before


def test_unknown_username_gets_the_same_answer_and_no_email(app, client, mailbox):
    known = _request(client, "nobody.here").get_data(as_text=True)
    assert mailbox.sent == []
    assert "If that username has an email address on file" in known


def test_account_without_email_is_sent_nothing(app, client, mailbox):
    _user(app, email="")
    _request(client, "reset.person")
    assert mailbox.sent == []


def test_the_standard_password_cannot_be_chosen(app, client, mailbox):
    uid = _user(app)
    before = _hash(app, uid)
    _request(client, "reset.person")
    _reset(client, mailbox.sent[-1][1], pw=DEFAULT_STAFF_PASSWORD)
    assert _hash(app, uid) == before


def test_mismatched_or_short_passwords_are_refused(app, client, mailbox):
    uid = _user(app)
    before = _hash(app, uid)
    _request(client, "reset.person")
    code = mailbox.sent[-1][1]
    _reset(client, code, pw="Brand-New-Pass-9", confirm="different-one")
    _reset(client, code, pw="short", confirm="short")
    assert _hash(app, uid) == before


def test_a_reset_code_cannot_be_used_to_sign_in(app, client, mailbox):
    _user(app)
    _request(client, "reset.person")
    code = mailbox.sent[-1][1]
    response = client.post("/login/verify", data={"otp": code}, follow_redirects=False)
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    with client.session_transaction() as s:
        assert not s.get("user_id")


def test_inactive_accounts_get_no_code(app, client, mailbox):
    _user(app, active=0)
    _request(client, "reset.person")
    assert mailbox.sent == []


def test_second_request_within_a_minute_keeps_the_first_code_working(app, client, mailbox):
    uid = _user(app)
    _request(client, "reset.person")
    first = mailbox.sent[-1][1]
    _request(client, "reset.person")
    assert len(mailbox.sent) == 1
    _reset(client, first)
    assert check_password_hash(_hash(app, uid), "Brand-New-Pass-9")
