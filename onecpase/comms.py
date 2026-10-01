"""WhatsApp (Meta Graph API) + Facebook Messenger/comments — inbound webhooks,
staff inboxes, and comment-to-lead conversion.

Ported from the reference 1Cpace app's /webhooks and /communications routes
(Phase 2 of the 1Cpase rewrite), adapted to this app's own blueprint,
permission-decorator, and leads-pipeline conventions rather than copied
verbatim — 1Cpace's leads table has a different shape from this app's, so
comment-to-lead conversion reuses onecpase.leads' own duplicate-detection and
consultant-allocation helpers instead of a raw INSERT.

Leave the WA_*/FB_* environment variables unset to keep this structurally
present but inert: webhook verification fails closed (wrong/missing token),
and sends report "not configured" instead of raising.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for

from .auth import permission_required
from .database import get_db
from .extensions import csrf
from .leads import _add_activity, _allocate_sales_consultant
from .platform_db import resolve_integration_tenant

try:
    import requests
except ImportError:  # pragma: no cover - requests is a core dependency
    requests = None


comms_bp = Blueprint("comms", __name__, url_prefix="/communications")
webhooks_bp = Blueprint("comms_webhooks", __name__, url_prefix="/webhooks")


def _claim_webhook_event(db, provider: str, event_key: str, event_type: str, payload) -> bool:
    """Atomically claim an event; business side effects must occur in the same transaction."""
    if not event_key:
        return False
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    cur = db.execute(
        "INSERT OR IGNORE INTO webhook_events(provider,event_key,event_type,payload_hash) VALUES (?,?,?,?)",
        (provider, str(event_key), event_type, payload_hash),
    )
    return cur.rowcount == 1


def _bind_webhook_tenant(provider: str, external_id: str) -> bool:
    """Bind g.tenant from provider identity, never from browser state."""
    tenant = resolve_integration_tenant(provider, external_id)
    if not tenant:
        return False
    # Public webhooks have no trusted browser tenant. Replace the cookie-derived
    # context with the tenant proven by the provider integration registry. If a
    # single provider request contains events for multiple registered tenants,
    # close the previous tenant DB before switching so get_db() cannot reuse a
    # connection belonging to the wrong tenant.
    current_slug = g.get("tenant_slug")
    if current_slug != tenant["slug"] and "db" in g:
        db = g.pop("db", None)
        if db is not None:
            db.close()
    g.tenant = tenant
    g.tenant_slug = tenant["slug"]
    return True


# ── Outbound send helpers ─────────────────────────────────────────────────────

def _meta_post(url: str, payload: dict, token: str) -> dict:
    if requests is None:
        return {"error": "requests library not installed"}
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


def send_whatsapp_message(phone_number_id: str, to: str, text: str) -> dict:
    token = current_app.config.get("WA_TOKEN", "")
    if not token or not phone_number_id:
        return {"error": "WhatsApp API not configured"}
    to_clean = "".join(ch for ch in to if ch.isdigit())
    return _meta_post(
        f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
        {
            "messaging_product": "whatsapp",
            "to": to_clean,
            "type": "text",
            "text": {"body": text},
        },
        token,
    )


def send_facebook_reply(recipient_psid: str, text: str) -> dict:
    token = current_app.config.get("FB_PAGE_ACCESS_TOKEN", "")
    if not token:
        return {"error": "Facebook API not configured"}
    if requests is None:
        return {"error": "requests library not installed"}
    try:
        resp = requests.post(
            "https://graph.facebook.com/v19.0/me/messages",
            params={"access_token": token},
            json={"recipient": {"id": recipient_psid}, "message": {"text": text}},
            timeout=10,
        )
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


def verify_meta_signature(payload_bytes: bytes, sig_header: str) -> bool:
    """Verify Meta's X-Hub-Signature-256 HMAC, failing closed without a secret."""
    secret = str(current_app.config.get("FB_APP_SECRET", "") or "").strip()
    if not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header or "")


