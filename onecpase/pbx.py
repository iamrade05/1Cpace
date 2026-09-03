"""Yeastar P-Series PBX integration for 1Cpace.

Supports click-to-call through Yeastar OpenAPI and a webhook endpoint for
call events/CDRs. Credentials stay in environment variables; access tokens
are cached only in-process. The integration intentionally does not expose
PBX credentials to the browser.
"""
import hmac
import json
from datetime import datetime
from urllib.parse import quote

import requests
from flask import Blueprint, current_app, jsonify, render_template, request, session

from .auth import login_required, permission_required
from .extensions import csrf
from .database import get_db

pbx_bp = Blueprint("pbx", __name__, url_prefix="/pbx")
_token = {"access": None, "refresh": None, "access_exp": 0, "refresh_exp": 0}


def _base():
    return f"{current_app.config['PBX_BASE_URL']}/{current_app.config['PBX_API_VERSION']}"


def _request(method, endpoint, **kwargs):
    """Authenticated Yeastar API request with one automatic token refresh."""
    now = datetime.now().timestamp()
    if not _token["access"] or now >= _token["access_exp"] - 30:
        _authenticate()
    params = kwargs.pop("params", {}) or {}
    params["access_token"] = _token["access"]
    headers = kwargs.pop("headers", {}) or {}
    headers.setdefault("User-Agent", "OpenAPI")
    headers.setdefault("Content-Type", "application/json")
    r = requests.request(method, f"{_base()}/{endpoint.lstrip('/')}", params=params,
                         headers=headers, timeout=current_app.config["PBX_TIMEOUT"], **kwargs)
    if r.status_code == 401:
        _authenticate(force=True)
        params["access_token"] = _token["access"]
        r = requests.request(method, f"{_base()}/{endpoint.lstrip('/')}", params=params,
                             headers=headers, timeout=current_app.config["PBX_TIMEOUT"], **kwargs)
    r.raise_for_status()
    data = r.json()
    if data.get("errcode", 0) not in (0, None):
        raise RuntimeError(data.get("errmsg") or "Yeastar API request failed")
    return data


