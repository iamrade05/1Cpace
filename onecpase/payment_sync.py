"""Scheduled payment ingestion shared by the dashboard and member histories."""
import json
import logging
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
ZONE = ZoneInfo('Africa/Johannesburg')


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS payment_sync_state (
        source TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT,
        finished_at TEXT, last_success_at TEXT, coverage_end TEXT,
        summary TEXT, error TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS payment_sync_entries (
        source TEXT NOT NULL, source_key TEXT NOT NULL, signature TEXT NOT NULL,
        collection_id INTEGER NOT NULL, PRIMARY KEY(source, source_key),
        FOREIGN KEY(collection_id) REFERENCES collections(id))''')
    # A completed import can still contain records held for review. Preserve
    # that warning while recording that the report itself was successfully read.
    db.execute('''UPDATE payment_sync_state SET last_success_at=finished_at,
        coverage_end=substr(finished_at,1,10) WHERE status IN ('success','needs_attention')
        AND last_success_at IS NULL AND finished_at IS NOT NULL''')


def status_rows(db):
    try:
        rows = {r['source']: dict(r) for r in db.execute('SELECT * FROM payment_sync_state')}
    except Exception as exc:
        if 'no such table' not in str(exc) and 'does not exist' not in str(exc):
            raise
        rows = {}
    result = []
    now = datetime.now(ZONE)
    for source, label in [('itensity', 'Itensity transactions'), ('nupay', 'NuPay payments and mandates')]:
        row = rows.get(source, dict(source=source, status='never_run', last_success_at=None))
        row['label'] = label
        row['stale'] = not row.get('last_success_at') or (
            now - datetime.fromisoformat(row['last_success_at'])).total_seconds() > 36*3600
        row['counts'] = json.loads(row.get('summary') or '{}')
        result.append(row)
    return result


def _kind(notes):
    found = re.search(r'(?:^|\|)\s*type=([^|]+)', notes or '')
    return found.group(1).strip() if found else ''


def _signature(values):
    return json.dumps([values['member_id'], values['collection_date'],
                       round(values['outstanding_balance'], 2), round(values['amount_paid'], 2),
                       _kind(values['notes'])], separators=(',', ':'))


def _existing(db):
    return [dict(r) for r in db.execute('''SELECT id,member_id,collection_date,
        outstanding_balance,amount_paid,method,status,notes FROM collections
        WHERE notes LIKE '%Itensity%' OR notes LIKE 'WEBAPPL ledger import%'
           OR notes LIKE 'Reviewed payment ledger%'
           OR notes LIKE '%nupay_txn=%' ''')]


def import_itensity(db, rows, *, include_debit_orders=False):
    from scripts.import_itensity_transactions import collection_values, CHARGE_TYPES, PAYMENT_TYPES
    counts = Counter()
    refs = {str(r['itensity_ref']).strip(): r['id'] for r in db.execute(
        "SELECT id,itensity_ref FROM members WHERE COALESCE(itensity_ref,'')!=''")}
    existing = _existing(db)
    signatures, nupay_members, reviewed_ids = defaultdict(list), defaultdict(list), {}
    for entry in existing:
        signatures[_signature(entry)].append(entry)
        if 'nupay_txn=' in (entry['notes'] or '') or 'source_key=nupay:' in (entry['notes'] or ''):
            nupay_members[(entry['member_id'],_kind(entry['notes']))].append(entry)
        match = re.search(r'(?:^|\|)\s*source_key=itensity:([^|\s]+)',entry['notes'] or '')
        if match:
            reviewed_ids[match.group(1)] = entry
    linked = {(r['source_key']): dict(r) for r in db.execute(
        "SELECT * FROM payment_sync_entries WHERE source='itensity'")}
    used = {r['collection_id'] for r in linked.values()}
    fields = {'uid':'Ref','date':'Date','type':'Type','amount':'Amount','desc':'Description',
              'loaded':'Loaded By','stamp':'Loaded On','sales':'Sales Rep'}
    for raw in rows:
        mid = refs.get(str(raw.get('uid') or '').strip())
        if mid is None:
            counts['unmatched'] += 1
            continue
        kind = str(raw.get('type') or '').strip()
        # Match the approved historical-ledger policy: NuPay owns debit-order
        # outcomes. Itensity mirrors those payments using different posting
        # dates, so importing both ledgers would count the same money twice.
        if not include_debit_orders and kind in {'Debit Order Payment','Debit Order Unpaid'}:
            counts['deferred_to_nupay'] += 1
            continue
        if kind not in CHARGE_TYPES | set(PAYMENT_TYPES) | {'Discount', 'Debit Order Unpaid'}:
            counts['needs_review'] += 1
            continue
        try:
            value = Decimal(str(raw.get('amount') or '').replace(',','').replace('R','').strip())
            if not value.is_finite():
                raise ValueError('non-finite amount')
            if not value:
                counts['ignored_zero'] += 1
                continue
            date.fromisoformat(str(raw['date'])[:10])
        except (ValueError, InvalidOperation, KeyError):
            counts['unreadable'] += 1
            continue
        values = collection_values({target: raw.get(key) for key,target in fields.items()}, mid)
        sig, key = _signature(values), str(raw['id'])
        if key in linked:
            counts['already_recorded' if linked[key]['signature'] == sig else 'needs_review'] += 1
            continue
        if key in reviewed_ids and _signature(reviewed_ids[key]) != sig:
            counts['needs_review'] += 1
            continue
        matches = ([reviewed_ids[key]] if key in reviewed_ids else
                   [r for r in signatures.get(sig,[]) if r['id'] not in used])
        if not matches and kind in {'Debit Order Payment', 'Recurring Fee'}:
            # NuPay dates can precede the Itensity posting date. Only reconcile
            # across sources, never collapse separate Itensity transactions.
            matches = [r for r in nupay_members.get((mid,kind),[]) if r['id'] not in used
                       and r['member_id'] == mid and _kind(r['notes']) == kind
                       and round(r['amount_paid'],2) == round(values['amount_paid'],2)
                       and round(r['outstanding_balance'],2) == round(values['outstanding_balance'],2)
                       and ((kind == 'Recurring Fee' and str(r['collection_date'])[:7] == values['collection_date'][:7])
                            or abs((date.fromisoformat(str(r['collection_date'])[:10]) -
                                    date.fromisoformat(values['collection_date'][:10])).days) <= 7)]
            if len(matches) > 1:
                counts['needs_review'] += 1
                continue
        if matches:
            cid = matches[0]['id']
            counts['reconciled_existing'] += 1
        else:
            cid = db.execute('''INSERT INTO collections(member_id,outstanding_balance,discount_pct,
                amount_paid,collection_date,method,status,notes,created_by) VALUES(?,?,?,?,?,?,?,?,NULL)''',
                tuple(values[k] for k in ('member_id','outstanding_balance','discount_pct','amount_paid',
                                          'collection_date','method','status','notes'))).lastrowid
            counts['inserted'] += 1
        used.add(cid)
        db.execute('INSERT INTO payment_sync_entries(source,source_key,signature,collection_id) VALUES(?,?,?,?)',
                   ('itensity',key,sig,cid))
        linked[key] = dict(signature=sig, collection_id=cid)
    return counts


def import_nupay(db, rows):
    from . import nupay_transactions as nt
    from .nupay_sync import _member_indexes, _member_for_report_row
    by_ref, by_id = _member_indexes(db)
    existing = _existing(db)
    member_payments, tags = defaultdict(list), set()
    for entry in existing:
        member_payments[entry['member_id']].append(entry)
        tags.update(part for part in (entry['notes'] or '').split(' | ') if part.startswith('nupay_txn='))
    used, filtered, counts = set(), [], Counter()
    for raw in rows:
        row = nt.normalise_row(raw)
        mid = _member_for_report_row(row, by_ref, by_id)
        if nt._status(row) == 'success' and mid is not None:
            if not nt._day(row.get('action_date')) or nt._money(row.get('instalment_amount')) <= 0:
                counts['unreadable'] += 1
                continue
            key = nt.transaction_key(row)
            # An exact NuPay identity is authoritative; do not match a second
            # equal-sized debit to the same imported collection row.
            tag = 'nupay_txn=' + key
            if tag in tags:
                filtered.append(row)
                continue
            candidates = [r for r in member_payments.get(mid['id'],[]) if r['id'] not in used
                and r['member_id'] == mid['id'] and _kind(r['notes']) == 'Debit Order Payment'
                and 'nupay_txn=' not in (r['notes'] or '')
                and round(r['amount_paid'],2) == nt._money(row.get('instalment_amount'))
                and abs((date.fromisoformat(str(r['collection_date'])[:10]) -
                         date.fromisoformat(nt._day(row.get('action_date')))).days) <= 7]
            if len(candidates) > 1:
                counts['needs_review'] += 1
                continue
            if candidates:
                match = candidates[0]
                db.execute("UPDATE collections SET notes=notes || ? WHERE id=?", (' | '+tag,match['id']))
                match['notes'] += ' | '+tag
                tags.add(tag)
                used.add(match['id'])
                counts['reconciled_existing'] += 1
                continue
        filtered.append(row)
    plan = nt.plan_import(db, filtered)
    nt.apply_plan(db, plan)
    counts.update(plan['summary'])
    return counts


def run_source(app, tenant, source):
    from flask import g
    with app.app_context():
        g.tenant = dict(tenant)
        g.tenant_slug = tenant['slug']
        return _run_source(app,tenant,source)


def _run_source(app, tenant, source):
    """Fetch outside the DB transaction, then atomically import and report.

    A durable lease prevents two worker processes from fetching the same source.
    SQLite's write transaction serializes imports with the CSV fallback.
    """
    db = sqlite3.connect(tenant['db_path'], timeout=60)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    ensure_schema(db)
    db.commit()
    started = datetime.now(ZONE).isoformat(timespec='seconds')
    db.execute('BEGIN IMMEDIATE')
    previous = db.execute('SELECT * FROM payment_sync_state WHERE source=?',(source,)).fetchone()
    if previous and previous['status']=='running' and previous['started_at'] > (
            datetime.now(ZONE)-timedelta(hours=2)).isoformat():
        db.rollback(); db.close()
        return
    db.execute('''INSERT INTO payment_sync_state(source,status,started_at) VALUES(?,'running',?)
        ON CONFLICT(source) DO UPDATE SET status='running',started_at=excluded.started_at,error=NULL''',
        (source,started))
    db.commit()
    try:
        end = datetime.now(ZONE).date()
        start = end-timedelta(days=int(app.config.get('PAYMENT_SYNC_LOOKBACK_DAYS',120)))
        if previous and previous['coverage_end']:
            start = min(start,date.fromisoformat(previous['coverage_end'])-timedelta(days=31))
        if source == 'itensity':
            from scripts.pull_itensity_transactions import pull_transactions
            rows = pull_transactions(start,end)
            mandates = None
        else:
            from .nupay_driver import fetch_transaction_report_rows, fetch_mandate_report_rows
            rows = fetch_transaction_report_rows(start,end)
            mandates, message = fetch_mandate_report_rows()
            if mandates is None:
                raise RuntimeError(message)
        root = Path(app.root_path).parent
        archive = root/'data'/'payment_sync'/tenant['slug']
        archive.mkdir(parents=True,exist_ok=True)
        (archive/f'{source}_{datetime.now(ZONE):%Y%m%d_%H%M%S}.json').write_text(
            json.dumps(dict(start=start.isoformat(),end=end.isoformat(),rows=rows)),encoding='utf-8')
        from .scheduler import _backup_before_write
        _backup_before_write(root,tenant['db_path'],f"{tenant['slug']}_before_{source}_payment_sync")
        db.execute('BEGIN IMMEDIATE')
        counts = import_itensity(db,rows) if source=='itensity' else import_nupay(db,rows)
        if mandates is not None:
            from .nupay_sync import sync_from_report
            from .nupay_reconciliation import store_upload
            store_upload(db,rows,['automatic NuPay report'],None)
            result = sync_from_report(db,mandates,apply=True,import_missing=True,actor_id=None)
            counts['mandates_updated'] = result.get('updated',0)
            counts['mandates_imported'] = result.get('import',{}).get('recorded',0)
        counts['rows_read'] = len(rows)
        warning = any(counts[k] for k in ('unmatched','unreadable','needs_review'))
        stamp = datetime.now(ZONE).isoformat(timespec='seconds')
        db.execute('''UPDATE payment_sync_state SET status=?,finished_at=?,summary=?,error=NULL,
            last_success_at=?,coverage_end=? WHERE source=?''',
            ('needs_attention' if warning else 'success',stamp,json.dumps(dict(counts)),
             stamp,end.isoformat(),source))
        db.commit()
        log.info('Payment sync %s for %s: %s',source,tenant['slug'],dict(counts))
    except Exception:
        db.rollback()
        log.exception('Payment sync %s failed for %s',source,tenant['slug'])
        # Keep portal payloads/credentials out of the dashboard error field.
        db.execute("UPDATE payment_sync_state SET status='failed',finished_at=?,error=? WHERE source=?",
                   (datetime.now(ZONE).isoformat(timespec='seconds'),
                    'Report fetch or import failed. Check the payment-sync server log.',source))
        db.commit()
    finally:
        db.close()


def run_daily(app):
    from .scheduler import _find_tenant
    tenant = _find_tenant(app,app.config['ITENSITY_TENANT_SLUG'])
    if tenant is None:
        log.error('Payment sync tenant is missing; no data imported')
        return
    for source in ('itensity','nupay'):
        run_source(app,tenant,source)


def run_due(app):
    """Catch up after downtime and retry failures hourly without repeated imports."""
    from .scheduler import _find_tenant
    tenant = _find_tenant(app,app.config['ITENSITY_TENANT_SLUG'])
    if tenant is None:
        return
    now = datetime.now(ZONE)
    with sqlite3.connect(tenant['db_path'],timeout=30) as db:
        db.row_factory=sqlite3.Row
        ensure_schema(db)
        states={r['source']:dict(r) for r in db.execute('SELECT * FROM payment_sync_state')}
    for source in ('itensity','nupay'):
        row=states.get(source)
        if row:
            if row['status']=='running' and row['started_at'] > (now-timedelta(hours=2)).isoformat():
                continue
            if row['status']=='failed' and row['finished_at'] > (now-timedelta(hours=1)).isoformat():
                continue
            if row['status'] in ('success','needs_attention') and row['finished_at'][:10]==now.date().isoformat():
                continue
            if row['status'] in ('success','needs_attention') and (now.hour,now.minute)<(5,30):
                continue
        run_source(app,tenant,source)
