"""Read-only live source inspection for the scoped HR release."""
from pathlib import Path
from types import SimpleNamespace
import difflib,json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import deploy_beta as deploy

SHARED=['onecpase/__init__.py','onecpase/database.py','onecpase/config.py','onecpase/phases.py',
        'onecpase/permissions.py','onecpase/notifications.py','onecpase/hr.py',
        'onecpase/templates/base.html','onecpase/templates/hr/index.html','onecpase/templates/hr/leave_request.html']
NEW=['onecpase/hr_workforce.py','onecpase/hr_workforce_routes.py','onecpase/static/css/hr-workforce.css']+[
    'onecpase/templates/hr/'+name for name in ['_workforce_nav.html','workforce_error.html','me.html','people_setup.html',
    'workforce_leave.html','availability.html','workforce_week.html','roster.html','balances.html','reviews.html']]

if __name__=='__main__':
    out=ROOT/'artifacts/hr-release'; out.mkdir(parents=True,exist_ok=True)
    client=deploy.connect(SimpleNamespace(host=deploy.DEFAULT_HOST,user='root',key=deploy.DEFAULT_KEY))
    try:
        sftp=client.open_sftp(); summary=[]; patch=[]
        for rel in SHARED:
            with sftp.open('/opt/onecpase/'+rel,'rb') as handle:
                raw=handle.read()
            target=out/'before'/rel; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(raw)
            before=raw.decode().splitlines(keepends=True); after=(ROOT/rel).read_text(encoding='utf-8').splitlines(keepends=True)
            delta=list(difflib.unified_diff(before,after,fromfile='a/'+rel,tofile='b/'+rel,n=4));patch+=delta
            summary.append({'path':rel,'diff_lines':len(delta)})
        sftp.close()
        (out/'live-to-local.patch').write_text(''.join(patch),encoding='utf-8')
        (out/'files.json').write_text(json.dumps({'shared':SHARED,'new':NEW},indent=2),encoding='utf-8')
        print(json.dumps(summary,indent=2))
        print(deploy.run(client,"systemctl is-active onecpase"))
    finally:
        client.close()
