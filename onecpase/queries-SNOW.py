from flask import (Blueprint, render_template, request, session,
                   redirect, url_for, flash, jsonify)
from datetime import datetime, date, timedelta
from .database import get_db
from .auth import login_required

queries_bp = Blueprint('queries', __name__, url_prefix='/queries')

# Categories where the account must be settled (zero balance) before we proceed.
OWING_SENSITIVE_CATEGORIES = {'cancellation', 'freeze'}

# Categories whose handling depends heavily on the member's financial position;
# these drive the Account Decision panel and the saved financial snapshot.
FINANCIAL_DECISION_CATEGORIES = {
    'payments', 'ptp_collections', 'cancellation', 'freeze', 'refund', 'access_card',
}

# Settlement-discount tiers by months in arrears, high→low:
# (min_months, max_months_or_None_for_open_ended, min_percent, max_percent,
#  default_percent, manager_approval_required).
ARREARS_DISCOUNT_RULES = [
    (9, None, 0,  100, 0,  True),   # 9+ months: manager discretion, full review
    (6, 8,    60, 75,  75, True),
    (4, 5,    35, 50,  50, True),
    (2, 3,    25, 25,  25, False),
]

# Roles that see gym-wide follow-up counts rather than just their own.
MANAGER_ROLES = {'admin', 'manager'}

# Default follow-up window for a new query (working days).
FOLLOW_UP_WORKING_DAYS = 5

# ── Constants ────────────────────────────────────────────────────────────────

QUERY_CATEGORIES = [
    ('membership',        'Membership'),
    ('payments',          'Payments / Arrears'),
    ('ptp_collections',   'PTP / Collections'),
    ('access_card',       'Access / Card'),
    ('sales',             'Sales / New Joiner'),
    ('compliance',        'Compliance / Verification'),
    ('cancellation',      'Cancellation'),
    ('freeze',            'Freeze / Suspension'),
    ('facility',          'Facility / Maintenance'),
    ('classes',           'Classes / Aerobics'),
    ('personal_training', 'Personal Training'),
    ('complaint',         'Complaint'),
    ('incident',          'Incident / Injury'),
    ('refund',            'Refund'),
    ('general',           'General Enquiry'),
]

QUERY_TYPES = {
    'membership':        ['Membership status', 'Access status', 'Contract term / end date',
                          'Membership package', 'Renewal', 'Upgrade/downgrade', 'Transfer',
                          'Dependent account', 'Guardian/payer update', 'Member details update',
                          'Freeze/suspension', 'Cancellation information', 'Reactivation', 'Other'],
    'payments':          ['Debit order failed', 'Debit order date change', 'Arrears balance',
                          'Statement request', 'Proof of payment', 'Double debit',
                          'Annual levy', 'Joining fee', 'Debit order dispute',
                          'Settlement discount', 'Promise to pay',
                          'Broken promise to pay', 'Payment arrangement',
                          'Access blocked due to arrears', 'Other'],
    'ptp_collections':   ['New PTP arrangement', 'Once-off settlement', 'Split payment arrangement',
                          'Premium top-up arrangement', 'Add arrears to back of contract',
                          'Broken PTP', 'PTP follow-up', 'Discount / settlement request',
                          'Access blocked due to arrears', 'Other'],
    'access_card':       ['Card not working', 'Lost card', 'PIN/QR code issue',
                          'Access blocked', 'Member photo issue', 'Unauthorized access',
                          'Entry denied due to arrears', 'Other'],
    'sales':             ['Pricing', 'Joining requirements', 'Promotion', '7-day voucher',
                          'DebiCheck help', 'Contract signing', 'Lead follow-up',
                          'Declined membership', 'Other'],
    'compliance':        ['Missing ID copy', 'Missing bank statement', 'DebiCheck not approved',
                          'Contract not signed', 'POS not captured', 'Underage approval',
                          'Incomplete profile', 'Other'],
    'cancellation':      ['Cancellation request', 'Early cancellation', 'Penalty waiver',
                          'Cancellation follow-up', 'Other'],
    'freeze':            ['Medical freeze', 'Holiday freeze', 'Financial freeze',
                          'Freeze extension', 'Other'],
    'facility':          ['Equipment broken', 'Weights missing', 'Aircon/fans issue',
                          'Lights/electricity', 'Generator issue', 'Water issue',
                          'Toilet issue', 'Cleaning complaint', 'Safety hazard', 'Other'],
    'classes':           ['Class timetable', 'Instructor query', 'Class cancellation',
                          'Class capacity', 'Class complaint', 'Sound/music issue',
                          'Class suggestion', 'Beginner support', 'Other'],
    'personal_training': ['PT pricing', 'PT booking', 'PT complaint', 'PT refund',
                          'PT allocation', 'Programme request', 'Other'],
    'complaint':         ['Staff attitude', 'Poor assistance', 'Long waiting time',
                          'Incorrect information', 'Manager request', 'General complaint',
                          'Other'],
    'incident':          ['Injury report', 'Equipment accident', 'Slip/fall',
                          'Fight/altercation', 'Theft/lost item', 'Medical emergency',
                          'Safety complaint', 'Other'],
    'refund':            ['Overpayment refund', 'Cancellation refund', 'PT refund',
                          'Levy refund', 'Admin error refund', 'Other'],
    'general':           ['General information', 'Operating hours', 'Guest pass',
                          'Parking', 'Locker', 'Other'],
}

