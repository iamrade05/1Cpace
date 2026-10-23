from datetime import date
import json

import pytest

from onecpase.database import get_db
from onecpase import hr_workforce as hr


@pytest.fixture
def workforce(app,client,monkeypatch):
    monkeypatch.setattr(hr,'today',lambda:date(2026,10,16))
    with app.app_context():
        db=get_db()
        for uid,name,role,dept in [(1,'Employee One','staff','Reception'),(2,'HR One','admin','Management'),
                                   (3,'HR Two','admin','Management'),(4,'Employee Two','staff','Reception'),
                                   (5,'Team Manager','manager','Reception')]:
            db.execute('INSERT INTO users(id,username,password_hash,full_name,role,department,email,active) VALUES (?,?,?,?,?,?,?,1)',
                       (uid,f'user{uid}','x',name,role,dept,f'user{uid}@example.com'))
            hr.ensure_profile(db,uid)
            db.execute("UPDATE hr_profiles SET start_date=?,weekdays=?,manager_id=?,employment_type='employee' WHERE user_id=?",('2025-01-01','[0, 1, 2, 3, 4]',5,uid))
        for permission in ('hr_leave_approve','hr_timesheet_approve'):
            db.execute('INSERT INTO user_permissions(user_id,permission) VALUES (5,?)',(permission,))
        db.commit()
    def login(uid):
        with client.session_transaction() as sess:
            sess.clear(); sess.update(user_id=uid,role='admin' if uid in (2,3) else 'staff',permissions=[])
    login(1)
    return login


def request_form(**extra):
    return dict(leave_type='annual',start_date='2026-10-19',end_date='2026-10-20',reason='Private detail',
                token='unique-request-token-123456',attest='yes',**extra)


def make_account(app,client,login,kind='annual',uid=1,balance='15',taken='2'):
    login(2)
    response=client.post('/hr/opening-balances',data=dict(user_id=uid,leave_type=kind,as_of='2026-10-16',balance=balance,taken=taken,source='Verified signed leave register'))
    assert response.status_code==302,response.data
    with app.app_context():
        oid=get_db().execute('SELECT MAX(id) FROM hr_openings').fetchone()[0]
    login(3)
    assert client.post(f'/hr/opening-balances/{oid}/confirm').status_code==302
    return oid


def test_employee_dashboard_unknown_balance_and_sensitive_routes(app,client,workforce):
    assert b'Opening balances awaiting confirmation' in client.get('/hr/me').data
    for path in ['/hr/people','/hr/employees','/hr/employees/2/edit','/hr/leave/calendar','/hr/weeks?user_id=4']:
        assert client.get(path).status_code==403,path
    assert client.post('/hr/timesheets/save',data={'staff_id':2}).status_code==409
    assert client.get('/hr/timesheets',follow_redirects=True).status_code==200


def test_profile_links_do_not_repurpose_external_reference(app,client,workforce):
    with app.app_context():
        db=get_db(); db.execute('UPDATE users SET staff_id=999,employee_number=? WHERE id=1',('1001',)); db.commit()
        hr.ensure_profile(db,1)
        assert db.execute('SELECT staff_id FROM users WHERE id=1').fetchone()[0]==999
        assert db.execute('SELECT COUNT(*) FROM hr_profiles WHERE user_id=1').fetchone()[0]==1


def test_opening_requires_second_person_and_is_idempotent(app,client,workforce):
    workforce(2)
    client.post('/hr/opening-balances',data=dict(user_id=1,leave_type='annual',as_of='2026-10-16',balance='13',taken='2',source='Confirmed history'))
    assert client.post('/hr/opening-balances/1/confirm').status_code==403
    workforce(3)
    assert client.post('/hr/opening-balances/1/confirm').status_code==302
    assert client.post('/hr/opening-balances/1/confirm').status_code==400
    workforce(1)
    response=client.get('/hr/me')
    assert response.status_code==200
    with app.app_context():
        db=get_db(); account=hr.current_accounts(db,1)[0]; result=hr.account_balance(db,account)
        assert result['available']==1300 and result['taken']==200
        assert db.execute('SELECT COUNT(*) FROM hr_ledger').fetchone()[0]==2


