import pytest

from onecpase.database import get_db
from onecpase.phases import is_enabled, nav_section_enabled


def login(app, client, email, role='staff'):
    with app.app_context():
        db = get_db()
        db.execute('INSERT INTO users(id,username,password_hash,full_name,role,email,active) '
                   'VALUES (1,?,?,?,?,?,1)', ('employee', 'x', 'Employee', role, email))
        sid = db.execute("INSERT INTO staff(first_name,last_name,salary) VALUES ('Private','Employee',98765)").lastrowid
        pid = db.execute("INSERT INTO payroll(staff_id,month,basic,net,status) VALUES (?,'2026-10',98765,98765,'pending')", (sid,)).lastrowid
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=1, role=role, permissions=['hr_payroll'])
    return sid, pid


@pytest.mark.parametrize('role', ['staff', 'manager', 'admin', 'trainer', 'receptionist'])
def test_hr_open_but_payroll_and_salary_denied_for_other_users(app, client, role):
    app.config['ACTIVE_PHASE'] = '4'
    sid, pid = login(app, client, 'someone@example.com', role)
    for path in ['/hr/', '/hr/leave', '/hr/me', '/hr/timesheets']:
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200
        assert b'href="/hr/payroll"' not in response.data
    assert client.get('/hr/payroll').status_code == 302
    assert client.post(f'/hr/payroll/{pid}/pay').status_code == 302
    assert client.get('/hr/payroll', headers={'Content-Type': 'application/json'}).status_code == 403
    for path in [f'/hr/employees/{sid}', f'/hr/employees/{sid}/edit']:
        response = client.get(path)
        assert response.status_code == (200 if role=='admin' else 403)
        assert b'98765' not in response.data
        assert b'name="salary"' not in response.data
    client.post(f'/hr/employees/{sid}/edit', data={'first_name': 'Private', 'last_name': 'Employee', 'salary': '1'})
    with app.app_context():
        db = get_db()
        assert db.execute('SELECT salary FROM staff WHERE id=?', (sid,)).fetchone()['salary'] == 98765
        assert db.execute('SELECT status FROM payroll WHERE id=?', (pid,)).fetchone()['status'] == 'pending'


@pytest.mark.parametrize('email', ['mvuleni@elev8healthandfitnessgym.com', 'ayrton@elev8healthandfitnessgym.com'])
def test_only_named_staff_can_view_and_process_payroll_without_permission_grant(app, client, email):
    app.config['ACTIVE_PHASE'] = '4'
    _, pid = login(app, client, email)
    with client.session_transaction() as session:
        session['permissions'] = []
    response = client.get('/hr/payroll?month=2026-10')
    assert response.status_code == 200
    assert b'98765' in response.data or b'98,765' in response.data
    assert client.post(f'/hr/payroll/{pid}/pay', data={'month': '2026-10'}).status_code == 302
    with app.app_context():
        assert get_db().execute('SELECT status FROM payroll WHERE id=?', (pid,)).fetchone()['status'] == 'paid'
        get_db().execute('UPDATE users SET active=0 WHERE id=1')
        get_db().commit()
    assert client.get('/hr/payroll').status_code != 200


def test_phase_four_opens_fitness_and_hr_but_later_modules_stay_closed():
    assert is_enabled('hr', 3) and nav_section_enabled('hr', 3)
    assert is_enabled('fitness', 4) and is_enabled('onboarding', 4)
    assert not is_enabled('reports', 4)
    assert not is_enabled('operations', 4)
