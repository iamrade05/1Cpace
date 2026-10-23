"""Deploy exactly the inspected HR file set, after a passing scoped release gate."""
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,shlex,sys
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import deploy_beta as deploy
from scripts.prepare_hr_release import SHARED,NEW


def main():
    mode=sys.argv[1] if len(sys.argv)>1 else 'prepare'
    out=ROOT/'artifacts/hr-release'; out.mkdir(parents=True,exist_ok=True)
    client=deploy.connect(SimpleNamespace(host=deploy.DEFAULT_HOST,user='root',key=deploy.DEFAULT_KEY))
    try:
        if mode=='prepare':
            stamp=deploy.run(client,'date -u +%Y%m%dT%H%M%SZ')
            stage='/opt/onecpase-backups/hr-workforce-'+stamp
            deploy.run(client,'mkdir -m 700 '+shlex.quote(stage))
            sftp=client.open_sftp(); manifest=[]
            try:
                for rel in SHARED+NEW:
                    target=stage+'/incoming/'+rel
                    deploy.run(client,'mkdir -p '+shlex.quote(str(Path(target).parent).replace('\\','/')))
                    try:
                        with sftp.open('/opt/onecpase/'+rel,'rb') as handle:
                            before=handle.read()
                    except FileNotFoundError:
                        before=None
                    if rel in SHARED:
                        expected=(out/'before'/rel).read_bytes()
                        assert before==expected,'Live source changed since inspection: '+rel
                    else:
                        assert before is None,'New target already exists: '+rel
                    data=(ROOT/rel).read_bytes()
                    with sftp.open(target,'wb') as handle:
                        handle.write(data)
                    manifest.append(dict(path=rel,before=hashlib.sha256(before).hexdigest() if before is not None else None,
                                         after=hashlib.sha256(data).hexdigest()))
                for filename,data in [('manifest.json',json.dumps(manifest,indent=2).encode()),
                                      ('remote.py',(ROOT/'scripts/hr_release_remote.py').read_bytes())]:
                    with sftp.open(stage+'/'+filename,'wb') as handle:
                        handle.write(data)
            finally:
                sftp.close()
            (out/'stage.txt').write_text(stage,encoding='utf-8')
            print(deploy.run(client,'/opt/onecpase/venv/bin/python '+shlex.quote(stage+'/remote.py')+' '+shlex.quote(stage)+' rehearse'))
        elif mode=='deploy':
            xml=ROOT/'artifacts/hr-release-tests.xml'
            suites=list(ET.parse(xml).getroot().iter('testsuite'))
            assert suites and sum(int(s.get('tests','0')) for s in suites)>=90,'Missing release checks'
            assert all(int(s.get('failures','0'))==0 and int(s.get('errors','0'))==0 for s in suites),'Release gate failed'
            stage=(out/'stage.txt').read_text().strip()
            print(deploy.run(client,'/opt/onecpase/venv/bin/python '+shlex.quote(stage+'/remote.py')+' '+shlex.quote(stage)+' deploy'))
            sftp=client.open_sftp()
            try:
                for filename in ['deployment.json','verification.json','rehearsal.json','manifest.json']:
                    with sftp.open(stage+'/'+filename,'rb') as handle:
                        (out/filename).write_bytes(handle.read())
            finally:
                sftp.close()
        else:
            raise ValueError('Use prepare or deploy')
    finally:
        client.close()


if __name__=='__main__':
    main()
