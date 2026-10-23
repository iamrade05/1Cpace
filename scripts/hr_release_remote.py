"""Remote scoped release: rehearsal, verified backup, additive HR upgrade and rollback."""
import hashlib,json,os,shutil,sqlite3,subprocess,sys,time
from pathlib import Path
from urllib.request import urlopen

stage=Path(sys.argv[1]).resolve(); mode=sys.argv[2]
assert stage.parent==Path('/opt/onecpase-backups') and stage.name.startswith('hr-workforce-')
app=Path('/opt/onecpase'); database=Path('/var/data/onecpase/1cpase.db')
manifest=json.loads((stage/'manifest.json').read_text())
os.umask(0o077)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_inputs():
    for item in manifest:
        target=app/item['path']; incoming=stage/'incoming'/item['path']
        assert sha(incoming)==item['after'],'Staged content changed: '+item['path']
        assert (sha(target) if target.exists() else None)==item['before'],'Live content changed: '+item['path']
        if incoming.suffix=='.py':
            compile(incoming.read_bytes(),str(incoming),'exec')


def backup_db(source,dest):
    with sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True) as src, sqlite3.connect(dest) as out:
        src.backup(out)
        assert out.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    os.chmod(dest,0o600)


def counts(path):
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
        names=['users','members','debicheck_mandates','leave_requests','attendance','timesheets','payroll']
        existing={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in names if name in existing}


def verify_copy(root,source_db):
    """Full application and authorised/unauthorised GET checks on a copied DB."""
    from dotenv import dotenv_values
    cfg=dotenv_values(app/'.env')
    os.environ.update(PYTHON_DOTENV_DISABLED='1',APP_ENV='testing',SCHEDULER_ENABLED='0',WHATSAPP_WEB_ENABLED='0',
                      TURNSTILE_ENABLED='0',RATELIMIT_STORAGE_URL='memory://')
    sys.path.insert(0,str(root))
    scratch=stage/('verify-'+mode); scratch.mkdir(exist_ok=True)
    copy=scratch/'app.db'; backup_db(source_db,copy)
    from onecpase import create_app
    from onecpase.database import get_db
    from onecpase.hr_workforce import migrate,ensure_profile,service_account
    settings=dict(TESTING=True,ENVIRONMENT='testing',DATABASE_URL='',DATABASE_PATH=str(copy),
        PLATFORM_DATABASE_PATH=str(scratch/'platform.db'),TENANTS_DIR=str(scratch/'tenants'),
        UPLOAD_FOLDER=str(scratch/'uploads'),SECRET_KEY='isolated-hr-release-verification',
        ENCRYPTION_KEY=cfg['ENCRYPTION_KEY'],WTF_CSRF_ENABLED=False,TURNSTILE_ENABLED=False,
        ACTIVE_PHASE=cfg.get('ACTIVE_PHASE','4'),SCHEDULER_ENABLED=False,WHATSAPP_WEB_ENABLED=False)
    testapp=create_app(settings)
    with testapp.app_context():
        db=get_db(); migrate(db); migrate(db)
        staff=[dict(r) for r in db.execute("SELECT * FROM users WHERE active=1 AND COALESCE(is_platform_user,0)=0") if not service_account(r)]
        assert staff,'No employee accounts found'
        for user in staff:
            ensure_profile(db,user['id'])
        db.commit()
        from onecpase.hr_workforce import is_admin
        from flask import session
        admins=[]; employees=[]
        for user in staff:
            with testapp.test_request_context('/'):
                session['user_id']=user['id']
                (admins if is_admin(db) else employees).append(user)
        assert admins and employees,'Need both HR reviewer and normal staff for verification'
    checked=[]
    for user,admin in [(admins[0],True),(employees[0],False)]:
        client=testapp.test_client()
        with client.session_transaction() as sess:
            sess.update(user_id=user['id'],role=user['role'],permissions=[],tenant_slug=None)
        paths=['/hr/','/hr/me','/hr/leave','/hr/leave/request','/hr/weeks','/hr/balances','/hr/roster']
        if admin:
            paths+=['/hr/people','/hr/leave/calendar','/hr/reviews','/hr/access-attendance']
        for path in paths:
            response=client.get(path,follow_redirects=True)
            assert response.status_code==200,(path,response.status_code)
            checked.append({'path':path,'scope':'hr' if admin else 'employee','status':response.status_code})
        if not admin:
            for path in ['/hr/people','/hr/leave/calendar','/hr/employees']:
                assert client.get(path).status_code==403,('Permission leak',path)
            assert client.post('/hr/timesheets/save',data={'staff_id':1}).status_code==409
    assert counts(source_db)==counts(copy),'Rehearsal changed existing business counts'
    return {'authenticated_checks':checked,'employee_count':len(staff),'integrity':'ok'}


