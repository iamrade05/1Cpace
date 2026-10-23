"""Deliver the tested company annual-leave policy update only."""
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,shlex,sys
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import deploy_beta as deploy
FILES=['onecpase/hr_workforce.py','onecpase/hr_workforce_routes.py','onecpase/templates/hr/me.html','onecpase/templates/hr/people_setup.html']

if __name__=='__main__':
    suites=list(ET.parse(ROOT/'artifacts/hr-annual21-tests.xml').getroot().iter('testsuite'))
    assert suites and sum(int(s.get('tests',0)) for s in suites)>=31
    assert all(int(s.get('failures',0))==0 and int(s.get('errors',0))==0 for s in suites)
    client=deploy.connect(SimpleNamespace(host=deploy.DEFAULT_HOST,user='root',key=deploy.DEFAULT_KEY))
    out=ROOT/'artifacts/hr-annual21';out.mkdir(parents=True,exist_ok=True)
    try:
        baseline={item['path']:item['after'] for item in json.loads((ROOT/'artifacts/hr-release/manifest.json').read_text())}
        stage='/opt/onecpase-backups/hr-annual21-'+deploy.run(client,'date -u +%Y%m%dT%H%M%SZ')
        deploy.run(client,'mkdir -m 700 '+shlex.quote(stage));sftp=client.open_sftp();manifest=[]
        for rel in FILES:
            with sftp.open('/opt/onecpase/'+rel,'rb') as handle:before=handle.read()
            assert hashlib.sha256(before).hexdigest()==baseline[rel],'Live source differs from the previous reviewed deployment: '+rel
            local_before=out/'before'/rel;local_before.parent.mkdir(parents=True,exist_ok=True);local_before.write_bytes(before)
            data=(ROOT/rel).read_bytes();target=stage+'/incoming/'+rel
            deploy.run(client,'mkdir -p '+shlex.quote(str(Path(target).parent).replace('\\','/')))
            with sftp.open(target,'wb') as handle:handle.write(data)
            manifest.append({'path':rel,'before':hashlib.sha256(before).hexdigest(),'after':hashlib.sha256(data).hexdigest()})
        for name,data in [('manifest.json',json.dumps(manifest).encode()),('remote.py',(ROOT/'scripts/hr_annual_policy_remote.py').read_bytes())]:
            with sftp.open(stage+'/'+name,'wb') as handle:handle.write(data)
        sftp.close();(out/'stage.txt').write_text(stage)
        result=deploy.run(client,'/opt/onecpase/venv/bin/python '+shlex.quote(stage+'/remote.py')+' '+shlex.quote(stage))
        (out/'result.json').write_text(json.dumps(json.loads(result),indent=2));print(result)
    finally:client.close()
