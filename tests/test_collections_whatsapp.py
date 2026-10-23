from datetime import date
from onecpase.database import get_db


def seed(app, client):
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,is_collections_caller) VALUES(1,'sender','x','Caller','admin',1)")
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number,contact) VALUES('Test','Client','coll-wa','0821234567')").lastrowid
        tid=db.execute("INSERT INTO collection_call_tasks(member_id,assigned_to,task_date,queue_type,status,next_channel) VALUES(?,1,?,'failed_debit','completed','WHATSAPP')",(mid,date.today().isoformat())).lastrowid
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=1,role='admin',permissions=['collections_call_queue'])
    return mid,tid


def test_collection_sends_via_reception_and_prevents_duplicate(app,client,monkeypatch):
    mid,tid=seed(app,client);calls=[]
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message',lambda *args:calls.append(args) or {'queued':True})
    path=f'/collections/whatsapp/task/{tid}'
    assert client.get(path).status_code==200
    assert client.post(path,data={'message':'Please contact us'}).status_code==302
    assert client.post(path,data={'message':'Please contact us'}).status_code==200
    assert calls==[('', '27821234567','Please contact us')]
    with app.app_context():
        db=get_db()
        assert db.execute('SELECT status FROM collection_communications').fetchone()[0]=='queued'
        assert not db.execute('SELECT whatsapp_sent FROM collection_call_tasks WHERE id=?',(tid,)).fetchone()[0]
        assert db.execute('SELECT linked_member_id FROM wa_conversations').fetchone()[0]==mid


def test_wrong_channel_and_wrong_caller_do_not_send(app,client,monkeypatch):
    mid,tid=seed(app,client)
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message',lambda *args: (_ for _ in ()).throw(AssertionError('Must not send')))
    with app.app_context():
        db=get_db();db.execute("UPDATE collection_call_tasks SET next_channel='SMS' WHERE id=?",(tid,));db.commit()
    assert client.post(f'/collections/whatsapp/task/{tid}',data={'message':'Hello'}).status_code==302
    with app.app_context():
        db=get_db();db.execute("UPDATE collection_call_tasks SET next_channel='WHATSAPP' WHERE id=?",(tid,));db.commit()
    with client.session_transaction() as session:
        session.update(user_id=999,role='staff',permissions=['collections_call_queue'])
    assert client.post(f'/collections/whatsapp/task/{tid}',data={'message':'Hello'}).status_code==403


def test_browser_submission_updates_collection_status(app,client,monkeypatch):
    mid,tid=seed(app,client)
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message',lambda *args:{'queued':True})
    client.post(f'/collections/whatsapp/task/{tid}',data={'message':'Hello'})
    from onecpase.platform_db import get_platform_db
    from onecpase.whatsapp_receive import record_outgoing_result
    app.config['WHATSAPP_WEB_TENANT']='collection-test'
    with app.app_context():
        db=get_platform_db();db.execute("INSERT INTO tenants(slug,name,db_path,active) VALUES('collection-test','Test',?,1)",(app.config['DATABASE_PATH'],));db.commit()
    record_outgoing_result(app,'27821234567','Hello','sent')
    with app.app_context():
        db=get_db()
        assert db.execute('SELECT status FROM wa_messages').fetchone()[0]=='sent'
        assert db.execute('SELECT status FROM collection_communications').fetchone()[0]=='sent'
        assert db.execute('SELECT whatsapp_sent FROM collection_call_tasks WHERE id=?',(tid,)).fetchone()[0]==1


def test_reminder_sends_without_recording_a_call(app,client,monkeypatch):
    from datetime import timedelta
    mid,tid=seed(app,client);day=date(2026,10,16)
    monkeypatch.setattr('onecpase.collections._collections_today',lambda:day)
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message',lambda *args:{'messages':[{'id':'reminder-test'}]})
    with app.app_context():
        db=get_db()
        rid=db.execute("INSERT INTO collection_reminders(member_id,due_date,reminder_date,cycle_group) VALUES(?,?,?,'month-end')",
                       (mid,(day+timedelta(days=2)).isoformat(),day.isoformat())).lastrowid
        db.commit()
    assert client.post(f'/collections/whatsapp/reminder/{rid}',data={'message':'Reminder'}).status_code==302
    with app.app_context():
        db=get_db()
        assert db.execute('SELECT status FROM collection_reminders WHERE id=?',(rid,)).fetchone()[0]=='sent'
        assert db.execute('SELECT count(*) FROM collection_call_tasks').fetchone()[0]==1

def test_legacy_voicemail_can_send_from_call_page(app,client,monkeypatch):
    mid,tid=seed(app,client);calls=[]
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message',lambda *args:calls.append(args) or {'queued':True})
    with app.app_context():
        db=get_db();db.execute("UPDATE collection_call_tasks SET next_channel='CALL',outcome='voicemail' WHERE id=?",(tid,));db.commit()
    from flask import url_for
    with app.test_request_context():
        path=url_for('collections.call_profile',task_id=tid)
    response=client.get(path)
    assert response.status_code==200
    page=response.get_data(as_text=True)
    assert 'Send WhatsApp from Reception' in page
    assert 'Mark WhatsApp sent' not in page
    assert client.post(f'/collections/whatsapp/task/{tid}',data={'message':'Hello'}).status_code==302
    assert len(calls)==1
    page=client.get(path).get_data(as_text=True)
    assert 'WhatsApp: queued' in page
    with app.app_context():
        assert get_db().execute('SELECT next_channel FROM collection_call_tasks WHERE id=?',(tid,)).fetchone()[0]=='WHATSAPP'
