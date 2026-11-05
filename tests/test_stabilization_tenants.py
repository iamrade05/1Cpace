import sys
from types import SimpleNamespace
from datetime import datetime
from flask import g

from onecpase.elev8_data import get_elev8_member_count
from onecpase.members import _get_last_gym_visit
from onecpase.reports import _itensity_last_visits, _VISIT_CACHE
from onecpase.database import get_db
from onecpase.dashboard import build_member_reconciliation
from test_access_revocation import sign_in


def test_other_gym_never_connects_to_elev8_even_with_warm_visit_cache(app,monkeypatch):
    def forbidden(*a,**kw):
        raise AssertionError('Another tenant attempted an Elev8 connection')
    monkeypatch.setitem(sys.modules,'pyodbc',SimpleNamespace(connect=forbidden))
    monkeypatch.setattr('onecpase.access_report._elev8_conn',forbidden)
    monkeypatch.setitem(_VISIT_CACHE,'at',datetime.now())
    monkeypatch.setitem(_VISIT_CACHE,'data',{'712345678':'2026-11-01'})
    monkeypatch.setitem(_VISIT_CACHE,'error',None)
    monkeypatch.setitem(_VISIT_CACHE,'tenant',(app.config['PLATFORM_DATABASE_PATH'],'elev8'))
    with app.test_request_context('/'):
        g.tenant_slug='othergym'
        assert get_elev8_member_count() is None
        assert _itensity_last_visits()[0]=={}
        assert _get_last_gym_visit(get_db(),{'id':999,'contact':'0712345678'}) is None


def test_bound_elev8_can_read_its_source(app,monkeypatch):
    class Connection:
        def cursor(self):return self
        def execute(self,*a):return self
        def fetchone(self):return (123,)
        def close(self):pass
    monkeypatch.setitem(sys.modules,'pyodbc',SimpleNamespace(connect=lambda *a,**kw:Connection()))
    with app.test_request_context('/'):
        g.tenant_slug='elev8'
        assert get_elev8_member_count()==123


def test_other_tenant_member_page_and_sync_do_not_call_shared_sources(app,client,monkeypatch):
    uid=sign_in(app,client)
    with app.app_context():
        from onecpase.platform_db import get_platform_db
        db=get_platform_db()
        db.execute("INSERT INTO tenants(slug,name,db_path,active) VALUES('othergym','Other Gym',?,1)",(app.config['DATABASE_PATH'],)); db.commit()
    from onecpase.tenants import TENANT_COOKIE_NAME
    client.set_cookie(TENANT_COOKIE_NAME,'othergym')
    with client.session_transaction() as session:
        session['tenant_slug']='othergym'
    def forbidden(*a,**kw):
        raise AssertionError('Cross-tenant source access')
    monkeypatch.setattr('onecpase.members.get_elev8_member_count',forbidden)
    monkeypatch.setattr('onecpase.dashboard.get_elev8_member_count',forbidden)
    monkeypatch.setattr('onecpase.dashboard.sync_itensity_members_to_local',forbidden)
    assert client.get('/members').status_code==200
    assert client.get('/dashboard').status_code==200
    assert client.post('/dashboard/reconciliation/sync').status_code==403


def test_shared_phone_does_not_hide_a_missing_member():
    local=[dict(id=1,id_number='111',contact='0712345678',member_status='Active')]
    live=[dict(id_number='111',contact='0712345678',member_status='Active'),
          dict(id_number='222',contact='0712345678',member_status='Active')]
    result=build_member_reconciliation(local,live)
    assert not result['is_clean']
    assert result['matched_count']==1
    assert len(result['missing_in_app'])==1


def test_email_punctuation_and_conflicting_ids_are_not_merged():
    local=[dict(id=1,id_number='111',email='a.b@example.com',member_status='Active')]
    for email in ('ab@example.com','a.b@example.com'):
        result=build_member_reconciliation(local,[dict(id_number='222',email=email,member_status='Active')])
        assert not result['is_clean']
        assert result['matched_count']==0


def test_contact_fallback_is_one_to_one_and_normalizes_sa_country_code():
    local=[dict(id=1,contact='0712345678',member_status='Active')]
    result=build_member_reconciliation(local,[dict(contact='+27 71 234 5678',member_status='Active')])
    assert result['is_clean']
    assert result['matched_count']==1


def test_shared_driver_is_not_loaded_for_another_gym(app):
    from onecpase.integrations import _load_driver
    with app.app_context():
        g.tenant_slug='othergym'
        driver,error=_load_driver('should-not-import','push_to_itensity.py')
        assert driver is None
        assert 'not configured for this gym' in error
