from datetime import date
import pytest
from werkzeug.exceptions import BadRequest
from onecpase.dashboard_reporting import resolve_period,activity_rows,comparison_row
from onecpase.database import get_db


@pytest.mark.parametrize('args,expected',[
    ({'period':'day'},('2026-10-28','2026-10-28','2026-10-27','2026-10-27')),
    ({'period':'week'},('2026-10-26','2026-10-28','2026-10-19','2026-10-21')),
    ({'period':'cycle'},('2026-10-15','2026-10-28','2026-09-15','2026-09-28')),
    ({'period':'3cycles'},('2026-08-15','2026-10-28','2026-05-15','2026-07-28')),
    ({'period':'6cycles'},('2026-05-15','2026-10-28','2025-11-15','2026-04-30')),
    ({'period':'cycle','cycle':'2026-10'},('2026-09-15','2026-10-14','2026-08-15','2026-09-14')),
    ({'period':'week','on':'2026-10-04'},('2026-09-28','2026-10-04','2026-09-21','2026-09-27')),
    ({'from':'2026-09-02','to':'2026-09-04'},('2026-09-02','2026-09-04','2026-08-30','2026-09-01')),
])
def test_reporting_dates_and_nonoverlap(args,expected):
    p=resolve_period(args,today=date(2026,10,28))
    assert (p.date_from,p.date_to,p.previous_start.isoformat(),p.previous_end.isoformat())==expected
    assert p.previous_end<p.start
    if p.partial:assert p.days==p.previous_days


@pytest.mark.parametrize('today,key,start,end,previous_start,previous_end',[
    ('2026-10-14','2026-10','2026-09-15','2026-10-14','2026-08-15','2026-09-14'),
    ('2026-10-15','2026-11','2026-10-15','2026-10-15','2026-09-15','2026-09-15'),
    ('2026-12-31','2027-01','2026-12-15','2026-12-31','2026-11-15','2026-12-01'),
    ('2028-02-29','2028-03','2028-02-15','2028-02-29','2028-01-15','2028-01-29'),
])
def test_cycle_boundary_leap_and_year_rollover(today,key,start,end,previous_start,previous_end):
    p=resolve_period({'period':'cycle'},today=date.fromisoformat(today))
    assert p.cycle==p.max_cycle==key
    assert (p.date_from,p.date_to,p.previous_start.isoformat(),p.previous_end.isoformat())==(start,end,previous_start,previous_end)


@pytest.mark.parametrize('args',[{'period':'bad'},{'period':'week','on':'bad'},
    {'period':'day','on':'2026-10-29'},{'period':'cycle','cycle':'2026-12'},
    {'period':'3cycles','cycle':'2026-12'},{'period':'6cycles','cycle':'2026-12'},
    {'period':'cycle','cycle':'2026-13'},{'from':'2026-10-10','to':'2026-10-01'},
    {'from':'2024-01-01','to':'2026-01-01'}])
def test_invalid_or_not_started_period_fails_clearly(args):
    with pytest.raises(BadRequest):resolve_period(args,today=date(2026,10,28))


def test_zero_baseline_and_percentage_point_changes_are_defined():
    assert comparison_row('New',5,0)['percent'] is None
    assert comparison_row('None',0,0)['percent']==0
    assert comparison_row('Rate',None,25,'percent')['change'] is None
    assert comparison_row('Rate',50,25,'percent')['change']==25


