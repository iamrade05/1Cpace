import base64
import json
import hashlib
import hmac
import secrets
import time
from pathlib import Path
from datetime import date, datetime, timedelta

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect,
    render_template, request, send_file, session, url_for,
)
from flask_mail import Message

from .auth import permission_required
from .database import (
    default_debit_mandate_text,
    default_sections_config,
    default_terms_parts_config,
    get_db,
)
from .encryption import decrypt_member
from .extensions import mail
from .platform_db import get_platform_db
from .comms import send_whatsapp_message

contracts_bp = Blueprint("contracts", __name__, url_prefix="/contracts")

SIGNER_ROLES = ("member", "representative", "witness")
# A cleared/blank canvas still produces a small PNG - a real signature is
# never this tiny, so this catches an accidental empty save without having
# to decode and inspect pixel data.
MIN_SIGNATURE_BYTES = 300
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024


SAMPLE_MEMBER = {
    "id": 0, "first_name": "Sample", "last_name": "Member", "id_number": "8001015009087",
    "date_of_birth": "1980-01-01", "gender": "Male", "contact": "082 000 0000",
    "alternative_number": "021 000 0000", "email": "sample@example.com",
    "occupation": "Consultant", "employer": "ACME Ltd", "work_number": "021 111 2222",
    "opt_in": 1, "join_date": date.today().isoformat(), "member_ref": "SAMPLE001",
    "street_number": "1", "street_name": "Example Street", "suburb": "Sandton",
    "city": "Johannesburg", "postal_code": "2196", "country": "South Africa", "source": "Walk-in",
    "tariff": "Premium", "contract_duration": "12 Months", "access_level": "Local",
    "member_status": "Active", "promotion": "", "consultant_name": "Jane Consultant",
    "joining_fee": 250, "member_addons": "Locker",
    "payment_type": "Debit Order", "debit_order_date": "1", "bank": "Standard Bank",
    "account_type": "Cheque/Current", "account_number": "123456789", "branch_code": "051001",
    "emergency_contact_name": "John Sample", "emergency_contact_number": "083 000 0000",
    "emergency_contact_relation": "Spouse", "witness_name": "",
    "monthly_installment": 599, "payer_name": "", "payer_id_number": "",
    "itensity_ref": "", "access_credential": "",
    **{f"parq_q{i}": 0 for i in range(1, 10)},
}


def _ordinal(value: str) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return value or ""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _debit_start_date(member) -> str:
    """Return the first selected debit day on or after the join date."""
    join_value = member.get("join_date") if isinstance(member, dict) else member["join_date"]
    debit_value = member.get("debit_order_date") if isinstance(member, dict) else member["debit_order_date"]
    if not join_value or not debit_value:
        return join_value or "the start of this agreement"
    try:
        join_date = date.fromisoformat(str(join_value)[:10])
        debit_day = max(1, min(int(debit_value), 28))
    except (TypeError, ValueError):
        return join_value
    if join_date.day < debit_day:
        return join_date.replace(day=debit_day).isoformat()
    month = join_date.month + 1
    year = join_date.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return date(year, month, 1).replace(day=debit_day).isoformat()


def _load_sections(contract_template) -> list:
    raw = contract_template["sections_config"] if contract_template else None
    if raw:
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            pass
    return default_sections_config()


def _fill_gym_name(text, gym_name: str) -> str:
    """Substitute {gym_name} without letting staff-entered braces raise.

    Clause text is authored by admin users, so an unmatched brace must not
    take the whole contract page down.
    """
    try:
        return (text or "").format(gym_name=gym_name)
    except (IndexError, KeyError, ValueError):
        return (text or "").replace("{gym_name}", gym_name)


def _terms_parts_from_text(contract_template) -> list:
    """Fall back to the template's own terms_text.

    A template saved from the admin form always carries terms_text (it is a
    required field) but only carries terms_parts_config when the editor added
    structured Parts. Without this the template's real terms are silently
    replaced by the generic default set.
    """
    try:
        raw_text = contract_template["terms_text"] if contract_template else ""
    except (KeyError, IndexError):
        return []
    clauses = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
    if not clauses:
        return []
    return [{
        "title": "Terms and Conditions",
        "intro": "",
        "clauses": clauses,
        "page_break": False,
        "initials_required": True,
    }]


