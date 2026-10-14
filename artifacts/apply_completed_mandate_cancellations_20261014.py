"""Apply an evidence-backed cancellation payload to the served tenant."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import deploy_beta as deploy

payload=json.loads((ROOT/'artifacts/verified-cancellation-payload-20261014.json').read_text())
assert payload['verified'] and payload['through_date']=='2026-10-14'
assert len({m['id'] for m in payload['members']})==len(payload['members'])
code='''
import os,json,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from dotenv import load_dotenv
load_dotenv('/opt/onecpase/.env')
from onecpase.encryption import decrypt
payload=PAYLOAD
registry=sqlite3.connect('file:'+os.environ.get('PLATFORM_DATABASE_PATH','/var/data/onecpase/platform.db')+'?mode=ro',uri=True)
paths=registry.execute("SELECT db_path FROM tenants WHERE slug='elev8' AND active=1").fetchall()
assert len(paths)==1
path=Path(paths[0][0]);path=(path if path.is_absolute() else Path('/opt/onecpase')/path).resolve()
assert path.is_relative_to(Path('/var/data/onecpase')) and str(path)==payload['database']
db=sqlite3.connect(path,timeout=30);db.row_factory=sqlite3.Row
stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
folder=Path('/opt/onecpase-backups')/('completed_mandate_cancellations_'+stamp)
folder.mkdir(mode=0o700)
backup=folder/'elev8_before.db'
with sqlite3.connect(backup) as target:db.backup(target)
os.chmod(backup,0o600)
(folder/'payload.json').write_text(json.dumps(payload,indent=2));os.chmod(folder/'payload.json',0o600)
changed=[]
db.execute('BEGIN IMMEDIATE')
try:
    for target in payload['members']:
        matches=db.execute('SELECT * FROM members WHERE itensity_ref=?',(target['itensity_ref'],)).fetchall()
        assert len(matches)==1
        row=matches[0]
        assert row['id']==target['id'] and row['member_status']==target['member_status']
        assert decrypt(row['first_name'])==target['first_name'] and decrypt(row['last_name'])==target['last_name']
        assert decrypt(row['id_number'])==target['id_number']
        # Prevent a new payment recorded in this app since the reviewed snapshot.
        current=[dict(p) for p in db.execute('SELECT collection_date,amount_paid,method,notes,status FROM collections WHERE member_id=? AND amount_paid>0',(row['id'],))]
        assert current==target['payments'],'Payment evidence changed during review'
        assert target['evidence']['last_visit'][:10]<target['evidence']['last_debit_date']
        assert target['evidence']['later_successful_payments']==0
        cursor=db.execute("UPDATE members SET member_status='Cancelled' WHERE id=? AND member_status=?",(row['id'],row['member_status']))
        assert cursor.rowcount==1
        db.execute('INSERT INTO audit_logs (action,old_value,new_value) VALUES (?,?,?)',
          ('completed_mandate_cancellation',json.dumps({'member_id':row['id'],'member_status':row['member_status']}),
           json.dumps({'member_id':row['id'],'member_status':'Cancelled','evidence':target['evidence'],'source_retrieved_at':payload['retrieved_at']})))
        changed.append({'id':row['id'],'member_ref':row['member_ref'],'itensity_ref':row['itensity_ref'],'old_status':row['member_status'],'new_status':'Cancelled'})
    db.commit()
except BaseException:
    db.rollback()
    raise
for target in changed:
    assert db.execute('SELECT member_status FROM members WHERE id=?',(target['id'],)).fetchone()[0]=='Cancelled'
result={'updated':len(changed),'backup':str(backup),'members':changed,'verified':True}
(folder/'result.json').write_text(json.dumps(result,indent=2));os.chmod(folder/'result.json',0o600)
print(json.dumps(result))
'''.replace('PAYLOAD',repr(payload))
client=deploy.connect(SimpleNamespace(host=deploy.DEFAULT_HOST,user='root',key=deploy.DEFAULT_KEY))
try:
    stdin,stdout,stderr=client.exec_command('cd /opt/onecpase && venv/bin/python -')
    stdin.write(code);stdin.channel.shutdown_write()
    output=stdout.read().decode();error=stderr.read().decode()
    if stdout.channel.recv_exit_status()!=0:
        (ROOT/'artifacts/cancellation-apply-error-20261014.txt').write_text(error,encoding='utf-8')
        raise RuntimeError('Cancellation transaction failed; see saved error evidence')
    result=json.loads(output)
    (ROOT/'artifacts/completed-mandate-cancellation-result-20261014.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('Updated:',result['updated'],'Verification:',result['verified'],'Backup:',result['backup'])
finally:client.close()
