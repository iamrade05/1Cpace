from bs4 import BeautifulSoup

from onecpase.database import get_db
from tests.test_fitness_tools import sign_in


def test_boarded_members_require_completed_review(app, client):
    sign_in(app, client)
    with app.app_context():
        db = get_db()
        for name, status, reviewed, outcome in [
            ('Reviewed', 'complete', 1, 'independent'),
            ('SupportRequired', 'complete', 1, 'follow_up'),
            ('ExpiredOnly', 'complete', 0, None),
            ('StillActive', 'active', 1, 'independent'),
        ]:
            mid = db.execute("INSERT INTO members(first_name,last_name,id_number,contact) VALUES(?,'Member',?,'0712345678')", (name, name)).lastrowid
            db.execute("INSERT INTO member_onboarding(member_id,status,review_done,review_outcome,review_date) VALUES(?,?,?,?,?)", (mid, status, reviewed, outcome, '2026-10-25'))
        db.commit()
    response = client.get('/fitness/onboarding/boarded')
    assert response.status_code == 200
    table = BeautifulSoup(response.data, 'html.parser').select_one('.table').get_text(' ', strip=True)
    assert 'Reviewed' in table and 'SupportRequired' in table
    assert 'Requires follow-up' in table
    assert 'ExpiredOnly' not in table and 'StillActive' not in table
    with app.app_context():
        assert get_db().execute('SELECT count(*) FROM member_onboarding').fetchone()[0] == 4


def test_boarded_members_require_fitness_access(app, client):
    assert client.get('/fitness/onboarding/boarded').status_code == 302
    from tests.test_permissions import _login
    _login(client, 'staff', 'Sales')
    assert client.get('/fitness/onboarding/boarded').status_code == 403


def test_fitness_menu_links_for_instructor(client):
    from tests.test_permissions import _login
    _login(client, 'trainer', 'Training')
    response = client.get('/fitness-dashboard')
    assert response.status_code == 200
    menu = BeautifulSoup(response.data, 'html.parser').select_one('[data-section-panel="fitness"]')
    links = menu.select('a')
    assert [a.get_text(strip=True) for a in links] == ['Dashboard', 'Onboarding', 'Boarded Members', 'Incident Logs', 'Supplements']
    for link in links:
        assert client.get(link['href']).status_code == 200
