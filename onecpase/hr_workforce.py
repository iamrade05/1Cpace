"""HR accounting and workflow rules. No access scan is interpreted as paid time."""
from calendar import monthrange
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json

from flask import session

POLICY = 'ZA-BCEA-2026-10-v1'
LEAVE_TYPES = ('annual', 'sick', 'family', 'parental', 'study', 'unpaid')
DECLARATION = 'I confirm that the work and break times in this version are accurate and that I have identified any missing or incorrect entries.'


def today():
    return datetime.now(timezone(timedelta(hours=2))).date()


def department_key(value):
    value=(value or 'General').strip().casefold()
    return {'cleaners':'cleaner','cleaning':'cleaner','training':'fitness',
            'fitness instructor':'fitness','fitness instructors':'fitness'}.get(value,value)


def service_account(user):
    return bool(user and (user['is_platform_user'] or (user['username']=='admin' and not user['employee_number'])))


def migrate(db, backend='sqlite'):
    pk = 'SERIAL PRIMARY KEY' if backend == 'postgresql' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    statements = [
        '''CREATE TABLE IF NOT EXISTS hr_policy_settings (
            name TEXT PRIMARY KEY, value TEXT NOT NULL, effective_date TEXT NOT NULL,
            source TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS hr_profiles (
            user_id INTEGER PRIMARY KEY REFERENCES users(id), staff_id INTEGER UNIQUE REFERENCES staff(id),
            start_date TEXT, weekdays TEXT, manager_id INTEGER REFERENCES users(id), employment_type TEXT NOT NULL DEFAULT 'unconfirmed',
            break_minutes INTEGER NOT NULL DEFAULT 60, break_agreement TEXT,
            version INTEGER NOT NULL DEFAULT 0)''',
        f'''CREATE TABLE IF NOT EXISTS hr_accounts (
            id {pk}, user_id INTEGER NOT NULL REFERENCES users(id), leave_type TEXT NOT NULL,
            cycle_start TEXT NOT NULL, cycle_end TEXT NOT NULL, as_of TEXT NOT NULL,
            annual_days INTEGER NOT NULL, confirmed_by INTEGER NOT NULL REFERENCES users(id),
            policy TEXT NOT NULL, UNIQUE(user_id,leave_type,cycle_start))''',
        f'''CREATE TABLE IF NOT EXISTS hr_ledger (
            id {pk}, account_id INTEGER NOT NULL REFERENCES hr_accounts(id),
            units INTEGER NOT NULL, kind TEXT NOT NULL, reference TEXT NOT NULL UNIQUE,
            effective_date TEXT NOT NULL, actor_id INTEGER NOT NULL REFERENCES users(id), reason TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS hr_audit (
            id {pk}, actor_id INTEGER NOT NULL REFERENCES users(id), subject TEXT NOT NULL,
            action TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS hr_requests (
            id {pk}, user_id INTEGER NOT NULL REFERENCES users(id), leave_type TEXT NOT NULL,
            start_date TEXT NOT NULL, end_date TEXT NOT NULL, units INTEGER NOT NULL,
            work_dates TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'submitted',
            account_id INTEGER REFERENCES hr_accounts(id), version INTEGER NOT NULL DEFAULT 0,
            decided_by INTEGER REFERENCES users(id), decision_note TEXT, created_at TEXT NOT NULL,
            policy TEXT NOT NULL, token TEXT NOT NULL UNIQUE)''',
        f'''CREATE TABLE IF NOT EXISTS hr_shifts (
            id {pk}, user_id INTEGER NOT NULL REFERENCES users(id), work_date TEXT NOT NULL,
            department TEXT NOT NULL, time_in TEXT NOT NULL, time_out TEXT NOT NULL,
            lunch_out TEXT NOT NULL, lunch_in TEXT NOT NULL, relief_id INTEGER REFERENCES users(id),
            created_by INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL DEFAULT 'draft', UNIQUE(user_id,work_date))''',
        f'''CREATE TABLE IF NOT EXISTS hr_weeks (
            id {pk}, user_id INTEGER NOT NULL REFERENCES users(id), week_start TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft',
            entries TEXT NOT NULL DEFAULT '[]', content_hash TEXT, UNIQUE(user_id,week_start))''',
        f'''CREATE TABLE IF NOT EXISTS hr_signatures (
            id {pk}, week_id INTEGER NOT NULL REFERENCES hr_weeks(id), version INTEGER NOT NULL,
            actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL,
            declaration TEXT NOT NULL, content_hash TEXT NOT NULL, snapshot TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(week_id,version,action))''',
        'CREATE INDEX IF NOT EXISTS hr_requests_dates ON hr_requests(start_date,end_date,status)',
        'CREATE INDEX IF NOT EXISTS hr_shifts_dates ON hr_shifts(work_date,department)',
        f'''CREATE TABLE IF NOT EXISTS hr_openings (
            id {pk}, user_id INTEGER NOT NULL REFERENCES users(id), payload TEXT NOT NULL,
            proposed_by INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL DEFAULT 'pending',
            decided_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL)''',
    ]
    for sql in statements:
        db.execute(sql)
    from .database import _ensure_column
    _ensure_column(db,backend,'hr_profiles','employment_type',"TEXT NOT NULL DEFAULT 'unconfirmed'")
    _ensure_column(db,backend,'hr_shifts','status',"TEXT NOT NULL DEFAULT 'draft'")
    db.commit()


