"""Existing browser cookies must never retain revoked authority."""
import pytest
from werkzeug.security import generate_password_hash

from onecpase.database import get_db
from onecpase.auth import _complete_login


def sign_in(app, client, role='admin', permissions=()):
    with app.app_context():
        db = get_db()
        uid = db.execute("""INSERT INTO users(username,password_hash,full_name,email,role,department)
            VALUES ('revocation',?,'Revocation Test','test@example.com',?,'General')""",
            (generate_password_hash('OriginalPassword123'), role)).lastrowid
        for permission in permissions:
            db.execute('INSERT INTO user_permissions(user_id,permission) VALUES (?,?)',(uid,permission))
        db.commit()
    with app.test_request_context('/'):
        _complete_login(get_db().execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
        from flask import session
        saved = dict(session)
    with client.session_transaction() as session:
        session.update(saved)
    return uid


@pytest.mark.parametrize('mutation', ['disable', 'delete'])
@pytest.mark.parametrize('method,path', [('get','/members'),('post','/operations/add')])
def test_revoked_identity_cannot_read_or_write(app,client,mutation,method,path):
    uid=sign_in(app,client)
    with app.app_context():
        db=get_db()
        db.execute('DELETE FROM users WHERE id=?' if mutation=='delete' else
                   'UPDATE users SET active=0 WHERE id=?',(uid,)); db.commit()
    response=getattr(client,method)(path,json={'name':'Forbidden equipment'})
    assert response.status_code==401
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM equipment').fetchone()[0]==0
    with client.session_transaction() as session:
        assert 'user_id' not in session


def test_demotion_refreshes_authority_without_a_dashboard_visit(app,client):
    uid=sign_in(app,client)
    with app.app_context():
        db=get_db(); db.execute("UPDATE users SET role='manager' WHERE id=?",(uid,)); db.commit()
    response=client.get('/admin/')
    assert response.status_code in (302,403)
    with client.session_transaction() as session:
        assert session['role']=='manager'


def test_removed_permission_is_effective_on_next_post(app,client):
    uid=sign_in(app,client,'staff',('equipment',))
    with app.app_context():
        db=get_db(); db.execute('DELETE FROM user_permissions WHERE user_id=?',(uid,)); db.commit()
    assert client.post('/operations/add',json={'name':'Forbidden'}).status_code==403


def test_password_version_revokes_legacy_and_current_cookies(app,client):
    uid=sign_in(app,client)
    with client.session_transaction() as session:
        session.pop('session_version',None)  # A cookie issued before the additive migration.
    with app.app_context():
        db=get_db(); db.execute('UPDATE users SET session_version=session_version+1 WHERE id=?',(uid,)); db.commit()
    assert client.get('/members',json={}).status_code==401


def test_own_password_change_keeps_only_current_session(app,client):
    uid=sign_in(app,client)
    other=app.test_client()
    with client.session_transaction() as session:
        saved=dict(session)
    with other.session_transaction() as session:
        session.update(saved)
    response=client.post('/change-password',data={'current_password':'OriginalPassword123',
        'new_password':'ReplacementPassword123','confirm_password':'ReplacementPassword123'})
    assert response.status_code==302
    assert client.get('/training').status_code==200
    assert other.get('/training',json={}).status_code==401


def test_current_password_change_requirement_replaces_cached_false(app,client):
    uid=sign_in(app,client)
    with app.app_context():
        db=get_db(); db.execute('UPDATE users SET must_change_password=1 WHERE id=?',(uid,)); db.commit()
    response=client.get('/members')
    assert response.status_code==302
    assert response.location.endswith('/change-password')


def test_tenant_deactivation_is_not_hidden_by_cache(app,client):
    uid=sign_in(app,client)
    client.post('/select-tenant',data={'tenant_slug':'elev8'})
    with client.session_transaction() as session:
        session.update(user_id=uid,role='admin',tenant_slug='elev8')
    assert client.get('/training').status_code==200
    with app.app_context():
        from onecpase.platform_db import get_platform_db
        db=get_platform_db(); db.execute("UPDATE tenants SET active=0 WHERE slug='elev8'"); db.commit()
    assert client.get('/training',json={}).status_code==401


def test_admin_password_reset_revokes_an_existing_target_cookie(app,client):
    sign_in(app,client)
    with app.app_context():
        db=get_db()
        target=db.execute("INSERT INTO users(username,password_hash,full_name,email,role,active) VALUES ('reset-target','x','Reset Target','target@example.com','staff',1)").lastrowid
        db.commit()
    other=app.test_client()
    with other.session_transaction() as session:
        session.update(user_id=target,role='staff',tenant_slug=None,session_version=0)
    assert other.get('/training').status_code==200
    assert client.post(f'/admin/users/{target}/reset-password').status_code==302
    assert other.get('/training',json={}).status_code==401


@pytest.mark.parametrize('kind',['login','reset'])
def test_pending_email_challenge_cannot_outlive_credentials(app,client,kind):
    import time
    from onecpase.auth import _challenge_hash,_reset_challenge_hash,_otp_hash
    from onecpase.tenants import TENANT_COOKIE_NAME
    from werkzeug.security import check_password_hash
    uid=sign_in(app,client)
    token,code='pending-before-reset','123456'
    hashed=(_challenge_hash if kind=='login' else _reset_challenge_hash)(token)
    with app.app_context():
        db=get_db()
        db.execute('INSERT INTO login_otp_challenges(challenge_hash,user_id,code_hash,expires_at,attempts,session_version) VALUES (?,?,?,?,0,0)',
                   (hashed,uid,_otp_hash(hashed,code),time.time()+300))
        db.execute('UPDATE users SET session_version=session_version+1 WHERE id=?',(uid,)); db.commit()
    pending=app.test_client()
    pending.set_cookie(TENANT_COOKIE_NAME,'elev8')
    with pending.session_transaction() as session:
        session['_otp_challenge' if kind=='login' else '_reset_challenge']=token
    if kind=='login':
        response=pending.post('/login/verify',data={'otp':code})
    else:
        response=pending.post('/reset-password',data={'code':code,'new_password':'AttemptedReplacement123','confirm_password':'AttemptedReplacement123'})
    assert response.status_code==302
    with pending.session_transaction() as session:
        assert 'user_id' not in session
    with app.app_context():
        assert check_password_hash(get_db().execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()[0],'OriginalPassword123')


@pytest.mark.parametrize('tenant_mode',[False,True])
def test_platform_password_reset_revokes_platform_and_tenant_authority(app,client,tenant_mode):
    from test_platform_power_user import _seed_power_user,_platform_login
    from onecpase.platform_db import get_platform_db
    tenant=_seed_power_user(app)
    _platform_login(client)
    if tenant_mode:
        client.post(f'/platform/tenants/{tenant}/enter')
    with app.app_context():
        db=get_platform_db()
        db.execute("UPDATE platform_admins SET password_hash=? WHERE username='owner'",(generate_password_hash('ChangedPlatformPassword123'),)); db.commit()
    response=client.get('/training' if tenant_mode else '/platform/tenants')
    assert response.status_code==302
    with client.session_transaction() as session:
        assert 'platform_admin_id' not in session
        assert 'user_id' not in session