def test_date_calculation_holidays_and_five_six_day_patterns(app,client,workforce):
    with app.app_context():
        db=get_db()
        assert len(hr.work_dates(db,1,date(2026,11,9),date(2026,11,29)))==15
        db.execute('UPDATE hr_profiles SET weekdays=? WHERE user_id=1',('[0,1,2,3,4,5]',))
        assert len(hr.work_dates(db,1,date(2026,11,9),date(2026,11,29)))==18
        assert '2026-11-04' not in hr.work_dates(db,1,date(2026,11,2),date(2026,11,6))
        assert '2026-12-16' not in hr.work_dates(db,1,date(2026,12,14),date(2026,12,18))
        assert hr.cycle(date(2024,2,29),'annual',date(2025,2,28))[0]==date(2025,2,28)


def test_leave_server_calculation_self_approval_and_reservation(app,client,workforce):
    make_account(app,client,workforce)
    workforce(1)
    form=request_form(); form['days']='999'
    assert client.post('/hr/leave/request',data=form).status_code==302
    assert client.post('/hr/leave/1/approve',data={'version':'0','decision_note':'reviewed'}).status_code==403
    workforce(5)
    # Manager cannot override missing coverage, even with a note.
    client.post('/hr/leave/1/approve',data={'version':'0','decision_note':'No cover'})
    with app.app_context():
        assert get_db().execute('SELECT status,units FROM hr_requests').fetchone()['status']=='submitted'
        assert get_db().execute('SELECT units FROM hr_requests').fetchone()[0]==200
    workforce(2)
    assert client.post('/hr/leave/1/approve',data={'version':'0','decision_note':'Relief arranged by operations for both dates'}).status_code==302
    client.post('/hr/leave/1/approve',data={'version':'0','decision_note':'retry'})
    with app.app_context():
        db=get_db(); result=hr.account_balance(db,hr.current_accounts(db,1)[0])
        assert result['available']==1300 and result['reserved']==200
        assert db.execute("SELECT COUNT(*) FROM hr_audit WHERE action='leave_approve'").fetchone()[0]==1
    # Only future leave can be cancelled, by another HR administrator.
    assert client.post('/hr/leave/1/withdraw',data={'version':'1','note':'Employee cancelled trip'}).status_code==302
    with app.app_context():
        db=get_db(); assert hr.account_balance(db,hr.current_accounts(db,1)[0])['reserved']==0


def test_leave_privacy_and_calendar_omits_reason(app,client,workforce):
    assert client.post('/hr/leave/request',data=request_form()).status_code==302
    workforce(4)
    assert b'Private detail' not in client.get('/hr/leave').data
    assert client.post('/hr/leave/1/reject',data={'version':'0','decision_note':'no'}).status_code==403
    workforce(5)
    assert b'Private detail' in client.get('/hr/leave').data
    response=client.get('/hr/leave/calendar?week=2026-10-19')
    assert response.status_code==200 and b'Private detail' not in response.data
    assert b'Employee Two' in response.data


def test_duplicate_overlap_and_unconfirmed_terms(app,client,workforce):
    client.post('/hr/leave/request',data=request_form())
    client.post('/hr/leave/request',data=request_form())
    with app.app_context():
        db=get_db(); assert db.execute('SELECT COUNT(*) FROM hr_requests').fetchone()[0]==1
        db.execute("UPDATE hr_requests SET status='rejected' WHERE user_id=1"); db.commit()
    assert b'already submitted' in client.post('/hr/leave/request',data=request_form()).data
    with app.app_context():
        db=get_db(); assert db.execute('SELECT COUNT(*) FROM hr_requests').fetchone()[0]==1
        db.execute('UPDATE hr_profiles SET weekdays=NULL WHERE user_id=4'); db.commit()
    workforce(4)
    response=client.post('/hr/leave/request',data=request_form())
    assert b'confirm employment' in response.data


def week_form(action='save'):
    return {'week_start':'2026-10-05','version':'0','action':action,'attest':'yes',
            'time_in_2026-10-05':'09:00','lunch_out_2026-10-05':'13:00',
            'lunch_in_2026-10-05':'14:00','time_out_2026-10-05':'19:00'}