# Retain the previous helper name for internal callers that imported it.
verify_fb_signature = verify_meta_signature


# ── Webhooks (public, CSRF-exempt, no login) ──────────────────────────────────

@webhooks_bp.route("/whatsapp", methods=["GET", "POST"])
@csrf.exempt
def whatsapp_webhook():
    if request.method == "GET":
        verify_token = current_app.config.get("WA_VERIFY_TOKEN", "")
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and verify_token and token == verify_token:
            return challenge, 200
        return "Forbidden", 403

    raw_body = request.get_data()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not verify_meta_signature(raw_body, sig):
        return "Unauthorized", 401

    data = request.get_json(silent=True) or {}

    db = None
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
                if not _bind_webhook_tenant("whatsapp", phone_number_id):
                    current_app.logger.warning("Ignoring WhatsApp webhook for unknown phone_number_id")
                    continue
                db = get_db()
                reception_id = current_app.config.get("WA_RECEPTION_PHONE_ID", "")
                sales_id = current_app.config.get("WA_SALES_PHONE_ID", "")
                if phone_number_id == reception_id:
                    wa_number = "reception"
                elif phone_number_id == sales_id:
                    wa_number = "sales"
                else:
                    # A tenant-specific phone ID is known, but its logical inbox
                    # name is not configured in this legacy schema. Keep the
                    # event tenant-bound rather than guessing another tenant.
                    wa_number = "whatsapp"

                contacts = value.get("contacts", [])
                contact_name = contacts[0].get("profile", {}).get("name", "") if contacts else ""

                for message in value.get("messages", []):
                    wa_msg_id = message.get("id")
                    from_phone = message.get("from", "")
                    msg_type = message.get("type", "text")
                    raw_ts = message.get("timestamp", "")
                    try:
                        ts = datetime.fromtimestamp(int(raw_ts)).isoformat() if raw_ts else datetime.now().isoformat()
                    except (ValueError, OSError):
                        ts = datetime.now().isoformat()

                    if msg_type == "text":
                        content = message.get("text", {}).get("body", "")
                    elif msg_type in ("image", "video", "audio", "document", "sticker"):
                        content = f"[{msg_type.upper()} received]"
                    else:
                        content = f"[{msg_type} message]"

                    event_key = str(wa_msg_id or "")
                    if not _claim_webhook_event(db, "whatsapp", event_key, "message", message):
                        continue
                    db.execute(
                        """
                        INSERT INTO wa_conversations (wa_number, contact_phone, contact_name, last_message_at, unread_count)
                        VALUES (?, ?, ?, ?, 1)
                        ON CONFLICT(wa_number, contact_phone) DO UPDATE SET
                            contact_name = COALESCE(excluded.contact_name, contact_name),
                            last_message_at = excluded.last_message_at,
                            unread_count = unread_count + 1
                        """,
                        (wa_number, from_phone, contact_name, ts),
                    )
                    conv = db.execute(
                        "SELECT id FROM wa_conversations WHERE wa_number=? AND contact_phone=?",
                        (wa_number, from_phone),
                    ).fetchone()
                    if conv:
                        db.execute(
                            """
                            INSERT INTO wa_messages
                            (conversation_id, wa_message_id, direction, message_type, content, timestamp)
                            VALUES (?, ?, 'inbound', ?, ?, ?)
                            """,
                            (conv["id"], wa_msg_id, msg_type, content, ts),
                        )
        # db stays None when every entry was for an unregistered phone id.
        if db is not None:
            db.commit()
    except Exception:
        current_app.logger.exception("Failed to process inbound WhatsApp webhook")

    return jsonify({"status": "ok"}), 200


