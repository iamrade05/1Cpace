"""Employee self-service and reviewed HR operations."""
from datetime import timedelta, datetime, timezone
import json
import secrets

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for, current_app, g

from .auth import login_required
from .database import get_db
from . import hr_workforce as rules

workforce_bp=Blueprint('workforce',__name__,url_prefix='/hr')


@workforce_bp.before_request
def employee_only():
    if not session.get('user_id'):
        return None  # login_required handles authentication/session tenant validation.
    user=rules.actor(get_db())
    if not user or user['is_platform_user']:
        abort(403)


@workforce_bp.errorhandler(ValueError)
def invalid(error):
    return render_template('hr/workforce_error.html',message=str(error)),400


@workforce_bp.errorhandler(PermissionError)
def forbidden(error):
    return render_template('hr/workforce_error.html',message=str(error)),403


def profile(uid=None):
    db=get_db()
    with rules.transaction(db):
        return rules.ensure_profile(db,uid or session['user_id'])


def people(db):
    return db.execute("SELECT id,full_name,department,employee_number FROM users WHERE active=1 AND COALESCE(is_platform_user,0)=0 AND (username<>'admin' OR COALESCE(employee_number,'')<>'') ORDER BY full_name").fetchall()


def require_admin(db):
    if not rules.is_admin(db):
        abort(403)


def allowed_view(db,uid):
    if uid not in rules.visible_ids(db):
        abort(403)


@workforce_bp.get('/reviews')
@login_required
def reviews():
    db=get_db()
    if not rules.is_admin(db) and not (rules.granted(db,'hr_leave_approve') or rules.granted(db,'hr_timesheet_approve')):
        abort(403)
    start=rules.week_start(rules.iso(request.args.get('week') or (rules.today()-timedelta(days=7)).isoformat()))
    ids=rules.visible_ids(db)
    employees=[p for p in people(db) if p['id'] in ids and p['id']!=session['user_id']]
    weeks={w['user_id']:w for w in db.execute('SELECT * FROM hr_weeks WHERE week_start=?',(start.isoformat(),)).fetchall() if w['user_id'] in ids}
    pending=db.execute("SELECT r.*,u.full_name FROM hr_requests r JOIN users u ON u.id=r.user_id WHERE r.status='submitted' ORDER BY r.start_date").fetchall()
    return render_template('hr/reviews.html',start=start,employees=employees,weeks=weeks,
        pending=[r for r in pending if rules.can_review(db,r['user_id'],'hr_leave_approve')])


@workforce_bp.get('/me')
@login_required
def dashboard():
    db=get_db()
    if rules.service_account(rules.actor(db)):
        return redirect(url_for('workforce.setup'))
    p=profile()
    accounts=[dict(a,**rules.account_balance(db,a)) for a in rules.current_accounts(db,session['user_id'])]
    return render_template('hr/me.html',profile=p,accounts=accounts,admin=rules.is_admin(db),annual_days=rules.annual_entitlement(db,p),
        requests=db.execute('SELECT * FROM hr_requests WHERE user_id=? ORDER BY id DESC LIMIT 30',(session['user_id'],)).fetchall(),
        weeks=db.execute('SELECT * FROM hr_weeks WHERE user_id=? ORDER BY week_start DESC LIMIT 12',(session['user_id'],)).fetchall(),
        shifts=db.execute("SELECT * FROM hr_shifts WHERE user_id=? AND work_date>=? AND status='published' ORDER BY work_date LIMIT 14",(session['user_id'],rules.today().isoformat())).fetchall())


