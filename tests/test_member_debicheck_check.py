import pytest
from onecpase.database import get_db


@pytest.mark.parametrize('state,label', [('active',b'Active DebiCheck mandate confirmed'), ('inactive',b'Mandate found, but it is not active'), ('none',b'No DebiCheck mandate found'), ('error',b'Unable to verify DebiCheck')])
def test_profile_checks_live_mandate_without_local_record(app,monkeypatch,state,label):
    with app.app_context():
        db=get_db()
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number) VALUES('Live','Check','1234567890123')").lastrowid
        db.commit()
    calls=[]
    def check(member):
        calls.append(member)
        return {'state':state,'status':'approved' if state=='active' else None,'contract_reference':'NU123' if state=='active' else None,'message':'Test NuPay result'}
    monkeypatch.setattr('onecpase.debicheck.check_member_on_nupay',check)
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess.update(user_id=1,username='testuser',role='admin')
        page=client.get(f'/members/{mid}')
        assert b'Check DebiCheck' in page.data
        response=client.post(f'/members/{mid}/check-debicheck',follow_redirects=True)
        assert response.status_code==200
        assert label in response.data
        assert len(calls)==1
    with app.app_context():
        rows=get_db().execute('SELECT * FROM nupay_mandate_checks WHERE member_id=?',(mid,)).fetchall()
        assert len(rows)==(0 if state=='error' else 1)
        if rows:
            assert rows[0]['state']==state
            assert rows[0]['checked_by']==1
        assert get_db().execute('SELECT COUNT(*) FROM debicheck_mandates').fetchone()[0]==0


def test_check_requires_login_and_existing_member(app,monkeypatch):
    def unexpected(member):
        pytest.fail('NuPay must not be called')
    monkeypatch.setattr('onecpase.debicheck.check_member_on_nupay',unexpected)
    with app.test_client() as client:
        assert client.post('/members/999/check-debicheck').status_code==302
        with client.session_transaction() as sess:
            sess.update(user_id=1,username='testuser',role='admin')
        assert client.post('/members/999/check-debicheck').status_code==404
