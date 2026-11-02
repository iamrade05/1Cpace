"""Tenant-bound signed query forms and audited staff handoffs.

Submissions and evidence are encrypted and retained atomically in the tenant DB.
No transition in this module submits a mandate or makes a bank payment.
"""
import base64
import hashlib
import io
import json
import math
import secrets
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import calendar

from flask import abort, current_app, g, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename
from .auth import permission_required
from .query_funnel import local_today
from .database import get_db, member_activity
from .encryption import encrypt, decrypt, decrypt_member
from .queries import queries_bp, QUERY_PERMISSION

NOTICE = ('Submitting this form does not mean that your application has been approved. '
          'Applications take 5 to 10 days to be fully processed. You will be notified of the outcome.')
KINDS = {'refund','freeze','complaint','cancellation','banking','proof_of_payment'}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(db, backend='sqlite'):
    identity = 'SERIAL PRIMARY KEY' if backend=='postgresql' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    for sql in [
        '''CREATE TABLE IF NOT EXISTS query_workflow_settings (
            id INTEGER PRIMARY KEY, approver_id INTEGER NOT NULL REFERENCES users(id),
            refund_processor_id INTEGER NOT NULL REFERENCES users(id))''',
        '''CREATE TABLE IF NOT EXISTS query_workflows (
            query_id INTEGER PRIMARY KEY REFERENCES queries(id), kind TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT 'intake', owner_id INTEGER, details TEXT NOT NULL,
            updated_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS query_form_links (
            token_hash TEXT PRIMARY KEY, query_id INTEGER NOT NULL REFERENCES queries(id),
            expires_at TEXT NOT NULL, revoked INTEGER DEFAULT 0, submitted_id INTEGER,
            created_by INTEGER NOT NULL, created_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS query_form_submissions (
            id {identity}, query_id INTEGER NOT NULL REFERENCES queries(id),
            token_hash TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, signature TEXT NOT NULL,
            snapshot TEXT NOT NULL, notice_version TEXT NOT NULL, submitted_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS query_form_documents (
            id {identity}, query_id INTEGER NOT NULL REFERENCES queries(id),
            submission_id INTEGER NOT NULL REFERENCES query_form_submissions(id),
            document_id INTEGER NOT NULL UNIQUE REFERENCES member_documents(id),
            media_type TEXT NOT NULL, content TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS query_workflow_events (
            id {identity}, query_id INTEGER NOT NULL REFERENCES queries(id), actor_id INTEGER,
            action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL)''',
    ]:
        db.execute(sql)
    from .query_funnel import ensure_schema as ensure_funnel
    ensure_funnel(db, backend)


def kind_for(qry):
    if qry['category'] in KINDS:
        return qry['category']
    if qry['category']=='payments' and qry['query_type']=='Proof of payment':
        return 'proof_of_payment'
    return None


def event(db,qid,action,details):
    db.execute('INSERT INTO query_workflow_events(query_id,actor_id,action,details,created_at) VALUES(?,?,?,?,?)',
               (qid,None if request.endpoint=='queries.member_form' else session.get('user_id'),action,encrypt(json.dumps(details)),stamp()))


def load_case(db,qid):
    qry=db.execute('SELECT * FROM queries WHERE id=?',(qid,)).fetchone()
    if not qry or not qry['member_id'] or not kind_for(qry):abort(404)
    row=db.execute('SELECT * FROM query_workflows WHERE query_id=?',(qid,)).fetchone()
    return qry,dict(row) if row else dict(query_id=qid,kind=kind_for(qry),stage='intake',owner_id=qry['assigned_to'],details=encrypt('{}'))


def save_case(db,qry,case,stage,details,owner=None):
    transition_at=stamp() if case['stage']!=stage or case['owner_id']!=owner else case.get('updated_at') or stamp()
    db.execute('''INSERT INTO query_workflows(query_id,kind,stage,owner_id,details,updated_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(query_id) DO UPDATE SET stage=excluded.stage,
        owner_id=excluded.owner_id,details=excluded.details,updated_at=excluded.updated_at''',
        (qry['id'],case['kind'],stage,owner,encrypt(json.dumps(details)),transition_at))
    db.execute('UPDATE queries SET assigned_to=? WHERE id=?',(owner,qry['id']))
    if owner and stage not in ('intake','awaiting_member','awaiting_settlement','awaiting_information') and (case['stage']!=stage or case['owner_id']!=owner):
        from .query_funnel import queue_owner
        queue_owner(db,qry,owner,stage)
    if stage in ('pending_approval','management_review','manager_cancel_debit','cancelled_awaiting_mandate_review','awaiting_payment'):
        db.execute("UPDATE queries SET department='Management' WHERE id=?",(qry['id'],))
    elif stage in ('verification','settlement_verification','awaiting_settlement','awaiting_information'):
        db.execute("UPDATE queries SET department='Reception' WHERE id=?",(qry['id'],))
    if stage=='manager_cancel_debit':
        handed=stamp()
        details['manager_handoff_at']=handed
        db.execute('UPDATE query_workflows SET details=? WHERE query_id=?',(encrypt(json.dumps(details)),qry['id']))
        due=(datetime.fromisoformat(handed).astimezone(ZoneInfo('Africa/Johannesburg')).date()+timedelta(days=7)).isoformat()
        db.execute('UPDATE queries SET due_date=?,follow_up_date=? WHERE id=?',(due,due,qry['id']))
    if stage in ('complete','rejected'):
        if stage=='rejected':db.execute('DELETE FROM query_refund_claims WHERE query_id=?',(qry['id'],))
        # Keep the original ticket and the signed funnel in one terminal state.
        completion=details.get('completion') or details.get('decision') or {}
        notes=completion.get('notes') or 'Signed query workflow completed.'
        db.execute("""UPDATE queries SET status='closed',closed_at=?,closed_by=?,resolution_notes=?,
            closure_reason='resolved',updated_at=?,approval_status=CASE WHEN approval_status='pending' THEN ? ELSE approval_status END
            WHERE id=?""",(stamp(),completion.get('by'),notes,stamp(),'rejected' if stage=='rejected' else 'approved',qry['id']))
        from .query_funnel import queue_client
        message=f"Your {case['kind'].replace('_',' ')} request has been {'declined' if stage=='rejected' else 'completed'}."
        if case['kind']=='freeze' and completion.get('start_date'):
            message+=f"\nApproved freeze: {completion['start_date']} to {completion['end_date']}."
        if case['kind']=='refund' and details.get('payment'):
            message+=f"\nRefund recorded: R{details['payment']['amount']} on {details['payment']['date']}."
        queue_client(db,qry,'outcome:'+stage+':'+stamp(),message+'\nReception will confirm the outcome with you.')