@contextmanager
def transaction(db):
    """Serialize decisions, including balance/coverage reads, on SQLite."""
    from .database import _db_type
    db.execute('BEGIN' if _db_type() == 'postgresql' else 'BEGIN IMMEDIATE')
    try:
        if _db_type() == 'postgresql':
            db.execute('LOCK TABLE hr_requests, hr_accounts, hr_shifts, hr_weeks IN EXCLUSIVE MODE')
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def audit(db, subject, action, payload):
    db.execute('INSERT INTO hr_audit(actor_id,subject,action,payload,created_at) VALUES (?,?,?,?,?)',
               (session['user_id'], str(subject), action, json.dumps(payload, sort_keys=True), datetime.now(timezone.utc).isoformat()))


def actor(db):
    return db.execute('SELECT * FROM users WHERE id=? AND active=1', (session.get('user_id'),)).fetchone()


def is_admin(db):
    from .hr_access import can_access_payroll
    user = actor(db)
    return bool(user and not user['is_platform_user'] and (user['role'] == 'admin' or can_access_payroll() or granted(db, 'hr_manage')))


def granted(db, permission):
    return bool(db.execute('SELECT 1 FROM user_permissions WHERE user_id=? AND permission=?',
                           (session.get('user_id'), permission)).fetchone())


def can_review(db, uid, permission):
    if uid == session.get('user_id'):
        return False
    if is_admin(db):
        return True
    profile = db.execute('SELECT manager_id FROM hr_profiles WHERE user_id=?', (uid,)).fetchone()
    return bool(actor(db) and granted(db, permission) and profile and profile['manager_id'] == session.get('user_id'))


def visible_ids(db):
    if is_admin(db):
        return [r['id'] for r in db.execute('SELECT id FROM users WHERE active=1 AND COALESCE(is_platform_user,0)=0')]
    ids = [session['user_id']]
    if granted(db, 'hr_leave_approve') or granted(db, 'hr_timesheet_approve'):
        ids += [r['user_id'] for r in db.execute('SELECT user_id FROM hr_profiles WHERE manager_id=?', (session['user_id'],))]
    return ids