@webhooks_bp.route("/facebook", methods=["GET", "POST"])
@csrf.exempt
def facebook_webhook():
    if request.method == "GET":
        verify_token = current_app.config.get("FB_VERIFY_TOKEN", "")
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and verify_token and token == verify_token:
            return challenge, 200
        return "Forbidden", 403

    raw_body = request.get_data()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not verify_meta_signature(raw_body, sig):
        return "Unauthorized", 401

    data = request.get_json(silent=True) or {}

    db = None
    try:
        for entry in data.get("entry", []):
            page_id = str(entry.get("id", "") or "")
            if not _bind_webhook_tenant("facebook", page_id):
                current_app.logger.warning("Ignoring Facebook webhook for unknown page id")
                continue
            db = get_db()
            for messaging in entry.get("messaging", []):
                sender_psid = messaging.get("sender", {}).get("id", "")
                if not sender_psid or sender_psid == page_id:
                    continue
                message = messaging.get("message", {})
                fb_msg_id = message.get("mid", "")
                if fb_msg_id and not _claim_webhook_event(db, "facebook", str(fb_msg_id), "message", messaging):
                    continue
                content = message.get("text", "")
                raw_ts = messaging.get("timestamp", 0)
                try:
                    ts = datetime.fromtimestamp(raw_ts / 1000).isoformat() if raw_ts else datetime.now().isoformat()
                except (ValueError, OSError):
                    ts = datetime.now().isoformat()

                existing = db.execute(
                    "SELECT id FROM fb_conversations WHERE sender_psid=?", (sender_psid,)
                ).fetchone()
                if existing is None:
                    db.execute(
                        "INSERT INTO fb_conversations (sender_psid, page_id, last_message_at, unread_count) VALUES (?,?,?,1)",
                        (sender_psid, page_id, ts),
                    )
                else:
                    db.execute(
                        "UPDATE fb_conversations SET last_message_at=?, unread_count=unread_count+1 WHERE sender_psid=?",
                        (ts, sender_psid),
                    )
                conv = db.execute(
                    "SELECT id FROM fb_conversations WHERE sender_psid=?", (sender_psid,)
                ).fetchone()
                if conv and fb_msg_id:
                    db.execute(
                        "INSERT OR IGNORE INTO fb_messages (conversation_id, fb_message_id, direction, content, timestamp) VALUES (?,?,'inbound',?,?)",
                        (conv["id"], fb_msg_id, content, ts),
                    )

            for change in entry.get("changes", []):
                if change.get("field") == "feed":
                    val = change.get("value", {})
                    if val.get("item") == "comment" and val.get("verb") == "add":
                        comment_id = val.get("comment_id", "")
                        post_id = val.get("post_id", "")
                        msg = val.get("message", "")
                        commenter_name = val.get("from", {}).get("name", "")
                        commenter_id = val.get("from", {}).get("id", "")
                        raw_ct = val.get("created_time", 0)
                        try:
                            ts = datetime.fromtimestamp(int(raw_ct)).isoformat() if raw_ct else datetime.now().isoformat()
                        except (ValueError, OSError):
                            ts = datetime.now().isoformat()
                        if comment_id and _claim_webhook_event(db, "facebook", str(comment_id), "comment", val):
                            db.execute(
                                "INSERT INTO fb_comments (fb_comment_id, post_id, commenter_name, commenter_id, content, timestamp) VALUES (?,?,?,?,?,?)",
                                (comment_id, post_id, commenter_name, commenter_id, msg, ts),
                            )
        # db stays None when every entry was for an unregistered page id.
        if db is not None:
            db.commit()
    except Exception:
        current_app.logger.exception("Failed to process inbound Facebook webhook")

    return jsonify({"status": "ok"}), 200


# ── Staff UI (authenticated) ──────────────────────────────────────────────────

@comms_bp.get("")
def communications_index():
    if session.get("role") == "admin" or "reception_whatsapp" in session.get("permissions", []):
        return redirect(url_for("comms.whatsapp_inbox", number="reception"))
    if session.get("role") == "admin" or "sales_whatsapp" in session.get("permissions", []):
        return redirect(url_for("comms.whatsapp_inbox", number="sales"))
    if session.get("role") == "admin" or "facebook_messages" in session.get("permissions", []):
        return redirect(url_for("comms.facebook_messages_inbox"))
    if session.get("role") == "admin" or "facebook_comments" in session.get("permissions", []):
        return redirect(url_for("comms.facebook_comments_view"))
    flash("You do not have access to Communications.", "warning")
    return redirect(url_for("dashboard.dashboard"))