if mode=='rehearse':
    check_inputs()
    root=stage/'rehearsal'; root.mkdir(exist_ok=True)
    shutil.copytree(app/'onecpase',root/'onecpase',ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
    for item in manifest:
        target=root/item['path']; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(stage/'incoming'/item['path'],target)
    result=verify_copy(root,database)
    (stage/'rehearsal.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)
    raise SystemExit(0)

if mode=='verify':
    result=verify_copy(app,database)
    (stage/'verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)
    raise SystemExit(0)

assert mode=='deploy' and (stage/'rehearsal.json').is_file()
check_inputs()
installed=[]; before_counts=None; stopped=False
try:
    subprocess.run(['systemctl','stop','onecpase'],check=True); stopped=True
    before_counts=counts(database)
    backup_db(database,stage/'database-before.db')
    # Restore rehearsal proves the backup is readable; originals stay untouched.
    backup_db(stage/'database-before.db',stage/'restore-check.db')
    assert counts(stage/'restore-check.db')==before_counts
    for item in manifest:
        target=app/item['path']
        if target.exists():
            backup=stage/'before'/item['path']; backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(target,backup)
        target.parent.mkdir(parents=True,exist_ok=True)
        temp=target.with_name(target.name+'.hr-release-stage')
        shutil.copy2(stage/'incoming'/item['path'],temp)
        owner=(app/'onecpase/hr.py').stat()
        os.chown(temp,owner.st_uid,owner.st_gid); os.chmod(temp,0o644)
        os.replace(temp,target); installed.append(item)
    # Only the new additive migration is run directly; startup retains its normal initializer.
    sys.path.insert(0,str(app)); os.environ['PYTHON_DOTENV_DISABLED']='1'
    os.environ['APP_ENV']='testing'; os.environ['SCHEDULER_ENABLED']='0'; os.environ['WHATSAPP_WEB_ENABLED']='0'
    from onecpase.hr_workforce import migrate,ensure_profile,service_account
    conn=sqlite3.connect(database); conn.row_factory=sqlite3.Row
    try:
        migrate(conn)
        for user in conn.execute("SELECT * FROM users WHERE active=1 AND COALESCE(is_platform_user,0)=0").fetchall():
            if not service_account(user):
                ensure_profile(conn,user['id'])
        conn.commit()
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        profiles=conn.execute('SELECT COUNT(*) FROM hr_profiles').fetchone()[0]
        for table in ['hr_accounts','hr_ledger','hr_requests','hr_weeks','hr_signatures']:
            assert conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0,'Unexpected operational HR data'
    finally:
        conn.close()
    assert counts(database)==before_counts,'Existing operational records changed'
    subprocess.run(['systemctl','start','onecpase'],check=True)
    healthy=False
    for attempt in range(30):
        try:
            with urlopen('http://127.0.0.1:5002/healthz',timeout=5) as response:
                healthy=json.load(response).get('status')=='ok'
            if healthy:
                break
        except Exception:
            pass
        time.sleep(1)
    assert healthy,'Service failed health verification'
    assert counts(database)==before_counts,'Startup changed existing business counts'
    for item in manifest:
        assert sha(app/item['path'])==item['after'],'Deployed file mismatch'
    # Test a fresh subprocess: no cached rehearsal modules or production mutation.
    command=[sys.executable,str(stage/'remote.py'),str(stage),'verify']
    verified=subprocess.run(command,check=True,capture_output=True,text=True)
    checks=json.loads((stage/'verification.json').read_text())
    report={'deployed':True,'backup':str(stage),'files':len(installed),'profiles':profiles,
            'counts_preserved':before_counts,'opening_balances':'unconfirmed','health':'ok',
            'authenticated_checks':len(checks['authenticated_checks'])}
    (stage/'deployment.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)
except BaseException:
    if stopped:
        subprocess.run(['systemctl','stop','onecpase'],check=False)
        for item in installed:
            target=app/item['path']
            if item['before'] is None:
                target.unlink(missing_ok=True)
            else:
                shutil.copy2(stage/'before'/item['path'],target)
        # Migration is additive: preserve the empty new schema rather than rewind business data.
        subprocess.run(['systemctl','start','onecpase'],check=False)
    raise