def ensure_profile(db, uid):
    """Only exact unique email links are automatic. External users.staff_id is untouched."""
    user = db.execute('SELECT * FROM users WHERE id=? AND active=1 AND COALESCE(is_platform_user,0)=0', (uid,)).fetchone()
    if not user:
        raise ValueError('An active employee account is required.')
    if service_account(user):
        raise ValueError('A system account cannot be used as an employee. Use a named employee login.')
    profile = db.execute('SELECT * FROM hr_profiles WHERE user_id=?', (uid,)).fetchone()
    if profile:
        return profile
    matches = db.execute('SELECT id,start_date FROM staff WHERE lower(email)=lower(?) AND email IS NOT NULL AND email<>?',
                         (user['email'], '')).fetchall() if user['email'] else []
    sid = None
    if len(matches) == 1 and not db.execute('SELECT 1 FROM hr_profiles WHERE staff_id=?', (matches[0]['id'],)).fetchone():
        sid = matches[0]['id']
    elif not matches:
        names = (user['full_name'] or user['username']).split(' ', 1)
        cursor = db.execute('INSERT INTO staff(first_name,last_name,department,role,email,contact) VALUES (?,?,?,?,?,?)',
                            (names[0], names[1] if len(names)>1 else '', user['department'], user['role'], user['email'], user['contact']))
        sid = cursor.lastrowid
    db.execute('INSERT OR IGNORE INTO hr_profiles(user_id,staff_id) VALUES (?,?)', (uid, sid))
    return db.execute('SELECT * FROM hr_profiles WHERE user_id=?', (uid,)).fetchone()


