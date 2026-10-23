"""Read/dry-run/apply the specifically authorised HR reporting and date setup."""
from pathlib import Path
from types import SimpleNamespace
import json,shlex,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import deploy_beta as deploy

READ="""
import sqlite3,json
c=sqlite3.connect('file:/var/data/onecpase/1cpase.db?mode=ro',uri=True);c.row_factory=sqlite3.Row
print(json.dumps([dict(r) for r in c.execute('''SELECT u.id,u.full_name,u.username,u.department,u.employee_number,u.role,
    p.start_date,p.weekdays,p.manager_id,p.employment_type,p.version FROM users u LEFT JOIN hr_profiles p ON p.user_id=u.id
    WHERE u.active=1 AND COALESCE(u.is_platform_user,0)=0 ORDER BY u.id''')]))
"""

APPLY="""
import sqlite3,json,sys,datetime,os
from pathlib import Path
plan=json.loads(sys.argv[1]);path=Path('/var/data/onecpase/1cpase.db')
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
folder=Path('/opt/onecpase-backups')/('hr-reporting-'+stamp);folder.mkdir(mode=0o700)
c=sqlite3.connect(path);c.row_factory=sqlite3.Row
with sqlite3.connect(folder/'database-before.db') as backup:
    c.backup(backup)
os.chmod(folder/'database-before.db',0o600)
c.execute('BEGIN IMMEDIATE')
changed=[]
try:
    for item in plan['employees']:
        row=c.execute('SELECT * FROM hr_profiles WHERE user_id=?',(item['user_id'],)).fetchone()
        assert row and row['version']==item['version'],'Profile changed; refresh before applying'
        updates={k:v for k,v in item['updates'].items() if row[k]!=v}
        if not updates:continue
        if 'start_date' in updates:
            assert not c.execute('SELECT 1 FROM hr_accounts WHERE user_id=?',(item['user_id'],)).fetchone(),'Confirmed balances require a reviewed date correction'
        before={k:row[k] for k in updates}
        c.execute('UPDATE hr_profiles SET '+','.join(k+'=?' for k in updates)+',version=version+1 WHERE user_id=?',(*updates.values(),item['user_id']))
        if 'start_date' in updates and row['staff_id']:
            c.execute('UPDATE staff SET start_date=? WHERE id=?',(updates['start_date'],row['staff_id']))
        if 'manager_id' in updates and updates['manager_id']:
            seen={item['user_id']};manager=updates['manager_id']
            while manager:
                assert manager not in seen,'Reporting cycle detected'
                seen.add(manager)
                parent=c.execute('SELECT manager_id FROM hr_profiles WHERE user_id=?',(manager,)).fetchone()
                manager=parent['manager_id'] if parent else None
        payload={'before':before,'after':updates,'source':'User supplied HR reporting lines and exact employment dates, 16 October 2026'}
        c.execute('INSERT INTO hr_audit(actor_id,subject,action,payload,created_at) VALUES (?,?,?,?,?)',
            (1,str(item['user_id']),'authorised_reporting_setup',json.dumps(payload),datetime.datetime.now(datetime.timezone.utc).isoformat()))
        changed.append({'user_id':item['user_id'],'name':item['name'],'updates':updates})
    for uid in plan['approvers']:
        for permission in ('hr_leave_approve','hr_timesheet_approve'):
            c.execute('INSERT OR IGNORE INTO user_permissions(user_id,permission) VALUES (?,?)',(uid,permission))
        c.execute('INSERT INTO hr_audit(actor_id,subject,action,payload,created_at) VALUES (?,?,?,?,?)',
            (1,str(uid),'reporting_approval_permissions',json.dumps({'permissions':['hr_leave_approve','hr_timesheet_approve'],'source':'User assigned reporting manager; own-report approval only, no self approval'}),datetime.datetime.now(datetime.timezone.utc).isoformat()))
    c.commit()
except BaseException:
    c.rollback();raise
result={'changed':changed,'backup':str(folder),'approvers':plan['approvers'],'leave_balances_modified':False}
(folder/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
"""


def make_plan(rows):
    by_number={r['employee_number']:r for r in rows if r['employee_number']}
    mvuleni,ayrton,thato=[by_number[n]['id'] for n in ('1001','1011','1008')]
    employees=[]
    for row in by_number.values():
        changes={}
        dept=(row['department'] or '').casefold()
        if row['id']==mvuleni:
            changes['manager_id']=ayrton
        elif dept in ('reception','cleaner','cleaners','fitness','fitness instructor','training','group instructor','group instructors'):
            changes['manager_id']=mvuleni
        elif dept=='sales' and row['id']!=thato:
            changes['manager_id']=thato
        if row['employee_number'] in ('1001','1003','1005') or dept in ('cleaner','cleaners'):
            changes['start_date']='2025-01-15'
        confirmed={'1008':'2026-08-01','1018':'2026-01-01','1007':'2026-09-01'}
        if row['employee_number'] in confirmed:
            changes['start_date']=confirmed[row['employee_number']]
        if changes:
            employees.append(dict(user_id=row['id'],name=row['full_name'],version=row['version'],updates=changes))
    return {'employees':employees,'approvers':[thato],
            'pending_month_only_dates':{},
            'annual_policy':'21 working days/year, 1.75 days/month; configured separately by annual policy deployment',
            'not_assigned':['Thato reporting manager','Ayrton reporting manager',"Kiddie's Corner reporting manager"]}

if __name__=='__main__':
    client=deploy.connect(SimpleNamespace(host=deploy.DEFAULT_HOST,user='root',key=deploy.DEFAULT_KEY))
    try:
        output=deploy.run(client,'/opt/onecpase/venv/bin/python -c '+shlex.quote(READ))
        rows=json.loads(output)
        out=ROOT/'artifacts/hr-reporting-setup';out.mkdir(parents=True,exist_ok=True)
        (out/'before.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
        plan=make_plan(rows)
        (out/'plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
        if len(sys.argv)>1 and sys.argv[1]=='apply':
            output=deploy.run(client,'/opt/onecpase/venv/bin/python -c '+shlex.quote(APPLY)+' '+shlex.quote(json.dumps(plan)))
            (out/'result.json').write_text(json.dumps(json.loads(output),indent=2),encoding='utf-8')
            print(output)
        else:
            print(json.dumps(plan,indent=2))
    finally:
        client.close()
