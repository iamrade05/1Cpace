"""Provider outcomes must survive local failures without duplicate submission."""
from datetime import datetime, timedelta, timezone
import pytest

from onecpase import applications
from onecpase.database import get_db
from test_access_revocation import sign_in


def application(app,client):
    uid=sign_in(app,client)
    with app.app_context():
        db=get_db()
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number,member_status) VALUES ('Recovery','Member','TEST-RECOVERY','Unverified')").lastrowid
        aid=db.execute("INSERT INTO membership_applications(member_id,created_by,application_status,push_status) VALUES (?,?,'ready_to_push','pushing')",(mid,uid)).lastrowid
        db.commit()
    return uid,mid,aid


def test_external_success_then_local_failure_is_recoverable_without_another_push(app,client,monkeypatch):
    uid,mid,aid=application(app,client)
    calls=[]
    monkeypatch.setattr(applications,'_itensity_hard_blockers',lambda *a:[])
    monkeypatch.setattr(applications,'push_member_to_itensity',lambda *a:(calls.append(1) or (True,'IT-CONFIRMED',None)))
    def fail_after_writes(*a,**kw):
        raise RuntimeError('Injected local transition failure')
    monkeypatch.setattr(applications,'_sync_sales_stage_for_application',fail_after_writes)
    applications._background_itensity_push(app,app.config['DATABASE_PATH'],aid,mid,None,False,[],uid)
    with app.app_context():
        row=get_db().execute('SELECT * FROM membership_applications WHERE id=?',(aid,)).fetchone()
        member=get_db().execute('SELECT * FROM members WHERE id=?',(mid,)).fetchone()
        assert row['application_status']=='ready_to_push'
        assert row['push_status']=='reconcile_required'
        assert row['itensity_confirmed_ref']=='IT-CONFIRMED'
        assert member['member_status']=='Unverified'
        assert not member['itensity_ref']
    monkeypatch.setattr(applications,'_sync_sales_stage_for_application',lambda *a,**kw:None)
    applications._background_itensity_push(app,app.config['DATABASE_PATH'],aid,mid,None,False,[],uid,True)
    with app.app_context():
        row=get_db().execute('SELECT * FROM membership_applications WHERE id=?',(aid,)).fetchone()
        assert (row['application_status'],row['push_status'])==('active','pushed')
        assert get_db().execute('SELECT itensity_ref FROM members WHERE id=?',(mid,)).fetchone()[0]=='IT-CONFIRMED'
    assert len(calls)==1


def test_unknown_external_outcome_cannot_be_retried_until_reviewed(app,client,monkeypatch):
    uid,mid,aid=application(app,client)
    monkeypatch.setattr(applications,'_itensity_hard_blockers',lambda *a:[])
    monkeypatch.setattr(applications,'push_member_to_itensity',lambda *a:(False,None,'Connection lost after submit'))
    applications._background_itensity_push(app,app.config['DATABASE_PATH'],aid,mid,None,False,[],uid)
    with app.app_context():
        db=get_db()
        assert db.execute('SELECT push_status FROM membership_applications WHERE id=?',(aid,)).fetchone()[0]=='push_unknown'
        assert not applications._mark_itensity_push_started(db,aid)
        db.rollback()
    assert client.post(f'/applications/{aid}/review-push',data={'outcome':'found','itensity_ref':'IT-REVIEWED','notes':'Matched the verified ID in the external roster.'}).status_code==302
    with app.app_context():
        row=get_db().execute('SELECT * FROM membership_applications WHERE id=?',(aid,)).fetchone()
        assert row['push_status']=='reconcile_required'
        assert row['itensity_confirmed_ref']=='IT-REVIEWED'


@pytest.mark.parametrize('running',[True,False])
def test_fresh_or_live_worker_cannot_be_manually_requeued(app,client,running):
    uid,mid,aid=application(app,client)
    with app.app_context():
        db=get_db()
        start=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(hours=1 if running else 0)
        db.execute('UPDATE membership_applications SET push_started_at=? WHERE id=?',(start.isoformat(),aid)); db.commit()
    key=(app.config['DATABASE_PATH'],aid)
    if running: applications._push_workers.add(key)
    try:
        assert client.post(f'/applications/{aid}/review-push',data={'outcome':'not_created','notes':'Checked externally'}).status_code==409
    finally:
        applications._push_workers.discard(key)