# Membership query types that should route to a specific department by default
# (falls back to the generic _recommended_department rules when not listed here).
MEMBERSHIP_TYPE_DEPARTMENT = {
    'Renewal':                  'Sales',
    'Upgrade/downgrade':        'Sales',
    'Reactivation':             'Sales',
    'Transfer':                 'Admin',
    'Dependent account':        'Admin',
    'Guardian/payer update':    'Admin',
    'Freeze/suspension':        'Admin',
    'Cancellation information': 'Admin',
}

# Short "what to check before answering" hints shown next to the query type
# picker. Keyed by category so other categories can add their own over time.
QUERY_TYPE_HINTS = {
    'membership': {
        'Membership status':        'Confirm status, access and reason if blocked before responding.',
        'Access status':            'Check arrears, verification and mandate status before promising to restore access.',
        'Contract term / end date': 'End date and months remaining are calculated from join date + contract duration below.',
        'Membership package':       'Confirm current package, monthly fee and any pending upgrade/downgrade.',
        'Renewal':                  'Check contract end date and balance before offering a renewal package.',
        'Upgrade/downgrade':        'Confirm current vs requested package, price difference and debit order impact.',
        'Transfer':                 'Requires new member ID copy, consent and manager approval before processing.',
        'Dependent account':        'Confirm the main account is in good standing before activating a dependent.',
        'Guardian/payer update':    'Collect new payer banking details and re-confirm the debit order mandate.',
        'Member details update':    'Verify identity before updating contact, address or banking details.',
        'Freeze/suspension':        'Outstanding balance must be reviewed before a freeze is approved.',
        'Cancellation information': 'Informational only — log an actual request under the Cancellation category.',
        'Reactivation':             'Check old balance, card status and whether a new package is required.',
    },
    'ptp_collections': {
        'New PTP arrangement':          'Check the discount tier below. DebiCheck and a bank statement should be on file before finalising.',
        'Once-off settlement':          'Best for 1–3 months arrears where the member can clear the balance in one payment.',
        'Split payment arrangement':    'Use where the member can clear arrears in 2–3 instalments.',
        'Premium top-up arrangement':   'Adds an extra amount to the monthly debit order — confirm affordability from the bank statement first.',
        'Add arrears to back of contract': 'Recommended when arrears exceed R1,000 — extends the contract term instead of a lump sum.',
        'Broken PTP':                   'Check auto-block settings; a broken PTP with auto-block re-blocks gym access.',
        'PTP follow-up':                'Confirm the promised payment date and whether a receipt has been uploaded.',
        'Discount / settlement request': 'Discount is tiered by months in arrears — see the decision panel below for the range and approval requirement.',
        'Access blocked due to arrears': 'Confirm the block reason and what is needed to restore access.',
    },
}

PRIORITIES = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('urgent', 'Urgent')]
STATUSES   = [
    ('open',           'Open'),
    ('in_progress',    'In Progress'),
    ('pending_member', 'Pending Member'),
    ('pending_admin',  'Pending Admin'),
    ('escalated',      'Escalated'),
    ('resolved',       'Resolved'),
    ('closed',         'Closed'),
]
PAYMENT_ARREARS_STATUSES = [
    ('arrears_confirmed', 'Arrears Confirmed'),
    ('ptp_created', 'PTP Created'),
    ('awaiting_payment', 'Awaiting Payment'),
    ('pop_received', 'POP Received'),
    ('payment_verified', 'Payment Verified'),
    ('discount_offered', 'Discount Offered'),
    ('manager_approval_required', 'Manager Approval Required'),
    ('access_blocked', 'Access Blocked'),
    ('access_restored', 'Access Restored'),
    ('resolved', 'Resolved'),
]
DEPARTMENTS = ['Reception', 'Sales', 'Admin', 'Collections', 'Management', 'Maintenance', 'PT']

PRIORITY_COLOURS = {
    'low': '#059669', 'medium': '#d97706', 'high': '#dc2626', 'urgent': '#7c3aed'
}
STATUS_COLOURS = {
    'open': '#1769aa', 'in_progress': '#d97706', 'pending_member': '#9ca3af',
    'pending_admin': '#6b7280', 'escalated': '#7c3aed', 'resolved': '#059669', 'closed': '#374151',
    'arrears_confirmed': '#b0842d', 'ptp_created': '#1769aa',
    'awaiting_payment': '#d97706', 'pop_received': '#0891b2',
    'payment_verified': '#059669', 'discount_offered': '#b0842d',
    'manager_approval_required': '#7c3aed', 'access_blocked': '#e11d48',
    'access_restored': '#059669',
}


