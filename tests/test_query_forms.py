import io
import json
from datetime import datetime, timezone

import pytest

from onecpase.database import get_db
from onecpase.encryption import decrypt
from onecpase.query_forms import freeze_timing


def seed(app,client,kind='refund'):
    with app.app_context():
        db=get_db()
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number,contact,email) VALUES('Form','Client','form-member','0821111111','form@example.test')").lastrowid
        qid=db.execute("""INSERT INTO queries(reference,member_id,category,query_type,description,logged_by,status)
            VALUES('Q-FORM',?,?,?,'Member request',1,'open')""",(mid,kind,'Banking details change' if kind=='banking' else 'Other')).lastrowid
        db.commit()
    client.set_cookie('onecpase_tenant','elev8')
    with client.session_transaction() as sess:
        sess.update(user_id=1,role='admin',tenant_slug='elev8',permissions=['daily_queries','all_members'])
    return mid,qid


def issue(client,qid,kind='refund'):
    path=f'/queries/{qid}/workflow'
    if kind=='refund':assert client.post(path,data={'action':'eligibility','notes':'Successful collection found; eligible for investigation'}).status_code==302
    response=client.post(path,data={'action':'issue'})
    assert response.status_code==200
    from bs4 import BeautifulSoup
    link=BeautifulSoup(response.data,'html.parser').select_one('#link')['value']
    return '/'+link.split('/',3)[3]


def submission(kind='refund'):
    return dict(details_correct='yes',reason='incorrect_debit',occurrences='1',
                description='A complaint',acknowledge='yes',signature=json.dumps([[[10,10],[60,40]]]),
                evidence=(io.BytesIO(b'%PDF-1.4\nTest bank statement'),'statement.pdf'))


def test_signed_refund_is_retained_once_and_does_not_pay_or_change_profile(app,client):
    mid,qid=seed(app,client);link=issue(client,qid)
    assert client.post(link,data=submission()).status_code==302
    assert client.post(link,data=submission()).status_code==200
    with app.app_context():
        db=get_db()
        row=db.execute('SELECT * FROM query_form_submissions').fetchone()
        assert row['payload'].startswith('enc:') and row['signature'].startswith('enc:')
        assert json.loads(decrypt(row['payload']))['occurrences']=='1'
        assert db.execute('SELECT count(*) FROM query_form_submissions').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM member_documents WHERE member_id=?',(mid,)).fetchone()[0]==2
        assert db.execute('SELECT stage FROM query_workflows').fetchone()[0]=='verification'
        assert db.execute('SELECT count(*) FROM collections WHERE member_id=?',(mid,)).fetchone()[0]==0
        assert db.execute('SELECT first_name FROM members WHERE id=?',(mid,)).fetchone()[0]=='Form'
    assert client.get(f'/queries/{qid}/workflow').status_code==200


def test_refund_eligibility_and_member_signature_are_required(app,client):
    _,qid=seed(app,client)
    assert client.post(f'/queries/{qid}/workflow',data={'action':'issue'}).status_code==400
    link=issue(client,qid)
    data=submission();data['signature']='[]'
    assert client.post(link,data=data).status_code==400
    with app.app_context():assert get_db().execute('SELECT count(*) FROM query_form_submissions').fetchone()[0]==0


def test_failed_retention_rolls_back_entire_submission(app,client,monkeypatch):
    from onecpase import query_forms
    _,qid=seed(app,client);link=issue(client,qid)
    def fail(*args):raise RuntimeError('Storage unavailable')
    monkeypatch.setattr(query_forms,'retain',fail)
    with pytest.raises(RuntimeError,match='Storage unavailable'):client.post(link,data=submission())
    with app.app_context():
        db=get_db()
        assert db.execute('SELECT count(*) FROM query_form_submissions').fetchone()[0]==0
        assert db.execute('SELECT submitted_id FROM query_form_links').fetchone()[0] is None


def test_correcting_a_form_retains_original_version(app,client):
    _,qid=seed(app,client,'complaint');link=issue(client,qid,'complaint')
    assert client.post(link,data=submission()).status_code==302
    path=f'/queries/{qid}/workflow'
    assert client.post(path,data={'action':'request_information','notes':'Please clarify the date.'}).status_code==302
    next_link=issue(client,qid,'complaint')
    assert next_link!=link
    assert client.post(next_link,data=submission()).status_code==302
    with app.app_context():assert get_db().execute('SELECT count(*) FROM query_form_submissions').fetchone()[0]==2


def test_freeze_notice_uses_signed_date_and_actual_following_cycle():
    on_time=freeze_timing('2026-11-10T23:00:00+00:00','2026-11-25','2026-12-25')
    assert on_time['received']=='2026-11-11' and not on_time['late']
    late=freeze_timing('2026-11-11T23:00:00+00:00','2026-11-25','2026-12-25')
    assert late['received']=='2026-11-12' and late['effective_cycle']=='2026-12-25'


