"""Read the served Elev8 tenant and verify candidate identities."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import deploy_beta as deploy
review=json.loads((ROOT/'artifacts/completed-mandate-cancellation-review-20261014.json').read_text())
refs=sorted({str(r['itensity_ref']) for r in review['rows'] if r['blockers']==['Current payment and attendance coverage through 14 October not verified'] and r.get('member_status')!='Cancelled'})
code="""
import json,os,sqlite3
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('/opt/onecpase/.env')
from onecpase.encryption import decrypt
registry=sqlite3.connect('file:'+os.environ.get('PLATFORM_DATABASE_PATH','/var/data/onecpase/platform.db')+'?mode=ro',uri=True)
tenants=registry.execute("SELECT db_path FROM tenants WHERE slug='elev8' AND active=1").fetchall()
assert len(tenants)==1
p=Path(tenants[0][0]);p=(p if p.is_absolute() else Path('/opt/onecpase')/p).resolve()
assert p.is_relative_to(Path('/var/data/onecpase'))
db=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
refs=REFS
members=[]
for ref in refs:
    for row in db.execute('SELECT * FROM members WHERE itensity_ref=?',(ref,)):
        item={k:row[k] for k in ['id','member_ref','itensity_ref','member_status']}
        for k in ['first_name','last_name','contact','id_number']:item[k]=decrypt(row[k])
        item['payments']=[dict(p) for p in db.execute('SELECT collection_date,amount_paid,method,notes,status FROM collections WHERE member_id=? AND amount_paid>0',(row['id'],))]
        members.append(item)
print(json.dumps({'database':str(p),'members':members,'audit_columns':[r[1] for r in db.execute('pragma table_info(audit_logs)')]}))
""".replace('REFS',repr(refs))
client=deploy.connect(SimpleNamespace(host=deploy.DEFAULT_HOST,user='root',key=deploy.DEFAULT_KEY))
try:
    stdin,stdout,stderr=client.exec_command('cd /opt/onecpase && venv/bin/python -')
    stdin.write(code);stdin.channel.shutdown_write()
    output=stdout.read().decode();error=stderr.read().decode()
    assert stdout.channel.recv_exit_status()==0, 'Remote read failed'
    result=json.loads(output)
    (ROOT/'artifacts/cancellation-target-snapshot-20261014.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('Target references:',len(refs),'Matched records:',len(result['members']))
    print('Status counts:',{s:sum(m['member_status']==s for m in result['members']) for s in {m['member_status'] for m in result['members']}})
finally:client.close()