def _next_reference(db):
    today = date.today().strftime('%Y%m%d')
    prefix = f'QRY-{today}-'
    last = db.execute(
        "SELECT reference FROM queries WHERE reference LIKE ? ORDER BY id DESC LIMIT 1",
        (f'{prefix}%',)
    ).fetchone()
    seq = (int(last['reference'].split('-')[-1]) + 1) if last else 1
    return f'{prefix}{seq:04d}'


def _log_note(db, query_id, note, action_type='note'):
    db.execute(
        """INSERT INTO query_notes (query_id, created_by, note, action_type)
           VALUES (?, ?, ?, ?)""",
        (query_id, session.get('user_id'), note, action_type)
    )
    db.execute(
        "UPDATE queries SET updated_at=datetime('now','localtime') WHERE id=?",
        (query_id,)
    )


def _cat_label(cat):
    return dict(QUERY_CATEGORIES).get(cat, cat)


def _add_working_days(start: date, n: int) -> date:
    """Return the date `n` working days (Mon–Fri) after `start`."""
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:          # 0–4 = Mon–Fri
            added += 1
    return d


def _money(value) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _discount_rule_for_arrears(months_owing: int) -> dict:
    """Discount range + approval requirement for a given months-in-arrears figure.
    Below the lowest tier (< 2 months) no discount applies and no approval is needed.
    """
    for min_months, max_months, min_pct, max_pct, default_pct, approval in ARREARS_DISCOUNT_RULES:
        if months_owing >= min_months and (max_months is None or months_owing <= max_months):
            return {
                "min_percent": min_pct, "max_percent": max_pct,
                "default_percent": default_pct, "approval_required": approval,
            }
    return {"min_percent": 0, "max_percent": 0, "default_percent": 0, "approval_required": False}


def _discount_range_label(months_owing: int, rule: dict) -> str:
    if rule["min_percent"] == rule["max_percent"]:
        return f"{rule['min_percent']}%"
    if months_owing >= 9:
        return "Manager discretion"
    return f"{rule['min_percent']}%–{rule['max_percent']}%"


def _contract_status(join_date, contract_duration):
    """Contract end date + months remaining, derived from join date and term.
    Returns None if either value is missing or unparsable (e.g. blank duration).
    """
    if not join_date or not contract_duration:
        return None
    try:
        months = int(str(contract_duration).strip().split()[0])
        start = datetime.strptime(str(join_date)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError, IndexError):
        return None

    end_month_index = start.month - 1 + months
    end = date(start.year + end_month_index // 12, end_month_index % 12 + 1,
               min(start.day, 28))
    today = date.today()
    remaining = (end.year - today.year) * 12 + (end.month - today.month)
    if today.day > end.day:
        remaining -= 1

    return {
        "contract_end_date": end.isoformat(),
        "remaining_months": max(0, remaining),
        "expired": end < today,
    }


def _payment_arrears_decision(total_outstanding, arrears_months):
    total_outstanding = _money(total_outstanding)
    try:
        arrears_months = int(arrears_months or 0)
    except (TypeError, ValueError):
        arrears_months = 0

    rule = _discount_rule_for_arrears(arrears_months)
    discount_percent = rule["default_percent"]
    discount_amount = _money(total_outstanding * discount_percent / 100)
    settlement_amount = _money(total_outstanding - discount_amount)
    range_label = _discount_range_label(arrears_months, rule)

    if total_outstanding <= 0:
        recommendation = "Account is up to date. No arrears action required."
        access_decision = "Allow access"
        manager_approval_required = False
        ptp_required = "No"
        priority = "medium"
    elif arrears_months >= 9:
        recommendation = (f"{arrears_months} months in arrears — discount is at manager "
                          "discretion. Full account review required before any settlement.")
        access_decision = "Keep access blocked until payment is received."
        manager_approval_required = True
        ptp_required = "Yes"
        priority = "high"
    elif arrears_months >= 6:
        recommendation = (f"Offer {range_label} settlement discount (recommended "
                          f"{discount_percent}%) or create urgent PTP. Manager approval required.")
        access_decision = "Keep access blocked until payment is received."
        manager_approval_required = True
        ptp_required = "Yes"
        priority = "high"
    elif arrears_months >= 4:
        recommendation = (f"Offer {range_label} settlement discount (recommended "
                          f"{discount_percent}%) and confirm payment date. Manager approval required.")
        access_decision = "Keep access blocked until settlement or approved PTP."
        manager_approval_required = True
        ptp_required = "Yes"
        priority = "high"
    elif arrears_months >= 2:
        recommendation = f"Offer {discount_percent}% settlement discount or create PTP."
        access_decision = "Keep access blocked unless manager approves temporary access."
        manager_approval_required = False
        ptp_required = "Yes"
        priority = "medium"
    else:
        recommendation = "Collect full outstanding balance or arrange short PTP."
        access_decision = "Allow access only if account rules permit."
        manager_approval_required = False
        ptp_required = "No"
        priority = "medium"

    return {
        "total_outstanding": total_outstanding,
        "arrears_months": arrears_months,
        "discount_percent": discount_percent,
        "discount_min_percent": rule["min_percent"],
        "discount_max_percent": rule["max_percent"],
        "discount_range_label": range_label,
        "discount_amount": discount_amount,
        "settlement_amount": settlement_amount,
        "recommendation": recommendation,
        "payment_recommendation": recommendation,
        "access_decision": access_decision,
        "manager_approval_required": manager_approval_required,
        "ptp_required": ptp_required,
        "proof_of_payment_required": "Yes" if total_outstanding > 0 else "No",
        "follow_up": f"{FOLLOW_UP_WORKING_DAYS} working days",
        "assigned_to": "Collections",
        "priority": priority,
    }