def staff_id(db,key,manager=False):
    value=current_app.config.get(key)
    if not value:
        setting=db.execute('SELECT * FROM query_workflow_settings WHERE id=1').fetchone()
        if setting:value=setting['approver_id' if key=='QUERY_APPROVER_USER_ID' else 'refund_processor_id']
    if not value:
        from .query_routing import enabled,staff
        if enabled():
            matched=staff(db,'mvuleni' if key=='QUERY_APPROVER_USER_ID' else 'ayrton')
            if matched:value=matched['id']
    row=db.execute('SELECT * FROM users WHERE id=? AND active=1',(value,)).fetchone() if value else None
    if row and (not manager or row['role'] in ('admin','manager')):return row['id']
    return None


def reception_owner(qry,details):
    return details.get('reception_owner_id') or qry['logged_by']


def reviewer_id(db,kind):
    if kind=='refund':
        processor=staff_id(db,'QUERY_REFUND_PROCESSOR_USER_ID')
        person=db.execute('SELECT role FROM users WHERE id=?',(processor,)).fetchone() if processor else None
        if person and person['role'] in ('admin','manager'):return processor
    return staff_id(db,'QUERY_APPROVER_USER_ID',True)


@queries_bp.route('/workflow-setup',methods=['GET','POST'])
@permission_required(QUERY_PERMISSION)
def workflow_setup():
    if session.get('role')!='admin':abort(403)
    db=get_db()
    if request.method=='POST':
        approver=db.execute("SELECT id FROM users WHERE id=? AND active=1 AND role IN ('admin','manager')",(request.form.get('approver_id'),)).fetchone()
        processor=db.execute('SELECT id FROM users WHERE id=? AND active=1',(request.form.get('processor_id'),)).fetchone()
        if not approver or not processor or approver['id']==processor['id']:abort(400,description='Choose separate active approving-manager and refund-processor accounts.')
        db.execute('''INSERT INTO query_workflow_settings(id,approver_id,refund_processor_id) VALUES(1,?,?)
            ON CONFLICT(id) DO UPDATE SET approver_id=excluded.approver_id,refund_processor_id=excluded.refund_processor_id''',
            (approver['id'],processor['id']))
        from .database import audit_log
        audit_log(db,'query_workflow_setup',new_value=f"approver={approver['id']}; processor={processor['id']}")
        db.commit()
        return redirect(url_for('queries.workflow_setup'))
    return render_template('queries/workflow_setup.html',staff=db.execute('SELECT id,full_name,username,role FROM users WHERE active=1 ORDER BY full_name').fetchall(),
        setting=dict(approver_id=staff_id(db,'QUERY_APPROVER_USER_ID',True),refund_processor_id=staff_id(db,'QUERY_REFUND_PROCESSOR_USER_ID')))


def valid_signature(raw):
    try:
        strokes=json.loads(raw)
        if not isinstance(strokes,list) or not 1<=len(strokes)<=50:return False
        total=0
        for line in strokes:
            if not isinstance(line,list) or len(line)<2:return False
            total+=len(line)
            for point in line:
                if not isinstance(point,list) or len(point)!=2:return False
                if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in point):return False
                if not (0<=point[0]<=600 and 0<=point[1]<=180):return False
        return 2<=total<=3000
    except (ValueError,TypeError):return False


def amount(value):
    try:
        result=Decimal(str(value)).quantize(Decimal('.01'))
        if not result.is_finite() or result<=0:raise ValueError()
        return str(result)
    except (InvalidOperation,ValueError,TypeError):abort(400,description='Enter a positive amount.')


def freeze_timing(submitted_at,due,next_due):
    received=datetime.fromisoformat(submitted_at).astimezone(ZoneInfo('Africa/Johannesburg')).date()
    due,next_due=date.fromisoformat(due),date.fromisoformat(next_due)
    if next_due<=due:raise ValueError('The next actual payment date must follow the due date.')
    cutoff=due-timedelta(days=14)
    return dict(received=received.isoformat(),due=due.isoformat(),cutoff=cutoff.isoformat(),
                effective_cycle=(due if received<=cutoff else next_due).isoformat(),late=received>cutoff)