def test_weekly_signatures_lock_and_correction_history(app,client,workforce):
    assert client.post('/hr/weeks',data=week_form('submit')).status_code==302
    with app.app_context():
        db=get_db(); row=db.execute('SELECT * FROM hr_weeks').fetchone()
        assert row['status']=='submitted' and json.loads(row['entries'])[0]['minutes']==540
    assert client.post('/hr/weeks',data=week_form()).status_code==400
    assert client.post('/hr/weeks/1/decision',data={'version':'1','action':'approved','note':'checked'}).status_code==403
    workforce(5)
    assert client.post('/hr/weeks/1/decision',data={'version':'1','action':'approved','note':'Actual hours and paid time reviewed'}).status_code==302
    workforce(2)
    assert client.post('/hr/weeks/1/decision',data={'version':'1','action':'reopened','note':'Correction requested by employee'}).status_code==302
    workforce(1)
    form=week_form('submit'); form['version']='1'; form['time_out_2026-10-05']='18:00'
    assert client.post('/hr/weeks',data=form).status_code==302
    with app.app_context():
        db=get_db(); assert db.execute('SELECT version FROM hr_weeks').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM hr_signatures').fetchone()[0]==4
        old=json.loads(db.execute("SELECT snapshot FROM hr_signatures WHERE action='approved'").fetchone()[0])
        assert old[0]['time_out']=='19:00'
    assert client.get('/hr/weeks?week=2026-10-05').status_code==200


@pytest.mark.parametrize('changes',[{'lunch_in_2026-10-05':''},{'time_out_2026-10-05':'08:00'},
                                  {'lunch_out_2026-10-05':'08:00'},{'time_in_2026-10-05':'99:00'}])
def test_invalid_intervals_do_not_save(app,client,workforce,changes):
    form=week_form(); form.update(changes)
    assert client.post('/hr/weeks',data=form).status_code==400
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM hr_weeks').fetchone()[0]==0


def test_current_week_cannot_be_signed_early(app,client,workforce):
    form=week_form('submit'); form['week_start']='2026-10-12'
    assert client.post('/hr/weeks',data=form).status_code==400


def shift_form(uid,lunch='09:00',end='10:00',**extra):
    return dict(user_id=uid,work_date='2026-10-19',time_in='05:00',time_out='13:00',lunch_out=lunch,lunch_in=end,**extra)


def test_roster_drafts_then_atomic_publication(app,client,workforce):
    workforce(2)
    assert client.post('/hr/roster',data=shift_form(1)).status_code==302
    assert client.post('/hr/roster/publish',data={'week':'2026-10-19'}).status_code==400
    assert client.post('/hr/roster',data=shift_form(4,'08:00','09:00')).status_code==302
    assert client.post('/hr/roster/publish',data={'week':'2026-10-19'}).status_code==302
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM hr_shifts WHERE status='published'").fetchone()[0]==2
    workforce(1)
    assert client.get('/hr/roster').status_code==200
    assert client.post('/hr/roster',data=shift_form(1)).status_code==403


def test_department_lunch_overlap_is_blocked(app,client,workforce):
    workforce(2)
    client.post('/hr/roster',data=shift_form(1))
    client.post('/hr/roster',data=shift_form(4))
    response=client.post('/hr/roster/publish',data={'week':'2026-10-19'})
    assert response.status_code==400 and b'overlapping lunch' in response.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM hr_shifts WHERE status='published'").fetchone()[0]==0


def test_first_six_month_sick_rule_and_family_eligibility(app,client,workforce):
    workforce(2)
    with app.app_context():
        db=get_db(); db.execute("UPDATE hr_profiles SET start_date='2026-09-01' WHERE user_id=1"); db.commit()
    base=dict(user_id=1,as_of='2026-10-16',balance='3',taken='0',source='Verified record',days_worked='26')
    assert client.post('/hr/opening-balances',data=dict(base,leave_type='sick')).status_code==400
    assert client.post('/hr/opening-balances',data=dict(base,leave_type='family')).status_code==400
    base['balance']='1'
    assert client.post('/hr/opening-balances',data=dict(base,leave_type='sick')).status_code==302


def test_csrf_and_revoked_database_permissions(app,client,workforce):
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/hr/weeks',data=week_form()).status_code==400
    app.config['WTF_CSRF_ENABLED']=False
    client.post('/hr/leave/request',data=request_form())
    workforce(5)
    with app.app_context():
        db=get_db(); db.execute('DELETE FROM user_permissions WHERE user_id=5'); db.commit()
    assert client.post('/hr/leave/1/reject',data={'version':'0','decision_note':'review'}).status_code==403


