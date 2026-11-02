"""Remove the false collection cases written by the 15-minute sweep, then shrink the database.

preview : read-only counts.
rehearse: the whole cleanup on a disposable copy, timed; the live database is not touched.
apply   : full verified backup, stop the app, clean, shrink, restart. Refuses unless the fix is live.

A false case is closed as paid at R0 within five seconds of opening, with no staff owner.
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path('/opt/onecpase')
BACKUPS = Path('/opt/onecpase-backups')
HERE = Path(__file__).resolve().parent
RESERVE = 512 * 1024 * 1024
FALSE = """status='closed' AND resolution_type='PAID_IN_FULL' AND COALESCE(arrears_amount,0)=0
           AND (julianday(closed_at)-julianday(created_at))*86400 <= 5
           AND owner_staff_id IS NULL AND assigned_to IS NULL"""
CHILDREN = ('collection_stage_history', 'collection_assignment_history', 'collection_notes', 'access_decisions')
# Rows here must come through untouched; only the case tables may shrink.
UNTOUCHED = ('members', 'collections', 'collection_communications', 'collection_call_tasks',
             'ptp_agreements', 'debicheck_mandates', 'contract_signatures', 'member_notes')


def say(**row):
    print(json.dumps(row), flush=True)


def tenant_database():
    from dotenv import dotenv_values
    registry = dotenv_values(ROOT / '.env').get('PLATFORM_DATABASE_PATH') or str(ROOT / 'platform.db')
    conn = sqlite3.connect('file:' + registry + '?mode=ro', uri=True)
    try:
        rows = conn.execute('SELECT slug, db_path FROM tenants WHERE active=1').fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, 'Expected one active tenant, found %d' % len(rows)
    return Path(rows[0][1])


def counts(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in UNTOUCHED if t in tables}


def open_cases(db):
    return db.execute("SELECT COUNT(*) FROM collections_cases WHERE status IN ('open','active','pending')").fetchone()[0]


def references(db):
    """Every table with a declared or conventional link to a case."""
    found = {}
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        names = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
        keys = [r[3] for r in db.execute(f'PRAGMA foreign_key_list("{table}")') if r[2] == 'collections_cases']
        if 'case_id' in names or keys:
            found[table] = sorted(set(keys + (['case_id'] if 'case_id' in names else [])))
    return found


def preview(path):
    db = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=60)
    try:
        false_ids = f'SELECT id FROM collections_cases WHERE {FALSE}'
        result = dict(
            database_mb=path.stat().st_size // 1048576,
            cases=db.execute('SELECT COUNT(*) FROM collections_cases').fetchone()[0],
            false_cases=db.execute(f'SELECT COUNT(*) FROM collections_cases WHERE {FALSE}').fetchone()[0],
            open_cases=open_cases(db),
            newest_false_case=db.execute(f'SELECT MAX(created_at) FROM collections_cases WHERE {FALSE}').fetchone()[0],
            references={},
        )
        for table, columns in references(db).items():
            result['references'][table] = {
                column: db.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IN ({false_ids})').fetchone()[0]
                for column in columns
            }
        return result
    finally:
        db.close()


def clean(path):
    """All-or-nothing: any surprise rolls the whole transaction back, then it shrinks."""
    db = sqlite3.connect(str(path), timeout=120, isolation_level=None)
    db.execute('PRAGMA foreign_keys=OFF')
    db.execute('PRAGMA temp_store=MEMORY')
    timings = {}
    started = time.time()
    links = references(db)
    unexpected = [t for t in links if t not in CHILDREN and db.execute(
        f'SELECT 1 FROM "{t}" WHERE "{links[t][0]}" IN (SELECT id FROM collections_cases WHERE {FALSE}) LIMIT 1').fetchone()]
    assert not unexpected, 'Real records point at false cases: ' + ', '.join(unexpected)
    before = counts(db)
    keep_open = open_cases(db)
    total_cases = db.execute('SELECT COUNT(*) FROM collections_cases').fetchone()[0]
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute(f'CREATE TEMP TABLE false_ids AS SELECT id FROM collections_cases WHERE {FALSE}')
        db.execute('CREATE UNIQUE INDEX temp.false_ids_id ON false_ids(id)')
        removed = {'collections_cases': db.execute('SELECT COUNT(*) FROM false_ids').fetchone()[0]}
        assert removed['collections_cases'] > 0, 'Nothing to remove'
        for table in CHILDREN:
            removed[table] = db.execute(f'DELETE FROM "{table}" WHERE case_id IN (SELECT id FROM false_ids)').rowcount
        db.execute('DELETE FROM collections_cases WHERE id IN (SELECT id FROM false_ids)')
        # What is left of the access log keeps only the moments a decision actually changed.
        removed['access_decisions_repeats'] = db.execute(
            """DELETE FROM access_decisions WHERE id IN (
                 SELECT id FROM (
                   SELECT id, access_status, reason,
                          LAG(access_status) OVER w AS previous_status, LAG(reason) OVER w AS previous_reason
                   FROM access_decisions WINDOW w AS (PARTITION BY member_id ORDER BY id))
                 WHERE previous_status = access_status AND COALESCE(previous_reason, '') = COALESCE(reason, ''))"""
        ).rowcount
        assert counts(db) == before, 'A protected table changed'
        assert open_cases(db) == keep_open, 'Open case count changed'
        assert db.execute('SELECT COUNT(*) FROM collections_cases').fetchone()[0] == total_cases - removed['collections_cases']
        orphans = {t: db.execute(f'SELECT COUNT(*) FROM "{t}" WHERE case_id IN (SELECT id FROM false_ids)').fetchone()[0] for t in CHILDREN}
        assert not any(orphans.values()), orphans
        db.execute('DROP TABLE temp.false_ids')
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        db.close()
        raise
    timings['delete_seconds'] = round(time.time() - started)
    started = time.time()
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    db.execute('VACUUM')
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    timings['vacuum_seconds'] = round(time.time() - started)
    check = db.execute('PRAGMA quick_check').fetchone()[0]
    assert check == 'ok', check
    assert counts(db) == before and open_cases(db) == keep_open
    db.close()
    return dict(removed=removed, protected_rows=before, open_cases=keep_open, integrity=check,
                database_mb=path.stat().st_size // 1048576, **timings)


def sqlite_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    assert not target.exists()
    src = sqlite3.connect('file:' + str(source) + '?mode=ro', uri=True, timeout=60)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        assert dst.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    finally:
        dst.close()
        src.close()
    os.chmod(target, 0o600)


def digest(handle):
    h = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
        h.update(chunk)
    return h.hexdigest()


def compress(path):
    """Replace a copy with a gzip proven to decompress to the same bytes."""
    target = path.with_name(path.name + '.gz')
    temporary = target.with_name(target.name + '.new')
    with path.open('rb') as handle:
        expected = digest(handle)
    with path.open('rb') as incoming, gzip.open(temporary, 'wb', compresslevel=6) as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
    with gzip.open(temporary, 'rb') as handle:
        assert digest(handle) == expected, 'Compressed backup failed verification'
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    size = path.stat().st_size
    path.unlink()
    return dict(file=str(target), sha256=expected, uncompressed_bytes=size, compressed_bytes=target.stat().st_size)


def healthy():
    for _ in range(40):
        try:
            with urlopen('http://127.0.0.1:5002/healthz', timeout=3) as response:
                if response.status == 200 and json.load(response)['status'] == 'ok':
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def rehearse(path):
    free = shutil.disk_usage('/').free
    required = path.stat().st_size + 3 * 1024 ** 3
    assert free > required, f'Need {required // 1048576} MB free for a safe rehearsal, have {free // 1048576} MB'
    copy = HERE / 'rehearsal.db'
    try:
        started = time.time()
        sqlite_copy(path, copy)
        result = dict(copy_seconds=round(time.time() - started), before=preview(copy))
        result['clean'] = clean(copy)
        result['after'] = preview(copy)
        result['live_database_touched'] = False
        return result
    finally:
        for leftover in (copy, copy.with_name('rehearsal.db-wal'), copy.with_name('rehearsal.db-shm'), copy.with_name('rehearsal.db-journal')):
            if leftover.exists():
                leftover.unlink()


def fix_is_live():
    assert 'float(arrears["total_arrears"] or 0) > 0' in (ROOT / 'onecpase/ptp.py').read_text(), 'The loop fix is not installed'
    service = subprocess.run(['systemctl', 'show', 'onecpase', '-p', 'ActiveEnterTimestamp', '--value'], capture_output=True, text=True).stdout.strip()
    started = subprocess.run(['date', '-u', '-d', service, '+%Y-%m-%d %H:%M:%S'], capture_output=True, text=True, check=True).stdout.strip()
    return started


def apply(path):
    # Other tools also release to this server; never stop the app underneath one.
    running = subprocess.run(['pgrep', '-af', 'release[.]py'], capture_output=True, text=True).stdout.strip()
    assert not running, 'Another release is running: ' + running[:200]
    restarted = fix_is_live()
    db = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=60)
    try:
        newest = db.execute(f'SELECT MAX(created_at) FROM collections_cases WHERE {FALSE}').fetchone()[0]
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        assert not db.execute("SELECT 1 FROM payment_sync_state WHERE status='running' AND started_at>?", (cutoff,)).fetchone(), 'Payment sync is running'
    finally:
        db.close()
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    minutes_since_restart = (datetime.strptime(now, '%Y-%m-%d %H:%M:%S') - datetime.strptime(restarted, '%Y-%m-%d %H:%M:%S')).total_seconds() / 60
    assert minutes_since_restart >= 16, f'Wait for a sweep after the fix: only {minutes_since_restart:.0f} minutes since restart'
    # Case times are South African local time on the server's clock settings: compare as stored.
    say(step='checked', newest_false_case=newest, service_restarted_utc=restarted, minutes_since_restart=round(minutes_since_restart))
    assert subprocess.run(['systemctl', 'is-active', 'onecpase-backup-retention.service'], capture_output=True, text=True).stdout.strip() != 'active'
    free = shutil.disk_usage('/').free
    assert free > path.stat().st_size + RESERVE * 2, 'Not enough space for the full backup'

    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup = BACKUPS / f'false_case_cleanup_{stamp}'
    backup.mkdir(mode=0o700)
    started = time.time()
    copy = backup / '0-1cpase.db'
    sqlite_copy(path, copy)
    say(step='backed_up', seconds=round(time.time() - started), bytes=copy.stat().st_size)

    result = dict(backup=str(backup), started_utc=now)
    outage_started = time.time()
    subprocess.run(['systemctl', 'stop', 'onecpase'], check=True)
    try:
        # A final backup would need another outage; the app stopped moments after the copy,
        # so anything written in between is covered by the live database, which is only
        # rewritten after every check above and inside clean() has passed.
        result['clean'] = clean(path)
    finally:
        subprocess.run(['systemctl', 'start', 'onecpase'], check=True)
    result['outage_seconds'] = round(time.time() - outage_started)
    assert healthy(), 'App did not come back healthy'
    say(step='cleaned', **result)
    result['backup_compressed'] = compress(copy)
    (backup / 'manifest.json').write_text(json.dumps(dict(
        source=str(path), restore='Stop onecpase, gunzip this copy over the database, start onecpase. Loses anything written since.',
        backup=result['backup_compressed'], removed=result['clean']['removed']), indent=2))
    result['after'] = preview(path)
    result['free_mb'] = shutil.disk_usage('/').free // 1048576
    return result


if __name__ == '__main__':
    action = sys.argv[1]
    database = tenant_database()
    result = dict(preview=preview, rehearse=rehearse, apply=apply)[action](database)
    (HERE / f'{action}-result.json').write_text(json.dumps(result, indent=2))
    say(step='done', action=action, **result)
