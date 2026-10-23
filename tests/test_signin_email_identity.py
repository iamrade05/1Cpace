"""The emailed sign-in code is what proves the right person is signing in.

Two things must hold for that to mean anything: a mailbox belongs to one
account only, and production can never run with the code switched off.
"""

import pytest

from onecpase import create_app
from onecpase.database import get_db
from test_staff_passwords import _make_admin, _make_user, _user


def _set_email(client, uid, email):
    with client.application.app_context():
        db = get_db()
        db.execute("UPDATE users SET email=? WHERE id=?", (email, uid))
        db.commit()


def _edit(client, uid, email):
    return client.post(
        f"/admin/users/{uid}/edit",
        data={"full_name": "Edited User", "email": email, "contact": "",
              "role": "staff", "department": "General", "active": "on"},
        follow_redirects=True,
    )


def test_edit_refuses_an_email_another_account_already_uses(client):
    _make_admin(client)
    first = _make_user(client, 60, "first.person")
    second = _make_user(client, 61, "second.person")
    _set_email(client, first, "first@example.com")
    _set_email(client, second, "second@example.com")

    _edit(client, second, "First@Example.com")  # same mailbox, different case

    assert _user(client, second)["email"] == "second@example.com"


def test_edit_allows_keeping_your_own_email(client):
    _make_admin(client)
    uid = _make_user(client, 62, "keeps.email")
    _set_email(client, uid, "keeps@example.com")

    _edit(client, uid, "keeps@example.com")

    assert _user(client, uid)["full_name"] == "Edited User"


def test_add_refuses_an_email_already_in_use(client):
    _make_admin(client)
    uid = _make_user(client, 63, "existing.person")
    _set_email(client, uid, "taken@example.com")

    client.post(
        "/admin/users/add",
        data={"username": "new.person", "full_name": "New Person", "email": "taken@example.com",
              "password": "TempPass456", "role": "staff", "department": "General"},
        follow_redirects=True,
    )

    with client.application.app_context():
        assert get_db().execute("SELECT 1 FROM users WHERE username='new.person'").fetchone() is None


def test_production_refuses_to_start_with_the_sign_in_code_switched_off(tmp_path):
    with pytest.raises(RuntimeError, match="OTP_ENABLED"):
        create_app({
            "TESTING": True,
            "ENVIRONMENT": "production",
            "OTP_ENABLED": False,
            "SECRET_KEY": "prod-secret-not-default",
            "ENCRYPTION_KEY": "kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8=",
            "RATELIMIT_STORAGE_URL": "redis://localhost:6379/0",
            "DATABASE_PATH": str(tmp_path / "t.db"),
            "PLATFORM_DATABASE_PATH": str(tmp_path / "p.db"),
            "TENANTS_DIR": str(tmp_path / "tenants"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        })


def test_nobody_can_choose_the_default_password_as_their_own(client):
    from werkzeug.security import generate_password_hash
    from onecpase.admin import DEFAULT_STAFF_PASSWORD

    uid = _make_user(client, 64, "changes.password")
    with client.application.app_context():
        db = get_db()
        db.execute("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
                   (generate_password_hash("Something-Else-1"), uid))
        db.commit()
    with client.session_transaction() as session:
        session["user_id"] = uid
        session["role"] = "staff"

    for attempt in (DEFAULT_STAFF_PASSWORD, DEFAULT_STAFF_PASSWORD.lower()):
        client.post("/change-password", data={
            "current_password": "Something-Else-1",
            "new_password": attempt, "confirm_password": attempt,
        }, follow_redirects=True)
        row = _user(client, uid)
        assert row["must_change_password"] == 1
        assert not __import__("werkzeug.security", fromlist=["x"]).check_password_hash(row["password_hash"], attempt)
