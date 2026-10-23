import sqlite3
import shutil
import pytest
from werkzeug.security import generate_password_hash
from onecpase import mobile_api

BASE = '/api/mobile/v1/elev8'


@pytest.fixture(scope='module')
def mobile_base(tmp_path_factory):
    from onecpase import create_app
    root=tmp_path_factory.mktemp('mobile-baseline')
    application=create_app(dict(TESTING=True, SECRET_KEY='mobile-test-only',
        ENCRYPTION_KEY='kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8=',
        DATABASE_URL='', DATABASE_PATH=str(root/'tenant.db'),
        PLATFORM_DATABASE_PATH=str(root/'platform.db'), TENANTS_DIR=str(root/'tenants'),
        UPLOAD_FOLDER=str(root/'uploads'), WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False, SCHEDULER_ENABLED=False, TURNSTILE_ENABLED=False,
        WHATSAPP_WEB_ENABLED=False, ACTIVE_PHASE='all', MOBILE_API_ENABLED=False,
        MOBILE_MEMBER_LOGIN_ENABLED=False))
    for name in ('DATABASE_PATH','PLATFORM_DATABASE_PATH'):
        shutil.copy2(application.config[name],application.config[name]+'.baseline')
    return application


@pytest.fixture
def app(mobile_base):
    # One expensive schema migration, then a fresh isolated database per test.
    for name in ('DATABASE_PATH','PLATFORM_DATABASE_PATH'):
        shutil.copy2(mobile_base.config[name]+'.baseline',mobile_base.config[name])
    mobile_base.config.update(ACTIVE_PHASE='all',MOBILE_API_ENABLED=False,MOBILE_MEMBER_LOGIN_ENABLED=False,SECRET_KEY='mobile-test-only')
    return mobile_base


@pytest.fixture
def mobile(app, monkeypatch):
    app.config.update(MOBILE_API_ENABLED=True, MOBILE_MEMBER_LOGIN_ENABLED=True)
    codes = []
    monkeypatch.setattr(mobile_api, '_send_otp_email', lambda email, name, code: codes.append((email, code)) or True)
    db = sqlite3.connect(app.config['DATABASE_PATH'])
    staff = db.execute('''INSERT INTO users(username,password_hash,full_name,email,role,department,active,must_change_password)
        VALUES (?,?,?,?,?,?,1,0)''', ('mobile-staff', generate_password_hash('StrongMobile123!'), 'Mobile Staff', 'staff@example.com', 'staff', 'Sales')).lastrowid
    ids=[]
    for index in (1,2):
        ids.append(db.execute('''INSERT INTO members(member_ref,first_name,last_name,id_number,email,member_status,monthly_installment)
            VALUES (?,?,?,?,?,'Active',599)''', (f'MOB-{index}',f'Member {index}','Example',f'MOB-ID-{index}',f'member{index}@example.com')).lastrowid)
    cid=db.execute("INSERT INTO fitness_classes(name,schedule_day,schedule_time,capacity) VALUES ('Mobile class','Monday','17:00',1)").lastrowid
    db.execute("INSERT INTO workout_plans(member_id,title,description,active) VALUES (?,'Private plan one','First member only',1)",(ids[0],))
    db.execute("INSERT INTO workout_plans(member_id,title,description,active) VALUES (?,'Private plan two','Second member only',1)",(ids[1],))
    db.commit();db.close()
    return dict(codes=codes,staff=staff,members=ids,cid=cid)


def login(client, mobile, kind='member', index=1):
    payload = dict(kind='staff',username='mobile-staff',password='StrongMobile123!') if kind=='staff' else dict(kind='member',reference=f'MOB-{index}',email=f'member{index}@example.com')
    response=client.post(BASE+'/auth/start',json=payload)
    assert response.status_code==200, response.data
    challenge=response.json['challenge']
    response=client.post(BASE+'/auth/verify',json=dict(challenge=challenge,code=mobile['codes'][-1][1]))
    assert response.status_code==200, response.data
    return {'Authorization':'Bearer '+response.json['token']}


def test_api_disabled_by_default(app,client):
    assert client.get(BASE+'/configuration').status_code==404


