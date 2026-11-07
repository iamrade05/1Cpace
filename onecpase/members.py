import re
import os
import time
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename
from .auth import permission_required
from .database import CUSTOM_TARIFF, audit_log, current_tenant_name, get_db, member_activity, next_member_ref
from .employee_numbers import current_employee_number, employee_number_context
from .debicheck_rules import account_holder_error, mandate_rules_context
from .member_counts import get_member_counts
from .elev8_data import elev8_source_enabled, get_elev8_member_count
from .communication import collection_care_message
from .encryption import decrypt_member, encrypt_member, hash_for_lookup, encryption_enabled
from .debicheck import (
    insert_mandate,
    mandate_from_member,
    push_mandate_to_nupay,
    record_push_result,
    validate_mandate,
)

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx"}

TT3_DOCUMENT = "TT3 (Manual DebiCheck)"

DOC_TYPES = [
    "SA ID Copy",
    "Bank Statement",
    "Proof of Address",
    "Signed Contract",
    "Debit Mandate",
    TT3_DOCUMENT,
    "Medical / Doctor's Clearance",
    "PAR-Q Form",
    "Other",
]

def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _doc_return_to(mid: int) -> str:
    """Document actions are posted from both the member page and the
    application page — return to whichever one the form came from, rather
    than always bouncing back to the member page. Restricted to a local
    path (never an absolute/protocol-relative URL) to avoid an open redirect."""
    target = request.form.get("return_to", "")
    if target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("members.member_detail", mid=mid) + "#docs"


def _collection_note_parts(note: str | None) -> dict[str, str]:
    parts = {}
    for part in (note or "").split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            parts[key.strip()] = value.strip()
    return parts


def _payment_source(collection, note_parts: dict[str, str]) -> str:
    method = (collection["method"] or "").lower()
    transaction_type = note_parts.get("type", "")
    loaded_by = note_parts.get("loaded_by", "")

    if transaction_type == "Discount":
        return "Discount"
    if method == "debicheck" or "Debit Order Payment" in transaction_type:
        return "Automatic Debit Order"
    if method in {"card", "cash", "eft"}:
        return f"Reception - {method.upper()}"
    if loaded_by and loaded_by != "Automatically Loaded":
        return f"Reception - {loaded_by}"
    return "Manual / Other"


def _build_payment_profile(collections) -> list[dict]:
    rows = []
    payments = []
    failed_by_month = defaultdict(bool)

    for collection in collections:
        note_parts = _collection_note_parts(collection["notes"])
        transaction_type = note_parts.get("type", "")
        month = (collection["collection_date"] or "")[:7]

        if (collection["amount_paid"] or 0) > 0:
            payments.append(
                {
                    "date": collection["collection_date"] or "",
                    "id": collection["id"] or 0,
                    "remaining": float(collection["amount_paid"] or 0),
                    "source": _payment_source(collection, note_parts),
                    "method": (collection["method"] or "").upper(),
                }
            )
        if transaction_type == "Debit Order Unpaid" or collection["status"] == "failed":
            failed_by_month[month] = True

    charge_rows = []
    for collection in collections:
        note_parts = _collection_note_parts(collection["notes"])
        transaction_type = note_parts.get("type", "")
        if transaction_type not in {"Recurring Fee", "Once-Off Fee"}:
            continue
        charge_rows.append((collection, note_parts))

    charge_rows.sort(key=lambda item: (item[0]["collection_date"] or "", item[0]["id"] or 0))
    payments.sort(key=lambda payment: (payment["date"], payment["id"]))

    for collection, note_parts in charge_rows:
        month = (collection["collection_date"] or "")[:7]
        due = float(collection["outstanding_balance"] or 0)
        paid = 0.0
        sources = []

        for payment in payments:
            if paid >= due:
                break
            if payment["remaining"] <= 0:
                continue
            applied = min(payment["remaining"], due - paid)
            payment["remaining"] -= applied
            paid += applied
            if payment["source"] not in sources:
                sources.append(payment["source"])

        balance = max(due - paid, 0.0)
        if balance == 0 and due > 0:
            status = "Paid"
        elif paid > 0:
            status = "Partial"
        elif failed_by_month[month]:
            status = "Failed Debit"
        else:
            status = "Outstanding"

        rows.append(
            {
                "date": collection["collection_date"],
                "description": note_parts.get("description") or collection["notes"] or "",
                "fee_type": "Recurring" if note_parts.get("type") == "Recurring Fee" else "Once-Off",
                "due": due,
                "paid": paid,
                "balance": balance,
                "source": " + ".join(sources) if sources else "No payment matched",
                "status": status,
            }
        )

    return sorted(rows, key=lambda row: row["date"] or "", reverse=True)


def _is_discount(collection) -> bool:
    return _collection_note_parts(collection["notes"]).get("type") == "Discount"


def _get_last_gym_visit(db, member):
    """Use Itensity for its bound gym; local turnstiles for other gyms."""
    from .itensity_attendance import configured_for_request, member_last_visit
    if configured_for_request():
        return member_last_visit(member)
    local = db.execute(
        """SELECT created_at AS last_visited, direction
           FROM turnstile_events WHERE member_id=? AND decision IN ('allowed','granted')
           ORDER BY created_at DESC LIMIT 1""",
        (member["id"],),
    ).fetchone()
    if local:
        try:
            visited = datetime.fromisoformat(str(local["last_visited"]))
            days_ago = (datetime.now() - visited).days
        except (TypeError, ValueError):
            days_ago = None
        return {
            "last_visited": local["last_visited"],
            "days_ago": days_ago,
            "shift": local["direction"] or "Turnstile",
            "source": "1Cpase turnstile",
        }

    return None


members_bp = Blueprint("members", __name__, template_folder="templates/members")
members_bp.context_processor(employee_number_context)
members_bp.context_processor(mandate_rules_context)

TARIFFS = [
    "Couples Membership",
    "Dependent",
    "Gym Swop",
    "Premium 12 2026",
    "Premium 24",
    "Premium 24 Month 2026",
    "Premium 24 month 2026 Exl Levy",
    "Premium 24 2025 Exl Levy",
    "Premium 36 2025",
    "Premium 36 2026",
    "Premium 36 month 2026 Exl. Levy",
    "Rewards program",
    "Black Friday 2025",
    "Student",
    "Pensioner",
    "Staff",
]

BANKS = [
    ("ABSA",          "632005"),
    ("African Bank",  "430000"),
    ("Bidvest Bank/OldMutual", "462005"),
    ("Capitec",       "470010"),
    ("Discovery Bank","679000"),
    ("FNB/RMB",       "250655"),
    ("Investec",      "580105"),
    ("Nedbank",       "198765"),
    ("PostBank",      "460005"),
    ("Sasfin Bank",   "683000"),
    ("Standard Bank", "051001"),
    ("Tyme Bank",     "678910"),
    ("U-Bank",        "431010"),
    ("Other",         ""),
]


