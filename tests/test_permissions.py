"""Department default permissions. Reception had none at all until this -
they could not open a member's or prospect's page, let alone use the "Call"
button on it, and permission_required just redirects rather than erroring,
so that looked exactly like the button silently doing nothing.
"""
from onecpase.database import get_db
from onecpase.permissions import default_permissions_for
import pytest
from onecpase.permissions import effective_permissions_for


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


@pytest.mark.parametrize('role,department,allowed,denied', [
    ('staff', 'Sales', {'view_leads', 'all_members', 'debicheck_mandates', 'hr_staff'}, {'all_collections', 'fitness_module', 'hr_payroll', 'user_management'}),
    ('reception', 'Reception', {'capture_leads', 'all_collections', 'collections_call_queue', 'hr_staff'}, {'fitness_module', 'hr_payroll', 'user_management'}),
    ('trainer', 'Training', {'all_members', 'fitness_module', 'hr_staff'}, {'view_leads', 'debicheck_mandates', 'communications_hub', 'hr_payroll', 'user_management'}),
])
def test_staff_profiles_override_old_grants(role, department, allowed, denied):
    permissions = effective_permissions_for(role, department, denied)
    assert allowed <= permissions
    assert not denied & permissions


@pytest.mark.parametrize('role,department,landing', [
    ('staff', 'Sales', '/leads/'),
    ('reception', 'Reception', '/leads/'),
    ('trainer', 'Training', '/members'),
])
def test_staff_access_refreshes_existing_sessions(client, role, department, landing):
    _login(client, role, department)
    with client.session_transaction() as session:
        session['permissions'] = ['hr_payroll', 'user_management', 'all_collections']
    response = client.get('/dashboard')
    assert response.status_code == 302
    assert landing in response.headers['Location']
    assert client.get('/operations/').status_code == 403
    assert client.get('/admin/users').status_code == 403
    response = client.get('/hr/payroll')
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert 'hr_payroll' not in session['permissions']


@pytest.mark.parametrize('role,department', [('staff', 'Sales'), ('reception', 'Reception'), ('trainer', 'Training')])
def test_hr_does_not_expose_payroll_or_allow_account_edits(client, role, department):
    _login(client, role, department)
    with client.application.app_context():
        db = get_db()
        sid = db.execute("INSERT INTO staff (first_name, last_name, salary) VALUES ('Test', 'Employee', 98765)").lastrowid
        db.execute("INSERT INTO payroll (staff_id, month, basic, net) VALUES (?, '2026-10', 98765, 98765)", (sid,))
        db.commit()
    detail = client.get(f'/hr/employees/{sid}')
    assert detail.status_code == 200
    assert b'All Payroll' not in detail.data
    assert b'98765' not in detail.data
    form = client.get(f'/hr/employees/{sid}/edit')
    assert b'name="salary"' not in form.data
    client.post(f'/hr/employees/{sid}/edit', data={'first_name': 'Test', 'last_name': 'Employee', 'salary': '1'})
    with client.application.app_context():
        assert get_db().execute('SELECT salary FROM staff WHERE id=?', (sid,)).fetchone()['salary'] == 98765
    assert client.post('/hr/1/edit', data={'role': 'admin'}).status_code == 302


def test_fitness_navigation_only_shows_its_sections(client):
    _login(client, 'trainer', 'Training')
    response = client.get('/members')
    assert response.status_code == 200
    for section in ('sales', 'communication', 'finance', 'connections', 'operations', 'analytics'):
        assert f'data-section="{section}"'.encode() not in response.data
    for section in ('members', 'fitness', 'hr'):
        assert f'data-section="{section}"'.encode() in response.data
    assert client.get('/internal/messages').status_code == 403


@pytest.mark.parametrize('role,department,collections_access', [('staff', 'Sales', False), ('reception', 'Reception', True), ('trainer', 'Training', False)])
def test_member_collection_controls_match_access(client, role, department, collections_access):
    _login(client, role, department)
    with client.application.app_context():
        db = get_db()
        mid = db.execute("INSERT INTO members (first_name, last_name, id_number, contact) VALUES ('Test', 'Member', '9001015009087', '0821234567')").lastrowid
        db.commit()
    response = client.get(f'/members/{mid}')
    assert response.status_code == 200
    assert (b'data-panel="ptp"' in response.data) == collections_access
    assert (b'Record Payment</a>' in response.data) == collections_access