def test_query_evidence_download_is_authorised_and_immutable(app,client):
    mid,qid=seed(app,client);link=issue(client,qid);client.post(link,data=submission())
    with app.app_context():doc=get_db().execute('SELECT id FROM member_documents WHERE member_id=? ORDER BY id DESC',(mid,)).fetchone()[0]
    assert client.get(f'/members/{mid}/documents/{doc}/download').status_code==200
    assert client.post(f'/members/{mid}/documents/{doc}/delete').status_code==409
    with client.session_transaction() as sess:sess.update(role='staff',permissions=['all_members'])
    assert client.get(f'/members/{mid}/documents/{doc}/download').status_code==403


def test_verified_refund_handoff_requires_designated_processor_and_actual_collection(app,client):
    mid,qid=seed(app,client);link=issue(client,qid);client.post(link,data=submission())
    app.config.update(QUERY_APPROVER_USER_ID=2,QUERY_REFUND_PROCESSOR_USER_ID=3)
    with app.app_context():
        db=get_db()
        for uid,role in [(1,'admin'),(2,'manager'),(3,'staff')]:
            db.execute('INSERT OR IGNORE INTO users(id,username,password_hash,full_name,role,active) VALUES(?,?,?,?,?,1)',
                       (uid,f'workflow-{uid}','x',f'Staff {uid}',role))
        cid=db.execute("""INSERT INTO collections(member_id,amount_paid,outstanding_balance,collection_date,method,status,notes)
            VALUES(?,100,0,'2026-10-20','debicheck','paid','nupay_txn=verified-123')""",(mid,)).lastrowid
        db.commit()
    path=f'/queries/{qid}/workflow'
    data=dict(action='verify',notes='Collection and original account confirmed from the statement',evidence_confirmed='yes',
              amount='100',collection_ids_text=str(cid),checks=['collected','no_prior_refund','originating_account'])
    assert client.post(path,data=data).status_code==302
    assert client.post(path,data={'action':'approve','notes':'Approved'}).status_code==403
    with client.session_transaction() as sess:sess.update(user_id=2,role='manager')
    assert client.post(path,data={'action':'approve','notes':'Approved after independent review'}).status_code==302
    pay=dict(action='payment_made',amount='100',reference='BANK-1',paid_on='2026-10-20',
             evidence=(io.BytesIO(b'%PDF-1.4\nPayment confirmation'),'paid.pdf'))
    assert client.post(path,data=pay).status_code==403
    with client.session_transaction() as sess:sess.update(user_id=3,role='staff',permissions=['daily_queries'])
    pay['evidence']=(io.BytesIO(b'%PDF-1.4\nPayment confirmation'),'paid.pdf')
    assert client.post(path,data=pay).status_code==302
    assert client.post(path,data={'action':'payment_made'}).status_code==409
    with app.app_context():
        db=get_db();details=json.loads(decrypt(db.execute('SELECT details FROM query_workflows').fetchone()[0]))
        assert details['payment']['reference']=='BANK-1'
        assert db.execute('SELECT stage FROM query_workflows').fetchone()[0]=='payment_made'
        assert db.execute('SELECT count(*) FROM collections').fetchone()[0]==1


def test_public_link_uses_own_tenant_even_with_another_staff_cookie(app,client,tmp_path):
    from onecpase.database import _init_db_on_connection
    from onecpase.platform_db import get_platform_db
    import sqlite3
    _,qid=seed(app,client);link=issue(client,qid)
    with app.app_context():
        path=tmp_path/'other.db';db=sqlite3.connect(path);db.row_factory=sqlite3.Row
        _init_db_on_connection(db,'sqlite','Other Gym');db.close()
        platform=get_platform_db()
        platform.execute("INSERT INTO tenants(slug,name,db_path,active) VALUES('other','Other Gym',?,1)",(str(path),));platform.commit()
    client.set_cookie('onecpase_tenant','other')
    with client.session_transaction() as sess:sess['tenant_slug']='other'
    response=client.get(link)
    assert response.status_code==200 and b'Form Client' in response.data and b'Other Gym' not in response.data
    assert client.post(link,data=submission()).status_code==302
    with sqlite3.connect(path) as other:
        assert other.execute('SELECT count(*) FROM query_form_submissions').fetchone()[0]==0


def test_expired_link_keeps_gym_brand_and_does_not_disclose_member(app,client):
    _,qid=seed(app,client);link=issue(client,qid)
    with app.app_context():
        db=get_db();db.execute("UPDATE query_form_links SET expires_at='2020-01-01T00:00:00+00:00'");db.commit()
    response=client.get(link)
    assert response.status_code==410 and b'Elev8' in response.data and b'Form Client' not in response.data
