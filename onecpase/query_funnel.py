"""Reception query evidence, deadlines and durable client communications.

The caller owns financial transactions. No helper here sends bank instructions.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import current_app, g
from .encryption import decrypt, decrypt_member, encrypt

LOCAL = ZoneInfo('Africa/Johannesburg')


def now():
    return datetime.now(timezone.utc)


def local_today():
    return datetime.now(LOCAL).date()


def day(value):
    value = str(value)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.astimezone(LOCAL).date() if parsed.tzinfo else parsed.date()


def money(value):
    return Decimal(str(value or 0)).quantize(Decimal('.01'))


def ensure_schema(db, backend='sqlite'):
    identity = 'SERIAL PRIMARY KEY' if backend == 'postgresql' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    db.execute(f'''CREATE TABLE IF NOT EXISTS query_communications (
        id {identity}, query_id INTEGER NOT NULL REFERENCES queries(id),
        event_key TEXT NOT NULL, channel TEXT NOT NULL, recipient TEXT NOT NULL,
        body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
        sent_at TEXT, error TEXT, UNIQUE(query_id,event_key,channel))''')
    db.execute('''CREATE TABLE IF NOT EXISTS query_freeze_execution (
        query_id INTEGER PRIMARY KEY REFERENCES queries(id), member_id INTEGER NOT NULL,
        start_date TEXT NOT NULL,end_date TEXT NOT NULL,prior_status TEXT NOT NULL,
        reference TEXT NOT NULL,executed_by INTEGER NOT NULL,executed_at TEXT NOT NULL,
        activated INTEGER NOT NULL DEFAULT 0,ended INTEGER NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS query_refund_claims (
        collection_id INTEGER PRIMARY KEY REFERENCES collections(id),
        query_id INTEGER NOT NULL REFERENCES queries(id))''')


def queue_client(db, qry, event_key, text):
    member = db.execute('SELECT * FROM members WHERE id=?', (qry['member_id'],)).fetchone()
    if not member:
        return
    member = decrypt_member(member)
    gym = dict(g.get('tenant') or {}).get('name', 'Your gym')
    body = f"{gym}\nQuery {qry['reference'] or qry['id']}\n\n{text}\n\nPlease contact Reception if you need assistance."
    for channel, recipient in [('email', member.get('email')), ('whatsapp', member.get('contact'))]:
        db.execute('''INSERT INTO query_communications(query_id,event_key,channel,recipient,body,status,created_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(query_id,event_key,channel) DO NOTHING''',
            (qry['id'], event_key, channel, encrypt(recipient or ''), encrypt(body),
             'pending' if recipient else 'failed', now().isoformat()))


def queue_owner(db,qry,owner,stage):
    user=db.execute('SELECT id,email FROM users WHERE id=? AND active=1',(owner,)).fetchone()
    if not user:return
    email=user['email']
    if not email:
        from .query_routing import enabled,staff,STAFF_EMAILS
        if enabled():
            for name in ('mvuleni','ayrton','thato'):
                match=staff(db,name)
                if match and match['id']==owner:email=STAFF_EMAILS[name];break
    db.execute('''INSERT INTO query_communications(query_id,event_key,channel,recipient,body,status,created_at)
        VALUES(?,?,'staff_email',?,?,?,?)''',
        (qry['id'],'owner:'+str(owner)+':'+stage+':'+now().isoformat(),encrypt(email or ''),
         encrypt(f"Query {qry['reference'] or qry['id']}: {stage.replace('_',' ')}.\nSign in to 1Cpace and open Queries to review your assigned step."),
         'pending' if email else 'failed',now().isoformat()))


def dispatch(db, limit=10):
    """Send only committed outbox rows; failures remain visible for Reception.

    A claimed row is never silently resent after a process crash, since SMTP
    may already have accepted it. Reception can explicitly retry such a row.
    """
    from flask_mail import Message
    from .extensions import mail
    from .comms import send_whatsapp_message
    if current_app.testing or current_app.config.get('MAIL_SUPPRESS_SEND'):
        return
    rows = db.execute("SELECT * FROM query_communications WHERE status='pending' ORDER BY id LIMIT ?", (limit,)).fetchall()
    for row in rows:
        claimed = db.execute("UPDATE query_communications SET status='sending',attempts=attempts+1 WHERE id=? AND status='pending'", (row['id'],))
        db.commit()
        if claimed.rowcount != 1:
            continue
        try:
            recipient, text = decrypt(row['recipient']), decrypt(row['body'])
            if not recipient:
                raise ValueError('Member contact details are missing')
            if row['channel'] in ('email','staff_email'):
                if not current_app.config.get('MAIL_SERVER'):
                    raise ValueError('Email transport is not configured')
                from .client_email import department_message
                mail.send(department_message('reception', 'ELEv8 — Your query update', [recipient], body=text))
                status = 'sent'
            else:
                result = send_whatsapp_message(current_app.config.get('WA_RECEPTION_PHONE_ID', ''), recipient, text)
                if result.get('error'):
                    raise ValueError('WhatsApp send failed; check connection in Admin')
                if not result.get('messages') and not result.get('queued'):
                    raise ValueError('WhatsApp provider did not accept the message')
                status = 'queued' if result.get('queued') else 'accepted'
            db.execute('UPDATE query_communications SET status=?,sent_at=?,error=NULL WHERE id=?',
                       (status, now().isoformat(), row['id']))
        except Exception:
            # Keep addresses, signed links and provider credentials out of logs.
            db.execute("UPDATE query_communications SET status='failed',error=? WHERE id=?",
                       ('Delivery failed. Check member contact details and the email/WhatsApp connection, then retry.', row['id']))
        db.commit()


def record_whatsapp_result(db, phone, text, status):
    from .whatsapp_web import normalise_phone
    for row in db.execute("SELECT id,recipient,body FROM query_communications WHERE channel='whatsapp' AND status IN ('queued','sending') ORDER BY id").fetchall():
        if normalise_phone(decrypt(row['recipient'])) == normalise_phone(phone) and decrypt(row['body']) == text:
            db.execute('UPDATE query_communications SET status=?,sent_at=? WHERE id=?', (status, now().isoformat(), row['id']))
            break


def settlement(db, qry, today=None):
    """Read accepted terms and allocate genuine receipts, without changing money.

    Payment purpose does not exclude a receipt under the user's policy.
    Date-only same-day receipts need an explicit ordering check by Reception.
    """
    today = today or datetime.now(LOCAL).date()
    result = dict(accepted=False, option=None, signed_at=None, total='0.00', paid='0.00',
                  balance='0.00', payments=[], instalments=[], paid_count=0, review_on=None,
                  ready=False, visits=[], ambiguous_payments=[], refunds=[])
    signed = db.execute('SELECT * FROM query_form_submissions WHERE query_id=? ORDER BY id DESC LIMIT 1', (qry['id'],)).fetchone()
    if not signed:
        return result
    snapshot, payload = json.loads(decrypt(signed['snapshot'])), json.loads(decrypt(signed['payload']))
    quote = snapshot.get('quote')
    if not quote:
        return result
    option = payload.get('payment_option') or quote.get('selected_option')
    if option not in ('cash', 'instalments'):
        return result
    # Correcting contact details/evidence does not reset payments already made
    # under the same accepted quote and payment option.
    for previous in db.execute('SELECT * FROM query_form_submissions WHERE query_id=? ORDER BY id',(qry['id'],)).fetchall():
        prior_snapshot=json.loads(decrypt(previous['snapshot']))
        prior_quote=prior_snapshot.get('quote') or {}
        prior_fields=json.loads(decrypt(previous['payload']))
        if prior_quote.get('id')==quote.get('id') and (prior_fields.get('payment_option') or prior_quote.get('selected_option'))==option:
            signed=previous
            break
    signed_day = day(signed['submitted_at'])
    total = money(quote.get('cash_total') if option == 'cash' else quote.get('instalment_total'))
    result.update(accepted=True, option=option, signed_at=signed['submitted_at'], total=str(total))
    case = db.execute('SELECT details FROM query_workflows WHERE query_id=?', (qry['id'],)).fetchone()
    details = json.loads(decrypt(case['details'])) if case else {}
    confirmed_same_day = set(details.get('settlement_order', {}).get('collection_ids', []))
    # A timestamp on the receipt can prove ordering; a date-only legacy record cannot.
    signed_clock = datetime.fromisoformat(signed['submitted_at']).astimezone(LOCAL).replace(tzinfo=None)
    seen, paid, last = set(), Decimal('0.00'), None
    # Include the preceding UTC date: 22:30 UTC is already the next gym day.
    for raw in db.execute("SELECT * FROM active_collections WHERE member_id=? AND status IN ('paid','partial') AND amount_paid>0 AND collection_date>=? ORDER BY collection_date,id", (qry['member_id'],(signed_day-timedelta(days=1)).isoformat())).fetchall():
        row = dict(raw)
        notes = row.get('notes') or ''
        if re.search(r'(?:^|[|;])\s*type\s*=\s*Discount\b', notes, re.I) or (row.get('method') or '').lower() in ('discount','adjustment'):
            continue
        source = re.search(r'(?:nupay_txn|source_key|transaction_id)=([^|]+)', notes)
        key = ('source', source.group(1).strip()) if source else ('row', row['id'])
        if key in seen:
            continue
        try:
            paid_day = day(row['collection_date'])
        except (ValueError, TypeError):
            continue
        if paid_day < signed_day or paid_day > today:
            continue
        if paid_day == signed_day:
            raw_time = str(row['collection_date'])
            if len(raw_time) > 10:
                clock = datetime.fromisoformat(raw_time)
                if clock.tzinfo:
                    clock = clock.astimezone(LOCAL).replace(tzinfo=None)
                if clock < signed_clock:
                    continue
            elif row['id'] not in confirmed_same_day:
                result['ambiguous_payments'].append(row)
                continue
        seen.add(key)
        paid += money(row['amount_paid'])
        last = max(last, paid_day) if last else paid_day
        result['payments'].append(row)
    eligible={row['id']:money(row['amount_paid']) for row in result['payments']}
    for refunded in db.execute("SELECT w.query_id,w.details FROM query_workflows w JOIN queries q ON q.id=w.query_id WHERE w.kind='refund' AND w.stage IN ('payment_made','complete') AND q.member_id=?",(qry['member_id'],)).fetchall():
        refund=json.loads(decrypt(refunded['details']))
        matching=set(refund.get('collection_ids',[])).intersection(eligible)
        if matching and refund.get('payment'):
            deduction=min(money(refund['payment']['amount']),sum(eligible[cid] for cid in matching))
            paid-=deduction
            result['refunds'].append(dict(query_id=refunded['query_id'],amount=str(deduction)))
    result.update(paid=str(paid), balance=str(max(Decimal('0.00'), total-paid)))
    if option == 'instalments':
        schedule = quote.get('instalment_schedule') or quote.get('instalment_payments') or []
        dates = quote.get('instalment_dates') or []
        if isinstance(schedule, str):
            schedule = json.loads(schedule)
        if isinstance(dates, str):
            dates = json.loads(dates)
        remaining = paid
        for index, amount in enumerate(schedule):
            due = money(amount)
            allocated = min(remaining, due)
            remaining -= allocated
            result['instalments'].append(dict(number=index+1, due_date=dates[index] if index < len(dates) else None,
                                               amount=str(due), paid=str(allocated), complete=allocated >= due))
        result['paid_count'] = sum(item['complete'] for item in result['instalments'])
        if len(schedule) == 6 and len(dates) == 6 and result['paid_count'] == 6 and last:
            review = max(last, date.fromisoformat(dates[-1])) + timedelta(days=7)
            result['review_on'] = review.isoformat()
            result['ready'] = today >= review
    else:
        result['ready'] = paid >= total
    result['visits'] = [dict(row) for row in db.execute("""SELECT id,created_at,direction FROM turnstile_events
        WHERE member_id=? AND decision IN ('granted','allowed') AND substr(created_at,1,10)>=?""",
        (qry['member_id'], signed_day.isoformat())).fetchall() if (row['direction'] or '').lower() not in ('exit','out')]
    return result


def refund_assessment(db, qry, ids, reason, contract_end=None):
    """Return evidence and an assessment, without authorising or paying money."""
    history = []
    for row in db.execute("""SELECT q.id,w.stage,w.details FROM queries q LEFT JOIN query_workflows w ON w.query_id=q.id
        WHERE q.member_id=? AND q.category='cancellation' ORDER BY q.id DESC""", (qry['member_id'],)).fetchall():
        details = json.loads(decrypt(row['details'])) if row['details'] else {}
        accepted = db.execute('SELECT submitted_at,snapshot,payload FROM query_form_submissions WHERE query_id=? ORDER BY id DESC LIMIT 1', (row['id'],)).fetchone()
        history.append(dict(query_id=row['id'], stage=row['stage'] or 'intake',
                            submitted_at=accepted['submitted_at'] if accepted else None,
                            option=(json.loads(decrypt(accepted['payload'])).get('payment_option') or (json.loads(decrypt(accepted['snapshot'])).get('quote') or {}).get('selected_option')) if accepted else None,
                            completed_at=details.get('completion',{}).get('at')))
    rows = [dict(r) for r in db.execute('SELECT * FROM active_collections WHERE member_id=? AND id IN ('+','.join('?' for _ in ids)+')', (qry['member_id'], *ids)).fetchall()] if ids else []
    result = dict(status='needs_review', reason=reason, findings=[], cancellations=history)
    if not ids:
        result.update(status='awaiting_transaction_selection',findings=['Select the successful collected transactions during Reception verification.'])
        return result
    if not rows or any(r['status'] not in ('paid','partial') or money(r['amount_paid']) <= 0 for r in rows):
        result.update(status='invalid', findings=['Successful collected payments must be identified.'])
        return result
    if any(re.search(r'\btype=Discount\b', r['notes'] or '', re.I) or (r['method'] or '').lower() in ('discount','adjustment') for r in rows):
        result.update(status='invalid', findings=['An accounting adjustment is not a refundable receipt.'])
        return result
    if reason == 'after_cancellation':
        completed = [h['completed_at'] for h in history if h['stage']=='complete' and h['completed_at']]
        if completed and all(day(r['collection_date']) > min(day(c) for c in completed) for r in rows):
            result.update(status='supported', findings=['Selected collections occurred after confirmed cancellation completion.'])
        else:
            result['findings'] = ['Cancellation completion before the debit is not established; review the cancellation/provider evidence.']
    elif reason == 'after_contract_end':
        if contract_end:
            ended=date.fromisoformat(contract_end)
            result['contract_end']=contract_end
            if all(day(r['collection_date']) > ended for r in rows):
                result.update(status='supported',findings=['Selected debits occurred after the contract end verified by Reception. Confirm no renewal or separate unpaid obligation applies.'])
            else:
                result.update(status='invalid',findings=['A selected debit occurred on or before the verified contract end.'])
        else:
            result['findings'] = ['Confirm the signed contract end and any renewal; an assumed end date cannot establish entitlement.']
    elif reason == 'reception_and_debit':
        methods = {(r['method'] or '').lower() for r in rows}
        if methods.intersection({'cash','card','eft','snapscan','other'}) and methods.intersection({'debicheck','debit_order','debit order','nupay'}):
            result['findings'] = ['Reception payment and debit found. Verify both paid the same obligation; receiving two payments alone does not prove an overpayment.']
        else:
            result.update(status='invalid', findings=['Identify both the reception receipt and the successful debit.'])
    elif reason == 'double_debit':
        refs = [re.search(r'(?:nupay_txn|source_key|transaction_id)=([^|]+)', r['notes'] or '') for r in rows]
        if len(rows)==2 and all(refs) and len({r.group(1).strip() for r in refs})==2:
            result['findings'] = ['Two distinct successful source transactions found; Reception must confirm they cover the same obligation.']
        else:
            result.update(status='invalid', findings=['Two distinct successful debit references are required.'])
    else:
        result['findings'] = ['Reception must record the supporting entitlement and payment evidence.']
    return result


def refresh(db, today=None):
    """Scheduled/query-page maintenance. Never fabricates a payment or approval."""
    from .query_forms import save_case,staff_id,reviewer_id,reception_owner
    today = today or datetime.now(LOCAL).date()
    # Earlier cancellation forms stopped at an approval stage, without the
    # settlement/non-attendance funnel. Return them to Reception, retaining
    # every signed document and existing decision for review.
    for row in db.execute("SELECT * FROM query_workflows WHERE kind='cancellation' AND stage IN ('pending_approval','approved_awaiting_execution')").fetchall():
        qry=db.execute('SELECT * FROM queries WHERE id=?',(row['query_id'],)).fetchone()
        if not qry or qry['status'] in ('closed','resolved'):continue
        details=json.loads(decrypt(row['details']))
        details['funnel_upgrade']={'previous_stage':row['stage'],'at':now().isoformat()}
        save_case(db,qry,dict(row),'verification',details,reception_owner(qry,details))
    for row in db.execute("SELECT * FROM query_workflows WHERE stage IN ('pending_approval','management_review','awaiting_payment','manager_cancel_old_mandate','cancelled_awaiting_mandate_review') AND owner_id IS NULL").fetchall():
        qry=db.execute('SELECT * FROM queries WHERE id=?',(row['query_id'],)).fetchone()
        if not qry or qry['status'] in ('closed','resolved'):continue
        owner=staff_id(db,'QUERY_REFUND_PROCESSOR_USER_ID') if row['stage']=='awaiting_payment' else reviewer_id(db,row['kind'])
        if owner:
            db.execute('UPDATE query_workflows SET owner_id=? WHERE query_id=?',(owner,row['query_id']))
            db.execute("UPDATE queries SET assigned_to=?,department='Management' WHERE id=?",(owner,row['query_id']))
    for row in db.execute("SELECT * FROM query_workflows WHERE kind='cancellation' AND stage='awaiting_settlement'").fetchall():
        qry = db.execute('SELECT * FROM queries WHERE id=?', (row['query_id'],)).fetchone()
        if not qry or qry['status'] in ('closed','resolved'):
            continue
        report = settlement(db, qry, today)
        if report['ready']:
            details = json.loads(decrypt(row['details']))
            save_case(db, qry, dict(row), 'settlement_verification', details, reception_owner(qry,details))
    for row in db.execute('SELECT * FROM query_freeze_execution WHERE ended=0').fetchall():
        if row['end_date'] < today.isoformat():
            if row['activated']:
                db.execute("UPDATE members SET member_status=? WHERE id=? AND member_status='Frozen'", (row['prior_status'], row['member_id']))
            db.execute('UPDATE query_freeze_execution SET ended=1 WHERE query_id=?', (row['query_id'],))
        elif row['start_date'] <= today.isoformat():
            db.execute("UPDATE members SET member_status='Frozen' WHERE id=? AND member_status=?", (row['member_id'], row['prior_status']))
            db.execute('UPDATE query_freeze_execution SET activated=1 WHERE query_id=?', (row['query_id'],))
    db.execute("""UPDATE queries SET priority='urgent' WHERE id IN
        (SELECT query_id FROM query_workflows WHERE stage IN ('manager_cancel_debit','cancelled_awaiting_mandate_review'))
        AND status NOT IN ('closed','resolved') AND due_date IS NOT NULL AND due_date<=?""", (today.isoformat(),))
