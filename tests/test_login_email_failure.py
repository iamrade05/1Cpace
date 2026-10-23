"""If the sign-in code cannot be emailed, nobody gets in.

On 2026-09-29 an SMTP failure let logins through without a code. Every way the
send can fail must end with no session and no leftover challenge.
"""
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from onecpase.database import get_db
from onecpase.extensions import mail


@pytest.fixture
def staff(app, client):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO users (id, username, password_hash, full_name, role, active, email, must_change_password)
               VALUES (80, 'thandi', ?, 'Thandi Nkosi', 'staff', 1, 'thandi@example.com', 0)""",
            (generate_password_hash("Her-Password-1"),),
        )
        db.commit()
    client.post("/select-tenant", data={"tenant_slug": "elev8"})
    return client


def _login(client):
    return client.post("/login", data={"username": "thandi", "password": "Her-Password-1"})


def _assert_locked_out(app, client, response):
    assert response.status_code == 503
    assert b"could not send" in response.data.lower()
    with client.session_transaction() as s:
        assert not s.get("user_id")
        assert not s.get("_otp_challenge")
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM login_otp_challenges").fetchone()[0] == 0
    # and the verify page cannot be used to skip ahead
    assert client.get("/login/verify").status_code == 302


def test_send_reports_failure(app, staff):
    with patch("onecpase.auth._send_otp_email", lambda *a: False):
        _assert_locked_out(app, staff, _login(staff))


def test_mail_not_configured(app, staff):
    app.config["MAIL_USERNAME"] = ""
    _assert_locked_out(app, staff, _login(staff))


def test_mail_server_raises(app, staff):
    app.config["MAIL_USERNAME"] = "someone"
    with patch.object(mail, "send", side_effect=ConnectionError("SMTP down")):
        _assert_locked_out(app, staff, _login(staff))
