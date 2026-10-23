"""Remove only disposable DB copies created by these HR verification runs."""
from pathlib import Path
from types import SimpleNamespace
import json,shlex,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import deploy_beta as deploy
REMOTE='''
from pathlib import Path
import json,shutil
base=Path('/opt/onecpase-backups').resolve()
targets=[
 'hr-workforce-20261016T163805Z/restore-check.db',
 'hr-workforce-20261016T163805Z/verify-rehearse/app.db',
 'hr-workforce-20261016T163805Z/verify-verify/app.db',
 'hr-annual21-20261016T173836Z/data-rehearsal.db',
 'hr-annual21-20261016T173836Z/verify-verify/app.db',
]
removed=[]
for rel in targets:
    path=(base/rel).resolve()
    assert path.is_relative_to(base) and path.name in ('restore-check.db','data-rehearsal.db','app.db')
    assert path.name!='database-before.db' and '/var/data/' not in str(path)
    for suffix in ('','-wal','-shm','-journal'):
        target=Path(str(path)+suffix)
        if target.is_file():
            size=target.stat().st_size;target.unlink();removed.append({'path':str(target),'bytes':size})
result={'removed_rehearsals':removed,'free_bytes':shutil.disk_usage(base).free,'production_and_backups_preserved':True}
print(json.dumps(result))
'''
if __name__=='__main__':
    c=deploy.connect(SimpleNamespace(host=deploy.DEFAULT_HOST,user='root',key=deploy.DEFAULT_KEY))
    try:
        result=deploy.run(c,'/opt/onecpase/venv/bin/python -c '+shlex.quote(REMOTE))
        (ROOT/'artifacts/hr-annual21/rehearsal-cleanup.json').write_text(json.dumps(json.loads(result),indent=2))
        print(result)
    finally:c.close()