def iso(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError('Enter a valid date.') from None


def days_between(start, end):
    if end < start or (end-start).days > 1096:
        raise ValueError('End date must follow start date; maximum request is three years.')
    return [start + timedelta(days=i) for i in range((end-start).days+1)]


def anniversary(start, year):
    return start.replace(year=year, day=min(start.day, monthrange(year,start.month)[1]))


def add_months(start, months):
    value=start.year*12+start.month-1+months
    year,month=divmod(value,12)
    return date(year,month+1,min(start.day,monthrange(year,month+1)[1]))


def cycle(start, kind, as_of):
    years = 3 if kind == 'sick' else 1
    elapsed = as_of.year-start.year
    if anniversary(start,as_of.year)>as_of:
        elapsed-=1
    first = anniversary(start,start.year+(max(elapsed,0)//years)*years)
    return first, anniversary(first,first.year+years)-timedelta(days=1)


def holidays(year):
    # South African Public Holidays Act fixed dates and Gregorian Easter.
    a=year%19; b=year//100; c=year%100; d=b//4; e=b%4; f=(b+8)//25
    g=(b-f+1)//3; h=(19*a+b-d-g+15)%30; i=c//4; k=c%4
    l=(32+2*e+2*i-h-k)%7; m=(a+11*h+22*l)//451
    month=(h+l-7*m+114)//31; day=(h+l-7*m+114)%31+1
    easter=date(year,month,day)
    result={date(year,mo,da) for mo,da in [(1,1),(3,21),(4,27),(5,1),(6,16),(8,9),(9,24),(12,16),(12,25),(12,26)]}
    result|={easter-timedelta(days=2),easter+timedelta(days=1)}
    # Presidency declaration, 15 September 2026: local election day.
    # https://www.gov.za/news/media-statements/president-cyril-ramaphosa-declares-election-day
    if year==2026:
        result.add(date(2026,11,4))
    result|={day+timedelta(days=1) for day in list(result) if day.weekday()==6}
    # Additional gazetted holidays must be supplied through config.
    from flask import current_app
    result|={iso(s) for s in current_app.config.get('HR_EXTRA_HOLIDAYS', [])}
    return result


def work_dates(db, uid, start, end, kind='annual'):
    profile = db.execute('SELECT * FROM hr_profiles WHERE user_id=?', (uid,)).fetchone()
    if not profile or not profile['start_date'] or not profile['weekdays']:
        raise ValueError('HR must confirm employment start date and working days first.')
    if start < iso(profile['start_date']):
        raise ValueError('Leave cannot precede employment.')
    weekdays = json.loads(profile['weekdays'])
    result=[]
    for day in days_between(start,end):
        scheduled = day.weekday() in weekdays
        # A published actual roster supersedes the normal pattern for that day.
        if db.execute("SELECT 1 FROM hr_shifts WHERE user_id=? AND work_date=? AND status='published'", (uid,day.isoformat())).fetchone():
            scheduled=True
        if scheduled and not (kind=='annual' and day in holidays(day.year)):
            result.append(day.isoformat())
    if not result:
        raise ValueError('No chargeable working days in this period.')
    return result


def units(value):
    try:
        val=Decimal(str(value))
        if not val.is_finite() or val<0 or val>1000 or val*100 != (val*100).to_integral_value():
            raise ValueError()
        return int(val*100)
    except (InvalidOperation, ValueError):
        raise ValueError('Enter days from 0 to 1000 with at most two decimal places.') from None


def account_balance(db, account, as_of=None):
    as_of = as_of or today()
    rows=db.execute('SELECT units,kind,effective_date FROM hr_ledger WHERE account_id=? AND effective_date<=?', (account['id'],as_of.isoformat())).fetchall()
    credits=sum(r['units'] for r in rows if r['kind']!='taken')
    taken=-sum(r['units'] for r in rows if r['kind']=='taken')
    earned=0
    if account['leave_type']=='annual':
        first=iso(account['as_of']); last=min(as_of,iso(account['cycle_end']))
        # Monthly accounting aligned to employment-cycle months. A credit is
        # earned at the end of each complete employment month, exactly once.
        cycle_start=iso(account['cycle_start'])
        completed=sum(first<add_months(cycle_start,n)-timedelta(days=1)<=last for n in range(1,13))
        earned=completed*account['annual_days']*100//12
    if account['leave_type']=='sick':
        profile=db.execute('SELECT start_date FROM hr_profiles WHERE user_id=?',(account['user_id'],)).fetchone()
        start=iso(profile['start_date'])
        if iso(account['as_of'])<add_months(start,6)<=as_of:
            # The first-six-month bank becomes the full first-cycle entitlement,
            # retaining all sick leave already taken. Never reset it annually.
            first_six_credits=sum(r['units'] for r in rows if r['kind']!='taken' and r['effective_date']<add_months(start,6).isoformat())
            earned=max(0,account['annual_days']*100-first_six_credits)
    approved=db.execute("SELECT work_dates FROM hr_requests WHERE account_id=? AND status='approved'", (account['id'],)).fetchall()
    dates=[d for row in approved for d in json.loads(row['work_dates'])]
    since=account['as_of']
    used=sum(100 for d in dates if since<=d<=as_of.isoformat())
    reserved=sum(100 for d in dates if d>as_of.isoformat())
    return dict(credits=credits,earned=earned,taken=taken+used,reserved=reserved,
                available=credits+earned-taken-used-reserved,current=credits+earned-taken-used)


def current_accounts(db, uid):
    # Old cycles remain visible; no silent forfeiture or reset of unused credits.
    return db.execute('SELECT * FROM hr_accounts WHERE user_id=? AND cycle_start<=? ORDER BY cycle_start DESC,leave_type',
                      (uid,today().isoformat())).fetchall()


def annual_entitlement(db, profile):
    policy=db.execute("SELECT value FROM hr_policy_settings WHERE name='annual_working_days'").fetchone()
    if policy:
        value=int(policy['value'])
        if not 1<=value<=365:
            raise ValueError('HR annual leave policy needs correction.')
        return value
    return len(json.loads(profile['weekdays']))*3 if profile['weekdays'] else None


def submit_leave(db, uid, form):
    kind=form.get('leave_type','annual')
    if kind not in LEAVE_TYPES:
        raise ValueError('Select a supported leave type.')
    p=db.execute('SELECT employment_type FROM hr_profiles WHERE user_id=?',(uid,)).fetchone()
    if not p or p['employment_type']=='unconfirmed' or (p['employment_type']!='employee' and kind!='unpaid'):
        raise ValueError('HR must confirm your employment arrangement before statutory leave can be requested. Contractors use the separately agreed unpaid time-off workflow.')
    if form.get('attest')!='yes':
        raise ValueError('Confirm your leave request before submitting.')
    start,end=iso(form.get('start_date')),iso(form.get('end_date'))
    dates=work_dates(db,uid,start,end,kind)
    if db.execute("SELECT 1 FROM hr_requests WHERE user_id=? AND status IN ('submitted','approved') AND start_date<=? AND end_date>=?",
                  (uid,end.isoformat(),start.isoformat())).fetchone():
        raise ValueError('You already have a pending or approved request overlapping these dates.')
    token=form.get('token','')
    if len(token)<16 or len(token)>100:
        raise ValueError('Reload the request form and try again.')
    if db.execute('SELECT 1 FROM hr_requests WHERE token=?',(token,)).fetchone():
        raise ValueError('This form was already submitted. Open your request list or reload a fresh form.')
    cursor=db.execute('''INSERT INTO hr_requests(user_id,leave_type,start_date,end_date,units,work_dates,reason,created_at,policy,token)
                         VALUES (?,?,?,?,?,?,?,?,?,?)''',
                      (uid,kind,start.isoformat(),end.isoformat(),len(dates)*100,json.dumps(dates),form.get('reason','').strip()[:4000],datetime.now(timezone.utc).isoformat(),POLICY,token))
    audit(db,cursor.lastrowid,'leave_submitted',{'user_id':uid,'days':dates,'policy':POLICY})
    p=db.execute('SELECT manager_id FROM hr_profiles WHERE user_id=?',(uid,)).fetchone()
    notify(db,p['manager_id'] if p else None,'hr_leave',cursor.lastrowid,'A leave request needs review.')


def leave_conflicts(db, row):
    user=db.execute('SELECT department FROM users WHERE id=?',(row['user_id'],)).fetchone()
    matches=db.execute('''SELECT r.id,u.full_name,u.department,r.start_date,r.end_date FROM hr_requests r JOIN users u ON u.id=r.user_id
        WHERE r.id<>? AND r.user_id<>?
        AND r.status='approved' AND r.start_date<=? AND r.end_date>=?''',
        (row['id'],row['user_id'],row['end_date'],row['start_date'])).fetchall()
    return [r for r in matches if department_key(r['department'])==department_key(user['department'])]


def uncovered_leave(db,row):
    """A minimum of one colleague must cover the absent employee's full shift."""
    missing=[]
    for day in json.loads(row['work_dates']):
        shift=db.execute("SELECT * FROM hr_shifts WHERE user_id=? AND work_date=? AND status='published'",(row['user_id'],day)).fetchone()
        if not shift:
            missing.append(day+' (no published shift)')
            continue
        covers=db.execute("SELECT * FROM hr_shifts WHERE user_id<>? AND work_date=? AND status='published' AND time_in<=? AND time_out>=?",(row['user_id'],day,shift['time_in'],shift['time_out'])).fetchall()
        covers=[s for s in covers if department_key(s['department'])==department_key(shift['department'])]
        if not any(not db.execute("SELECT 1 FROM hr_requests WHERE user_id=? AND status='approved' AND start_date<=? AND end_date>=?",(s['user_id'],day,day)).fetchone() for s in covers):
            missing.append(day)
        if db.execute("SELECT 1 FROM hr_shifts WHERE relief_id=? AND work_date=? AND status='published'",(row['user_id'],day)).fetchone():
            missing.append(day+' (assigned as relief)')
    return missing


def decide_leave(db, lid, action, form):
    row=db.execute('SELECT * FROM hr_requests WHERE id=?',(lid,)).fetchone()
    if not row:
        raise ValueError('Request not found.')
    if not can_review(db,row['user_id'],'hr_leave_approve'):
        raise PermissionError('You cannot decide this request or approve your own leave.')
    if row['status']!='submitted' or str(row['version'])!=form.get('version'):
        raise ValueError('This request has changed. Refresh before deciding.')
    if action not in ('approve','reject'):
        raise ValueError('Invalid decision.')
    note=form.get('decision_note','').strip()[:2000]
    if not note:
        raise ValueError('Record the review findings and any cover arrangement.')
    account=None
    if action=='approve':
        recalculated=work_dates(db,row['user_id'],iso(row['start_date']),iso(row['end_date']),row['leave_type'])
        if recalculated!=json.loads(row['work_dates']):
            raise ValueError('Working days changed. Withdraw and resubmit this request with the correct days.')
        if row['leave_type']=='annual' and (leave_conflicts(db,row) or uncovered_leave(db,row)):
            if not is_admin(db) or not note:
                raise ValueError('Department leave conflict. HR must record a cover arrangement to override.')
        if row['leave_type'] in ('annual','sick','family'):
            account=db.execute('''SELECT * FROM hr_accounts WHERE user_id=? AND leave_type=? AND cycle_start<=?
                AND cycle_end>=? AND as_of<=?''',(row['user_id'],row['leave_type'],row['start_date'],row['end_date'],row['start_date'])).fetchone()
            if not account:
                raise ValueError('Confirm a leave account covering the entire request before approval. Split requests at cycle boundaries.')
            if account_balance(db,account)['available']<row['units']:
                raise ValueError('Insufficient confirmed balance. HR must review eligibility and entitlement; do not silently convert to unpaid leave.')
        elif row['leave_type'] in ('parental','study') and not note:
            raise ValueError('Record the reviewed entitlement and payment arrangement before approving this leave.')
    db.execute('UPDATE hr_requests SET status=?,account_id=?,decided_by=?,decision_note=?,version=version+1 WHERE id=?',
               ('approved' if action=='approve' else 'rejected',account['id'] if account else None,session['user_id'],note,lid))
    audit(db,lid,'leave_'+action,{'note':note,'version':row['version']})
    db.execute("UPDATE staff_notifications SET resolved_at=? WHERE source_kind='hr_leave' AND source_id=? AND resolved_at IS NULL",(datetime.now(timezone.utc).isoformat(),lid))
    notify(db,row['user_id'],'hr_leave',lid,'Your leave request was '+('approved.' if action=='approve' else 'declined.'),row['version']+1)


def minute(value):
    try:
        parsed=datetime.strptime(value,'%H:%M')
        if parsed.strftime('%H:%M')!=value:
            raise ValueError()
        return parsed.hour*60+parsed.minute
    except (ValueError,TypeError):
        raise ValueError('Times must use HH:MM in 24-hour format.') from None


def interval(ti,lo,li,to):
    start,end=minute(ti),minute(to)
    if end<=start:
        raise ValueError('Clock out must follow clock in; overnight shifts are not supported.')
    if bool(lo)!=bool(li):
        raise ValueError('Enter both lunch start and lunch end.')
    lunch=0
    if lo:
        a,b=minute(lo),minute(li)
        if not start<=a<b<=end:
            raise ValueError('Lunch must be ordered and fall inside the work interval.')
        lunch=b-a
    return end-start-lunch


def week_start(day):
    return day-timedelta(days=day.weekday())


def digest(entries):
    return hashlib.sha256(json.dumps(entries,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def week_warnings(entries):
    warnings=[]
    if sum(e['minutes'] for e in entries)>45*60:
        warnings.append('More than 45 hours: review ordinary hours and overtime before approval.')
    previous=None
    for e in sorted(entries,key=lambda item:item['date']):
        if not e['time_in']:
            continue
        start,end=minute(e['time_in']),minute(e['time_out'])
        if start<360 or end>1080:
            warnings.append(e['date']+': work before 06:00 or after 18:00; check applicable night-work arrangements and transport.')
        if e['minutes']>540:
            warnings.append(e['date']+': more than nine hours; review applicable daily limits and overtime.')
        if not e['lunch_out'] and end-start>5*60:
            warnings.append(e['date']+': no meal break recorded; review missed/interrupted lunch.')
        if e['lunch_out'] and minute(e['lunch_out'])-start>5*60:
            warnings.append(e['date']+': lunch starts after five continuous hours.')
        if e['lunch_in'] and end-minute(e['lunch_in'])>5*60:
            warnings.append(e['date']+': more than five continuous hours after lunch.')
        actual=datetime.combine(iso(e['date']),datetime.min.time())+timedelta(minutes=start)
        if previous and (actual-previous).total_seconds()<12*3600:
            warnings.append(e['date']+': less than 12 hours between shifts.')
        previous=datetime.combine(iso(e['date']),datetime.min.time())+timedelta(minutes=end)
    return warnings


def notify(db,uid,kind,source_id,message,version=0):
    """In-app notification in the same transaction; no email/SMS is sent."""
    if not uid:
        return
    db.execute('''INSERT OR IGNORE INTO staff_notifications
        (recipient_id,source_kind,source_id,event_key,title,created_at)
        VALUES (?,?,?,?,?,?)''',
        (uid,kind,source_id,f'{kind}:{source_id}:{version}:{uid}', message,
         datetime.now(timezone.utc).isoformat()))


def save_week(db, uid, form):
    start=week_start(iso(form.get('week_start')))
    row=db.execute('SELECT * FROM hr_weeks WHERE user_id=? AND week_start=?',(uid,start.isoformat())).fetchone()
    if row and (row['status'] not in ('draft','returned') or str(row['version'])!=form.get('version')):
        raise ValueError('This week is locked or changed. Refresh before editing.')
    if not row and form.get('version') not in ('0',None):
        raise ValueError('Reload this week before saving.')
    entries=[]
    for offset in range(7):
        day=(start+timedelta(days=offset)).isoformat()
        e={'date':day}
        for key in ('time_in','lunch_out','lunch_in','time_out','notes'):
            e[key]=form.get(key+'_'+day,'').strip()[:2000]
        e['minutes']=interval(e['time_in'],e['lunch_out'],e['lunch_in'],e['time_out']) if any(e[k] for k in ('time_in','time_out','lunch_out','lunch_in')) else 0
        entries.append(e)
    submit=form.get('action')=='submit'
    if submit and form.get('attest')!='yes':
        raise ValueError('Confirm the declaration to sign and submit.')
    if submit and start+timedelta(days=6)>today():
        raise ValueError('Submit the weekly timesheet after the week has ended.')
    content=json.dumps(entries,sort_keys=True)
    content_hash=digest(entries)
    status='submitted' if submit else 'draft'
    if row:
        db.execute('UPDATE hr_weeks SET entries=?,content_hash=?,status=?,version=version+1 WHERE id=?',(content,content_hash,status,row['id']))
        wid,version=row['id'],row['version']+1
    else:
        wid=db.execute('INSERT INTO hr_weeks(user_id,week_start,entries,content_hash,status,version) VALUES (?,?,?,?,?,1)',(uid,start.isoformat(),content,content_hash,status)).lastrowid
        version=1
    if submit:
        sign_week(db,wid,version,'submitted',DECLARATION,content_hash,content)
        p=db.execute('SELECT manager_id FROM hr_profiles WHERE user_id=?',(uid,)).fetchone()
        notify(db,p['manager_id'] if p else None,'hr_week',wid,'A weekly timesheet needs review.',version)
    audit(db,wid,'week_'+status,{'version':version,'hash':content_hash})
    return start


def sign_week(db,wid,version,action,declaration,content_hash,snapshot):
    db.execute('''INSERT INTO hr_signatures(week_id,version,actor_id,action,declaration,content_hash,snapshot,created_at)
        VALUES (?,?,?,?,?,?,?,?)''',(wid,version,session['user_id'],action,declaration,content_hash,snapshot,datetime.now(timezone.utc).isoformat()))


def decide_week(db,wid,form):
    row=db.execute('SELECT * FROM hr_weeks WHERE id=?',(wid,)).fetchone()
    if not row:
        raise ValueError('Timesheet not found.')
    if not can_review(db,row['user_id'],'hr_timesheet_approve'):
        raise PermissionError('You cannot approve this timesheet or your own timesheet.')
    if str(row['version'])!=form.get('version'):
        raise ValueError('The timesheet has changed. Refresh before deciding.')
    action=form.get('action')
    if action not in ('approved','returned','reopened'):
        raise ValueError('Select a valid decision.')
    if (action=='reopened' and (row['status']!='approved' or not is_admin(db))) or (action!='reopened' and row['status']!='submitted'):
        raise ValueError('This decision is not available for the current state.')
    note=form.get('note','').strip()[:2000]
    if not note:
        raise ValueError('Record your review or correction reason.')
    sign_week(db,wid,row['version'],action,note,row['content_hash'],row['entries'])
    db.execute('UPDATE hr_weeks SET status=? WHERE id=?',('returned' if action=='reopened' else action,wid))
    audit(db,wid,'week_'+action,{'version':row['version'],'note':note})
    db.execute("UPDATE staff_notifications SET resolved_at=? WHERE source_kind='hr_week' AND source_id=? AND resolved_at IS NULL",(datetime.now(timezone.utc).isoformat(),wid))
    notify(db,row['user_id'],'hr_week',wid,'Your weekly timesheet was '+action+'.',row['version']+1)
