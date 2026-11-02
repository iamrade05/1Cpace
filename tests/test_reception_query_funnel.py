import json
import io
from datetime import date, timedelta

from flask import session
from onecpase.database import get_db
from onecpase.encryption import decrypt, encrypt
from onecpase.query_funnel import settlement, refresh, refund_assessment, dispatch, record_whatsapp_result
from onecpase.queries import _build_cancellation_quote, _record_cancellation_quote
from test_query_forms import seed, issue, submission


def login(client, uid=1):
    with client.session_transaction() as s:
        s.update(user_id=uid,role='reception' if uid==1 else 'manager' if uid==2 else 'admin',
                 tenant_slug='elev8',permissions=['daily_queries','all_members'])


def staff(app):
    app.config.update(QUERY_APPROVER_USER_ID=2, QUERY_REFUND_PROCESSOR_USER_ID=3)
    with app.app_context():
        db=get_db()
        for uid,name,role in [(1,'Reception','reception'),(2,'Mvuleni Radebe','manager'),(3,'Ayrton Emanuel','admin')]:
            db.execute("INSERT OR IGNORE INTO users(id,username,password_hash,full_name,role,active) VALUES(?,?,'x',?,?,1)",(uid,'funnel'+str(uid),name,role))
        db.commit()


def cancellation(app,client,option='cash'):
    mid,qid=seed(app,client,'cancellation');staff(app);login(client)
    with app.app_context():
        db=get_db()
        quote=_build_cancellation_quote(12,6,100,0,'lumpsum',date(2026,1,2))
        with app.test_request_context('/'):
            session['user_id']=1;_record_cancellation_quote(db,qid,quote)
        quoteid=db.execute('SELECT id FROM cancellation_quotes WHERE query_id=?',(qid,)).fetchone()[0]
        db.commit()
    link=issue(client,qid,'cancellation')
    data=submission();data.update(payment_option=option,quote_id=str(quoteid))
    assert client.post(link,data=data).status_code==302
    with app.app_context():
        db=get_db();db.execute("UPDATE query_form_submissions SET submitted_at='2026-01-01T10:00:00+02:00' WHERE query_id=?",(qid,));db.commit()
    return mid,qid


def pay(db,mid,amount,when='2026-01-02',method='cash',notes='',purpose='general',status='paid'):
    return db.execute('INSERT INTO collections(member_id,amount_paid,outstanding_balance,collection_date,method,status,notes,payment_purpose) VALUES(?,?,0,?,?,?,?,?)',
                      (mid,amount,when,method,status,notes,purpose)).lastrowid


def state(app,qid):
    with app.app_context():
        db=get_db()
        return dict(db.execute('SELECT * FROM queries WHERE id=?',(qid,)).fetchone()),dict(db.execute('SELECT * FROM query_workflows WHERE query_id=?',(qid,)).fetchone())


