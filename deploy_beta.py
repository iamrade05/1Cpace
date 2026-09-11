#!/usr/bin/env python3
"""Deploy 1Cpace V8 to the beta instance on the Absolute Hosting VPS.

Run from any directory:

    python deploy_beta.py                # validate, confirm, deploy
    python deploy_beta.py --check-only   # validate locally, check the live
                                         # service, change nothing
    python deploy_beta.py --yes          # non-interactive

Beta shares a host with production 1Cpace, which lives in /opt/cpace and is a
different application entirely. This script only ever writes to /opt/cpace-beta
and refuses to run if pointed anywhere else.

Secrets, databases and uploaded member documents are never sent from this
computer and are never overwritten on the server. The server's .env is the
only place beta's configuration lives.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import shlex
import sys
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

APP_DIR = "/opt/cpace-beta"
DATA_DIR = "/var/data/cpace-beta"
SERVICE = "cpace-beta"
DEFAULT_HOST = "102.211.207.233"
DEFAULT_KEY = Path.home() / ".ssh" / "1cpace_absolute_ed25519"
PUBLIC_HEALTH_URL = "https://beta.1cpace.co.za/healthz"

# Production's directory. Guarded against, never written to.
PRODUCTION_DIR = "/opt/cpace"

INCLUDE_FILES = (
    "run.py",
    "requirements.txt",
    "gunicorn.conf.py",
    "seed_platform_admin.py",
    "deploy/beta/cpace-beta.service",
    "deploy/beta/nginx-beta.conf",
)

# onecpase/ carries the templates and static assets as package data.
INCLUDE_DIRS = ("onecpase", "scripts")

# Never shipped: real member data, secrets, local state, test tooling.
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".db", ".sqlite3", ".log")
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", "uploads", "tenants"}


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
        help="validate locally and check the live service without deploying",
    )
    args = parser.parse_args()
    target = posixpath.normpath(args.app_dir)
    if target == PRODUCTION_DIR or not target.startswith("/opt/cpace-beta"):
        raise SystemExit(
            f"Refusing to deploy to {target!r}. This script only writes to "
            f"{APP_DIR}; production is deployed with the live app's own script."
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
    archive = directory / "cpace-beta-release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in paths:
            tar.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    return archive


def connect(args: argparse.Namespace) -> paramiko.SSHClient:
    if not args.key.is_file():
        raise SystemExit(f"SSH private key was not found: {args.key}")
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
            timeout=20,
        )
    except paramiko.SSHException as exc:
        raise SystemExit(
            f"Secure SSH connection failed for {args.user}@{args.host}: {exc}"
        ) from exc
    return client


def run(client: paramiko.SSHClient, command: str, *, check: bool = True) -> str:
    _stdin, stdout, stderr = client.exec_command(command)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    status = stdout.channel.recv_exit_status()
    if check and status != 0:
        raise SystemExit(f"Remote command failed ({status}): {command}\n{err or out}")
    return out.strip()


def preflight(client: paramiko.SSHClient, args: argparse.Namespace) -> None:
    """Confirm the instance exists and is separate from production."""
    missing = run(
        client,
        f"test -d {shlex.quote(args.app_dir)} && echo yes || echo no",
    )
    if missing == "no":
        raise SystemExit(
            f"{args.app_dir} does not exist on {args.host}. Run the one-time "
            "setup in deploy/beta/README.md before the first deploy."
        )
    env_present = run(
        client, f"test -f {shlex.quote(args.app_dir)}/.env && echo yes || echo no"
    )
    if env_present == "no":
        raise SystemExit(
            f"{args.app_dir}/.env is missing. Beta will not start without it — "
            "see deploy/beta/README.md."
        )
    print(f"Remote: {args.app_dir} present, .env present.")
    print("Service:", run(client, f"systemctl is-active {SERVICE} || true"))


def deploy(client: paramiko.SSHClient, archive: Path, args: argparse.Namespace) -> None:
    remote_tmp = f"/tmp/cpace-beta-{uuid.uuid4().hex}.tar.gz"
    sftp = client.open_sftp()
    try:
        sftp.put(str(archive), remote_tmp)
    finally:
        sftp.close()
    print(f"Uploaded release to {remote_tmp}.")

    app = shlex.quote(args.app_dir)
    # Extract over the tree. The archive holds no .env, no database and no
    # uploads, so none of those can be clobbered by this step.
    run(client, f"tar -xzf {remote_tmp} -C {app}")
    run(client, f"rm -f {remote_tmp}")
    run(client, f"chown -R cpace:cpace {app}")
    print("Release extracted.")

    run(client, f"{app}/venv/bin/pip install --quiet -r {app}/requirements.txt")
    print("Dependencies installed.")

    run(client, f"install -D -m 644 {app}/deploy/beta/cpace-beta.service "
                f"/etc/systemd/system/{SERVICE}.service")
    run(client, "systemctl daemon-reload")
    run(client, f"systemctl restart {SERVICE}")
    print(f"{SERVICE} restarted:", run(client, f"systemctl is-active {SERVICE} || true"))


def check_public_health() -> None:
    try:
        with urlopen(PUBLIC_HEALTH_URL, timeout=30) as response:
            body = response.read().decode(errors="replace")
            print(f"{PUBLIC_HEALTH_URL} -> HTTP {response.status}: {body[:200]}")
    except URLError as exc:
        print(f"Health check could not reach {PUBLIC_HEALTH_URL}: {exc}")


def main() -> int:
    args = parse_args()
    paths = release_paths()
    validate_release(paths)

    client = connect(args)
    try:
        preflight(client, args)
        if args.check_only:
            check_public_health()
            print("Check-only: nothing was changed.")
            return 0

        if not args.yes:
            print(f"\nAbout to deploy {len(paths)} files to {args.app_dir} on {args.host}.")
            if input("Type DEPLOY to continue: ").strip() != "DEPLOY":
                print("Aborted; nothing was changed.")
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            archive = build_archive(paths, Path(tmp))
            deploy(client, archive, args)
    finally:
        client.close()

    check_public_health()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
