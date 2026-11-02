import pytest
from onecpase.database import get_db


def login(app,client,role,department=''):
    with app.app_context():
        db=get_db()
        uid=db.execute('INSERT INTO users(username,password_hash,full_name,role,department,active) VALUES(?,?,?,?,?,1)',
                       ('dash-'+role+department,'x','Dashboard user',role,department)).lastrowid
        db.commit()
    with client.session_transaction() as state:
        state.update(user_id=uid,role=role,department=department,permissions=['dashboard','all_collections'],tenant_slug=None)
    return uid


@pytest.mark.parametrize('role,department,home',[
    ('manager','Sales','/sales-dashboard'),('staff','Sales','/sales-dashboard'),
    ('reception','Reception','/reception-dashboard'),('trainer','Training','/fitness-dashboard'),
    ('manager','Management','/my-dashboard')])
def test_department_home_and_cross_department_boundaries(app,client,role,department,home):
    login(app,client,role,department)
    response=client.get('/dashboard')
    assert response.status_code==302 and response.headers['Location'].endswith(home)
    page=client.get(home)
    assert page.status_code==200
    assert b'href="/dashboard"' not in page.data
    for other in ('/sales-dashboard','/reception-dashboard','/fitness-dashboard','/my-dashboard'):
        if other!=home:assert client.get(other).status_code==403
    assert client.get('/dashboard/reconciliation').status_code==403
    assert client.post('/dashboard/reconciliation/sync').status_code==403
    assert client.get('/reports/overview').status_code in (302,403)
    assert client.post('/reports/run-custom').status_code in (302,403)
    if home!='/reception-dashboard':
        assert client.get('/collections/dashboard').status_code in (302,403)
        assert client.get('/ptp/').status_code in (302,403)


def test_database_role_wins_over_stale_admin_session_and_dashboard_grant(app,client):
    login(app,client,'manager','Sales')
    with client.session_transaction() as state:state.update(role='admin',permissions=['dashboard','all_collections'])
    assert client.get('/dashboard').headers['Location'].endswith('/sales-dashboard')
    assert client.get('/dashboard/reconciliation').status_code==403


def test_admin_can_inspect_each_department_and_main_dashboard(app,client,monkeypatch):
    login(app,client,'admin','Management')
    monkeypatch.setattr('onecpase.dashboard.get_elev8_member_count',lambda:None)
    for path in ('/dashboard','/sales-dashboard','/reception-dashboard','/fitness-dashboard','/my-dashboard'):
        assert client.get(path).status_code==200


def test_sales_filters_and_report_do_not_include_other_department_records(app,client):
    own=login(app,client,'manager','Sales')
    with app.app_context():
        db=get_db()
        other=db.execute("INSERT INTO users(username,password_hash,full_name,role,department) VALUES('other-dept','x','Other','reception','Reception')").lastrowid
        db.execute("INSERT INTO leads(full_name,assigned_to,created_at) VALUES('Sales department prospect',?,'2026-10-01')",(own,))
        db.execute("INSERT INTO leads(full_name,assigned_to,created_at) VALUES('RECEPTION_ONLY_SECRET',?,'2026-10-01')",(other,))
        db.commit()
    page=client.get('/sales-dashboard?from=2026-10-01&to=2026-10-25')
    assert page.status_code==200 and b'RECEPTION_ONLY_SECRET' not in page.data
    assert client.get(f'/sales-dashboard?consultant={other}').status_code==403


def test_reception_balance_matches_payments_and_team_activity_excludes_sales(app,client,monkeypatch):
    own=login(app,client,'reception','Reception')
    with app.app_context():
        db=get_db()
        other=db.execute("INSERT INTO users(username,password_hash,full_name,role,department) VALUES('sales-other','x','Sales','manager','Sales')").lastrowid
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number,join_date,source) VALUES('Test','Member','reception-dash-id','2026-10-01','Referral')").lastrowid
        db.execute("INSERT INTO collections(member_id,outstanding_balance,amount_paid,collection_date,status,notes) VALUES(?,100,0,'2026-10-01','pending','type=Recurring Fee')",(mid,))
        db.execute("INSERT INTO collections(member_id,outstanding_balance,amount_paid,collection_date,status,notes) VALUES(?,0,40,'2026-10-02','paid','type=Cash Payment')",(mid,))
        for uid in (own,other):
            db.execute("INSERT INTO queries(logged_by,category,description,created_at) VALUES(?,'complaint','Private','2026-10-02')",(uid,))
            db.execute("INSERT INTO pbx_call_logs(user_id,direction,start_time,talk_duration) VALUES(?,'outbound','2026-10-02 09:00:00',15)",(uid,))
        db.commit()
    captured={}
    def render(name,**context):captured.update(context);return 'ok'
    monkeypatch.setattr('onecpase.dashboard.render_template',render)
    assert client.get('/reception-dashboard?from=2026-10-01&to=2026-10-25').status_code==200
    cards={c['label']:c['value'] for c in captured['cards']}
    assert cards['Outstanding amount']==60 and cards['Collections received']==40
    assert cards['Collection coverage rate']==40
    assert cards['Queries logged']==1 and cards['PBX calls made']==1


def test_fitness_dashboard_has_no_financial_or_sales_cards(app,client):
    login(app,client,'trainer','Fitness')
    page=client.get('/fitness-dashboard')
    assert page.status_code==200
    for phrase in (b'Collections received',b'Outstanding amount',b'Sales Funnel',b'href="/sales-dashboard"',b'href="/collections/dashboard"'):
        assert phrase not in page.data
    assert b'Assisted sessions' in page.data


def test_unclassified_staff_only_see_their_own_work(app,client,monkeypatch):
    own=login(app,client,'staff','Operations')
    with app.app_context():
        db=get_db()
        other=db.execute("INSERT INTO users(username,password_hash,full_name) VALUES('other-worker','x','Other worker')").lastrowid
        for uid in (own,other):db.execute("INSERT INTO queries(logged_by,assigned_to,category,description) VALUES(?,?,'complaint','Private')",(uid,uid))
        db.commit()
    captured={}
    def render(name,**context):captured.update(context);return 'ok'
    monkeypatch.setattr('onecpase.dashboard.render_template',render)
    assert client.get('/my-dashboard').status_code==200
    assert {c['label']:c['value'] for c in captured['cards']}=={'My open queries':1,'Queries I logged':1}
