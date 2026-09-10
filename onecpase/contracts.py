import json
import hashlib
import secrets
from datetime import date, datetime

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
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


def render_contract(contract_template, member):
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
    return render_template(
        "contracts/view.html",
        m=member,
        today=date.today(),
        contract_template=contract_template,
        terms_parts=_load_terms_parts(contract_template, gym_name),
        debit_mandate=debit_mandate,
        section_enabled=section_enabled,
        field_enabled=field_enabled,
        generated_at=datetime.now().strftime("%d %B %Y %H:%M"),
    )


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
        response = request.form.get("response")
        if response not in ("approved", "declined"):
            abort(400)
        db.execute(
            "UPDATE contract_share_tokens SET status=?, responded_at=datetime('now'), response_note=? WHERE id=?",
            (response, request.form.get("note", "").strip()[:500], row["id"]),
        )
        if response == "approved":
            db.execute(
                "UPDATE membership_applications SET contract_status='signed' WHERE member_id=?",
                (row["member_id"],),
            )
            from .applications import ensure_member_application, sync_application_status
            aid = ensure_member_application(db, row["member_id"])
            db.execute(
                "INSERT OR IGNORE INTO compliance_checklists (application_id) VALUES (?)", (aid,)
            )
            db.execute(
                "UPDATE compliance_checklists SET contract_status='signed', updated_at=datetime('now') WHERE application_id=?",
                (aid,),
            )
            sync_application_status(db, aid)
        db.commit()
        return render_template("contracts/share_response.html", approved=response == "approved")
    template = db.execute("SELECT * FROM contract_templates WHERE active=1 ORDER BY is_default DESC,id LIMIT 1").fetchone()
    return render_contract(template, decrypt_member(member))


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
    return render_contract(contract_template, member)
