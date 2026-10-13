import io
import json
import pytest
from flask import session
from werkzeug.datastructures import FileStorage
from onecpase.database import get_db
from onecpase.statement_uploads import upload_statements


def seed(db):
    mid = db.execute("INSERT INTO members(first_name,last_name,id_number,monthly_installment) VALUES('Statement','Test','STMT-1',500)").lastrowid
    db.commit()
    return mid


@pytest.mark.parametrize('return_to', [None, '/applications/7'])
def test_member_and_application_upload_multiple_statements(app, monkeypatch, return_to):
    def analyse(path, **kwargs):
        month = 1 if 'jan' in path else 2
        return {'error': None, 'tx_rows': [(2026,month,1,500),(2026,month,25,5500),(2026,month,28,1000)], 'verification_qualified': False}
    monkeypatch.setattr('onecpase.statement_uploads.analyse_pdf', analyse)
    with app.app_context():
        mid = seed(get_db())
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess.update(user_id=1, username='testuser', role='admin')
        data = {'document_type': 'Bank Statement', 'document': [(io.BytesIO(b'pdf'), 'jan.pdf'), (io.BytesIO(b'pdf'), 'feb.pdf')]}
        if return_to:
            data['return_to'] = return_to
        response = client.post(f'/members/{mid}/documents', data=data)
        assert response.status_code == 302
        assert response.headers['Location'] == (return_to or f'/members/{mid}#docs')
    with app.app_context():
        docs = get_db().execute('SELECT analysis_json FROM member_documents WHERE member_id=?', (mid,)).fetchall()
        assert len(docs) == 2
        for doc in docs:
            cash = json.loads(doc['analysis_json'])['cashflow']
            assert cash['enough_months'] and cash['months'] == 2
            assert cash['suggested_debit_day'] == 26


def test_reject_mixed_upload_before_saving(app):
    with app.test_request_context('/'):
        db = get_db()
        mid = seed(db)
        files = [FileStorage(io.BytesIO(b'pdf'), filename='ok.pdf'), FileStorage(io.BytesIO(b'jpg'), filename='bad.jpg')]
        with pytest.raises(ValueError, match='PDF'):
            upload_statements(db, mid, files)
        assert db.execute('SELECT COUNT(*) FROM member_documents').fetchone()[0] == 0


def test_debicheck_upload_stays_inline_and_uses_shared_analysis(app, monkeypatch):
    monkeypatch.setattr('onecpase.statement_uploads.analyse_pdf', lambda path, **kwargs:
        {'error': None, 'tx_rows': [(2026,1,1,0),(2026,1,25,5000),
                                  (2026,2,1,0),(2026,2,25,5000)]})
    with app.app_context():
        mid = seed(get_db())
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess.update(user_id=1, username='testuser', role='admin')
        page = client.get(f'/debicheck/add?member_id={mid}')
        assert page.status_code == 200
        assert b'inline-document-upload' in page.data
        response = client.post(f'/debicheck/members/{mid}/documents', data={
            'document_type': 'Bank Statement', 'document': (io.BytesIO(b'pdf'), 'statement.pdf')})
        assert response.status_code == 200
        assert response.json['analysis']['cashflow']['enough_months']
        assert 'Location' not in response.headers
        response = client.post(f'/debicheck/members/{mid}/documents', data={
            'document_type': 'SA ID Copy', 'document': (io.BytesIO(b'jpg'), 'identity.jpg')})
        assert response.status_code == 200
        response = client.post(f'/debicheck/members/{mid}/documents', data={
            'document_type': 'Bank Statement', 'document': (io.BytesIO(b'jpg'), 'bad.jpg')})
        assert response.status_code == 400
        response = client.post('/debicheck/members/999999/documents', data={
            'document_type': 'SA ID Copy', 'document': (io.BytesIO(b'jpg'), 'identity.jpg')})
        assert response.status_code == 404
    with app.app_context():
        docs = get_db().execute('SELECT document_type FROM member_documents WHERE member_id=?', (mid,)).fetchall()
        assert [r['document_type'] for r in docs] == ['Bank Statement','SA ID Copy']


def test_debicheck_document_upload_requires_permission(app):
    with app.app_context():
        mid = seed(get_db())
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess.update(user_id=1, username='testuser', role='staff', permissions=[])
        response = client.post(f'/debicheck/members/{mid}/documents', data={
            'document_type': 'SA ID Copy', 'document': (io.BytesIO(b'jpg'), 'identity.jpg')})
        assert response.status_code in (302,403)
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM member_documents').fetchone()[0] == 0