def test_migration_repeatable_and_screens_render(app,client,workforce):
    with app.app_context():
        db=get_db(); hr.migrate(db); hr.migrate(db)
    workforce(2)
    for path in ['/hr/me','/hr/people','/hr/leave','/hr/leave/request','/hr/leave/calendar','/hr/roster','/hr/weeks']:
        assert client.get(path).status_code==200,path


def test_concurrent_approvals_cannot_overspend(app,client,workforce):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from flask import session
    make_account(app,client,workforce,balance='3',taken='0')
    workforce(1)
    client.post('/hr/leave/request',data=request_form())
    form=request_form(); form.update(start_date='2026-10-21',end_date='2026-10-22',token='another-unique-token-123456')
    client.post('/hr/leave/request',data=form)
    barrier=Barrier(2)
    def decide(lid):
        with app.test_request_context('/hr/leave'):
            session['user_id']=2
            db=get_db(); barrier.wait(timeout=10)
            try:
                with hr.transaction(db):
                    hr.decide_leave(db,lid,'approve',{'version':'0','decision_note':'Reviewed replacement coverage arranged by HR'})
                return 'approved'
            except ValueError:
                return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(decide,[1,2]))
    assert sorted(results)==['approved','blocked']
    with app.app_context():
        db=get_db(); assert hr.account_balance(db,hr.current_accounts(db,1)[0])['available']==100


def test_earned_annual_and_old_cycle_balances_remain_visible(app,client,workforce,monkeypatch):
    make_account(app,client,workforce,balance='10',taken='5')
    with app.app_context():
        db=get_db(); account=hr.current_accounts(db,1)[0]
        later=hr.account_balance(db,account,date(2026,11,16))
        assert later['earned']==125
    monkeypatch.setattr(hr,'today',lambda:date(2027,1,15))
    workforce(1)
    response=client.get('/hr/balances')
    assert response.status_code==200 and b'2026-01-01' in response.data
    with app.app_context():
        assert len(hr.current_accounts(get_db(),1))==1


def test_adjustment_retains_history_and_requires_second_reviewer(app,client,workforce):
    make_account(app,client,workforce,balance='10',taken='5')
    workforce(2)
    assert client.post('/hr/balance-adjustments',data={'account_id':1,'direction':'credit','days':'2','source':'Signed correction: two days entered twice'}).status_code==302
    assert client.post('/hr/opening-balances/2/confirm').status_code==403
    workforce(3)
    assert client.post('/hr/opening-balances/2/confirm').status_code==302
    with app.app_context():
        db=get_db(); result=hr.account_balance(db,hr.current_accounts(db,1)[0])
        assert result['available']==1200 and result['taken']==500
        assert db.execute('SELECT COUNT(*) FROM hr_ledger').fetchone()[0]==3


def test_hr_notifications_survive_generic_refresh_and_link_to_review(app,client,workforce):
    client.post('/hr/leave/request',data=request_form())
    workforce(5)
    response=client.get('/notifications')
    assert response.status_code==200 and b'leave request needs review' in response.data
    assert b'/hr/leave' in response.data
    client.post('/hr/leave/1/reject',data={'version':'0','decision_note':'Dates discussed with employee'})
    workforce(1)
    assert b'leave request was declined' in client.get('/notifications').data


def test_manager_needs_both_permission_and_reporting_assignment(app,client,workforce):
    client.post('/hr/leave/request',data=request_form())
    workforce(5)
    with app.app_context():
        db=get_db(); db.execute('UPDATE hr_profiles SET manager_id=3 WHERE user_id=1'); db.commit()
    assert client.post('/hr/leave/1/reject',data={'version':'0','decision_note':'Not my employee'}).status_code==403


def test_leave_becomes_taken_without_duplicate_debits(app,client,workforce,monkeypatch):
    make_account(app,client,workforce)
    workforce(1); client.post('/hr/leave/request',data=request_form())
    workforce(2); client.post('/hr/leave/1/approve',data={'version':'0','decision_note':'Named replacement confirmed'})
    monkeypatch.setattr(hr,'today',lambda:date(2026,10,21))
    with app.app_context():
        db=get_db(); account=hr.current_accounts(db,1)[0]
        first=hr.account_balance(db,account); second=hr.account_balance(db,account)
        assert first==second
        assert first['reserved']==0 and first['taken']==400


