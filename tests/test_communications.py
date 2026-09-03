import hashlib
import hmac
import json

from onecpase.comms import verify_fb_signature
from onecpase.database import get_db


def _login(client, permissions=None, role="staff"):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "testuser"
        session["full_name"] = "Test User"
        session["role"] = role
        session["permissions"] = permissions or []


# ── Signature verification (pure logic) ───────────────────────────────────────

def test_verify_fb_signature_accepts_anything_when_secret_unset(app):
    with app.app_context():
        app.config["FB_APP_SECRET"] = ""
        assert verify_fb_signature(b"anything", "") is True


def test_verify_fb_signature_rejects_wrong_signature_when_secret_set(app):
    with app.app_context():
        app.config["FB_APP_SECRET"] = "topsecret"
        assert verify_fb_signature(b"payload", "sha256=wrong") is False


def test_verify_fb_signature_accepts_correctly_signed_payload(app):
    with app.app_context():
        app.config["FB_APP_SECRET"] = "topsecret"
        body = b'{"entry": []}'
        sig = "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
        assert verify_fb_signature(body, sig) is True


# ── WhatsApp webhook ───────────────────────────────────────────────────────────

def test_whatsapp_webhook_verification_challenge(client, app):
    with app.app_context():
        app.config["WA_VERIFY_TOKEN"] = "my-verify-token"
    resp = client.get(
        "/webhooks/whatsapp",
        query_string={"hub.mode": "subscribe", "hub.verify_token": "my-verify-token", "hub.challenge": "12345"},
    )
    assert resp.status_code == 200
    assert resp.data == b"12345"


def test_whatsapp_webhook_verification_rejects_wrong_token(client, app):
    with app.app_context():
        app.config["WA_VERIFY_TOKEN"] = "my-verify-token"
    resp = client.get(
        "/webhooks/whatsapp",
        query_string={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"},
    )
    assert resp.status_code == 403


def test_whatsapp_webhook_ingests_inbound_text_message(client, app):
    with app.app_context():
        app.config["WA_RECEPTION_PHONE_ID"] = "1000"
    payload = {
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "1000"},
            "contacts": [{"profile": {"name": "Jane Doe"}}],
            "messages": [{"id": "wamid.1", "from": "27821234567", "type": "text",
                          "text": {"body": "Hi, is the gym open?"}, "timestamp": "1700000000"}],
        }}]}]
    }
    resp = client.post("/webhooks/whatsapp", json=payload)
    assert resp.status_code == 200

    with app.app_context():
        db = get_db()
        conv = db.execute("SELECT * FROM wa_conversations WHERE contact_phone='27821234567'").fetchone()
        assert conv is not None
        assert conv["wa_number"] == "reception"
        assert conv["contact_name"] == "Jane Doe"
        assert conv["unread_count"] == 1
        msg = db.execute("SELECT * FROM wa_messages WHERE conversation_id=?", (conv["id"],)).fetchone()
        assert msg["content"] == "Hi, is the gym open?"
        assert msg["direction"] == "inbound"


def test_whatsapp_webhook_ignores_unknown_phone_number_id(client, app):
    payload = {
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "unknown"},
            "messages": [{"id": "wamid.2", "from": "27821234567", "type": "text", "text": {"body": "hi"}}],
        }}]}]
    }
    resp = client.post("/webhooks/whatsapp", json=payload)
    assert resp.status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM wa_conversations").fetchone()[0] == 0


# ── Facebook webhook ───────────────────────────────────────────────────────────

def test_facebook_webhook_rejects_bad_signature_when_secret_configured(client, app):
    with app.app_context():
        app.config["FB_APP_SECRET"] = "topsecret"
    resp = client.post(
        "/webhooks/facebook",
        data=json.dumps({"entry": []}),
        content_type="application/json",
        headers={"X-Hub-Signature-256": "sha256=bogus"},
    )
    assert resp.status_code == 401


def test_facebook_webhook_ingests_comment(client, app):
    with app.app_context():
        app.config["FB_APP_SECRET"] = ""
    payload = {
        "entry": [{"changes": [{"field": "feed", "value": {
            "item": "comment", "verb": "add",
            "comment_id": "fb_c_1", "post_id": "post_1",
            "message": "How much is membership?",
            "from": {"name": "John Prospect", "id": "psid_1"},
        }}]}]
    }
    resp = client.post("/webhooks/facebook", json=payload)
    assert resp.status_code == 200
    with app.app_context():
        comment = get_db().execute("SELECT * FROM fb_comments WHERE fb_comment_id='fb_c_1'").fetchone()
        assert comment is not None
        assert comment["commenter_name"] == "John Prospect"
        assert comment["content"] == "How much is membership?"


# ── Permission gating on the staff UI ─────────────────────────────────────────

def test_whatsapp_inbox_denies_user_without_permission(client):
    _login(client, permissions=[])
    resp = client.get("/communications/whatsapp/reception", follow_redirects=True)
    assert b"do not have access" in resp.data.lower()


def test_whatsapp_inbox_allows_user_with_permission(client):
    _login(client, permissions=["reception_whatsapp"])
    resp = client.get("/communications/whatsapp/reception")
    assert resp.status_code == 200


