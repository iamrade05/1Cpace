"""Default staff password, forced change, and the protected account.

The default is only safe because it cannot outlive one sign-in: every path
that sets it also sets must_change_password, and login_required refuses to
serve anything else until the person has chosen their own. These tests hold
that pairing together — a reset that forgot the flag would leave a known
password live on the account indefinitely.
"""

from werkzeug.security import check_password_hash

from onecpase.admin import DEFAULT_STAFF_PASSWORD, PROTECTED_USERNAMES, is_protected_user
from onecpase.database import get_db


def _make_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"
    with client.application.app_context():
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO users
               (id, username, password_hash, full_name, role, active)
               VALUES (1, 'admin', 'unused', 'Test Admin', 'admin', 1)"""
        )
        db.commit()


def _make_user(client, uid, username, role="staff"):
    with client.application.app_context():
        db = get_db()
        db.execute(
            """INSERT OR REPLACE INTO users
               (id, username, password_hash, full_name, role, active, must_change_password)
               VALUES (?, ?, 'original-hash', ?, ?, 1, 0)""",
            (uid, username, username.replace(".", " ").title(), role),
        )
        db.commit()
    return uid


def _user(client, uid):
    with client.application.app_context():
        return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def test_reset_sets_the_default_and_forces_a_change(client):
    _make_admin(client)
    uid = _make_user(client, 50, "test.consultant")

    response = client.post(f"/admin/users/{uid}/reset-password", follow_redirects=True)
    assert response.status_code == 200

    row = _user(client, uid)
    assert check_password_hash(row["password_hash"], DEFAULT_STAFF_PASSWORD)
    assert row["must_change_password"] == 1, (
        "a reset without the forced change would leave a known password live"
    )


def test_protected_account_is_never_reset_to_the_default(client):
    _make_admin(client)
    protected = sorted(PROTECTED_USERNAMES)[0]
    uid = _make_user(client, 51, protected, role="admin")

    client.post(f"/admin/users/{uid}/reset-password", follow_redirects=True)

    row = _user(client, uid)
    assert row["password_hash"] == "original-hash"
    assert row["must_change_password"] == 0


def test_protection_is_case_insensitive():
    protected = sorted(PROTECTED_USERNAMES)[0]
    assert is_protected_user(protected)
    assert is_protected_user(protected.upper())
    assert is_protected_user(f"  {protected}  ")
    assert not is_protected_user("someone.else")
    assert not is_protected_user("")
    assert not is_protected_user(None)


def test_admin_cannot_reset_their_own_account(client):
    """Self-service goes through Change Password, which verifies the old one."""
    _make_admin(client)
    before = _user(client, 1)["password_hash"]

    client.post("/admin/users/1/reset-password", follow_redirects=True)

    assert _user(client, 1)["password_hash"] == before


def test_admin_set_password_also_forces_a_change(client):
    """An admin typing a password for someone knows it — so it cannot stand."""
    _make_admin(client)
    uid = _make_user(client, 52, "edited.user")

    client.post(
        f"/admin/users/{uid}/edit",
        data={
            "full_name": "Edited User", "email": "edited.user@example.com", "contact": "",
            "role": "staff", "department": "General", "active": "on",
            "new_password": "TempPass456",
        },
        follow_redirects=True,
    )

    row = _user(client, uid)
    assert check_password_hash(row["password_hash"], "TempPass456")
    assert row["must_change_password"] == 1