def test_interrupted_worker_can_be_reviewed_after_restart(app,client):
    uid,mid,aid=application(app,client)
    with app.app_context():
        db=get_db(); db.execute("UPDATE membership_applications SET push_started_at='2020-01-01 00:00:00' WHERE id=?",(aid,)); db.commit()
    assert client.post(f'/applications/{aid}/review-push',data={'outcome':'not_created','notes':'Verified no external member exists after restart.'}).status_code==302
    with app.app_context():
        assert get_db().execute('SELECT push_status FROM membership_applications WHERE id=?',(aid,)).fetchone()[0]=='push_failed'


def test_legacy_active_failed_record_can_be_reconciled_without_resubmission(app,client,monkeypatch):
    uid,mid,aid=application(app,client)
    with app.app_context():
        db=get_db()
        db.execute("UPDATE members SET member_status='Active',itensity_ref='IT-OLD' WHERE id=?",(mid,))
        db.execute("UPDATE membership_applications SET application_status='active',push_status='push_failed' WHERE id=?",(aid,))
        db.commit()
    assert client.post(f'/applications/{aid}/review-push',data={'outcome':'not_created','notes':'Invalid retry'}).status_code==409
    assert client.post(f'/applications/{aid}/review-push',data={'outcome':'found','itensity_ref':'IT-OLD','notes':'Verified existing external membership.'}).status_code==302
    with app.app_context():
        db=get_db()
        assert applications._mark_itensity_push_started(db,aid)
        db.commit()
    monkeypatch.setattr(applications,'_itensity_hard_blockers',lambda *a:[])
    monkeypatch.setattr(applications,'_sync_sales_stage_for_application',lambda *a,**kw:None)
    monkeypatch.setattr(applications,'push_member_to_itensity',lambda *a:pytest.fail('Must not recreate the member'))
    monkeypatch.setattr(applications,'update_member_in_itensity',lambda *a:pytest.fail('Recovery is local only'))
    applications._background_itensity_push(app,app.config['DATABASE_PATH'],aid,mid,'IT-OLD',False,[],uid,True)
    with app.app_context():
        assert get_db().execute('SELECT push_status FROM membership_applications WHERE id=?',(aid,)).fetchone()[0]=='pushed'


@pytest.mark.parametrize('change',["active=0","session_version=1","role='staff'"])
def test_revoked_requester_cannot_start_a_background_provider_operation(app,client,monkeypatch,change):
    uid,mid,aid=application(app,client)
    with app.app_context():
        db=get_db(); db.execute('UPDATE users SET '+change+' WHERE id=?',(uid,)); db.commit()
    monkeypatch.setattr(applications,'_itensity_hard_blockers',lambda *a:[])
    monkeypatch.setattr(applications,'push_member_to_itensity',lambda *a:pytest.fail('Revoked requester must not submit'))
    applications._background_itensity_push(app,app.config['DATABASE_PATH'],aid,mid,None,False,[],uid,False,0)
    with app.app_context():
        assert get_db().execute('SELECT push_status FROM membership_applications WHERE id=?',(aid,)).fetchone()[0]=='push_failed'


def test_revocation_during_provider_submission_preserves_reference_without_local_activation(app,client,monkeypatch):
    import sqlite3
    uid,mid,aid=application(app,client)
    monkeypatch.setattr(applications,'_itensity_hard_blockers',lambda *a:[])
    def external_success(*args):
        with sqlite3.connect(app.config['DATABASE_PATH']) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?',(uid,))
        return True,'IT-CREATED-BEFORE-REVOCATION',None
    monkeypatch.setattr(applications,'push_member_to_itensity',external_success)
    applications._background_itensity_push(app,app.config['DATABASE_PATH'],aid,mid,None,False,[],uid,False,0)
    with app.app_context():
        row=get_db().execute('SELECT * FROM membership_applications WHERE id=?',(aid,)).fetchone()
        assert row['push_status']=='reconcile_required'
        assert row['itensity_confirmed_ref']=='IT-CREATED-BEFORE-REVOCATION'
        assert row['application_status']=='ready_to_push'
