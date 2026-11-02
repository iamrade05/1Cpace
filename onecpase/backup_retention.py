"""Keep five complete database snapshots and five deployment rollback sets."""
import argparse
import errno
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
import tempfile

KEEP_BACKUPS = 5
BACKUP_RESERVE_BYTES = 512 * 1024 * 1024
_STAMP = re.compile(r'(\d{8})(?:[T_](\d{6}))?')


def _root(directory):
    directory = Path(directory).absolute()
    if directory.is_symlink() or directory.resolve() != directory or directory.parent == directory:
        raise ValueError('Backup directory must be a real, explicit directory.')
    if not directory.is_dir():
        raise ValueError('Backup directory is missing.')
    return directory


def _stamp(path):
    match = _STAMP.search(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match[1] + (match[2] or '000000'), '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _remove(root, path):
    # Resolve every deletion target and refuse links before removing anything.
    if path.is_symlink() or path.parent != root or not path.resolve().is_relative_to(root):
        raise ValueError('Refusing a deletion outside the backup directory.')
    if path.is_dir():
        shutil.rmtree(path)  # rmtree unlinks nested symlinks; it never follows them.
    else:
        path.unlink()


def _valid_database(path):
    try:
        with path.open('rb') as stream:
            if stream.read(16) != b'SQLite format 3\x00':
                return False
        journal = path.with_name(path.name + '-journal')
        if journal.exists() and journal.stat().st_size:
            return False
        connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            return connection.execute('PRAGMA quick_check').fetchall() == [('ok',)]
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return False


def prune_databases(directory, keep=KEEP_BACKUPS, apply=False, minimum_age_seconds=900):
    root = _root(directory)
    candidates = [p for p in root.iterdir() if p.is_file() and not p.is_symlink()
                  and p.suffix in {'.db', '.sqlite3'} and _stamp(p) is not None]
    candidates.sort(key=lambda p: (_stamp(p), p.name), reverse=True)
    retained = []
    invalid = []
    for path in candidates:
        if _valid_database(path):
            retained.append(path)
            if len(retained) == keep:
                break
        else:
            invalid.append(path)
    # A failed/incomplete backup never displaces a good restore point.
    removals = [] if len(retained) < keep else [p for p in candidates if p not in retained]
    now = time.time()
    removals = [p for p in removals if now - p.stat().st_mtime >= minimum_age_seconds]
    if apply:
        for path in removals:
            _remove(root, path)
            for suffix in ('-journal', '-wal', '-shm'):
                sidecar = path.with_name(path.name + suffix)
                if sidecar.exists():
                    _remove(root, sidecar)
    return {'type': 'database', 'retained': [p.name for p in retained],
            'invalid': [p.name for p in invalid], 'removed' if apply else 'would_remove': [p.name for p in removals]}


def prune_deployments(directory, keep=KEEP_BACKUPS, apply=False, minimum_age_seconds=900):
    root = _root(directory)
    groups = {}
    for path in root.iterdir():
        if path.is_symlink() or _stamp(path) is None:
            continue
        # Matching code/data archives belong to one dated deployment set.
        groups.setdefault(_stamp(path), []).append(path)
    newest = sorted(groups, reverse=True)[:keep]
    now = time.time()
    removals = []
    for stamp, paths in groups.items():
        if stamp in newest:
            continue
        if any(now - p.stat().st_mtime < minimum_age_seconds for p in paths):
            continue
        removals.extend(paths)
    if apply:
        for path in removals:
            _remove(root, path)
    return {'type': 'deployment', 'retained_sets': [[p.name for p in groups[s]] for s in newest],
            'removed' if apply else 'would_remove': [p.name for p in removals]}


def ensure_backup_capacity(db_path, backup_dir, reserve_bytes=BACKUP_RESERVE_BYTES):
    """Leave working space available after copying the database and its WAL."""
    source = Path(db_path)
    required = source.stat().st_size
    wal = source.with_name(source.name + '-wal')
    if wal.exists():
        required += wal.stat().st_size
    required += reserve_bytes
    available = shutil.disk_usage(backup_dir).free
    if available < required:
        raise OSError(errno.ENOSPC,
                      f"Backup needs {required} free bytes including the working-space reserve; "
                      f"only {available} bytes are available.")
    return required


def snapshot_database(db_path, backup_dir, filename):
    """Publish only a complete, checked snapshot, then retain the newest five."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(exist_ok=True)
    root = _root(backup_dir)
    destination = root / filename
    if destination.parent != root or destination.exists() or destination.is_symlink():
        raise ValueError('Backup destination must be a new file in the backup directory.')
    ensure_backup_capacity(db_path, root)
    descriptor, temporary_name = tempfile.mkstemp(prefix=destination.name + '.', suffix='.partial', dir=root)
    os.close(descriptor)
    temporary = Path(temporary_name)
    source = target = None
    try:
        source = sqlite3.connect(db_path)
        target = sqlite3.connect(temporary)
        source.backup(target)
        assert target.execute('PRAGMA quick_check').fetchall() == [('ok',)], 'Backup integrity check failed'
        target.close(); target = None
        source.close(); source = None
        with temporary.open('r+b') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        if temporary.exists():
            temporary.unlink()
    prune_databases(root, apply=True, minimum_age_seconds=0)
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-dir', required=True)
    parser.add_argument('--deployment-dir', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--minimum-age-seconds', type=int, default=900)
    args = parser.parse_args()
    print(json.dumps([
        prune_databases(args.database_dir, apply=args.apply, minimum_age_seconds=args.minimum_age_seconds),
        prune_deployments(args.deployment_dir, apply=args.apply, minimum_age_seconds=args.minimum_age_seconds),
    ], indent=2))


if __name__ == '__main__':
    main()
