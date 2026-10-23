"""Scoped annual entitlement deployment and explicitly confirmed date updates."""
from pathlib import Path
import hashlib,json,os,shutil,sqlite3,subprocess,sys,time
from urllib.request import urlopen

root=Path('/opt/onecpase');stage=Path(sys.argv[1]).resolve()
assert stage.parent==Path('/opt/onecpase-backups') and stage.name.startswith('hr-annual21-')
manifest=json.loads((stage/'manifest.json').read_text())
db_path=Path('/var/data/onecpase/1cpase.db')

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

for item in manifest:
    assert digest(root/item['path'])==item['before'],'Live file changed'
    assert digest(stage/'incoming'/item['path'])==item['after'],'Staged file changed'
    if item['path'].endswith('.py'):compile((stage/'incoming'/item['path']).read_bytes(),item['path'],'exec')

installed=[];subprocess.run(['systemctl','stop','onecpase'],check=True)
try:
    with sqlite3.connect(db_path) as source,sqlite3.connect(stage/'database-before.db') as backup:
        source.backup(backup)
        assert backup.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    os.chmod(stage/'database-before.db',0o600)
    for item in manifest:
        target=root/item['path'];before=stage/'before'/item['path'];before.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(target,before)
        temp=target.with_name(target.name+'.annual21-stage');shutil.copy2(stage/'incoming'/item['path'],temp)
        stat=target.stat();os.chown(temp,stat.st_uid,stat.st_gid);os.chmod(temp,stat.st_mode&0o777)
        os.replace(temp,target);installed.append(item)
    os.environ.update(PYTHON_DOTENV_DISABLED='1',APP_ENV='testing',SCHEDULER_ENABLED='0',WHATSAPP_WEB_ENABLED='0')
    sys.path.insert(0,str(root))
    from onecpase.hr_workforce import migrate
    db=sqlite3.connect(db_path);db.row_factory=sqlite3.Row
    migrate(db)
    # Rehearse all data mutations on a disposable copy first; production commit is last.
    def configure(c):
        c.execute('BEGIN IMMEDIATE')
        changes=[]
        for number,start in [('1008','2026-08-01'),('1018','2026-01-01'),('1007','2026-09-01')]:
            person=c.execute('SELECT u.id,p.* FROM users u JOIN hr_profiles p ON p.user_id=u.id WHERE u.employee_number=?',(number,)).fetchall()
            assert len(person)==1,'Employee number must be unique'
            p=person[0]
            if p['start_date']!=start:
                assert not c.execute('SELECT 1 FROM hr_accounts WHERE user_id=?',(p['user_id'],)).fetchone(),'Review existing leave accounts before changing start date'
                c.execute('UPDATE hr_profiles SET start_date=?,version=version+1 WHERE user_id=?',(start,p['user_id']))
                c.execute('UPDATE staff SET start_date=? WHERE id=?',(start,p['staff_id']))
                changes.append({'user_id':p['user_id'],'start_date':start,'previous':p['start_date']})
        policy='Company annual entitlement: 21 working days/year; 1.75 days/month. Explicit owner confirmation 16 October 2026.'
        c.execute("INSERT INTO hr_policy_settings(name,value,effective_date,source) VALUES ('annual_working_days','21','2026-10-16',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value,effective_date=excluded.effective_date,source=excluded.source",(policy,))
        accounts=[dict(r) for r in c.execute("SELECT id,user_id,annual_days FROM hr_accounts WHERE leave_type='annual' AND cycle_end>='2026-10-16'")]
        c.execute("UPDATE hr_accounts SET annual_days=21,policy='COMPANY-ANNUAL21-20261016' WHERE leave_type='annual' AND cycle_end>='2026-10-16'")
        payload=json.dumps({'dates':changes,'annual_working_days':21,'monthly_days':1.75,'previous_account_rates':accounts,'opening_balances_unchanged':True})
        from datetime import datetime,timezone
        c.execute('INSERT INTO hr_audit(actor_id,subject,action,payload,created_at) VALUES (?,?,?,?,?)',(1,'company_annual_policy','owner_confirmed_policy_and_dates',payload,datetime.now(timezone.utc).isoformat()))
        return {'dates':changes,'annual_working_days':21,'monthly_days':1.75,'updated_annual_accounts':len(accounts),'opening_balances_unchanged':True}
    copy=sqlite3.connect(stage/'data-rehearsal.db');copy.row_factory=sqlite3.Row;db.backup(copy)
    configure(copy);copy.commit();assert copy.execute('PRAGMA integrity_check').fetchone()[0]=='ok';copy.close()
    result=configure(db);db.commit();assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok';db.close()
    subprocess.run(['systemctl','start','onecpase'],check=True)
    for attempt in range(30):
        try:
            with urlopen('http://127.0.0.1:5002/healthz',timeout=5) as response:
                if json.load(response).get('status')=='ok':break
        except Exception:pass
        time.sleep(1)
    else:raise RuntimeError('Health check failed')
    result.update(deployed=True,files=len(manifest),backup=str(stage),health='ok')
    (stage/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
except BaseException:
    subprocess.run(['systemctl','stop','onecpase'],check=False)
    for item in installed:shutil.copy2(stage/'before'/item['path'],root/item['path'])
    subprocess.run(['systemctl','start','onecpase'],check=False)
    raise
