import json
from datetime import datetime,timedelta
from onecpase.database import get_db
from onecpase.fitness_cycle import today
from onecpase.fitness_journey import ensure_journey,queue_welcome
from onecpase.onboarding import ensure_onboarding_rows,register_arrival,calculate_daily_calories


def seed(app,client):
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(1,'fitness-test','x','Instructor','trainer',1)")
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number,contact,join_date,date_of_birth,gender) VALUES('New','Client','journey-id','0821234567',?,'1990-01-01','male')",((today()-timedelta(days=1)).isoformat(),)).lastrowid
        db.commit();ensure_onboarding_rows(db)
        vid=register_arrival(db,mid,'manual',datetime.combine(today(),datetime.min.time()))
    with client.session_transaction() as session:
        session.update(user_id=1,role='trainer',permissions=['fitness_module','body_matrix'])
    return mid,vid


def test_programme_starts_day_after_joining_and_tracks_first_assistance_separately(app,client):
    mid,vid=seed(app,client)
    with app.app_context():
        assert get_db().execute('SELECT started_on FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0] is None
        assert get_db().execute('SELECT program_start_on FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0]==today().isoformat()
    assert client.post(f'/fitness/onboarding/visit/{vid}',data={'instructor_id':1,'outcome':'assisted','actions':['gym_intro']}).status_code==302
    with app.app_context():
        db=get_db();start=db.execute('SELECT started_on FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0]
        assert start==today().isoformat()
        assert register_arrival(db,mid,'manual',datetime.combine(today()+timedelta(days=9),datetime.min.time()))
        assert register_arrival(db,mid,'manual',datetime.combine(today()+timedelta(days=10),datetime.min.time())) is None


def test_assessment_is_shared_with_body_matrix_and_goal(app,client):
    mid,vid=seed(app,client)
    response=client.post(f'/fitness/onboarding/visit/{vid}/assessment',data={'weight_kg':'80','height_cm':'180','waist_cm':'90','fitness_goal':'Improve strength','activity_level':'light'})
    assert response.status_code==302
    with app.app_context():
        db=get_db();a=db.execute('SELECT * FROM assessments').fetchone();b=db.execute('SELECT * FROM body_matrix').fetchone()
        assert a['weight_kg']==b['weight_kg']==80 and a['bmi']==b['bmi']
        assert b['waist_cm']==90
        assert db.execute('SELECT goal FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0]=='Improve strength'
    assert client.post(f'/fitness/onboarding/visit/{vid}/assessment',data={'weight_kg':'-80','height_cm':'180','fitness_goal':'x'}).status_code==400


def test_session_signature_needs_assistance_and_baseline_and_locks_record(app,client):
    mid,vid=seed(app,client)
    payload={'client_name':'New Client','signature':json.dumps([[[10,10],[20,20],[30,10]]]),'confirm':'yes'}
    path=f'/fitness/intake/visit/{vid}/sign'
    assert client.post(path,data=payload).status_code==400
    client.post(f'/fitness/onboarding/visit/{vid}',data={'instructor_id':1,'outcome':'assisted'})
    assert client.post(path,data=payload).status_code==400
    client.post(f'/fitness/onboarding/visit/{vid}/assessment',data={'weight_kg':80,'height_cm':180,'fitness_goal':'Fitness'})
    assert client.post(path,data={**payload,'signature':'[]'}).status_code==400
    assert client.post(path,data=payload).status_code==302
    assert b'Client session signature' in client.get(f'/fitness/onboarding/visit/{vid}').data
    client.post(f'/fitness/onboarding/visit/{vid}',data={'instructor_id':1,'outcome':'not_assisted'})
    with app.app_context():
        db=get_db();assert db.execute('SELECT outcome FROM onboarding_visits WHERE id=?',(vid,)).fetchone()[0]=='assisted'
        assert db.execute('SELECT count(*) FROM fitness_signoffs').fetchone()[0]==1
    assert client.post(f'/fitness/onboarding/visit/{vid}/assessment',data={'weight_kg':81,'height_cm':180,'fitness_goal':'Fitness'}).status_code==409


def test_intake_tracks_interest_times_allocation_and_register(app,client):
    mid,vid=seed(app,client)
    path=f'/fitness/intake/{mid}'
    assert client.post(path,data={'interest':'interested','preferred_times':'Weekdays 17:00','instructor_id':'1','goal':'Strength','goal_category':'muscle_gain'}).status_code==302
    response=client.get(path)
    assert b'Weekdays 17:00' in response.data and b'Not signed' in response.data
    assert client.post(path,data={'interest':'interested','instructor_id':'99999'}).status_code==400


def test_historical_members_are_not_automatically_welcomed(app,client,monkeypatch):
    mid,vid=seed(app,client)
    calls=[]
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message',lambda *args:calls.append(args) or {'queued':True})
    with app.test_request_context('/'):
        db=get_db();ensure_journey(db)
        assert db.execute('SELECT welcome_status FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0]=='historical'
        new=db.execute("INSERT INTO members(first_name,last_name,id_number,contact,join_date) VALUES('Future','Client','future-id','0821234568',?)",(today().isoformat(),)).lastrowid
        db.commit();ensure_journey(db)
        assert db.execute('SELECT welcome_status FROM fitness_intake WHERE member_id=?',(new,)).fetchone()[0]=='pending'
        member=db.execute('SELECT * FROM members WHERE id=?',(new,)).fetchone()
        assert queue_welcome(db,member)=='queued'
        assert queue_welcome(db,member)=='already_recorded'
    assert len(calls)==1


def test_calorie_estimate_requires_adult_inputs_and_does_not_guess():
    assert calculate_daily_calories(80,180,30,'male','light')>0
    for age,gender,activity in [(15,'male','light'),(30,None,'light'),(30,'male','unknown')]:
        assert calculate_daily_calories(80,180,age,gender,activity) is None


def test_journey_requires_fitness_access(client):
    assert client.get('/fitness/intake/1').status_code==302
    assert client.post('/fitness/intake/visit/1/sign').status_code==302


def test_reception_can_capture_preferences_but_cannot_sign_sessions(app,client):
    mid,vid=seed(app,client)
    with app.app_context():
        db=get_db();db.execute("UPDATE users SET role='reception',department='Reception' WHERE id=1");db.commit()
    with client.session_transaction() as session:session.update(role='reception')
    assert client.post(f'/fitness/intake/{mid}',data={'interest':'interested','preferred_times':'17:00'}).status_code==302
    assert b'17:00' in client.get(f'/fitness/intake/{mid}').data
    response=client.post(f'/fitness/intake/visit/{vid}/sign',data={'client_name':'Test'})
    assert response.status_code in (302,403)
    with app.app_context():assert get_db().execute('SELECT count(*) FROM fitness_signoffs').fetchone()[0]==0


def test_background_welcome_sync_only_sends_new_joiners_once(app,client,monkeypatch):
    old,vid=seed(app,client)
    from onecpase.fitness_journey import sync_welcomes
    from onecpase.platform_db import get_platform_db
    from onecpase.whatsapp_receive import record_outgoing_result
    calls=[]
    app.config['WHATSAPP_WEB_TENANT']='fitness-test'
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message',lambda *args:calls.append(args) or {'queued':True})
    with app.app_context():
        registry=get_platform_db();registry.execute("INSERT INTO tenants(slug,name,db_path,active) VALUES('fitness-test','Test',?,1)",(app.config['DATABASE_PATH'],));registry.commit()
        db=get_db();mid=db.execute("INSERT INTO members(first_name,last_name,id_number,contact,join_date) VALUES('Future','Client','bg-future','0821234568',?)",(today().isoformat(),)).lastrowid;db.commit()
    app.config['ACTIVE_PHASE']=1
    sync_welcomes(app)
    assert calls==[]
    app.config['ACTIVE_PHASE']='all'
    sync_welcomes(app);sync_welcomes(app)
    assert len(calls)==1 and calls[0][1]=='27821234568'
    record_outgoing_result(app,calls[0][1],calls[0][2],'sent')
    with app.app_context():
        db=get_db()
        assert db.execute('SELECT welcome_status FROM fitness_intake WHERE member_id=?',(old,)).fetchone()[0]=='historical'
        assert db.execute('SELECT welcome_status FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0]=='sent'
        assert db.execute('SELECT last_message_at FROM wa_conversations WHERE linked_member_id=?',(mid,)).fetchone()[0]


def test_calendar_boundaries_and_johannesburg_arrival_dates(app,client):
    from datetime import timezone
    mid,vid=seed(app,client)
    with app.app_context():
        db=get_db()
        local=register_arrival(db,mid,'turnstile',datetime(2026,10,16,23,30,tzinfo=timezone.utc))
        assert db.execute('SELECT visit_date FROM onboarding_visits WHERE id=?',(local,)).fetchone()[0]=='2026-10-17'
        db.execute("UPDATE fitness_intake SET program_start_on='2026-12-28' WHERE member_id=?",(mid,));db.commit()
        assert register_arrival(db,mid,'manual',datetime(2027,1,6,10))
        assert register_arrival(db,mid,'manual',datetime(2027,1,7,10)) is None


def test_legacy_migration_uses_assisted_sessions_not_gate_entries(app,client):
    mid,vid=seed(app,client)
    with app.app_context():
        db=get_db();db.execute('DELETE FROM fitness_intake');db.execute('DELETE FROM fitness_journey_policy');db.commit()
        ensure_journey(db)
        assert db.execute('SELECT started_on FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0] is None
        db.execute('DELETE FROM fitness_intake');db.execute('DELETE FROM fitness_journey_policy')
        db.execute("UPDATE onboarding_visits SET recorded_at=?,outcome='assisted' WHERE id=?",(today().isoformat(),vid));db.commit()
        ensure_journey(db)
        assert db.execute('SELECT started_on FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()[0]==today().isoformat()

def test_manual_welcome_allows_joined_unverified_and_blocks_ineligible(app,client,monkeypatch):
    mid,vid=seed(app,client);calls=[]
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message',lambda *args:calls.append(args) or {'queued':True})
    with app.app_context():
        db=get_db();db.execute("UPDATE members SET outcome='joined',member_status='Unverified' WHERE id=?",(mid,));db.commit()
    path=f'/fitness/intake/{mid}'
    assert b'Send welcome from Reception' in client.get(path).data
    assert client.post(path+'/welcome').status_code==302
    assert len(calls)==1
    assert client.post(path+'/welcome').status_code==302
    assert len(calls)==1
    with app.app_context():
        db=get_db();db.execute("UPDATE members SET member_status='Inactive' WHERE id=?",(mid,));db.execute("UPDATE fitness_intake SET welcome_status='historical' WHERE member_id=?",(mid,));db.commit()
    assert b'Send welcome from Reception' not in client.get(path).data
    response=client.post(path+'/welcome',follow_redirects=True)
    assert response.status_code==200 and b'Welcome not sent' in response.data
    assert len(calls)==1
    assert client.post('/fitness/intake/999999/welcome').status_code==404
