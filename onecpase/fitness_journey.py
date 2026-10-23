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
    db.commit()


def welcome_text(first_name):
    return (f'Hi {first_name}, welcome to Elev8 Health & Fitness! Thank you for trusting us to support your fitness journey. '
            'We offer introductory instructor support during your first 10 calendar days, starting with your first session, '
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
        if interest not in {'awaiting_reply','interested','declined'} or (trainer and trainer not in {str(row['id']) for row in staff}):
            abort(400)
        db.execute('''UPDATE fitness_intake SET interest=?,preferred_times=?,assigned_instructor_id=?,goal=?,notes=?,updated_at=?,updated_by=? WHERE member_id=?''',
                   (interest,request.form.get('preferred_times','').strip(),trainer,request.form.get('goal','').strip(),request.form.get('notes','').strip(),datetime.now(timezone.utc).isoformat(),session['user_id'],mid))
        db.execute('UPDATE members SET fitness_goal=? WHERE id=?',(request.form.get('goal','').strip(),mid))
        from .database import member_activity
        member_activity(db,mid,f'Fitness preferences updated: {interest}; instructor {trainer or "unallocated"}.',user_id=session['user_id'])
        db.commit();flash('Fitness preferences and instructor allocation saved.','success')
        return redirect(url_for('fitness_journey.member_intake',mid=mid))
    intake=db.execute('SELECT * FROM fitness_intake WHERE member_id=?',(mid,)).fetchone()
    visits=db.execute('''SELECT v.*,u.full_name instructor_name,s.client_name,s.signed_at FROM onboarding_visits v
        LEFT JOIN users u ON u.id=v.instructor_id LEFT JOIN fitness_signoffs s ON s.visit_id=v.id
        WHERE v.member_id=? ORDER BY v.visit_date DESC''',(mid,)).fetchall()
    onboarding=db.execute('SELECT * FROM member_onboarding WHERE member_id=?',(mid,)).fetchone()
    visits=[dict(row)|{'signed_label':display_stamp(row['signed_at'])} for row in visits]
    end=(date.fromisoformat(intake['started_on'][:10])+timedelta(days=9)).isoformat() if intake['started_on'] else None
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