def _wa_permission(number: str) -> str:
    return "reception_whatsapp" if number == "reception" else "sales_whatsapp"


@comms_bp.get("/whatsapp/<number>")
def whatsapp_inbox(number: str):
    if number not in ("reception", "sales"):
        return redirect(url_for("comms.communications_index"))
    if session.get("role") != "admin" and _wa_permission(number) not in session.get("permissions", []):
        flash("You do not have access to that WhatsApp inbox.", "warning")
        return redirect(url_for("comms.communications_index"))
    db = get_db()
    conversations = db.execute(
        """
        SELECT c.*, MAX(m.content) AS last_content
        FROM wa_conversations c
        LEFT JOIN wa_messages m ON m.conversation_id = c.id
        WHERE c.wa_number = ?
        GROUP BY c.id
        ORDER BY c.last_message_at DESC
        """,
        (number,),
    ).fetchall()
    display_key = "WA_RECEPTION_DISPLAY" if number == "reception" else "WA_SALES_DISPLAY"
    wa_phone = current_app.config.get(display_key, "")
    return render_template(
        "communications/whatsapp_inbox.html",
        conversations=conversations,
        number=number,
        wa_phone=wa_phone,
    )


@comms_bp.route("/whatsapp/<number>/chat/<int:conv_id>", methods=["GET", "POST"])
def whatsapp_conversation(number: str, conv_id: int):
    if number not in ("reception", "sales"):
        return redirect(url_for("comms.communications_index"))
    if session.get("role") != "admin" and _wa_permission(number) not in session.get("permissions", []):
        flash("Access denied.", "warning")
        return redirect(url_for("comms.communications_index"))
    db = get_db()
    conv = db.execute("SELECT * FROM wa_conversations WHERE id=? AND wa_number=?", (conv_id, number)).fetchone()
    if conv is None:
        flash("Conversation not found.", "error")
        return redirect(url_for("comms.whatsapp_inbox", number=number))

    if request.method == "POST":
        text = request.form.get("message", "").strip()
        if text:
            phone_key = "WA_RECEPTION_PHONE_ID" if number == "reception" else "WA_SALES_PHONE_ID"
            phone_id = current_app.config.get(phone_key, "")
            result = send_whatsapp_message(phone_id, conv["contact_phone"], text)
            ts = datetime.now().isoformat()
            wa_msg_id = result.get("messages", [{}])[0].get("id") if isinstance(result.get("messages"), list) else None
            db.execute(
                "INSERT INTO wa_messages (conversation_id, wa_message_id, direction, message_type, content, status, sent_by_user_id, timestamp) VALUES (?,?,?, 'text',?,'sent',?,?)",
                (conv_id, wa_msg_id, "outbound", text, session.get("user_id"), ts),
            )
            db.execute("UPDATE wa_conversations SET last_message_at=? WHERE id=?", (ts, conv_id))
            db.commit()
            if "error" in result:
                flash(f"Message saved but send failed: {result['error']}", "warning")
        return redirect(url_for("comms.whatsapp_conversation", number=number, conv_id=conv_id))

    db.execute("UPDATE wa_conversations SET unread_count=0 WHERE id=?", (conv_id,))
    db.commit()
    messages = db.execute(
        "SELECT * FROM wa_messages WHERE conversation_id=? ORDER BY timestamp ASC, id ASC",
        (conv_id,),
    ).fetchall()
    return render_template("communications/whatsapp_conversation.html", conv=conv, messages=messages, number=number)