def member_fields(kind,form):
    fields={'details_correct':form.get('details_correct'),'correction':form.get('correction','').strip()}
    if fields['details_correct'] not in ('yes','no') or (fields['details_correct']=='no' and not fields['correction']):
        abort(400,description='Confirm your details or provide a correction.')
    if kind=='refund':
        reason=form.get('reason')
        if reason not in ('incorrect_debit','double_debit','after_cancellation','after_contract_end','reception_and_debit','other') or form.get('occurrences') not in ('1','2'):
            abort(400,description='Select the refund reason and occurrence count.')
        fields.update(reason=reason,occurrences=form['occurrences'])
    elif kind=='freeze':
        if form.get('reason') not in ('exams','employment','study_travel','medical','holiday','financial','other'):abort(400,description='Select a supported freeze reason.')
        try:
            start,end=date.fromisoformat(form['start_date']),date.fromisoformat(form['end_date'])
            if end<start:raise ValueError()
        except (ValueError,KeyError):abort(400,description='Enter valid requested freeze dates.')
        fields.update(reason=form['reason'],start_date=start.isoformat(),end_date=end.isoformat())
    elif kind=='complaint':
        if not form.get('description','').strip():abort(400,description='Describe what happened.')
        fields['description']=form['description'].strip()
    elif kind=='banking':
        for key in ('account_name','bank','account_type','account_number','branch_code'):
            value=form.get(key,'').strip()
            if not value:abort(400,description='Complete the banking details.')
            fields[key]=value
    elif kind=='proof_of_payment':
        fields['payment_reference']=form.get('payment_reference','').strip()
    elif kind=='cancellation':
        if form.get('payment_option') not in ('cash','instalments'):
            abort(400,description='Choose cash settlement or six monthly instalments.')
        fields['payment_option']=form['payment_option']
    if any(len(str(v))>4000 for v in fields.values()):abort(400)
    return fields


def uploads():
    result=[]
    files=[f for f in request.files.getlist('evidence') if f.filename]
    if len(files)>5:abort(400,description='Upload at most five documents.')
    for file in files:
        data=file.read(5*1024*1024+1)
        media='application/pdf' if data.startswith(b'%PDF-') else 'image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else None
        if not media or len(data)>5*1024*1024:abort(400,description='Use PDF, PNG or JPEG files up to 5 MB.')
        result.append((secure_filename(file.filename) or 'evidence',media,data))
    return result


def retain(db,qry,sid,name,media,data):
    doc=db.execute('''INSERT INTO member_documents(member_id,document_type,original_name,stored_name,file_size)
        VALUES(?,'Signed query evidence',?,?,?)''',(qry['member_id'],name,'query-form:'+secrets.token_hex(16),len(data))).lastrowid
    db.execute('''INSERT INTO query_form_documents(query_id,submission_id,document_id,media_type,content)
        VALUES(?,?,?,?,?)''',(qry['id'],sid,doc,media,encrypt(base64.b64encode(data).decode())))