def _load_terms_parts(contract_template, gym_name: str) -> list:
    raw = contract_template["terms_parts_config"] if contract_template else None
    parts = []
    if raw:
        try:
            parts = json.loads(raw)
        except (TypeError, ValueError):
            parts = []
    # An empty/absent Parts config is not an instruction to print the default
    # terms over this template's own.
    if not parts:
        parts = _terms_parts_from_text(contract_template) or default_terms_parts_config()

    rendered = []
    for part in parts:
        rendered.append({
            "title": _fill_gym_name(part.get("title"), gym_name),
            "intro": _fill_gym_name(part.get("intro"), gym_name),
            "clauses": [_fill_gym_name(c, gym_name) for c in part.get("clauses", [])],
            "page_break": bool(part.get("page_break")),
            "initials_required": bool(part.get("initials_required")),
        })
    return rendered


def _latest_signatures(db, member_id: int | None) -> dict:
    """The most recent captured signature per role for this member.

    A new signature is inserted rather than updated in place (see /sign),
    so this just takes the newest row per signer_role."""
    if not member_id:
        return {}
    rows = db.execute(
        """SELECT signer_role, image_filename, signed_at FROM contract_signatures
           WHERE member_id = ? ORDER BY signed_at DESC, id DESC""",
        (member_id,),
    ).fetchall()
    latest: dict = {}
    for row in rows:
        latest.setdefault(row["signer_role"], row)
    return latest


def _current_tenant():
    """The tenant to brand this contract with.

    g.tenant is already set for the whole request by the app-wide tenant
    cookie (or explicitly by public_share() for the no-login share link) -
    reuse it rather than re-querying, and fall back to a slug-only lookup if
    only g.tenant_slug made it through (mirrors login.html/join.html, which
    already use tenant.logo_filename the same way)."""
    tenant = g.get("tenant")
    if tenant:
        return tenant
    slug = g.get("tenant_slug")
    if not slug:
        return None
    row = get_platform_db().execute(
        "SELECT * FROM tenants WHERE slug=? AND active=1", (slug,)
    ).fetchone()
    return dict(row) if row else None


def render_contract(contract_template, member, *, signing_enabled: bool = False, share=None,
                    contract_status=None, signed_docs=()):
    """Shared renderer for both a real member's contract and the admin
    template preview — same data prep, same template, different member row."""
    gym_name = (contract_template["gym_name"] if contract_template else None) or "Elev8 Health & Fitness"
    sections = _load_sections(contract_template)
    sections_by_key = {s["key"]: s for s in sections}

    def section_enabled(key: str) -> bool:
        section = sections_by_key.get(key)
        return bool(section and section.get("enabled", True))

    def field_enabled(section_key: str, field_key: str) -> bool:
        section = sections_by_key.get(section_key)
        if not section or not section.get("enabled", True):
            return False
        for field in section.get("fields", []):
            if field["key"] == field_key:
                return bool(field.get("enabled", True))
        return False

    debit_mandate_raw = (contract_template["debit_mandate_text"] if contract_template else None) \
        or default_debit_mandate_text()
    debit_mandate = debit_mandate_raw.format(
        gym_name=gym_name,
        debit_day=_ordinal(member.get("debit_order_date") if isinstance(member, dict) else member["debit_order_date"]),
        debit_start=_debit_start_date(member),
    )
    member_id = member["id"] if member["id"] else None
    return render_template(
        "contracts/view.html",
        m=member,
        today=date.today(),
        tenant=_current_tenant(),
        contract_template=contract_template,
        terms_parts=_load_terms_parts(contract_template, gym_name),
        debit_mandate=debit_mandate,
        section_enabled=section_enabled,
        field_enabled=field_enabled,
        generated_at=datetime.now().strftime("%d %B %Y %H:%M"),
        signing_enabled=signing_enabled,
        signatures=_latest_signatures(get_db(), member_id),
        share=share,
        contract_status=contract_status,
        signed_docs=signed_docs,
    )


def _mark_contract_signed(db, member_id: int) -> None:
    """Flip the compliance checklist's contract status to signed - shared by
    the remote approve-by-link flow and an on-screen member signature."""
    from .applications import ensure_member_application, sync_application_status
    aid = ensure_member_application(db, member_id)
    db.execute(
        "INSERT OR IGNORE INTO compliance_checklists (application_id) VALUES (?)", (aid,)
    )
    db.execute(
        "UPDATE compliance_checklists SET contract_status='signed', updated_at=datetime('now') WHERE application_id=?",
        (aid,),
    )
    sync_application_status(db, aid)


def _share_url(token: str) -> str:
    return url_for(
        "contracts.public_share", tenant_slug=g.tenant_slug or "elev8",
        token=token, _external=True,
    )


