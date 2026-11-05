from datetime import date
import json
import pytest

from onecpase.staff_attendance import build_week,shift_window


@pytest.fixture
def hr_admin(app):
    from onecpase.database import get_db
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES (1,'testuser','x','Test HR Admin','admin',1)")
        db.commit()


def snapshot():
    return {'tenant_slug':'elev8','week_start':'2026-10-12','source_latest_date':'2026-10-15',
        'captured_at':'2026-10-16T14:00:00+02:00','source':'Test access source',
        'source_rows_by_date':{'2026-10-12':5,'2026-10-13':2,'2026-10-14':2,'2026-10-15':2},
        'roster':[{'id':2,'username':'sales','full_name':'Test Sales','department':'Sales','employee_number':'1003'}],
        'matched_references':{'1234':2},'shift_assignments':{},'public_holidays':[],'events':[],'quality':{}}


def event(at,allowed=True):
    return {'user_id':2,'occurred_at':'2026-10-12T'+at,'allowed':allowed,'movement':'Enter','zone':'Main'}


def test_sales_early_training_keeps_actual_scan_but_starts_policy_at_nine():
    data=snapshot();data['events']=[event('07:00:00')]
    day=build_week(data,'2026-10-12')['employees'][0]['days'][0]
    assert day['first_entry']=='07:00:00'
    assert day['policy_start']=='09:00:00'
    assert day['pre_shift_entry']
    assert day['clock_out'] is None and day['verified_hours'] is None


def test_multiple_entries_never_infer_an_exit_or_lunch():
    data=snapshot();data['events']=[event('09:00:00'),event('13:00:00'),event('14:00:00'),event('18:00:00')]
    day=build_week(data,'2026-10-12')['employees'][0]['days'][0]
    assert day['successful_scans']==4 and day['scan_groups']==4
    assert day['last_entry']=='18:00:00'
    assert all(day[key] is None for key in ('clock_out','lunch_out','lunch_in','verified_hours'))


def test_denied_and_repeated_scans_are_distinguished_from_presence():
    data=snapshot();data['events']=[event('09:00:00'),event('09:00:15'),event('09:05:00'),event('10:00:00',False)]
    data['events'].append(dict(data['events'][0]))
    report=build_week(data,'2026-10-12');day=report['employees'][0]['days'][0]
    assert day['successful_scans']==3 and day['scan_groups']==2
    assert day['denied_scans']==1 and report['duplicates_removed']==1


def test_missing_fresh_data_is_not_absence_and_sunday_is_closed():
    report=build_week(snapshot(),'2026-10-12')
    days=report['employees'][0]['days']
    assert days[4]['status']=='Awaiting source data'
    assert days[6]['status']=='Closed'
    assert all('absent' not in day['status'].lower() for day in days)


def test_another_week_does_not_reuse_events_or_shift_assignments():
    data=snapshot();data['events']=[event('09:00:00')]
    report=build_week(data,'2026-10-19')
    assert not report['available'] and report['totals']['successful_scans']==0
    assert report['employees'][0]['days'][0]['status']=='No snapshot for this week'


def test_shift_templates_and_closed_sunday():
    assert shift_window(date(2026,10,12),'Reception','early')['start']=='05:00'
    assert shift_window(date(2026,10,12),'Reception','late')['end']=='21:00'
    assert shift_window(date(2026,10,16),'Reception','late')['end']=='20:00'
    assert shift_window(date(2026,10,17),'Reception','late')['start']=='06:00'
    assert shift_window(date(2026,10,18),'Reception','late')['start'] is None
    assert shift_window(date(2026,10,12),'Cleaner')['start'] is None
    assert shift_window(date(2026,10,12),'Group Instructor')['start'] is None
    assert shift_window(date(2026,10,17),'Sales')['start'] is None
    assert shift_window(date(2026,10,12),'Reception','late',['2026-10-12'])['end']=='14:00'
    for day in [date(2026,10,12),date(2026,10,16),date(2026,10,17),date(2026,10,18)]:
        for assignment in (None,'early','late'):
            assert shift_window(day,'Cleaner',assignment)==shift_window(day,'Reception',assignment)
    assert shift_window(date(2026,10,12),'Cleaner',None,['2026-10-12'])['start']=='06:00'


def test_preview_requires_sign_in(client):
    assert client.get('/hr/access-attendance').status_code==302


def test_preview_uses_snapshot_and_never_writes_timesheets(app,client,tmp_path,hr_admin):
    from onecpase.database import get_db
    path=tmp_path/'access.json';data=snapshot();data['events']=[event('07:00:00')]
    data['quality']['unlinked_source_staff']=[{'name':'Unlinked Staff','role':'Reception','scans':2}]
    path.write_text(json.dumps(data));app.config['STAFF_ACCESS_SNAPSHOT']=str(path)
    with client.session_transaction() as sess:
        sess.update(user_id=1,username='testuser',role='admin')
    response=client.get('/hr/access-attendance?week=2026-10-12')
    assert response.status_code==200
    assert b'07:00:00' in response.data and b'09:00:00' in response.data
    assert b'Last entry is not a sign-out' in response.data
    assert b'Awaiting source data' in response.data
    assert b'Unlinked Staff' in response.data and b'identity is confirmed' in response.data
    with app.app_context():assert get_db().execute('SELECT COUNT(*) FROM timesheets').fetchone()[0]==0


def test_snapshot_cannot_be_viewed_in_a_different_tenant(app,client,tmp_path,hr_admin):
    from onecpase.platform_db import get_platform_db
    path=tmp_path/'access.json';path.write_text(json.dumps(snapshot()))
    app.config['STAFF_ACCESS_SNAPSHOT']=str(path)
    with app.app_context():
        registry=get_platform_db()
        registry.execute("INSERT INTO tenants(slug,name,db_path,active) VALUES('another-gym','Another Gym',?,1)",(app.config['DATABASE_PATH'],))
        registry.commit()
    client.set_cookie('onecpase_tenant','another-gym')
    with client.session_transaction() as sess:sess.update(user_id=1,username='testuser',role='admin',tenant_slug='another-gym')
    assert client.get('/hr/access-attendance').status_code==404


def test_missing_snapshot_has_a_useful_empty_state(client,hr_admin):
    with client.session_transaction() as sess:sess.update(user_id=1,username='testuser',role='admin')
    response=client.get('/hr/access-attendance')
    assert response.status_code==200
    assert b'No usable staff access import' in response.data
