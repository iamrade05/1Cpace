"""Exact, tenant-local client phone matching for WhatsApp conversations."""
import re
from .encryption import decrypt
from .whatsapp_web import normalise_phone


def phone_key(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'\+?[0-9 ()-]{8,25}', value):
        return None
    return normalise_phone(value)


def identify_conversations(db, conversations):
    members = {}
    by_phone = {}
    for row in db.execute('SELECT id,first_name,last_name,contact,alternative_number,work_number,member_ref,member_status FROM members').fetchall():
        member = dict(row)
        member['full_name'] = ' '.join(str(decrypt(member[field]) or '').strip() for field in ('first_name', 'last_name')).strip()
        members[member['id']] = member
        for field in ('contact', 'alternative_number', 'work_number'):
            key = phone_key(decrypt(member[field]))
            if key:
                by_phone.setdefault(key, {})[member['id']] = member
    results = []
    for row in conversations:
        conv = dict(row)
        candidates = list(by_phone.get(phone_key(conv['contact_phone']), {}).values())
        if not conv['linked_member_id'] and not conv['linked_lead_id'] and len(candidates) == 1:
            member_id = candidates[0]['id']
            db.execute('UPDATE wa_conversations SET linked_member_id=? WHERE id=? AND linked_member_id IS NULL AND linked_lead_id IS NULL',
                       (member_id, conv['id']))
            # Re-read to preserve a manual link saved concurrently.
            linked = db.execute('SELECT linked_member_id,linked_lead_id FROM wa_conversations WHERE id=?', (conv['id'],)).fetchone()
            conv.update(dict(linked))
        conv['client'] = members.get(conv['linked_member_id'])
        conv['phone_match_count'] = len(candidates)
        results.append(conv)
    db.commit()
    return results
