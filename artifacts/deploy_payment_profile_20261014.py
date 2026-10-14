"""Deploy only the collapsible payment profile, preserving other live changes."""
import hashlib
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from jinja2 import Environment

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import deploy_beta as deploy

relative='onecpase/templates/collections/call_profile.html'
local=(ROOT/relative).read_text(encoding='utf-8')
start=local.index('<details class="form-card" id="payment-profile"')
end=local.index('</details>',start)+len('</details>')
block=local[start:end]+'\n\n'
marker='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px;margin-bottom:16px;">'
Environment().parse(block)
client=deploy.connect(SimpleNamespace(host=deploy.DEFAULT_HOST,user='root',key=deploy.DEFAULT_KEY))
sftp=client.open_sftp()
remote='/opt/onecpase/'+relative
stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
folder='/opt/onecpase-backups/payment_profile_'+stamp
installed=False
try:
    with sftp.open('/opt/onecpase/onecpase/collections.py') as f:
        backend=f.read().decode()
    assert 'payment_profile=payment_profile[:12]' in backend, 'Payment profile context unavailable'
    with sftp.open(remote) as f:original=f.read().decode('utf-8')
    if 'id="payment-profile"' in original:
        assert original.count('id="payment-profile"')==1
        live_start=original.index('<details class="form-card" id="payment-profile"')
        live_end=original.index('</details>',live_start)+len('</details>')
        updated=original[:live_start]+block.rstrip()+original[live_end:]
    else:
        assert original.count(marker)==1
        updated=original.replace(marker,block+marker)
    Environment().parse(updated)
    deploy.run(client,'mkdir -m 700 '+shlex.quote(folder))
    saved=folder+'/call_profile.html'
    deploy.run(client,'cp -p '+shlex.quote(remote)+' '+shlex.quote(saved))
    stat=sftp.stat(remote)
    staged=remote+'.payment-profile-'+stamp
    with sftp.open(remote) as f:assert f.read().decode('utf-8')==original
    with sftp.open(staged,'w') as f:f.write(updated.encode('utf-8'))
    sftp.chmod(staged,stat.st_mode&0o777);sftp.chown(staged,stat.st_uid,stat.st_gid)
    sftp.posix_rename(staged,remote);installed=True
    deploy.run(client,'systemctl restart onecpase && systemctl is-active onecpase')
    assert deploy.check_public_health(), 'Public health check failed'
    with sftp.open(remote) as f:assert hashlib.sha256(f.read()).digest()==hashlib.sha256(updated.encode()).digest()
    result={'target':'https://beta.1cpace.co.za','files':[relative],'backup':folder,'verified':True,
            'sha256':hashlib.sha256(updated.encode()).hexdigest()}
    (ROOT/'artifacts/payment-profile-deployment-20261014.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
except BaseException:
    if installed:
        deploy.run(client,'cp -p '+shlex.quote(saved)+' '+shlex.quote(remote))
        deploy.run(client,'systemctl restart onecpase')
    raise
finally:sftp.close();client.close()
