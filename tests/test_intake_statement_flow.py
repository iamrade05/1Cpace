import io
import pytest
from onecpase.database import get_db


def login(client):
    with client.session_transaction() as sess:
        sess.update(user_id=1,username='testuser',role='admin')


def test_new_intake_opens_application_with_bold_statement_prompt(app):
    with app.test_client() as client:
        login(client)
        response=client.post('/members/add',data={'first_name':'Intake','last_name':'Test','id_number':'9001015009087','contact':'0712345678','payment_type':'Cash'})
        assert response.status_code==302
        assert '/applications/' in response.headers['Location']
        assert 'intake=1' in response.headers['Location']
        response=client.get(response.headers['Location'])
        assert response.status_code==200
        assert b'<strong>Upload 2 months of bank statements to open DebiCheck.</strong>' in response.data
        assert b'id="intakeStatementPrompt"' in response.data
        assert b'intake=1' in response.data


@pytest.mark.parametrize('months,error',[(1,None),(2,None),(2,'Unreadable PDF')])
def test_intake_upload_opens_debicheck_only_after_statement_passes(app,monkeypatch,months,error):
    def analyse(path,**kwargs):
        rows=[(2026,month,1,500) for month in range(1,months+1)]+[(2026,month,25,5500) for month in range(1,months+1)]
        return {'estimated_income':10000,'rating':'good','avg_balance':1000,'max_balance':5500,'returned_debits':0,'error':error,'verification_qualified':True,'tx_rows':rows}
    monkeypatch.setattr('onecpase.statement_uploads.analyse_pdf',analyse)
    with app.test_request_context("/"):
        db=get_db()
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number) VALUES('Intake','Test','9001015009087')").lastrowid
        from onecpase.applications import ensure_member_application
        aid=ensure_member_application(db,mid)
        db.commit()
    with app.test_client() as client:
        login(client)
        target=f'/applications/{aid}?intake=1'
        response=client.post(f'/members/{mid}/documents',data={'document_type':'Bank Statement','return_to':target,'document':(io.BytesIO(b'pdf'),'statement.pdf')})
        assert response.headers['Location']==target
        response=client.get(target)
        if months>=2 and not error:
            assert response.status_code==302
            assert response.headers['Location']==f'/debicheck/add?member_id={mid}'
        else:
            assert response.status_code==200
            assert b'id="intakeStatementPrompt"' in response.data
        # Ordinary visits are not redirected into the intake flow.
        assert client.get(f'/applications/{aid}').status_code==200
