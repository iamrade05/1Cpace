"""Fitness intake, Reception welcomes and client session acknowledgements."""
import json
import math
from functools import wraps
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from .auth import permission_required
from .database import get_db
from .fitness_cycle import today

journey_bp = Blueprint('fitness_journey', __name__, url_prefix='/fitness/intake')


@journey_bp.route('/allocations', methods=['GET','POST'])
@permission_required('fitness_module')
def allocations():
    from . import fitness_allocation as allocation
    db=get_db();ensure_journey(db)
    allocation.begin_allocation(db)
    try:
        if request.method=='POST':
            if request.form.get('action') not in ('accept','decline'):abort(400)
            allocation.respond(db,int(request.form.get('offer_id','')),session['user_id'],
                               request.form['action']=='accept',request.form.get('reason',''))
        allocation.process_queue(db)
        db.commit()
    except (ValueError,TypeError) as exc:
        # Expired offers must stay expired even when a late response is rejected.
        db.rollback()
        allocation.begin_allocation(db);allocation.process_queue(db);db.commit()
        flash(str(exc),'warning')
    if request.method=='POST':return redirect(url_for('fitness_journey.allocations'))
    manager=session.get('role') in ('admin','manager')
    clause='' if manager else ' WHERE o.instructor_id=?'
    offers=db.execute('''SELECT o.*,u.full_name FROM fitness_allocation_offers o
        JOIN users u ON u.id=o.instructor_id'''+clause+' ORDER BY o.id DESC LIMIT 100',
        () if manager else (session['user_id'],)).fetchall()
    clause='' if manager else ' WHERE r.instructor_id=?'
    requests=db.execute('''SELECT r.*,u.full_name FROM fitness_allocation_requests r
        LEFT JOIN users u ON u.id=r.instructor_id'''+clause+' ORDER BY r.id DESC LIMIT 100',
        () if manager else (session['user_id'],)).fetchall()
    requests=[dict(row)|{'start_label':display_stamp(row['starts_at']),'end_label':display_stamp(row['ends_at'])} for row in requests]
    offers=[dict(row)|{'deadline_label':display_stamp(row['expires_at'])} for row in offers]
    return render_template('fitness/allocations.html',offers=offers,allocations=requests,manager=manager)


@journey_bp.post('/<int:mid>/request-allocation')
@permission_required('fitness_module')
def request_allocation(mid):
    from . import fitness_allocation as allocation
    db=get_db();ensure_journey(db)
    if not db.execute('SELECT 1 FROM members WHERE id=?',(mid,)).fetchone():abort(404)
    try:
        start=datetime.fromisoformat(request.form.get('starts_at','')).replace(tzinfo=ZoneInfo('Africa/Johannesburg'))
        end=datetime.fromisoformat(request.form.get('ends_at','')).replace(tzinfo=ZoneInfo('Africa/Johannesburg'))
        allocation.begin_allocation(db)
        allocation.request_allocation(db,mid,start.isoformat(),end.isoformat(),session['user_id'])
        allocation.process_queue(db);db.commit()
        flash('Availability recorded. The allocation queue will select an eligible instructor.','success')
    except (ValueError,TypeError) as exc:
        db.rollback();flash(str(exc),'warning')
    return redirect(url_for('fitness_journey.member_intake',mid=mid))


@journey_bp.post('/appointment/<int:rid>')
@permission_required('fitness_module')
def appointment(rid):
    from . import fitness_allocation as allocation
    db=get_db();ensure_journey(db)
    try:
        allocation.begin_allocation(db)
        allocation.record_appointment(db,rid,request.form.get('action'),session['user_id'],
                                      request.form.get('reason',''),request.form.get('visit_id') or None)
        allocation.process_queue(db);db.commit()
        flash('Appointment updated.','success')
    except (ValueError,TypeError) as exc:
        db.rollback();flash(str(exc),'warning')
    return redirect(url_for('fitness_journey.allocations'))