def _get_form_data():
    f = request.form
    return dict(
        first_name          = f.get("first_name", "").strip(),
        last_name           = f.get("last_name", "").strip(),
        id_number           = f.get("id_number", "").strip(),
        contact             = f.get("contact", "").strip(),
        email               = f.get("email", "").strip(),
        date_of_birth       = f.get("date_of_birth", ""),
        gender              = f.get("gender", "").strip(),
        join_date           = f.get("join_date", ""),
        member_status       = f.get("member_status", "Active").strip(),
        source              = f.get("source", "Walk-in").strip(),
        entry_type          = f.get("entry_type", "appointment").strip(),
        tariff              = f.get("tariff", "").strip(),
        package             = f.get("package", "").strip(),
        custom_name         = f.get("custom_name", "").strip(),
        custom_amount       = f.get("custom_amount", "").strip(),
        custom_months       = f.get("custom_months", "").strip(),
        custom_type         = f.get("custom_type", "").strip(),
        custom_note         = f.get("custom_note", "").strip(),
        custom_package_note = "",
        contract_duration   = f.get("contract_duration", "").strip(),
        monthly_installment = f.get("monthly_installment", "0") or "0",
        uploaded_by_id      = f.get("uploaded_by_id", "") or None,
        street_number       = f.get("street_number", "").strip(),
        street_name         = f.get("street_name", "").strip(),
        suburb              = f.get("suburb", "").strip(),
        city                = f.get("city", "").strip(),
        postal_code         = f.get("postal_code", "").strip(),
        country             = f.get("country", "South Africa").strip(),
        payment_type        = f.get("payment_type", "Debit Order").strip(),
        debit_order_date    = f.get("debit_order_date", "").strip(),
        bank                = f.get("bank", "").strip(),
        account_type        = f.get("account_type", "").strip(),
        account_name        = f.get("account_name", "").strip(),
        account_number      = f.get("account_number", "").strip(),
        branch_code         = f.get("branch_code", "").strip(),
        payer_name          = f.get("payer_name", "").strip(),
        payer_id_number     = f.get("payer_id_number", "").strip(),
        payer_type          = f.get("payer_type", "self").strip(),
        debicheck_auth_type = f.get("debicheck_auth_type", "cell").strip(),
        debicheck_client_ref1 = f.get("debicheck_client_ref1", "").strip(),
        debicheck_client_ref2 = current_employee_number() or f.get("debicheck_client_ref2", "").strip(),
        debicheck_frequency = f.get("debicheck_frequency", "3").strip(),
        debicheck_installments = f.get("debicheck_installments", "1").strip(),
        debicheck_tracking = f.get("debicheck_tracking", "14").strip(),
        debicheck_submit_date = f.get("debicheck_submit_date", "").strip(),
        debicheck_id_type = f.get("debicheck_id_type", "0").strip(),
        debicheck_passport = f.get("debicheck_passport", "").strip(),
        debicheck_juristic = f.get("debicheck_juristic", "").strip(),
        opt_in              = 1 if f.get("opt_in") == "yes" else 0,
        fitness_goal        = f.get("fitness_goal", "").strip(),
        training_experience = 1 if f.get("training_experience") == "yes" else 0,
        promotion           = f.get("promotion", "").strip(),
        access_level        = f.get("access_level", "Local").strip(),
        access_credential   = f.get("access_credential", "").strip(),
        occupation          = f.get("occupation", "").strip(),
        employer            = f.get("employer", "").strip(),
        alternative_number  = f.get("alternative_number", "").strip(),
        work_number         = f.get("work_number", "").strip(),
        joining_fee         = f.get("joining_fee", "0") or "0",
        member_addons       = f.get("member_addons", "").strip(),
        witness_name        = f.get("witness_name", "").strip(),
        emergency_contact_name     = f.get("emergency_contact_name", "").strip(),
        emergency_contact_number   = f.get("emergency_contact_number", "").strip(),
        emergency_contact_relation = f.get("emergency_contact_relation", "").strip(),
        parq_q1 = 1 if f.get("parq_q1") == "yes" else 0,
        parq_q2 = 1 if f.get("parq_q2") == "yes" else 0,
        parq_q3 = 1 if f.get("parq_q3") == "yes" else 0,
        parq_q4 = 1 if f.get("parq_q4") == "yes" else 0,
        parq_q5 = 1 if f.get("parq_q5") == "yes" else 0,
        parq_q6 = 1 if f.get("parq_q6") == "yes" else 0,
        parq_q7 = 1 if f.get("parq_q7") == "yes" else 0,
        parq_q8 = 1 if f.get("parq_q8") == "yes" else 0,
        parq_q9 = 1 if f.get("parq_q9") == "yes" else 0,
    )


@members_bp.route("/members")
@permission_required("all_members")
def members_index():
    db = get_db()
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")

    restrict_to_own = bool(session.get("is_sales_consultant"))

    # Signed by: whoever loaded the client here, else their lead's consultant or
    # the application's creator, else Itensity's own "sold by" for imported members.
    base_sql = """
        SELECT m.id, m.member_ref, m.first_name, m.last_name, m.contact, m.email, m.id_number,
               m.created_at,
               m.join_date, m.member_status, m.tariff, m.payment_type, m.monthly_installment,
               m.itensity_ref, u.full_name AS consultant_name,
               COALESCE(u.full_name, lead_user.full_name, app_user.full_name,
                        NULLIF(TRIM(m.sold_by), '')) AS signed_by
        FROM members m
        LEFT JOIN users u ON m.uploaded_by_id = u.id
        LEFT JOIN membership_applications ma
          ON ma.id = (SELECT MAX(x.id) FROM membership_applications x WHERE x.member_id = m.id)
        LEFT JOIN leads l ON l.id = ma.lead_id
        LEFT JOIN users lead_user ON lead_user.id = l.assigned_to
        LEFT JOIN users app_user ON app_user.id = ma.created_by
        WHERE 1=1
    """
    params = []

    if restrict_to_own:
        base_sql += " AND m.uploaded_by_id = ?"
        params.append(session["user_id"])

    if q:
        like = f"%{q}%"
        if encryption_enabled():
            # id_number is encrypted — use the HMAC hash for exact-match lookup
            base_sql += """ AND (
                m.first_name LIKE ? OR m.last_name LIKE ?
                OR (m.first_name || ' ' || m.last_name) LIKE ?
                OR m.contact LIKE ? OR m.id_number_hash = ?
                OR m.email LIKE ? OR m.itensity_ref LIKE ? OR m.member_ref LIKE ?
                OR m.sold_by LIKE ? OR u.full_name LIKE ?
                OR EXISTS (SELECT 1 FROM debicheck_mandates d WHERE d.member_id = m.id
                           AND (d.contract_reference LIKE ? OR d.nupay_response LIKE ?))
            )"""
            params += [like, like, like, like, hash_for_lookup(q.strip()), like, like, like, like, like, like, like]
        else:
            base_sql += """ AND (
                m.first_name LIKE ? OR m.last_name LIKE ?
                OR (m.first_name || ' ' || m.last_name) LIKE ?
                OR m.contact LIKE ? OR m.id_number LIKE ?
                OR m.email LIKE ? OR m.itensity_ref LIKE ? OR m.member_ref LIKE ?
                OR m.sold_by LIKE ? OR u.full_name LIKE ?
                OR EXISTS (SELECT 1 FROM debicheck_mandates d WHERE d.member_id = m.id
                           AND (d.contract_reference LIKE ? OR d.nupay_response LIKE ?))
            )"""
            params += [like, like, like, like, like, like, like, like, like, like, like, like]

    if status_filter:
        base_sql += " AND m.member_status = ?"
        params.append(status_filter)

    base_sql += " ORDER BY m.join_date DESC, m.id DESC"

    rows = db.execute(base_sql, params).fetchall()
    contract_refs = nupay_contract_references(db)

    member_counts = get_member_counts(
        db,
        uploaded_by_id=session["user_id"] if restrict_to_own else None,
    )

    # dbo.Members is the authoritative current Elev8 roster/count, and the
    # local members table stays the operational UI dataset. The lookup returns
    # None when SQL Server is unavailable, so the local total is the fallback
    # rather than a fabricated zero. It is deliberately skipped for a
    # consultant restricted to their own uploads: that total is theirs, and
    # the gym-wide roster count would silently replace it with everyone's.
    authoritative_total_members = get_elev8_member_count() if not restrict_to_own and elev8_source_enabled() else None
    total_members = (
        authoritative_total_members
        if authoritative_total_members is not None
        else member_counts["total"]
    )

    return render_template(
        "members/index.html",
        members=rows,
        contract_refs=contract_refs,
        search=q,
        status_filter=status_filter,
        status_counts=member_counts["by_status"],
        total_members=total_members,
        authoritative_total_members=authoritative_total_members,
    )


