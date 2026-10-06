#!/usr/bin/env python3
"""Deploy 1Cpace V8 to the beta instance on the Absolute Hosting VPS.

    python deploy_beta.py                # validate, confirm, deploy
    python deploy_beta.py --check-only   # validate and report, change nothing
    python deploy_beta.py --yes          # non-interactive

Beta is /opt/onecpase, served at beta.1cpace.co.za by nginx on port 5002.
Three unrelated applications share this host — production 1Cpace in
/opt/cpace on 5000, FiksAccounts in /opt/fiksaccounts on 5001, and this.
The target is hard-guarded so a mistyped flag cannot reach the other two.

The onecpase package is replaced wholesale rather than extracted over, so
modules deleted upstream do not linger on the server. The previous tree is
backed up first and the swap only happens once the new tree is in place.

Never sent from this computer and never overwritten on the server: the .env,
the databases and uploads in /var/data/onecpase, and the venv.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import shlex
import socket
import sys
import time
import tarfile
import tempfile
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

try:
    import paramiko
except ImportError as exc:  # pragma: no cover - operator setup path
    raise SystemExit(
        "Paramiko is required. Install it with: python -m pip install paramiko"
    ) from exc


ROOT = Path(__file__).resolve().parent

APP_DIR = "/opt/onecpase"
SERVICE = "onecpase"
OWNER = "onecpase:onecpase"
BACKUP_DIR = "/opt/onecpase-backups"
KEEP_BACKUPS = 3
BACKUP_EXCLUDES = (
    "./venv",
    "./.release-*",
    "./onecpase.previous",
    "./backups",
    "./whatsapp_web_session",
    "./__pycache__",
    "*.log",
)
DEFAULT_HOST = "102.211.207.233"
DEFAULT_KEY = Path.home() / ".ssh" / "1cpace_absolute_ed25519"
PUBLIC_HEALTH_URL = "https://beta.1cpace.co.za/healthz"

# Other applications on the same host. Never a deploy target.
FORBIDDEN_DIRS = ("/opt/cpace", "/opt/fiksaccounts")

INCLUDE_FILES = (
    "run.py",
    "requirements.txt",
    "gunicorn.conf.py",
    "seed_platform_admin.py",
    "deploy/beta/onecpase.service",
)

# onecpase/ carries templates and static assets as package data.
INCLUDE_DIRS = ("onecpase", "scripts")

# Never shipped: real member data, secrets, local state, test tooling.
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".db", ".sqlite3", ".log")
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", "uploads", "tenants", "whatsapp_web_session"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("ABSOLUTE_HOST", DEFAULT_HOST))
    parser.add_argument("--user", default=os.getenv("ABSOLUTE_USER", "root"))
    parser.add_argument(
        "--key",
        type=Path,
        default=Path(os.getenv("ABSOLUTE_SSH_KEY", str(DEFAULT_KEY))).expanduser(),
    )
    parser.add_argument("--app-dir", default=APP_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--yes", action="store_true", help="skip the DEPLOY prompt")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate locally and report the live service without deploying",
    )
    args = parser.parse_args()
    target = posixpath.normpath(args.app_dir)
    if target != APP_DIR or any(target.startswith(d) for d in FORBIDDEN_DIRS):
        raise SystemExit(
            f"Refusing to deploy to {target!r}. This script only writes to "
            f"{APP_DIR}. Production 1Cpace and FiksAccounts share this host "
            "and have their own deployment paths."
        )
    args.app_dir = target
    return args


def release_paths() -> list[Path]:
    """Every file that makes up the release, with local state left behind."""
    paths: list[Path] = []
    for relative in INCLUDE_FILES:
        path = ROOT / relative
        if not path.is_file():
            raise SystemExit(f"Required release file is missing: {path}")
        paths.append(path)
    for relative in INCLUDE_DIRS:
        directory = ROOT / relative
        if not directory.is_dir():
            raise SystemExit(f"Required release directory is missing: {directory}")
        for child in directory.rglob("*"):
            if not child.is_file() or child.is_symlink():
                continue
            if child.suffix in EXCLUDED_SUFFIXES:
                continue
            if EXCLUDED_PARTS.intersection(child.parts):
                continue
            paths.append(child)
    return paths


def validate_release(paths: list[Path]) -> None:
    """Compile every Python file, and prove no secret or database slipped in."""
    checked = 0
    for path in paths:
        name = path.name
        if name == ".env" or name.startswith(".env."):
            raise SystemExit(f"Refusing to ship a secrets file: {path}")
        if path.suffix in (".db", ".sqlite3"):
            raise SystemExit(f"Refusing to ship a database: {path}")
        if path.suffix == ".py":
            compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
            checked += 1
    print(f"Local validation passed ({checked} Python files, {len(paths)} files total).")


def build_archive(paths: list[Path], directory: Path) -> Path:
    archive = directory / "onecpase-release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in paths:
            tar.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    return archive


def connect(args: argparse.Namespace) -> paramiko.SSHClient:
    """Open an SSH connection, retrying a timed-out TCP connect.

    This host intermittently fails to answer the first connect within 20s
    while the ssh client succeeds on the same key moments later, so a single
    attempt aborts a deploy for no real reason. Only timeouts are retried —
    an auth or host-key failure is final and should surface immediately.
    """
    if not args.key.is_file():
        raise SystemExit(f"SSH private key was not found: {args.key}")

    attempts = 3
    for attempt in range(1, attempts + 1):
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(
                args.host,
                username=args.user,
                key_filename=str(args.key),
                look_for_keys=False,
                allow_agent=False,
                timeout=60,
                banner_timeout=60,
                auth_timeout=60,
            )
            return client
        except (TimeoutError, socket.timeout) as exc:
            client.close()
            if attempt == attempts:
                raise SystemExit(
                    f"Could not reach {args.user}@{args.host} after {attempts} "
                    f"attempts: {exc}. The host answers ping and port 22 when "
                    "this happens, so retrying usually works."
                ) from exc
            print(f"  connect attempt {attempt} timed out; retrying...")
            time.sleep(3)
        except paramiko.SSHException as exc:
            client.close()
            raise SystemExit(
                f"Secure SSH connection failed for {args.user}@{args.host}: {exc}"
            ) from exc


def run(client: paramiko.SSHClient, command: str, *, check: bool = True) -> str:
    _stdin, stdout, stderr = client.exec_command(command)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    status = stdout.channel.recv_exit_status()
    if check and status != 0:
        raise SystemExit(f"Remote command failed ({status}): {command}\n{err or out}")
    return out.strip()


def report(client: paramiko.SSHClient, args: argparse.Namespace) -> None:
    """Describe the instance without changing it."""
    app = shlex.quote(args.app_dir)
    if run(client, f"test -d {app} && echo yes || echo no") == "no":
        raise SystemExit(f"{args.app_dir} does not exist on {args.host}.")
    if run(client, f"test -f {app}/.env && echo yes || echo no") == "no":
        raise SystemExit(f"{args.app_dir}/.env is missing; beta cannot start without it.")
    print(f"Remote {args.app_dir}: present, .env present.")
    print("Service:", run(client, f"systemctl is-active {SERVICE} || true"))
    print("Phase:  ", run(
        client, f"grep -E '^ACTIVE_PHASE=' {app}/.env || echo 'ACTIVE_PHASE unset (defaults to 1)'"
    ))


def prune_backups(client: paramiko.SSHClient) -> None:
    """Keep only the newest KEEP_BACKUPS of each kind; the disk filled once."""
    for prefix in ("onecpase_", "data_"):
        run(client, f"cd {shlex.quote(BACKUP_DIR)} && ls -1 {prefix}*.tar.gz 2>/dev/null "
                    f"| sort | head -n -{KEEP_BACKUPS} | xargs -r rm -f", check=False)
    print(f"Pruned {BACKUP_DIR} to the newest {KEEP_BACKUPS} of each backup kind.")


def deploy(client: paramiko.SSHClient, archive: Path, args: argparse.Namespace) -> None:
    app = shlex.quote(args.app_dir)
    stamp = run(client, "date +%Y%m%d_%H%M%S")
    staging = f"{args.app_dir}/.release-{uuid.uuid4().hex}"
    remote_tmp = f"/tmp/onecpase-{uuid.uuid4().hex}.tar.gz"

    sftp = client.open_sftp()
    try:
        sftp.put(str(archive), remote_tmp)
    finally:
        sftp.close()
    print("Release uploaded.")

    # Back up the current tree (without the venv, which pip rebuilds) and the
    # databases, before anything is moved.
    run(client, f"mkdir -p {shlex.quote(BACKUP_DIR)}")
    backup = f"{BACKUP_DIR}/onecpase_{stamp}.tar.gz"
    # Only the code is worth snapshotting here: the venv is rebuilt, the
    # previous package and the whatsapp session are large and recoverable, and
    # earlier backups must never be nested inside the next one.
    excludes = " ".join(f"--exclude={shlex.quote(p)}" for p in BACKUP_EXCLUDES)
    run(client, f"tar -czf {shlex.quote(backup)} -C {app} {excludes} . 2>/dev/null || true")
    run(client, f"tar -czf {shlex.quote(BACKUP_DIR)}/data_{stamp}.tar.gz "
                f"-C /var/data/onecpase . 2>/dev/null || true")
    print(f"Backed up to {BACKUP_DIR}/onecpase_{stamp}.tar.gz and data_{stamp}.tar.gz")
    prune_backups(client)

    # Stage the new tree, then swap. The package is replaced outright so
    # modules deleted upstream do not survive on the server.
    run(client, f"rm -rf {shlex.quote(staging)} && mkdir -p {shlex.quote(staging)}")
    run(client, f"tar -xzf {remote_tmp} -C {shlex.quote(staging)}")
    run(client, f"rm -f {remote_tmp}")

    run(client, f"rm -rf {app}/onecpase.previous")
    run(client, f"mv {app}/onecpase {app}/onecpase.previous")
    run(client, f"mv {shlex.quote(staging)}/onecpase {app}/onecpase")
    # Everything else overwrites in place.
    run(client, f"cp -r {shlex.quote(staging)}/. {app}/")
    run(client, f"rm -rf {shlex.quote(staging)}")
    run(client, f"chown -R {OWNER} {app}")
    print("Release swapped in; previous package kept as onecpase.previous.")

    run(client, f"{app}/venv/bin/pip install --quiet -r {app}/requirements.txt")
    print("Dependencies installed.")

    run(client, f"install -m 644 {app}/deploy/beta/onecpase.service "
                f"/etc/systemd/system/{SERVICE}.service")
    run(client, "systemctl daemon-reload")
    run(client, f"systemctl restart {SERVICE}")
    state = run(client, f"systemctl is-active {SERVICE} || true")
    print(f"{SERVICE}:", state)
    if state != "active":
        print(run(client, f"journalctl -u {SERVICE} -n 30 --no-pager || true", check=False))
        raise SystemExit(
            f"{SERVICE} did not come back up. The previous package is at "
            f"{args.app_dir}/onecpase.previous and backups are in {BACKUP_DIR}."
        )


def check_public_health() -> None:
    try:
        with urlopen(PUBLIC_HEALTH_URL, timeout=30) as response:
            print(f"{PUBLIC_HEALTH_URL} -> HTTP {response.status}: "
                  f"{response.read().decode(errors='replace')[:200]}")
    except URLError as exc:
        print(f"Health check could not reach {PUBLIC_HEALTH_URL}: {exc}")


def main() -> int:
    args = parse_args()
    paths = release_paths()
    validate_release(paths)

    client = connect(args)
    try:
        report(client, args)
        if args.check_only:
            check_public_health()
            print("Check-only: nothing was changed.")
            return 0

        if not args.yes:
            print(f"\nAbout to replace {args.app_dir} on {args.host} with "
                  f"{len(paths)} files and restart {SERVICE}.")
            if input("Type DEPLOY to continue: ").strip() != "DEPLOY":
                print("Aborted; nothing was changed.")
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            deploy(client, build_archive(paths, Path(tmp)), args)
    finally:
        client.close()

    check_public_health()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