@journey_bp.route('/allocation-roster',methods=['GET','POST'])
@permission_required('fitness_module')
def allocation_roster():
    from . import fitness_allocation as allocation
    if session.get('role') not in ('admin','manager'):abort(403)
    db=get_db();ensure_journey(db)
    if request.method=='POST':
        try:
            action=request.form.get('action')
            weekday=int(request.form.get('weekday',''))
            start,end=request.form.get('start_time',''),request.form.get('end_time','')
            if weekday not in range(7) or not start or not end:raise ValueError('Choose a day and valid opening/shift times.')
            start=datetime.strptime(start,'%H:%M').strftime('%H:%M');end=datetime.strptime(end,'%H:%M').strftime('%H:%M')
            if end<=start:raise ValueError('End must be after start; enter each day separately.')
            allocation.begin_allocation(db)
            if action=='hours':
                db.execute('''INSERT INTO fitness_gym_hours(weekday,start_time,end_time) VALUES(?,?,?)
                    ON CONFLICT(weekday) DO UPDATE SET start_time=excluded.start_time,end_time=excluded.end_time''',(weekday,start,end))
            elif action=='roster':
                uid=int(request.form.get('user_id',''));staff_id=request.form.get('staff_id') or None
                kind,rotation=request.form.get('kind'),request.form.get('rotation')
                if kind not in ('fitness','group') or rotation not in ('early','late','support','fixed'):raise ValueError('Choose a valid instructor type and rotation.')
                if kind=='group' and rotation!='fixed':raise ValueError('Group instructors use their confirmed assistance hours.')
                if not db.execute('SELECT 1 FROM users WHERE id=? AND active=1',(uid,)).fetchone():raise ValueError('Choose an active account.')
                if staff_id and not db.execute('SELECT 1 FROM staff WHERE id=?',(staff_id,)).fetchone():raise ValueError('Choose a valid linked HR record.')
                db.execute('''INSERT INTO fitness_allocation_roster(user_id,staff_id,kind,rotation,enabled,updated_by,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET staff_id=excluded.staff_id,kind=excluded.kind,
                    rotation=excluded.rotation,enabled=excluded.enabled,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                    (uid,staff_id,kind,rotation,int(request.form.get('enabled')=='yes'),session['user_id'],allocation.utc_now().isoformat()))
                db.execute('''INSERT INTO fitness_roster_windows(user_id,weekday,start_time,end_time) VALUES(?,?,?,?)
                    ON CONFLICT(user_id,weekday) DO UPDATE SET start_time=excluded.start_time,end_time=excluded.end_time''',(uid,weekday,start,end))
            else:raise ValueError('Choose a valid action.')
            allocation.process_queue(db);db.commit();flash('Allocation roster saved.','success')
        except (ValueError,TypeError) as exc:
            db.rollback();flash(str(exc),'warning')
        return redirect(url_for('fitness_journey.allocation_roster'))
    roster=db.execute('''SELECT r.*,u.full_name,w.weekday,w.start_time,w.end_time FROM fitness_allocation_roster r
        JOIN users u ON u.id=r.user_id LEFT JOIN fitness_roster_windows w ON w.user_id=r.user_id ORDER BY u.full_name,w.weekday''').fetchall()
    return render_template('fitness/allocation_roster.html',roster=roster,
        users=db.execute('SELECT id,full_name FROM users WHERE active=1 ORDER BY full_name').fetchall(),
        staff=db.execute('SELECT id,first_name,last_name FROM staff ORDER BY first_name').fetchall(),
        hours=db.execute('SELECT * FROM fitness_gym_hours ORDER BY weekday').fetchall())


@journey_bp.route('/allocation-reviews', methods=['GET', 'POST'])
@permission_required('fitness_module')
def allocation_reviews():
    from . import fitness_allocation as allocation
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    ensure_journey(db)
    allocation.begin_allocation(db)
    allocation.expire_offers(db)
    if request.method == 'POST':
        try:
            target = int(request.form.get('target_id', ''))
            action = request.form.get('action')
            if action == 'classify' and request.form.get('counts') in ('yes', 'no'):
                allocation.classify_skip(db, target, session['user_id'], request.form['counts']=='yes', request.form.get('reason',''))
            elif action == 'decide' and request.form.get('restrict') in ('yes', 'no'):
                allocation.decide_rights(db, target, session['user_id'], request.form['restrict']=='yes', request.form.get('reason',''))
            else:
                raise ValueError('Select a valid management decision.')
            db.commit()
        except (ValueError, TypeError) as exc:
            db.rollback()
            flash(str(exc), 'warning')
        return redirect(url_for('fitness_journey.allocation_reviews'))
    db.commit()
    offers = db.execute('''SELECT o.*,u.full_name FROM fitness_allocation_offers o
        JOIN users u ON u.id=o.instructor_id WHERE o.status IN ('declined','timed_out')
        ORDER BY o.id DESC''').fetchall()
    reviews = db.execute('''SELECT r.*,u.full_name FROM fitness_pt_reviews r
        JOIN users u ON u.id=r.instructor_id ORDER BY r.id DESC''').fetchall()
    return render_template('fitness/allocation_reviews.html', offers=offers, reviews=reviews)


def intake_access(view):
    @wraps(view)
    def wrapped(**kwargs):
        permission='fitness_module' if session.get('role')=='admin' or 'fitness_module' in session.get('permissions',[]) else 'reception_whatsapp'
        return permission_required(permission)(view)(**kwargs)
    return wrapped


def display_stamp(value):
    if not value:return ''
    stamp=datetime.fromisoformat(value)
    if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(ZoneInfo('Africa/Johannesburg')).strftime('%d %b %Y %H:%M')


def ensure_journey(db):
    db.execute('''CREATE TABLE IF NOT EXISTS fitness_intake (
        member_id INTEGER PRIMARY KEY, interest TEXT DEFAULT 'awaiting_reply', preferred_times TEXT,
        assigned_instructor_id INTEGER, goal TEXT, notes TEXT, welcome_status TEXT DEFAULT 'pending',
        welcome_message_id INTEGER, started_on TEXT, updated_at TEXT, updated_by INTEGER,
        FOREIGN KEY(member_id) REFERENCES members(id) ON DELETE CASCADE,
        FOREIGN KEY(assigned_instructor_id) REFERENCES users(id))''')
    from .database import _ensure_column, _db_type
    _ensure_column(db, _db_type(), 'fitness_intake', 'started_on', 'TEXT')
    _ensure_column(db, _db_type(), 'fitness_intake', 'program_start_on', 'TEXT')
    _ensure_column(db, _db_type(), 'fitness_intake', 'goal_category', "TEXT DEFAULT 'unclear'")
    _ensure_column(db, _db_type(), 'fitness_intake', 'allocation_state', "TEXT DEFAULT 'awaiting_goal_clarification'")
    _ensure_column(db, _db_type(), 'fitness_intake', 'primary_instructor_id', 'INTEGER REFERENCES users(id)')
    db.execute('UPDATE fitness_intake SET primary_instructor_id=assigned_instructor_id WHERE primary_instructor_id IS NULL AND assigned_instructor_id IS NOT NULL')
    from .fitness_allocation import ensure_schema as ensure_allocation_schema
    ensure_allocation_schema(db)
    db.execute('''CREATE TABLE IF NOT EXISTS fitness_signoffs (
        visit_id INTEGER PRIMARY KEY, client_name TEXT NOT NULL, signature TEXT NOT NULL,
        signed_at TEXT NOT NULL, captured_by INTEGER NOT NULL, instructor_id INTEGER NOT NULL,
        FOREIGN KEY(visit_id) REFERENCES onboarding_visits(id) ON DELETE CASCADE,
        FOREIGN KEY(captured_by) REFERENCES users(id), FOREIGN KEY(instructor_id) REFERENCES users(id))''')
    db.execute('CREATE TABLE IF NOT EXISTS fitness_journey_policy (id INTEGER PRIMARY KEY, initialized_at TEXT)')
    first = db.execute('INSERT OR IGNORE INTO fitness_journey_policy(id,initialized_at) VALUES(1,?)',
                       (today().isoformat(),)).rowcount
    if first:
        # An upgrade is not permission to send a welcome campaign to historical members.
        db.execute("INSERT OR IGNORE INTO fitness_intake(member_id,welcome_status) SELECT id,'historical' FROM members WHERE outcome='joined' OR EXISTS(SELECT 1 FROM member_onboarding o WHERE o.member_id=members.id)")
        db.execute('''UPDATE fitness_intake SET started_on=(SELECT MIN(v.visit_date) FROM onboarding_visits v
            WHERE v.member_id=fitness_intake.member_id AND v.recorded_at IS NOT NULL AND v.outcome IS NOT NULL AND v.outcome!='not_assisted')''')
    db.execute("INSERT OR IGNORE INTO fitness_intake(member_id) SELECT id FROM members WHERE outcome='joined'")
    # Keep an already-started legacy programme intact. New programmes begin on
    # the day after joining, independently of their first attended session.
    for intake in db.execute('''SELECT f.member_id,f.started_on,m.join_date FROM fitness_intake f
        JOIN members m ON m.id=f.member_id WHERE f.program_start_on IS NULL''').fetchall():
        try:
            start=intake['started_on'] or (date.fromisoformat(str(intake['join_date'])[:10])+timedelta(days=1)).isoformat()
        except (TypeError,ValueError):
            continue  # Missing/invalid joining date requires staff correction.
        db.execute('UPDATE fitness_intake SET program_start_on=? WHERE member_id=?',(start,intake['member_id']))
    db.commit()


def welcome_text(first_name):
    return (f'Hi {first_name}, welcome to Elev8 Health & Fitness! Thank you for trusting us to support your fitness journey. '
            'We offer a 10-calendar-day introductory support programme starting the day after you join, '
            'to help you get comfortable with the gym and your training. Would you like to take part? '
            'Please reply with the days and times you expect to attend so we can arrange an instructor. '
            'Afterwards, you can train independently or choose optional personal training. We look forward to welcoming you!')


def queue_welcome(db, member, created_by=None):
    from .comms import send_whatsapp_message
    from .whatsapp_contacts import phone_key
    from .encryption import decrypt
    from flask import current_app
    phone = phone_key(decrypt(member['contact']))
    if not phone:
        db.execute("UPDATE fitness_intake SET welcome_status='invalid_number' WHERE member_id=?", (member['id'],)); db.commit()
        return 'invalid_number'
    claimed = db.execute("UPDATE fitness_intake SET welcome_status='preparing' WHERE member_id=? AND welcome_status IN ('pending','failed','historical','invalid_number')", (member['id'],)).rowcount
    db.commit()
    if not claimed:
        return 'already_recorded'
    text = welcome_text(decrypt(member['first_name']))
    db.execute("INSERT OR IGNORE INTO wa_conversations(wa_number,contact_phone,linked_member_id) VALUES('reception',?,?)", (phone,member['id']))
    conv = db.execute("SELECT id FROM wa_conversations WHERE wa_number='reception' AND contact_phone=?", (phone,)).fetchone()
    stamp=datetime.now(timezone.utc).isoformat()
    message_id = db.execute("INSERT INTO wa_messages(conversation_id,direction,message_type,content,status,sent_by_user_id,timestamp) VALUES(?,'outbound','text',?,'preparing',?,?)",
                            (conv['id'],text,created_by,stamp)).lastrowid
    db.execute('UPDATE wa_conversations SET last_message_at=? WHERE id=?',(stamp,conv['id']))
    db.execute('UPDATE fitness_intake SET welcome_message_id=? WHERE member_id=?', (message_id,member['id'])); db.commit()
    result = send_whatsapp_message(current_app.config.get('WA_RECEPTION_PHONE_ID',''),phone,text)
    messages = result.get('messages') or []
    provider_id = messages[0].get('id') if isinstance(messages,list) and messages else None
    state = 'failed' if 'error' in result else 'queued' if result.get('queued') else 'sent' if provider_id else 'failed'
    db.execute('UPDATE wa_messages SET status=?,wa_message_id=? WHERE id=?', (state,provider_id,message_id))
    db.execute('UPDATE fitness_intake SET welcome_status=? WHERE member_id=?', (state,member['id'])); db.commit()
    return state


def sync_welcomes(app, send=True):
    from .phases import is_enabled
    if not is_enabled('fitness_journey',app.config.get('ACTIVE_PHASE',1)):
        return
    from .platform_db import get_platform_db
    with app.app_context():
        tenant = get_platform_db().execute('SELECT * FROM tenants WHERE slug=? AND active=1',
                    (app.config.get('WHATSAPP_WEB_TENANT','elev8'),)).fetchone()
        if not tenant:
            return
        g.tenant=dict(tenant);g.tenant_slug=tenant['slug']
        db=get_db();ensure_journey(db)
        if not send:
            return
        from .onboarding import ensure_onboarding_rows, sync_arrivals_from_turnstile
        ensure_onboarding_rows(db);sync_arrivals_from_turnstile(db)
        # New joined records after activation only; old imports never trigger a campaign.
        member=db.execute("""SELECT m.* FROM members m JOIN fitness_intake f ON f.member_id=m.id
            WHERE f.welcome_status='pending' AND m.outcome='joined' AND m.member_status='Active'
              AND substr(m.join_date,1,10)>=? AND substr(m.join_date,1,10)<=?
            ORDER BY m.id LIMIT 1""", (db.execute('SELECT substr(initialized_at,1,10) FROM fitness_journey_policy WHERE id=1').fetchone()[0],today().isoformat())).fetchone()
        if member:
            queue_welcome(db,member)


@journey_bp.route('/<int:mid>', methods=['GET','POST'])
@intake_access
def member_intake(mid):
    from .fitness import get_fitness_staff
    db=get_db();ensure_journey(db)
    member=db.execute('SELECT * FROM members WHERE id=?',(mid,)).fetchone()
    if not member:
        abort(404)
    db.execute('INSERT OR IGNORE INTO fitness_intake(member_id,welcome_status) VALUES(?,\'historical\')',(mid,));db.commit()
    staff=get_fitness_staff()
    if request.method=='POST':
        interest=request.form.get('interest','awaiting_reply');trainer=request.form.get('instructor_id') or None
        if trainer and trainer not in {str(row['id']) for row in staff}:abort(400)
        category=request.form.get('goal_category','unclear')
        if category not in {'weight_loss','toning','muscle_gain','weight_gain','general_fitness','mixed','unclear'}:abort(400)
        clarification=category in {'general_fitness','mixed','unclear'}
        from .fitness_allocation import begin_allocation,process_queue
        begin_allocation(db)
        current=db.execute('SELECT * FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()
        # The automatic queue owns allocation. A preference edit preserves its
        # instructor rather than overwriting it with a dropdown value.
        trainer=current['assigned_instructor_id']
        if interest!='interested' or current['goal_category']!=category:
            db.execute("UPDATE fitness_allocation_offers SET status='withdrawn',reason='Member preferences changed' WHERE member_id=? AND status='pending'",(mid,))
            db.execute("UPDATE fitness_allocation_requests SET status='cancelled',next_action='Member preferences changed; confirm a new window' WHERE member_id=? AND status IN ('waiting','offered','reserved')",(mid,))
        if clarification and trainer:
            flash('Ask the member to clarify their primary goal before allocating an instructor.','warning')
            trainer=None
        if interest not in {'awaiting_reply','interested','declined'}:
            abort(400)
        db.execute('''UPDATE fitness_intake SET interest=?,preferred_times=?,assigned_instructor_id=?,goal=?,notes=?,updated_at=?,updated_by=? WHERE member_id=?''',
                   (interest,request.form.get('preferred_times','').strip(),trainer,request.form.get('goal','').strip(),request.form.get('notes','').strip(),datetime.now(timezone.utc).isoformat(),session['user_id'],mid))
        db.execute('UPDATE members SET fitness_goal=? WHERE id=?',(request.form.get('goal','').strip(),mid))
        db.execute('UPDATE fitness_intake SET goal_category=?,allocation_state=? WHERE member_id=?',
                   (category,'awaiting_goal_clarification' if clarification else 'allocated' if trainer else 'awaiting_allocation',mid))
        from .database import member_activity
        member_activity(db,mid,f'Fitness preferences updated: {interest}; instructor {trainer or "unallocated"}.',user_id=session['user_id'])
        process_queue(db)
        db.commit();flash('Fitness preferences and instructor allocation saved.','success')
        return redirect(url_for('fitness_journey.member_intake',mid=mid))
    intake=db.execute('SELECT * FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()
    visits=db.execute('''SELECT v.*,u.full_name instructor_name,s.client_name,s.signed_at FROM onboarding_visits v
        LEFT JOIN users u ON u.id=v.instructor_id LEFT JOIN fitness_signoffs s ON s.visit_id=v.id
        WHERE v.member_id=? ORDER BY v.visit_date DESC''',(mid,)).fetchall()
    onboarding=db.execute('SELECT * FROM member_onboarding WHERE member_id=?',(mid,)).fetchone()
    visits=[dict(row)|{'signed_label':display_stamp(row['signed_at'])} for row in visits]
    end=(date.fromisoformat(intake['program_start_on'][:10])+timedelta(days=9)).isoformat() if intake['program_start_on'] else None
    return render_template('fitness/intake.html',member=member,intake=intake,staff=staff,visits=visits,onboarding=onboarding,onboarding_end=end,
                           welcome_eligible=_manual_welcome_eligible(member))


def _manual_welcome_eligible(member):
    # Joined members can await membership verification while arranging their introduction.
    return member['outcome'] == 'joined' and member['member_status'] in ('Active', 'Unverified')


@journey_bp.post('/<int:mid>/welcome')
@intake_access
def send_welcome(mid):
    db=get_db();ensure_journey(db)
    member=db.execute('SELECT * FROM members WHERE id=?',(mid,)).fetchone()
    if not member:abort(404)
    if not _manual_welcome_eligible(member):
        flash('Welcome not sent. The client must be joined with an Active or Unverified membership status. Check the client profile.', 'warning')
        return redirect(url_for('fitness_journey.member_intake',mid=mid))
    state=queue_welcome(db,member,session['user_id'])
    flash('Welcome message: '+state.replace('_',' ')+'.','info')
    return redirect(url_for('fitness_journey.member_intake',mid=mid))


@journey_bp.post('/visit/<int:vid>/sign')
@permission_required('fitness_module')
def sign_session(vid):
    db=get_db();ensure_journey(db)
    visit=db.execute('SELECT * FROM onboarding_visits WHERE id=?',(vid,)).fetchone()
    if not visit:abort(404)
    if not visit['recorded_at'] or not visit['instructor_id'] or visit['outcome'] in (None,'not_assisted'):
        abort(400,description='Record the assisted session before asking the client to sign.')
    baseline=db.execute("SELECT id FROM assessments WHERE member_id=? AND is_onboarding=1 AND weight_kg>0 AND height_cm>0 AND fitness_goal IS NOT NULL AND fitness_goal!=''",(visit['member_id'],)).fetchone()
    if not baseline:
        abort(400,description='Complete the baseline assessment and client goal before session sign-off.')
    name=request.form.get('client_name','').strip()
    try:
        strokes=json.loads(request.form.get('signature','[]'))
        valid=isinstance(strokes,list) and 1<=len(strokes)<=50
        total=0
        for stroke in strokes:
            valid=valid and isinstance(stroke,list) and len(stroke)>=2
            total+=len(stroke)
            for point in stroke:
                valid=valid and len(point)==2 and all(isinstance(v,(float,int)) and math.isfinite(v) for v in point) and 0<=point[0]<=600 and 0<=point[1]<=180
        valid=valid and 2<=total<=3000
    except (ValueError,TypeError,KeyError):
        valid=False
    if not valid or not name or len(name)>150 or request.form.get('confirm')!='yes':
        abort(400,description='The client must draw a signature, enter their name and confirm the session.')
    db.execute('''INSERT OR IGNORE INTO fitness_signoffs(visit_id,client_name,signature,signed_at,captured_by,instructor_id)
        VALUES(?,?,?,?,?,?)''',(vid,name,json.dumps(strokes),datetime.now(timezone.utc).isoformat(),session['user_id'],visit['instructor_id']))
    db.commit();flash('Client session acknowledgement saved.','success')
    return redirect(url_for('onboarding.record_visit',vid=vid))