_CONTRACT_REF_RE = re.compile(r"Contract(?:\s+Reference)?\s*:?\s*([A-Z]{2,}[A-Z0-9]{6,})")


def nupay_contract_references(db) -> dict[int, str]:
    """Member id -> NuPay contract reference, from their active mandate when there is one.

    Older mandates only carry the reference inside NuPay's response text.
    """
    best: dict[int, tuple] = {}
    for row in db.execute(
        "SELECT id, member_id, status, contract_reference, nupay_response FROM debicheck_mandates"
    ).fetchall():
        ref = (row["contract_reference"] or "").strip()
        if not ref:
            found = _CONTRACT_REF_RE.search(row["nupay_response"] or "")
            ref = found.group(1) if found else ""
        if not ref:
            continue
        rank = (1 if row["status"] in ("active", "approved") else 0, row["id"])
        if row["member_id"] not in best or rank > best[row["member_id"]][0]:
            best[row["member_id"]] = (rank, ref)
    return {member_id: ref for member_id, (_rank, ref) in best.items()}


def _get_tariffs():
    return get_db().execute(
        "SELECT * FROM tariffs WHERE active=1 ORDER BY sort_order, name"
    ).fetchall()


def _prefill_from_lead(lead, tariff_rows) -> dict:
    """Add Member form values already known from the prospect being converted."""
    first_name, _, last_name = (lead["full_name"] or "").strip().partition(" ")
    d = {
        "first_name": first_name,
        "last_name": last_name.strip(),
        "contact": lead["phone"] or "",
        "email": lead["email"] or "",
        "opt_in": lead["marketing_consent"] if lead["marketing_consent"] is not None else 1,
        "uploaded_by_id": lead["assigned_to"] or session.get("user_id") or "",
    }
    interest = (lead["interested_package"] or "").strip()
    for t in tariff_rows:
        # Older prospects stored the convert form's "<name> R<amount>" label.
        if interest and interest in (t["name"], f"{t['name']} R{t['monthly_amount']:.0f}"):
            d.update(
                tariff=t["name"], package=t["name"],
                contract_duration=f"{t['duration_months']} months",
                monthly_installment=t["monthly_amount"],
            )
            break
    else:
        if interest:
            d["package"] = interest
    return d


# ── Custom package (a manager's call) ─────────────────────────────────────────

CUSTOM_PACKAGE_TYPES = ("Family package", "Different agreement", "Other")
_CUSTOM_RAW_FIELDS = ("custom_name", "custom_amount", "custom_months", "custom_type", "custom_note")


def _is_manager() -> bool:
    return session.get("role") in {"admin", "manager"}


def _apply_custom_package(d, existing=None):
    """Enforce and resolve the custom-package part of a member form.

    A custom package - a different agreement, or a family package - is a manager
    decision. Returns an error message, or None once ``d`` holds the resolved
    package name, amount, duration and reason. A non-manager can never set one,
    and can neither change nor wipe one a manager already set.
    """
    was_custom = bool(existing) and existing.get("tariff") == CUSTOM_TARIFF
    if not _is_manager():
        if was_custom:
            for field in ("tariff", "package", "contract_duration", "monthly_installment", "custom_package_note"):
                d[field] = existing.get(field) or ("0" if field == "monthly_installment" else "")
        elif d["tariff"] == CUSTOM_TARIFF:
            return "Only a manager can set a custom package."
        return None
    if d["tariff"] != CUSTOM_TARIFF:
        d["custom_package_note"] = ""
        return None

    try:
        amount = Decimal(d["custom_amount"])
    except InvalidOperation:
        amount = None
    if amount is None or not amount.is_finite() or amount < 0 or amount > Decimal("99999.99"):
        return "A custom package needs a monthly amount between R0.00 and R99,999.99."
    try:
        months = int(d["custom_months"])
    except ValueError:
        months = 0
    if not 1 <= months <= 120:
        return "A custom package needs a contract length of 1 to 120 months."
    if not d["custom_name"]:
        return "A custom package needs a name, for example the family name."
    if d["custom_type"] not in CUSTOM_PACKAGE_TYPES:
        return "A custom package needs a reason type: family package, different agreement or other."
    if not d["custom_note"]:
        return "A custom package needs a note saying what was agreed."

    d["package"] = d["custom_name"]
    d["monthly_installment"] = f"{amount.quantize(Decimal('0.01')):.2f}"
    d["contract_duration"] = f"{months} months"
    d["custom_package_note"] = f"{d['custom_type']}: {d['custom_note']}"
    for field in _CUSTOM_RAW_FIELDS:
        d.pop(field, None)
    return None


def _custom_form_values(member):
    """The stored custom package as the form's own fields, to pre-fill the manager panel."""
    if member.get("tariff") != CUSTOM_TARIFF:
        return {}
    kind, _, note = (member.get("custom_package_note") or "").partition(": ")
    months = (member.get("contract_duration") or "").split(" ")[0]
    return {
        "custom_name": member.get("package") or "",
        "custom_amount": f"{float(member.get('monthly_installment') or 0):.2f}",
        "custom_months": months if months.isdigit() else "",
        "custom_type": kind,
        "custom_note": note,
    }


def _custom_package_activity(before, d):
    """The activity-log line for a change to a member's custom package, or None."""
    was = bool(before) and before.get("tariff") == CUSTOM_TARIFF
    if d["tariff"] != CUSTOM_TARIFF:
        return f"Custom package removed; member moved to {d['tariff'] or 'no tariff'}." if was else None
    unchanged = was and (
        (before.get("package") or "") == d["package"]
        and float(before.get("monthly_installment") or 0) == float(d["monthly_installment"])
        and (before.get("contract_duration") or "") == d["contract_duration"]
        and (before.get("custom_package_note") or "") == d["custom_package_note"]
    )
    if unchanged:
        return None
    return (
        f"Custom package set: {d['package']}, R{float(d['monthly_installment']):.2f} a month, "
        f"{d['contract_duration']}. {d['custom_package_note']}"
    )


