"""Run false_case_cleanup_remote.py on the server as a systemd unit, so a dropped connection cannot stop it mid-way."""
from pathlib import Path
from types import SimpleNamespace
import json
import shlex
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import deploy_beta as deploy

FOLDER = ROOT / 'artifacts/false-case-cleanup-20261102'
STAGE = '/opt/onecpase/.maintenance-false-cases-20261102'
UNIT = 'onecpase-false-case-cleanup'

action = sys.argv[1]
assert action in ('preview', 'rehearse', 'apply')
FOLDER.mkdir(exist_ok=True)


def connect():
    return deploy.connect(SimpleNamespace(host=deploy.DEFAULT_HOST, user='root', key=deploy.DEFAULT_KEY))


client = connect()
try:
    deploy.run(client, 'mkdir -p -m 700 ' + shlex.quote(STAGE))
    sftp = client.open_sftp()
    sftp.put(str(ROOT / 'artifacts/false_case_cleanup_remote.py'), STAGE + '/cleanup.py')
    sftp.close()
    deploy.run(client, f'rm -f {STAGE}/{action}-result.json {STAGE}/{action}.log; systemctl reset-failed {UNIT} 2>/dev/null; true', check=False)
    command = f'cd {STAGE} && exec /opt/onecpase/venv/bin/python cleanup.py {action} > {action}.log 2>&1'
    deploy.run(client, f'systemd-run --unit={UNIT} --collect /bin/sh -c {shlex.quote(command)}')
finally:
    client.close()

deadline = time.time() + 3600
state = 'active'
while time.time() < deadline and state in ('active', 'activating'):
    time.sleep(15)
    try:
        client = connect()
        try:
            state = deploy.run(client, f'systemctl is-active {UNIT} 2>/dev/null || true', check=False).strip() or 'inactive'
            log = deploy.run(client, f'tail -n 40 {STAGE}/{action}.log 2>/dev/null', check=False)
            result = deploy.run(client, f'cat {STAGE}/{action}-result.json 2>/dev/null', check=False)
        finally:
            client.close()
    except Exception as exc:
        print('poll failed, retrying:', type(exc).__name__, flush=True)
print(log[-3000:], flush=True)
if result:
    (FOLDER / f'{action}-result.json').write_text(result, encoding='utf-8')
    print(json.dumps(json.loads(result), indent=1), flush=True)
else:
    raise SystemExit(f'{action} finished without a result; state={state}')