@workforce_bp.route('/people',methods=['GET','POST'])
@login_required
def setup():
    db=get_db(); require_admin(db)
    if request.method=='POST':
        uid=int(request.form.get('user_id','0'))
        with rules.transaction(db):
            p=rules.ensure_profile(db,uid)
            if str(p['version'])!=request.form.get('version'):
                raise ValueError('Employee terms changed. Refresh before saving.')
            start=rules.iso(request.form.get('start_date'))
            if start>rules.today():
                raise ValueError('Active employment start date cannot be in the future.')
            weekdays=sorted({int(v) for v in request.form.getlist('weekdays')})
            if not weekdays or any(d<0 or d>5 for d in weekdays):
                raise ValueError('Select normal working days, Monday through Saturday; Sunday is closed.')
            manager=int(request.form.get('manager_id') or 0) or None
            if manager==uid or (manager and not db.execute('SELECT 1 FROM users WHERE id=? AND active=1 AND COALESCE(is_platform_user,0)=0',(manager,)).fetchone()):
                raise ValueError('Choose another active employee as manager.')
            minutes=int(request.form.get('break_minutes','60'))
            employment_type=request.form.get('employment_type')
            if employment_type not in ('employee','contractor'):
                raise ValueError('Confirm whether this is an employment or contractor arrangement.')
            agreement=request.form.get('break_agreement','').strip()[:1000]
            if minutes not in (30,60) or (minutes==30 and not agreement):
                raise ValueError('A reduced 30-minute lunch requires a recorded written agreement reference.')
            if p['start_date'] and p['start_date']!=start.isoformat() and db.execute('SELECT 1 FROM hr_accounts WHERE user_id=?',(uid,)).fetchone():
                raise ValueError('Employment date has confirmed leave history. Resolve the correction with HR before changing it.')
            if p['weekdays'] and p['weekdays']!=json.dumps(weekdays) and db.execute("SELECT 1 FROM hr_requests WHERE user_id=? AND status IN ('submitted','approved') AND end_date>=?",(uid,rules.today().isoformat())).fetchone():
                raise ValueError('Resolve future leave before changing normal working days.')
            if employment_type!=p['employment_type'] and db.execute('SELECT 1 FROM hr_accounts WHERE user_id=?',(uid,)).fetchone():
                raise ValueError('This person has confirmed leave accounts. Review employment reclassification and entitlements before changing the arrangement.')
            db.execute('UPDATE hr_profiles SET start_date=?,weekdays=?,manager_id=?,break_minutes=?,break_agreement=?,employment_type=?,version=version+1 WHERE user_id=?',
                       (start.isoformat(),json.dumps(weekdays),manager,minutes,agreement,employment_type,uid))
            if p['staff_id']:
                db.execute('UPDATE staff SET start_date=? WHERE id=?',(start.isoformat(),p['staff_id']))
            rules.audit(db,uid,'terms_updated',{'start_date':start.isoformat(),'weekdays':weekdays,'manager_id':manager,'break_minutes':minutes,'agreement':agreement,'employment_type':employment_type})
        flash('Employment terms saved. Leave balances still require verification.','success')
        return redirect(url_for('workforce.setup',user_id=uid))
    all_people=people(db)
    with rules.transaction(db):
        for person in all_people:
            rules.ensure_profile(db,person['id'])
    uid=request.args.get('user_id',type=int) or session['user_id']
    if uid not in [p['id'] for p in all_people]:
        if not all_people:
            raise ValueError('Add named employee accounts in user administration before HR setup.')
        uid=all_people[0]['id']
    p=db.execute('SELECT * FROM hr_profiles WHERE user_id=?',(uid,)).fetchone()
    if not p:
        abort(404)
    return render_template('hr/people_setup.html',people=all_people,profile=p,annual_days=rules.annual_entitlement(db,p),
        weekdays=json.loads(p['weekdays'] or '[]'),today=rules.today().isoformat(),
        accounts=db.execute('SELECT * FROM hr_accounts WHERE user_id=? ORDER BY cycle_start DESC',(uid,)).fetchall(),
        openings=db.execute('''SELECT o.*,u.full_name FROM hr_openings o JOIN users u ON u.id=o.user_id
            WHERE o.status='pending' ORDER BY o.id''').fetchall())