@members_bp.route("/members/add", methods=["GET", "POST"])
@permission_required("add_member")
def members_add():
    db = get_db()
    consultants = db.execute(
        "SELECT id, full_name FROM users WHERE active = 1 ORDER BY full_name"
    ).fetchall()
    tariff_rows = _get_tariffs()
    lead = None
    lead_id = request.args.get("lead_id", type=int)
    if lead_id:
        from .leads import lead_conversion_redirect
        lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        blocked = lead_conversion_redirect(db, lead)
        if blocked:
            return blocked

    def _form(d):
        from datetime import date
        return render_template("members/add.html", d=d, tariffs=TARIFFS,
                               tariff_rows=tariff_rows, banks=BANKS,
                               consultants=consultants, lead=lead,
                               today=date.today().isoformat())

    if request.method == "POST":
        d = _get_form_data()
        if lead is not None:
            # Attribution comes from the prospect record, never the form.
            d["source"] = lead["original_source"] or lead["source"] or d["source"]
            d["entry_type"] = "lead"
        d["payer_type"] = (
            "third_party" if d["payment_type"] == "Third-Party Debit Order" else "self"
        )
        push_nupay = request.form.get("submit_action") == "push_nupay"
        if not (d["first_name"] and d["last_name"] and d["id_number"] and d["contact"]):
            flash("First name, last name, ID number, and cell number are required.", "error")
            return _form(d)

        custom_error = _apply_custom_package(d)
        if custom_error:
            flash(custom_error, "error")
            return _form(d)

        if d["debicheck_id_type"] == "1":
            if not d["id_number"]:
                flash("Passport number is required.", "error")
                return _form(d)
            d["debicheck_passport"] = d["id_number"]
        elif not (d["id_number"].isdigit() and len(d["id_number"]) == 13):
            flash("RSA ID number must be exactly 13 digits, or select Passport as the identity type.", "error")
            return _form(d)

        # Duplicate ID check (use hash when encryption is active, plaintext otherwise)
        if encryption_enabled():
            dup = db.execute(
                "SELECT id FROM members WHERE id_number_hash = ?",
                (hash_for_lookup(d["id_number"]),),
            ).fetchone()
        else:
            dup = db.execute(
                "SELECT id FROM members WHERE id_number = ?", (d["id_number"],)
            ).fetchone()
        if dup:
            flash(f"A member with this ID number already exists (member #{dup['id']}).", "error")
            return _form(d)

        if d["payment_type"] in ("Debit Order", "Third-Party Debit Order"):
            missing_bank = [label for field, label in (
                ("account_name", "Account holder name"),
                ("bank", "Bank"),
                ("account_type", "Account type"),
                ("account_number", "Account number"),
                ("branch_code", "Branch code"),
            ) if not d.get(field)]
            name_error = account_holder_error(d.get("account_name"), d.get("debicheck_id_type") == "4")
            if missing_bank or name_error:
                if name_error:
                    flash(name_error, "error")
                if missing_bank:
                    flash(f"Debit order requires: {', '.join(missing_bank)}.", "error")
                return _form(d)

        if push_nupay:
            if d["payment_type"] not in ("Debit Order", "Third-Party Debit Order"):
                errors = ["NuPay push is only available for debit-order payments."]
            else:
                errors = validate_mandate(d, sales=True)
            if errors:
                for error in errors:
                    flash(error, "error")
                d["_open_payment"] = 1
                return _form(d)
        try:
            # Direct member creation is an onboarding shortcut, never a JOINED shortcut.
            # A normal staff user creates an Unverified member + application; only the
            # controlled application/Itensity workflow can produce Active/Joined.
            lifecycle_override = session.get("role") in {"admin", "manager"} and "member_lifecycle_change" in session.get("permissions", set())
            if not lifecycle_override:
                d["member_status"] = "Unverified"
                d["join_date"] = ""
                d["entry_type"] = d.get("entry_type") or "manual_onboarding"
            ed = encrypt_member(d)  # encrypts id_number, account_number, branch_code, payer_id_number
            member_ref = next_member_ref(db, current_tenant_name())
            cursor = db.execute(
                """INSERT INTO members
                   (member_ref, first_name, last_name, id_number, id_number_hash, contact, email,
                    date_of_birth, gender, join_date, member_status, source, entry_type,
                    tariff, package, contract_duration, monthly_installment,
                    uploaded_by_id, street_number, street_name, suburb, city,
                    postal_code, country, payment_type, debit_order_date, bank,
                    account_type, account_name, account_number, branch_code,
                    payer_name, payer_id_number, opt_in,
                    fitness_goal, training_experience,
                    promotion, access_level, occupation,
                    emergency_contact_name, emergency_contact_number, emergency_contact_relation,
                    parq_q1, parq_q2, parq_q3, parq_q4, parq_q5,
                    parq_q6, parq_q7, parq_q8, parq_q9, outcome, custom_package_note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (member_ref, ed["first_name"], ed["last_name"], ed["id_number"], ed.get("id_number_hash"),
                 ed["contact"], ed["email"],
                 ed["date_of_birth"], ed["gender"], ed["join_date"], ed["member_status"],
                 ed["source"], ed["entry_type"], ed["tariff"], ed["package"],
                 ed["contract_duration"], float(ed["monthly_installment"]),
                 int(ed["uploaded_by_id"]) if ed["uploaded_by_id"] else None,
                 ed["street_number"], ed["street_name"], ed["suburb"], ed["city"],
                 ed["postal_code"], ed["country"], ed["payment_type"], ed["debit_order_date"],
                 ed["bank"], ed["account_type"], ed["account_name"], ed["account_number"], ed["branch_code"],
                 ed["payer_name"], ed["payer_id_number"], ed["opt_in"],
                 ed["fitness_goal"], ed["training_experience"],
                 ed["promotion"], ed["access_level"], ed["occupation"],
                 ed["emergency_contact_name"], ed["emergency_contact_number"], ed["emergency_contact_relation"],
                 ed["parq_q1"], ed["parq_q2"], ed["parq_q3"], ed["parq_q4"], ed["parq_q5"],
                 ed["parq_q6"], ed["parq_q7"], ed["parq_q8"], ed["parq_q9"], "application",
                 ed["custom_package_note"]),
            )
            mid = cursor.lastrowid
            from .applications import ensure_member_application
            intake_aid = ensure_member_application(db, mid, d["package"] or d["tariff"],
                                                   lead_id=lead["id"] if lead is not None else None)
            member_activity(db, mid, "Member profile created")
            custom_note = _custom_package_activity(None, d)
            if custom_note:
                member_activity(db, mid, custom_note)
            if lead is not None:
                from .leads import record_lead_conversion, sa_id_luhn_valid
                record_lead_conversion(db, lead, mid, intake_aid, d["id_number"], d["date_of_birth"])
                if d["debicheck_id_type"] != "1" and not sa_id_luhn_valid(d["id_number"]):
                    flash(
                        "That ID number failed the standard check-digit test — please "
                        "confirm it against the client's ID document. Conversion has "
                        "continued, but the number may be mistyped.",
                        "warning",
                    )
                member_activity(db, mid, f"Converted from prospect #{lead['id']}")
            db.commit()

            if push_nupay:
                mandate = mandate_from_member(mid, d)
                app_row = db.execute(
                    "SELECT id FROM membership_applications "
                    "WHERE member_id = ? ORDER BY id DESC LIMIT 1", (mid,)
                ).fetchone()
                mandate["application_id"] = app_row["id"] if app_row else None
                mandate["owner_id"] = session.get("user_id")
                from .encryption import encrypt_mandate
                mandate_id = insert_mandate(db, encrypt_mandate(mandate), session.get("user_id"))
                member_activity(db, mid, f"DebiCheck mandate #{mandate_id} created")
                db.commit()
                from .applications import nupay_override_note, nupay_submission_blockers, authorize_nupay_override
                blockers = nupay_submission_blockers(db, mid)
                if blockers and not authorize_nupay_override(db, mid, blockers):
                    flash("Member and mandate saved. NuPay submission blocked: " + "; ".join(blockers), "warning")
                    return redirect(url_for("applications.application_detail", aid=intake_aid, intake=1))
                override_note = nupay_override_note(db, mid, blockers) if blockers else None
                from .debicheck_limits import claim_send, finish_send
                claim_id, limit = claim_send(db, mandate_id, mid)
                if limit:
                    db.commit()
                    flash("Member and mandate saved. Not sent to NuPay: " + limit, "warning")
                    return redirect(url_for("applications.application_detail", aid=intake_aid, intake=1))
                db.commit()  # Persist an explicit admin exception and the day's send before external submission.
                try:
                    pushed, message = push_mandate_to_nupay(mandate)
                except Exception as exc:
                    pushed, message = False, f"NuPay integration error: {exc}"
                finish_send(db, claim_id, pushed)
                record_push_result(db, mandate_id, pushed, message)
                member_activity(
                    db, mid,
                    f"DebiCheck mandate #{mandate_id} "
                    f"{'submitted to NuPay' if pushed else 'push failed'}: {message}",
                )
                if pushed and override_note:
                    member_activity(db, mid, override_note)
                db.commit()
                if pushed:
                    flash(
                        f"Member saved and DebiCheck sent to NuPay for client approval: {message}",
                        "success",
                    )
                else:
                    flash(
                        f"Member saved, but the NuPay push failed: {message}. The mandate can be retried.",
                        "warning",
                    )
                return redirect(url_for("applications.application_detail", aid=intake_aid, intake=1))
            
            # Itensity push happens from the compliance-approved application flow.
            flash(f"Member {d['first_name']} {d['last_name']} added.", "success")

            if d["payment_type"] in ("Debit Order", "Third-Party Debit Order"):
                flash("Upload at least two months of bank statements to open DebiCheck.", "info")
                return redirect(url_for("applications.application_detail", aid=intake_aid, intake=1))

            return redirect(url_for("applications.application_detail", aid=intake_aid, intake=1))
        except Exception:
            db.rollback()
            current_app.logger.exception("Failed to add member")
            flash("Failed to save member. Please check the details and try again.", "error")
            return _form(d)

    return _form(_prefill_from_lead(lead, tariff_rows) if lead is not None else {})


@members_bp.route("/members/<int:mid>")
@permission_required("all_members")
def member_detail(mid: int):
    db = get_db()
    member = db.execute(
        """SELECT m.*, u.full_name AS consultant_name
           FROM members m LEFT JOIN users u ON m.uploaded_by_id = u.id
           WHERE m.id = ?""",
        (mid,),
    ).fetchone()
    if member is None:
        flash("Member not found.", "warning")
        return redirect(url_for("members.members_index"))
    member = decrypt_member(member)

    collections = db.execute(
        "SELECT * FROM collections WHERE member_id = ? ORDER BY collection_date DESC, id DESC",
        (mid,),
    ).fetchall()

    application = db.execute(
        "SELECT * FROM membership_applications WHERE member_id = ?", (mid,)
    ).fetchone()

    # Itensity push readiness — reuses the same rules applications.py enforces
    # so the profile's shortcut button can never be more permissive than the
    # application page's own push button.
    from .applications import MANAGER_ROLES, _is_ready_to_push, _missing_requirements, _push_analysis, can_override_push, needs_statement_check
    itensity_checklist = None
    itensity_ready = False
    itensity_missing = []
    if application is not None:
        itensity_checklist = db.execute(
            "SELECT * FROM compliance_checklists WHERE application_id = ?", (application["id"],)
        ).fetchone()
        latest_analysis = db.execute(
            "SELECT * FROM statement_analyses WHERE application_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (application["id"],),
        ).fetchone()
        from .applications import _itensity_hard_blockers
        evidence_blockers = _itensity_hard_blockers(db, application["id"], mid)
        latest_analysis = _push_analysis(db, mid, latest_analysis)
        itensity_ready = (
            _is_ready_to_push(itensity_checklist, latest_analysis)
            and not evidence_blockers
            and application["push_status"] not in ("pushed", "pushing")
            if itensity_checklist else False
        )
        itensity_missing = list(dict.fromkeys(_missing_requirements(itensity_checklist, latest_analysis) + evidence_blockers))

    notes = db.execute(
        """SELECT n.*, u.full_name AS author
           FROM member_notes n
           LEFT JOIN users u ON n.created_by = u.id
           WHERE n.member_id = ?
           ORDER BY n.created_at DESC""",
        (mid,),
    ).fetchall()

    import json as _json
    raw_docs = db.execute(
        """SELECT d.*, u.full_name AS uploader
           FROM member_documents d
           LEFT JOIN users u ON d.uploaded_by = u.id
           WHERE d.member_id = ?
           ORDER BY d.uploaded_at DESC""",
        (mid,),
    ).fetchall()
    documents = []
    for d in raw_docs:
        row = dict(d)
        row["analysis"] = _json.loads(d["analysis_json"]) if d["analysis_json"] else None
        documents.append(row)

    payment_profile   = _build_payment_profile(collections)
    from .financial_reporting import totals as financial_totals
    total_paid        = financial_totals(collections)['total_collected']
    total_outstanding = sum(r["balance"] for r in payment_profile)

    # ── PTP / Collection data ──────────────────────────────────────────────
    from .ptp import (compute_arrears_from_profile, CALL_CODES,
                      PTP_PAYMENT_METHODS, ARRANGEMENT_TYPES, PTP_STATUSES,
                      _has_submitted_mandate)

    ptp_agreements = db.execute("""
        SELECT p.*, uc.full_name AS created_by_name, ua.full_name AS approved_by_name
        FROM ptp_agreements p
        LEFT JOIN users uc ON p.created_by = uc.id
        LEFT JOIN users ua ON p.approved_by = ua.id
        WHERE p.member_id = ?
        ORDER BY p.created_at DESC
    """, (mid,)).fetchall()

    contact_logs = db.execute("""
        SELECT cl.*, u.full_name AS consultant_name
        FROM contact_logs cl
        LEFT JOIN users u ON cl.consultant_id = u.id
        WHERE cl.member_id = ?
        ORDER BY cl.created_at DESC
        LIMIT 25
    """, (mid,)).fetchall()

    ptp_receipts = db.execute("""
        SELECT r.*, u.full_name AS uploaded_by_name, v.full_name AS verified_by_name
        FROM ptp_receipts r
        LEFT JOIN users u ON r.uploaded_by = u.id
        LEFT JOIN users v ON r.verified_by = v.id
        WHERE r.member_id = ?
        ORDER BY r.uploaded_at DESC
    """, (mid,)).fetchall()

    # Payment plan items for active PTPs
    plan_items = {}
    for ptp in ptp_agreements:
        if ptp["ptp_status"] in ("pending", "partially_paid"):
            items = db.execute(
                "SELECT * FROM payment_plan_items WHERE ptp_id=? ORDER BY instalment_number",
                (ptp["id"],),
            ).fetchall()
            if items:
                plan_items[ptp["id"]] = list(items)

    arrears_info = compute_arrears_from_profile(payment_profile)
    has_submitted_mandate = _has_submitted_mandate(db, mid)
    mandate = db.execute(
        """SELECT * FROM debicheck_mandates WHERE member_id=?
           ORDER BY created_at DESC, id DESC LIMIT 1""",
        (mid,),
    ).fetchone()

    # Active PTP (most recent pending/partially_paid)
    active_ptp = next(
        (p for p in ptp_agreements if p["ptp_status"] in ("pending", "partially_paid")),
        None,
    )

    # ── Queries linked to this member ───────────────────────────────────────
    member_queries = db.execute("""
        SELECT q.*, u.full_name AS assigned_to_name
        FROM queries q LEFT JOIN users u ON q.assigned_to = u.id
        WHERE q.member_id = ?
        ORDER BY q.created_at DESC
        LIMIT 20
    """, (mid,)).fetchall()
    open_queries_count = sum(
        1 for q in member_queries if q["status"] not in ("resolved", "closed")
    )

    # Arrived from a query's "Convert to PTP": the PTP form carries the query
    # id so the new arrangement is linked back to it, and a discount a manager
    # approved on that query is filled in.
    ptp_from_query = None
    from_query = request.args.get("from_query", "")
    if from_query.isdigit():
        ptp_from_query = db.execute(
            """SELECT id, reference, approval_status, proposed_discount_pct
               FROM queries WHERE id=? AND member_id=? AND status NOT IN ('resolved','closed')""",
            (int(from_query), mid),
        ).fetchone()

    last_gym_visit = _get_last_gym_visit(db, member)
    tenant_name = current_tenant_name()

    collection_case = db.execute(
        """SELECT c.*, u.full_name AS owner_name, s.full_name AS sales_name
           FROM collections_cases c
           LEFT JOIN users u ON u.id = c.owner_staff_id
           LEFT JOIN users s ON s.id = c.original_sales_consultant_id
           WHERE c.member_id = ? AND c.status IN ('open','active','pending')
           ORDER BY c.id DESC LIMIT 1""",
        (mid,),
    ).fetchone()

    # What NuPay says about this member's debits (None until a NuPay report has been uploaded).
    nupay = None
    if session.get("role") == "admin" or "all_collections" in (session.get("permissions") or []):
        try:
            from .nupay_reconciliation import member_view

            nupay = member_view(db, mid)
        except Exception:
            current_app.logger.exception("NuPay summary failed for member %s", mid)

    return render_template(
        "members/detail.html",
        member=member,
        nupay=nupay,
        collection_case=collection_case,
        collections=collections,
        payment_profile=payment_profile,
        application=application,
        itensity_ready=itensity_ready,
        itensity_missing=itensity_missing,
        itensity_can_override=can_override_push(session.get("role"), itensity_missing),
        itensity_needs_statement_check=needs_statement_check(session.get("role"), itensity_missing),
        is_manager=session.get("role") in MANAGER_ROLES,
        notes=notes,
        documents=documents,
        doc_types=DOC_TYPES,
        total_paid=total_paid,
        total_outstanding=total_outstanding,
        banks=BANKS,
        # PTP data
        ptp_agreements=ptp_agreements,
        contact_logs=contact_logs,
        ptp_receipts=ptp_receipts,
        plan_items=plan_items,
        arrears_info=arrears_info,
        has_tt3=bool(db.execute(
            "SELECT 1 FROM member_documents WHERE member_id=? AND document_type=? LIMIT 1", (mid, TT3_DOCUMENT),
        ).fetchone()),
        latest_debicheck_check=db.execute(
            "SELECT * FROM nupay_mandate_checks WHERE member_id=? ORDER BY id DESC LIMIT 1", (mid,)
        ).fetchone(),
        has_submitted_mandate=has_submitted_mandate,
        mandate=mandate,
        active_ptp=active_ptp,
        call_codes=CALL_CODES,
        ptp_payment_methods=PTP_PAYMENT_METHODS,
        arrangement_types=ARRANGEMENT_TYPES,
        ptp_statuses=PTP_STATUSES,
        last_gym_visit=last_gym_visit,
        member_queries=member_queries,
        open_queries_count=open_queries_count,
        ptp_from_query=ptp_from_query,
        whatsapp_message=collection_care_message(member["first_name"], tenant_name),
    )


@members_bp.post("/members/<int:mid>/check-debicheck")
@permission_required("all_members")
def member_check_debicheck(mid: int):
    from .debicheck import check_member_on_nupay

    db = get_db()
    row = db.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
    if row is None:
        abort(404)
    result = check_member_on_nupay(decrypt_member(row))
    if result["state"] == "error":
        flash("Unable to verify DebiCheck: " + result["message"], "warning")
    else:
        db.execute(
            """INSERT INTO nupay_mandate_checks
               (member_id, state, status, contract_reference, message, checked_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mid, result["state"], result["status"], result["contract_reference"],
             result["message"], session.get("user_id")),
        )
        member_activity(db, mid, "DebiCheck check on NuPay: " + result["message"])
        db.commit()
        labels = {"active": "Active DebiCheck mandate confirmed", "inactive": "Mandate found, but it is not active", "none": "No DebiCheck mandate found"}
        flash(labels[result["state"]] + ". " + result["message"],
              "success" if result["state"] == "active" else "warning")
    return redirect(url_for("members.member_detail", mid=mid) + "#debicheck-check")


