import pytest
from bs4 import BeautifulSoup
from flask import session

from onecpase.database import get_db


def assign(app, number):
    with app.app_context():
        db = get_db()
        db.execute("INSERT OR IGNORE INTO users(id,username,password_hash,full_name,role) VALUES(1,'mvuleni','x','Mvuleni Radebe','admin')")
        db.execute("UPDATE users SET employee_number=? WHERE id=1", (number,))
        db.commit()


@pytest.mark.parametrize('url,field', [('/debicheck/add', 'client_ref2'), ('/members/add', 'debicheck_client_ref2')])
def test_employee_number_appears_without_prefix(app, client, url, field):
    assign(app, '1001')
    with client.session_transaction() as sess:
        sess.update(user_id=1, role='admin', username='mvuleni')
    response = client.get(url)
    assert response.status_code == 200
    element = BeautifulSoup(response.data, 'html.parser').find('input', attrs={'name':field})
    assert element['value'] == '1001'
    assert element.has_attr('readonly')


@pytest.mark.parametrize('submitted', ['', 'E-1001', '9999'])
def test_creation_uses_employee_number_even_if_post_is_missing_or_changed(app, submitted):
    from onecpase.debicheck_routes import _submission_data
    from onecpase.members import _get_form_data
    assign(app, '1001')
    member = {'first_name':'Test','last_name':'Member','id_number':'9001015009087',
              'itensity_ref':'','join_date':'2026-10-16'}
    with app.test_request_context('/debicheck/add', method='POST', data={'client_ref2':submitted, 'debicheck_client_ref2':submitted}):
        session['user_id'] = 1
        from flask import request
        assert _submission_data(member, request.form)['debicheck_client_ref2'] == '1001'
        assert _get_form_data()['debicheck_client_ref2'] == '1001'


def test_account_without_number_retains_manual_entry(app):
    from onecpase.debicheck_routes import _submission_data
    assign(app, None)
    member = {'first_name':'Test','last_name':'Member','id_number':'9001015009087',
              'itensity_ref':'','join_date':'2026-10-16'}
    with app.test_request_context('/debicheck/add', method='POST', data={'client_ref2':'1011'}):
        session['user_id'] = 1
        from flask import request
        assert _submission_data(member, request.form)['debicheck_client_ref2'] == '1011'


def test_employee_number_is_not_reused_after_user_changes(app):
    from onecpase.employee_numbers import current_employee_number
    assign(app, '1001')
    with app.test_request_context('/'):
        session['user_id'] = 1
        assert current_employee_number() == '1001'
    with app.test_request_context('/'):
        session['user_id'] = 999
        assert current_employee_number() == ''