def test_member_auth_requires_delivered_code_and_is_one_use(client,mobile):
    response=client.post(BASE+'/auth/start',json=dict(kind='member',reference='MOB-1',email='member1@example.com'))
    challenge=response.json['challenge']
    assert client.post(BASE+'/auth/verify',json=dict(challenge=challenge,code='not-a-code')).status_code==401
    body=dict(challenge=challenge,code=mobile['codes'][-1][1])
    assert client.post(BASE+'/auth/verify',json=body).status_code==200
    assert client.post(BASE+'/auth/verify',json=body).status_code==401


def test_otp_attempts_are_bounded(client,mobile):
    response=client.post(BASE+'/auth/start',json=dict(kind='member',reference='MOB-1',email='member1@example.com'))
    challenge=response.json['challenge']
    for _ in range(5): assert client.post(BASE+'/auth/verify',json=dict(challenge=challenge,code='wrong')).status_code==401
    assert client.post(BASE+'/auth/verify',json=dict(challenge=challenge,code=mobile['codes'][-1][1])).status_code==401


def test_unknown_member_does_not_reveal_existence(client,mobile):
    response=client.post(BASE+'/auth/start',json=dict(kind='member',reference='unknown',email='unknown@example.com'))
    assert response.status_code==200 and response.json['challenge']
    assert not mobile['codes']


def test_no_email_fallback_when_delivery_fails(client,mobile,monkeypatch):
    monkeypatch.setattr(mobile_api,'_send_otp_email',lambda *args:False)
    assert client.post(BASE+'/auth/start',json=dict(kind='staff',username='mobile-staff',password='StrongMobile123!')).status_code==503


def test_member_delivery_failure_does_not_reveal_membership(client,mobile,monkeypatch):
    monkeypatch.setattr(mobile_api,'_send_otp_email',lambda *args:False)
    known=client.post(BASE+'/auth/start',json=dict(kind='member',reference='MOB-1',email='member1@example.com'))
    unknown=client.post(BASE+'/auth/start',json=dict(kind='member',reference='unknown',email='unknown@example.com'))
    assert known.status_code==unknown.status_code==200
    assert known.json.keys()==unknown.json.keys()
    assert known.json['message']==unknown.json['message']
    assert client.post(BASE+'/auth/verify',json=dict(challenge=known.json['challenge'],code='123456')).status_code==401


def test_member_can_only_read_own_plan_and_not_staff_lists(client,mobile):
    headers=login(client,mobile)
    data=client.get(BASE+'/member/plans?member_id='+str(mobile['members'][1]),headers=headers).json
    assert [r['title'] for r in data['items']]==['Private plan one']
    assert client.get(BASE+'/staff/members',headers=headers).status_code==401
    assert client.get(BASE+'/home',headers=headers).json['reference']=='MOB-1'


def test_cookie_alone_is_not_mobile_auth(client,mobile):
    with client.session_transaction() as session: session.update(user_id=mobile['staff'],role='admin',tenant_slug='elev8')
    assert client.get(BASE+'/home').status_code==401


def test_logout_revokes_token(client,mobile):
    headers=login(client,mobile)
    assert client.post(BASE+'/auth/logout',json={},headers=headers).status_code==200
    assert client.get(BASE+'/home',headers=headers).status_code==401


def test_booking_repeat_cancel_and_capacity(client,mobile):
    headers=login(client,mobile)
    route=BASE+f"/classes/{mobile['cid']}/booking"
    for _ in range(2):assert client.post(route,json=dict(action='book'),headers=headers).status_code==200
    second=login(client,mobile,index=2)
    assert client.post(route,json=dict(action='book'),headers=second).status_code==409
    # A supplied other-member id is ignored; cancellation is always self-only.
    assert client.post(route,json=dict(action='cancel',member_id=mobile['members'][0]),headers=second).status_code==200
    assert client.get(BASE+'/classes',headers=headers).json['items'][0]['booked'] is True
    assert client.post(route,json=dict(action='cancel'),headers=headers).status_code==200
    assert client.post(route,json=dict(action='book'),headers=second).status_code==200


