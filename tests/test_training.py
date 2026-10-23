import pytest

from onecpase.database import get_db


@pytest.mark.parametrize('path', ['/training', '/training/topics/sales', '/training/manual/pdf', '/training/manual/docx'])
def test_training_requires_login(client, path):
    assert client.get(path).status_code == 302


@pytest.fixture
def staff(app, client):
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department) VALUES (1,'training','x','Trainee','staff','Sales')")
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=1, role='staff', full_name='Trainee', permissions=[])
    return client


@pytest.mark.parametrize('role,department', [('staff', 'Sales'), ('reception', 'Reception'), ('staff', 'Fitness'), ('manager', 'Management'), ('admin', 'Management')])
def test_staff_training_available_without_module_permissions_and_at_phase_one(app, staff, role, department):
    app.config['ACTIVE_PHASE'] = 1
    with app.app_context():
        db = get_db()
        db.execute('UPDATE users SET role=?,department=? WHERE id=1', (role, department))
        db.commit()
    response = staff.get('/training')
    assert response.status_code == 200
    assert b'Staff Training' in response.data
    for slug in ['sales', 'collections', 'ptp', 'debicheck', 'queries', 'notifications', 'practice']:
        topic = staff.get(f'/training/topics/{slug}')
        assert topic.status_code == 200
        assert b'training-reader' in topic.data


@pytest.mark.parametrize('format,magic', [('pdf', b'%PDF'), ('docx', b'PK')])
def test_training_manual_files_and_download_headers(staff, format, magic):
    response = staff.get(f'/training/manual/{format}')
    assert response.status_code == 200
    assert response.data.startswith(magic)
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert ('attachment' if format == 'docx' else 'inline') in response.headers['Content-Disposition']
    assert 'attachment' in staff.get(f'/training/manual/{format}?download=1').headers['Content-Disposition']


def test_training_unknown_resources_and_stale_tenant(staff):
    assert staff.get('/training/topics/missing').status_code == 404
    assert staff.get('/training/manual/html').status_code == 404
    with staff.session_transaction() as session:
        session['tenant_slug'] = 'other-gym'
    assert staff.get('/training/manual/pdf').status_code == 302


def test_ptp_example_and_query_status_table_are_readable(staff):
    assert b'<table>' in staff.get('/training/topics/ptp').data
    assert b'Pending Approval' in staff.get('/training/topics/queries').data
