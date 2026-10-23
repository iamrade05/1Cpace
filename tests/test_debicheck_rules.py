from datetime import date

import pytest

from onecpase import debicheck_rules as rules
from onecpase.database import get_db


@pytest.mark.parametrize('name', ['J Smith', 'JP Smith', 'J P Smith', 'J van der Merwe'])
def test_initials_and_surname_are_allowed(name):
    assert rules.account_holder_error(name) == ''


@pytest.mark.parametrize('name', ['J. Smith', 'J* Smith', 'J-Smith', 'J Smith-Jones', 'J O\'Neil', 'J Smith 123', 'John Peter Smith', 'Smith'])
def test_punctuation_and_full_first_names_require_correction(name):
    assert 'initials and surname' in rules.account_holder_error(name)


@pytest.mark.parametrize('value,valid', [('2026-10-16',False),('2026-10-22',False),('2026-10-23',False),('2026-10-24',True),('2026-10-25',True),('2026-02-31',False)])
def test_sales_signup_date_must_be_outside_seven_days(monkeypatch, value, valid):
    monkeypatch.setattr(rules, 'minimum_debit_date', lambda:date(2026,10,24))
    assert (rules.debit_date_error(value, sales=True) == '') is valid


@pytest.mark.parametrize('value,valid', [('2026-10-16',False),('2026-10-19',False),('2026-10-20',True),('2026-10-22',True),('2026-10-23',True),('2026-02-31',False)])
def test_outside_sales_only_past_dates_are_refused(monkeypatch, value, valid):
    """Collections, standalone mandates, edits and retries are not held to 7 days."""
    monkeypatch.setattr(rules, 'earliest_debit_date', lambda:date(2026,10,20))
    monkeypatch.setattr(rules, 'minimum_debit_date', lambda:date(2026,10,28))
    assert (rules.debit_date_error(value) == '') is valid


def test_validate_mandate_applies_the_lead_time_only_when_sales(monkeypatch):
    from onecpase.debicheck import validate_mandate
    monkeypatch.setattr(rules, 'earliest_debit_date', lambda:date(2026,10,20))
    monkeypatch.setattr(rules, 'minimum_debit_date', lambda:date(2026,10,28))
    data = {'payment_type':'Debit Order','account_name':'J Smith','bank':'ABSA','account_type':'Savings',
            'account_number':'1234567890','branch_code':'632005','monthly_installment':'500',
            'debicheck_client_ref2':'1001','debicheck_submit_date':'2026-10-22'}
    assert not any('7 days' in e for e in validate_mandate(data))
    assert any('7 days' in e for e in validate_mandate(data, sales=True))


@pytest.mark.parametrize('name,submit_date,accepted', [('J Smith','2026-10-23',False),('J. Smith','2026-10-24',False),('John Smith','2026-10-24',False),('J Smith','2026-10-24',True)])
def test_standalone_save_enforces_rules_server_side(client, monkeypatch, name, submit_date, accepted):
    monkeypatch.setattr(rules, 'earliest_debit_date', lambda:date(2026,10,24))
    with client.session_transaction() as sess:
        sess.update(user_id=1,username='testuser',role='admin')
    with client.application.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,employee_number) VALUES(1,'testuser','x','Test User','admin','1001')")
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number,join_date) VALUES('John','Smith','9001015009087','2026-10-01')").lastrowid
        db.commit()
    response=client.post('/debicheck/add',data={'member_id':mid,'account_name':name,
        'account_type':'Savings','bank':'ABSA','account_number':'1234567890','branch_code':'632005',
        'instalment_amount':'500','submit_date':submit_date,'client_ref2':'9999','submit_action':'save'})
    with client.application.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM debicheck_mandates').fetchone()[0] == (1 if accepted else 0)
    assert response.status_code == (302 if accepted else 200)
    if not accepted:
        assert b'cannot be in the past' in response.data or b'initials and surname' in response.data


def test_forms_show_minimum_and_warning_script(client,monkeypatch):
    monkeypatch.setattr(rules,'minimum_debit_date',lambda:date(2026,10,28))
    monkeypatch.setattr(rules,'earliest_debit_date',lambda:date(2026,10,24))
    with client.session_transaction() as sess:
        sess.update(user_id=1,username='testuser',role='admin')
    for path,minimum in [('/debicheck/add','2026-10-24'),('/members/add','2026-10-28')]:
        response=client.get(path)
        assert response.status_code==200
        assert f'min="{minimum}"'.encode() in response.data
        assert b'debicheck-rules.js' in response.data
        assert b'data-mandate-name' in response.data


@pytest.mark.parametrize('name,submit_date,expected', [('J. Smith','2026-10-24','initials and surname'),('J Smith','2026-10-23','cannot be in the past')])
def test_saved_mandate_retries_and_driver_cannot_bypass_rules(monkeypatch,name,submit_date,expected):
    from onecpase.debicheck import validate_stored_mandate
    from onecpase.nupay_driver import _validate_mandate
    monkeypatch.setattr(rules,'earliest_debit_date',lambda:date(2026,10,24))
    mandate={'account_name':name,'submit_date':submit_date,'id_type':'0'}
    assert any(expected in error for error in validate_stored_mandate(mandate))
    assert any(expected in error for error in _validate_mandate(mandate))


def test_detail_page_flags_saved_date_that_needs_changing(client,monkeypatch):
    from test_debicheck_routes import _login,_member_and_mandate
    monkeypatch.setattr(rules,'earliest_debit_date',lambda:date(2026,10,24))
    _login(client,'admin')
    with client.application.app_context():
        db=get_db()
        _mid,mandate_id=_member_and_mandate(db)
        db.execute('UPDATE debicheck_mandates SET submit_date=? WHERE id=?',('2026-10-23',mandate_id))
        db.commit()
    response=client.get(f'/debicheck/{mandate_id}')
    assert b'Change the mandate details before submitting' in response.data
    assert b'cannot be in the past' in response.data
    assert b'E-1001' not in response.data