def test_period_cash_and_calls_exclude_future_days_and_other_staff(app):
    with app.app_context():
        db=get_db()
        for uid,dept in ((1,'Reception'),(2,'Sales')):
            db.execute('INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES(?,?,?,?,?,?,1)',
                       (uid,'period'+str(uid),'x','Period staff','staff',dept))
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number) VALUES('Period','Member','period-member')").lastrowid
        # Current comparable days, previous comparable days, later previous
        # days, later current days and discount adjustments are distinct.
        for day,amount,notes in [('2026-10-28',100,''),('2026-09-28',40,''),
                                 ('2026-09-29',1000,''),('2026-10-29',2000,''),
                                 ('2026-10-28',500,'type=Discount')]:
            db.execute("INSERT INTO collections(member_id,collection_date,amount_paid,status,notes) VALUES(?,?,?,'paid',?)",(mid,day,amount,notes))
        for uid,stamp in ((1,'2026-10-28 11:00:00'),(1,'2026-09-28 11:00:00'),(2,'2026-10-28 12:00:00')):
            db.execute("INSERT INTO pbx_call_logs(user_id,direction,start_time,talk_duration) VALUES(?,'outbound',?,60)",(uid,stamp))
        p=resolve_period({'period':'cycle'},today=date(2026,10,28))
        rows={r['label']:r for r in activity_rows(db,p,'reception',1)}
        assert rows['Collections received']['current']==100
        assert rows['Collections received']['previous']==40
        assert rows['Collections received']['percent']==150
        assert rows['Outbound calls']['current']==1
        assert rows['Outbound calls']['previous']==1
        personal={r['label']:r for r in activity_rows(db,p,'work',2)}
        assert personal['Outbound calls']['current']==1
        assert personal['Outbound calls']['previous']==0


def test_all_eight_dashboards_use_one_reporting_contract(app,client,monkeypatch):
    monkeypatch.setattr('onecpase.dashboard.get_elev8_member_count',lambda:None)
    monkeypatch.setattr('onecpase.dashboard_reporting.business_today',lambda:date(2026,10,28))
    from onecpase.hr_workforce import ensure_profile
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES(1,'period-admin','x','Period Admin','admin','Management',1)")
        ensure_profile(db,1);db.commit()
    with client.session_transaction() as session:session.update(user_id=1,role='admin',permissions=[])
    for path in ('/dashboard','/sales-dashboard','/reception-dashboard','/fitness-dashboard',
                 '/my-dashboard','/collections/dashboard','/ptp/','/hr/me'):
        response=client.get(path+'?period=week&on=2026-10-21')
        assert response.status_code==200,path
        html=response.get_data(as_text=True)
        assert 'data-dashboard-period-form' in html,path
        assert '19 Oct 2026 – 25 Oct 2026' in html,path
        assert '12 Oct 2026 – 18 Oct 2026' in html,path
        assert '>3 cycles</option>' in html and '>6 cycles</option>' in html,path


def test_selected_chart_compares_the_same_partial_cycle():
    from onecpase.dashboard import _selected_trend
    p=resolve_period({'period':'cycle'},today=date(2026,10,28))
    rows=[dict(event_date=day,amount=value) for day,value in
        [('2026-10-28 23:59:59',100),('2026-09-28',50),('2026-09-29',1000),('2026-10-29',1000)]]
    chart=_selected_trend(rows,[],p)
    assert chart['revenue']['total']==100
    assert chart['revenue']['previous_total']==50
    assert chart['revenue']['delta']==100


def test_hr_utc_events_are_counted_on_the_south_african_day(app):
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(1,'hr-period','x','HR Staff','staff',1)")
        for index,stamp in enumerate(('2026-10-27T22:30:00+00:00','2026-10-28T22:30:00+00:00','2026-10-26T23:00:00+00:00')):
            db.execute("INSERT INTO hr_requests(user_id,leave_type,start_date,end_date,units,work_dates,created_at,policy,token) VALUES(1,'annual','2026-11-01','2026-11-01',100,'[]',?,'test',?)",(stamp,'period-'+str(index)))
        p=resolve_period({'period':'day'},today=date(2026,10,28))
        rows={r['label']:r for r in activity_rows(db,p,'hr',1)}
        assert rows['Leave requests submitted']['current']==1
        assert rows['Leave requests submitted']['previous']==1