def test_cash_funnel_checks_receipts_attendance_manager_provider_and_closes_same_query(app,client):
    mid,qid=cancellation(app,client)
    path=f'/queries/{qid}/workflow'
    assert client.post(path,data=dict(action='verify',notes='Signed return checked',evidence_confirmed='yes')).status_code==302
    assert state(app,qid)[1]['stage']=='awaiting_settlement'
    with app.app_context():
        db=get_db();q=db.execute('SELECT * FROM queries WHERE id=?',(qid,)).fetchone()
        due=float(settlement(db,q)['total'])
        pay(db,mid,due,when='2025-12-31') # Prior to accepted cancellation.
        pay(db,mid,due,notes='type=Discount') # Not money received.
        pay(db,mid,due,status='reversed')
        cid=pay(db,mid,due,notes='source_key=actual-receipt',purpose='card_purchase')
        pay(db,mid,due,notes='source_key=actual-receipt') # Duplicate source representation.
        report=settlement(db,q)
        assert float(report['paid'])==due and len(report['payments'])==1
        db.commit()
    confirm=dict(action='confirm_settlement',notes='All real receipts checked',attendance_confirmed='yes',attendance_source='Complete access register January through today, member identity matched')
    assert client.post(path,data=confirm).status_code==302
    q,w=state(app,qid)
    assert w['stage']=='manager_cancel_debit' and w['owner_id']==q['assigned_to']==2
    details=json.loads(decrypt(w['details']))
    from onecpase.query_funnel import day
    assert q['due_date']==(day(details['manager_handoff_at'])+timedelta(days=7)).isoformat()
    complete=dict(action='finalise_cancellation',notes='Provider confirms order stopped',reference='PROVIDER-CANCEL-42',communicated='yes')
    login(client,3)
    assert client.post(path,data=complete).status_code==403
    login(client,2)
    with app.app_context():
        db=get_db()
        db.execute("""INSERT INTO debicheck_mandates(member_id,merchant_id,client_ref1,client_ref2,account_name,account_type,account_number,branch_code,submit_date,status)
            VALUES(?,'synthetic','one','two','Example','1','123456','123456','2026-01-01','approved')""",(mid,));db.commit()
    assert client.post(path,data=complete).status_code==409
    with app.app_context():
        db=get_db();db.execute("UPDATE debicheck_mandates SET status='cancelled' WHERE member_id=?",(mid,));db.commit()
    assert client.post(path,data=complete).status_code==302
    q,w=state(app,qid)
    assert q['status']=='closed' and q['member_cancelled']==1 and w['stage']=='complete'
    with app.app_context():
        db=get_db()
        assert db.execute('SELECT member_status FROM members WHERE id=?',(mid,)).fetchone()[0]=='Cancelled'
        assert db.execute('SELECT count(*) FROM queries').fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM query_communications WHERE event_key LIKE 'outcome:%'").fetchone()[0]==2
    assert client.get(path).status_code==200


def test_six_instalments_return_to_reception_a_week_after_final_payment(app,client):
    mid,qid=cancellation(app,client,'instalments')
    path=f'/queries/{qid}/workflow'
    assert client.post(path,data=dict(action='verify',notes='Six month terms checked',evidence_confirmed='yes')).status_code==302
    with app.app_context():
        db=get_db();q=db.execute('SELECT * FROM queries WHERE id=?',(qid,)).fetchone()
        report=settlement(db,q)
        for row in report['instalments']:
            pay(db,mid,float(row['amount']),row['due_date'],method='debicheck',notes='nupay_txn=month-'+str(row['number']))
        report=settlement(db,q,date(2026,6,8))
        assert report['paid_count']==6 and report['review_on']=='2026-06-09' and not report['ready']
        refresh(db,date(2026,6,8))
        assert db.execute('SELECT stage FROM query_workflows WHERE query_id=?',(qid,)).fetchone()[0]=='awaiting_settlement'
        refresh(db,date(2026,6,9))
        assert db.execute('SELECT stage FROM query_workflows WHERE query_id=?',(qid,)).fetchone()[0]=='settlement_verification'
        db.execute("INSERT INTO turnstile_events(member_id,raw_report_hex,decision,direction,created_at) VALUES(?,'fixture','granted','enter','2026-03-10')",(mid,));db.commit()
    assert client.post(path,data=dict(action='confirm_settlement',notes='Trying despite recorded entry',attendance_confirmed='yes',attendance_source='Access register')).status_code==409
    assert state(app,qid)[1]['stage']=='settlement_verification'


def test_date_only_same_day_receipt_needs_reception_ordering_check(app,client):
    mid,qid=cancellation(app,client)
    with app.app_context():
        db=get_db();q=db.execute('SELECT * FROM queries WHERE id=?',(qid,)).fetchone()
        due=settlement(db,q)['total'];cid=pay(db,mid,float(due),'2026-01-01');db.commit()
        report=settlement(db,q)
        assert report['paid']=='0.00' and len(report['ambiguous_payments'])==1
    assert client.post(f'/queries/{qid}/workflow',data=dict(action='confirm_payment_order',same_day_ids=[str(cid)],notes='Receipt time 13:00, signed request returned 10:00')).status_code==302
    with app.app_context():
        db=get_db();q=db.execute('SELECT * FROM queries WHERE id=?',(qid,)).fetchone()
        assert settlement(db,q)['paid']==due