@comms_bp.route("/whatsapp/<number>/chat/<int:conv_id>/link-lead", methods=["GET", "POST"])
def whatsapp_link_lead(number: str, conv_id: int):
    if number not in ("reception", "sales"):
        return redirect(url_for("comms.communications_index"))
    if session.get("role") != "admin" and _wa_permission(number) not in session.get("permissions", []):
        flash("Access denied.", "warning")
        return redirect(url_for("comms.communications_index"))
    db = get_db()
    conv = db.execute("SELECT * FROM wa_conversations WHERE id=? AND wa_number=?", (conv_id, number)).fetchone()
    if conv is None:
        flash("Conversation not found.", "error")
        return redirect(url_for("comms.whatsapp_inbox", number=number))

    if request.method == "POST":
        lead_id = request.form.get("lead_id") or None
        member_id = request.form.get("member_id") or None
        db.execute(
            "UPDATE wa_conversations SET linked_lead_id=?, linked_member_id=? WHERE id=? AND wa_number=?",
            (lead_id, member_id, conv_id, number),
        )
        db.commit()
        flash("Conversation linked.", "success")
        return redirect(url_for("comms.whatsapp_conversation", number=number, conv_id=conv_id))

    leads = db.execute(
        "SELECT id, full_name, phone FROM leads ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    members = db.execute(
        "SELECT id, first_name, last_name, contact FROM members WHERE member_status != 'Cancelled' ORDER BY first_name"
    ).fetchall()
    return render_template("communications/whatsapp_link.html", conv=conv, leads=leads, members=members, number=number)


@comms_bp.get("/facebook/messages")
@permission_required("facebook_messages")
def facebook_messages_inbox():
    db = get_db()
    conversations = db.execute(
        """
        SELECT c.*, MAX(m.content) AS last_content
        FROM fb_conversations c
        LEFT JOIN fb_messages m ON m.conversation_id = c.id
        GROUP BY c.id
        ORDER BY c.last_message_at DESC
        """
    ).fetchall()
    return render_template("communications/facebook_messages.html", conversations=conversations)


@comms_bp.route("/facebook/messages/<int:conv_id>", methods=["GET", "POST"])
@permission_required("facebook_messages")
def facebook_conversation(conv_id: int):
    db = get_db()
    conv = db.execute("SELECT * FROM fb_conversations WHERE id=?", (conv_id,)).fetchone()
    if conv is None:
        flash("Conversation not found.", "error")
        return redirect(url_for("comms.facebook_messages_inbox"))

    if request.method == "POST":
        text = request.form.get("message", "").strip()
        if text:
            result = send_facebook_reply(conv["sender_psid"], text)
            ts = datetime.now().isoformat()
            fb_msg_id = result.get("message_id")
            db.execute(
                "INSERT INTO fb_messages (conversation_id, fb_message_id, direction, content, sent_by_user_id, timestamp) VALUES (?,?,?, ?,?,?)",
                (conv_id, fb_msg_id, "outbound", text, session.get("user_id"), ts),
            )
            db.execute("UPDATE fb_conversations SET last_message_at=? WHERE id=?", (ts, conv_id))
            db.commit()
            if "error" in result:
                flash(f"Message saved but send failed: {result['error']}", "warning")
        return redirect(url_for("comms.facebook_conversation", conv_id=conv_id))

    db.execute("UPDATE fb_conversations SET unread_count=0 WHERE id=?", (conv_id,))
    db.commit()
    messages = db.execute(
        "SELECT * FROM fb_messages WHERE conversation_id=? ORDER BY timestamp ASC, id ASC",
        (conv_id,),
    ).fetchall()
    return render_template("communications/facebook_conversation.html", conv=conv, messages=messages)


@comms_bp.get("/facebook/comments")
@permission_required("facebook_comments")
def facebook_comments_view():
    db = get_db()
    filter_mode = request.args.get("filter", "all")
    query = "SELECT * FROM fb_comments"
    if filter_mode == "interested":
        query += " WHERE is_interested = 1"
    elif filter_mode == "new":
        query += " WHERE is_replied = 0"
    query += " ORDER BY timestamp DESC"
    comments = db.execute(query).fetchall()
    return render_template("communications/facebook_comments.html", comments=comments, filter_mode=filter_mode)


