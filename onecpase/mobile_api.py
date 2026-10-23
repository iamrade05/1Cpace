"""Opt-in, tenant-bound native client API. Member sessions never become staff sessions."""
from functools import wraps
import hashlib
import hmac
import secrets
import time

from flask import Blueprint, abort, current_app, g, jsonify, request
from werkzeug.security import check_password_hash
from werkzeug.exceptions import HTTPException

from .auth import _generate_otp, _send_otp_email, valid_email_address
from .database import close_db, get_db, _db_type
from .extensions import csrf, limiter
from .permissions import effective_permissions_for
from .phases import is_enabled
from .platform_db import get_platform_db

mobile_bp = Blueprint('mobile', __name__, url_prefix='/api/mobile/v1/<tenant_slug>')
# No browser-cookie authentication is accepted: writes require a scoped bearer,
# or a random, expiring OTP challenge. Browser routes retain normal CSRF checks.
csrf.exempt(mobile_bp)


@mobile_bp.errorhandler(HTTPException)
def api_error(error):
    return jsonify(error=error.description), error.code


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mobile_challenges (
        handle TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_id INTEGER NOT NULL,
        code_hash TEXT NOT NULL, expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        fingerprint TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS mobile_sessions (
        token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_id INTEGER NOT NULL,
        expires_at REAL NOT NULL, fingerprint TEXT NOT NULL)''')
    db.commit()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def scoped_hash(value):
    key = current_app.config['SECRET_KEY']
    return hmac.new(key.encode() if isinstance(key, str) else key,
                    f'{g.tenant_slug}:{value}'.encode(), hashlib.sha256).hexdigest()


def fingerprint(kind, row):
    values = (row['password_hash'], row['email']) if kind == 'staff' else (
        row['member_ref'], row['email'], row['member_status'])
    return digest(repr(values))


def subject(kind, uid):
    db = get_db()
    if kind == 'staff':
        return db.execute('''SELECT * FROM users WHERE id=? AND active=1
            AND COALESCE(is_platform_user,0)=0''', (uid,)).fetchone()
    return db.execute("SELECT * FROM members WHERE id=? AND member_status='Active'", (uid,)).fetchone()


def code_hash(handle, code):
    key = current_app.config['SECRET_KEY']
    return hmac.new(key.encode() if isinstance(key, str) else key,
                    f'mobile:{g.tenant_slug}:{handle}:{code}'.encode(), hashlib.sha256).hexdigest()


@mobile_bp.before_request
def bind_tenant():
    if not current_app.config.get('MOBILE_API_ENABLED', False):
        abort(404)
    slug = request.view_args.pop('tenant_slug')
    tenant = get_platform_db().execute(
        'SELECT * FROM tenants WHERE slug=? AND active=1', (slug,)).fetchone()
    if not tenant or current_app.config.get('DATABASE_URL'):
        # Multi-tenant native authentication is not enabled for legacy PG mode.
        abort(404)
    close_db()
    g.tenant, g.tenant_slug = dict(tenant), slug
    if request.method == 'POST' and not isinstance(request.get_json(silent=True), dict):
        return jsonify(error='Send a JSON object.'), 400


@mobile_bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


def authenticated(kind=None):
    def decorate(view):
        @wraps(view)
        def wrapped(**kwargs):
            header = request.headers.get('Authorization', '')
            if not header.startswith('Bearer ') or len(header) > 200:
                return jsonify(error='Sign in to continue.'), 401
            row = get_db().execute('SELECT * FROM mobile_sessions WHERE token_hash=? AND expires_at>?',
                (scoped_hash(header[7:]), time.time())).fetchone()
            if not row or (kind and row['kind'] != kind):
                return jsonify(error='Your session is unavailable. Sign in again.'), 401
            user = subject(row['kind'], row['subject_id'])
            if not user or not hmac.compare_digest(row['fingerprint'], fingerprint(row['kind'], user)):
                return jsonify(error='Your account changed. Sign in again.'), 401
            if row['kind'] == 'member' and not current_app.config.get('MOBILE_MEMBER_LOGIN_ENABLED', False):
                return jsonify(error='Member access is not enabled by this gym.'), 403
            if row['kind'] == 'staff' and user['must_change_password']:
                return jsonify(error='Change your password in the staff website, then sign in again.'), 403
            g.mobile_kind, g.mobile_user = row['kind'], user
            return view(**kwargs)
        return wrapped
    return decorate


def allowed(permission, blueprint):
    user = g.mobile_user
    assigned = [r['permission'] for r in get_db().execute(
        'SELECT permission FROM user_permissions WHERE user_id=?', (user['id'],))]
    perms = effective_permissions_for(user['role'], user['department'], assigned, user['is_sales_consultant'])
    return is_enabled(blueprint, current_app.config.get('ACTIVE_PHASE', 1)) and (
        user['role'] == 'admin' or permission in perms)


def feature(blueprint):
    if not is_enabled(blueprint, current_app.config.get('ACTIVE_PHASE', 1)):
        abort(403, description='This feature is not open at your gym.')


@mobile_bp.get('/configuration')
def configuration():
    return jsonify(gym=g.tenant['name'], staff=True,
        member=bool(current_app.config.get('MOBILE_MEMBER_LOGIN_ENABLED', False)))


@mobile_bp.post('/auth/start')
@limiter.limit('5 per minute')
def start():
    body = request.get_json(silent=True) or {}
    kind = body.get('kind')
    db = get_db()
    user = None
    if kind == 'staff':
        user = db.execute('''SELECT * FROM users WHERE lower(username)=? AND active=1
            AND COALESCE(is_platform_user,0)=0''', (str(body.get('username', '')).strip().lower(),)).fetchone()
        if not user or not check_password_hash(user['password_hash'], str(body.get('password', ''))):
            return jsonify(error='Invalid sign-in details.'), 401
        if user['must_change_password']:
            return jsonify(error='Change your password in the staff website before mobile sign-in.'), 403
    elif kind == 'member' and current_app.config.get('MOBILE_MEMBER_LOGIN_ENABLED', False):
        rows = db.execute('''SELECT * FROM members WHERE member_ref=? AND lower(email)=?
            AND member_status='Active' ''', (str(body.get('reference', '')).strip(),
            str(body.get('email', '')).strip().lower())).fetchall()
        user = rows[0] if len(rows) == 1 else None
        # Keep an identical challenge shape for an unknown member; do not reveal
        # whether a reference/email pair exists.
    else:
        return jsonify(error='This sign-in option is not available.'), 403
    handle, code = secrets.token_urlsafe(32), _generate_otp()
    if user and valid_email_address(user['email']):
        db.execute('DELETE FROM mobile_challenges WHERE expires_at<=?', (time.time(),))
        db.execute('DELETE FROM mobile_sessions WHERE expires_at<=?', (time.time(),))
        db.execute('INSERT INTO mobile_challenges(handle,kind,subject_id,code_hash,expires_at,fingerprint) VALUES (?,?,?,?,?,?)',
            (scoped_hash(handle), kind, user['id'], code_hash(handle, code), time.time()+300, fingerprint(kind, user)))
        db.commit()
        name = user['full_name'] if kind == 'staff' else user['first_name']
        if not _send_otp_email(user['email'], name, code):
            db.execute('DELETE FROM mobile_challenges WHERE handle=?', (scoped_hash(handle),)); db.commit()
            if kind == 'staff':
                return jsonify(error='We could not send the code. Please try again later.'), 503
    elif kind == 'staff':
        return jsonify(error='Ask your administrator to confirm your sign-in email.'), 403
    return jsonify(challenge=handle, message='Check your registered email. If no code arrives, contact your gym.', expires_in=300)


@mobile_bp.post('/auth/verify')
@limiter.limit('15 per minute')
def verify():
    body = request.get_json(silent=True) or {}
    handle, code = str(body.get('challenge', '')), str(body.get('code', '')).strip()
    db = get_db()
    # Claim and consume a challenge within one transaction; prevent replay and
    # simultaneous valid-code submissions from issuing multiple sessions.
    db.execute('BEGIN IMMEDIATE' if _db_type() == 'sqlite' else 'BEGIN')
    row = db.execute('SELECT * FROM mobile_challenges WHERE handle=?', (scoped_hash(handle),)).fetchone()
    if not row or row['expires_at'] <= time.time() or row['attempts'] >= 5:
        db.rollback()
        return jsonify(error='Code expired or unavailable. Start sign-in again.'), 401
    db.execute('UPDATE mobile_challenges SET attempts=attempts+1 WHERE handle=?', (scoped_hash(handle),))
    if not hmac.compare_digest(row['code_hash'], code_hash(handle, code)):
        db.commit()
        return jsonify(error='Incorrect code. Check the email and try again.'), 401
    user = subject(row['kind'], row['subject_id'])
    if not user or fingerprint(row['kind'], user) != row['fingerprint']:
        db.execute('DELETE FROM mobile_challenges WHERE handle=?', (scoped_hash(handle),)); db.commit()
        return jsonify(error='Account changed. Start sign-in again.'), 401
    token = secrets.token_urlsafe(48)
    db.execute('DELETE FROM mobile_challenges WHERE handle=?', (scoped_hash(handle),))
    db.execute('INSERT INTO mobile_sessions(token_hash,kind,subject_id,expires_at,fingerprint) VALUES (?,?,?,?,?)',
        (scoped_hash(token), row['kind'], user['id'], time.time()+12*3600, row['fingerprint']))
    db.commit()
    return jsonify(token=token, kind=row['kind'], expires_in=12*3600)


@mobile_bp.post('/auth/logout')
@authenticated()
def logout():
    db = get_db()
    db.execute('DELETE FROM mobile_sessions WHERE token_hash=?', (scoped_hash(request.headers['Authorization'][7:]),))
    db.commit()
    return jsonify(ok=True)


@mobile_bp.get('/home')
@authenticated()
def home():
    user = g.mobile_user
    if g.mobile_kind == 'member':
        feature('members')
        return jsonify(kind='member', name=user['first_name'], gym=g.tenant['name'],
            reference=user['member_ref'], membership=user['member_status'], package=user['package'] or user['tariff'],
            monthly_fee=user['monthly_installment'], access=user['gym_access_status'],
            features=['account']+(['classes', 'plans'] if is_enabled('fitness', current_app.config.get('ACTIVE_PHASE',1)) else []))
    features = ['notifications', 'hr']
    for name, permission, blueprint in [('leads','view_leads','leads'), ('members','all_members','members'),
                                       ('queries','daily_queries','queries'), ('classes','classes','fitness')]:
        if allowed(permission, blueprint): features.append(name)
    return jsonify(kind='staff', name=user['full_name'], gym=g.tenant['name'],
                   department=user['department'], role=user['role'], features=features)


def items(rows, title, subtitle, status, detail=None):
    return [dict(id=r['id'], title=str(title(r) or ''), subtitle=str(subtitle(r) or ''),
                 status=str(status(r) or ''), detail=str(detail(r) or '') if detail else '') for r in rows]


@mobile_bp.get('/staff/<section>')
@authenticated('staff')
def staff_list(section):
    db, uid = get_db(), g.mobile_user['id']
    if section == 'notifications':
        rows = db.execute('''SELECT id,title,source_kind,due_date FROM staff_notifications
            WHERE recipient_id=? AND resolved_at IS NULL AND acknowledged_at IS NULL ORDER BY id DESC LIMIT 50''', (uid,)).fetchall()
        return jsonify(items=items(rows,lambda r:r['title'],lambda r:r['source_kind'],lambda r:r['due_date']))
    if section == 'hr':
        rows = db.execute('SELECT id,week_start,status,entries FROM hr_weeks WHERE user_id=? ORDER BY week_start DESC LIMIT 12',(uid,)).fetchall()
        return jsonify(items=items(rows,lambda r:'Week of '+r['week_start'],lambda r:'My signed time record',lambda r:r['status']))
    mappings = {'leads':('view_leads','leads'), 'members':('all_members','members'), 'queries':('daily_queries','queries')}
    if section not in mappings or not allowed(*mappings[section]): abort(403)
    q = str(request.args.get('q','')).strip()[:100]
    if section == 'leads':
        rows = db.execute('''SELECT id,full_name,source,lead_status,next_action FROM leads
            WHERE assigned_to=? AND full_name LIKE ? ORDER BY id DESC LIMIT 50''',(uid,'%'+q+'%')).fetchall()
        result=items(rows,lambda r:r['full_name'],lambda r:r['source'],lambda r:r['lead_status'],lambda r:r['next_action'])
    elif section == 'members':
        rows = db.execute('''SELECT id,first_name,last_name,member_ref,member_status,package FROM members
            WHERE first_name LIKE ? OR last_name LIKE ? OR member_ref LIKE ? ORDER BY id DESC LIMIT 50''',('%'+q+'%',)*3).fetchall()
        result=items(rows,lambda r:r['first_name']+' '+r['last_name'],lambda r:r['member_ref'],lambda r:r['member_status'],lambda r:r['package'])
    else:
        rows=db.execute('''SELECT id,reference,description,status,category FROM queries
            WHERE assigned_to=? AND (reference LIKE ? OR description LIKE ?) ORDER BY id DESC LIMIT 50''',(uid,'%'+q+'%','%'+q+'%')).fetchall()
        result=items(rows,lambda r:r['reference'],lambda r:r['category'],lambda r:r['status'],lambda r:r['description'])
    return jsonify(items=result)


@mobile_bp.get('/member/<section>')
@authenticated('member')
def member_list(section):
    db, user = get_db(), g.mobile_user
    feature('members')
    if section == 'account':
        return jsonify(items=[dict(id=1,title=user['package'] or user['tariff'] or 'Membership',
            subtitle=user['member_ref'],status=user['member_status'],
            detail=f"Monthly membership: R{float(user['monthly_installment'] or 0):.2f}. Contact your gym for a verified account statement.")])
    if section == 'plans':
        feature('fitness')
        rows=db.execute('''SELECT id,title,description,start_date,active FROM workout_plans
            WHERE member_id=? ORDER BY id DESC LIMIT 30''',(user['id'],)).fetchall()
        return jsonify(items=items(rows,lambda r:r['title'],lambda r:r['start_date'],lambda r:'Active' if r['active'] else 'Previous',lambda r:r['description']))
    abort(404)


@mobile_bp.get('/classes')
@authenticated()
def classes():
    feature('fitness')
    if g.mobile_kind == 'staff' and not allowed('classes','fitness'): abort(403)
    uid = g.mobile_user['id'] if g.mobile_kind == 'member' else -1
    rows=get_db().execute('''SELECT fc.id,fc.name,fc.schedule_day,fc.schedule_time,fc.capacity,
        (SELECT COUNT(*) FROM class_bookings cb WHERE cb.class_id=fc.id AND cb.status='booked') AS booked,
        (SELECT COUNT(*) FROM class_bookings cb WHERE cb.class_id=fc.id AND cb.member_id=? AND cb.status='booked') AS mine
        FROM fitness_classes fc WHERE fc.active=1 ORDER BY fc.schedule_day,fc.schedule_time''',(uid,)).fetchall()
    return jsonify(items=[dict(id=r['id'],title=r['name'],subtitle=f"{r['schedule_day']} · {r['schedule_time']}",
        status='Booked' if r['mine'] else f"{max(0,r['capacity']-r['booked'])} places",detail='',booked=bool(r['mine'])) for r in rows])


@mobile_bp.post('/classes/<int:cid>/booking')
@authenticated('member')
def book(cid):
    feature('fitness')
    db=get_db(); uid=g.mobile_user['id']
    body=request.get_json(silent=True) or {}
    if body.get('action') not in ('book','cancel'): return jsonify(error='Choose book or cancel.'),400
    from .fitness_bookings import change_booking
    try:
        change_booking(db, cid, uid, body['action'])
    except ValueError as error:
        return jsonify(error=str(error)),409
    return jsonify(ok=True)
