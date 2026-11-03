"""Scoped release: Review & Submit on NuPay for managers only. Built from a clean copy so other uncommitted work stays out."""
from pathlib import Path
import sys,json,hashlib,shutil,subprocess,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from artifacts import page_consistency_release as release
SOURCE=Path('C:/Users/eafri/AppData/Local/Temp/claude/d--Dev-1Cpace/dc959724-af77-46f3-a9dd-ed9e4e212644/scratchpad/nupay-review-wt')
BASE='2bc75d6'
FILES=['onecpase/debicheck_routes.py','onecpase/templates/debicheck/detail.html','onecpase/templates/members/detail.html']
folder=ROOT/'artifacts/nupay-review-managers-20261103'
if sys.argv[1]=='manifest':
    folder.mkdir(exist_ok=True)
    (folder/'source-manifest.json').write_text(json.dumps([dict(path=p,sha256=hashlib.sha256((SOURCE/p).read_bytes()).hexdigest()) for p in FILES],indent=2))
    # Only this work's changes are merged into whatever is installed.
    if (folder/'before').exists():shutil.rmtree(folder/'before')
    for p in FILES:
        shown=subprocess.run(['git','show',BASE+':'+p],cwd=ROOT,capture_output=True,check=True)
        saved=folder/'before'/p;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(shown.stdout)
    print(len(FILES));raise SystemExit()
for row in json.loads((folder/'source-manifest.json').read_text()):assert hashlib.sha256((SOURCE/row['path']).read_bytes()).hexdigest()==row['sha256'],'Changed since the manifest: '+row['path']
if sys.argv[1] in ('apply','verify'):
    suites=ET.parse(folder/'tests.xml').getroot()
    assert sum(int(s.get('tests',0)) for s in suites.iter('testsuite'))>=1280
    assert not any(int(s.get('failures',0))+int(s.get('errors',0)) for s in suites.iter('testsuite'))
release.ROOT=SOURCE
release.FOLDER=ROOT/'artifacts/nupay-review-managers-release-20261103';release.BEFORE=folder/'before';release.FILES=FILES
release.REMOTE_SCRIPT=ROOT/'artifacts/nupay_review_managers_remote.py';release.STAGE_PREFIX='/opt/onecpase/.workflow-release-nupay_review_managers_'
release.SUPPORT_SCRIPTS=[ROOT/'artifacts/page_consistency_remote.py',ROOT/'artifacts/dashboard_periods_remote.py',ROOT/'artifacts/workflow_release_remote.py',ROOT/'artifacts/query_funnel_remote.py',ROOT/'artifacts/contract_signing_remote.py']
if __name__=='__main__':release.main()
