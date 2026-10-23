"""Module Permissions on the Edit User form must grant what is ticked.

The ticks once posted the whole ("key", "Label") pair, which the save discarded
as unknown - so every save silently removed the user's individual access.
"""

from onecpase.database import get_db
from test_staff_passwords import _make_admin, _make_user


def _perms(client, uid):
    with client.application.app_context():
        return {row["permission"] for row in get_db().execute(
            "SELECT permission FROM user_permissions WHERE user_id=?", (uid,)
        )}


def test_ticked_permissions_are_saved_and_shown_checked(client):
    _make_admin(client)
    uid = _make_user(client, 70, "perm.person")

    client.post(
        f"/admin/users/{uid}/edit",
        data={"full_name": "Perm Person", "email": "perm@example.com", "contact": "",
              "role": "staff", "department": "General", "active": "on",
              "permissions": ["reports_module", "user_management"]},
        follow_redirects=True,
    )

    assert _perms(client, uid) == {"reports_module", "user_management"}
    page = client.get(f"/admin/users/{uid}/edit").data.decode()
    assert 'value="reports_module"' in page
    assert "Reports Module" in page
    assert "(&#39;reports_module&#39;" not in page
    checked = page.split('value="reports_module"', 1)[1].split(">", 1)[0]
    assert "checked" in checked
