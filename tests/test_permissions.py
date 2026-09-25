"""Department default permissions. Reception had none at all until this -
they could not open a member's or prospect's page, let alone use the "Call"
button on it, and permission_required just redirects rather than erroring,
so that looked exactly like the button silently doing nothing.
"""
from onecpase.database import get_db
from onecpase.permissions import default_permissions_for


def test_reception_can_view_members_leads_and_place_calls():
    perms = default_permissions_for("reception", "Reception")
    assert "all_members" in perms
    assert "view_leads" in perms
    assert "pbx_call" in perms


def _login(client, role, department):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = role
        session["permissions"] = list(default_permissions_for(role, department))
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, department, active) "
            "VALUES (1, 'busi', 'x', 'Colin Busi Mabena', ?, ?, 1)",
            (role, department),
        )
        db.commit()


def test_reception_can_open_a_member_page_and_see_the_call_button(client):
    _login(client, "reception", "Reception")
    with client.application.app_context():
        db = get_db()
        mid = db.execute(
            "INSERT INTO members (first_name, last_name, id_number, contact) "
            "VALUES ('Test', 'Member', '9001015009087', '0821234567')"
        ).lastrowid
        db.commit()

    response = client.get(f"/members/{mid}")
    assert response.status_code == 200
    assert b"startPbxCall(" in response.data
    assert b'name="csrf_token"' in response.data


def test_reception_can_open_a_lead_page(client):
    _login(client, "reception", "Reception")
    with client.application.app_context():
        db = get_db()
        lid = db.execute(
            "INSERT INTO leads (full_name, phone, lead_status) VALUES ('Walk-In Prospect', '0821234567', 'captured')"
        ).lastrowid
        db.commit()

    response = client.get(f"/leads/{lid}")
    assert response.status_code == 200