def test_admin_bypasses_all_communications_permissions(client):
    _login(client, permissions=[], role="admin")
    resp = client.get("/communications/facebook/comments")
    assert resp.status_code == 200


def test_facebook_comment_flag_toggles_and_redirects_to_the_list(client, app):
    _login(client, permissions=["facebook_comments"])
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO fb_comments (fb_comment_id, commenter_name, content, timestamp) "
            "VALUES ('fb_c_flag', 'Flag Test', 'hi', datetime('now'))"
        )
        db.commit()
        comment_id = db.execute("SELECT id FROM fb_comments WHERE fb_comment_id='fb_c_flag'").fetchone()["id"]

    resp = client.post(f"/communications/facebook/comments/{comment_id}/flag")
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/communications/facebook/comments")

    with app.app_context():
        comment = get_db().execute("SELECT is_interested FROM fb_comments WHERE id=?", (comment_id,)).fetchone()
        assert comment["is_interested"] == 1


def test_whatsapp_conversation_and_link_pages_render(client, app):
    _login(client, permissions=["reception_whatsapp"])
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO wa_conversations (wa_number, contact_phone, contact_name) VALUES ('reception', '27821111111', 'Test Contact')"
        )
        db.commit()
        conv_id = db.execute("SELECT id FROM wa_conversations").fetchone()["id"]

    resp = client.get(f"/communications/whatsapp/reception/chat/{conv_id}")
    assert resp.status_code == 200
    resp = client.get(f"/communications/whatsapp/reception/chat/{conv_id}/link-lead")
    assert resp.status_code == 200


def test_facebook_messages_inbox_and_conversation_render(client, app):
    _login(client, permissions=["facebook_messages"])
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO fb_conversations (sender_psid, sender_name) VALUES ('psid_9', 'FB Contact')")
        db.commit()
        conv_id = db.execute("SELECT id FROM fb_conversations").fetchone()["id"]

    resp = client.get("/communications/facebook/messages")
    assert resp.status_code == 200
    resp = client.get(f"/communications/facebook/messages/{conv_id}")
    assert resp.status_code == 200


def test_communications_index_redirects_to_first_available_inbox(client):
    _login(client, permissions=["facebook_comments"])
    resp = client.get("/communications", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/communications/facebook/comments")


# ── Comment-to-lead conversion plugs into the real leads pipeline ─────────────

def test_create_lead_from_comment_inserts_a_real_lead(client, app):
    _login(client, permissions=["facebook_comments", "capture_leads"])
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO fb_comments (fb_comment_id, commenter_name, commenter_id, content, timestamp) "
            "VALUES ('fb_c_2', 'Alice Prospect', 'psid_2', 'Interested in joining!', datetime('now'))"
        )
        db.commit()
        comment_id = db.execute("SELECT id FROM fb_comments WHERE fb_comment_id='fb_c_2'").fetchone()["id"]

    resp = client.post(f"/communications/facebook/comments/{comment_id}/create-lead")
    assert resp.status_code == 302

    with app.app_context():
        db = get_db()
        comment = db.execute("SELECT * FROM fb_comments WHERE id=?", (comment_id,)).fetchone()
        assert comment["linked_lead_id"] is not None
        assert comment["is_interested"] == 1
        lead = db.execute("SELECT * FROM leads WHERE id=?", (comment["linked_lead_id"],)).fetchone()
        assert lead["full_name"] == "Alice Prospect"
        assert lead["source"] == "Social Media"
        assert lead["campaign"] == "Facebook"
        assert lead["lead_channel"] == "outreach"


def test_create_lead_from_comment_reuses_lead_for_repeat_commenter(client, app):
    _login(client, permissions=["facebook_comments", "capture_leads"])
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO fb_comments (fb_comment_id, commenter_name, commenter_id, content, timestamp) "
            "VALUES ('fb_c_3', 'Bob Prospect', 'psid_3', 'first comment', datetime('now'))"
        )
        db.execute(
            "INSERT INTO fb_comments (fb_comment_id, commenter_name, commenter_id, content, timestamp) "
            "VALUES ('fb_c_4', 'Bob Prospect', 'psid_3', 'second comment', datetime('now'))"
        )
        db.commit()
        first_id = db.execute("SELECT id FROM fb_comments WHERE fb_comment_id='fb_c_3'").fetchone()["id"]
        second_id = db.execute("SELECT id FROM fb_comments WHERE fb_comment_id='fb_c_4'").fetchone()["id"]

    client.post(f"/communications/facebook/comments/{first_id}/create-lead")
    client.post(f"/communications/facebook/comments/{second_id}/create-lead")

    with app.app_context():
        db = get_db()
        first_lead = db.execute("SELECT linked_lead_id FROM fb_comments WHERE id=?", (first_id,)).fetchone()["linked_lead_id"]
        second_lead = db.execute("SELECT linked_lead_id FROM fb_comments WHERE id=?", (second_id,)).fetchone()["linked_lead_id"]
        assert first_lead == second_lead
        assert db.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