@queries_bp.route('/<int:qid>/workflow',methods=['GET','POST'])
@permission_required(QUERY_PERMISSION)
def signed_workflow(qid):
    db=get_db()
    if request.method=='GET':
        from .query_funnel import refresh
        refresh(db);db.commit()
    if request.method=='POST':
        db.execute('BEGIN IMMEDIATE' if not current_app.config.get('DATABASE_URL') else 'BEGIN')
        if current_app.config.get('DATABASE_URL'):db.execute('SELECT id FROM queries WHERE id=? FOR UPDATE',(qid,)).fetchone()
    qry,case=load_case(db,qid)
    if session.get('role') not in ('admin','manager') and session['user_id'] not in (qry['logged_by'],qry['assigned_to'],case['owner_id']):abort(403)
    details=json.loads(decrypt(case['details']))
    if request.method=='POST':
        if session.get('role') not in ('admin','manager') and session['user_id'] not in (qry['logged_by'],qry['assigned_to'],case['owner_id']):abort(403)
        action=request.form.get('action');notes=request.form.get('notes','').strip()
        if qry['status'] in ('closed','resolved') and action!='retry_communication':abort(409,description='The query is already complete.')
        if action=='eligibility':
            if case['stage']!='intake' or not notes:abort(400)
            details['eligibility']={'confirmed':True,'notes':notes,'by':session['user_id'],'at':stamp()}
            save_case(db,qry,case,'intake',details,qry['assigned_to'])
        elif action=='assign_reception':
            if session.get('role') not in ('admin','manager') or case['stage'] not in ('intake','awaiting_member','awaiting_information','verification','awaiting_settlement','settlement_verification'):abort(403)
            person=db.execute("SELECT id FROM users WHERE id=? AND active=1 AND COALESCE(is_platform_user,0)=0 AND (role='reception' OR department='Reception')",(request.form.get('reception_id'),)).fetchone()
            if not person or not notes or person['id']==staff_id(db,'QUERY_APPROVER_USER_ID',True):abort(400,description='Choose an active Reception verifier separate from the final manager, with assignment notes.')
            details['reception_owner_id']=person['id']
            save_case(db,qry,case,case['stage'],details,person['id'])
        elif action=='issue':
            if not g.get('tenant_slug'):abort(409,description='A tenant-bound query is required.')
            if case['stage'] not in ('intake','awaiting_information','awaiting_member'):abort(409)
            if case['kind']=='refund' and not details.get('eligibility',{}).get('confirmed'):
                abort(400,description='Confirm refund eligibility before issuing the form.')
            if case['kind']=='cancellation' and not db.execute('SELECT 1 FROM cancellation_quotes WHERE query_id=?',(qid,)).fetchone():
                abort(400,description='Save the cancellation quote first.')
            token=secrets.token_urlsafe(32)
            db.execute('UPDATE query_form_links SET revoked=1 WHERE query_id=? AND submitted_id IS NULL',(qid,))
            db.execute('INSERT INTO query_form_links(token_hash,query_id,expires_at,created_by,created_at) VALUES(?,?,?,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(),qid,(datetime.now(timezone.utc)+timedelta(days=7)).isoformat(),session['user_id'],stamp()))
            save_case(db,qry,case,'awaiting_member',details,qry['assigned_to'])
            from .query_funnel import queue_client
            link=url_for('queries.member_form',tenant_slug=g.tenant_slug,token=token,_external=True)
            queue_client(db,qry,'form:'+hashlib.sha256(token.encode()).hexdigest(),
                         'Please complete and sign your secure request form. This link expires in seven days.\n'+link+
                         ('\nAdditional information required: '+details['information_request'] if details.get('information_request') else ''))
            event(db,qid,'form_issued',{})
            db.commit()
            return render_template('queries/form_link.html',link=url_for('queries.member_form',tenant_slug=g.tenant_slug,token=token,_external=True),qid=qid)
        elif action=='request_information':
            if case['stage'] not in ('verification','management_review','pending_approval') or not notes:abort(400)
            details['information_request']=notes
            save_case(db,qry,case,'awaiting_information',details,reception_owner(qry,details))
            from .query_funnel import queue_client
            queue_client(db,qry,'information:'+stamp(),'We need additional information to process your request:\n'+notes)
        elif action=='assess_refund':
            if case['kind']!='refund' or case['stage']!='verification':abort(409)
            try:ids=sorted({int(v.strip()) for v in request.form.get('collection_ids_text','').split(',') if v.strip()})
            except ValueError:abort(400,description='Use collected transaction record IDs separated by commas.')
            if not ids:abort(400,description='Select the collected transactions to check.')
            signed=db.execute('SELECT payload FROM query_form_submissions WHERE query_id=? ORDER BY id DESC LIMIT 1',(qid,)).fetchone()
            reason=json.loads(decrypt(signed['payload']))['reason']
            ended=request.form.get('verified_contract_end') or None
            if ended:
                try:date.fromisoformat(ended)
                except ValueError:abort(400,description='Enter a valid contract end date.')
            from .query_funnel import refund_assessment
            details['refund_assessment']=refund_assessment(db,qry,ids,reason,ended)
            save_case(db,qry,case,case['stage'],details,case['owner_id'])
        elif action=='verify':
            if case['stage']!='verification' or not notes or request.form.get('evidence_confirmed')!='yes':abort(400)
            details['verification']={'notes':notes,'by':session['user_id'],'at':stamp()}
            if case['kind']=='cancellation':
                from .query_funnel import settlement
                report=settlement(db,qry)
                if not report['accepted']:abort(409,description='A signed cancellation and payment option are required.')
                save_case(db,qry,case,'settlement_verification' if report['ready'] else 'awaiting_settlement',details,reception_owner(qry,details))
                event(db,qid,action,{'notes':notes});db.commit()
                return redirect(url_for('queries.signed_workflow',qid=qid))
            if case['kind']=='freeze':
                signed=db.execute('SELECT submitted_at FROM query_form_submissions WHERE query_id=? ORDER BY id DESC LIMIT 1',(qid,)).fetchone()
                if not request.form.get('due_source','').strip():abort(400,description='Record the source of the actual payment schedule.')
                try:details['freeze_timing']=freeze_timing(signed['submitted_at'],request.form['due_date'],request.form['next_due_date'])
                except (ValueError,KeyError):abort(400,description='Confirm the actual current and next payment dates.')
                details['freeze_timing']['source']=request.form['due_source'].strip()
            if case['kind']=='banking' and qry['query_type']=='Banking details change':
                try:
                    start,end=date.fromisoformat(request.form['coverage_start']),date.fromisoformat(request.form['coverage_end'])
                    month=start.month+2;year=start.year+(month-1)//12;month=(month-1)%12+1
                    minimum=date(year,month,min(start.day,calendar.monthrange(year,month)[1]))
                    if end+timedelta(days=1)<minimum:raise ValueError()
                except (ValueError,KeyError):abort(400,description='Changes require a bank statement covering at least two months.')
                details['statement_coverage']={'from':start.isoformat(),'to':end.isoformat()}
            if case['kind']=='refund':
                checks={'collected','no_prior_refund','originating_account'}
                submission=db.execute('SELECT payload FROM query_form_submissions WHERE query_id=? ORDER BY id DESC LIMIT 1',(qid,)).fetchone()
                reason=json.loads(decrypt(submission['payload']))['reason']
                if reason=='double_debit':checks.add('two_distinct_collections')
                if reason=='after_cancellation':checks.add('cancelled_before_debit')
                if reason=='after_contract_end':checks.add('contract_ended_before_debit')
                if reason=='reception_and_debit':checks.add('same_obligation')
                if not checks.issubset(set(request.form.getlist('checks'))):abort(400,description='Confirm every applicable transaction check.')
                details['verified_amount']=amount(request.form.get('amount'))
                details['verification']['checks']=sorted(checks)
                ids=request.form.getlist('collection_ids') or request.form.get('collection_ids_text','').split(',')
                try:ids={int(value.strip()) for value in ids if str(value).strip()}
                except ValueError:abort(400,description='Enter the actual collected transaction record IDs.')
                if not ids or (reason=='double_debit' and len(ids)!=2):abort(400,description='Identify the actual collected payments.')
                rows=db.execute('SELECT * FROM collections WHERE member_id=? AND id IN ('+','.join('?' for _ in ids)+')',
                                (qry['member_id'],*ids)).fetchall()
                if len(rows)!=len(ids) or any(r['status'] not in ('paid','partial') or float(r['amount_paid'] or 0)<=0 for r in rows):
                    abort(400,description='Every selected payment must be a successful collection for this member.')
                if Decimal(details['verified_amount'])>sum(Decimal(str(r['amount_paid'])) for r in rows):abort(400,description='The refund exceeds the selected collected payments.')
                if reason=='double_debit':
                    import re
                    keys=[]
                    for row in rows:
                        key=re.search(r'(?:nupay_txn|source_key|transaction_id)=([^|]+)',row['notes'] or '')
                        if not key:abort(400,description='Distinct source transaction references are required for a double-debit claim.')
                        keys.append(key.group(1).strip())
                    if len(set(keys))<2:abort(400,description='These records refer to the same collected payment.')
                for paid in db.execute("SELECT details FROM query_workflows WHERE kind='refund' AND stage IN ('pending_approval','awaiting_payment','payment_made','complete') AND query_id!=?",(qid,)).fetchall():
                    previous=json.loads(decrypt(paid['details']))
                    if ids.intersection(previous.get('collection_ids',[])):abort(409,description='One of these collections already has a recorded refund.')
                details['collection_ids']=sorted(ids)
                from .query_funnel import refund_assessment
                contract_end=request.form.get('verified_contract_end') if reason=='after_contract_end' else None
                if reason=='after_contract_end':
                    try:date.fromisoformat(contract_end)
                    except (ValueError,TypeError):abort(400,description='Confirm the actual signed contract end date and renewal checks.')
                assessment=refund_assessment(db,qry,sorted(ids),reason,contract_end)
                if assessment['status']=='invalid':abort(409,description=' '.join(assessment['findings']))
                if reason in ('after_contract_end','reception_and_debit') and not request.form.get('entitlement_reference','').strip():
                    abort(400,description='Record the verified contract/obligation evidence reference.')
                assessment['reception_reference']=request.form.get('entitlement_reference','').strip()
                assessment['reception_verified_by']=session['user_id']
                details['refund_assessment']=assessment
                db.execute('DELETE FROM query_refund_claims WHERE query_id=?',(qid,))
                for cid in sorted(ids):
                    db.execute('INSERT INTO query_refund_claims(collection_id,query_id) VALUES(?,?) ON CONFLICT(collection_id) DO NOTHING',(cid,qid))
                    if db.execute('SELECT query_id FROM query_refund_claims WHERE collection_id=?',(cid,)).fetchone()['query_id']!=qid:
                        abort(409,description='This collection is already reserved by another refund query.')
            owner=reviewer_id(db,case['kind'])
            if case['kind']!='banking' and not owner:abort(409,description='Configure the final reviewer before handing off the query.')
            save_case(db,qry,case,'ready_for_debicheck' if case['kind']=='banking' else 'pending_approval',details,
                      reception_owner(qry,details) if case['kind']=='banking' else owner)
        elif action=='link_mandate':
            if case['kind']!='banking' or case['stage']!='ready_for_debicheck':abort(409)
            mandate=db.execute('SELECT * FROM debicheck_mandates WHERE id=? AND member_id=?',
                               (request.form.get('mandate_id'),qry['member_id'])).fetchone()
            if not mandate:abort(400,description='Choose the replacement mandate for this member.')
            signed=db.execute('SELECT payload FROM query_form_submissions WHERE query_id=? ORDER BY id DESC LIMIT 1',(qid,)).fetchone()
            if not signed:abort(409,description='A signed banking request is required.')
            signed_fields=json.loads(decrypt(signed['payload']))
            for key in ('account_number','branch_code','account_type'):
                actual=str(decrypt(mandate[key]) or '').strip()
                expected=str(signed_fields.get(key) or '').strip()
                if key=='account_type':
                    from .debicheck import ACCOUNT_TYPE_CODES
                    expected=ACCOUNT_TYPE_CODES.get(expected,expected)
                    actual=ACCOUNT_TYPE_CODES.get(actual,actual)
                if actual!=expected:
                    abort(409,description='The mandate must match the banking details on the signed request.')
            old=request.form.get('old_mandate_id')
            if qry['query_type']=='Banking details change':
                if not old or str(old)==str(mandate['id']) or not db.execute('SELECT 1 FROM debicheck_mandates WHERE id=? AND member_id=?',(old,qry['member_id'])).fetchone():abort(400,description='Identify the old mandate separately.')
            details.update(mandate_id=mandate['id'],old_mandate_id=int(old) if old else None)
            save_case(db,qry,case,'awaiting_debicheck_approval',details,reception_owner(qry,details))
        elif action=='check_mandate':
            if case['stage']!='awaiting_debicheck_approval':abort(409)
            refresh_banking_workflows(db,details['mandate_id'])
        elif action=='old_mandate_cancelled':
            if case['stage']!='manager_cancel_old_mandate' or session.get('role') not in ('admin','manager'):abort(403)
            if session['user_id']!=staff_id(db,'QUERY_APPROVER_USER_ID',True):abort(403)
            if not notes or not request.form.get('reference','').strip():abort(400,description='Record actual provider cancellation confirmation.')
            old=db.execute('SELECT status FROM debicheck_mandates WHERE id=?',(details['old_mandate_id'],)).fetchone()
            if not old or old['status'] not in ('cancelled','inactive'):abort(400,description='Old mandate cancellation must first be confirmed in the provider record.')
            details['old_cancellation']={'reference':request.form['reference'].strip(),'by':session['user_id'],'at':stamp(),'notes':notes}
            save_case(db,qry,case,'complete',details,reception_owner(qry,details))
        elif action in ('approve','reject'):
            if case['stage'] not in ('pending_approval','management_review') or session.get('role') not in ('admin','manager') or not notes:abort(403)
            approver=reviewer_id(db,case['kind'])
            if not approver or approver!=session['user_id']:abort(403,description='Configure the designated query approver.')
            if details.get('verification',{}).get('by')==session['user_id']:abort(403,description='A different manager must review the verification.')
            details['decision']={'action':action,'notes':notes,'by':session['user_id'],'at':stamp()}
            owner=reception_owner(qry,details)
            stage='rejected' if action=='reject' else 'approved_awaiting_execution'
            if case['kind']=='freeze' and action=='approve':owner=approver
            if case['kind']=='refund' and action=='approve':
                details['approved_amount']=details['verified_amount']
                stage='awaiting_payment';owner=staff_id(db,'QUERY_REFUND_PROCESSOR_USER_ID')
            save_case(db,qry,case,stage,details,owner)
        elif action=='confirm_settlement':
            if case['kind']!='cancellation' or case['stage'] not in ('awaiting_settlement','settlement_verification'):abort(409)
            from .query_funnel import settlement
            if request.form.get('attendance_confirmed')!='yes' or not notes or not request.form.get('attendance_source','').strip():
                abort(400,description='Verify non-attendance against the access register and record its source and coverage.')
            report=settlement(db,qry)
            if not report['ready']:abort(409,description='Settlement and the one-week six-instalment review interval must be complete.')
            if report['visits']:abort(409,description='Gym entry was recorded after cancellation acceptance. Resolve the attendance discrepancy before proceeding.')
            details['settlement_verification']={'by':session['user_id'],'at':stamp(),'notes':notes,
                'attendance_source':request.form['attendance_source'].strip(),'attendance_through':local_today().isoformat(),
                'report':report}
            owner=staff_id(db,'QUERY_APPROVER_USER_ID',True)
            if not owner:abort(409,description='Configure the reception manager first.')
            if owner==session['user_id']:abort(403,description='Reception must verify before a different manager performs the final check.')
            save_case(db,qry,case,'manager_cancel_debit',details,owner)
        elif action=='confirm_payment_order':
            if case['kind']!='cancellation' or case['stage'] not in ('verification','awaiting_settlement','settlement_verification') or not notes:abort(409)
            from .query_funnel import settlement
            report=settlement(db,qry)
            try:ids={int(v) for v in request.form.getlist('same_day_ids')}
            except ValueError:abort(400)
            if not ids or not ids.issubset({r['id'] for r in report['ambiguous_payments']}):abort(400)
            prior=details.get('settlement_order',{}).get('collection_ids',[])
            details['settlement_order']={'collection_ids':sorted(ids.union(prior)),'by':session['user_id'],'notes':notes,'at':stamp()}
            save_case(db,qry,case,case['stage'],details,case['owner_id'])
        elif action=='execute_freeze':
            if case['kind']!='freeze' or case['stage']!='approved_awaiting_execution' or session['user_id']!=reviewer_id(db,'freeze'):abort(403)
            if not notes or not request.form.get('reference','').strip():abort(400,description='Record the completed billing/provider freeze reference.')
            submitted=db.execute('SELECT payload FROM query_form_submissions WHERE query_id=? ORDER BY id DESC LIMIT 1',(qid,)).fetchone()
            fields=json.loads(decrypt(submitted['payload']))
            start=max(fields['start_date'],details['freeze_timing']['effective_cycle'])
            if start>fields['end_date']:abort(409,description='The approved start follows the requested end. Obtain corrected dates from the client.')
            member=db.execute('SELECT member_status FROM members WHERE id=?',(qry['member_id'],)).fetchone()
            if member['member_status']!='Active':abort(409,description='Resolve the existing membership status before applying another freeze.')
            if db.execute('SELECT 1 FROM query_freeze_execution WHERE member_id=? AND ended=0 AND start_date<=? AND end_date>=?',(qry['member_id'],fields['end_date'],start)).fetchone():abort(409,description='This member already has an overlapping freeze.')
            db.execute('INSERT INTO query_freeze_execution(query_id,member_id,start_date,end_date,prior_status,reference,executed_by,executed_at) VALUES(?,?,?,?,?,?,?,?)',
                (qid,qry['member_id'],start,fields['end_date'],member['member_status'],encrypt(request.form['reference'].strip()),session['user_id'],stamp()))
            details['completion']={'notes':notes,'by':session['user_id'],'at':stamp(),'reference':request.form['reference'].strip(),'start_date':start,'end_date':fields['end_date']}
            save_case(db,qry,case,'complete',details,reception_owner(qry,details))
            from .query_funnel import refresh
            refresh(db)
        elif action=='retry_communication':
            row=db.execute('SELECT * FROM query_communications WHERE id=? AND query_id=?',(request.form.get('communication_id'),qid)).fetchone()
            if not row or row['status'] not in ('failed','sending'):abort(409)
            if row['status']=='sending' and not notes:abort(400,description='Confirm the transport did not already send this message before retrying.')
            if row['channel'] in ('email','whatsapp'):
                member=decrypt_member(db.execute('SELECT * FROM members WHERE id=?',(qry['member_id'],)).fetchone())
                recipient=member.get('email' if row['channel']=='email' else 'contact') or ''
                if not recipient:abort(400,description='Update the member contact details before retrying.')
                db.execute('UPDATE query_communications SET recipient=? WHERE id=?',(encrypt(recipient),row['id']))
            db.execute("UPDATE query_communications SET status='pending',error=NULL WHERE id=?",(row['id'],))
        elif action=='payment_made':
            if case['kind']!='refund' or case['stage']!='awaiting_payment':abort(409)
            processor=staff_id(db,'QUERY_REFUND_PROCESSOR_USER_ID')
            if not processor or session['user_id']!=processor:abort(403)
            if amount(request.form.get('amount'))!=details['approved_amount']:abort(400,description='A changed amount needs a new approval.')
            ids=details.get('collection_ids',[])
            rows=db.execute('SELECT * FROM collections WHERE member_id=? AND id IN ('+','.join('?' for _ in ids)+')',(qry['member_id'],*ids)).fetchall() if ids else []
            if len(rows)!=len(ids) or not rows or any(r['status'] not in ('paid','partial') for r in rows) or sum(Decimal(str(r['amount_paid'] or 0)) for r in rows)<Decimal(details['approved_amount']):
                abort(409,description='The collected payments changed after approval. Recheck the refund before recording payment.')
            evidence=uploads()
            if not request.form.get('reference','').strip() or not evidence:abort(400,description='Record the payment reference and confirmation evidence.')
            try:paid_on=date.fromisoformat(request.form['paid_on'])
            except (ValueError,KeyError):abort(400)
            if paid_on>local_today():abort(400)
            sid=db.execute('SELECT id FROM query_form_submissions WHERE query_id=? ORDER BY id DESC LIMIT 1',(qid,)).fetchone()['id']
            for name,media,data in evidence:retain(db,qry,sid,name,media,data)
            details['payment']={'amount':details['approved_amount'],'date':paid_on.isoformat(),'reference':request.form['reference'].strip(),'by':processor,'at':stamp(),'verification':'staff_confirmation'}
            save_case(db,qry,case,'payment_made',details,processor)
        elif action=='complete':
            if case['stage'] not in ('payment_made','approved_awaiting_execution') or not notes or request.form.get('communicated')!='yes':abort(400)
            if case['kind'] in ('freeze','banking','cancellation'):
                abort(409,description='The provider/billing execution steps must be completed separately before closure.')
            details['completion']={'notes':notes,'by':session['user_id'],'at':stamp(),'communicated':True}
            save_case(db,qry,case,'complete',details,reception_owner(qry,details))
        elif action=='finalise_cancellation':
            if case['kind']!='cancellation' or case['stage'] not in ('manager_cancel_debit','cancelled_awaiting_mandate_review') or session.get('role') not in ('admin','manager'):abort(403)
            if session['user_id']!=staff_id(db,'QUERY_APPROVER_USER_ID',True):abort(403)
            if not notes or request.form.get('communicated')!='yes':abort(400)
            if not request.form.get('reference','').strip():abort(400,description='Record actual debit-order cancellation confirmation, including any external provider.')
            live=db.execute("SELECT 1 FROM debicheck_mandates WHERE member_id=? AND status NOT IN ('cancelled','inactive','rejected','failed','expired')",(qry['member_id'],)).fetchone()
            if live:abort(409,description='Confirm provider cancellation of outstanding debit mandates before completing the case.')
            from .query_funnel import settlement
            if case['stage']=='manager_cancel_debit' and (not settlement(db,qry)['ready'] or settlement(db,qry)['visits']):abort(409,description='Settlement or attendance changed after Reception verification. Recheck before cancellation.')
            details['completion']={'notes':notes,'by':session['user_id'],'at':stamp(),'communicated':True,'reference':request.form['reference'].strip()}
            db.execute("UPDATE members SET member_status='Cancelled' WHERE id=?",(qry['member_id'],))
            db.execute('UPDATE queries SET member_cancelled=1 WHERE id=?',(qid,))
            save_case(db,qry,case,'complete',details,reception_owner(qry,details))
        else:abort(400)
        event(db,qid,action,{'notes':notes})
        db.commit()
        return redirect(url_for('queries.signed_workflow',qid=qid))
    documents=db.execute('''SELECT d.*,m.original_name,m.uploaded_at FROM query_form_documents d
        JOIN member_documents m ON m.id=d.document_id WHERE d.query_id=? ORDER BY d.id''',(qid,)).fetchall()
    submissions=db.execute('SELECT id,submitted_at FROM query_form_submissions WHERE query_id=? ORDER BY id DESC',(qid,)).fetchall()
    latest=db.execute('SELECT payload FROM query_form_submissions WHERE query_id=? ORDER BY id DESC LIMIT 1',(qid,)).fetchone()
    from .query_funnel import settlement,refund_assessment
    report=settlement(db,qry) if case['kind']=='cancellation' else None
    communications=db.execute('SELECT id,channel,status,created_at,sent_at,error FROM query_communications WHERE query_id=? ORDER BY id DESC',(qid,)).fetchall()
    return render_template('queries/workflow.html',qry=qry,case=case,details=details,documents=documents,submissions=submissions,
        signed_fields=json.loads(decrypt(latest['payload'])) if latest else {},settlement=report,communications=communications,
        refund_assessment=details.get('refund_assessment') or (refund_assessment(db,qry,[],'not_selected') if case['kind']=='refund' else None),
        overdue=bool(qry['due_date'] and qry['due_date']<=local_today().isoformat() and case['stage'] in ('manager_cancel_debit','cancelled_awaiting_mandate_review')),
        reception_staff=db.execute("SELECT id,full_name FROM users WHERE active=1 AND COALESCE(is_platform_user,0)=0 AND (role='reception' OR department='Reception') ORDER BY full_name").fetchall())


@queries_bp.route('/form/<tenant_slug>/<token>',methods=['GET','POST'])
def member_form(tenant_slug,token):
    if not g.get('tenant'):abort(404)
    db=get_db();hashed=hashlib.sha256(token.encode()).hexdigest()
    link=db.execute('SELECT * FROM query_form_links WHERE token_hash=?',(hashed,)).fetchone()
    tenant=dict(g.tenant)
    if not link or link['revoked'] or datetime.fromisoformat(link['expires_at'])<datetime.now(timezone.utc):
        return render_template('queries/member_form.html',tenant=tenant,expired=True),410
    qry,case=load_case(db,link['query_id'])
    if link['submitted_id']:
        return render_template('queries/member_form.html',tenant=tenant,submitted=True,notice=NOTICE)
    if qry['status'] in ('closed','resolved') or case['stage']!='awaiting_member':abort(409)
    member=decrypt_member(db.execute('SELECT * FROM members WHERE id=?',(qry['member_id'],)).fetchone())
    quote=db.execute('SELECT * FROM cancellation_quotes WHERE query_id=? ORDER BY version DESC LIMIT 1',(qry['id'],)).fetchone() if case['kind']=='cancellation' else None
    if request.method=='POST':
        if request.form.get('acknowledge')!='yes' or not valid_signature(request.form.get('signature','')):abort(400,description='Acknowledge the notice and draw your signature.')
        fields=member_fields(case['kind'],request.form);evidence=uploads()
        if quote and fields.get('payment_option')=='instalments' and not json.loads(quote['instalment_dates'] or '[]'):
            abort(409,description='Reception must confirm the six agreed payment dates before this option can be accepted.')
        if (case['kind'] in ('refund','freeze','proof_of_payment') or
            (case['kind']=='banking' and qry['query_type']=='Banking details change')) and not evidence:abort(400,description='Upload the required supporting document.')
        if quote and str(request.form.get('quote_id'))!=str(quote['id']):abort(409,description='The quote changed. Reload and review it before signing.')
        snapshot={'tenant':tenant['name'],'logo':tenant.get('logo_filename'),'member':{k:member.get(k) for k in ('first_name','last_name','contact','email')},'notice':NOTICE,'quote':dict(quote) if quote else None,'version':'query-form-v1'}
        try:
            # Taking the write lock before rereading makes double-clicks idempotent.
            db.execute('BEGIN IMMEDIATE' if not current_app.config.get('DATABASE_URL') else 'BEGIN')
            if current_app.config.get('DATABASE_URL'):
                # Match staff's query-first lock order to serialize quote revisions.
                db.execute('SELECT id FROM queries WHERE id=? FOR UPDATE',(qry['id'],)).fetchone()
            current=db.execute('SELECT submitted_id,revoked,expires_at FROM query_form_links WHERE token_hash=?'+(' FOR UPDATE' if current_app.config.get('DATABASE_URL') else ''),(hashed,)).fetchone()
            if current['submitted_id']:
                db.rollback();return redirect(request.url)
            if current['revoked']:abort(410)
            if datetime.fromisoformat(current['expires_at'])<=datetime.now(timezone.utc):abort(410)
            fresh_qry,fresh_case=load_case(db,qry['id'])
            if fresh_qry['status'] in ('closed','resolved') or fresh_case['stage']!='awaiting_member':abort(409)
            if quote:
                latest=db.execute('SELECT id FROM cancellation_quotes WHERE query_id=? ORDER BY version DESC LIMIT 1',(qry['id'],)).fetchone()
                if not latest or latest['id']!=quote['id']:abort(409,description='The quote changed. Reload before signing.')
            qry,case=fresh_qry,fresh_case
            sid=db.execute('''INSERT INTO query_form_submissions(query_id,token_hash,payload,signature,snapshot,notice_version,submitted_at)
                VALUES(?,?,?,?,?,'query-form-v1',?)''',(qry['id'],hashed,encrypt(json.dumps(fields)),encrypt(request.form['signature']),encrypt(json.dumps(snapshot,default=str)),stamp())).lastrowid
            retain(db,qry,sid,'Signed request.json','application/json',json.dumps(dict(fields=fields,signature=json.loads(request.form['signature']),snapshot=snapshot),default=str).encode())
            for name,media,data in evidence:retain(db,qry,sid,name,media,data)
            db.execute('UPDATE query_form_links SET submitted_id=? WHERE token_hash=?',(sid,hashed))
            details=json.loads(decrypt(case['details']))
            owner=reception_owner(qry,details)
            save_case(db,qry,case,'verification',details,owner)
            event(db,qry['id'],'member_submitted',{'submission_id':sid})
            db.commit()
        except Exception:
            db.rollback();raise
        return redirect(request.url)
    return render_template('queries/member_form.html',tenant=tenant,member=member,kind=case['kind'],notice=NOTICE,quote=quote,
        information_request=json.loads(decrypt(case['details'])).get('information_request'))


def protected_document(db,mid,doc_id):
    if session.get('role') not in ('admin','manager') and QUERY_PERMISSION not in session.get('permissions',[]):abort(403)
    row=db.execute('''SELECT d.*,m.original_name FROM query_form_documents d JOIN member_documents m ON m.id=d.document_id
        WHERE d.document_id=? AND m.member_id=?''',(doc_id,mid)).fetchone()
    if not row:abort(404)
    qry=db.execute('SELECT * FROM queries WHERE id=?',(row['query_id'],)).fetchone()
    case=db.execute('SELECT owner_id FROM query_workflows WHERE query_id=?',(row['query_id'],)).fetchone()
    if session.get('role') not in ('admin','manager') and session['user_id'] not in (qry['logged_by'],qry['assigned_to'],case['owner_id'] if case else None):abort(403)
    member_activity(db,mid,f"Accessed query #{row['query_id']} evidence document #{doc_id}.")
    db.commit()
    return send_file(io.BytesIO(base64.b64decode(decrypt(row['content']))),mimetype=row['media_type'],as_attachment=True,download_name=row['original_name'])


def refresh_banking_workflows(db,mandate_id):
    """Called only after a provider-backed mandate status update, never on push."""
    mandate=db.execute('SELECT * FROM debicheck_mandates WHERE id=?',(mandate_id,)).fetchone()
    from .debicheck import DEBICHECK_APPROVED_STATUSES
    if not mandate or mandate['status'] not in DEBICHECK_APPROVED_STATUSES:return
    for raw in db.execute("SELECT * FROM query_workflows WHERE kind='banking' AND stage='awaiting_debicheck_approval'").fetchall():
        case=dict(raw);details=json.loads(decrypt(case['details']))
        if details.get('mandate_id')!=mandate_id:continue
        qry=db.execute('SELECT * FROM queries WHERE id=? AND member_id=?',(case['query_id'],mandate['member_id'])).fetchone()
        if not qry:continue
        old=details.get('old_mandate_id')
        save_case(db,qry,case,'manager_cancel_old_mandate' if old else 'complete',details,
                  staff_id(db,'QUERY_APPROVER_USER_ID',True) if old else reception_owner(qry,details))


@queries_bp.get('/<int:qid>/evidence/<int:doc_id>')
@permission_required(QUERY_PERMISSION)
def query_evidence(qid,doc_id):
    db=get_db();qry,_=load_case(db,qid)
    if not db.execute('SELECT 1 FROM query_form_documents WHERE query_id=? AND document_id=?',(qid,doc_id)).fetchone():abort(404)
    return protected_document(db,qry['member_id'],doc_id)