def test_sales_period_events_use_sast_first_booking_and_consultant_scope(app):
    with app.app_context():
        db=get_db()
        for uid in (1,2):
            db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES(?,?, 'x','Sales Staff','staff','Sales',1)",(uid,'sales-period'+str(uid)))
        for lid,uid,assigned,created,contact in (
            (1,1,'2026-10-28T08:00:00+00:00','2026-10-01','2026-10-27T23:00:00+00:00'),
            (2,1,'','2026-10-27T23:00:00+00:00','2026-09-27T23:00:00+00:00'),
            (3,2,'2026-10-28','2026-10-28','2026-10-28')):
            db.execute('INSERT INTO leads(id,full_name,assigned_to,assigned_at,created_at,first_contact_at) VALUES(?,?,?,?,?,?)',
                       (lid,'Period Prospect',uid,assigned,created,contact))
        for lid,uid,stamp in ((1,1,'2026-09-27T23:00:00+00:00'),(1,1,'2026-10-28T08:00:00+00:00'),
                              (2,1,'2026-10-27T23:00:00+00:00'),(3,2,'2026-10-28T08:00:00+00:00')):
            db.execute("INSERT INTO sales_stage_history(lead_id,to_stage,changed_by,changed_at) VALUES(?,'APPOINTMENT_BOOKED',?,?)",(lid,uid,stamp))
        for uid,start,created,talk in ((1,None,'2026-10-27T22:30:00+00:00',60),
                                      (1,'2026-10-27T23:00:00+00:00','2026-10-27',59),
                                      (2,'2026-10-28','2026-10-28',60)):
            db.execute("INSERT INTO pbx_call_logs(user_id,direction,start_time,created_at,talk_duration) VALUES(?,'outbound',?,?,?)",(uid,start,created,talk))
        p=resolve_period({'period':'cycle'},today=date(2026,10,28))
        for lid, uid, outcome, stamp in (
            (1,1,'appointment_booked','2026-10-27T22:30:00Z'),
            (2,1,'no_response','2026-10-28T08:00:00Z'),
            (3,2,'follow_up_required','2026-10-28T08:00:00Z'),
            (1,1,'not_interested','2026-09-28T08:00:00Z'),
        ):
            db.execute("INSERT INTO lead_activities(lead_id,activity_type,outcome,created_by,occurred_at) VALUES(?,'call',?,?,?)",
                       (lid,outcome,uid,stamp))
        rows={r['label']:r for r in activity_rows(db,p,'sales',1,allowed_ids=[1])}
        assert rows['Leads assigned']['current']==2
        assert rows['First contacts recorded']['current']==rows['First contacts recorded']['previous']==1
        assert rows['First appointments booked']['current']==rows['First appointments booked']['previous']==1
        assert rows['Calls made']['current']==2
        assert rows['Connected calls']['current']==1
        assert rows['Calls made']['previous']==rows['Connected calls']['previous']==1
        assert not any('60s' in label for label in rows)


def test_sales_dashboard_business_dates_preserve_legacy_report_option(app):
    from onecpase.sales_kpi import build_sales_report
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(1,'sast-consultant','x','Consultant','staff',1)")
        db.execute("INSERT INTO leads(full_name,assigned_to,created_at) VALUES('Late-night lead',1,'2026-10-27T23:30:00+00:00')")
        assert build_sales_report(db,'2026-10-28','2026-10-28',1,business_dates=True)['funnel']['assigned']==1
        assert build_sales_report(db,'2026-10-28','2026-10-28',1)['funnel']['assigned']==0


@pytest.mark.parametrize('path',['/sales-dashboard','/reception-dashboard','/collections/dashboard'])
def test_historical_daily_view_defaults_targets_to_that_day_and_keeps_explicit_override(app,client,path):
    from bs4 import BeautifulSoup
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES(1,'daily-period-admin','x','Daily Admin','admin','Management',1)")
        db.commit()
    with client.session_transaction() as session:session.update(user_id=1,role='admin',permissions=[])
    for suffix,expected in (('','2026-10-21'),('&kpi_date=2026-10-20','2026-10-20')):
        response=client.get(path+'?period=day&on=2026-10-21'+suffix)
        assert response.status_code==200
        document=BeautifulSoup(response.data,'html.parser')
        assert document.select_one('input[name=kpi_date]')['value']==expected