def test_web_and_mobile_share_booking_capacity_and_duplicate_rule(app,client,mobile):
    headers=login(client,mobile)
    route=BASE+f"/classes/{mobile['cid']}/booking"
    assert client.post(route,json=dict(action='book'),headers=headers).status_code==200
    db=sqlite3.connect(app.config['DATABASE_PATH'])
    db.execute("UPDATE users SET role='admin',department='Management' WHERE id=?",(mobile['staff'],));db.commit();db.close()
    web=app.test_client();web.set_cookie('onecpase_tenant','elev8')
    with web.session_transaction() as sess:
        sess.update(user_id=mobile['staff'],role='admin',tenant_slug='elev8',must_change_password=False)
    assert web.post(f"/fitness/{mobile['cid']}/book",data=dict(member_id=mobile['members'][1])).status_code==302
    assert web.post(f"/fitness/{mobile['cid']}/book",data=dict(member_id=mobile['members'][0])).status_code==302
    db=sqlite3.connect(app.config['DATABASE_PATH'])
    assert db.execute("SELECT COUNT(*) FROM class_bookings WHERE class_id=? AND status='booked'",(mobile['cid'],)).fetchone()[0]==1
    old_id=db.execute('SELECT id FROM class_bookings WHERE class_id=?',(mobile['cid'],)).fetchone()[0]
    db.close()
    assert client.post(route,json=dict(action='cancel'),headers=headers).status_code==200
    assert client.post(route,json=dict(action='book'),headers=headers).status_code==200
    assert web.post(f'/fitness/bookings/{old_id}/cancel').status_code==302
    assert client.get(BASE+'/classes',headers=headers).json['items'][0]['booked'] is True


def test_member_phase_and_enablement_enforced(app,client,mobile):
    headers=login(client,mobile)
    app.config['ACTIVE_PHASE']='1'
    assert client.get(BASE+'/classes',headers=headers).status_code==403
    assert client.get(BASE+'/member/plans',headers=headers).status_code==403
    app.config['ACTIVE_PHASE']='0'
    assert client.get(BASE+'/member/account',headers=headers).status_code==403
    assert client.get(BASE+'/home',headers=headers).status_code==403
    app.config['MOBILE_MEMBER_LOGIN_ENABLED']=False
    assert client.get(BASE+'/home',headers=headers).status_code==403


def test_staff_permission_refresh_and_disabled_account(app,client,mobile):
    headers=login(client,mobile,kind='staff')
    assert 'leads' in client.get(BASE+'/home',headers=headers).json['features']
    assert client.get(BASE+'/member/account',headers=headers).status_code==401
    db=sqlite3.connect(app.config['DATABASE_PATH'])
    db.execute("UPDATE users SET department='Fitness' WHERE id=?",(mobile['staff'],));db.commit()
    assert client.get(BASE+'/staff/leads',headers=headers).status_code==403
    db.execute('UPDATE users SET active=0 WHERE id=?',(mobile['staff'],));db.commit();db.close()
    assert client.get(BASE+'/home',headers=headers).status_code==401


def test_mobile_token_not_valid_in_other_tenant(app,client,mobile):
    headers=login(client,mobile)
    assert client.get('/api/mobile/v1/no-such-gym/home',headers=headers).status_code==404
    db=sqlite3.connect(app.config['PLATFORM_DATABASE_PATH'])
    db.execute('INSERT INTO tenants(slug,name,db_path,active) VALUES (?,?,?,1)',
        ('second','Second gym',app.config['DATABASE_PATH']))
    db.commit();db.close()
    # Even a duplicated database or mistaken shared path cannot reuse a bearer
    # for a different registered tenant slug.
    assert client.get('/api/mobile/v1/second/home',headers=headers).status_code==401
    assert client.get(BASE+'/home',headers={'Authorization':'Bearer invalid'}).status_code==401


def test_expired_session_and_changed_email_are_rejected(app,client,mobile):
    headers=login(client,mobile)
    db=sqlite3.connect(app.config['DATABASE_PATH'])
    db.execute('UPDATE mobile_sessions SET expires_at=0');db.commit()
    assert client.get(BASE+'/home',headers=headers).status_code==401
    headers=login(client,mobile)
    db.execute('UPDATE members SET email=? WHERE id=?',('changed@example.com',mobile['members'][0]));db.commit();db.close()
    assert client.get(BASE+'/home',headers=headers).status_code==401


def test_secret_rotation_revokes_mobile_sessions(app,client,mobile):
    headers=login(client,mobile)
    app.config['SECRET_KEY']='rotated-test-secret'
    assert client.get(BASE+'/home',headers=headers).status_code==401


def test_malformed_json_and_no_cache(client,mobile):
    assert client.post(BASE+'/auth/start',json=[]).status_code==400
    assert client.get(BASE+'/configuration').headers['Cache-Control']=='no-store'