def _recommended_department(category, is_owing=False, query_type=None):
    if category == 'membership' and query_type in MEMBERSHIP_TYPE_DEPARTMENT:
        return MEMBERSHIP_TYPE_DEPARTMENT[query_type]
    if category in {'payments', 'ptp_collections'}:
        return 'Collections'
    if category in {'cancellation', 'freeze', 'refund'}:
        return 'Admin'
    if category in {'facility', 'incident'}:
        return 'Management'
    if category == 'personal_training':
        return 'PT'
    if category == 'sales':
        return 'Sales'
    if is_owing:
        return 'Collections'
    return 'Reception'


def _recommended_priority(category, summary=None):
    if category == 'incident':
        return 'urgent'
    if category in {'complaint', 'refund'}:
        return 'high'
    if summary and summary.get("arrears_months", 0) >= 6:
        return 'high'
    if summary and summary.get("is_blocked"):
        return 'medium'
    return 'medium'


def _account_summary(db, member_id):
    """Snapshot of a member's account for display on a query.

    Reuses the members module's payment-profile logic so the owing figure
    matches the member page exactly.
    """
    from .members import _build_payment_profile          # local import avoids cycle
    from .encryption import decrypt_member

    row = db.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    if not row:
        return None
    member = decrypt_member(row)

    collections = db.execute(
        "SELECT * FROM collections WHERE member_id=? ORDER BY collection_date DESC, id DESC",
        (member_id,)
    ).fetchall()
    from .ptp import compute_arrears_from_profile           # canonical arrears calc

    profile = _build_payment_profile(collections)
    total_outstanding = _money(sum(r["balance"] for r in profile))

    # ── Arrears decision data ────────────────────────────────────────────────
    arrears = compute_arrears_from_profile(profile)
    arrears_months = arrears["months_in_arrears"]
    payment_decision = _payment_arrears_decision(total_outstanding, arrears_months)

    mandate = db.execute(
        "SELECT status FROM debicheck_mandates WHERE member_id=? ORDER BY id DESC LIMIT 1",
        (member_id,)
    ).fetchone()
    payment_type = (member.get("payment_type") or "").strip()
    acct_no = (member.get("account_number") or "").strip()
    has_debit_order = (payment_type.lower() == "debit order"
                       or mandate is not None or bool(acct_no))

    # ── PTP requirement signals (soft — informational, not enforced) ────────
    # A PTP arrangement should ideally be backed by an active debit order,
    # a bank statement on file, and confirmed income on that statement.
    statement_doc = db.execute(
        """SELECT analysis_json FROM member_documents
           WHERE member_id=? AND document_type='Bank Statement'
           ORDER BY uploaded_at DESC LIMIT 1""",
        (member_id,)
    ).fetchone()
    has_bank_statement = statement_doc is not None
    income_verified = False
    if statement_doc and statement_doc["analysis_json"]:
        import json as _json
        try:
            income_verified = bool(_json.loads(statement_doc["analysis_json"]).get("income_detected"))
        except (ValueError, TypeError):
            income_verified = False
    ptp_requirements_met = has_debit_order and has_bank_statement and income_verified

    counts = db.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status NOT IN ('resolved','closed') THEN 1 ELSE 0 END) AS open
           FROM queries WHERE member_id=?""",
        (member_id,)
    ).fetchone()
    dispute_count = db.execute(
        """SELECT COUNT(*) FROM queries
           WHERE member_id=? AND (LOWER(COALESCE(query_type,'')) LIKE '%dispute%'
                                  OR LOWER(COALESCE(description,'')) LIKE '%dispute%')""",
        (member_id,)
    ).fetchone()[0]

    access_status = member.get("gym_access_status") or "allowed"
    masked = ("•••• " + acct_no[-4:]) if len(acct_no) >= 4 else acct_no

    # ── Membership / contract snapshot ───────────────────────────────────────
    contract = _contract_status(member.get("join_date"), member.get("contract_duration"))

    return {
        "member": member,
        "package": member.get("package") or "",
        "contract_duration": member.get("contract_duration") or "",
        "contract_end_date": contract["contract_end_date"] if contract else None,
        "contract_remaining_months": contract["remaining_months"] if contract else None,
        "contract_expired": contract["expired"] if contract else None,
        "total_outstanding": total_outstanding,
        "is_owing": total_outstanding > 0.009,
        "arrears_months": payment_decision["arrears_months"],
        "discount_percent": payment_decision["discount_percent"],
        "discount_min_percent": payment_decision["discount_min_percent"],
        "discount_max_percent": payment_decision["discount_max_percent"],
        "discount_range_label": payment_decision["discount_range_label"],
        "discount_amount": payment_decision["discount_amount"],
        "settlement_amount": payment_decision["settlement_amount"],
        "has_bank_statement": has_bank_statement,
        "income_verified": income_verified,
        "ptp_requirements_met": ptp_requirements_met,
        "recommended_action": payment_decision["recommendation"],
        "payment_recommendation": payment_decision["payment_recommendation"],
        "access_decision": payment_decision["access_decision"],
        "manager_approval_required": payment_decision["manager_approval_required"],
        "ptp_required": payment_decision["ptp_required"],
        "proof_of_payment_required": payment_decision["proof_of_payment_required"],
        "payment_follow_up": payment_decision["follow_up"],
        "payment_assigned_to": payment_decision["assigned_to"],
        "payment_priority": payment_decision["priority"],
        "has_debit_order": has_debit_order,
        "payment_type": payment_type or "—",
        "debit_order_status": mandate["status"] if mandate else None,
        "bank": member.get("bank") or "",
        "account_name": member.get("account_name") or "",
        "account_masked": masked,
        "branch_code": member.get("branch_code") or "",
        "debit_order_date": member.get("debit_order_date") or "",
        "query_total": counts["total"] or 0,
        "query_open": counts["open"] or 0,
        "dispute_count": dispute_count,
        "is_blocked": access_status != "allowed",
        "access_status": access_status,
    }


def followup_due_count(db):
    """Count of open queries needing follow-up (due/overdue) for the current
    user. Managers/admins see the gym-wide count; everyone else sees their own.
    Returns (count, scope) where scope is 'all' or 'mine'.
    """
    if not session.get("user_id"):
        return 0, "mine"
    today_str = date.today().isoformat()
    is_manager = session.get("role") in MANAGER_ROLES
    sql = ("SELECT COUNT(*) FROM queries "
           "WHERE follow_up_date IS NOT NULL AND follow_up_date <= ? "
           "AND status NOT IN ('resolved','closed')")
    params = [today_str]
    if not is_manager:
        sql += " AND assigned_to = ?"
        params.append(session.get("user_id"))
    count = db.execute(sql, params).fetchone()[0]
    return count, ("all" if is_manager else "mine")


# ── Live account summary (JSON) ────────────────────────────────────────────────
@queries_bp.route('/member-summary/<int:member_id>')
@login_required
def member_summary(member_id):
    """Return the account decision snapshot for a member as JSON, so the Add
    Query form can show it the moment a member is selected."""
    db = get_db()
    summary = _account_summary(db, member_id)
    if not summary:
        return jsonify({"ok": False, "error": "Member not found"}), 404

    member = summary["member"]
    return jsonify({
        "ok": True,
        "member_id": member_id,
        "member_name": f"{member.get('first_name', '')} {member.get('last_name', '')}".strip(),
        "contact": member.get("contact") or "",
        "package": summary["package"],
        "contract_duration": summary["contract_duration"],
        "contract_end_date": summary["contract_end_date"],
        "contract_remaining_months": summary["contract_remaining_months"],
        "contract_expired": summary["contract_expired"],
        "total_outstanding": summary["total_outstanding"],
        "is_owing": summary["is_owing"],
        "arrears_months": summary["arrears_months"],
        "discount_percent": summary["discount_percent"],
        "discount_min_percent": summary["discount_min_percent"],
        "discount_max_percent": summary["discount_max_percent"],
        "discount_range_label": summary["discount_range_label"],
        "discount_amount": summary["discount_amount"],
        "settlement_amount": summary["settlement_amount"],
        "has_bank_statement": summary["has_bank_statement"],
        "income_verified": summary["income_verified"],
        "ptp_requirements_met": summary["ptp_requirements_met"],
        "recommended_action": summary["recommended_action"],
        "payment_recommendation": summary["payment_recommendation"],
        "access_decision": summary["access_decision"],
        "manager_approval_required": summary["manager_approval_required"],
        "ptp_required": summary["ptp_required"],
        "proof_of_payment_required": summary["proof_of_payment_required"],
        "payment_follow_up": summary["payment_follow_up"],
        "payment_assigned_to": summary["payment_assigned_to"],
        "payment_priority": summary["payment_priority"],
        "payment_type": summary["payment_type"],
        "has_debit_order": summary["has_debit_order"],
        "debit_order_status": summary["debit_order_status"],
        "access_status": summary["access_status"],
        "is_blocked": summary["is_blocked"],
        "query_total": summary["query_total"],
        "query_open": summary["query_open"],
        "dispute_count": summary["dispute_count"],
    })


# ── Dashboard ─────────────────────────────────────────────────────────────────
@queries_bp.route('/')
@login_required
def index():
    db = get_db()
    q         = request.args.get('q', '').strip()
    cat       = request.args.get('cat', '')
    status_f  = request.args.get('status', '')
    priority_f= request.args.get('priority', '')
    assigned_f= request.args.get('assigned', '')
    followup_f= request.args.get('followup', '')

    sql = """
        SELECT qr.*, m.first_name || ' ' || m.last_name AS member_full_name,
               ul.full_name AS logged_by_name,
               ua.full_name AS assigned_to_name
        FROM queries qr
        LEFT JOIN members m  ON qr.member_id = m.id
        LEFT JOIN users ul   ON qr.logged_by = ul.id
        LEFT JOIN users ua   ON qr.assigned_to = ua.id
        WHERE 1=1
    """
    params = []

    if q:
        sql += """ AND (qr.reference LIKE ? OR qr.member_name LIKE ? OR qr.contact LIKE ?
                    OR qr.description LIKE ? OR qr.query_type LIKE ?
                    OR (m.first_name || ' ' || m.last_name) LIKE ?)"""
        like = f'%{q}%'
        params += [like]*6
    if cat:
        sql += " AND qr.category = ?"
        params.append(cat)
    if status_f:
        sql += " AND qr.status = ?"
        params.append(status_f)
    if priority_f:
        sql += " AND qr.priority = ?"
        params.append(priority_f)
    if assigned_f == 'me':
        sql += " AND qr.assigned_to = ?"
        params.append(session.get('user_id'))
    elif assigned_f == 'unassigned':
        sql += " AND qr.assigned_to IS NULL"

    if followup_f == 'due':
        sql += (" AND qr.follow_up_date IS NOT NULL AND qr.follow_up_date <= ?"
                " AND qr.status NOT IN ('resolved','closed')")
        params.append(date.today().isoformat())
        # Non-managers only see their own follow-ups (matches the badge scope).
        if session.get('role') not in MANAGER_ROLES:
            sql += " AND qr.assigned_to = ?"
            params.append(session.get('user_id'))

    sql += " ORDER BY CASE qr.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, qr.created_at DESC"

    rows = db.execute(sql, params).fetchall()

    # Account snapshot per distinct member, for triage columns (cached per member).
    member_summaries = {}
    for r in rows:
        mid = r['member_id']
        if mid and mid not in member_summaries:
            member_summaries[mid] = _account_summary(db, mid)

    today_str = date.today().isoformat()
    kpi_open      = db.execute("SELECT COUNT(*) FROM queries WHERE status NOT IN ('resolved','closed')").fetchone()[0]
    kpi_overdue   = db.execute(
        "SELECT COUNT(*) FROM queries WHERE due_date < ? AND status NOT IN ('resolved','closed')",
        (today_str,)).fetchone()[0]
    kpi_escalated = db.execute("SELECT COUNT(*) FROM queries WHERE status='escalated'").fetchone()[0]
    kpi_urgent    = db.execute(
        "SELECT COUNT(*) FROM queries WHERE priority='urgent' AND status NOT IN ('resolved','closed')"
    ).fetchone()[0]
    kpi_resolved_today = db.execute(
        "SELECT COUNT(*) FROM queries WHERE status IN ('resolved','closed') AND DATE(updated_at)=?",
        (today_str,)).fetchone()[0]

    users = db.execute("SELECT id, full_name FROM users WHERE active=1 ORDER BY full_name").fetchall()

    return render_template(
        'queries/index.html',
        rows=rows, q=q, cat=cat, status_f=status_f,
        priority_f=priority_f, assigned_f=assigned_f, followup_f=followup_f,
        kpi_open=kpi_open, kpi_overdue=kpi_overdue,
        kpi_escalated=kpi_escalated, kpi_urgent=kpi_urgent,
        kpi_resolved_today=kpi_resolved_today,
        categories=QUERY_CATEGORIES, priorities=PRIORITIES, statuses=STATUSES,
        priority_colours=PRIORITY_COLOURS, status_colours=STATUS_COLOURS,
        users=users, today=today_str, member_summaries=member_summaries,
    )


# ── Add ───────────────────────────────────────────────────────────────────────
@queries_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    db = get_db()
    member_id = request.args.get('member_id') or request.form.get('member_id')
    member = None
    if member_id:
        member = db.execute(
            "SELECT id, first_name, last_name, contact FROM members WHERE id=?",
            (member_id,)
        ).fetchone()

    if request.method == 'POST':
        category    = request.form.get('category', '').strip()
        query_type  = request.form.get('query_type', '').strip()
        description = request.form.get('description', '').strip()
        priority    = request.form.get('priority', 'medium')
        member_name = request.form.get('member_name', '').strip()
        contact     = request.form.get('contact', '').strip()
        due_date    = request.form.get('due_date', '').strip() or None
        follow_up   = request.form.get('follow_up_date', '').strip() or None
        # Every new query gets a 5-working-day follow-up window by default.
        if not follow_up:
            follow_up = _add_working_days(date.today(), FOLLOW_UP_WORKING_DAYS).isoformat()
        assigned_to = request.form.get('assigned_to', '').strip() or None
        department  = request.form.get('department', '').strip() or None
        mem_id      = request.form.get('member_id', '').strip() or None

        if not category or not description:
            flash('Category and description are required.', 'error')
        else:
            # ── Decision support: snapshot + routing recommendations ──────────
            snapshot = _account_summary(db, int(mem_id)) if mem_id else None

            # Auto-route where the agent didn't override. Type-based routing
            # (e.g. membership Transfer -> Admin) applies even without a
            # linked member; arrears-based priority needs the snapshot.
            if not department:
                department = _recommended_department(
                    category, snapshot["is_owing"] if snapshot else False, query_type
                )
            if snapshot and priority == 'medium':
                priority = _recommended_priority(category, snapshot)

            # Freeze the financial position seen at capture time (balances change).
            snap_out = snap_months = snap_dpct = snap_damt = snap_settle = 0
            snap_action = None
            snap_access = None
            snap_mgr = 0
            if snapshot and category in FINANCIAL_DECISION_CATEGORIES:
                snap_out    = snapshot["total_outstanding"]
                snap_months = snapshot["arrears_months"]
                snap_dpct   = snapshot["discount_percent"]
                snap_damt   = snapshot["discount_amount"]
                snap_settle = snapshot["settlement_amount"]
                snap_action = snapshot["recommended_action"]
                snap_access = snapshot["access_decision"]
                snap_mgr    = 1 if snapshot["manager_approval_required"] else 0

            ref = _next_reference(db)
            db.execute("""
                INSERT INTO queries
                  (reference, logged_by, member_id, member_name, contact,
                   category, query_type, description, priority,
                   assigned_to, department, due_date, follow_up_date,
                   outstanding_snapshot, arrears_months_snapshot,
                   discount_percent_snapshot, discount_amount_snapshot,
                   settlement_amount_snapshot, recommended_action_snapshot,
                   access_decision_snapshot, manager_approval_required)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (ref, session.get('user_id'),
                  int(mem_id) if mem_id else None,
                  member_name, contact,
                  category, query_type, description, priority,
                  int(assigned_to) if assigned_to else None,
                  department, due_date, follow_up,
                  snap_out, snap_months, snap_dpct, snap_damt,
                  snap_settle, snap_action, snap_access, snap_mgr))
            db.commit()
            qid = db.execute("SELECT id FROM queries WHERE reference=?", (ref,)).fetchone()['id']
            flash(f'Query {ref} created.', 'success')
            if mem_id:
                return redirect(url_for('members.member_detail', mid=mem_id) + '#queries')
            return redirect(url_for('queries.detail', qid=qid))

    users = db.execute("SELECT id, full_name FROM users WHERE active=1 ORDER BY full_name").fetchall()
    return render_template(
        'queries/add.html',
        member=member, categories=QUERY_CATEGORIES,
        query_types=QUERY_TYPES, query_type_hints=QUERY_TYPE_HINTS,
        priorities=PRIORITIES, departments=DEPARTMENTS,
        users=users, today=date.today().isoformat(),
    )


