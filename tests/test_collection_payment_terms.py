from datetime import date

import pytest

from onecpase.database import get_db
from onecpase.ptp_grant import arrangement_payment_terms
from tests.test_collections_decision import _call_task_for_arrangement, _login, _admin


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr('onecpase.debicheck_rules.earliest_debit_date', lambda: date(2026,11,6))


def test_collections_can_agree_today_instead_of_an_automatic_later_debit_date():
    assert arrangement_payment_terms('2026-11-06', 'debicheck', debit_day=15) == ('2026-11-06','debicheck')


@pytest.mark.parametrize('stamp', ['', '2026-11-05', '2027-02-30', 'Friday'])
def test_invalid_or_past_dates_are_rejected_instead_of_silently_replaced(stamp):
    with pytest.raises(ValueError):
        arrangement_payment_terms(stamp, 'debicheck', debit_day=15)


def terms(**changes):
    return dict(outcome='promised_to_pay', intention='instalments', upfront_offered='0',
                monthly_affordable='100', first_collection_date='2027-01-31',
                arrangement_payment_method='debicheck', notes='Client agreed the quoted terms.', **changes)


def test_agreed_date_and_method_match_preview_saved_schedule_and_mandate_form(app, client):
    from onecpase.collection_arrangement import arrangement_confirmation
    task = _call_task_for_arrangement(app, 'none')
    _login(client,task)
    url = f"/collections/call-queue/{task['id']}"
    data = terms()
    quote = client.get(url+'/offer-preview',query_string=data).get_json()
    assert quote['first_collection_date'] == '2027-01-31'
    assert quote['payment_method'] == 'debicheck'
    assert [p['date'] for p in quote['proposed_schedule']] == ['2027-01-31','2027-02-28','2027-03-31']
    assert client.post(url+'/result',data=data).status_code == 302
    with app.app_context():
        db = get_db()
        ptp = db.execute('SELECT * FROM ptp_agreements WHERE member_id=?',(task['member_id'],)).fetchone()
        assert ptp['first_collection_date'] == ptp['promise_date'] == '2027-01-31'
        assert ptp['payment_method'] == 'debicheck'
        assert ptp['grant_status'] != 'granted'
        confirmation = arrangement_confirmation(db,task['member_id'])
        assert confirmation['schedule'] == quote['proposed_schedule']
        assert '31 Jan 2027' in confirmation['proposal_say'] and 'by DebiCheck' in confirmation['proposal_say']
        assert 'First arrears collection 2027-01-31 by debicheck' in db.execute(
            'SELECT notes FROM collection_call_tasks WHERE id=?',(task['id'],)).fetchone()[0]
        pid = ptp['id']
    _admin(client)
    response = client.get('/debicheck/add',query_string={'member_id':task['member_id'],
        'from_ptp':pid,'orders':'Arrangement (PTP):83.34'})
    assert response.status_code == 200
    assert 'value="2027-01-31"' in response.get_data(as_text=True)


@pytest.mark.parametrize('changes', [
    {'first_collection_date':''}, {'first_collection_date':'2026-11-05'},
    {'first_collection_date':'invalid'}, {'arrangement_payment_method':'cash'},
    {'arrangement_payment_method':'eft'}])
def test_invalid_terms_cannot_close_the_call_or_create_an_arrangement(app,client,changes):
    task = _call_task_for_arrangement(app,'none')
    _login(client,task)
    data = terms(); data.update(changes)
    url = f"/collections/call-queue/{task['id']}"
    quote = client.get(url+'/offer-preview',query_string=data).get_json()
    assert quote['payment_terms_error'] and not quote['proposed_schedule']
    assert client.post(url+'/result',data=data).status_code == 302
    with app.app_context():
        db = get_db()
        assert db.execute('SELECT status FROM collection_call_tasks WHERE id=?',(task['id'],)).fetchone()[0] != 'completed'
        assert db.execute('SELECT count(*) FROM ptp_agreements').fetchone()[0] == 0


def test_linked_mandate_cannot_silently_change_the_agreed_date(app,client):
    task = _call_task_for_arrangement(app,'none')
    _login(client,task)
    client.post(f"/collections/call-queue/{task['id']}/result",data=terms())
    with app.app_context():
        pid = get_db().execute('SELECT id FROM ptp_agreements').fetchone()[0]
    _admin(client)
    response = client.post('/debicheck/add',data={
        'member_id':task['member_id'],'from_ptp':str(pid),'purpose':'Arrangement (PTP)',
        'orders':'Arrangement (PTP):83.34','submit_date':'2027-02-01',
        'account_name':'T Client','bank':'FNB','account_type':'Cheque/Current',
        'account_number':'62000000001','branch_code':'250655','instalment_amount':'83.34',
        'client_ref1':'TEST','client_ref2':'0001'})
    assert 'first debit date must match the saved arrangement (2027-01-31)' in response.get_data(as_text=True)
    with app.app_context():
        db = get_db()
        assert db.execute('SELECT count(*) FROM debicheck_mandates').fetchone()[0] == 0
        assert db.execute('SELECT first_collection_date FROM ptp_agreements WHERE id=?',(pid,)).fetchone()[0] == '2027-01-31'


def test_full_settlement_does_not_validate_unused_monthly_payment_fields(app,client):
    task = _call_task_for_arrangement(app,'none')
    _login(client,task)
    data = terms(); data.update(intention='settle',first_collection_date='',arrangement_payment_method='')
    quote = client.get(f"/collections/call-queue/{task['id']}/offer-preview",query_string=data).get_json()
    assert quote['payment_terms_error'] is None
    assert quote['proposed_schedule'] == []
