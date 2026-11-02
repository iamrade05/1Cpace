"""Scoped release: contract signing link, TT3 upload, first-month view, funnel buttons, sweep and Power User fixes."""
from pathlib import Path
import sys,json,hashlib,shutil,subprocess,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from artifacts import page_consistency_release as release
BASE='1c804c0'  # the last commit before this work
FILES=['onecpase/collections.py','onecpase/collections_engine.py','onecpase/contracts.py','onecpase/dashboard_access.py','onecpase/database.py',
       'onecpase/debicheck.py','onecpase/first_month_collections.py','onecpase/members.py','onecpase/ptp.py','onecpase/tt3.py',
       'onecpase/static/css/components.css','onecpase/static/js/contract-sign.js','onecpase/static/js/document-upload.js','onecpase/templates/base.html',
       'onecpase/templates/collections/first_month.html','onecpase/templates/contracts/share_response.html','onecpase/templates/contracts/view.html',
       'onecpase/templates/debicheck/_document_upload.html','onecpase/templates/leads/detail.html','onecpase/templates/members/detail.html',
       'scripts/import_itensity_members.py']
folder=ROOT/'artifacts/contract-signing-20261102'
if sys.argv[1]=='manifest':
    folder.mkdir(exist_ok=True)
    (folder/'source-manifest.json').write_text(json.dumps([dict(path=p,sha256=hashlib.sha256((ROOT/p).read_bytes()).hexdigest()) for p in FILES],indent=2))
    # Only this work's changes are merged into whatever is installed; new files have no baseline.
    if (folder/'before').exists():shutil.rmtree(folder/'before')
    for p in FILES:
        shown=subprocess.run(['git','show',BASE+':'+p],cwd=ROOT,capture_output=True)
        if shown.returncode==0:
            saved=folder/'before'/p;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(shown.stdout)
    print(len(FILES));raise SystemExit()
for row in json.loads((folder/'source-manifest.json').read_text()):assert hashlib.sha256((ROOT/row['path']).read_bytes()).hexdigest()==row['sha256'],'Changed since the manifest: '+row['path']
if sys.argv[1] in ('apply','verify'):
    suites=ET.parse(folder/'tests.xml').getroot()
    assert sum(int(s.get('tests',0)) for s in suites.iter('testsuite'))>=1280
    assert not any(int(s.get('failures',0))+int(s.get('errors',0)) for s in suites.iter('testsuite'))
release.FOLDER=ROOT/'artifacts/contract-signing-release-20261102';release.BEFORE=folder/'before';release.FILES=FILES
release.REMOTE_SCRIPT=ROOT/'artifacts/contract_signing_remote.py';release.STAGE_PREFIX='/opt/onecpase/.workflow-release-contract_signing_'
release.SUPPORT_SCRIPTS=[ROOT/'artifacts/page_consistency_remote.py',ROOT/'artifacts/dashboard_periods_remote.py',ROOT/'artifacts/workflow_release_remote.py',ROOT/'artifacts/query_funnel_remote.py']
if __name__=='__main__':release.main()