# ── Detail ────────────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>')
@login_required
def detail(qid):
    db  = get_db()
    qry = db.execute("""
        SELECT qr.*, m.first_name || ' ' || m.last_name AS member_full_name,
               m.contact AS member_contact, m.id AS member_pk,
               ul.full_name AS logged_by_name,
               ua.full_name AS assigned_to_name,
               uc.full_name AS closed_by_name
        FROM queries qr
        LEFT JOIN members m  ON qr.member_id = m.id
        LEFT JOIN users ul   ON qr.logged_by = ul.id
        LEFT JOIN users ua   ON qr.assigned_to = ua.id
        LEFT JOIN users uc   ON qr.closed_by = uc.id
        WHERE qr.id = ?
    """, (qid,)).fetchone()
    if not qry:
        flash('Query not found.', 'warning')
        return redirect(url_for('queries.index'))

    notes = db.execute("""
        SELECT qn.*, u.full_name AS author
        FROM query_notes qn LEFT JOIN users u ON qn.created_by = u.id
        WHERE qn.query_id = ? ORDER BY qn.created_at ASC
    """, (qid,)).fetchall()

    users = db.execute("SELECT id, full_name FROM users WHERE active=1 ORDER BY full_name").fetchall()

    summary = _account_summary(db, qry['member_id']) if qry['member_id'] else None
    # Cancellation/Freeze on an owing account must be acknowledged before closing.
    requires_ack = bool(
        summary and summary['is_owing']
        and qry['category'] in OWING_SENSITIVE_CATEGORIES
    )

    return render_template(
        'queries/detail.html',
        qry=qry, notes=notes, users=users,
        categories=QUERY_CATEGORIES, query_types=QUERY_TYPES,
        priorities=PRIORITIES, statuses=STATUSES,
        payment_arrears_statuses=PAYMENT_ARREARS_STATUSES,
        departments=DEPARTMENTS,
        priority_colours=PRIORITY_COLOURS, status_colours=STATUS_COLOURS,
        cat_label=_cat_label,
        summary=summary, requires_ack=requires_ack,
        today=date.today().isoformat(),
    )


