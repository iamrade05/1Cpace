from onecpase.database import get_db
from onecpase.platform_db import get_platform_db
from onecpase.whatsapp_receive import save_incoming


def configure(app):
    app.config['WHATSAPP_WEB_TENANT'] = 'receive-test'
    with app.app_context():
        get_platform_db().execute("INSERT INTO tenants(slug,name,db_path,active) VALUES ('receive-test','Test',?,1)",
                                  (app.config['DATABASE_PATH'],))
        get_platform_db().commit()


def test_received_text_is_tenant_bound_and_deduplicated(app):
    configure(app)
    snapshot = {'phone': '+27 82 123 4567', 'name': 'Contact',
                'messages': [{'id': 'first', 'content': 'Hello'}, {'id': 'second', 'content': 'Question'}]}
    assert save_incoming(app, snapshot) == 2
    assert save_incoming(app, snapshot) == 0
    with app.app_context():
        conv = get_db().execute('SELECT * FROM wa_conversations').fetchone()
        assert conv['wa_number'] == 'reception' and conv['contact_phone'] == '27821234567'
        assert conv['unread_count'] == 2
        assert get_db().execute('SELECT count(*) FROM wa_messages WHERE direction=\'inbound\'').fetchone()[0] == 2


def test_existing_sales_chat_receives_reply(app):
    configure(app)
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO wa_conversations(wa_number,contact_phone) VALUES ('sales','27821234567')")
        db.commit()
    assert save_incoming(app, {'phone': '+27821234567', 'messages': [{'id': 'reply', 'content': 'Yes'}]}) == 1
    with app.app_context():
        rows = get_db().execute('SELECT * FROM wa_conversations').fetchall()
        assert len(rows) == 1 and rows[0]['wa_number'] == 'sales' and rows[0]['unread_count'] == 1


def test_groups_and_unresolved_contacts_are_not_misrouted(app):
    for phone in ['Group name', '', '123@g.us', 'Member 0821234567']:
        assert save_incoming(app, {'phone': phone, 'messages': [{'id': 'x', 'content': 'Hello'}]}) == 0


def test_unavailable_tenant_fails_closed(app):
    import pytest
    app.config['WHATSAPP_WEB_TENANT'] = 'missing'
    with pytest.raises(RuntimeError, match='tenant is unavailable'):
        save_incoming(app, {'phone': '+27821234567', 'messages': [{'id': 'x', 'content': 'Hello'}]})


def test_browser_extraction_distinguishes_incoming_text(monkeypatch):
    from playwright.sync_api import sync_playwright
    from onecpase.whatsapp_receive import READ_CHAT
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('''<div id="main">
                <header data-testid="conversation-info-header-chat-title">Saved Contact</header>
                <div data-id="in-1"><div data-testid="msg-container"><i data-testid="tail-in"></i>
                <div data-pre-plain-text="meta"><span class="selectable-text">Hello from member</span></div></div></div>
                <div data-id="out-1"><div data-testid="msg-container"><i data-icon="msg-dblcheck"></i>
                <div data-pre-plain-text="meta"><span class="selectable-text">Staff reply</span></div></div></div>
                </div><div data-testid="chat-info-drawer"><div data-testid="contact-info-header">Contact info</div>
                <div data-testid="contact-info-subtitle selectable-text">Saved Contact</div><div>+27 82 123 4567</div></div>''')
            snapshot = page.evaluate(READ_CHAT)
            assert snapshot['phone'] == '+27 82 123 4567'
            assert snapshot['messages'] == [{'id': 'in-1', 'content': 'Hello from member'}]
            page.evaluate('''() => {const d=document.createElement('div');d.setAttribute('role','dialog');
                d.innerHTML='What’s new on WhatsApp Web<button>Continue</button>';
                d.querySelector('button').onclick=()=>d.remove();
                document.body.append(d);}''')
            from onecpase.whatsapp_receive import poll_incoming
            monkeypatch.setattr('onecpase.whatsapp_receive.save_incoming', lambda app, value: len(value['messages']))
            assert poll_incoming(None, page) == 1
            assert page.locator('[role="dialog"]').count() == 0
            page.locator('[data-testid="contact-info-header"]').evaluate('(e) => e.remove()')
            assert page.evaluate(READ_CHAT) is None
        finally:
            browser.close()
