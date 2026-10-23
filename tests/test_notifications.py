from datetime import datetime

import pytest
from onecpase.database import get_db
from onecpase.notifications import sync_notifications, validate_supervisor


@pytest.fixture
def seeded(app):
    with app.app_context():
        db = get_db()
        for uid, role, department in [(1, 'staff', 'Sales'), (2, 'manager', 'Management'), (3, 'reception', 'Reception'), (4, 'admin', 'Management')]:
            db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,email) VALUES (?,?, 'x',?,?,?,?)", (uid, f'n{uid}', f'Staff {uid}', role, department, f'n{uid}@example.com'))
        db.execute("UPDATE users SET supervisor_id=2 WHERE id IN (1,3)")
        db.execute("INSERT INTO members(id,first_name,last_name,id_number) VALUES (1,'Test','Member','notif-test')")
        db.execute("""INSERT INTO queries(id,category,description,status,assigned_to,logged_by,follow_up_date)
            VALUES (1,'payments','Test','escalated',1,3,'2026-10-08')""")
        db.commit()
    return app


def login(client, uid, role='staff'):
    with client.session_transaction() as s:
        s.update(user_id=uid, role=role, permissions=['daily_queries'])


def sync(db, day=9):
    sync_notifications(db, now=datetime(2026,10,day,10))
    db.commit()


def test_only_owner_and_own_supervisor_receive_escalation(seeded):
    with seeded.app_context():
        db=get_db()
        sync(db)
        rows=db.execute('SELECT * FROM staff_notifications').fetchall()
        assert {r['recipient_id'] for r in rows}=={1,2}
        assert all(r['reminder_count']==1 for r in rows)
        sync(db)
        assert db.execute('SELECT SUM(reminder_count) FROM staff_notifications').fetchone()[0]==2
        sync(db,10)
        assert db.execute('SELECT SUM(reminder_count) FROM staff_notifications').fetchone()[0]==4


def test_acknowledgment_stops_only_recipients_reminders_and_does_not_resolve(seeded,client):
    with seeded.app_context():
        db=get_db(); sync(db)
        nid=db.execute('SELECT id FROM staff_notifications WHERE recipient_id=1').fetchone()[0]
    login(client,1)
    assert client.post(f'/notifications/{nid}/acknowledge').status_code==302
    with seeded.app_context():
        db=get_db(); sync(db,10)
        rows={r['recipient_id']:r for r in db.execute('SELECT * FROM staff_notifications')}
        assert rows[1]['acknowledged_at'] and rows[1]['reminder_count']==1
        assert rows[2]['reminder_count']==2
        assert db.execute('SELECT status FROM queries WHERE id=1').fetchone()[0]=='escalated'
        db.execute("UPDATE queries SET status='resolved' WHERE id=1")
        sync(db,11)
        assert all(r[0] for r in db.execute('SELECT resolved_at FROM staff_notifications'))


def test_new_escalation_resets_alert_even_after_acknowledgment(seeded):
    with seeded.app_context():
        db=get_db(); sync(db)
        db.execute("UPDATE staff_notifications SET acknowledged_at='2026-10-09'")
        db.execute("INSERT INTO query_notes(query_id,created_by,note,action_type) VALUES (1,1,'Again','escalated')")
        sync(db,10)
        rows=db.execute('SELECT * FROM staff_notifications WHERE resolved_at IS NULL').fetchall()
        assert len(rows)==2 and all(not r['acknowledged_at'] for r in rows)


def test_notifications_are_private_and_cross_recipient_acknowledgment_is_refused(seeded,client):
    with seeded.app_context():
        db=get_db(); sync(db)
        nid=db.execute('SELECT id FROM staff_notifications WHERE recipient_id=1').fetchone()[0]
    login(client,3,'reception')
    assert client.get('/notifications/count').json=={'count':0}
    assert client.post(f'/notifications/{nid}/acknowledge').status_code==404
    assert 'Query #1' not in client.get('/notifications').text
    login(client,1)
    assert client.get('/notifications/count').json=={'count':1}
    assert 'Query #1' in client.get('/notifications').text


