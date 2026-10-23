"""Read-back policy/dates and verify authenticated screens on a protected DB copy."""
from pathlib import Path
from types import SimpleNamespace
import json,shlex,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import deploy_beta as deploy

READ='''
import sqlite3,json
c=sqlite3.connect('file:/var/data/onecpase/1cpase.db?mode=ro',uri=True);c.row_factory=sqlite3.Row
policy=dict(c.execute("SELECT * FROM hr_policy_settings WHERE name='annual_working_days'").fetchone())
assert policy['value']=='21'
dates=[dict(r) for r in c.execute("SELECT u.employee_number,p.start_date FROM users u JOIN hr_profiles p ON p.user_id=u.id WHERE u.employee_number IN ('1008','1018','1007') ORDER BY u.employee_number")]
assert {r['employee_number']:r['start_date'] for r in dates}=={'1008':'2026-08-01','1018':'2026-01-01','1007':'2026-09-01'}
print(json.dumps({'policy':policy,'dates':dates}))
'''

if __name__=='__main__':
    out=ROOT/'artifacts/hr-annual21';stage=(out/'stage.txt').read_text().strip()
    client=deploy.connect(SimpleNamespace(host=deploy.DEFAULT_HOST,user='root',key=deploy.DEFAULT_KEY))
    try:
        data=json.loads(deploy.run(client,'/opt/onecpase/venv/bin/python -c '+shlex.quote(READ)))
        source=(ROOT/'scripts/hr_release_remote.py').read_text()
        source=source.replace("stage.name.startswith('hr-workforce-')","stage.name.startswith(('hr-workforce-','hr-annual21-'))")
        needle="            assert response.status_code==200,(path,response.status_code)\n"
        assert source.count(needle)==1
        source=source.replace(needle,needle+"            if path=='/hr/me':\n                assert b'21 working days per year' in response.data and b'1.75 days earned' in response.data\n")
        sftp=client.open_sftp()
        with sftp.open(stage+'/verify-policy.py','wb') as handle:handle.write(source.encode())
        sftp.close()
        checks=json.loads(deploy.run(client,'/opt/onecpase/venv/bin/python '+shlex.quote(stage+'/verify-policy.py')+' '+shlex.quote(stage)+' verify'))
        data['authenticated_checks']=len(checks['authenticated_checks']);data['integrity']=checks['integrity']
        (out/'verified.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
        print(json.dumps(data,indent=2))
    finally:client.close()