# ── Add Note ──────────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>/note', methods=['POST'])
@login_required
def add_note(qid):
    note = request.form.get('note', '').strip()
    if note:
        db = get_db()
        _log_note(db, qid, note)
        db.commit()
    return redirect(url_for('queries.detail', qid=qid) + '#timeline')


# ── Update status / assign / priority ─────────────────────────────────────────
@queries_bp.route('/<int:qid>/update', methods=['POST'])
@login_required
def update(qid):
    db = get_db()
    new_status    = request.form.get('status', '').strip()
    new_priority  = request.form.get('priority', '').strip()
    new_assigned  = request.form.get('assigned_to', '').strip() or None
    new_dept      = request.form.get('department', '').strip() or None
    follow_up     = request.form.get('follow_up_date', '').strip() or None
    due_date      = request.form.get('due_date', '').strip() or None
    action_taken  = request.form.get('action_taken', '').strip() or None

    qry = db.execute("SELECT status, priority, assigned_to FROM queries WHERE id=?", (qid,)).fetchone()
    if not qry:
        flash('Query not found.', 'warning')
        return redirect(url_for('queries.index'))

    db.execute("""
        UPDATE queries SET status=?, priority=?, assigned_to=?, department=?,
               follow_up_date=?, due_date=?, action_taken=?,
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (new_status or qry['status'],
          new_priority or qry['priority'] or 'medium',
          int(new_assigned) if new_assigned else None,
          new_dept,
          follow_up, due_date, action_taken, qid))

    # Auto-log status/assignment changes
    if new_status and new_status != qry['status']:
        _log_note(db, qid, f"Status changed to {new_status.replace('_',' ').title()}",
                  'status_change')
    if new_assigned and str(new_assigned) != str(qry['assigned_to'] or ''):
        user = db.execute("SELECT full_name FROM users WHERE id=?", (int(new_assigned),)).fetchone()
        _log_note(db, qid,
                  f"Assigned to {user['full_name'] if user else 'unknown'}",
                  'assignment')
    db.commit()
    return redirect(url_for('queries.detail', qid=qid))


# ── Close ─────────────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>/close', methods=['POST'])
@login_required
def close(qid):
    db = get_db()
    resolution = request.form.get('resolution_notes', '').strip()

    # Cancellation/Freeze on an owing account: the agent must confirm the client
    # was informed the outstanding amount will be added before we proceed.
    qry = db.execute("SELECT category, member_id FROM queries WHERE id=?", (qid,)).fetchone()
    if qry and qry['member_id'] and qry['category'] in OWING_SENSITIVE_CATEGORIES:
        summary = _account_summary(db, qry['member_id'])
        if summary and summary['is_owing'] and not request.form.get('owing_ack'):
            flash(
                f"This account is owing R{summary['total_outstanding']:,.2f}. "
                "Confirm the client was informed the outstanding amount must be "
                "settled / will be added before closing this query.",
                'error'
            )
            return redirect(url_for('queries.detail', qid=qid))

    db.execute("""
        UPDATE queries SET status='closed', resolution_notes=?,
               closed_by=?, closed_at=datetime('now','localtime'),
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (resolution, session.get('user_id'), qid))
    _log_note(db, qid,
              f"Query closed. {('Resolution: ' + resolution) if resolution else ''}",
              'closed')
    db.commit()
    flash('Query closed.', 'success')
    return redirect(url_for('queries.detail', qid=qid))


# ── Escalate ──────────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>/escalate', methods=['POST'])
@login_required
def escalate(qid):
    db = get_db()
    reason = request.form.get('reason', '').strip()
    db.execute("""
        UPDATE queries SET status='escalated', priority='urgent',
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (qid,))
    _log_note(db, qid,
              f"Escalated to management. {('Reason: ' + reason) if reason else ''}",
              'escalated')
    db.commit()
    flash('Query escalated.', 'success')
    return redirect(url_for('queries.detail', qid=qid))


# ── Convert to PTP ────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>/convert-ptp', methods=['POST'])
@login_required
def convert_ptp(qid):
    db  = get_db()
    qry = db.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
    if not qry or not qry['member_id']:
        flash('Query must be linked to a member to convert to PTP.', 'error')
        return redirect(url_for('queries.detail', qid=qid))
    _log_note(db, qid, 'Converted to PTP — redirected to member PTP tab.', 'ptp')
    db.execute("""
        UPDATE queries SET status='in_progress', category='ptp_collections',
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (qid,))
    db.commit()
    return redirect(url_for('members.member_detail', mid=qry['member_id']) + '#ptp')
