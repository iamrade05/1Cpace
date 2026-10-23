"""Rehearse startup migrations on disposable copies of local SQLite databases.

Reads the local .env for database locations and the existing encryption key.
Original databases are opened read-only; no provider or hardware jobs run.
"""
from pathlib import Path
import json
import os
import sqlite3
import tempfile

from dotenv import dotenv_values

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
from onecpase import create_app

ROOT = Path(__file__).resolve().parents[1]
TABLES = ('members', 'users', 'leads', 'membership_applications',
          'collections', 'debicheck_mandates', 'ptp_agreements', 'queries')


def snapshot(source, destination):
    source = Path(source).resolve()
    original = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    copied = sqlite3.connect(destination)
    try:
        original.backup(copied)
    finally:
        copied.close()
        original.close()


def counts(path):
    conn = sqlite3.connect(path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        return {name: conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                for name in TABLES if name in tables}
    finally:
        conn.close()


def main():
    config = {**dotenv_values(ROOT / '.env'), **os.environ}
    if config.get('DATABASE_URL'):
        raise SystemExit('This rehearsal supports SQLite; PostgreSQL needs target acceptance.')
    key = config.get('ENCRYPTION_KEY')
    if not key:
        raise SystemExit('The existing encryption key is required; no replacement key will be generated.')
    with tempfile.TemporaryDirectory(prefix='onecpase-migration-') as directory:
        temporary = Path(directory)
        registry = temporary / 'platform.db'
        default = temporary / 'default.db'
        snapshot(config.get('PLATFORM_DATABASE_PATH') or ROOT / 'platform.db', registry)
        snapshot(config.get('DATABASE_PATH') or ROOT / '1cpase.db', default)
        copies = [default]
        connection = sqlite3.connect(registry)
        try:
            for tenant_id, source in connection.execute('SELECT id, db_path FROM tenants').fetchall():
                destination = temporary / f'tenant-{tenant_id}.db'
                snapshot(source, destination)
                copies.append(destination)
                connection.execute('UPDATE tenants SET db_path=? WHERE id=?', (str(destination), tenant_id))
            connection.commit()
        finally:
            connection.close()
        before = {path: counts(path) for path in copies}
        create_app({
            'TESTING': True, 'ENVIRONMENT': 'testing', 'DATABASE_URL': '',
            'DATABASE_PATH': str(default), 'PLATFORM_DATABASE_PATH': str(registry),
            'TENANTS_DIR': str(temporary / 'tenants'), 'UPLOAD_FOLDER': str(temporary / 'uploads'),
            'ENCRYPTION_KEY': key, 'SECRET_KEY': 'migration-rehearsal-only',
            'TURNSTILE_ENABLED': False, 'SCHEDULER_ENABLED': False,
            'WHATSAPP_WEB_ENABLED': False, 'RATELIMIT_STORAGE_URL': 'memory://',
            'ACTIVE_PHASE': 'all',
        })
        for path in copies:
            after = counts(path)
            assert all(after[name] == number for name, number in before[path].items()), 'Migration changed operational row counts'
        print(json.dumps({'databases_checked': len(copies), 'integrity': 'ok',
                          'operational_row_counts_preserved': True, 'originals_modified': False}))


if __name__ == '__main__':
    main()
