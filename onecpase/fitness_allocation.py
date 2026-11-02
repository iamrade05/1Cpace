"""Allocation offer history and explicit management decisions about PT rights.

An offer decline is evidence for review, not an automatic disciplinary decision.
All identifiers and decisions belong to the current tenant database.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


OFFER_HOURS = 8
SKIP_REVIEW_THRESHOLD = 4


def ensure_schema(db):
    from .database import _db_type
    identity = 'SERIAL PRIMARY KEY' if _db_type() == 'postgresql' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    db.execute(f'''CREATE TABLE IF NOT EXISTS fitness_allocation_offers (
        id {identity}, member_id INTEGER NOT NULL REFERENCES members(id),
        instructor_id INTEGER NOT NULL REFERENCES users(id),
        offered_at TEXT NOT NULL, expires_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', reason TEXT,
        counts_as_skip INTEGER, reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT,
        review_reason TEXT)''')
    db.execute(f'''CREATE TABLE IF NOT EXISTS fitness_pt_reviews (
        id {identity}, instructor_id INTEGER NOT NULL REFERENCES users(id),
        triggered_offer_id INTEGER NOT NULL UNIQUE REFERENCES fitness_allocation_offers(id),
        status TEXT NOT NULL DEFAULT 'pending', decision TEXT,
        decided_by INTEGER REFERENCES users(id), decided_at TEXT, reason TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS fitness_pt_rights (
        instructor_id INTEGER PRIMARY KEY REFERENCES users(id), restricted INTEGER NOT NULL DEFAULT 0,
        decided_by INTEGER REFERENCES users(id), decided_at TEXT, reason TEXT)''')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS fitness_one_pending_offer ON fitness_allocation_offers(member_id) WHERE status=\'pending\'')
    from .database import _ensure_column
    _ensure_column(db,_db_type(),'fitness_allocation_offers','request_id','INTEGER')
    db.execute('''CREATE TABLE IF NOT EXISTS fitness_allocation_roster (
        user_id INTEGER PRIMARY KEY REFERENCES users(id), staff_id INTEGER REFERENCES staff(id),
        kind TEXT NOT NULL, rotation TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
        updated_by INTEGER REFERENCES users(id), updated_at TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS fitness_roster_windows (
        user_id INTEGER NOT NULL REFERENCES users(id), weekday INTEGER NOT NULL,
        start_time TEXT NOT NULL, end_time TEXT NOT NULL,
        PRIMARY KEY(user_id,weekday))''')
    db.execute('''CREATE TABLE IF NOT EXISTS fitness_gym_hours (
        weekday INTEGER PRIMARY KEY, start_time TEXT NOT NULL, end_time TEXT NOT NULL)''')
    db.execute(f'''CREATE TABLE IF NOT EXISTS fitness_allocation_requests (
        id {identity}, member_id INTEGER NOT NULL REFERENCES members(id),
        starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
        instructor_id INTEGER REFERENCES users(id), next_action TEXT,
        created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL)''')
    for column in ('confirmed_at','confirmation_note','outcome_note'):
        _ensure_column(db,_db_type(),'fitness_allocation_requests',column,'TEXT')
    _ensure_column(db,_db_type(),'fitness_allocation_requests','visit_id','INTEGER REFERENCES onboarding_visits(id)')
    _ensure_column(db,_db_type(),'fitness_allocation_requests','updated_by','INTEGER REFERENCES users(id)')
    _ensure_column(db,_db_type(),'fitness_allocation_requests','updated_at','TEXT')
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS fitness_one_open_request ON fitness_allocation_requests(member_id) WHERE status IN ('waiting','offered','reserved')")
    db.execute('''CREATE TABLE IF NOT EXISTS fitness_allocation_cursor (
        id INTEGER PRIMARY KEY, last_user_id INTEGER NOT NULL DEFAULT 0)''')
    db.execute('INSERT OR IGNORE INTO fitness_allocation_cursor(id,last_user_id) VALUES(1,0)')


def utc_now():
    return datetime.now(timezone.utc)


def require_manager(db, manager_id):
    if not db.execute("SELECT 1 FROM users WHERE id=? AND active=1 AND role IN ('admin','manager')", (manager_id,)).fetchone():
        raise ValueError('An active manager must make this decision.')


def check_reason(reason):
    if len(reason)>4000:raise ValueError('Keep the reason within 4000 characters.')


def create_offer(db, member_id, instructor_id, now=None):
    """Caller supplies an instructor already checked against the eligible roster."""
    now = now or utc_now()
    if not db.execute('SELECT 1 FROM users WHERE id=? AND active=1', (instructor_id,)).fetchone():
        raise ValueError('Instructor must be active.')
    if not db.execute('SELECT 1 FROM members WHERE id=?', (member_id,)).fetchone():
        raise ValueError('Member does not exist.')
    intake=db.execute('SELECT goal_category FROM fitness_intake WHERE member_id=?',(member_id,)).fetchone()
    if not intake or intake['goal_category'] in ('general_fitness','mixed','unclear',None):
        raise ValueError('Ask the member to clarify their primary goal before allocation.')
    return db.execute('''INSERT INTO fitness_allocation_offers
        (member_id,instructor_id,offered_at,expires_at) VALUES(?,?,?,?)''',
        (member_id, instructor_id, now.isoformat(), (now+timedelta(hours=OFFER_HOURS)).isoformat())).lastrowid


def expire_offers(db, now=None):
    # ISO timestamps are always stored in UTC by create_offer.
    return db.execute('''UPDATE fitness_allocation_offers SET status='timed_out',
        reason='No response within 8 hours' WHERE status='pending' AND expires_at<=?''',
        ((now or utc_now()).isoformat(),)).rowcount


def respond(db, offer_id, instructor_id, accept, reason='', now=None):
    now = now or utc_now()
    check_reason(reason)
    expire_offers(db, now)
    if not accept and not reason.strip():
        raise ValueError('A reason is required when declining.')
    offer=db.execute('SELECT * FROM fitness_allocation_offers WHERE id=? AND instructor_id=?',(offer_id,instructor_id)).fetchone()
    if offer and offer['request_id'] and accept:
        allocation=db.execute("SELECT * FROM fitness_allocation_requests WHERE id=? AND status='offered'",(offer['request_id'],)).fetchone()
        if not allocation or not is_available(db,instructor_id,allocation,now,ignore_request=allocation['id']):
            raise ValueError('The appointment is no longer available; it needs reallocation.')
        goal=db.execute('SELECT goal_category,interest FROM fitness_intake WHERE member_id=?',(offer['member_id'],)).fetchone()
        if goal['interest']!='interested' or goal['goal_category'] not in ('weight_loss','toning'):
            raise ValueError('Member preferences changed; this offer requires reallocation.')
    changed = db.execute('''UPDATE fitness_allocation_offers SET status=?,reason=?
        WHERE id=? AND instructor_id=? AND status='pending' AND expires_at>?''',
        ('accepted' if accept else 'declined', reason.strip(), offer_id, instructor_id, now.isoformat())).rowcount
    if not changed:
        raise ValueError('Offer is unavailable, expired or belongs to another instructor.')
    if accept:
        db.execute("UPDATE fitness_intake SET assigned_instructor_id=?,primary_instructor_id=COALESCE(primary_instructor_id,?),allocation_state='allocated' WHERE member_id=?",
                   (instructor_id,instructor_id, offer['member_id']))
    if offer and offer['request_id']:
        db.execute('UPDATE fitness_allocation_requests SET status=?,instructor_id=?,next_action=? WHERE id=?',
                   ('reserved' if accept else 'waiting', instructor_id if accept else None,
                    'Confirm appointment with member' if accept else 'Find next available instructor',offer['request_id']))


def begin_allocation(db):
    """Serialize offers, reservations, roster edits and management classifications."""
    from .database import _db_type
    if _db_type()=='postgresql':
        db.execute('BEGIN')
        db.execute('SELECT id FROM fitness_allocation_cursor WHERE id=1 FOR UPDATE').fetchone()
    else:
        db.execute('BEGIN IMMEDIATE')


def shift_window(rotation, on_date, start, end):
    """Anchor-based weekly swapping survives process restarts and year boundaries."""
    odd=((on_date-date(2026,10,26)).days//7)%2
    if rotation in ('early','late'):
        early=(rotation=='early') != bool(odd)
        return ('05:00','13:00') if early else ('13:00','21:00')
    if rotation=='support':return ('16:00','20:00')
    return start,end


def request_allocation(db, member_id, starts_at, ends_at, actor_id, now=None):
    now=now or utc_now()
    start,end=datetime.fromisoformat(starts_at),datetime.fromisoformat(ends_at)
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError('Appointment times require a timezone.')
    local_start=start.astimezone(ZoneInfo('Africa/Johannesburg'))
    local_end=end.astimezone(ZoneInfo('Africa/Johannesburg'))
    if start<=now or end<=start or local_start.date()!=local_end.date():
        raise ValueError('Choose a future appointment that starts and ends on the same day.')
    intake=db.execute('SELECT * FROM fitness_intake WHERE member_id=?',(member_id,)).fetchone()
    if not intake or intake['goal_category'] not in ('weight_loss','toning','muscle_gain','weight_gain'):
        raise ValueError('Clarify the primary goal before requesting allocation.')
    if intake['interest']!='interested':raise ValueError('Confirm that the member wants introductory support.')
    if not intake['program_start_on']:raise ValueError('Confirm the joining date first.')
    first=date.fromisoformat(intake['program_start_on'])
    if not first<=local_start.date()<=first+timedelta(days=9):
        raise ValueError('Appointment must fall inside the ten-day programme.')
    if db.execute("SELECT 1 FROM fitness_allocation_requests WHERE member_id=? AND status IN ('waiting','offered','reserved','attendance_due')",(member_id,)).fetchone():
        raise ValueError('This member already has an open allocation request.')
    if db.execute("SELECT 1 FROM fitness_allocation_offers WHERE member_id=? AND status='pending'",(member_id,)).fetchone():
        raise ValueError('This member already has a pending instructor offer.')
    rid=db.execute('''INSERT INTO fitness_allocation_requests(member_id,starts_at,ends_at,created_by,created_at,next_action)
        VALUES(?,?,?,?,?,'Find available instructor')''',
        (member_id,start.astimezone(timezone.utc).isoformat(),end.astimezone(timezone.utc).isoformat(),actor_id,now.isoformat())).lastrowid
    db.execute("UPDATE fitness_intake SET allocation_state='awaiting_allocation' WHERE member_id=?",(member_id,))
    return rid


def is_available(db, instructor_id, allocation, now=None, ignore_request=None):
    now=now or utc_now()
    start=datetime.fromisoformat(allocation['starts_at']).astimezone(ZoneInfo('Africa/Johannesburg'))
    end=datetime.fromisoformat(allocation['ends_at']).astimezone(ZoneInfo('Africa/Johannesburg'))
    if start<=now:return False
    roster=db.execute('''SELECT r.* FROM fitness_allocation_roster r JOIN users u ON u.id=r.user_id
        WHERE r.user_id=? AND r.enabled=1 AND u.active=1''',(instructor_id,)).fetchone()
    window=db.execute('SELECT * FROM fitness_roster_windows WHERE user_id=? AND weekday=?',(instructor_id,start.weekday())).fetchone()
    hours=db.execute('SELECT * FROM fitness_gym_hours WHERE weekday=?',(start.weekday(),)).fetchone()
    if not roster or not window or not hours:return False
    shift_start,shift_end=shift_window(roster['rotation'],start.date(),window['start_time'],window['end_time'])
    if not max(shift_start,hours['start_time'])<=start.strftime('%H:%M')<end.strftime('%H:%M')<=min(shift_end,hours['end_time']):return False
    day=start.date().isoformat()
    if db.execute('''SELECT 1 FROM leave_requests WHERE status='approved' AND start_date<=? AND end_date>=?
        AND (user_id=? OR (staff_id IS NOT NULL AND staff_id=?))''',(day,day,instructor_id,roster['staff_id'])).fetchone():return False
    # Legacy class records have no duration. A class on that day requires a
    # schedule review instead of guessing an end time and double-booking.
    for cls in db.execute('SELECT schedule_day FROM fitness_classes WHERE instructor_id=? AND active=1',(instructor_id,)).fetchall():
        days=(cls['schedule_day'] or '').lower()
        known=any(day in days for day in ('monday','tuesday','wednesday','thursday','friday','saturday','sunday'))
        if not known or start.strftime('%A').lower() in days:return False
    for daily in db.execute('SELECT time_in,time_out FROM instructor_dailies WHERE instructor_id=? AND date=?',(instructor_id,day)).fetchall():
        if not daily['time_in'] or not daily['time_out']:return False
        if daily['time_in']<end.strftime('%H:%M') and daily['time_out']>start.strftime('%H:%M'):return False
    if db.execute('''SELECT 1 FROM fitness_allocation_requests WHERE instructor_id=? AND status IN ('offered','reserved')
        AND starts_at<? AND ends_at>? AND id!=?''',(instructor_id,allocation['ends_at'],allocation['starts_at'],ignore_request or -1)).fetchone():return False
    return True


def process_queue(db, now=None):
    """Run inside begin_allocation/commit. Makes local reservations; sends nothing."""
    now=now or utc_now()
    expire_offers(db,now)
    for pending in db.execute("SELECT * FROM fitness_allocation_offers WHERE status='pending' AND request_id IS NOT NULL").fetchall():
        request=db.execute('SELECT * FROM fitness_allocation_requests WHERE id=?',(pending['request_id'],)).fetchone()
        if not request or not is_available(db,pending['instructor_id'],request,now,ignore_request=pending['request_id']):
            db.execute("UPDATE fitness_allocation_offers SET status='withdrawn',reason='Roster or availability changed' WHERE id=? AND status='pending'",(pending['id'],))
    db.execute("""UPDATE fitness_allocation_requests SET status='waiting',instructor_id=NULL,next_action='Previous offer expired; find next instructor'
        WHERE status='offered' AND NOT EXISTS(SELECT 1 FROM fitness_allocation_offers o
        WHERE o.request_id=fitness_allocation_requests.id AND o.status='pending')""")
    db.execute("""UPDATE fitness_allocation_requests SET status='attendance_due',next_action='Record actual attendance and assistance'
        WHERE status='reserved' AND starts_at<=?""",(now.isoformat(),))
    db.execute("""UPDATE fitness_allocation_requests SET status='expired',next_action='Arrange another appointment; allocation was not confirmed'
        WHERE status IN ('waiting','offered') AND starts_at<=?""",(now.isoformat(),))
    db.execute("""UPDATE fitness_allocation_offers SET status='withdrawn',reason='Appointment time passed'
        WHERE status='pending' AND request_id IN (SELECT id FROM fitness_allocation_requests WHERE status='expired')""")
    for allocation in db.execute("SELECT * FROM fitness_allocation_requests WHERE status='waiting' ORDER BY id").fetchall():
        intake=db.execute('SELECT * FROM fitness_intake WHERE member_id=?',(allocation['member_id'],)).fetchone()
        if intake['goal_category'] not in ('weight_loss','toning','muscle_gain','weight_gain'):
            db.execute("UPDATE fitness_allocation_requests SET next_action='Ask member to clarify primary goal' WHERE id=?",(allocation['id'],))
            continue
        last=db.execute('SELECT last_user_id FROM fitness_allocation_cursor WHERE id=1').fetchone()[0]
        candidates=db.execute('''SELECT * FROM fitness_allocation_roster WHERE enabled=1
            ORDER BY CASE WHEN user_id>? THEN 0 ELSE 1 END,user_id''',(last,)).fetchall()
        chosen=False
        for roster in candidates:
            uid=roster['user_id']
            if roster['kind']=='group' and intake['goal_category'] not in ('weight_loss','toning'):continue
            if db.execute('SELECT 1 FROM fitness_allocation_offers WHERE request_id=? AND instructor_id=?',(allocation['id'],uid)).fetchone():continue
            if not is_available(db,uid,allocation,now):continue
            if roster['kind']=='group' and datetime.fromisoformat(allocation['starts_at'])<=now+timedelta(hours=OFFER_HOURS):continue
            if roster['kind']=='group':
                offer=create_offer(db,allocation['member_id'],uid,now)
                db.execute('UPDATE fitness_allocation_offers SET request_id=? WHERE id=?',(allocation['id'],offer))
                status,action='offered','Instructor to accept or decline within 8 hours'
            else:
                status,action='reserved','Confirm appointment with member'
                db.execute("UPDATE fitness_intake SET assigned_instructor_id=?,primary_instructor_id=COALESCE(primary_instructor_id,?),allocation_state='allocated' WHERE member_id=?",(uid,uid,allocation['member_id']))
            db.execute('UPDATE fitness_allocation_requests SET status=?,instructor_id=?,next_action=? WHERE id=?',(status,uid,action,allocation['id']))
            db.execute('UPDATE fitness_allocation_cursor SET last_user_id=? WHERE id=1',(uid,))
            chosen=True
            break
        if not chosen:
            db.execute("UPDATE fitness_allocation_requests SET next_action='Awaiting allocation: no eligible instructor available' WHERE id=?",(allocation['id'],))
    sync_notifications(db,now)


def record_appointment(db, request_id, action, actor_id, reason, visit_id=None, now=None):
    now=now or utc_now()
    check_reason(reason)
    row=db.execute('SELECT * FROM fitness_allocation_requests WHERE id=?',(request_id,)).fetchone()
    actor=db.execute('SELECT role,active FROM users WHERE id=?',(actor_id,)).fetchone()
    if not row or not actor or not actor['active'] or (actor['role'] not in ('admin','manager') and row['instructor_id']!=actor_id):
        raise ValueError('Only the allocated instructor or management can update this appointment.')
    if not reason.strip():raise ValueError('Record the confirmation method or outcome reason.')
    if action=='confirm':
        if row['status']!='reserved' or row['confirmed_at'] or datetime.fromisoformat(row['starts_at'])<=now:
            raise ValueError('Only an upcoming, unconfirmed reservation can be confirmed.')
        db.execute("UPDATE fitness_allocation_requests SET confirmed_at=?,confirmation_note=?,updated_by=?,updated_at=?,next_action='Attend appointment' WHERE id=?",
                   (now.isoformat(),reason.strip(),actor_id,now.isoformat(),request_id))
        return
    if action not in ('attended','missed','cancelled') or row['status'] not in ('waiting','offered','reserved','attendance_due'):
        raise ValueError('Choose a valid outcome for an open appointment.')
    if action in ('attended','missed') and datetime.fromisoformat(row['starts_at'])>now:
        raise ValueError('Attendance can only be recorded after the appointment starts.')
    if action=='attended':
        day=datetime.fromisoformat(row['starts_at']).astimezone(ZoneInfo('Africa/Johannesburg')).date().isoformat()
        if not db.execute('''SELECT 1 FROM onboarding_visits WHERE id=? AND member_id=? AND visit_date=?
            AND recorded_at IS NOT NULL AND outcome IS NOT NULL AND outcome!='not_assisted' ''',(visit_id,row['member_id'],day)).fetchone():
            raise ValueError('Link the recorded assisted session for this member and appointment date.')
    db.execute('UPDATE fitness_allocation_requests SET status=?,outcome_note=?,visit_id=?,updated_by=?,updated_at=?,next_action=? WHERE id=?',
               (action,reason.strip(),visit_id if action=='attended' else None,actor_id,now.isoformat(),'Review session notes' if action=='attended' else 'Confirm a new appointment window',request_id))
    db.execute("UPDATE fitness_allocation_offers SET status='withdrawn',reason='Appointment closed' WHERE request_id=? AND status='pending'",(request_id,))


def sync_notifications(db, now=None):
    """Persistent in-app offer alerts, owned by this workflow; no outbound transport."""
    stamp=(now or utc_now()).isoformat()
    pending=db.execute("SELECT id,instructor_id,expires_at FROM fitness_allocation_offers WHERE status='pending'").fetchall()
    for offer in pending:
        db.execute('''INSERT INTO staff_notifications(source_kind,source_id,event_key,recipient_id,title,due_date,created_at)
            VALUES('fitness_offer',?,'respond',?,?,?,?) ON CONFLICT(source_kind,source_id,event_key,recipient_id) DO NOTHING''',
            (offer['id'],offer['instructor_id'],f"Fitness allocation offer #{offer['id']}: accept or decline within 8 hours",datetime.fromisoformat(offer['expires_at']).astimezone(ZoneInfo('Africa/Johannesburg')).date().isoformat(),stamp))
    db.execute('''UPDATE staff_notifications SET resolved_at=? WHERE source_kind='fitness_offer' AND resolved_at IS NULL
        AND NOT EXISTS(SELECT 1 FROM fitness_allocation_offers o WHERE o.id=staff_notifications.source_id AND o.status='pending')''',(stamp,))


def classify_skip(db, offer_id, manager_id, counts, reason):
    """Management determines which declines/timeouts count; preserve first decision."""
    require_manager(db, manager_id)
    check_reason(reason)
    if not reason.strip():
        raise ValueError('Management must record the reason for its decision.')
    changed = db.execute('''UPDATE fitness_allocation_offers
        SET counts_as_skip=?,reviewed_by=?,reviewed_at=?,review_reason=?
        WHERE id=? AND status IN ('declined','timed_out') AND reviewed_at IS NULL''',
        (int(counts), manager_id, utc_now().isoformat(), reason.strip(), offer_id)).rowcount
    if not changed:
        raise ValueError('Only an unreviewed declined or timed-out offer can be classified.')
    instructor = db.execute('SELECT instructor_id FROM fitness_allocation_offers WHERE id=?', (offer_id,)).fetchone()[0]
    count = db.execute('SELECT COUNT(*) FROM fitness_allocation_offers WHERE instructor_id=? AND counts_as_skip=1', (instructor,)).fetchone()[0]
    if counts and count >= SKIP_REVIEW_THRESHOLD:
        # Keep one unresolved issue per instructor; all further skips remain visible.
        pending = db.execute("SELECT 1 FROM fitness_pt_reviews WHERE instructor_id=? AND status='pending'", (instructor,)).fetchone()
        if not pending:
            db.execute('INSERT INTO fitness_pt_reviews(instructor_id,triggered_offer_id) VALUES(?,?)', (instructor,offer_id))
    return count


def decide_rights(db, review_id, manager_id, restrict, reason):
    require_manager(db, manager_id)
    check_reason(reason)
    if not reason.strip():
        raise ValueError('A management reason is required.')
    stamp = utc_now().isoformat()
    changed = db.execute('''UPDATE fitness_pt_reviews SET status='decided',decision=?,
        decided_by=?,decided_at=?,reason=? WHERE id=? AND status='pending' ''',
        ('restrict' if restrict else 'retain',manager_id,stamp,reason.strip(),review_id)).rowcount
    if not changed:
        raise ValueError('This review has already been decided or does not exist.')
    instructor = db.execute('SELECT instructor_id FROM fitness_pt_reviews WHERE id=?',(review_id,)).fetchone()[0]
    db.execute('''INSERT INTO fitness_pt_rights(instructor_id,restricted,decided_by,decided_at,reason)
        VALUES(?,?,?,?,?) ON CONFLICT(instructor_id) DO UPDATE SET restricted=excluded.restricted,
        decided_by=excluded.decided_by,decided_at=excluded.decided_at,reason=excluded.reason''',
        (instructor,int(restrict),manager_id,stamp,reason.strip()))


def pt_allowed(db, instructor_id):
    row = db.execute('SELECT restricted FROM fitness_pt_rights WHERE instructor_id=?',(instructor_id,)).fetchone()
    return not row or not row['restricted']