@comms_bp.post("/facebook/comments/<int:comment_id>/flag")
@permission_required("facebook_comments")
def facebook_comment_flag(comment_id: int):
    db = get_db()
    comment = db.execute("SELECT * FROM fb_comments WHERE id=?", (comment_id,)).fetchone()
    if comment is None:
        flash("Comment not found.", "error")
        return redirect(url_for("comms.facebook_comments_view"))
    new_val = 0 if comment["is_interested"] else 1
    db.execute("UPDATE fb_comments SET is_interested=? WHERE id=?", (new_val, comment_id))
    db.commit()
    return redirect(url_for("comms.facebook_comments_view", filter=request.args.get("filter", "all")))


@comms_bp.post("/facebook/comments/<int:comment_id>/create-lead")
@permission_required("facebook_comments")
def facebook_comment_create_lead(comment_id: int):
    if session.get("role") != "admin" and "capture_leads" not in session.get("permissions", []):
        flash("Access denied.", "warning")
        return redirect(url_for("comms.facebook_comments_view"))
    db = get_db()
    comment = db.execute("SELECT * FROM fb_comments WHERE id=?", (comment_id,)).fetchone()
    if comment is None:
        flash("Comment not found.", "error")
        return redirect(url_for("comms.facebook_comments_view"))
    if comment["linked_lead_id"]:
        flash("This comment is already linked to a lead.", "warning")
        return redirect(url_for("comms.facebook_comments_view"))

    lead_id = _create_lead_from_facebook_comment(db, comment)
    db.execute("UPDATE fb_comments SET is_interested=1, linked_lead_id=? WHERE id=?", (lead_id, comment_id))
    db.commit()
    flash(f"Lead created for {comment['commenter_name'] or 'this commenter'}.", "success")
    return redirect(url_for("comms.facebook_comments_view"))


def _create_lead_from_facebook_comment(db, comment) -> int:
    """Feed a Facebook comment into the same lead pipeline the public join
    funnel uses — consultant auto-allocation included — rather than a bare
    INSERT. 'Social Media' is already a recognised source for
    _allocate_sales_consultant().

    Comments carry no phone number, so onecpase.leads' own phone-based
    _find_duplicate() can't apply here; dedupe instead on whether this same
    commenter already has an earlier comment linked to a lead."""
    full_name = (comment["commenter_name"] or "Facebook Commenter").strip()
    source, campaign = "Social Media", "Facebook"

    if comment["commenter_id"]:
        prior = db.execute(
            """SELECT linked_lead_id FROM fb_comments
               WHERE commenter_id = ? AND linked_lead_id IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (comment["commenter_id"],),
        ).fetchone()
        if prior:
            _add_activity(
                db, prior["linked_lead_id"], "note", outcome="recorded",
                notes="Another Facebook comment from the same commenter; original lead retained.",
            )
            return prior["linked_lead_id"]

    consultant = _allocate_sales_consultant(db, None, source)
    assigned_to = consultant["id"] if consultant else None
    lead_status = "assigned" if consultant else "captured"
    cursor = db.execute(
        """INSERT INTO leads
           (full_name, source, original_source, campaign, assigned_to,
            lead_status, marketing_consent, do_not_contact, last_activity_at,
            lead_channel, entry_path, notes)
           VALUES (?, ?, ?, ?, ?, ?, 1, 0, datetime('now'), 'outreach', 'standard', ?)""",
        (
            full_name, source, source, campaign, assigned_to, lead_status,
            f"From Facebook comment: {(comment['content'] or '').strip()[:280]}",
        ),
    )
    lead_id = cursor.lastrowid
    if consultant:
        db.execute(
            "UPDATE users SET last_lead_assigned_at=? WHERE id=?",
            (datetime.now().isoformat(timespec="microseconds", sep=" "), assigned_to),
        )
    _add_activity(
        db, lead_id, "prospect_created", outcome=lead_status,
        notes=f"Facebook comment converted to lead by {session.get('full_name', 'staff')}.",
    )
    return lead_id
