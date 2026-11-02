"""Dashboard boundaries are independent of operational approval permissions."""
from functools import wraps
from flask import abort, g, session, url_for
from .database import get_db


def scope_for(user):
    if not user:return 'work'
    user=dict(user)
    role=str(user.get('role') or '').strip().lower()
    department=str(user.get('department') or '').strip().lower()
    if role=='admin':return 'admin'
    if department in ('reception','collections') or role in ('reception','receptionist'):return 'reception'
    if department in ('fitness','training') or role in ('trainer','instructor'):return 'fitness'
    if department=='sales' or user.get('is_sales_consultant') or role in ('sales','sales_manager'):return 'sales'
    return 'work'


def actor():
    if not hasattr(g,'dashboard_actor'):
        # A platform owner's shadow user only counts while they are actually in Power User mode.
        g.dashboard_actor=get_db().execute('SELECT * FROM users WHERE id=? AND active=1 AND (COALESCE(is_platform_user,0)=0 OR ?)',
                                          (session.get('user_id'),int(bool(session.get('is_power_user'))))).fetchone() if session.get('user_id') else None
    return g.dashboard_actor


def current_scope():return scope_for(actor())


def home_endpoint():
    return {'admin':'dashboard.dashboard','sales':'dashboard.sales_dashboard',
            'reception':'dashboard.reception_dashboard','fitness':'dashboard.fitness_dashboard'}.get(current_scope(),'dashboard.work_dashboard')


def dashboard_only(scope):
    def decorate(view):
        @wraps(view)
        def wrapped(*args,**kwargs):
            if not actor() or current_scope() not in ('admin',scope):abort(403)
            return view(*args,**kwargs)
        return wrapped
    return decorate


def team_ids(db,scope):
    return [u['id'] for u in db.execute('SELECT * FROM users WHERE active=1 AND COALESCE(is_platform_user,0)=0').fetchall() if scope_for(u)==scope]
