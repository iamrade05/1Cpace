from datetime import datetime, timedelta, timezone

import pytest

from onecpase.database import get_db
from onecpase.fitness_journey import ensure_journey
from onecpase import fitness_allocation as allocation


def setup(db):
    db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(1,'offer-test','x','Instructor','trainer',1)")
    db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(2,'manager-test','x','Manager','manager',1)")
    mid = db.execute("INSERT INTO members(first_name,last_name,id_number,contact,outcome,join_date) VALUES('New','Member','offers-id','0821234567','joined','2026-10-25')").lastrowid
    ensure_journey(db)
    db.execute("UPDATE fitness_intake SET goal_category='muscle_gain',allocation_state='awaiting_allocation' WHERE member_id=?",(mid,))
    return mid


def test_eight_hour_offer_cannot_be_accepted_after_deadline(app):
    with app.app_context():
        db=get_db(); mid=setup(db)
        now=datetime(2026,10,25,tzinfo=timezone.utc)
        offer=allocation.create_offer(db,mid,1,now)
        with pytest.raises(ValueError):
            allocation.respond(db,offer,1,True,now=now+timedelta(hours=8))
        assert db.execute('SELECT status FROM fitness_allocation_offers WHERE id=?',(offer,)).fetchone()[0]=='timed_out'
        assert db.execute('SELECT assigned_instructor_id FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0] is None


def test_fourth_management_counted_skip_raises_review_without_auto_restriction(app):
    with app.app_context():
        db=get_db(); mid=setup(db)
        # Five declines, one excused: only four count towards the issue.
        for index in range(5):
            offer=allocation.create_offer(db,mid,1)
            allocation.respond(db,offer,1,False,reason='Schedule conflict')
            allocation.classify_skip(db,offer,2,index!=0,'Management assessed the explanation')
            assert allocation.pt_allowed(db,1)
            reviews=db.execute('SELECT * FROM fitness_pt_reviews').fetchall()
            assert len(reviews)==(1 if index==4 else 0)
        review=reviews[0]['id']
        allocation.decide_rights(db,review,2,True,'Four avoidable declines; management restricts PT')
        assert not allocation.pt_allowed(db,1)
        assert db.execute('SELECT active FROM users WHERE id=1').fetchone()[0]==1
        with pytest.raises(ValueError):
            allocation.decide_rights(db,review,2,False,'Cannot replace immutable review')
        with pytest.raises(ValueError):
            allocation.classify_skip(db,offer,2,False,'Cannot change original classification')


def test_review_page_requires_management(app,client):
    with app.app_context():
        setup(get_db())
    with client.session_transaction() as state:
        state.update(user_id=1,role='trainer',permissions=['fitness_module'])
    assert client.get('/fitness/intake/allocation-reviews').status_code==403
    with client.session_transaction() as state:
        state.update(user_id=2,role='manager',permissions=['fitness_module'])
    assert client.get('/fitness/intake/allocation-reviews').status_code==200


def test_unclear_goal_blocks_offer_until_member_clarification(app):
    with app.app_context():
        db=get_db();mid=setup(db)
        db.execute("UPDATE fitness_intake SET goal_category='mixed' WHERE member_id=?",(mid,))
        with pytest.raises(ValueError,match='clarify'):
            allocation.create_offer(db,mid,1)
        assert db.execute('SELECT COUNT(*) FROM fitness_allocation_offers').fetchone()[0]==0


NOW=datetime(2026,10,25,8,tzinfo=timezone.utc)
START='2026-10-26T07:00:00+00:00'
END='2026-10-26T08:00:00+00:00'


def roster(db, uid, kind='group', rotation='fixed'):
    db.execute('''INSERT INTO fitness_allocation_roster(user_id,kind,rotation,enabled)
        VALUES(?,?,?,1)''',(uid,kind,rotation))
    db.execute("INSERT INTO fitness_roster_windows VALUES(?,0,'05:00','21:00')",(uid,))
    db.execute("INSERT OR IGNORE INTO fitness_gym_hours VALUES(0,'05:00','21:00')")


def queue_member(db, mid):
    db.execute("UPDATE fitness_intake SET goal_category='weight_loss',interest='interested' WHERE member_id=?",(mid,))
    return allocation.request_allocation(db,mid,START,END,2,NOW)


def test_decline_advances_and_acceptance_reserves_atomically(app):
    with app.app_context():
        db=get_db();mid=setup(db)
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(3,'next-instructor','x','Next instructor','instructor',1)")
        roster(db,1);roster(db,3)
        rid=queue_member(db,mid)
        allocation.process_queue(db,NOW)
        first=db.execute('SELECT * FROM fitness_allocation_offers').fetchone()
        assert first['instructor_id']==1
        allocation.respond(db,first['id'],1,False,'Unavailable for this appointment',NOW+timedelta(hours=1))
        allocation.process_queue(db,NOW+timedelta(hours=1))
        second=db.execute('SELECT * FROM fitness_allocation_offers ORDER BY id DESC').fetchone()
        assert second['instructor_id']==3
        allocation.respond(db,second['id'],3,True,now=NOW+timedelta(hours=2))
        with pytest.raises(ValueError):
            allocation.respond(db,first['id'],1,True,now=NOW+timedelta(hours=2))
        assert db.execute('SELECT status,instructor_id FROM fitness_allocation_requests WHERE id=?',(rid,)).fetchone()['status']=='reserved'
        assert db.execute('SELECT assigned_instructor_id FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0]==3
        allocation.process_queue(db,NOW+timedelta(hours=2))
        assert db.execute("SELECT COUNT(*) FROM staff_notifications WHERE source_kind='fitness_offer' AND resolved_at IS NULL").fetchone()[0]==0


def test_timeout_reallocates_once_and_alerts_survive_general_notification_refresh(app):
    from onecpase.notifications import sync_notifications
    with app.app_context():
        db=get_db();mid=setup(db)
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(3,'next-instructor','x','Next instructor','instructor',1)")
        roster(db,1);roster(db,3);queue_member(db,mid)
        allocation.process_queue(db,NOW)
        sync_notifications(db)
        assert db.execute("SELECT COUNT(*) FROM staff_notifications WHERE source_kind='fitness_offer' AND resolved_at IS NULL").fetchone()[0]==1
        allocation.process_queue(db,NOW+timedelta(hours=8))
        allocation.process_queue(db,NOW+timedelta(hours=8))
        assert db.execute('SELECT COUNT(*) FROM fitness_allocation_offers').fetchone()[0]==2
        assert db.execute("SELECT instructor_id FROM fitness_allocation_offers WHERE status='pending'").fetchone()[0]==3
        assert db.execute("SELECT counts_as_skip FROM fitness_allocation_offers WHERE status='timed_out'").fetchone()[0] is None


@pytest.mark.parametrize('blocker',['leave','class','appointment','hours','disabled'])
def test_unavailable_instructor_is_not_allocated(app,blocker):
    with app.app_context():
        db=get_db();mid=setup(db);roster(db,1,'fitness');rid=queue_member(db,mid)
        if blocker=='leave':db.execute("INSERT INTO leave_requests(user_id,start_date,end_date,status) VALUES(1,'2026-10-26','2026-10-26','approved')")
        if blocker=='class':db.execute("INSERT INTO fitness_classes(name,instructor_id,schedule_day,schedule_time) VALUES('Class',1,'Monday','09:00')")
        if blocker=='appointment':db.execute("INSERT INTO instructor_dailies(instructor_id,date,time_in,time_out) VALUES(1,'2026-10-26','09:00','10:00')")
        if blocker=='hours':db.execute("DELETE FROM fitness_gym_hours")
        if blocker=='disabled':db.execute("UPDATE fitness_allocation_roster SET enabled=0")
        allocation.process_queue(db,NOW)
        row=db.execute('SELECT * FROM fitness_allocation_requests WHERE id=?',(rid,)).fetchone()
        assert row['status']=='waiting' and row['instructor_id'] is None
        assert 'no eligible instructor' in row['next_action']


def test_anchor_rotation_and_goal_specific_queue(app):
    from datetime import date
    assert allocation.shift_window('early',date(2026,10,26),'00:00','23:00')==('05:00','13:00')
    assert allocation.shift_window('early',date(2026,11,2),'00:00','23:00')==('13:00','21:00')
    assert allocation.shift_window('late',date(2027,1,4),'00:00','23:00')==('13:00','21:00')
    with app.app_context():
        db=get_db();mid=setup(db);roster(db,1,'group')
        queue_member(db,mid)
        db.execute("UPDATE fitness_intake SET goal_category='muscle_gain' WHERE member_id=?",(mid,))
        allocation.process_queue(db,NOW)
        assert db.execute('SELECT COUNT(*) FROM fitness_allocation_offers').fetchone()[0]==0


def test_offer_response_cannot_be_posted_by_other_instructor(app,client):
    with app.app_context():
        db=get_db();mid=setup(db);roster(db,1);queue_member(db,mid)
        allocation.process_queue(db,NOW)
        offer=db.execute('SELECT id FROM fitness_allocation_offers').fetchone()[0];db.commit()
    with client.session_transaction() as state:
        state.update(user_id=2,role='manager',permissions=['fitness_module'])
    response=client.post('/fitness/intake/allocations',data={'action':'accept','offer_id':offer})
    assert response.status_code==302
    with app.app_context():
        assert get_db().execute('SELECT status FROM fitness_allocation_offers WHERE id=?',(offer,)).fetchone()[0]!='accepted'


def test_covering_appointment_keeps_primary_instructor_and_requires_assisted_session(app):
    with app.app_context():
        db=get_db();mid=setup(db);roster(db,1,'fitness');rid=queue_member(db,mid)
        db.execute('UPDATE fitness_intake SET primary_instructor_id=2 WHERE member_id=?',(mid,))
        allocation.process_queue(db,NOW)
        allocation.record_appointment(db,rid,'confirm',1,'Confirmed by member at reception',now=NOW)
        assert db.execute('SELECT confirmed_at FROM fitness_allocation_requests WHERE id=?',(rid,)).fetchone()[0]
        allocation.process_queue(db,datetime(2026,10,26,7,30,tzinfo=timezone.utc))
        assert db.execute('SELECT status FROM fitness_allocation_requests WHERE id=?',(rid,)).fetchone()[0]=='attendance_due'
        with pytest.raises(ValueError,match='recorded assisted session'):
            allocation.record_appointment(db,rid,'attended',1,'Gym entry only',now=datetime(2026,10,26,8,tzinfo=timezone.utc))
        assert db.execute('SELECT primary_instructor_id FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0]==2
        allocation.record_appointment(db,rid,'missed',1,'Instructor confirmed member did not attend',now=datetime(2026,10,26,8,tzinfo=timezone.utc))
        assert db.execute('SELECT status FROM fitness_allocation_requests WHERE id=?',(rid,)).fetchone()[0]=='missed'


def test_overlapping_reservation_is_not_double_booked_and_rotation_persists(app):
    with app.app_context():
        db=get_db();mid=setup(db);roster(db,1,'fitness')
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(3,'next-instructor','x','Next instructor','instructor',1)")
        roster(db,3,'fitness')
        queue_member(db,mid);allocation.process_queue(db,NOW)
        other=db.execute("INSERT INTO members(first_name,last_name,id_number,outcome,join_date) VALUES('Other','Member','other-offer-id','joined','2026-10-25')").lastrowid
        db.commit();ensure_journey(db)
        queue_member(db,other);allocation.process_queue(db,NOW)
        assert [r[0] for r in db.execute('SELECT instructor_id FROM fitness_allocation_requests ORDER BY id').fetchall()]==[1,3]
        assert db.execute('SELECT last_user_id FROM fitness_allocation_cursor WHERE id=1').fetchone()[0]==3


def test_manager_can_configure_roster_but_instructor_cannot(app,client):
    with app.app_context():setup(get_db())
    with client.session_transaction() as state:
        state.update(user_id=1,role='trainer',permissions=['fitness_module'])
    assert client.get('/fitness/intake/allocation-roster').status_code==403
    with client.session_transaction() as state:
        state.update(user_id=2,role='manager',permissions=['fitness_module'])
    assert client.get('/fitness/intake/allocation-roster').status_code==200
    assert client.post('/fitness/intake/allocation-roster',data={
        'action':'hours','weekday':'0','start_time':'05:00','end_time':'21:00'}).status_code==302
    assert client.post('/fitness/intake/allocation-roster',data={
        'action':'roster','weekday':'0','start_time':'05:00','end_time':'21:00',
        'user_id':'1','kind':'fitness','rotation':'early','enabled':'yes'}).status_code==302
    with app.app_context():
        assert get_db().execute('SELECT rotation,enabled FROM fitness_allocation_roster WHERE user_id=1').fetchone()['rotation']=='early'


def test_preference_change_withdraws_offer_and_does_not_assign_dropdown_instructor(app,client):
    with app.app_context():
        db=get_db();mid=setup(db);roster(db,1);queue_member(db,mid);allocation.process_queue(db,NOW);db.commit()
    with client.session_transaction() as state:
        state.update(user_id=1,role='trainer',permissions=['fitness_module'])
    assert client.post(f'/fitness/intake/{mid}',data={'interest':'interested','goal_category':'mixed','goal':'Unsure','instructor_id':'1'}).status_code==302
    with app.app_context():
        db=get_db()
        assert db.execute('SELECT status FROM fitness_allocation_offers').fetchone()[0]=='withdrawn'
        assert db.execute('SELECT allocation_state FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0]=='awaiting_goal_clarification'


def test_instructor_can_accept_visible_offer_through_local_screen(app,client,monkeypatch):
    monkeypatch.setattr(allocation,'utc_now',lambda:NOW)
    with app.app_context():
        db=get_db();mid=setup(db);roster(db,1);queue_member(db,mid);allocation.process_queue(db,NOW);db.commit()
        offer=db.execute('SELECT id FROM fitness_allocation_offers').fetchone()[0]
    with client.session_transaction() as state:
        state.update(user_id=1,role='trainer',permissions=['fitness_module'])
    page=client.get('/fitness/intake/allocations')
    assert page.status_code==200 and b'value="accept"' in page.data and b'Africa/Johannesburg' in page.data
    assert client.post('/fitness/intake/allocations',data={'action':'accept','offer_id':offer}).status_code==302
    with app.app_context():
        assert get_db().execute('SELECT status FROM fitness_allocation_requests').fetchone()[0]=='reserved'


def test_scheduler_processes_queue_without_outbound_transport(app,monkeypatch):
    from onecpase import scheduler
    monkeypatch.setattr(allocation,'utc_now',lambda:NOW)
    monkeypatch.setattr(scheduler,'_active_tenants',lambda app:[{'slug':'isolated','db_path':app.config['DATABASE_PATH']}])
    with app.app_context():
        db=get_db();mid=setup(db);roster(db,1);queue_member(db,mid);db.commit()
    scheduler._job_fitness_allocations(app)
    with app.app_context():
        assert get_db().execute('SELECT status FROM fitness_allocation_requests').fetchone()[0]=='offered'
        assert get_db().execute("SELECT COUNT(*) FROM staff_notifications WHERE source_kind='fitness_offer'").fetchone()[0]==1


def test_new_leave_withdraws_pending_offer_without_counting_a_skip(app):
    with app.app_context():
        db=get_db();mid=setup(db);roster(db,1);queue_member(db,mid);allocation.process_queue(db,NOW)
        db.execute("INSERT INTO leave_requests(user_id,start_date,end_date,status) VALUES(1,'2026-10-26','2026-10-26','approved')")
        allocation.process_queue(db,NOW+timedelta(hours=1))
        offer=db.execute('SELECT * FROM fitness_allocation_offers').fetchone()
        assert offer['status']=='withdrawn' and offer['counts_as_skip'] is None
        assert db.execute('SELECT status FROM fitness_allocation_requests').fetchone()[0]=='waiting'