def test_refund_routes_to_ayrton_and_reserves_actual_receipts(app,client):
    mid,qid=seed(app,client);staff(app);login(client)
    link=issue(client,qid);assert client.post(link,data=submission()).status_code==302
    with app.app_context():
        db=get_db();cid=pay(db,mid,100,method='debicheck',notes='nupay_txn=refund-one');db.commit()
    data=dict(action='verify',notes='Refund entitlement and original account checked',evidence_confirmed='yes',amount='100',collection_ids_text=str(cid),checks=['collected','no_prior_refund','originating_account'])
    assert client.post(f'/queries/{qid}/workflow',data=data).status_code==302
    assert state(app,qid)[1]['owner_id']==3
    login(client,2)
    assert client.post(f'/queries/{qid}/workflow',data=dict(action='approve',notes='Wrong manager')).status_code==403
    login(client,3)
    assert client.post(f'/queries/{qid}/workflow',data=dict(action='approve',notes='Ayrton independently approves')).status_code==302
    with app.app_context():
        db=get_db()
        assert db.execute('SELECT query_id FROM query_refund_claims WHERE collection_id=?',(cid,)).fetchone()[0]==qid
        q=db.execute('SELECT * FROM queries WHERE id=?',(qid,)).fetchone()
        assert refund_assessment(db,q,[cid],'after_contract_end','2025-12-31')['status']=='supported'
        assert refund_assessment(db,q,[cid],'after_contract_end','2026-02-01')['status']=='invalid'
    path=f'/queries/{qid}/workflow'
    assert client.post(path,data=dict(action='payment_made',amount='100',reference='SYNTHETIC-BANK-REFUND',paid_on='2026-01-03',
        evidence=(io.BytesIO(b'%PDF-1.4\nSynthetic payment confirmation'),'confirmation.pdf'))).status_code==302
    assert client.post(path,data=dict(action='complete',notes='Refund payment and client outcome confirmed',communicated='yes')).status_code==302
    assert state(app,qid)[0]['status']=='closed'


def test_freeze_requires_manager_execution_and_restores_status_after_end(app,client):
    mid,qid=seed(app,client,'freeze');staff(app);login(client)
    link=issue(client,qid,'freeze');data=submission();data.update(reason='medical',start_date='2027-01-01',end_date='2027-02-28')
    assert client.post(link,data=data).status_code==302
    path=f'/queries/{qid}/workflow'
    assert client.post(path,data=dict(action='verify',notes='Medical evidence checked',evidence_confirmed='yes',due_date='2027-01-01',next_due_date='2027-02-01',due_source='Verified billing schedule')).status_code==302
    login(client,2)
    assert client.post(path,data=dict(action='approve',notes='Second document check completed')).status_code==302
    assert client.post(path,data=dict(action='execute_freeze',notes='Billing freeze applied')).status_code==400
    assert client.post(path,data=dict(action='execute_freeze',notes='Billing freeze applied for approved dates',reference='BILLING-FREEZE-1')).status_code==302
    assert state(app,qid)[0]['status']=='closed'
    with app.app_context():
        db=get_db();refresh(db,date(2027,1,1))
        assert db.execute('SELECT member_status FROM members WHERE id=?',(mid,)).fetchone()[0]=='Frozen'
        refresh(db,date(2027,3,1))
        assert db.execute('SELECT member_status FROM members WHERE id=?',(mid,)).fetchone()[0]=='Active'


