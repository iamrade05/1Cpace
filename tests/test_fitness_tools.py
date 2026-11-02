from datetime import date
from bs4 import BeautifulSoup
import pytest
from onecpase.fitness_cycle import cycle_bounds, cycle_key, joining_window
from onecpase.fitness_anatomy import GROUPS
from onecpase.database import get_db


def test_full_cycles_and_confirmed_week():
    assert cycle_bounds('2026-10') == (date(2026,9,15),date(2026,10,14))
    assert cycle_bounds('2027-01') == (date(2026,12,15),date(2027,1,14))
    assert cycle_key(date(2026,10,14)) == '2026-10'
    assert cycle_key(date(2026,10,15)) == '2026-11'
    assert joining_window({},date(2026,10,11))[3:] == (date(2026,10,5),date(2026,10,11))
    with pytest.raises(ValueError):
        joining_window({'cycle':'2026-10','from':'2026-09-14','to':'2026-10-11'})


def sign_in(app,client):
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(1,'trainer','x','Trainer','admin',1)")
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=1,role='admin',permissions=['fitness_module'])


def test_pages_and_region_selection(app,client):
    sign_in(app,client)
    response=client.get('/fitness/onboarding/?cycle=2026-10&from=2026-10-05&to=2026-10-11')
    assert response.status_code == 200
    assert b'15 September' in response.data and b'14 October 2026' in response.data
    assert client.get('/fitness/onboarding/?cycle=2026-10&from=2026-09-14').status_code == 400
    assert len(GROUPS) == 14
    for group in GROUPS:
        response=client.get('/fitness/anatomy?area='+group['id']+'&view=back')
        assert response.status_code == 200
        assert group['exercise'].encode() in response.data
    assert client.get('/fitness/anatomy?area=unknown').status_code == 400
    assert client.get('/fitness/anatomy?member_id=999999').status_code == 404


def test_fitness_tools_require_login(client):
    assert client.get('/fitness/anatomy').status_code == 302
    assert client.get('/fitness/onboarding/joiners').status_code == 302


def test_joining_window_is_inclusive_and_full_cycle_retained(app,client):
    sign_in(app,client)
    dates=['2026-09-14','2026-09-15','2026-10-04','2026-10-05','2026-10-11','2026-10-12','2026-10-14','2026-10-15']
    with app.app_context():
        db=get_db()
        for number,day in enumerate(dates):
            db.execute("INSERT INTO members(first_name,last_name,join_date,id_number,contact) VALUES(?,?,?,?,?)",('Boundary'+str(number),'Member',day,'test-'+str(number),'0712345678'))
        db.commit()
    response=client.get('/fitness/onboarding/?cycle=2026-10&from=2026-10-05&to=2026-10-11')
    # The manual arrival picker intentionally lists other members too. Check
    # the joining-cohort table when verifying the selected date window.
    cohort=BeautifulSoup(response.data,'html.parser').select_one('.fit-table').get_text(' ',strip=True)
    assert 'Boundary3' in cohort and 'Boundary4' in cohort
    for number in [0,1,2,5,6,7]:
        assert 'Boundary'+str(number) not in cohort
    assert b'<strong>6</strong> joined in the full cycle' in response.data
    response=client.get('/fitness/onboarding/?cycle=2026-10&from=2026-09-15&to=2026-10-14')
    cohort=BeautifulSoup(response.data,'html.parser').select_one('.fit-table').get_text(' ',strip=True)
    assert 'Boundary1' in cohort and 'Boundary6' in cohort
    assert 'Boundary0' not in cohort and 'Boundary7' not in cohort


def test_new_members_appear_without_arrival_and_no_duplicate_navigation(app,client):
    from onecpase.fitness_cycle import today
    sign_in(app,client)
    with app.app_context():
        db=get_db()
        mid=db.execute("INSERT INTO members(first_name,last_name,join_date,id_number,contact,outcome) VALUES('NewAutomatic','Member',?,'new-1','0712345678','joined')",(today().isoformat(),)).lastrowid
        db.commit()
    response=client.get('/fitness/onboarding/')
    assert response.status_code == 200
    assert b'NewAutomatic' in response.data
    assert b'value="'+str(mid).encode()+b'"' in response.data
    assert b'href="/fitness/onboarding/joiners"' not in response.data
    with app.app_context():
        assert get_db().execute('SELECT count(*) FROM member_onboarding WHERE member_id=?',(mid,)).fetchone()[0] == 1


def test_joining_details_and_intake_are_one_register_with_fitness_links(app, client):
    from onecpase.fitness_cycle import today
    sign_in(app, client)
    with app.app_context():
        db = get_db()
        mid = db.execute("INSERT INTO members(first_name,last_name,id_number,contact,join_date,package,outcome) VALUES('Combined','Client','combined-intake','0712345678',?,'Premium fitness','joined')", (today().isoformat(),)).lastrowid
        db.commit()
    assert client.get('/fitness/onboarding/').status_code == 200
    with app.app_context():
        db = get_db()
        db.execute("UPDATE fitness_intake SET interest='interested',preferred_times='Tuesday 17:00' WHERE member_id=?", (mid,))
        db.commit()
    page = BeautifulSoup(client.get('/fitness/onboarding/').data, 'html.parser')
    assert len(page.select('#fitness-intake-register')) == 1
    register = page.select_one('#fitness-intake-register')
    rows = register.select('tbody tr')
    assert len(rows) == 1
    text = rows[0].get_text(' ', strip=True)
    assert all(value in text for value in ('Combined Client', today().isoformat(), 'Premium fitness', 'interested', 'Tuesday 17:00'))
    for link in rows[0].select('a'):
        assert link['href'] == f'/fitness/intake/{mid}'
        assert client.get(link['href']).status_code == 200
    assert page.select_one('#fitness-arrivals-title') is not None
    client.get('/fitness/onboarding/')
    with app.app_context():
        assert get_db().execute('SELECT count(*) FROM member_onboarding WHERE member_id=?',(mid,)).fetchone()[0] == 1