def test_stale_week_version_and_changed_opening_terms_are_rejected(app,client,workforce):
    client.post('/hr/weeks',data=week_form())
    assert client.post('/hr/weeks',data=week_form()).status_code==400
    workforce(2)
    client.post('/hr/opening-balances',data={'user_id':1,'leave_type':'annual','as_of':'2026-10-16','balance':'10','taken':'0','source':'verified'})
    with app.app_context():
        db=get_db(); db.execute('UPDATE hr_profiles SET version=version+1 WHERE user_id=1'); db.commit()
    workforce(3)
    assert client.post('/hr/opening-balances/1/confirm').status_code==400


def test_single_person_department_has_floating_manager_relief(app,client,workforce):
    workforce(2)
    assert client.post('/hr/roster',data=shift_form(1,relief_id=2)).status_code==302
    assert client.post('/hr/roster',data=shift_form(2,'08:00','09:00')).status_code==302
    assert client.post('/hr/roster/publish',data={'week':'2026-10-19'}).status_code==302


def test_review_queue_shows_missing_and_submitted_weeks(app,client,workforce):
    client.post('/hr/weeks',data=week_form('submit'))
    workforce(5)
    response=client.get('/hr/reviews?week=2026-10-05')
    assert response.status_code==200
    assert b'Employee One' in response.data and b'submitted' in response.data
    assert b'Employee Two' in response.data and b'Not started' in response.data


def test_sick_six_month_transition_preserves_usage_and_later_adjustments(app,client,workforce,monkeypatch):
    with app.app_context():
        db=get_db(); db.execute("UPDATE hr_profiles SET start_date='2026-09-01' WHERE user_id=1"); db.commit()
    workforce(2)
    client.post('/hr/opening-balances',data={'user_id':1,'leave_type':'sick','as_of':'2026-10-16','balance':'0','taken':'1','days_worked':'26','source':'Confirmed first-six-month record'})
    workforce(3); assert client.post('/hr/opening-balances/1/confirm').status_code==302
    monkeypatch.setattr(hr,'today',lambda:date(2027,3,2))
    with app.app_context():
        db=get_db(); account=hr.current_accounts(db,1)[0]
        assert hr.account_balance(db,account)['available']==2900
        db.execute("INSERT INTO hr_ledger(account_id,units,kind,reference,effective_date,actor_id,reason) VALUES (1,-100,'adjustment','test-correction','2027-03-02',2,'Reviewed correction')")
        db.commit()
        assert hr.account_balance(db,account)['available']==2800


def test_system_administrator_is_not_created_as_an_employee(app,client,workforce):
    with app.app_context():
        db=get_db(); db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES (6,'admin','x','System Administrator','admin',1)"); db.commit()
    workforce(6)
    assert client.get('/hr/me').status_code==302
    assert client.get('/hr/people').status_code==200
    assert client.get('/hr/weeks').status_code==302
    with app.app_context():
        assert get_db().execute('SELECT 1 FROM hr_profiles WHERE user_id=6').fetchone() is None


def test_company_21_working_day_policy_and_monthly_accrual(app,client,workforce):
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO hr_policy_settings(name,value,effective_date,source) VALUES ('annual_working_days','21','2026-10-16','Owner-approved company benefit')")
        db.execute("UPDATE hr_profiles SET weekdays='[0,1,2,3,4,5]' WHERE user_id=4")
        db.commit()
        for uid in (1,4):
            p=db.execute('SELECT * FROM hr_profiles WHERE user_id=?',(uid,)).fetchone()
            assert hr.annual_entitlement(db,p)==21
    make_account(app,client,workforce,balance='10',taken='0')
    with app.app_context():
        db=get_db(); account=hr.current_accounts(db,1)[0]
        assert account['annual_days']==21
        assert hr.account_balance(db,account,date(2026,11,16))['earned']==175
    workforce(1)
    response=client.get('/hr/me')
    assert b'21 working days per year' in response.data and b'1.75 days earned' in response.data


def test_changed_company_policy_requires_reverified_opening_proposal(app,client,workforce):
    workforce(2)
    client.post('/hr/opening-balances',data={'user_id':1,'leave_type':'annual','as_of':'2026-10-16','balance':'10','taken':'0','source':'verified'})
    with app.app_context():
        db=get_db(); db.execute("INSERT INTO hr_policy_settings(name,value,effective_date,source) VALUES ('annual_working_days','21','2026-10-16','Owner instruction')"); db.commit()
    workforce(3)
    response=client.post('/hr/opening-balances/1/confirm')
    assert response.status_code==400 and b'Annual entitlement changed' in response.data