def _new_share(member_id: int, channel: str = "link") -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    db = get_db()
    db.execute(
        """UPDATE contract_share_tokens SET status='superseded'
           WHERE member_id=? AND status='pending'""", (member_id,)
    )
    db.execute(
        """INSERT INTO contract_share_tokens
           (member_id, token_hash, expires_at, sent_via, created_by)
           VALUES (?, ?, datetime('now','+7 days'), ?, ?)""",
        (member_id, hashlib.sha256(raw_token.encode()).hexdigest(), channel, session.get("user_id")),
    )
    db.commit()
    return raw_token, _share_url(raw_token)


@contracts_bp.route("/<int:mid>/share", methods=["POST"])
@permission_required("all_members")
def share_contract(mid: int):
    db = get_db()
    member = db.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
    if member is None:
        abort(404)
    channel = request.form.get("channel", "link")
    token, link = _new_share(mid, channel)
    member = decrypt_member(member)
    message = f"Hello {member['first_name']}, please review and approve your membership contract: {link}"
    if channel == "whatsapp":
        result = send_whatsapp_message(
            current_app.config.get("WA_RECEPTION_PHONE_ID", ""),
            member.get("contact", ""), message,
        )
        if result.get("error"):
            flash(result["error"], "error")
        else:
            flash("Contract approval link sent by WhatsApp.", "success")
    elif channel == "email":
        if not member.get("email"):
            flash("This member has no email address.", "error")
        else:
            try:
                mail.send(Message("Your Elev8 membership contract", recipients=[member["email"]], body=message))
                flash("Contract approval link sent by email.", "success")
            except Exception:
                current_app.logger.exception("Failed sending contract email for member %s", mid)
                flash("Email is not configured or could not be sent.", "error")
    else:
        flash(f"Approval link created for SMS/manual sending: {link}", "success")
    return redirect(url_for("members.member_detail", mid=mid))


@contracts_bp.route("/share/<tenant_slug>/<token>", methods=["GET", "POST"])
def public_share(tenant_slug: str, token: str):
    tenant = get_platform_db().execute(
        "SELECT * FROM tenants WHERE slug=? AND active=1", (tenant_slug,)
    ).fetchone()
    if not tenant:
        abort(404)
    g.tenant = dict(tenant)
    g.tenant_slug = tenant_slug
    db = get_db()
    row = db.execute(
        "SELECT * FROM contract_share_tokens WHERE token_hash=?",
        (hashlib.sha256(token.encode()).hexdigest(),),
    ).fetchone()
    if not row or row["status"] in ("superseded", "approved", "declined"):
        return render_template("contracts/share_expired.html"), 410
    if datetime.fromisoformat(str(row["expires_at"]).replace("Z", " ")) < datetime.now():
        db.execute("UPDATE contract_share_tokens SET status='expired' WHERE id=?", (row["id"],))
        db.commit()
        return render_template("contracts/share_expired.html"), 410
    db.execute("UPDATE contract_share_tokens SET viewed_at=COALESCE(viewed_at,datetime('now')) WHERE id=?", (row["id"],))
    db.commit()
    member = db.execute("SELECT m.*,u.full_name consultant_name FROM members m LEFT JOIN users u ON u.id=m.uploaded_by_id WHERE m.id=?", (row["member_id"],)).fetchone()
    if member is None:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action") or request.form.get("response")
        if action == "send_code":
            return _share_page(db, row["id"], member, _send_share_code(db, row, decrypt_member(member)))
        if action == "verify_code":
            return _share_page(db, row["id"], member, _check_share_code(db, row, request.form.get("code", "")))
        if action not in ("approved", "declined"):
            abort(400)
        # The link alone is only a bearer token - approving needs the code first.
        if action == "approved" and not row["verified_at"]:
            return _share_page(
                db, row["id"], member,
                "Please verify the code we send you before approving.", status=403,
            )
        db.execute(
            "UPDATE contract_share_tokens SET status=?, responded_at=datetime('now'), response_note=? WHERE id=?",
            (action, request.form.get("note", "").strip()[:500], row["id"]),
        )
        if action == "approved":
            _mark_contract_signed(db, row["member_id"])
        db.commit()
        return render_template("contracts/share_response.html", approved=action == "approved")
    return _share_page(db, row["id"], member)


# ── One-time code before a member can approve from the shared link ────────────

SHARE_CODE_TTL_MINUTES = 10
SHARE_CODE_MAX_ATTEMPTS = 5
SHARE_CODE_MAX_SENDS = 5
SHARE_CODE_COOLDOWN_SECONDS = 60


