"""Receive recent direct-chat text from the linked browser's visible UI."""
import re
from datetime import datetime, timezone

from flask import g

OPEN_UNREAD = r'''(() => {
 const row = Array.from(document.querySelectorAll('#pane-side [role="row"]')).find(
   e => e.querySelector('[aria-label*="unread" i]'));
 if (!row) return false;
 (row.querySelector('[data-testid="cell-frame-container"]') || row).click();
 return true;
})()'''

READ_CHAT = r'''(() => {
 const main=document.querySelector('#main');
 if (!main) return null;
 const drawer=document.querySelector('[data-testid="chat-info-drawer"]');
 if (!drawer?.querySelector('[data-testid="contact-info-header"]')) return null;
 const subtitle=drawer?.querySelector('[data-testid="contact-info-subtitle selectable-text"]');
 const header=main.querySelector('[data-testid="conversation-info-header-chat-title"]');
 const phoneLine=(drawer?.innerText || '').split('\n').map(s=>s.trim()).find(s=>/^\+\d[\d ()-]{7,}$/.test(s));
 const phone=(phoneLine || subtitle?.innerText || header?.innerText || '').trim();
 const name=(header?.innerText || '').trim();
 const messages=[];
 for (const row of main.querySelectorAll('[data-id]')) {
   const bubble=row.querySelector('[data-testid="msg-container"]');
   if (!bubble) continue;
   const outgoing=!!row.querySelector('[data-testid="tail-out"], [data-icon="msg-check"], [data-icon="msg-dblcheck"], [data-icon="msg-time"]');
   const incoming=!!row.querySelector('[data-testid="tail-in"]');
   const rect=bubble.getBoundingClientRect(), panel=main.getBoundingClientRect();
   if (outgoing || (!incoming && rect.left+rect.width/2 >= panel.left+panel.width/2)) continue;
   const text=Array.from(row.querySelectorAll('[data-pre-plain-text] [data-testid="selectable-text"], [data-pre-plain-text] .selectable-text'));
   const content=Array.from(new Set(text)).map(e=>e.innerText).join('\n').trim();
   if (content) messages.push({id:row.getAttribute('data-id'),content});
 }
 return {phone,name,messages};
})()'''


def save_incoming(app, snapshot):
    """Bind exclusively to the linked tenant; duplicates never inflate unread."""
    if not snapshot or not re.fullmatch(r'\+?[\d ()-]{8,25}', snapshot.get('phone', '')):
        return 0
    from .whatsapp_web import normalise_phone
    from .database import get_db
    from .platform_db import get_platform_db
    phone = normalise_phone(snapshot['phone'])
    if not phone:
        return 0
    with app.app_context():
        tenant = get_platform_db().execute(
            'SELECT * FROM tenants WHERE slug=? AND active=1',
            (app.config.get('WHATSAPP_WEB_TENANT', 'elev8'),),
        ).fetchone()
        if tenant is None:
            raise RuntimeError('The linked WhatsApp tenant is unavailable')
        g.tenant = dict(tenant)
        g.tenant_slug = tenant['slug']
        db = get_db()
        saved = 0
        for message in snapshot.get('messages', []):
            message_id = str(message.get('id') or '')
            content = str(message.get('content') or '').strip()
            if not message_id or not content:
                continue
            key = 'web:' + message_id
            if db.execute('SELECT id FROM wa_messages WHERE wa_message_id=?', (key,)).fetchone():
                continue
            # Keep a single direct-chat inbox for the one linked account.
            conv = db.execute(
                'SELECT id FROM wa_conversations WHERE contact_phone=? ORDER BY last_message_at DESC, id DESC LIMIT 1',
                (phone,),
            ).fetchone()
            if conv is None:
                db.execute("INSERT OR IGNORE INTO wa_conversations (wa_number,contact_phone,contact_name) VALUES ('reception',?,?)",
                           (phone, snapshot.get('name') or phone))
                conv = db.execute("SELECT id FROM wa_conversations WHERE wa_number='reception' AND contact_phone=?", (phone,)).fetchone()
            stamp = datetime.now(timezone.utc).isoformat()
            cur = db.execute(
                "INSERT OR IGNORE INTO wa_messages (conversation_id,wa_message_id,direction,message_type,content,status,timestamp) VALUES (?,?,'inbound','text',?,'received',?)",
                (conv['id'], key, content, stamp),
            )
            if cur.rowcount:
                db.execute('UPDATE wa_conversations SET last_message_at=?, unread_count=COALESCE(unread_count,0)+1 WHERE id=?',
                           (stamp, conv['id']))
                saved += 1
        db.commit()
        return saved


