import pytest

from onecpase.collection_membership import membership_payment_context
from onecpase.database import get_db
from tests.test_collections_decision import _call_task_for_arrangement, _login


@pytest.mark.parametrize('intention',['moved','not_returning'])
def test_intention_to_leave_does_not_stop_membership_fees_or_cancel_account(app,client,intention):
    task=_call_task_for_arrangement(app,'none')
    _login(client,task)
    quote=client.get(f"/collections/call-queue/{task['id']}/offer-preview",query_string={
        'conversation_intention':intention,'monthly_affordable':'100','intention':'instalments'}).get_json()
    assert quote['normal_instalment'] == 150
    assert quote['arrangement']['total_monthly'] > 150
    assert [m['purpose'] for m in quote['mandates_to_load']] == ['Normal instalment','Arrangement (PTP)']
    assert 'does not cancel the membership' in quote['membership_context']['say']
    with app.app_context():
        db=get_db()
        assert db.execute('SELECT member_status FROM members WHERE id=?',(task['member_id'],)).fetchone()[0] == 'Active'
        assert db.execute('SELECT count(*) FROM queries').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM ptp_agreements').fetchone()[0] == 0


def test_cancelled_member_gets_arrears_only_schedule_and_no_new_normal_mandate(app,client):
    task=_call_task_for_arrangement(app,'none')
    _login(client,task)
    with app.app_context():
        db=get_db()
        db.execute("UPDATE members SET member_status='Cancelled' WHERE id=?",(task['member_id'],));db.commit()
    data={'conversation_intention':'not_returning','outcome':'promised_to_pay','monthly_affordable':'100',
          'intention':'instalments','notes':'Client accepts arrears repayment; membership already cancelled.'}
    url=f"/collections/call-queue/{task['id']}"
    quote=client.get(url+'/offer-preview',query_string=data).get_json()
    assert quote['membership_context']['cancelled']
    assert quote['normal_instalment'] == quote['arrangement']['normal_instalment'] == 0
    assert quote['arrangement']['total_monthly'] == quote['arrangement']['monthly_arrears']
    assert [m['purpose'] for m in quote['mandates_to_load']] == ['Arrangement (PTP)']
    assert 'does not restart' in quote['membership_context']['say']
    assert 'separately check' in quote['membership_context']['say']
    assert client.post(url+'/result',data=data).status_code == 302
    with app.app_context():
        db=get_db()
        ptp=db.execute('SELECT * FROM ptp_agreements').fetchone()
        assert [r[0] for r in db.execute('SELECT purpose FROM ptp_mandate_links WHERE ptp_id=?',(ptp['id'],))] == ['Arrangement (PTP)']
        assert ptp['payment_count'] == quote['arrangement']['payments']
        assert ptp['grant_status'] != 'granted'
        assert db.execute('SELECT member_status FROM members WHERE id=?',(task['member_id'],)).fetchone()[0] == 'Cancelled'


def test_pending_cancellation_keeps_fee_and_points_to_existing_request(app,client):
    task=_call_task_for_arrangement(app,'none')
    with app.app_context():
        db=get_db()
        qid=db.execute("INSERT INTO queries(reference,member_id,category,query_type,description,status) VALUES('CAN-REVIEW',?,'cancellation','Cancellation request','Member requested cancellation','open')",(task['member_id'],)).lastrowid
        db.commit()
        context=membership_payment_context(db,task['member_id'])
        assert not context['cancelled'] and context['normal_instalment'] == 150
        assert context['open_cancellation']['id'] == qid
        assert 'cancellation request is already open' in context['say']
    _login(client,task)
    page=client.get(f"/collections/call-queue/{task['id']}/profile")
    assert page.status_code == 200
    assert b'Review existing cancellation CAN-REVIEW' in page.data