def _hash_share_code(token_id: int, code: str) -> str:
    return hashlib.sha256(f"{token_id}:{code}".encode()).hexdigest()


def _whatsapp_number(contact) -> str | None:
    """A South African number in the international form WhatsApp needs."""
    digits = "".join(ch for ch in (contact or "") if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        return "27" + digits[1:]
    if digits.startswith("27") and len(digits) == 11:
        return digits
    return None


def _deliver_share_code(member, code: str) -> str | None:
    """Send *code* to the member: WhatsApp on the number on file, else the email
    on file. Returns where it went (for the page to say), or None if neither worked."""
    gym = (g.get("tenant") or {}).get("name") or "Your gym"
    text = (f"{gym}: your contract code is {code}. It expires in {SHARE_CODE_TTL_MINUTES} minutes. "
            "Do not share it with anyone.")
    phone = _whatsapp_number(member.get("contact"))
    if phone:
        result = send_whatsapp_message(current_app.config.get("WA_RECEPTION_PHONE_ID", ""), phone, text)
        if not result.get("error"):
            return f"WhatsApp number ending {phone[-4:]}"
    email = (member.get("email") or "").strip()
    if email:
        try:
            mail.send(Message("Your membership contract code", recipients=[email], body=text))
            local, _, domain = email.partition("@")
            return f"the email address {local[:1]}***@{domain}"
        except Exception:
            current_app.logger.exception("Failed sending contract code email for member %s", member.get("id"))
    return None


def _send_share_code(db, row, member) -> str | None:
    """Issue a new code. Returns a message for the page when it could not, else None."""
    if (row["otp_sends"] or 0) >= SHARE_CODE_MAX_SENDS:
        return "Too many codes have been requested for this link. Please ask the gym for a new link."
    last = row["otp_sent_at"]
    if last and (datetime.now() - datetime.fromisoformat(last)).total_seconds() < SHARE_CODE_COOLDOWN_SECONDS:
        return "Please wait a minute before asking for another code."
    code = f"{secrets.randbelow(10 ** 6):06d}"
    sent_to = _deliver_share_code(member, code)
    if not sent_to:
        return "We could not send a code. Please ask the gym to help you sign."
    now = datetime.now()
    db.execute(
        """UPDATE contract_share_tokens
           SET otp_hash=?, otp_expires_at=?, otp_attempts=0, otp_sends=COALESCE(otp_sends,0)+1,
               otp_sent_at=?, otp_sent_to=?
           WHERE id=?""",
        (
            _hash_share_code(row["id"], code),
            (now + timedelta(minutes=SHARE_CODE_TTL_MINUTES)).isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"), sent_to, row["id"],
        ),
    )
    db.commit()
    return None


def _check_share_code(db, row, entered: str) -> str | None:
    """Check an entered code. Returns a message for the page when it fails, else None."""
    entered = "".join(ch for ch in entered if ch.isdigit())
    if not row["otp_hash"] or not row["otp_expires_at"]:
        return "Please ask for a code first."
    if datetime.fromisoformat(row["otp_expires_at"]) < datetime.now():
        return "That code has expired. Please ask for a new one."
    if (row["otp_attempts"] or 0) >= SHARE_CODE_MAX_ATTEMPTS:
        return "Too many wrong attempts. Please ask for a new code."
    if not hmac.compare_digest(_hash_share_code(row["id"], entered), row["otp_hash"]):
        db.execute(
            "UPDATE contract_share_tokens SET otp_attempts=COALESCE(otp_attempts,0)+1 WHERE id=?",
            (row["id"],),
        )
        db.commit()
        return "That code is not right. Please check it and try again."
    db.execute(
        "UPDATE contract_share_tokens SET verified_at=?, otp_hash=NULL WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), row["id"]),
    )
    db.commit()
    return None


def _share_page(db, token_id: int, member, notice=None, status: int = 200):
    """The contract page for a shared link, in whichever stage of verification it is."""
    row = db.execute("SELECT * FROM contract_share_tokens WHERE id=?", (token_id,)).fetchone()
    if row["verified_at"]:
        stage = "verified"
    elif (row["otp_hash"] and row["otp_expires_at"]
          and datetime.fromisoformat(row["otp_expires_at"]) >= datetime.now()):
        stage = "code_sent"
    else:
        stage = "start"
    template = db.execute(
        "SELECT * FROM contract_templates WHERE active=1 ORDER BY is_default DESC,id LIMIT 1"
    ).fetchone()
    share = {"stage": stage, "notice": notice, "sent_to": row["otp_sent_to"]}
    return render_contract(template, decrypt_member(member), share=share), status


@contracts_bp.route("/<int:mid>")
@permission_required("all_members")
def view_contract(mid: int):
    db = get_db()
    member = db.execute(
        """SELECT m.*, u.full_name AS consultant_name
           FROM members m LEFT JOIN users u ON m.uploaded_by_id = u.id
           WHERE m.id = ?""", (mid,)
    ).fetchone()
    if member is None:
        flash("Member not found.", "warning")
        return redirect(url_for("members.members_index"))

    contract_template = db.execute(
        """SELECT * FROM contract_templates
           WHERE active=1
           ORDER BY is_default DESC, id ASC LIMIT 1"""
    ).fetchone()
    # member is a raw DB row - id_number, payer_id_number, account_number and
    # branch_code are stored encrypted and must be decrypted before a human
    # reads or signs this page, the same way public_share() already does.
    checklist = db.execute(
        """SELECT cc.contract_status
           FROM compliance_checklists cc
           JOIN membership_applications a ON a.id = cc.application_id
           WHERE a.member_id = ? ORDER BY a.id DESC LIMIT 1""", (mid,)
    ).fetchone()
    signed_docs = db.execute(
        """SELECT id, original_name, uploaded_at FROM member_documents
           WHERE member_id = ? AND document_type = 'Signed Contract' ORDER BY id DESC""", (mid,)
    ).fetchall()
    return render_contract(
        contract_template, decrypt_member(member), signing_enabled=True,
        contract_status=checklist["contract_status"] if checklist else None,
        signed_docs=signed_docs,
    )


def _save_signature_image(mid: int, image_data: str) -> str:
    """Decode a canvas data URL and write it to disk. Returns the stored
    filename. Raises ValueError on anything that isn't a small, real PNG."""
    if not image_data.startswith("data:image/png;base64,"):
        raise ValueError("Signature must be a PNG image.")
    raw = base64.b64decode(image_data.split(",", 1)[1], validate=True)
    if len(raw) < MIN_SIGNATURE_BYTES:
        raise ValueError("The signature looks blank — please sign before saving.")
    if len(raw) > MAX_SIGNATURE_BYTES:
        raise ValueError("Signature image is too large.")

    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "members" / str(mid) / "signatures"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{int(time.time() * 1000)}.png"
    (upload_dir / filename).write_bytes(raw)
    return filename


@contracts_bp.post("/<int:mid>/sign")
@permission_required("all_members")
def sign_contract(mid: int):
    """Capture an on-screen signature (tablet/mobile canvas) for one signer
    role on this member's contract. A member signature counts the same as a
    remote link approval - it flips the compliance checklist's contract
    status - since both are equally valid proof the contract was signed."""
    db = get_db()
    member = db.execute("SELECT id FROM members WHERE id=?", (mid,)).fetchone()
    if member is None:
        return jsonify(ok=False, error="Member not found."), 404

    payload = request.get_json(silent=True) or {}
    signer_role = str(payload.get("signer_role", "")).strip()
    image_data = str(payload.get("image_data", ""))
    if signer_role not in SIGNER_ROLES:
        return jsonify(ok=False, error="Unknown signer."), 400

    try:
        filename = _save_signature_image(mid, image_data)
    except (ValueError, base64.binascii.Error):
        return jsonify(ok=False, error="Could not read that signature. Please try again."), 400

    db.execute(
        """INSERT INTO contract_signatures (member_id, signer_role, image_filename, signed_by)
           VALUES (?, ?, ?, ?)""",
        (mid, signer_role, filename, session.get("user_id")),
    )
    if signer_role == "member":
        _mark_contract_signed(db, mid)
    db.commit()
    return jsonify(
        ok=True,
        image_url=url_for("contracts.signature_image", mid=mid, role=signer_role),
    )


@contracts_bp.get("/<int:mid>/signature/<role>")
@permission_required("all_members")
def signature_image(mid: int, role: str):
    db = get_db()
    row = db.execute(
        """SELECT image_filename FROM contract_signatures
           WHERE member_id = ? AND signer_role = ? ORDER BY signed_at DESC, id DESC LIMIT 1""",
        (mid, role),
    ).fetchone()
    if row is None:
        abort(404)
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "members" / str(mid) / "signatures" / row["image_filename"]
    if not path.exists():
        abort(404)
    return send_file(str(path), mimetype="image/png")