def test_sales_and_reception_exception_sources_close_when_completed(seeded):
    with seeded.app_context():
        db=get_db()
        db.execute("INSERT INTO leads(id,full_name,assigned_to) VALUES (1,'Test Lead',1)")
        db.execute("INSERT INTO lead_activities(lead_id,activity_type,status,follow_up_at) VALUES (1,'follow_up','pending','2026-10-08')")
        db.execute("INSERT INTO collection_exceptions(member_id,reason,created_by) VALUES (1,'Approval needed',3)")
        sync(db)
        assert db.execute("SELECT COUNT(*) FROM staff_notifications WHERE source_kind='sale'").fetchone()[0]==2
        assert db.execute("SELECT COUNT(*) FROM staff_notifications WHERE source_kind='collection'").fetchone()[0]==2
        db.execute("UPDATE lead_activities SET status='completed'")
        db.execute("UPDATE collection_exceptions SET status='approved'")
        sync(db,10)
        assert db.execute("SELECT COUNT(*) FROM staff_notifications WHERE source_kind IN ('sale','collection') AND resolved_at IS NULL").fetchone()[0]==0


def test_supervisor_validation_rejects_cycles_inactive_and_unknown(seeded):
    with seeded.app_context():
        db=get_db()
        assert validate_supervisor(db,1,2)==2
        assert validate_supervisor(db,1,'') is None
        for candidate in [1,999,'invalid']:
            with pytest.raises(ValueError): validate_supervisor(db,1,candidate)
        with pytest.raises(ValueError): validate_supervisor(db,2,1)
        db.execute('UPDATE users SET active=0 WHERE id=2')
        with pytest.raises(ValueError): validate_supervisor(db,1,2)


def test_supervisor_change_retires_previous_supervisors_alert(seeded):
    with seeded.app_context():
        db=get_db(); sync(db)
        db.execute('UPDATE users SET supervisor_id=4 WHERE id=1')
        sync(db,10)
        assert {r[0] for r in db.execute('SELECT recipient_id FROM staff_notifications WHERE resolved_at IS NULL')}=={1,4}


def test_phase_one_has_sales_alerts_and_hides_query_and_collection_alerts(seeded,client):
    seeded.config['ACTIVE_PHASE']=1
    login(client,1)
    assert client.get('/notifications/count').json=={'count':0}


def test_admin_can_save_supervisor_without_password_change(seeded,client):
    login(client,4,'admin')
    response=client.post('/admin/users/1/edit', data={'full_name':'Staff 1','email':'n1@example.com','role':'staff','department':'Sales','active':'1','supervisor_id':'4'})
    assert response.status_code==302
    with seeded.app_context():
        assert get_db().execute('SELECT supervisor_id FROM users WHERE id=1').fetchone()[0]==4
    response=client.post('/admin/users/4/edit', data={'full_name':'Staff 4','email':'n4@example.com','role':'admin','active':'1','supervisor_id':'1'})
    assert response.status_code==200
    assert 'reporting cycle' in response.text


def test_admin_create_and_password_edit_save_supervisor(seeded,client):
    login(client,4,'admin')
    response=client.post('/admin/users/add',data={'username':'newstaff','full_name':'New Staff','email':'new@example.com','role':'staff','password':'StrongPass123','supervisor_id':'2'})
    assert response.status_code==302
    with seeded.app_context():
        row=get_db().execute("SELECT id,supervisor_id FROM users WHERE username='newstaff'").fetchone()
        assert row['supervisor_id']==2
        uid=row['id']
    response=client.post(f'/admin/users/{uid}/edit',data={'full_name':'New Staff','email':'new@example.com','role':'staff','active':'1','new_password':'OtherPass123','supervisor_id':'4'})
    assert response.status_code==302
    with seeded.app_context():
        assert get_db().execute('SELECT supervisor_id FROM users WHERE id=?',(uid,)).fetchone()[0]==4


def test_escalation_route_creates_alert_immediately(seeded,client):
    login(client,1)
    assert client.post('/queries/1/escalate',data={'reason':'Supervisor assistance needed'}).status_code==302
    with seeded.app_context():
        rows=get_db().execute('SELECT recipient_id FROM staff_notifications WHERE resolved_at IS NULL').fetchall()
        assert {r[0] for r in rows}=={1,2}


def test_acknowledged_old_escalation_cannot_hide_new_approval(seeded):
    with seeded.app_context():
        db=get_db(); sync(db)
        db.execute("UPDATE staff_notifications SET acknowledged_at='2026-10-09'")
        db.execute("INSERT INTO query_notes(query_id,note,action_type) VALUES (1,'Escalate','escalated')")
        db.execute("UPDATE queries SET status='pending_approval', approval_status='pending', approval_requested_at='2026-10-09 10:00:00' WHERE id=1")
        sync(db)
        db.execute("UPDATE staff_notifications SET acknowledged_at='2026-10-09'")
        db.execute("UPDATE queries SET approval_requested_at='2026-10-10 10:00:00' WHERE id=1")
        sync(db,10)
        rows=db.execute('SELECT * FROM staff_notifications WHERE resolved_at IS NULL').fetchall()
        assert len(rows)==2 and all(not r['acknowledged_at'] for r in rows)