@workforce_bp.post('/opening-balances')
@login_required
def opening_propose():
    db=get_db(); require_admin(db)
    uid=int(request.form.get('user_id','0'))
    with rules.transaction(db):
        p=rules.ensure_profile(db,uid)
        if not p['start_date'] or not p['weekdays'] or p['employment_type']!='employee':
            raise ValueError('Confirm employment terms first.')
        kind=request.form.get('leave_type')
        if kind not in ('annual','sick','family'):
            raise ValueError('Choose annual, sick or family responsibility leave.')
        as_of=rules.iso(request.form.get('as_of'))
        start=rules.iso(p['start_date'])
        if as_of<start or as_of>rules.today():
            raise ValueError('Balance date must fall between employment start and today.')
        first,last=rules.cycle(start,kind,as_of)
        count=len(json.loads(p['weekdays']))
        entitlement={'annual':rules.annual_entitlement(db,p),'sick':count*6,'family':3}[kind]
        available=rules.units(request.form.get('balance'))
        taken=rules.units(request.form.get('taken'))
        if kind=='family' and (as_of<=rules.add_months(start,4) or count<4):
            raise ValueError('Family responsibility requires more than four months employment and at least four days weekly.')
        worked=None
        if kind=='sick' and as_of<rules.add_months(start,6):
            worked=int(request.form.get('days_worked') or -1)
            if worked<0 or worked>(as_of-start).days+1 or available+taken>(worked//26)*100:
                raise ValueError('First-six-month sick leave needs verified days worked and cannot exceed one day per 26 worked.')
        if kind in ('sick','family') and available+taken>entitlement*100:
            raise ValueError('Opening values exceed the basic entitlement. Enhanced contractual policies need a separately reviewed configuration.')
        source=request.form.get('source','').strip()[:2000]
        if not source:
            raise ValueError('Record the source and reconciliation of historical leave.')
        payload=dict(as_of=as_of.isoformat(),cycle_start=first.isoformat(),cycle_end=last.isoformat(),
                     leave_type=kind,balance=available,taken=taken,annual_days=entitlement,source=source,
                     days_worked=worked,profile_version=p['version'])
        db.execute('INSERT INTO hr_openings(user_id,payload,proposed_by,created_at) VALUES (?,?,?,?)',
                   (uid,json.dumps(payload,sort_keys=True),session['user_id'],datetime.now(timezone.utc).isoformat()))
        rules.audit(db,uid,'opening_proposed',payload)
    flash('Opening balance staged. A different authorised HR administrator must confirm it.','success')
    return redirect(url_for('workforce.setup',user_id=uid))


@workforce_bp.post('/opening-balances/<int:oid>/confirm')
@login_required
def opening_confirm(oid):
    db=get_db(); require_admin(db)
    with rules.transaction(db):
        row=db.execute('SELECT * FROM hr_openings WHERE id=?',(oid,)).fetchone()
        if not row or row['status']!='pending':
            raise ValueError('Opening proposal is not pending.')
        if row['proposed_by']==session['user_id']:
            raise PermissionError('A different HR administrator must verify this opening balance.')
        p=json.loads(row['payload'])
        if p.get('mode')=='adjustment':
            account=db.execute('SELECT * FROM hr_accounts WHERE id=? AND user_id=?',(p['account_id'],row['user_id'])).fetchone()
            if not account:
                raise ValueError('Leave account no longer exists.')
            if rules.account_balance(db,account)['available']+p['delta']<0:
                raise ValueError('This correction would overspend the balance. Reconcile future reservations first.')
            terms=db.execute('SELECT start_date FROM hr_profiles WHERE user_id=?',(row['user_id'],)).fetchone()
            if account['leave_type']=='sick' and p['delta']>0 and rules.today()<rules.add_months(rules.iso(terms['start_date']),6):
                worked=int(p.get('days_worked') or -1)
                if worked<0 or rules.account_balance(db,account)['credits']+p['delta']>(worked//26)*100:
                    raise ValueError('Another credit changed the first-six-month sick balance. Reconcile verified days worked before confirming.')
            db.execute('INSERT INTO hr_ledger(account_id,units,kind,reference,effective_date,actor_id,reason) VALUES (?,?,?,?,?,?,?)',
                       (account['id'],p['delta'],'adjustment',f'adjustment:{oid}',rules.today().isoformat(),session['user_id'],p['source']))
            db.execute("UPDATE hr_openings SET status='confirmed',decided_by=? WHERE id=?",(session['user_id'],oid))
            rules.audit(db,account['id'],'balance_adjusted',{'proposal_id':oid,'delta':p['delta'],'source':p['source']})
            flash('Reviewed balance adjustment posted; original history preserved.','success')
            return redirect(url_for('workforce.setup',user_id=row['user_id']))
        terms=db.execute('SELECT version FROM hr_profiles WHERE user_id=?',(row['user_id'],)).fetchone()
        if terms['version']!=p['profile_version']:
            raise ValueError('Employment terms changed. Submit a new opening proposal.')
        if p['leave_type']=='annual':
            profile=db.execute('SELECT * FROM hr_profiles WHERE user_id=?',(row['user_id'],)).fetchone()
            if p['annual_days']!=rules.annual_entitlement(db,profile):
                raise ValueError('Annual entitlement changed. Submit a new opening proposal under the current company policy.')
        if db.execute('SELECT 1 FROM hr_accounts WHERE user_id=? AND leave_type=? AND cycle_start=?',(row['user_id'],p['leave_type'],p['cycle_start'])).fetchone():
            raise ValueError('This cycle already has a confirmed account. Do not import it twice.')
        aid=db.execute('''INSERT INTO hr_accounts(user_id,leave_type,cycle_start,cycle_end,as_of,annual_days,confirmed_by,policy)
                         VALUES (?,?,?,?,?,?,?,?)''',(row['user_id'],p['leave_type'],p['cycle_start'],p['cycle_end'],p['as_of'],p['annual_days'],session['user_id'],rules.POLICY)).lastrowid
        for kind,value in [('opening',p['balance']+p['taken']),('taken',-p['taken'])]:
            db.execute('INSERT INTO hr_ledger(account_id,units,kind,reference,effective_date,actor_id,reason) VALUES (?,?,?,?,?,?,?)',
                       (aid,value,kind,f'opening:{oid}:{kind}',p['as_of'],session['user_id'],p['source']))
        db.execute("UPDATE hr_openings SET status='confirmed',decided_by=? WHERE id=?",(session['user_id'],oid))
        rules.audit(db,aid,'opening_confirmed',{'proposal_id':oid,'user_id':row['user_id']})
    flash('Opening balance confirmed.','success')
    return redirect(url_for('workforce.setup',user_id=row['user_id']))


@workforce_bp.post('/balance-adjustments')
@login_required
def adjustment_propose():
    db=get_db(); require_admin(db)
    with rules.transaction(db):
        account=db.execute('SELECT * FROM hr_accounts WHERE id=?',(request.form.get('account_id'),)).fetchone()
        if not account:
            raise ValueError('Choose a confirmed leave account.')
        delta=rules.units(request.form.get('days'))
        direction=request.form.get('direction')
        if direction not in ('credit','debit') or not delta:
            raise ValueError('Choose credit or debit and a non-zero quantity.')
        delta=delta if direction=='credit' else -delta
        source=request.form.get('source','').strip()[:2000]
        if not source:
            raise ValueError('Record verified evidence and the correction reason.')
        if account['leave_type']=='sick' and delta>0:
            profile=db.execute('SELECT * FROM hr_profiles WHERE user_id=?',(account['user_id'],)).fetchone()
            start=rules.iso(profile['start_date'])
            if rules.today()<rules.add_months(start,6):
                worked=int(request.form.get('days_worked') or -1)
                credits=rules.account_balance(db,account)['credits']
                if worked<0 or worked>(rules.today()-start).days+1 or credits+delta>(worked//26)*100:
                    raise ValueError('First-six-month sick credits require verified days worked: one day per 26 worked.')
        payload=dict(mode='adjustment',account_id=account['id'],delta=delta,source=source,
                     days_worked=request.form.get('days_worked',''))
        db.execute('INSERT INTO hr_openings(user_id,payload,proposed_by,created_at) VALUES (?,?,?,?)',
            (account['user_id'],json.dumps(payload,sort_keys=True),session['user_id'],datetime.now(timezone.utc).isoformat()))
        rules.audit(db,account['id'],'balance_adjustment_proposed',payload)
    flash('Adjustment staged for another HR administrator to verify.','success')
    return redirect(url_for('workforce.setup',user_id=account['user_id']))


@workforce_bp.get('/balances')
@login_required
def balances():
    db=get_db(); uid=request.args.get('user_id',type=int) or session['user_id']; allowed_view(db,uid)
    accounts=[dict(a,**rules.account_balance(db,a)) for a in rules.current_accounts(db,uid)]
    ledger=db.execute('''SELECT l.*,a.leave_type FROM hr_ledger l JOIN hr_accounts a ON a.id=l.account_id
        WHERE a.user_id=? ORDER BY l.effective_date DESC,l.id DESC''',(uid,)).fetchall()
    return render_template('hr/balances.html',accounts=accounts,ledger=ledger)


@workforce_bp.get('/leave/calendar')
@login_required
def calendar():
    db=get_db(); start=rules.week_start(rules.iso(request.args.get('week') or rules.today().isoformat())); end=start+timedelta(days=6)
    # Availability only: deliberately omit reasons, signatures and medical details.
    if not rules.is_admin(db) and not (rules.granted(db,'hr_leave_approve') or rules.granted(db,'hr_timesheet_approve')):
        abort(403)
    return render_template('hr/availability.html',people=people(db),start=start,end=end,
        days=[start+timedelta(days=i) for i in range(7)],
        leaves=db.execute("SELECT user_id,start_date,end_date,status FROM hr_requests WHERE status IN ('submitted','approved') AND start_date<=? AND end_date>=?",(end.isoformat(),start.isoformat())).fetchall(),
        shifts=db.execute("SELECT * FROM hr_shifts WHERE work_date BETWEEN ? AND ? AND status='published'",(start.isoformat(),end.isoformat())).fetchall())


@login_required
def leave_list():
    db=get_db(); ids=rules.visible_ids(db)
    rows=db.execute('SELECT r.*,u.full_name,u.department FROM hr_requests r JOIN users u ON u.id=r.user_id WHERE r.user_id IN ('+','.join('?' for _ in ids)+') ORDER BY r.id DESC',ids).fetchall()
    return render_template('hr/workforce_leave.html',leaves=rows,
        can_review=lambda uid:rules.can_review(db,uid,'hr_leave_approve'),
        conflicts={r['id']:rules.leave_conflicts(db,r) for r in rows},
        uncovered={r['id']:rules.uncovered_leave(db,r) for r in rows if r['status']=='submitted'},
        balances={r['id']:next((rules.account_balance(db,a)['available']/100 for a in rules.current_accounts(db,r['user_id'])
                   if a['leave_type']==r['leave_type'] and a['cycle_start']<=r['start_date'] and a['cycle_end']>=r['end_date']),None) for r in rows},
        legacy=db.execute('''SELECT DISTINCT l.*,u.full_name FROM leave_requests l
            LEFT JOIN hr_profiles p ON p.staff_id=l.staff_id LEFT JOIN users u ON u.id=COALESCE(l.user_id,p.user_id)
            WHERE COALESCE(l.user_id,p.user_id) IN ('''+','.join('?' for _ in ids)+') ORDER BY l.id DESC',ids).fetchall(),
        can_calendar=rules.is_admin(db) or rules.granted(db,'hr_leave_approve') or rules.granted(db,'hr_timesheet_approve'))


@login_required
def request_leave():
    db=get_db()
    if rules.service_account(rules.actor(db)):
        return redirect(url_for('workforce.setup'))
    profile()
    if request.method=='POST':
        try:
            with rules.transaction(db):
                rules.submit_leave(db,session['user_id'],request.form)
        except ValueError as error:
            flash(str(error),'error')
        else:
            flash('Leave request signed and submitted.','success')
            return redirect(url_for('hr.leave_index'))
    return render_template('hr/leave_request.html',leave_types=rules.LEAVE_TYPES,token=secrets.token_urlsafe(24))


@login_required
def leave_decision(lid,action):
    db=get_db()
    try:
        with rules.transaction(db):
            rules.decide_leave(db,lid,action,request.form)
    except ValueError as error:
        flash(str(error),'error')
    except PermissionError:
        abort(403)
    else:
        flash('Leave decision recorded.','success')
    return redirect(url_for('hr.leave_index'))


@workforce_bp.post('/leave/<int:lid>/withdraw')
@login_required
def withdraw(lid):
    db=get_db()
    with rules.transaction(db):
        row=db.execute('SELECT * FROM hr_requests WHERE id=?',(lid,)).fetchone()
        if not row:
            abort(404)
        if str(row['version'])!=request.form.get('version'):
            raise ValueError('Request changed. Refresh first.')
        own=row['user_id']==session['user_id']
        if row['status']=='submitted' and own:
            state='withdrawn'
        elif row['status']=='approved' and rules.is_admin(db) and not own and row['start_date']>rules.today().isoformat() and request.form.get('note','').strip():
            state='cancelled'
        else:
            raise PermissionError('Only your pending requests can be withdrawn. Approved future leave requires another HR administrator to cancel with a reason.')
        db.execute('UPDATE hr_requests SET status=?,version=version+1 WHERE id=?',(state,lid))
        db.execute("UPDATE staff_notifications SET resolved_at=? WHERE source_kind='hr_leave' AND source_id=? AND resolved_at IS NULL",(datetime.now(timezone.utc).isoformat(),lid))
        rules.audit(db,lid,'leave_'+state,{'note':request.form.get('note','')[:2000]})
        if state=='cancelled':
            rules.notify(db,row['user_id'],'hr_leave',lid,'Your approved future leave was cancelled by HR.',row['version']+1)
    return redirect(url_for('hr.leave_index'))


@workforce_bp.route('/weeks',methods=['GET','POST'])
@login_required
def weeks():
    db=get_db()
    uid=request.args.get('user_id',type=int) or session['user_id']; allowed_view(db,uid)
    if uid==session['user_id'] and rules.service_account(rules.actor(db)):
        return redirect(url_for('workforce.reviews'))
    profile(uid)
    start=rules.week_start(rules.iso(request.args.get('week') or rules.today().isoformat()))
    if request.method=='POST':
        if uid!=session['user_id']:
            abort(403)
        with rules.transaction(db):
            start=rules.save_week(db,uid,request.form)
        flash('Weekly timesheet saved.','success')
        return redirect(url_for('workforce.weeks',week=start.isoformat()))
    row=db.execute('SELECT * FROM hr_weeks WHERE user_id=? AND week_start=?',(uid,start.isoformat())).fetchone()
    entries={e['date']:e for e in json.loads(row['entries'])} if row else {}
    legacy=False
    if not row:
        # Existing manual records remain drafts until the employee reviews/signs.
        p=db.execute('SELECT staff_id FROM hr_profiles WHERE user_id=?',(uid,)).fetchone()
        if p and p['staff_id']:
            old=db.execute('SELECT * FROM timesheets WHERE staff_id=? AND work_date BETWEEN ? AND ? ORDER BY id',
                           (p['staff_id'],start.isoformat(),(start+timedelta(days=6)).isoformat())).fetchall()
            for entry in old:
                e={key:entry[key] or '' for key in ('time_in','lunch_out','lunch_in','time_out','notes')}
                e['date']=entry['work_date']
                try:
                    e['minutes']=rules.interval(e['time_in'],e['lunch_out'],e['lunch_in'],e['time_out'])
                except ValueError:
                    e['minutes']=0
                    e['notes']+=' [Legacy interval needs correction: '+str({k:e[k] for k in ('time_in','time_out','lunch_out','lunch_in')})+']'
                    for key in ('time_in','time_out','lunch_out','lunch_in'):
                        e[key]=''
                entries[e['date']]=e
            legacy=bool(old)
    signatures=db.execute('SELECT s.*,u.full_name FROM hr_signatures s JOIN users u ON u.id=s.actor_id WHERE week_id=? ORDER BY s.id',(row['id'],)).fetchall() if row else []
    return render_template('hr/workforce_week.html',row=row,entries=entries,signatures=signatures,
        days=[start+timedelta(days=i) for i in range(7)],start=start,
        uid=uid,total_minutes=sum(e['minutes'] for e in entries.values()),own=uid==session['user_id'],editable=(not row or row['status'] in ('draft','returned')) and uid==session['user_id'],
        declaration=rules.DECLARATION,warnings=rules.week_warnings(list(entries.values())),legacy=legacy,
        evidence=attendance_evidence(uid,start),
        can_review=rules.can_review(db,uid,'hr_timesheet_approve'),admin=rules.is_admin(db),
        people=[p for p in people(db) if p['id'] in rules.visible_ids(db)])


@workforce_bp.post('/weeks/<int:wid>/decision')
@login_required
def week_decision(wid):
    db=get_db()
    with rules.transaction(db):
        rules.decide_week(db,wid,request.form)
    row=db.execute('SELECT user_id,week_start FROM hr_weeks WHERE id=?',(wid,)).fetchone()
    return redirect(url_for('workforce.weeks',user_id=row['user_id'],week=row['week_start']))


def attendance_evidence(uid,start):
    from pathlib import Path
    from .staff_attendance import build_week
    allowed=current_app.config.get('ACCESS_REPORT_TENANT_SLUG','elev8')
    tenant=g.get('tenant_slug') or dict(g.get('tenant') or {}).get('slug')
    if tenant and tenant!=allowed:
        return None
    path=Path(current_app.config.get('STAFF_ACCESS_SNAPSHOT') or Path(current_app.config['UPLOAD_FOLDER']).parent/'staff-access'/'current.json')
    archive=path.parent/(start.isoformat()+'.json')
    if not current_app.config.get('STAFF_ACCESS_SNAPSHOT') and archive.is_file():
        path=archive
    try:
        snapshot=json.loads(path.read_text(encoding='utf-8'))
        if snapshot.get('tenant_slug')!=allowed:
            return None
        report=build_week(snapshot,start)
        employee=next((e for e in report['employees'] if e['id']==uid),None)
        return dict(employee=employee,source_latest_date=snapshot.get('source_latest_date'),captured_at=snapshot.get('captured_at')) if employee else None
    except (OSError,ValueError,KeyError,TypeError):
        return None


@workforce_bp.route('/roster',methods=['GET','POST'])
@login_required
def roster():
    db=get_db()
    if request.method=='POST':
        require_admin(db)
        with rules.transaction(db):
            save_shift(db,request.form,validate=False)
        flash('Draft shift saved. Publish the complete week after arranging cover.','success')
        return redirect(url_for('workforce.roster',week=request.form['work_date']))
    start=rules.week_start(rules.iso(request.args.get('week') or rules.today().isoformat()))
    return render_template('hr/roster.html',people=people(db),admin=rules.is_admin(db),start=start,
        rows=db.execute('''SELECT s.*,u.full_name,r.full_name AS relief_name FROM hr_shifts s JOIN users u ON u.id=s.user_id
            LEFT JOIN users r ON r.id=s.relief_id WHERE work_date BETWEEN ? AND ? AND (s.status='published' OR ?=1)
            ORDER BY work_date,time_in,u.full_name''',
            (start.isoformat(),(start+timedelta(days=6)).isoformat(),int(rules.is_admin(db)))).fetchall())


@workforce_bp.post('/roster/publish')
@login_required
def publish_roster():
    db=get_db(); require_admin(db)
    start=rules.week_start(rules.iso(request.form.get('week'))); end=start+timedelta(days=6)
    with rules.transaction(db):
        shifts=db.execute('SELECT * FROM hr_shifts WHERE work_date BETWEEN ? AND ?',(start.isoformat(),end.isoformat())).fetchall()
        if not shifts:
            raise ValueError('Add draft shifts before publication.')
        # All candidates are visible to the validator; commit only if every shift passes.
        for shift in shifts:
            save_shift(db,dict(shift,shift_id=str(shift['id'])),validate=True)
        db.execute("UPDATE hr_shifts SET status='published' WHERE work_date BETWEEN ? AND ?",(start.isoformat(),end.isoformat()))
        rules.audit(db,start.isoformat(),'roster_published',{'shift_ids':[s['id'] for s in shifts]})
    flash('Roster and lunch coverage validated and published.','success')
    return redirect(url_for('workforce.roster',week=start.isoformat()))


@workforce_bp.post('/roster/<int:sid>/delete')
@login_required
def delete_draft_shift(sid):
    db=get_db(); require_admin(db)
    with rules.transaction(db):
        row=db.execute("SELECT * FROM hr_shifts WHERE id=? AND status='draft'",(sid,)).fetchone()
        if not row:
            raise ValueError('Only an unpublished draft shift can be removed.')
        db.execute('DELETE FROM hr_shifts WHERE id=?',(sid,))
        rules.audit(db,sid,'draft_shift_removed',{'date':row['work_date']})
    return redirect(url_for('workforce.roster',week=row['work_date']))


@workforce_bp.post('/roster/revise')
@login_required
def revise_roster():
    db=get_db(); require_admin(db)
    start=rules.week_start(rules.iso(request.form.get('week'))); end=start+timedelta(days=6)
    note=request.form.get('note','').strip()[:2000]
    if start<=rules.today() or not note:
        raise ValueError('Only a future week can return to draft. Record the reason; past schedules are retained.')
    with rules.transaction(db):
        rows=db.execute("SELECT * FROM hr_shifts WHERE work_date BETWEEN ? AND ? AND status='published'",(start.isoformat(),end.isoformat())).fetchall()
        if not rows:
            raise ValueError('No published roster to revise.')
        rules.audit(db,start.isoformat(),'roster_returned_to_draft',{'reason':note,'previous':[dict(r) for r in rows]})
        db.execute("UPDATE hr_shifts SET status='draft' WHERE work_date BETWEEN ? AND ?",(start.isoformat(),end.isoformat()))
    flash('Future roster returned to draft. Republish after correcting all shifts and cover.','success')
    return redirect(url_for('workforce.roster',week=start.isoformat()))


def save_shift(db,form,validate=True):
    uid=int(form.get('user_id','0')); p=rules.ensure_profile(db,uid)
    day=rules.iso(form.get('work_date'))
    if day.weekday()==6:
        raise ValueError('The gym is closed on Sundays.')
    user=db.execute('SELECT department FROM users WHERE id=?',(uid,)).fetchone()
    department=user['department'] or 'General'
    ti,lo,li,to=[form.get(k,'') for k in ('time_in','lunch_out','lunch_in','time_out')]
    rules.interval(ti,lo,li,to)
    a,b,c,d=[rules.minute(v) for v in (ti,lo,li,to)]
    if c-b<p['break_minutes'] or b-a>300 or d-c>300:
        raise ValueError('Provide the agreed meal duration without more than five continuous working hours.')
    special=day.weekday()==5 or day in rules.holidays(day.year)
    opening,closing=(360,840) if special else (300,1200 if day.weekday()==4 else 1260)
    if a<opening or d>closing:
        raise ValueError('Shift falls outside the confirmed gym opening hours.')
    if validate:
        if not p['start_date'] or not p['weekdays']:
            raise ValueError('Confirm employment terms before publishing shifts.')
        first=rules.week_start(day)
        weekly=db.execute('SELECT * FROM hr_shifts WHERE user_id=? AND work_date BETWEEN ? AND ?',
                          (uid,first.isoformat(),(first+timedelta(days=6)).isoformat())).fetchall()
        total=sum(rules.interval(s['time_in'],s['lunch_out'],s['lunch_in'],s['time_out']) for s in weekly)
        if total>2700:
            raise ValueError('Roster exceeds 45 ordinary hours. Review the schedule and overtime arrangements before publication.')
        daily_limit=480 if len({s['work_date'] for s in weekly})>5 else 540
        if rules.interval(ti,lo,li,to)>daily_limit:
            raise ValueError('Shift exceeds the normal daily ordinary-hours limit for this working week. Review work and overtime arrangements.')
    relief=int(form.get('relief_id') or 0) or None
    existing=db.execute('SELECT * FROM hr_shifts WHERE user_id=? AND work_date=?',(uid,day.isoformat())).fetchone()
    if not validate:
        if existing:
            raise ValueError('A shift already exists for this person and date. Remove an unpublished draft before replacing it.')
        db.execute('INSERT INTO hr_shifts(user_id,work_date,department,time_in,time_out,lunch_out,lunch_in,relief_id,created_by) VALUES (?,?,?,?,?,?,?,?,?)',
                   (uid,day.isoformat(),department,ti,to,lo,li,relief,session['user_id']))
        rules.audit(db,uid,'shift_drafted',{'date':day.isoformat(),'times':[ti,lo,li,to],'relief_id':relief})
        return
    if db.execute("SELECT 1 FROM hr_requests WHERE user_id=? AND status='approved' AND start_date<=? AND end_date>=?",(uid,day.isoformat(),day.isoformat())).fetchone():
        raise ValueError('Employee is on approved leave on this date.')
    shifts=db.execute('SELECT * FROM hr_shifts WHERE work_date=? AND user_id<>?',(day.isoformat(),uid)).fetchall()
    shifts=[s for s in shifts if not db.execute("SELECT 1 FROM hr_requests WHERE user_id=? AND status='approved' AND start_date<=? AND end_date>=?",(s['user_id'],day.isoformat(),day.isoformat())).fetchone()]
    for other in shifts:
        if rules.department_key(other['department'])==rules.department_key(department) and b<rules.minute(other['lunch_in']) and c>rules.minute(other['lunch_out']):
            raise ValueError('Another employee in this department has overlapping lunch.')
    cover=[s for s in shifts if rules.department_key(s['department'])==rules.department_key(department)
           and rules.minute(s['time_in'])<=b and rules.minute(s['time_out'])>=c
           and (rules.minute(s['lunch_in'])<=b or rules.minute(s['lunch_out'])>=c)]
    if not cover and rules.department_key(department) not in ('management','general'):
        replacement=next((s for s in shifts if s['user_id']==relief),None)
        if not replacement or relief==uid or rules.minute(replacement['time_in'])>b or rules.minute(replacement['time_out'])<c or (b<rules.minute(replacement['lunch_in']) and c>rules.minute(replacement['lunch_out'])):
            raise ValueError('This single-person department needs a rostered, available relief person. Roster relief first.')
        if db.execute('''SELECT 1 FROM hr_shifts WHERE work_date=? AND relief_id=? AND user_id<>?
            AND lunch_out<? AND lunch_in>?''',(day.isoformat(),relief,uid,li,lo)).fetchone():
            raise ValueError('Relief employee is already covering another lunch.')
        if rules.department_key(replacement['department']) not in ('management','general'):
            own_cover=[s for s in shifts if s['user_id']!=relief and rules.department_key(s['department'])==rules.department_key(replacement['department'])
                and rules.minute(s['time_in'])<=b and rules.minute(s['time_out'])>=c
                and (rules.minute(s['lunch_in'])<=b or rules.minute(s['lunch_out'])>=c)]
            if not own_cover:
                raise ValueError('Relief would leave their own department uncovered. Assign a floating manager or additional department cover.')
    adjacent=db.execute('SELECT * FROM hr_shifts WHERE user_id=? AND work_date IN (?,?)',
        (uid,(day-timedelta(days=1)).isoformat(),(day+timedelta(days=1)).isoformat())).fetchall()
    for s in adjacent:
        gap=1440-rules.minute(s['time_out'])+a if s['work_date']<day.isoformat() else 1440-d+rules.minute(s['time_in'])
        if gap<720:
            raise ValueError('Less than 12 hours daily rest. Revise the roster; exceptional arrangements require a reviewed policy.')
    # Validation is read-only; publication changes the complete week atomically.
    return