def test_collections_document_upload_keeps_task_ownership(app):
    with app.app_context():
        db = get_db()
        mid = seed(db)
        task = db.execute("INSERT INTO collection_call_tasks(task_date,member_id,queue_type,assigned_to) VALUES('2026-10-13',?,'collections',1)", (mid,)).lastrowid
        db.commit()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess.update(user_id=1, username='testuser', role='admin')
        response = client.post(f'/collections/call-queue/{task}/documents', data={
            'document_type': 'SA ID Copy', 'document': (io.BytesIO(b'jpg'), 'identity.jpg')})
        assert response.status_code == 200
        with client.session_transaction() as sess:
            sess.update(user_id=2, role='staff', permissions=['collections_call_queue'])
        response = client.post(f'/collections/call-queue/{task}/documents', data={
            'document_type': 'SA ID Copy', 'document': (io.BytesIO(b'jpg'), 'other.jpg')})
        assert response.status_code == 403
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM member_documents WHERE member_id=?', (mid,)).fetchone()[0] == 1


def test_failed_file_not_hidden_by_successful_file(app, monkeypatch):
    def analyse(path, **kwargs):
        if 'bad' in path:
            return {'error': 'Unreadable PDF'}
        return {'error': None, 'tx_rows': [(2026,1,1,0),(2026,1,25,5500)]}
    monkeypatch.setattr('onecpase.statement_uploads.analyse_pdf', analyse)
    with app.test_request_context('/'):
        session['user_id'] = 1
        db = get_db()
        mid = seed(db)
        result = upload_statements(db, mid, [FileStorage(io.BytesIO(b'pdf'), filename=name) for name in ['bad.pdf', 'ok.pdf']])
        assert 'Unreadable PDF' in result['error']
        assert not result['cashflow']['enough_months']
        assert db.execute('SELECT bank_statement_status FROM compliance_checklists').fetchone()[0] == 'uploaded'


def test_collections_uses_shared_upload_rules(app, monkeypatch):
    monkeypatch.setattr('onecpase.statement_uploads.analyse_pdf', lambda path, **kwargs: {'error': None, 'tx_rows': [(2026,1,1,0),(2026,1,25,5000)]})
    with app.app_context():
        db = get_db()
        mid = seed(db)
        task = db.execute("INSERT INTO collection_call_tasks(task_date,member_id,queue_type,assigned_to) VALUES('2026-10-12',?,'collections',1)", (mid,)).lastrowid
        db.commit()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess.update(user_id=1, username='testuser', role='admin')
        response = client.post(f'/collections/call-queue/{task}/bank-statement', data={'document': (io.BytesIO(b'pdf'), 'one.pdf')})
        assert response.status_code == 200
        assert response.json['enough_months'] is False
        assert response.json['months'] == 1
        response = client.post(f'/collections/call-queue/{task}/bank-statement', data={'document': (io.BytesIO(b'jpg'), 'one.jpg')})
        assert response.status_code == 400



def test_failed_batch_cannot_qualify_through_last_successful_document(app, monkeypatch):
    from onecpase.applications import nupay_submission_blockers, statement_months_analysed, bank_statement_qualified
    def analyse(path, **kwargs):
        if 'bad' in path:
            return {'error':'Unreadable PDF'}
        return {'error':None,'estimated_income':10000,'rating':'good','verification_qualified':True,
                'tx_rows':[(2026,1,1,1000),(2026,1,25,6000),(2026,2,1,1000),(2026,2,25,6000)]}
    monkeypatch.setattr('onecpase.statement_uploads.analyse_pdf',analyse)
    with app.test_request_context('/'):
        session['user_id']=1
        db=get_db()
        mid=seed(db)
        result=upload_statements(db,mid,[FileStorage(io.BytesIO(b'pdf'),filename=name) for name in ['bad.pdf','good.pdf']])
        assert result['cashflow']['enough_months']
        assert result['error']
        assert not statement_months_analysed(db,mid)
        assert not bank_statement_qualified(db,mid)
        assert nupay_submission_blockers(db,mid)
        doc=db.execute('SELECT analysis_json FROM member_documents ORDER BY id DESC LIMIT 1').fetchone()
        assert json.loads(doc['analysis_json'])['batch_error']
