import json
import sqlite3
import pytest
from flask import session
from onecpase.applications import bank_statement_activation_blockers, authorize_nupay_override


@pytest.mark.parametrize('result', [None, {}, {'error':'Unreadable PDF'}, {'verification_qualified':True}, 'invalid'])
def test_missing_or_failed_analysis_requires_explicit_admin_override(app,result,monkeypatch):
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE member_documents(id INTEGER PRIMARY KEY, member_id INTEGER, document_type TEXT, analysis_json TEXT)')
    if result is not None:
        db.execute("INSERT INTO member_documents VALUES(1,1,'Bank Statement',?)",(json.dumps(result),))
    assert bank_statement_activation_blockers(db,1)
    db.execute('CREATE TABLE membership_applications(id INTEGER, member_id INTEGER)')
    monkeypatch.setattr('onecpase.applications.audit_log',lambda *args,**kwargs: None)
    with app.test_request_context('/debicheck/1/push',method='POST',data={'override':'1'}):
        session['role']='admin'
        assert authorize_nupay_override(db,1,['Missing evidence'])
        session['role']='staff'
        assert not authorize_nupay_override(db,1,['Missing evidence'])
    with app.test_request_context('/debicheck/1/push',method='POST'):
        session['role']='admin'
        assert not authorize_nupay_override(db,1,['Missing evidence'])
    db.close()


def test_only_latest_statement_analysis_counts():
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE member_documents(id INTEGER PRIMARY KEY, member_id INTEGER, document_type TEXT, analysis_json TEXT)')
    result=json.dumps({'estimated_income':10000,'rating':'good','verification_qualified':True})
    db.execute("INSERT INTO member_documents VALUES(1,1,'Bank Statement',?)",(result,))
    assert bank_statement_activation_blockers(db,1)==[]
    assert bank_statement_activation_blockers(db,2)
    db.execute("INSERT INTO member_documents VALUES(2,1,'Bank Statement',NULL)")
    assert bank_statement_activation_blockers(db,1)
    db.execute('UPDATE member_documents SET analysis_json=? WHERE id=2',(result,))
    assert bank_statement_activation_blockers(db,1)==[]
    db.close()
