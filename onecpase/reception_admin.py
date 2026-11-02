"""Reception administration routes with role checks and atomic stock/cash writes."""
from datetime import date
from functools import wraps
import secrets
import sqlite3
import json
from flask import Blueprint,abort,flash,redirect,render_template,request,session,url_for
from .auth import login_required
from .database import get_db
from . import reception_ops as ops

reception_admin_bp=Blueprint('reception_admin',__name__,url_prefix='/reception-admin')
def access(db=None):
    db=db or get_db()
    user=db.execute('SELECT * FROM users WHERE id=? AND active=1 AND COALESCE(is_platform_user,0)=0',(session.get('user_id'),)).fetchone()
    if not user:return False,False
    manager=user['role']=='admin'
    allowed=manager or str(user['department'] or '').strip().lower()=='reception' or user['role'] in ('reception','receptionist')
    return allowed,manager
def reception_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args,**kwargs):
        if not access()[0]:abort(403)
        return view(*args,**kwargs)
    return wrapped
def selected_day():
    try:return date.fromisoformat(request.args.get('day') or ops.today().isoformat()).isoformat()
    except ValueError:abort(400,description='Choose a valid date.')

@reception_admin_bp.get('/')
@reception_required
def index():
    db=get_db();day=selected_day();active=ops.current_shift(db);inventory=ops.items(db)
    shifts=[dict(r) for r in db.execute('SELECT * FROM reception_shifts WHERE work_date=? ORDER BY id',(day,))]
    records=[ops.report(db,s['id']) for s in shifts]
    manager=access(db)[1]
    tab=request.args.get('tab','overview')
    if tab not in ('overview','checks','stock','cards','cash','reports'):tab='overview'
    selected_member=None
    if request.args.get('member',type=int):
        try:selected_member=ops.member(db,request.args.get('member',type=int))
        except ValueError:pass
    for item in inventory:
        rows=db.execute('SELECT m.kind,m.quantity FROM reception_movements m JOIN reception_shifts s ON s.id=m.shift_id WHERE m.item_id=? AND s.work_date=?',(item['id'],day)).fetchall()
        item['received']=sum(r['quantity'] for r in rows if r['quantity']>0 and r['kind']!='initial_count')
        item['out']=sum(-r['quantity'] for r in rows if r['quantity']<0)
    pending=[dict(r) for r in db.execute("SELECT s.id,s.work_date,s.slot,u.full_name FROM reception_shifts s JOIN users u ON u.id=s.closed_by WHERE s.status='closed' AND s.reviewed_at IS NULL ORDER BY s.id DESC")] if manager else []
    members=[]
    search=request.args.get('member_q','').strip()[:100]
    if search:
        pattern='%'+search+'%'
        members=db.execute('SELECT id,member_ref,first_name,last_name FROM members WHERE first_name LIKE ? OR last_name LIKE ? OR member_ref LIKE ? ORDER BY last_name LIMIT 30',(pattern,pattern,pattern)).fetchall()
    return render_template('reception_admin/index.html',day=day,today=ops.today().isoformat(),active=active,inventory=inventory,records=records,
        manager=manager,pending=pending,outstanding=ops.outstanding(db,day),token=secrets.token_urlsafe(24),categories=ops.CATEGORIES,methods=ops.METHODS,members=members,member_search=search,
        live_totals=ops.totals(db,active['id']) if active else None,tab=tab,selected_member=selected_member)

@reception_admin_bp.post('/action')
@reception_required
def action():
    db=get_db();kind=request.form.get('action');uid=session['user_id'];manager=access(db)[1]
    try:
        with ops.transaction(db):
            if kind not in ('open','item'):
                active=ops.current_shift(db)
                if not active or str(active['id'])!=request.form.get('shift_id'):raise ValueError('The shift changed. Refresh before recording this entry.')
            token=ops.text(request.form.get('token'),True)
            if len(token)<16:raise ValueError('Reload the form before submitting.')
            if db.execute('SELECT 1 FROM reception_audit WHERE request_token=?',(token,)).fetchone():
                flash('This entry was already saved.','info')
                return redirect(url_for('reception_admin.index'))
            if kind=='open':ops.open_shift(db,uid,request.form)
            elif kind=='close':ops.close_shift(db,uid,request.form)
            elif kind=='movement':ops.stock_movement(db,uid,request.form,manager)
            elif kind=='receipt':ops.receipt(db,uid,request.form)
            elif kind=='issue_card':ops.issue_card(db,uid,request.form)
            elif kind=='return_card':ops.return_card(db,uid,request.form)
            elif kind=='cash_move':ops.cash_move(db,uid,request.form)
            elif kind=='lost_card':
                if not manager:raise PermissionError('A manager must record a lost card.')
                ops.lost_card(db,uid,request.form)
            elif kind=='void':
                if not manager:raise PermissionError('A manager must correct a receipt.')
                ops.void_receipt(db,uid,ops.integer(request.form.get('receipt_id')),request.form.get('reference'))
            elif kind=='item':
                if not manager:raise PermissionError('A manager must add stock items.')
                if ops.current_shift(db):raise ValueError('Add catalogue items between shifts so every item has an opening count.')
                name=ops.text(request.form.get('name'),True);category=request.form.get('kind')
                if category not in ('supplies','merchandise'):raise ValueError('Choose supplies or merchandise.')
                code='custom_'+secrets.token_hex(8)
                db.execute('INSERT INTO reception_items(code,name,kind,reorder_level) VALUES(?,?,?,?)',(code,name,category,ops.integer(request.form.get('reorder_level'))));ops.audit(db,uid,'catalogue_item_added',dict(name=name,kind=category))
            else:raise ValueError('Choose a reception action.')
            db.execute('INSERT INTO reception_audit(actor_id,action,payload,created_at,request_token) VALUES(?,?,?,?,?)',(uid,'form_saved',json.dumps(dict(action=kind)),ops.stamp(),token))
        flash('Reception record saved.','success')
    except PermissionError as error:abort(403,description=str(error))
    except (ValueError,sqlite3.IntegrityError) as error:
        flash(str(error) if isinstance(error,ValueError) else 'This record already exists or conflicts with another update. Refresh before trying again.','error')
    return redirect(url_for('reception_admin.index'))

@reception_admin_bp.get('/reports/<int:sid>')
@reception_required
def shift_report(sid):
    try:record=ops.report(get_db(),sid)
    except ValueError:abort(404)
    return render_template('reception_admin/report.html',record=record,categories=ops.CATEGORIES,methods=ops.METHODS,manager=access()[1],token=secrets.token_urlsafe(24))

@reception_admin_bp.post('/reports/<int:sid>/review')
@reception_required
def review(sid):
    if not access()[1]:abort(403)
    db=get_db()
    with ops.transaction(db):
        row=db.execute('SELECT * FROM reception_shifts WHERE id=?',(sid,)).fetchone()
        if not row:abort(404)
        if row['status']!='closed':abort(409,description='Close the shift before manager review.')
        if row['closed_by']==session['user_id']:abort(409,description='Another manager must review your own cash-up.')
        if not row['reviewed_at']:
            db.execute('UPDATE reception_shifts SET reviewed_by=?,reviewed_at=? WHERE id=?',(session['user_id'],ops.stamp(),sid));ops.audit(db,session['user_id'],'shift_reviewed',dict(shift_id=sid))
    return redirect(url_for('reception_admin.shift_report',sid=sid))