def _authenticate(force=False):
    now = datetime.now().timestamp()
    if not force and _token["access"] and now < _token["access_exp"] - 30:
        return
    cid = current_app.config.get("PBX_CLIENT_ID")
    secret = current_app.config.get("PBX_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError("PBX is not configured: set PBX_CLIENT_ID and PBX_CLIENT_SECRET")
    r = requests.post(f"{_base()}/get_token", json={"username": cid, "password": secret},
                      headers={"User-Agent": "OpenAPI", "Content-Type": "application/json"},
                      timeout=current_app.config["PBX_TIMEOUT"])
    r.raise_for_status()
    data = r.json()
    if data.get("errcode") != 0:
        raise RuntimeError(data.get("errmsg") or "PBX authentication failed")
    _token.update({"access": data["access_token"], "refresh": data.get("refresh_token"),
                   "access_exp": now + int(data.get("access_token_expire_time", 1800)),
                   "refresh_exp": now + int(data.get("refresh_token_expire_time", 86400))})


def _normalise_phone(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit() or ch == "+")


def _find_contact(phone):
    db = get_db(); digits = _normalise_phone(phone).replace("+", "")
    if not digits: return None
    lead = db.execute("SELECT id, full_name FROM leads WHERE phone_normalized = ? LIMIT 1", (digits,)).fetchone()
    if lead: return {"lead_id": lead["id"], "member_id": None, "name": lead["full_name"]}
    member = db.execute("SELECT id, full_name FROM members WHERE REPLACE(REPLACE(REPLACE(phone, ' ', ''), '+', ''), '-', '') = ? LIMIT 1", (digits,)).fetchone()
    if member: return {"lead_id": None, "member_id": member["id"], "name": member["full_name"]}
    return None


def _user_extension():
    db = get_db(); uid = session.get("user_id")
    if uid:
        row = db.execute("SELECT pbx_extension FROM users WHERE id = ?", (uid,)).fetchone()
        if row and row["pbx_extension"]: return str(row["pbx_extension"])
    return current_app.config.get("PBX_DEFAULT_EXTENSION", "")


CALL_OUTCOMES = {
    "connected": "Connected",
    "no_answer": "No Answer",
    "voicemail": "Voicemail",
    "callback": "Callback Requested",
    "appointment_booked": "Appointment Booked",
    "not_interested": "Not Interested",
    "wrong_number": "Wrong Number",
    "payment_promise": "Payment Promise",
    "dispute": "Dispute",
    "escalation": "Escalated",
    "other": "Other",
}

def _record_crm_outcome(call_row, outcome, notes="", next_action="", follow_up_at=""):
    """Push a completed PBX call outcome into the canonical CRM timeline."""
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    notes = (notes or "").strip()
    next_action = (next_action or "").strip()
    follow_up_at = (follow_up_at or "").strip() or None

    if call_row["lead_id"]:
        from .leads import _add_activity
        _add_activity(
            db, call_row["lead_id"], "call", outcome=outcome,
            notes=notes or f"PBX call {call_row['external_call_id']}",
            occurred_at=call_row["start_time"] or now,
            next_action=next_action,
            follow_up_at=follow_up_at,
        )
        if next_action or follow_up_at:
            db.execute(
                """UPDATE leads SET next_action=?, next_action_at=?,
                   last_activity_at=datetime('now'), updated_at=datetime('now')
                   WHERE id=?""",
                (next_action or None, follow_up_at, call_row["lead_id"]),
            )
    elif call_row["member_id"]:
        from .database import member_activity
        label = CALL_OUTCOMES.get(outcome, outcome.replace("_", " ").title())
        note = f"PBX call — {label}"
        if notes:
            note += f": {notes}"
        if next_action:
            note += f" | Next action: {next_action}"
        if follow_up_at:
            note += f" | Follow-up: {follow_up_at}"
        member_activity(db, call_row["member_id"], note)

    if call_row["collection_task_id"]:
        mapped = {
            "connected": "reminder_delivered",
            "payment_promise": "promised_to_pay",
            "callback": "asked_callback",
            "no_answer": "no_answer",
            "voicemail": "voicemail",
            "wrong_number": "wrong_number",
            "dispute": "disputed",
            "not_interested": "refused",
            "escalation": "refused",
            "other": "reminder_delivered",
        }.get(outcome, "reminder_delivered")
        successful = 1 if mapped in {"reminder_delivered", "promised_to_pay",
                                     "asked_callback", "already_paid", "disputed",
                                     "refused"} else 0
        next_channel = "WHATSAPP" if not successful else None
        db.execute(
            """UPDATE collection_call_tasks
               SET status='completed', outcome=?, successful_contact=?,
                   notes=?, called_at=datetime('now'), next_channel=?,
                   next_action_at=CASE WHEN ? IS NULL THEN NULL ELSE date('now','+1 day') END
               WHERE id=?""",
            (mapped, successful, notes or None, next_channel, next_channel,
             call_row["collection_task_id"]),
        )

    db.execute(
        """UPDATE pbx_call_logs
           SET outcome=?, outcome_notes=?, next_action=?, follow_up_at=?,
               outcome_recorded_at=?, outcome_recorded_by=?, updated_at=?
           WHERE id=?""",
        (outcome, notes, next_action or None, follow_up_at, now,
         session.get("user_id"), now, call_row["id"]),
    )
    db.commit()


def _save_call(data, commit=True):
    db = get_db(); ext_id = data.get("call_id") or data.get("uid") or data.get("cdrid")
    if not ext_id: return
    caller = str(data.get("call_from") or data.get("callfrom") or data.get("src") or "")
    callee = str(data.get("call_to") or data.get("callto") or data.get("dst") or "")
    ctype = str(data.get("call_type") or data.get("calltype") or data.get("type") or "")
    direction = "inbound" if ctype.lower() == "inbound" else "outbound" if ctype.lower() == "outbound" else "internal"
    contact = _find_contact(caller if direction == "inbound" else callee)
    start = data.get("time") or data.get("timestart") or data.get("datetime")
    duration = int(float(data.get("call_duration") or data.get("duration") or data.get("callduraction") or 0))
    talk = int(float(data.get("talk_duration") or data.get("talkduration") or data.get("talkduraction") or 0))
    status = str(data.get("disposition") or data.get("status") or data.get("last_status") or "UNKNOWN").upper()
    recording = data.get("recording") or data.get("recordfile")
    payload = json.dumps(data, ensure_ascii=False)
    existing = db.execute("SELECT id FROM pbx_call_logs WHERE external_call_id = ?", (str(ext_id),)).fetchone()
    if existing:
        db.execute("""UPDATE pbx_call_logs SET caller=?,callee=?,start_time=?,duration=?,talk_duration=?,status=?,call_type=?,recording=?,raw_json=?,updated_at=datetime('now') WHERE external_call_id=?""",
                   (caller,callee,start,duration,talk,status,ctype,recording,payload,str(ext_id)))
    else:
        db.execute("""INSERT INTO pbx_call_logs(external_call_id,lead_id,member_id,user_id,collection_task_id,direction,caller,callee,start_time,duration,talk_duration,status,call_type,recording,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (str(ext_id), contact.get("lead_id") if contact else None, contact.get("member_id") if contact else None,
                    session.get("user_id"),data.get("collection_task_id"),direction,caller,callee,start,duration,talk,status,ctype,recording,payload))
    if commit:
        db.commit()


@pbx_bp.post("/dial")
@login_required
@permission_required("pbx_call")
def dial():
    body = request.get_json(silent=True) or request.form
    callee = _normalise_phone(body.get("phone") or body.get("callee"))
    caller = str(body.get("caller") or _user_extension())
    if not caller or not callee: return jsonify(error="Caller extension and destination are required"), 400
    payload = {"caller": caller, "callee": callee, "auto_answer": str(body.get("auto_answer") or "no")}
    perm = current_app.config.get("PBX_DIAL_PERMISSION")
    if perm: payload["dial_permission"] = perm
    try:
        result = _request("POST", "call/dial", json=payload)
        cid = result.get("call_id")
        contact = _find_contact(callee)
        db = get_db()
        db.execute("""INSERT OR IGNORE INTO pbx_call_logs(external_call_id,lead_id,member_id,user_id,collection_task_id,direction,caller,callee,status,call_type,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (str(cid), contact.get("lead_id") if contact else None, contact.get("member_id") if contact else None,
                    session.get("user_id"), body.get("collection_task_id") or None,
                    "outbound",caller,callee,"INITIATED","Outbound",json.dumps(result)))
        db.commit()
        return jsonify(ok=True, call_id=cid, contact=contact)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 502


@pbx_bp.post("/webhook")
@csrf.exempt
def webhook():
    # PBX webhooks are external, unauthenticated HTTP requests: fail closed.
    # Keep the exact Yeastar integration header name; never accept secrets in
    # query strings because URLs can be captured by proxy/access logs.
    secret = str(current_app.config.get("PBX_WEBHOOK_SECRET", "") or "")
    supplied = str(request.headers.get("X-1Cpace-PBX-Secret", "") or "")
    if not secret or not supplied or not hmac.compare_digest(supplied, secret):
        return jsonify(error="Unauthorized"), 401
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("type") or payload.get("event") or payload.get("action") or "unknown"
    msg = payload.get("msg") if isinstance(payload.get("msg"), dict) else payload
    key = str(msg.get("call_id") or msg.get("uid") or msg.get("cdrid") or json.dumps(payload, sort_keys=True))
    db = get_db()
    try:
        inserted = db.execute(
            "INSERT OR IGNORE INTO pbx_webhook_events(event_key,event_type,payload) VALUES (?,?,?)",
            (key, str(event_type), json.dumps(payload)),
        ).rowcount == 1
        if not inserted:
            db.rollback()
            return jsonify(ok=True, duplicate=True)
        _save_call(msg, commit=False)
        db.commit()
    except Exception as exc:
        db.rollback()
        current_app.logger.exception("PBX webhook processing failed: %s", exc)
        return jsonify(error="Webhook processing failed"), 500
    return jsonify(ok=True)



@pbx_bp.post("/calls/<int:call_id>/outcome")
@login_required
@permission_required("pbx_call")
def call_outcome(call_id):
    body = request.get_json(silent=True) or request.form
    outcome = str(body.get("outcome") or "").strip().lower()
    if outcome not in CALL_OUTCOMES:
        return jsonify(ok=False, error="Invalid call outcome"), 400
    db = get_db()
    row = db.execute("SELECT * FROM pbx_call_logs WHERE id=?", (call_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="Call not found"), 404
    try:
        _record_crm_outcome(
            row, outcome,
            notes=body.get("notes", ""),
            next_action=body.get("next_action", ""),
            follow_up_at=body.get("follow_up_at", ""),
        )
        return jsonify(ok=True, call_id=call_id, outcome=outcome)
    except Exception as exc:
        db.rollback()
        current_app.logger.exception("PBX call outcome failed")
        return jsonify(ok=False, error=str(exc)), 500

@pbx_bp.get("/calls")
@login_required
def calls():
    rows = get_db().execute("SELECT * FROM pbx_call_logs ORDER BY COALESCE(start_time, created_at) DESC LIMIT 250").fetchall()
    return render_template("pbx/calls.html", calls=rows, call_outcomes=CALL_OUTCOMES)


@pbx_bp.get("/health")
@login_required
def health():
    try:
        info = _request("GET", "system/information")
        return jsonify(ok=True, pbx=info)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 502