def test_today_is_due_but_overdue_reminders_start_the_following_day(seeded):
    with seeded.app_context():
        db=get_db()
        db.execute("UPDATE queries SET follow_up_date='2026-10-09' WHERE id=1")
        sync(db)
        assert db.execute('SELECT SUM(reminder_count) FROM staff_notifications').fetchone()[0]==0
        sync(db,10)
        assert db.execute('SELECT SUM(reminder_count) FROM staff_notifications').fetchone()[0]==2


def test_reception_links_to_member_instead_of_manager_only_decisions(seeded,client):
    with seeded.app_context():
        db=get_db()
        db.execute("INSERT INTO collection_exceptions(member_id,reason,created_by) VALUES (1,'Approval needed',3)")
        db.commit()
    login(client,3,'reception')
    response=client.get('/notifications')
    assert response.status_code==200
    assert 'href="/members/1"' in response.text
    assert 'href="/collections/exceptions"' not in response.text


def test_acknowledgment_requires_csrf_in_real_configuration(seeded,client):
    with seeded.app_context():
        db=get_db(); sync(db)
        nid=db.execute('SELECT id FROM staff_notifications WHERE recipient_id=1').fetchone()[0]
    seeded.config['WTF_CSRF_ENABLED']=True
    login(client,1)
    assert client.post(f'/notifications/{nid}/acknowledge').status_code==400


def test_stale_other_gym_session_cannot_read_or_acknowledge(seeded,client):
    login(client,1)
    with client.session_transaction() as s:
        s['tenant_slug']='another-gym'
    assert client.get('/notifications/count',headers={'Content-Type':'application/json'}).status_code==401
    login(client,1)
    with client.session_transaction() as s:
        s['tenant_slug']='another-gym'
    assert client.post('/notifications/1/acknowledge',json={}).status_code==401


def test_application_approvals_notify_assigned_consultant_and_supervisor(seeded,client):
    with seeded.app_context():
        db=get_db()
        db.execute("INSERT INTO leads(id,full_name,assigned_to) VALUES (1,'Approval Test',1)")
        db.execute('INSERT INTO membership_applications(id,lead_id,created_by) VALUES (1,1,3)')
        db.execute('INSERT INTO approvals(application_id,requested_by) VALUES (1,3)')
        sync(db)
        rows=db.execute("SELECT * FROM staff_notifications WHERE source_kind='application' AND resolved_at IS NULL").fetchall()
        assert {r['recipient_id'] for r in rows}=={1,2}
        db.execute("UPDATE approvals SET status='approved'")
        sync(db,10)
        assert db.execute("SELECT COUNT(*) FROM staff_notifications WHERE source_kind='application' AND resolved_at IS NULL").fetchone()[0]==0


def test_ptp_without_exception_notifies_and_linked_exception_does_not_duplicate(seeded):
    with seeded.app_context():
        db=get_db()
        db.execute("INSERT INTO ptp_agreements(id,member_id,created_by,promise_date,payment_method,manager_approval_status) VALUES (1,1,3,'2026-10-20','cash','pending')")
        sync(db)
        assert db.execute("SELECT COUNT(*) FROM staff_notifications WHERE source_kind='ptp' AND resolved_at IS NULL").fetchone()[0]==2
        db.execute("INSERT INTO collection_exceptions(member_id,ptp_id,reason,created_by) VALUES (1,1,'Manager decision',3)")
        sync(db)
        assert db.execute("SELECT COUNT(*) FROM staff_notifications WHERE source_kind='ptp' AND resolved_at IS NULL").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM staff_notifications WHERE source_kind='collection' AND resolved_at IS NULL").fetchone()[0]==2


def test_background_job_creates_notifications_without_staff_opening_a_page(seeded):
    from onecpase.scheduler import _job_staff_notifications
    _job_staff_notifications(seeded)
    with seeded.app_context():
        rows=get_db().execute('SELECT recipient_id FROM staff_notifications WHERE resolved_at IS NULL').fetchall()
        assert {r[0] for r in rows}=={1,2}
