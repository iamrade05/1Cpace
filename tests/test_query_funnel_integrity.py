"""Regressions found while following real Reception/client handoffs."""
import json

from onecpase.database import get_db
from onecpase.encryption import encrypt
from onecpase.notifications import sync_notifications
from test_query_forms import seed, issue, submission


def test_expired_link_can_be_replaced_without_losing_the_query(app, client):
    _, qid = seed(app, client, 'complaint')
    old = issue(client, qid, 'complaint')
    with app.app_context():
        db = get_db()
        db.execute("UPDATE query_form_links SET expires_at='2020-01-01T00:00:00+00:00'")
        db.commit()
    assert client.get(old).status_code == 410
    replacement = issue(client, qid, 'complaint')
    assert replacement != old
    assert client.get(old).status_code == 410
    assert client.post(replacement, data=submission()).status_code == 302
    # Submission has handed the request to staff; it cannot be overwritten.
    assert client.post(f'/queries/{qid}/workflow', data={'action': 'issue'}).status_code == 409


def test_unsigned_supported_query_cannot_be_closed(app, client):
    _, qid = seed(app, client)
    response = client.post(f'/queries/{qid}/close', data={
        'closure_reason': 'resolved', 'resolution_notes': 'Attempted early closure', 'owing_ack': '1'})
    assert response.status_code == 302
    with app.app_context():
        assert get_db().execute('SELECT status FROM queries WHERE id=?', (qid,)).fetchone()[0] == 'open'


def test_priority_edit_preserves_workflow_owner(app, client):
    _, qid = seed(app, client, 'freeze')
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(2,'reviewer','x','Reviewer','manager',1)")
        db.execute("UPDATE queries SET assigned_to=2,department='Management' WHERE id=?", (qid,))
        db.execute("INSERT INTO query_workflows(query_id,kind,stage,owner_id,details,updated_at) VALUES(?,'freeze','pending_approval',2,?,'2026-10-29T10:00:00+02:00')",
                   (qid, encrypt(json.dumps({}))))
        db.commit()
    assert client.post(f'/queries/{qid}/update', data={'priority': 'high', 'assigned_to': '1', 'department': 'Admin'}).status_code == 302
    with app.app_context():
        q = get_db().execute('SELECT * FROM queries WHERE id=?', (qid,)).fetchone()
        assert q['assigned_to'] == 2 and q['department'] == 'Management'


def test_handoff_notifies_owner_before_ordinary_followup_date(app, client):
    _, qid = seed(app, client, 'freeze')
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(2,'reviewer','x','Reviewer','manager',1)")
        db.execute("UPDATE queries SET assigned_to=2,follow_up_date='2099-01-01' WHERE id=?", (qid,))
        db.execute("INSERT INTO query_workflows(query_id,kind,stage,owner_id,details,updated_at) VALUES(?,'freeze','pending_approval',2,?,'2026-10-29T10:00:00+02:00')",
                   (qid, encrypt('{}')))
        sync_notifications(db, enabled_kinds=['query'])
        sync_notifications(db, enabled_kinds=['query'])
        rows = db.execute("SELECT * FROM staff_notifications WHERE source_id=? AND recipient_id=2 AND event_key LIKE 'workflow:%'", (qid,)).fetchall()
        assert len(rows) == 1 and rows[0]['resolved_at'] is None
        db.execute("UPDATE query_workflows SET stage='complete' WHERE query_id=?", (qid,))
        sync_notifications(db, enabled_kinds=['query'])
        assert db.execute('SELECT resolved_at FROM staff_notifications WHERE id=?', (rows[0]['id'],)).fetchone()[0]