def test_durable_messages_encrypt_links_track_transport_and_do_not_resend(app,client,monkeypatch):
    _,qid=seed(app,client,'complaint');staff(app);login(client)
    link=issue(client,qid,'complaint')
    from onecpase.extensions import mail
    from onecpase import comms
    sent=[]
    monkeypatch.setattr(mail,'send',lambda message: sent.append(message.body))
    monkeypatch.setattr(comms,'send_whatsapp_message',lambda *args: {'queued':True})
    app.config.update(TESTING=False,MAIL_SUPPRESS_SEND=False,MAIL_SERVER='smtp.example.test')
    with app.app_context():
        db=get_db()
        rows=db.execute('SELECT * FROM query_communications').fetchall()
        assert len(rows)==2 and all(row['body'].startswith('enc:') for row in rows)
        dispatch(db);dispatch(db)
        assert len(sent)==1
        wa=db.execute("SELECT * FROM query_communications WHERE channel='whatsapp'").fetchone()
        assert wa['status']=='queued'
        record_whatsapp_result(db,decrypt(wa['recipient']),decrypt(wa['body']),'sent')
        assert db.execute('SELECT status FROM query_communications WHERE id=?',(wa['id'],)).fetchone()[0]=='sent'
    app.config.update(TESTING=True,MAIL_SUPPRESS_SEND=True)


def test_manager_deadline_becomes_urgent_without_being_extended(app,client):
    mid,qid=cancellation(app,client)
    with app.app_context():
        db=get_db()
        db.execute("UPDATE query_workflows SET stage='manager_cancel_debit',owner_id=2 WHERE query_id=?",(qid,))
        db.execute("UPDATE queries SET due_date='2026-10-20',priority='medium' WHERE id=?",(qid,))
        refresh(db,date(2026,10,20));refresh(db,date(2026,10,21))
        q=db.execute('SELECT * FROM queries WHERE id=?',(qid,)).fetchone()
        assert q['priority']=='urgent' and q['due_date']=='2026-10-20'
        db.commit()
    assert b'seven days since manager handoff' in client.get(f'/queries/{qid}/workflow').data


def test_returned_contact_correction_preserves_original_settlement_start(app,client):
    mid,qid=cancellation(app,client)
    with app.app_context():
        db=get_db();original=db.execute('SELECT * FROM query_form_submissions WHERE query_id=?',(qid,)).fetchone()
        db.execute('INSERT INTO query_form_submissions(query_id,token_hash,payload,signature,snapshot,notice_version,submitted_at) VALUES(?,?,?,?,?,?,?)',
            (qid,'synthetic-second-token',original['payload'],original['signature'],original['snapshot'],'query-form-v1','2026-01-10T10:00:00+02:00'))
        q=db.execute('SELECT * FROM queries WHERE id=?',(qid,)).fetchone()
        total=settlement(db,q)['total'];pay(db,mid,float(total),'2026-01-02')
        report=settlement(db,q)
        assert report['paid']==total and report['signed_at']=='2026-01-01T10:00:00+02:00'


def test_utc_receipt_on_previous_date_is_counted_on_correct_south_african_day(app,client):
    mid,qid=cancellation(app,client)
    with app.app_context():
        db=get_db()
        db.execute("UPDATE query_form_submissions SET submitted_at='2026-01-01T00:15:00+02:00' WHERE query_id=?",(qid,))
        q=db.execute('SELECT * FROM queries WHERE id=?',(qid,)).fetchone()
        total=settlement(db,q)['total']
        pay(db,mid,float(total),'2025-12-31T22:30:00+00:00')
        report=settlement(db,q)
        assert report['paid']==total and report['ready']


def test_explicit_reception_assignment_survives_member_corrections(app,client):
    _,qid=seed(app,client,'complaint');staff(app);login(client)
    link=issue(client,qid,'complaint');assert client.post(link,data=submission()).status_code==302
    with app.app_context():
        db=get_db();db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES(4,'other-reception','x','Second Receptionist','reception','Reception',1)");db.commit()
    login(client,2);path=f'/queries/{qid}/workflow'
    assert client.post(path,data=dict(action='assign_reception',reception_id='4',notes='Move to the current Reception shift')).status_code==302
    assert state(app,qid)[1]['owner_id']==4
    login(client,4)
    assert client.post(path,data=dict(action='request_information',notes='Clarify the service date')).status_code==302
    link=issue(client,qid,'complaint')
    assert client.post(link,data=submission()).status_code==302
    q,w=state(app,qid)
    assert w['owner_id']==q['assigned_to']==4