def poll_incoming(app, page):
    """Only called on the browser worker thread, between outgoing sends."""
    dismissed = page.evaluate(r'''(() => {
      const dialog=document.querySelector('[role="dialog"]');
      if (!dialog || !/^What[’']s new on WhatsApp Web/.test(dialog.innerText)) return false;
      const button=Array.from(dialog.querySelectorAll('button')).find(e=>e.innerText.trim()==='Continue');
      if (!button) return false;
      button.click();return true;
    })()''')
    if dismissed:
        page.wait_for_timeout(500)
    # Opening unread chats also marks them read on the linked phone.
    unread = page.locator('#pane-side [role="row"]:has([aria-label*="unread" i])').first
    opened = unread.is_visible()
    if opened:
        unread.click(timeout=3000)
        page.wait_for_timeout(1000)
    if not page.evaluate("!!document.querySelector('#main [data-id]')"):
        return 0
    page.evaluate("(() => {if(!document.querySelector('[data-testid=\"chat-info-drawer\"]')) document.querySelector('#main [data-testid=\"conversation-info-header-chat-title\"]')?.click();return true})()")
    page.wait_for_timeout(500)
    try:
        return save_incoming(app, page.evaluate(READ_CHAT))
    finally:
        page.evaluate("(() => {const d=document.querySelector('[data-testid=\"chat-info-drawer\"]');const b=d?.querySelector('[data-icon=\"x\"], [data-icon=\"x-viewer\"], [data-testid=\"btn-closer-drawer\"]');b?.closest('button, [role=\"button\"]')?.click();return true})()")


def record_outgoing_result(app, phone, text, status):
    """Update app history after the browser submits a queued message."""
    from .platform_db import get_platform_db
    from .database import get_db
    with app.app_context():
        tenant = get_platform_db().execute('SELECT * FROM tenants WHERE slug=? AND active=1',
            (app.config.get('WHATSAPP_WEB_TENANT', 'elev8'),)).fetchone()
        if not tenant:
            return
        g.tenant = dict(tenant)
        db = get_db()
        message = db.execute("""SELECT m.id FROM wa_messages m JOIN wa_conversations c ON c.id=m.conversation_id
            WHERE c.contact_phone=? AND m.content=? AND m.direction='outbound' AND m.status='queued'
            ORDER BY m.id LIMIT 1""", (phone, text)).fetchone()
        if not message:
            return
        db.execute('UPDATE wa_messages SET status=? WHERE id=?', (status, message['id']))
        from .phases import is_enabled
        if is_enabled('fitness_journey',app.config.get('ACTIVE_PHASE',1)):
            from .fitness_journey import ensure_journey
            ensure_journey(db)
            db.execute('UPDATE fitness_intake SET welcome_status=? WHERE welcome_message_id=?', (status,message['id']))
        logs = db.execute("SELECT * FROM collection_communications WHERE channel='WHATSAPP' AND status='queued' AND notes LIKE ?",
                          ('%; message #' + str(message['id']),)).fetchall()
        for log in logs:
            db.execute('UPDATE collection_communications SET status=? WHERE id=?', (status, log['id']))
            if status == 'sent' and log['task_id']:
                db.execute('UPDATE collection_call_tasks SET whatsapp_sent=1 WHERE id=?', (log['task_id'],))
            reminder = re.fullmatch(r'App WhatsApp reminder #(\d+); message #\d+', log['notes'] or '')
            if status == 'sent' and reminder:
                db.execute("UPDATE collection_reminders SET status='sent',channel='WHATSAPP' WHERE id=? AND status='pending'", (int(reminder[1]),))
        db.commit()
