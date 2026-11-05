import pytest

from onecpase.database import get_db


def sign_in(app, client, permissions=None):
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(1,'compose','x','Sender','staff',1)")
        for permission in permissions or ['reception_whatsapp']:
            db.execute('INSERT INTO user_permissions(user_id,permission) VALUES(1,?)',(permission,))
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=1, role='staff', permissions=permissions or ['reception_whatsapp'])


@pytest.mark.parametrize('number', ['reception', 'sales'])
def test_compose_button_and_form(app, client, number):
    sign_in(app, client, [number + '_whatsapp'])
    assert b'New message' in client.get('/communications/whatsapp/' + number).data
    response = client.get('/communications/whatsapp/' + number + '/new')
    assert response.status_code == 200
    assert b'name="phone"' in response.data and b'name="message"' in response.data


def test_compose_permissions(app, client, monkeypatch):
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message', lambda *args: pytest.fail('Unauthorized send'))
    path = '/communications/whatsapp/reception/new'
    assert client.post(path, data={'phone': '0821234567', 'message': 'Hello'}).status_code == 302
    sign_in(app, client, ['sales_whatsapp'])
    assert client.get(path).status_code == 302
    assert client.post(path, data={'phone': '0821234567', 'message': 'Hello'}).status_code == 302


@pytest.mark.parametrize('result,status', [({'queued': True}, 'queued'), ({'error': 'Not connected'}, 'failed'), ({'messages': [{'id': 'test-id'}]}, 'sent')])
def test_compose_records_sender_result(app, client, monkeypatch, result, status):
    sign_in(app, client)
    calls = []
    def send(*args):
        calls.append(args)
        return result
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message', send)
    response = client.post('/communications/whatsapp/reception/new', data={'phone': '082 123 4567', 'message': ' Hello '}, follow_redirects=True)
    assert response.status_code == 200
    assert calls == [('', '27821234567', 'Hello')]
    with app.app_context():
        row = get_db().execute('SELECT * FROM wa_messages').fetchone()
        assert row['status'] == status and row['sent_by_user_id'] == 1
    assert status.encode() in response.data


def test_validation_and_conversation_reuse(app, client, monkeypatch):
    sign_in(app, client)
    calls = []
    monkeypatch.setattr('onecpase.comms.send_whatsapp_message', lambda *args: calls.append(args) or {'queued': True})
    path = '/communications/whatsapp/reception/new'
    for phone, message in [('bad', 'Hello'), ('0821234567', ' '), ('0821234567', 'x' * 4097)]:
        assert client.post(path, data={'phone': phone, 'message': message}).status_code == 200
    assert calls == []
    for phone in ['0821234567', '+27 82 123 4567']:
        assert client.post(path, data={'phone': phone, 'message': 'Hello'}).status_code == 302
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM wa_conversations').fetchone()[0] == 1
        assert get_db().execute('SELECT COUNT(*) FROM wa_messages').fetchone()[0] == 2


def test_compose_uses_linked_browser_sender(app, client, monkeypatch):
    sign_in(app, client)
    from flask import g
    app.config.update(WHATSAPP_WEB_ENABLED=True, WA_TOKEN='', WA_RECEPTION_PHONE_ID='')
    @app.before_request
    def preview_sender_tenant():
        app.config.update(WHATSAPP_WEB_ENABLED=True, WA_TOKEN='', WA_RECEPTION_PHONE_ID='')
        app.config['WHATSAPP_WEB_TENANT'] = g.get('tenant_slug')
    queued = []
    monkeypatch.setattr('onecpase.whatsapp_web.get_status', lambda: {'started': True, 'logged_in': True})
    monkeypatch.setattr('onecpase.whatsapp_web.queue_message', lambda *args: queued.append(args))
    response = client.post('/communications/whatsapp/reception/new', data={'phone': '0821234567', 'message': 'Hello'})
    assert response.status_code == 302
    assert queued == [('27821234567', 'Hello')]
    with app.app_context():
        assert get_db().execute('SELECT status FROM wa_messages').fetchone()[0] == 'queued'