@members_bp.post("/members/<int:mid>/check-tt3")
@permission_required("all_members")
def member_check_tt3(mid: int):
    """Ask NuPay again: a TT3 can take time to go live after the form is uploaded."""
    from .tt3 import start_check

    db = get_db()
    if not db.execute(
        "SELECT 1 FROM member_documents WHERE member_id=? AND document_type=? LIMIT 1", (mid, TT3_DOCUMENT),
    ).fetchone():
        abort(404)
    if start_check(db, mid):
        flash("Checking NuPay for an active DebiCheck. Refresh in about a minute for the result.", "info")
    else:
        flash("A NuPay check is already running for this member.", "warning")
    return redirect(url_for("members.member_detail", mid=mid) + "#debicheck-check")


@members_bp.route("/members/<int:mid>/notes", methods=["POST"])
@permission_required("all_members")
def member_add_note(mid: int):
    note_text = request.form.get("note", "").strip()
    if note_text:
        db = get_db()
        member_activity(db, mid, note_text)
        db.commit()
    return redirect(url_for("members.member_detail", mid=mid) + "#notes")


@members_bp.route("/members/<int:mid>/documents", methods=["POST"])
@permission_required("all_members")
def member_upload_doc(mid: int):
    return _upload_member_doc(mid)


def _upload_member_doc(mid: int, inline=False):
    """Shared document storage and analysis for member and DebiCheck screens."""
    from flask import jsonify
    from .applications import ensure_member_application, sync_application_status
    from .statement_uploads import upload_statements
    from uuid import uuid4
    db = get_db()
    if not db.execute("SELECT 1 FROM members WHERE id=?", (mid,)).fetchone():
        abort(404)
    def finish(message, category="success", status=200, analysis=None):
        if inline:
            return jsonify(message=message, category=category, analysis=analysis), status
        flash(message, category)
        return redirect(_doc_return_to(mid))
    doc_type = request.form.get("document_type", "Other")
    if doc_type not in DOC_TYPES:
        return finish("Select a valid document type.", "error", 400)
    files = [f for f in request.files.getlist("document") if f and f.filename]
    if doc_type == "Bank Statement":
        try:
            result = upload_statements(db, mid, files)
        except ValueError as exc:
            return finish(str(exc), "error", 400)
        db.commit()
        cash = result["cashflow"]
        if result.get("error"):
            return finish(f"Statements uploaded, but automatic analysis failed: {result['error']}", "warning", analysis=result)
        message = (f"Bank statements uploaded and analysed. {cash.get('note', '')} "
                   f"Average money in R{cash['avg_money_in']:,.2f}; money out R{cash['avg_money_out']:,.2f}; "
                   f"surplus R{cash['avg_surplus']:,.2f}. Suggested debit day: {cash['suggested_debit_day'] or 'unavailable'}.")
        return finish(message, "success" if cash['enough_months'] else "warning", analysis=result)
    if not files:
        return finish("No file selected.", "error", 400)
    if not all(_allowed(f.filename) for f in files):
        return finish("File type not allowed. Use PDF, JPG, PNG, DOC, DOCX, XLS, XLSX.", "error", 400)
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "members" / str(mid)
    upload_dir.mkdir(parents=True, exist_ok=True)
    for file in files:
        stored_name = f"{uuid4().hex}_{secure_filename(file.filename)}"
        dest = upload_dir / stored_name
        file.save(str(dest))
        db.execute("""INSERT INTO member_documents
            (member_id, document_type, original_name, stored_name, file_size, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (mid, doc_type, file.filename, stored_name, dest.stat().st_size, session.get("user_id")))
        member_activity(db, mid, f"Uploaded {doc_type}: {file.filename}")
    aid = ensure_member_application(db, mid)
    checklist_updates = {"Signed Contract": ("contract_status", "signed"),
                         "SA ID Copy": ("id_copy_status", "uploaded")}
    if doc_type in checklist_updates:
        field, status = checklist_updates[doc_type]
        db.execute(f"UPDATE compliance_checklists SET {field}=?, updated_at=datetime('now') WHERE application_id=?", (status, aid))
        sync_application_status(db, aid)
    db.commit()
    if doc_type == TT3_DOCUMENT:
        from .tt3 import start_check
        start_check(db, mid)
        return finish("TT3 uploaded. Checking NuPay for an active DebiCheck now; "
                      "the result will show under DebiCheck verification on the member's profile.")
    return finish(f"{len(files)} document(s) uploaded successfully.")


@members_bp.route("/members/<int:mid>/documents/<int:doc_id>/download")
@permission_required("all_members")
def member_download_doc(mid: int, doc_id: int):
    db = get_db()
    doc = db.execute(
        "SELECT * FROM member_documents WHERE id = ? AND member_id = ?", (doc_id, mid)
    ).fetchone()
    if doc is None:
        abort(404)
    if doc['stored_name'].startswith('query-form:'):
        from .query_forms import protected_document
        return protected_document(db,mid,doc_id)
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "members" / str(mid) / doc["stored_name"]
    if not path.exists():
        abort(404)
    member_activity(db, mid, f"Downloaded {doc['document_type']}: {doc['original_name']}")
    if doc["document_type"] == "Bank Statement":
        from .applications import STATEMENT_OPENED_ACTION
        audit_log(db, STATEMENT_OPENED_ACTION, new_value=str(doc_id), user_id=session.get("user_id"))
    db.commit()
    return send_file(str(path), as_attachment=True, download_name=doc["original_name"])


@members_bp.route("/members/<int:mid>/documents/<int:doc_id>/analyse", methods=["POST"])
@permission_required("all_members")
def member_analyse_doc(mid: int, doc_id: int):
    import json
    from .statement_parser import analyse_pdf

    db = get_db()
    doc = db.execute(
        "SELECT * FROM member_documents WHERE id = ? AND member_id = ?", (doc_id, mid)
    ).fetchone()
    member = db.execute("SELECT monthly_installment FROM members WHERE id = ?", (mid,)).fetchone()

    if not doc:
        flash("Document not found.", "error")
        return redirect(_doc_return_to(mid))

    path = Path(current_app.config["UPLOAD_FOLDER"]) / "members" / str(mid) / doc["stored_name"]
    monthly = float(member["monthly_installment"] or 0) if member else 0.0
    pdf_password = request.form.get("pdf_password", "").strip() or None

    result = analyse_pdf(str(path), monthly_installment=monthly, pdf_password=pdf_password)

    db.execute(
        "UPDATE member_documents SET analysis_json = ? WHERE id = ?",
        (json.dumps(result), doc_id),
    )
    from .applications import ensure_member_application, sync_application_status
    aid = ensure_member_application(db, mid)
    if not result.get("error"):
        db.execute(
            """UPDATE compliance_checklists
               SET bank_statement_status = 'analysed', updated_at = datetime('now')
               WHERE application_id = ?""",
            (aid,),
        )
        sync_application_status(db, aid)
    if result.get("error"):
        activity = f"Statement analysis failed for {doc['original_name']}: {result['error']}"
    else:
        activity = (
            f"Analysed bank statement {doc['original_name']} "
            f"(income R{result['estimated_income']:,.2f}, rating {result['rating'].upper()})"
        )
    member_activity(db, mid, activity)
    db.commit()

    if result.get("error"):
        flash(f"Analysis error: {result['error']}", "error")
    else:
        flash("Statement analysed successfully.", "success")

    return redirect(_doc_return_to(mid))


@members_bp.route("/members/<int:mid>/documents/<int:doc_id>/delete", methods=["POST"])
@permission_required("all_members")
def member_delete_doc(mid: int, doc_id: int):
    db = get_db()
    doc = db.execute(
        "SELECT * FROM member_documents WHERE id = ? AND member_id = ?", (doc_id, mid)
    ).fetchone()
    if doc:
        if doc['stored_name'].startswith('query-form:'):
            abort(409, description='Signed query evidence is retained with its original submission.')
        path = Path(current_app.config["UPLOAD_FOLDER"]) / "members" / str(mid) / doc["stored_name"]
        if path.exists():
            os.remove(str(path))
        db.execute("DELETE FROM member_documents WHERE id = ?", (doc_id,))
        from .applications import ensure_member_application, sync_application_status
        aid = ensure_member_application(db, mid)
        checklist_updates = {
            "Bank Statement": ("bank_statement_status", "not_uploaded"),
            "Signed Contract": ("contract_status", "pending"),
            "SA ID Copy": ("id_copy_status", "not_uploaded"),
        }
        if doc["document_type"] in checklist_updates:
            remaining = db.execute(
                """SELECT COUNT(*) FROM member_documents
                   WHERE member_id = ? AND document_type = ?""",
                (mid, doc["document_type"]),
            ).fetchone()[0]
            if remaining == 0:
                field, status = checklist_updates[doc["document_type"]]
                db.execute(
                    f"UPDATE compliance_checklists SET {field} = ?, updated_at = datetime('now') WHERE application_id = ?",
                    (status, aid),
                )
                sync_application_status(db, aid)
        member_activity(db, mid, f"Deleted {doc['document_type']}: {doc['original_name']}")
        db.commit()
        flash("Document deleted.", "success")
    return redirect(_doc_return_to(mid))


@members_bp.route("/members/<int:mid>/edit", methods=["GET", "POST"])
@permission_required("add_member")
def member_edit(mid: int):
    db = get_db()
    member = db.execute("SELECT * FROM members WHERE id = ?", (mid,)).fetchone()
    if member is None:
        flash("Member not found.", "warning")
        return redirect(url_for("members.members_index"))
    member = decrypt_member(member)  # decrypt for form pre-fill

    consultants = db.execute(
        "SELECT id, full_name FROM users WHERE active = 1 ORDER BY full_name"
    ).fetchall()
    tariff_rows = _get_tariffs()

    if request.method == "POST":
        d = _get_form_data()
        custom_error = _apply_custom_package(d, member)
        credential_conflict = None
        if d["access_credential"]:
            credential_conflict = db.execute(
                "SELECT id, first_name, last_name FROM members WHERE access_credential=? AND id!=?",
                (d["access_credential"], mid),
            ).fetchone()
        id_conflict = None
        id_valid = (
            bool(d["id_number"]) if d["debicheck_id_type"] == "1"
            else d["id_number"].isdigit() and len(d["id_number"]) == 13
        )
        if id_valid and encryption_enabled():
            id_conflict = db.execute(
                "SELECT id, first_name, last_name FROM members WHERE id_number_hash = ? AND id != ?",
                (hash_for_lookup(d["id_number"]), mid),
            ).fetchone()
        elif id_valid:
            id_conflict = db.execute(
                "SELECT id, first_name, last_name FROM members WHERE id_number = ? AND id != ?",
                (d["id_number"], mid),
            ).fetchone()

        if not (d["first_name"] and d["last_name"]):
            flash("First name and last name are required.", "error")
        elif not id_valid:
            flash("RSA ID number must be exactly 13 digits, or select Passport as the identity type.", "error")
        elif id_conflict:
            flash(
                f"That ID number is already linked to "
                f"{id_conflict['first_name']} {id_conflict['last_name']} (#{id_conflict['id']}).",
                "error",
            )
        elif credential_conflict:
            flash(
                f"That barcode is already linked to "
                f"{credential_conflict['first_name']} {credential_conflict['last_name']} "
                f"(#{credential_conflict['id']}).",
                "error",
            )
        elif custom_error:
            flash(custom_error, "error")
        else:
            # Lifecycle fields are controlled by the application/state machine.
            # Normal member editing may not promote, join, or backdate a member.
            lifecycle_override = session.get("role") in {"admin", "manager"} and "member_lifecycle_change" in session.get("permissions", set())
            if not lifecycle_override:
                d["member_status"] = member["member_status"] or "Unverified"
                d["join_date"] = member["join_date"] or ""
            try:
                ed = encrypt_member(d)  # encrypts id_number, account_number, branch_code, payer_id_number
                db.execute(
                    """UPDATE members SET
                       first_name=?, last_name=?, id_number=?, id_number_hash=?, contact=?, email=?,
                       date_of_birth=?, gender=?, join_date=?, member_status=?,
                       source=?, entry_type=?, tariff=?, package=?,
                       contract_duration=?, monthly_installment=?, uploaded_by_id=?,
                       street_number=?, street_name=?, suburb=?, city=?,
                       postal_code=?, country=?, payment_type=?, debit_order_date=?,
                       bank=?, account_type=?, account_number=?, branch_code=?,
                       payer_name=?, payer_id_number=?, opt_in=?,
                       fitness_goal=?, training_experience=?,
                       promotion=?, access_level=?, access_credential=?, occupation=?,
                       employer=?, alternative_number=?, work_number=?, joining_fee=?,
                       member_addons=?, witness_name=?,
                       emergency_contact_name=?, emergency_contact_number=?, emergency_contact_relation=?,
                       parq_q1=?, parq_q2=?, parq_q3=?, parq_q4=?, parq_q5=?,
                       parq_q6=?, parq_q7=?, parq_q8=?, parq_q9=?,
                       custom_package_note=?
                       WHERE id=?""",
                    (ed["first_name"], ed["last_name"], ed["id_number"], ed.get("id_number_hash"),
                     ed["contact"], ed["email"],
                     ed["date_of_birth"], ed["gender"], ed["join_date"], ed["member_status"],
                     ed["source"], ed["entry_type"], ed["tariff"], ed["package"],
                     ed["contract_duration"], float(ed["monthly_installment"]),
                     int(ed["uploaded_by_id"]) if ed["uploaded_by_id"] else None,
                     ed["street_number"], ed["street_name"], ed["suburb"], ed["city"],
                     ed["postal_code"], ed["country"], ed["payment_type"], ed["debit_order_date"],
                     ed["bank"], ed["account_type"], ed["account_number"], ed["branch_code"],
                     ed["payer_name"], ed["payer_id_number"], ed["opt_in"],
                     ed["fitness_goal"], ed["training_experience"],
                     ed["promotion"], ed["access_level"], ed["access_credential"] or None, ed["occupation"],
                     ed["employer"], ed["alternative_number"], ed["work_number"], float(ed["joining_fee"] or 0),
                     ed["member_addons"], ed["witness_name"],
                     ed["emergency_contact_name"], ed["emergency_contact_number"], ed["emergency_contact_relation"],
                     ed["parq_q1"], ed["parq_q2"], ed["parq_q3"], ed["parq_q4"], ed["parq_q5"],
                     ed["parq_q6"], ed["parq_q7"], ed["parq_q8"], ed["parq_q9"],
                     ed["custom_package_note"], mid),
                )
                changed = []
                for field, value in d.items():
                    old_value = member.get(field) if isinstance(member, dict) else None
                    if str(old_value or "") != str(value or ""):
                        changed.append(field.replace("_", " "))
                detail = ", ".join(changed[:8])
                if len(changed) > 8:
                    detail += f" and {len(changed) - 8} more"
                member_activity(
                    db, mid,
                    f"Updated member profile{': ' + detail if detail else ''}",
                )
                custom_note = _custom_package_activity(member, d)
                if custom_note:
                    member_activity(db, mid, custom_note)
                db.commit()
                flash(f"{d['first_name']} {d['last_name']} updated.", "success")
                return redirect(url_for("members.member_detail", mid=mid))
            except Exception:
                current_app.logger.exception("Failed to update member id=%s", mid)
                flash("Failed to update member. Please try again.", "error")

    return render_template(
        "members/edit.html",
        member=member,
        tariffs=TARIFFS,
        tariff_rows=tariff_rows,
        custom_form=_custom_form_values(member),
        banks=BANKS,
        consultants=consultants,
    )
