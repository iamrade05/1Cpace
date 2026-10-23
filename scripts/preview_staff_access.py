"""Read gym access scans and match them to the live staff directory.

No access decisions, attendance records or payroll data are changed.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta,timezone
import json
from pathlib import Path
import shlex,sys,unicodedata
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def normal(value):
    return ' '.join(''.join(c.lower() if c.isalnum() else ' ' for c in unicodedata.normalize('NFKD',str(value or '')) if not unicodedata.combining(c)).split())


def load_roster():
    import deploy_beta as deployment
    code="""import sqlite3,json
c=sqlite3.connect('file:/var/data/onecpase/1cpase.db?mode=ro',uri=True);c.row_factory=sqlite3.Row
print(json.dumps([dict(r) for r in c.execute(\"SELECT id,username,full_name,department,staff_id,employee_number FROM users WHERE active=1 AND username!='admin' ORDER BY full_name\")]))
"""
    client=deployment.connect(SimpleNamespace(host=deployment.DEFAULT_HOST,user='root',key=deployment.DEFAULT_KEY))
    try:return json.loads(deployment.run(client,'/opt/onecpase/venv/bin/python -c '+shlex.quote(code)))
    finally:client.close()


def extract(week,roster):
    from onecpase.access_report import _elev8_conn
    connection=_elev8_conn();cursor=connection.cursor()
    try:
        latest=cursor.execute('SELECT MAX(TRY_CONVERT(date,attendance_date)) FROM dbo.access_log').fetchone()[0]
        identities=cursor.execute("SELECT DISTINCT itensity_reference,member_name,member_surname FROM dbo.access_log WHERE NULLIF(LTRIM(RTRIM(itensity_reference)),'') IS NOT NULL AND itensity_reference<>'N/A'").fetchall()
        matched=defaultdict(list);issues=[]
        for ref,first,last in identities:
            candidates=[]
            for user in roster:
                full=normal(user['full_name']);source_name=normal(str(first or '')+' '+str(last or ''))
                given=set(normal(first).split());surname=normal(last)
                name_match=(full==source_name or (surname and full.endswith(' '+surname) and given and given.issubset(set(full[:-len(surname)].split()))))
                reference_match=str(user.get('staff_id') or '')==str(ref).strip()
                if name_match or reference_match:candidates.append(user)
            if len(candidates)==1:
                matched[str(ref).strip()].append(candidates[0]['id'])
            elif len(candidates)>1:issues.append({'reference':str(ref),'issue':'Ambiguous employee match'})
        ref_map={ref:next(iter(set(ids))) for ref,ids in matched.items() if len(set(ids))==1}
        counts={str(row[0]):int(row[1]) for row in cursor.execute('SELECT TRY_CONVERT(date,attendance_date),COUNT(*) FROM dbo.access_log WHERE TRY_CONVERT(date,attendance_date)>=? AND TRY_CONVERT(date,attendance_date)<? GROUP BY TRY_CONVERT(date,attendance_date)',week,week+timedelta(days=7))}
        rows=cursor.execute('''SELECT itensity_reference,TRY_CONVERT(date,attendance_date),check_in_time,
          check_out_time,duration_mins,movement,allowed,zone,role
          FROM dbo.access_log WHERE TRY_CONVERT(date,attendance_date)>=? AND TRY_CONVERT(date,attendance_date)<?''',week,week+timedelta(days=7)).fetchall()
        events=[];invalid=0;unlinked_source=Counter()
        source_names={str(ref).strip():' '.join(filter(None,[str(first or '').strip(),str(last or '').strip()])) for ref,first,last in identities}
        for ref,day,entry,exit_time,duration,movement,allowed,zone,source_role in rows:
            uid=ref_map.get(str(ref).strip())
            if uid is None:
                if str(source_role or '').strip().lower() not in ('member','non-member',''):
                    unlinked_source[(str(ref).strip(),str(source_role or ''))]+=1
                continue
            try:stamp=datetime.fromisoformat(str(day)+'T'+str(entry).strip())
            except ValueError:invalid+=1;continue
            events.append({'user_id':uid,'source_reference':str(ref).strip(),'occurred_at':stamp.isoformat(),
                'movement':str(movement or ''),'allowed':str(allowed or '')=='1','zone':str(zone or ''),
                'exit_time':str(exit_time or ''),'duration_minutes':str(duration or ''),'source_role':str(source_role or '')})
        keys=[(e['user_id'],e['occurred_at'],e['movement'],e['allowed'],e['zone']) for e in events]
        return {'schema_version':1,'tenant_slug':'elev8','source':'Gym SQL Server dbo.access_log',
            'captured_at':datetime.now(timezone(timedelta(hours=2))).isoformat(),
            'week_start':week.isoformat(),'week_end':(week+timedelta(days=6)).isoformat(),
            'source_latest_date':str(latest),'source_rows_by_date':counts,'roster':roster,
            'matched_references':ref_map,'mapping_issues':issues,'events':events,
            'shift_assignments':{'lindelani':'early','mfundentle':'early','morongoe':'early',
                'colin':'late','tanki':'late','mvuleni':'late'} if week==date(2026,10,12) else {},
            'public_holidays':[],
            'quality':{'source_week_rows':sum(counts.values()),'staff_rows':len(events),
                'exact_duplicate_scans':len(keys)-len(set(keys)),'invalid_staff_times':invalid,
                'staff_exit_rows':sum(bool(e['exit_time']) for e in events),
                'unlinked_source_staff':[{'name':source_names.get(ref,'Unidentified staff record'),'role':role,'scans':count} for (ref,role),count in unlinked_source.items()],
                'unmatched_staff':[u['full_name'] for u in roster if u['id'] not in ref_map.values()]}}
    finally:connection.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--week');parser.add_argument('--out',default='artifacts/staff-attendance-20261012/source.json')
    parser.add_argument('--sync',action='store_true',help='Publish the read-only snapshot to the gym app')
    args=parser.parse_args()
    today=datetime.now(timezone(timedelta(hours=2))).date()
    week=date.fromisoformat(args.week) if args.week else today-timedelta(days=today.weekday())
    snapshot=extract(week,load_roster());path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(snapshot,indent=2),encoding='utf-8')
    if args.sync:publish(snapshot)
    print(json.dumps({'saved':str(path),'latest_date':snapshot['source_latest_date'],**snapshot['quality']},indent=2))


def publish(snapshot):
    import deploy_beta as deployment
    code="""from pathlib import Path
from dotenv import dotenv_values
cfg=dotenv_values('/opt/onecpase/.env')
print(Path(cfg.get('UPLOAD_FOLDER') or '/opt/onecpase/uploads').parent/'staff-access')
"""
    client=deployment.connect(SimpleNamespace(host=deployment.DEFAULT_HOST,user='root',key=deployment.DEFAULT_KEY))
    try:
        folder=deployment.run(client,'/opt/onecpase/venv/bin/python -c '+shlex.quote(code)).strip()
        assert folder in ('/var/data/onecpase/staff-access','/opt/onecpase/staff-access')
        deployment.run(client,'install -d -m 750 -o onecpase -g onecpase '+shlex.quote(folder))
        sftp=client.open_sftp()
        try:
            for name in [snapshot['week_start']+'.json','current.json']:
                target=folder+'/'+name;temporary=target+'.upload'
                with sftp.open(temporary,'wb') as handle:handle.write(json.dumps(snapshot).encode())
                sftp.chmod(temporary,0o640)
                deployment.run(client,'chown onecpase:onecpase '+shlex.quote(temporary))
                sftp.posix_rename(temporary,target)
        finally:sftp.close()
        print('Staff access snapshot published; no attendance or payroll records changed.')
    finally:client.close()


if __name__=='__main__':
    status_path=ROOT/'artifacts/staff-attendance/sync-status.json'
    status_path.parent.mkdir(parents=True,exist_ok=True)
    try:
        main()
    except Exception as exc:
        status_path.write_text(json.dumps({'ok':False,'attempted_at':datetime.now(timezone(timedelta(hours=2))).isoformat(),
            'error_type':type(exc).__name__}),encoding='utf-8')
        raise
    else:
        status_path.write_text(json.dumps({'ok':True,'completed_at':datetime.now(timezone(timedelta(hours=2))).isoformat()}),encoding='utf-8')
