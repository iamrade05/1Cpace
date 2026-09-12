#!/usr/bin/env python3
"""Replace the beta tenant database with this machine's copy.

Beta and this machine share the same ENCRYPTION_KEY, which is what makes this
safe: member ID numbers are encrypted at rest, and a database moved to a host
with a different key would decrypt to nothing. The script refuses to run if
the two keys do not match.

Only the tenant database is copied. Beta's platform.db is left alone — it
holds beta's own tenant row pointing at beta's paths and beta's power-user
account, none of which exist on this machine.

The service is stopped for the swap so the app cannot hold a half-replaced
file open, and beta's current database is copied aside first.

Dry run unless --commit is given.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import socket
import sqlite3
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

try:
    import paramiko
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Paramiko is required: python -m pip install paramiko") from exc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HOST = "102.211.207.233"
USER = "root"
KEY = Path.home() / ".ssh" / "1cpace_absolute_ed25519"
SERVICE = "onecpase"
REMOTE_ENV = "/opt/onecpase/.env"
REMOTE_VENV_PY = "/opt/onecpase/venv/bin/python"


def connect() -> paramiko.SSHClient:
    """Connect, retrying timeouts — this host answers intermittently."""
    for attempt in range(1, 6):
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(HOST, username=USER, key_filename=str(KEY),
                           look_for_keys=False, allow_agent=False,
                           timeout=60, banner_timeout=60, auth_timeout=60)
            return client
        except (TimeoutError, socket.timeout):
            client.close()
            if attempt == 5:
                raise SystemExit(f"Could not reach {HOST} after 5 attempts.")
            print(f"  connect attempt {attempt} timed out; retrying...")
            time.sleep(3)
        except paramiko.SSHException as exc:
            client.close()
            raise SystemExit(f"SSH failed: {exc}") from exc


def run(client, command, *, check=True):
    _in, out, err = client.exec_command(command)
    text = out.read().decode(errors="replace")
    error = err.read().decode(errors="replace")
    status = out.channel.recv_exit_status()
    if check and status != 0:
        raise SystemExit(f"Remote command failed ({status}): {command}\n{error or text}")
    return text.strip()


def key_fingerprint(env_text: str) -> str:
    m = re.search(r"^ENCRYPTION_KEY=(.*)$", env_text, re.M)
    value = (m.group(1).strip() if m else "")
    return hashlib.sha256(value.encode()).hexdigest()[:12] if value else ""


def local_counts(path: Path) -> dict:
    db = sqlite3.connect(path)
    try:
        return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("users", "members", "leads")}
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-db", type=Path,
                        default=ROOT / "data" / "tenants" / "elev8.db")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    if not args.local_db.is_file():
        raise SystemExit(f"Local database not found: {args.local_db}")

    local_env = (ROOT / ".env").read_text(encoding="utf-8")
    local_fp = key_fingerprint(local_env)
    mine = local_counts(args.local_db)
    size_mb = args.local_db.stat().st_size / 1_048_576

    print(f"Local : {args.local_db}")
    print(f"        {size_mb:.1f} MB  users={mine['users']} members={mine['members']} leads={mine['leads']}")

    client = connect()
    try:
        remote_env = run(client, f"cat {REMOTE_ENV}")
        remote_fp = key_fingerprint(remote_env)
        m = re.search(r"^DATABASE_PATH=(.*)$", remote_env, re.M)
        if not m:
            raise SystemExit("Beta .env has no DATABASE_PATH")
        remote_db = m.group(1).strip()

        print(f"Beta  : {remote_db}")
        theirs = run(client, f"{REMOTE_VENV_PY} -c \""
                     f"import sqlite3;d=sqlite3.connect('{remote_db}');"
                     f"print(' '.join(str(d.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]) "
                     f"for t in ('users','members','leads')))\"")
        print(f"        users/members/leads = {theirs}")

        print(f"\nENCRYPTION_KEY  local {local_fp}  beta {remote_fp}")
        if not local_fp or local_fp != remote_fp:
            raise SystemExit(
                "REFUSED: the encryption keys differ. Copying the database would "
                "make every encrypted ID number unreadable on beta."
            )
        print("                keys match — encrypted fields will decrypt on beta")

        if not args.commit:
            print("\nDRY RUN — beta unchanged. Re-run with --commit.")
            return 0

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = "/opt/onecpase-backups"
        run(client, f"mkdir -p {backup_dir}")
        backup = f"{backup_dir}/tenant_before_push_{stamp}.db"
        run(client, f"cp -p {remote_db} {backup}")
        print(f"\nBeta database backed up to {backup}")

        staged = f"/tmp/tenant-{uuid.uuid4().hex}.db"
        sftp = client.open_sftp()
        try:
            sftp.put(str(args.local_db), staged)
        finally:
            sftp.close()
        print("Uploaded.")

        # Prove the upload is a readable database before anything is replaced.
        check = run(client, f"{REMOTE_VENV_PY} -c \""
                    f"import sqlite3;d=sqlite3.connect('{staged}');"
                    f"print(d.execute('PRAGMA integrity_check').fetchone()[0]);"
                    f"print(d.execute('SELECT COUNT(*) FROM members').fetchone()[0])\"")
        integrity, members = check.splitlines()[:2]
        print(f"Upload check: integrity={integrity} members={members}")
        if integrity != "ok" or int(members) != mine["members"]:
            run(client, f"rm -f {staged}")
            raise SystemExit("REFUSED: the uploaded database did not verify. Beta untouched.")

        run(client, f"systemctl stop {SERVICE}")
        run(client, f"mv {staged} {remote_db}")
        run(client, f"chown onecpase:onecpase {remote_db}")
        run(client, f"chmod 640 {remote_db}")
        # WAL/SHM from the old database would not match the new one.
        run(client, f"rm -f {remote_db}-wal {remote_db}-shm", check=False)
        run(client, f"systemctl start {SERVICE}")
        print(f"{SERVICE}: {run(client, f'systemctl is-active {SERVICE}', check=False)}")

        after = run(client, f"{REMOTE_VENV_PY} -c \""
                    f"import sqlite3;d=sqlite3.connect('{remote_db}');"
                    f"print(' '.join(str(d.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]) "
                    f"for t in ('users','members','leads')))\"")
        print(f"Beta now: users/members/leads = {after}")
        print("Health  :", run(client, "curl -s -o /dev/null -w '%{http_code}' "
                               "localhost:5002/healthz", check=False))
        print(f"\nRollback: systemctl stop {SERVICE} && cp -p {backup} {remote_db} "
              f"&& chown onecpase:onecpase {remote_db} && systemctl start {SERVICE}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
