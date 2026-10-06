"""Queries & Tickets — Phase 3.

Queries is the case-management layer: a query records *why* a member needs
something done, routes it, tracks follow-up and closes it with a reason. The
financial arrangement itself lives in the PTP module; a query links to its
PTP through ``queries.ptp_id`` and follows that PTP's outcome.

Lifecycle::

    Logged → Open → In Progress / Pending → (Pending Approval) → Resolved → Closed

Discounts above the staff ceiling, accounts at manager discretion and member
cancellations requested by staff go through Pending Approval: the agent
proposes, a manager approves or rejects, and only then does the query move on.
"""
import json
import sqlite3

from flask import (Blueprint, render_template, request, session,
                   redirect, url_for, flash, jsonify, has_request_context)
from datetime import datetime, date, timedelta

from .database import get_db, audit_log, member_activity
from .collections_engine import (
    arrears_discount_policy, discount_needs_manager, STAFF_DISCOUNT_CEILING,
)
from .auth import permission_required, roles_required

queries_bp = Blueprint('queries', __name__, url_prefix='/queries')

QUERY_PERMISSION = 'daily_queries'

# Categories where the account must be settled (zero balance) before we proceed.
OWING_SENSITIVE_CATEGORIES = {'cancellation', 'freeze'}

# Categories whose handling depends heavily on the member's financial position;
# these drive the Account Decision panel and the saved financial snapshot.
FINANCIAL_DECISION_CATEGORIES = {
    'payments', 'ptp_collections', 'cancellation', 'freeze', 'refund', 'access_card',
}

# Categories that use the payment workflow statuses and can carry a proposed
# settlement discount.
PAYMENT_WORKFLOW_CATEGORIES = {'payments', 'ptp_collections'}

# Roles that see gym-wide follow-up counts and decide approvals.
MANAGER_ROLES = {'admin', 'manager'}

# Default follow-up window for a new query (working days).
FOLLOW_UP_WORKING_DAYS = 5

# Cancellation settlement negotiation policy — a member owing money on top of
# the cancellation fee gets a different settlement path depending how they're
# paying, but only once the combined total clears this threshold.
CANCELLATION_SETTLEMENT_THRESHOLD = 2000.0
CANCELLATION_LUMPSUM_DISCOUNT_PCT = 50
CANCELLATION_INSTALLMENT_FLAT_AMOUNT = 1500.0
CANCELLATION_CONTRACT_MONTHS = (12, 24, 36)
CANCELLATION_PAYMENT_METHODS = [
    ('lumpsum', 'Lump Sum'),
    ('existing_debit', 'Existing Debit Order'),
    ('new_debit', 'New Debit Order'),
]


def calculate_cancellation_fee(contract_months: int, months_completed: int, monthly_fee: float) -> tuple[float, int]:
    """Return (fee_amount, penalty_months) per section 11.3 of the membership
    agreement — the penalty schedule differs by contract length, and within a
    length, by how many months the member has already completed."""
    if contract_months == 36:
        if months_completed <= 17:
            penalty = 6
        elif months_completed <= 26:
            penalty = 4
        else:
            penalty = 2
    elif contract_months == 24:
        if months_completed <= 11:
            penalty = 6
        elif months_completed <= 17:
            penalty = 4
        else:
            penalty = 2
    elif contract_months == 12:
        penalty = 2 if months_completed >= 6 else 4
    else:
        penalty = 0
    return round(penalty * monthly_fee, 2), penalty


def calculate_cancellation_settlement(
    cancellation_fee: float,
    outstanding_balance: float,
    payment_method: str,
    manual_discount_pct: float = 0.0,
) -> dict:
    """Cancellation settlement, combining the cancellation fee with any
    outstanding balance owed. The auto-rules below only ever apply to a
    member who actually has an outstanding balance — the plain contract
    penalty (calculate_cancellation_fee) always stays in effect on its own
    for a member with no balance owing, no matter how large the fee itself is.

    When the member DOES have an outstanding balance and (fee + balance)
    exceeds CANCELLATION_SETTLEMENT_THRESHOLD:
      - lump sum payers automatically get CANCELLATION_LUMPSUM_DISCOUNT_PCT%
        off the combined total (owed + fee) — overrides any manually entered
        discount.
      - installment (existing/new debit) payers pay a flat
        CANCELLATION_INSTALLMENT_FLAT_AMOUNT instead of the combined total.
    Otherwise (no balance owing, or combined total at/below the threshold):
    only the cancellation fee itself is reduced by the manually entered
    discount percentage; the outstanding balance (if any) is unaffected and
    still owed in full."""
    combined_total = round(cancellation_fee + outstanding_balance, 2)
    has_outstanding_balance = outstanding_balance > 0.009
    if has_outstanding_balance and combined_total > CANCELLATION_SETTLEMENT_THRESHOLD and payment_method == "lumpsum":
        discount_pct = CANCELLATION_LUMPSUM_DISCOUNT_PCT
        settlement_amount = round(combined_total * (1 - discount_pct / 100), 2)
        rule = "discount_50"
    elif has_outstanding_balance and combined_total > CANCELLATION_SETTLEMENT_THRESHOLD and payment_method in ("existing_debit", "new_debit"):
        discount_pct = 0.0
        settlement_amount = CANCELLATION_INSTALLMENT_FLAT_AMOUNT
        rule = "flat_1500"
    else:
        discount_pct = max(0.0, min(100.0, manual_discount_pct))
        settlement_amount = round(cancellation_fee * (1 - discount_pct / 100), 2)
        rule = "none"
    return {
        "combined_total": combined_total,
        "discount_pct": discount_pct,
        "settlement_amount": settlement_amount,
        "rule": rule,
    }

# ── Constants ────────────────────────────────────────────────────────────────

QUERY_CATEGORIES = [
    ('membership',        'Membership'),
    ('payments',          'Payments & Arrears'),
    ('ptp_collections',   'Collections / PTP'),
    ('access_card',       'Access'),
    ('compliance',        'Compliance'),
    ('cancellation',      'Cancellation'),
    ('freeze',            'Freeze'),
    ('refund',            'Refund'),
    ('sales',             'Sales'),
    ('facility',          'Facility'),
    ('classes',           'Classes'),
    ('personal_training', 'Personal Training'),
    ('complaint',         'Complaint'),
    ('incident',          'Incident'),
    ('general',           'General'),
]

# Payments & Arrears is about the balance itself (what is owed, why a debit
# failed, proof of payment). Anything that needs a financial *arrangement* —
# a promise to pay, a settlement discount, a payment plan — is a Collections /
# PTP case, and the arrangement itself is created in the PTP module.
QUERY_TYPES = {
    'membership':        ['Membership status', 'Access status', 'Contract term / end date',
                          'Membership package', 'Renewal', 'Upgrade/downgrade', 'Transfer',
                          'Dependent account', 'Guardian/payer update', 'Member details update',
                          'Freeze/suspension', 'Cancellation information', 'Reactivation', 'Other'],
    'payments':          ['Debit order failed', 'Debit order date change', 'Arrears balance',
                          'Statement request', 'Proof of payment', 'Double debit',
                          'Annual levy', 'Joining fee', 'Debit order dispute',
                          'Access blocked due to arrears', 'Other'],
    'ptp_collections':   ['New PTP arrangement', 'Once-off settlement', 'Split payment arrangement',
                          'Premium top-up arrangement', 'Add arrears to back of contract',
                          'Broken PTP', 'PTP follow-up', 'Discount / settlement request',
                          'Access blocked due to arrears', 'Other'],
    'access_card':       ['Card not working', 'Lost card', 'PIN/QR code issue',
                          'Access blocked', 'Member photo issue', 'Unauthorized access',
                          'Entry denied due to arrears', 'Other'],
    'compliance':        ['Missing ID copy', 'Missing bank statement', 'DebiCheck not approved',
                          'Contract not signed', 'POS not captured', 'Underage approval',
                          'Incomplete profile', 'Other'],
    'cancellation':      ['Cancellation request', 'Early cancellation', 'Penalty waiver',
                          'Cancellation follow-up', 'Other'],
    'freeze':            ['Medical freeze', 'Holiday freeze', 'Financial freeze',
                          'Freeze extension', 'Other'],
    'refund':            ['Overpayment refund', 'Cancellation refund', 'PT refund',
                          'Levy refund', 'Admin error refund', 'Other'],
    'sales':             ['Pricing', 'Joining requirements', 'Promotion', '7-day voucher',
                          'DebiCheck help', 'Contract signing', 'Lead follow-up',
                          'Declined membership', 'Other'],
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
# picker. Every key here must be a type listed in QUERY_TYPES for the same
# category — the form only shows a hint on an exact match (tested).
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
    'payments': {
        'Debit order failed':       'Check the collection history and mandate status before re-presenting the debit.',
        'Arrears balance':          'Explain how the balance is made up. If the member needs an arrangement, log it under Collections / PTP.',
        'Proof of payment':         'Attach or note the POP reference; Collections verifies it against the bank before the balance changes.',
        'Debit order dispute':      'Record exactly what is disputed and the debit date(s). Disputes are counted on the member account.',
        'Access blocked due to arrears': 'Confirm the block reason and what is needed to restore access.',
    },
    'ptp_collections': {
        'New PTP arrangement':          'Check the discount range below. DebiCheck and a bank statement should be on file before finalising.',
        'Once-off settlement':          'Best for 1–3 months arrears where the member can clear the balance in one payment.',
        'Split payment arrangement':    'Use where the member can clear arrears in 2–3 instalments.',
        'Premium top-up arrangement':   'Adds an extra amount to the monthly debit order — confirm affordability from the bank statement first.',
        'Add arrears to back of contract': 'Recommended when arrears exceed R1,000 — extends the contract term instead of a lump sum.',
        'Broken PTP':                   'Check auto-block settings; a broken PTP with auto-block re-blocks gym access.',
        'PTP follow-up':                'Confirm the promised payment date and whether a receipt has been uploaded.',
        'Discount / settlement request': f'Discount is set by months in arrears — up to {STAFF_DISCOUNT_CEILING}% you may agree it; above that a manager approves.',
        'Access blocked due to arrears': 'Confirm the block reason and what is needed to restore access.',
    },
}

PRIORITIES = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('urgent', 'Urgent')]

# Every status a query can hold.
STATUSES = [
    ('open',              'Open'),
    ('in_progress',       'In Progress'),
    ('pending_member',    'Pending Member'),
    ('pending_admin',     'Pending Admin'),
    ('pending_approval',  'Pending Approval'),
    ('escalated',         'Escalated'),
    ('resolved',          'Resolved'),
    ('closed',            'Closed'),
]
# Extra statuses for Payments & Arrears and Collections / PTP queries.
PAYMENT_ARREARS_STATUSES = [
    ('arrears_confirmed', 'Arrears Confirmed'),
    ('discount_offered',  'Discount Offered'),
    ('ptp_created',       'PTP Created'),
    ('awaiting_payment',  'Awaiting Payment'),
    ('pop_received',      'POP Received'),
    ('payment_verified',  'Payment Verified'),
    ('access_blocked',    'Access Blocked'),
    ('access_restored',   'Access Restored'),
]
# Set by the system only — never chosen in the Update form.
SYSTEM_STATUSES = {'pending_approval', 'closed'}
CLOSED_STATUSES = ('resolved', 'closed')

CLOSURE_REASONS = [
    ('resolved',          'Resolved — action completed'),
    ('no_action_needed',  'Resolved — information given, no action needed'),
    ('member_unreachable', 'Member unreachable'),
    ('withdrawn',         'Withdrawn by member'),
    ('duplicate',         'Duplicate query'),
]

APPROVAL_STATUSES = {
    'not_required': 'Not required',
    'pending':      'Pending approval',
    'approved':     'Approved',
    'rejected':     'Rejected',
}

DEPARTMENTS = ['Reception', 'Sales', 'Admin', 'Collections', 'Management', 'Maintenance', 'PT']

PRIORITY_COLOURS = {
    'low': '#059669', 'medium': '#d97706', 'high': '#dc2626', 'urgent': '#7c3aed'
}
STATUS_COLOURS = {
    'open': '#1769aa', 'in_progress': '#d97706', 'pending_member': '#9ca3af',
    'pending_admin': '#6b7280', 'pending_approval': '#7c3aed',
    'escalated': '#7c3aed', 'resolved': '#059669', 'closed': '#374151',
    'arrears_confirmed': '#b0842d', 'ptp_created': '#1769aa',
    'awaiting_payment': '#d97706', 'pop_received': '#0891b2',
    'payment_verified': '#059669', 'discount_offered': '#b0842d',
    'access_blocked': '#e11d48', 'access_restored': '#059669',
}


def status_options_for(category):
    """Statuses staff may choose in the Update form for this category."""
    options = [s for s in STATUSES if s[0] not in SYSTEM_STATUSES]
    if category in PAYMENT_WORKFLOW_CATEGORIES:
        # Workflow statuses first, then the payment steps, then the end states.
        head = [s for s in options if s[0] not in ('escalated', 'resolved')]
        tail = [s for s in options if s[0] in ('escalated', 'resolved')]
        options = head + PAYMENT_ARREARS_STATUSES + tail
    return options


def _status_label(status):
    return dict(STATUSES + PAYMENT_ARREARS_STATUSES).get(status, (status or '').replace('_', ' ').title())


def _is_manager():
    return session.get('role') in MANAGER_ROLES


# ── Helpers ───────────────────────────────────────────────────────────────────

def _next_reference(db):
    today = date.today().strftime('%Y%m%d')
    prefix = f'QRY-{today}-'
    last = db.execute(
        "SELECT reference FROM queries WHERE reference LIKE ? ORDER BY id DESC LIMIT 1",
        (f'{prefix}%',)
    ).fetchone()
    seq = (int(last['reference'].split('-')[-1]) + 1) if last else 1
    return f'{prefix}{seq:04d}'


def _is_unique_violation(exc):
    text = str(exc).lower()
    return isinstance(exc, sqlite3.IntegrityError) or 'unique' in text or 'duplicate key' in text


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


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_float(value):
    """None when blank, else a float (raises ValueError when unreadable)."""
    text = str(value if value is not None else '').strip()
    if text == '':
        return None
    return float(text)


def _contract_status(join_date, contract_duration):
    """Contract end date, months completed and months remaining, derived from
    join date and term. Returns None if either value is missing or unparsable
    (e.g. a blank or "Month-to-month" duration).
    """
    if not join_date or not contract_duration:
        return None
    try:
        months = int(str(contract_duration).strip().split()[0])
        start = datetime.strptime(str(join_date)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError, IndexError):
        return None
    if months <= 0:
        return None

    end_month_index = start.month - 1 + months
    end = date(start.year + end_month_index // 12, end_month_index % 12 + 1,
               min(start.day, 28))
    today = date.today()
    remaining = (end.year - today.year) * 12 + (end.month - today.month)
    if today.day > end.day:
        remaining -= 1
    remaining = max(0, remaining)

    return {
        "contract_months": months,
        "contract_end_date": end.isoformat(),
        "remaining_months": remaining,
        "months_completed": max(0, min(months, months - remaining)),
        "expired": end < today,
    }


def _payment_arrears_decision(total_outstanding, arrears_months):
    """Recommended handling for an account's arrears, from the shared policy
    in collections_engine (the same one Collections and PTP use)."""
    total_outstanding = _money(total_outstanding)
    try:
        arrears_months = max(int(arrears_months or 0), 0)
    except (TypeError, ValueError):
        arrears_months = 0

    policy = arrears_discount_policy(arrears_months)
    discount_percent = policy["discount_percent"]
    range_label = policy["range_label"]
    discount_amount = _money(total_outstanding * discount_percent / 100)
    settlement_amount = _money(total_outstanding - discount_amount)
    manager_approval_required = policy["manager_approval_required"]

    if total_outstanding <= 0:
        recommendation = "Account is up to date. No arrears action required."
        access_decision = "Allow access"
        manager_approval_required = False
        ptp_required = "No"
        priority = "medium"
    elif policy["manager_discretion"]:
        recommendation = (f"{arrears_months} months in arrears — discount is at manager "
                          "discretion. Full account review required before any settlement.")
        access_decision = "Keep access blocked until payment is received."
        ptp_required = "Yes"
        priority = "high"
    elif arrears_months >= 6:
        recommendation = (f"Offer {range_label} settlement discount (recommended "
                          f"{discount_percent}%) or create urgent PTP. Manager approval required.")
        access_decision = "Keep access blocked until payment is received."
        ptp_required = "Yes"
        priority = "high"
    elif arrears_months >= 4:
        recommendation = (f"Offer {range_label} settlement discount (recommended "
                          f"{discount_percent}%) and confirm payment date.")
        access_decision = "Keep access blocked until settlement or approved PTP."
        ptp_required = "Yes"
        priority = "high"
    elif arrears_months >= 2:
        recommendation = f"Offer {discount_percent}% settlement discount or create PTP."
        access_decision = "Keep access blocked unless manager approves temporary access."
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
        "discount_min_percent": policy["min_percent"],
        "discount_max_percent": policy["max_percent"],
        "discount_range_label": range_label,
        "manager_discretion": policy["manager_discretion"],
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
    if category in PAYMENT_WORKFLOW_CATEGORIES:
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
    return 'medium'


def _ptp_readiness(db, member_id, has_debit_order):
    """Whether the member meets the PTP module's own evidence rules.

    Uses the same tests the PTP module applies when it creates an
    arrangement (ptp.ptp_create): a confirmed DebiCheck mandate and a bank
    statement whose analysis qualified it as verification evidence. Signals
    only — the PTP module still makes the final call.
    """
    from .ptp import _has_submitted_mandate             # local import avoids cycle

    docs = db.execute(
        """SELECT analysis_json FROM member_documents
           WHERE member_id=? AND lower(COALESCE(document_type,'')) LIKE ?
           ORDER BY uploaded_at DESC""",
        (member_id, "%bank%"),
    ).fetchall()
    has_bank_statement = bool(docs)
    income_verified = False
    statement_qualified = False
    for doc in docs:
        try:
            analysis = json.loads(doc["analysis_json"] or "{}")
        except (TypeError, ValueError):
            analysis = {}
        income_verified = income_verified or bool(analysis.get("income_detected"))
        statement_qualified = statement_qualified or bool(analysis.get("verification_qualified"))

    debicheck_confirmed = _has_submitted_mandate(db, member_id)
    return {
        "has_bank_statement": has_bank_statement,
        "income_verified": income_verified,
        "statement_qualified": statement_qualified,
        "debicheck_confirmed": debicheck_confirmed,
        "ptp_requirements_met": bool(debicheck_confirmed and statement_qualified),
        "has_debit_order": has_debit_order,
    }


def _account_summary(db, member_id):
    """Snapshot of a member's account for display on a query.

    Reuses the members module's payment-profile logic so the owing figure
    matches the member page exactly.
    """
    from .members import _build_payment_profile          # local import avoids cycle
    from .encryption import decrypt_member
    from .ptp import compute_arrears_from_profile           # canonical arrears calc

    row = db.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    if not row:
        return None
    member = decrypt_member(row)

    collections = db.execute(
        "SELECT * FROM collections WHERE member_id=? ORDER BY collection_date DESC, id DESC",
        (member_id,)
    ).fetchall()

    profile = _build_payment_profile(collections)
    total_outstanding = _money(sum(r["balance"] for r in profile))

    # ── Arrears decision data ────────────────────────────────────────────────
    arrears = compute_arrears_from_profile(profile)
    payment_decision = _payment_arrears_decision(total_outstanding, arrears["months_in_arrears"])

    mandate = db.execute(
        "SELECT status FROM debicheck_mandates WHERE member_id=? ORDER BY id DESC LIMIT 1",
        (member_id,)
    ).fetchone()
    payment_type = (member.get("payment_type") or "").strip()
    acct_no = (member.get("account_number") or "").strip()
    has_debit_order = (payment_type.lower() == "debit order"
                       or mandate is not None or bool(acct_no))
    readiness = _ptp_readiness(db, member_id, has_debit_order)

    counts = db.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status NOT IN ('resolved','closed') THEN 1 ELSE 0 END) AS open
           FROM queries WHERE member_id=?""",
        (member_id,)
    ).fetchone()
    # The dispute pattern below is bound as a parameter, not written into the
    # SQL text, so it survives psycopg2's own %-style substitution on
    # Postgres (an inlined LIKE pattern using that character collides with
    # substitution once a non-empty params tuple is present).
    dispute_count = db.execute(
        """SELECT COUNT(*) FROM queries
           WHERE member_id=? AND (LOWER(COALESCE(query_type,'')) LIKE ?
                                  OR LOWER(COALESCE(description,'')) LIKE ?)""",
        (member_id, "%dispute%", "%dispute%")
    ).fetchone()[0]

    open_ptp = db.execute(
        """SELECT id, ptp_status, promise_amount, promise_date, manager_approval_status
           FROM ptp_agreements
           WHERE member_id=? AND ptp_status IN ('pending','partially_paid')
           ORDER BY id DESC LIMIT 1""",
        (member_id,)
    ).fetchone()

    access_status = member.get("gym_access_status") or "allowed"
    masked = ("•••• " + acct_no[-4:]) if len(acct_no) >= 4 else acct_no

    # ── Membership / contract snapshot ───────────────────────────────────────
    contract = _contract_status(member.get("join_date"), member.get("contract_duration"))

    return {
        "member": member,
        "package": member.get("package") or "",
        "contract_duration": member.get("contract_duration") or "",
        "contract_months": contract["contract_months"] if contract else None,
        "contract_end_date": contract["contract_end_date"] if contract else None,
        "contract_remaining_months": contract["remaining_months"] if contract else None,
        "contract_months_completed": contract["months_completed"] if contract else None,
        "contract_expired": contract["expired"] if contract else None,
        "monthly_installment": _money(member.get("monthly_installment") or 0),
        "total_outstanding": total_outstanding,
        "is_owing": total_outstanding > 0.009,
        "arrears_months": payment_decision["arrears_months"],
        "discount_percent": payment_decision["discount_percent"],
        "discount_min_percent": payment_decision["discount_min_percent"],
        "discount_max_percent": payment_decision["discount_max_percent"],
        "discount_range_label": payment_decision["discount_range_label"],
        "manager_discretion": payment_decision["manager_discretion"],
        "staff_discount_ceiling": STAFF_DISCOUNT_CEILING,
        "discount_amount": payment_decision["discount_amount"],
        "settlement_amount": payment_decision["settlement_amount"],
        "has_bank_statement": readiness["has_bank_statement"],
        "income_verified": readiness["income_verified"],
        "statement_qualified": readiness["statement_qualified"],
        "debicheck_confirmed": readiness["debicheck_confirmed"],
        "ptp_requirements_met": readiness["ptp_requirements_met"],
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
        "open_ptp_id": open_ptp["id"] if open_ptp else None,
        "open_ptp_status": open_ptp["ptp_status"] if open_ptp else None,
        "is_blocked": access_status != "allowed",
        "access_status": access_status,
    }


# Keys from _account_summary that the Add form's JSON endpoint never sends:
# the decrypted member row and banking details stay server-side.
_SUMMARY_PRIVATE_KEYS = {"member", "bank", "account_name", "account_masked", "branch_code"}


def _proposed_discount(form, summary):
    """Read and validate the agent's proposed discount against the policy.

    Returns (percent, error). Blank means "use the recommended figure".
    """
    try:
        proposed = _optional_float(form.get('proposed_discount_pct'))
    except ValueError:
        return None, 'The proposed discount must be a number.'
    if proposed is None:
        return float(summary["discount_percent"]), None
    if proposed < 0 or proposed > 100:
        return None, 'The proposed discount must be between 0% and 100%.'
    if not summary["manager_discretion"] and proposed > summary["discount_max_percent"]:
        return None, (
            f'{summary["arrears_months"]} month(s) in arrears allows at most '
            f'{summary["discount_max_percent"]}% ({summary["discount_range_label"]}).'
        )
    return proposed, None


def followup_due_count(db):
    """Count of open queries needing follow-up (due/overdue) for the current
    user. Managers/admins see the gym-wide count; everyone else sees their own.
    Returns (count, scope) where scope is 'all' or 'mine'.
    """
    if not session.get("user_id"):
        return 0, "mine"
    today_str = date.today().isoformat()
    is_manager = _is_manager()
    sql = ("SELECT COUNT(*) FROM queries "
           "WHERE follow_up_date IS NOT NULL AND follow_up_date <= ? "
           "AND status NOT IN ('resolved','closed')")
    params = [today_str]
    if not is_manager:
        sql += " AND assigned_to = ?"
        params.append(session.get("user_id"))
    count = db.execute(sql, params).fetchone()[0]
    return count, ("all" if is_manager else "mine")


# ── PTP link: the PTP module calls these ──────────────────────────────────────

# PTP outcome → (query status, note). Only open queries are moved.
_PTP_OUTCOME = {
    'pending':        ('ptp_created',      'PTP #{ptp} created — awaiting payment.'),
    'partially_paid': ('awaiting_payment', 'PTP #{ptp} partially paid — balance still awaited.'),
    'paid':           ('payment_verified', 'PTP #{ptp} paid in full — payment verified. Resolve and close when done.'),
    'broken':         ('escalated',        'PTP #{ptp} broken — promise date passed without payment.'),
    'superseded':     ('in_progress',      'PTP #{ptp} was replaced by a newer arrangement.'),
}


def link_ptp(db, query_id, member_id, ptp_id):
    """Attach a newly created PTP to the query it was created from.

    Returns True when the query was linked. The query must belong to the same
    member and still be open.
    """
    if not query_id:
        return False
    qry = db.execute(
        "SELECT id, status FROM queries WHERE id=? AND member_id=?",
        (query_id, member_id),
    ).fetchone()
    if not qry or qry["status"] in CLOSED_STATUSES:
        return False
    db.execute(
        "UPDATE queries SET ptp_id=?, updated_at=datetime('now','localtime') WHERE id=?",
        (ptp_id, query_id),
    )
    sync_from_ptp(db, ptp_id, 'pending')
    return True


def sync_from_ptp(db, ptp_id, ptp_status, approval=None):
    """Move every open query linked to this PTP to match the PTP's outcome.

    ``approval`` is the manager's decision on the PTP itself ('declined' sends
    the query back to In Progress). Does not commit.
    """
    rows = db.execute(
        "SELECT id, status FROM queries WHERE ptp_id=? AND status NOT IN ('resolved','closed')",
        (ptp_id,),
    ).fetchall()
    for row in rows:
        if approval == 'declined':
            new_status, note = 'in_progress', f'PTP #{ptp_id} was declined by a manager.'
        elif ptp_status in _PTP_OUTCOME:
            new_status, template = _PTP_OUTCOME[ptp_status]
            note = template.format(ptp=ptp_id)
        else:
            continue
        sets = "status=?, updated_at=datetime('now','localtime')"
        params = [new_status]
        if ptp_status == 'broken' and approval is None:
            sets += ", priority='high', follow_up_date=?"
            params.append(date.today().isoformat())
        db.execute(f"UPDATE queries SET {sets} WHERE id=?", (*params, row["id"]))
        db.execute(
            """INSERT INTO query_notes (query_id, created_by, note, action_type)
               VALUES (?, ?, ?, 'ptp')""",
            (row["id"], session.get('user_id') if has_request_context() else None, note),
        )


def approved_discount_for(db, query_id, member_id):
    """The discount a manager approved on this query, or None."""
    row = db.execute(
        """SELECT proposed_discount_pct FROM queries
           WHERE id=? AND member_id=? AND approval_status='approved'""",
        (query_id, member_id),
    ).fetchone()
    return float(row["proposed_discount_pct"] or 0) if row else None


# ── Live account summary (JSON) ────────────────────────────────────────────────
@queries_bp.route('/member-summary/<int:member_id>')
@permission_required(QUERY_PERMISSION)
def member_summary(member_id):
    """Return the account decision snapshot for a member as JSON, so the Add
    Query form can show it the moment a member is selected."""
    db = get_db()
    summary = _account_summary(db, member_id)
    if not summary:
        return jsonify({"ok": False, "error": "Member not found"}), 404

    member = summary["member"]
    payload = {k: v for k, v in summary.items() if k not in _SUMMARY_PRIVATE_KEYS}
    payload.update(
        ok=True,
        member_id=member_id,
        member_name=f"{member.get('first_name', '')} {member.get('last_name', '')}".strip(),
        contact=member.get("contact") or "",
    )
    return jsonify(payload)


# ── Dashboard ─────────────────────────────────────────────────────────────────
@queries_bp.route('/')
@permission_required(QUERY_PERMISSION)
def index():
    db = get_db()
    q         = request.args.get('q', '').strip()
    cat       = request.args.get('cat', '')
    status_f  = request.args.get('status', '')
    priority_f= request.args.get('priority', '')
    assigned_f= request.args.get('assigned', '')
    followup_f= request.args.get('followup', '')
    approval_f= request.args.get('approval', '')
    member_f  = _as_int(request.args.get('member_id'))

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
    if member_f:
        sql += " AND qr.member_id = ?"
        params.append(member_f)
    if cat:
        sql += " AND qr.category = ?"
        params.append(cat)
    if status_f:
        sql += " AND qr.status = ?"
        params.append(status_f)
    if priority_f:
        sql += " AND qr.priority = ?"
        params.append(priority_f)
    if approval_f == 'pending':
        sql += " AND qr.approval_status = 'pending'"
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
        if not _is_manager():
            sql += " AND qr.assigned_to = ?"
            params.append(session.get('user_id'))

    sql += " ORDER BY CASE qr.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, qr.created_at DESC"

    rows = db.execute(sql, params).fetchall()

    # Account snapshot per distinct member with an OPEN query, for the triage
    # columns. Closed queries don't need a live balance, and each summary
    # decrypts the member and reads their collections.
    member_summaries = {}
    for r in rows:
        mid = r['member_id']
        if mid and mid not in member_summaries and r['status'] not in CLOSED_STATUSES:
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
    kpi_pending_approval = db.execute(
        "SELECT COUNT(*) FROM queries WHERE approval_status='pending'"
    ).fetchone()[0]

    users = db.execute("SELECT id, full_name FROM users WHERE active=1 ORDER BY full_name").fetchall()

    return render_template(
        'queries/index.html',
        rows=rows, q=q, cat=cat, status_f=status_f,
        priority_f=priority_f, assigned_f=assigned_f, followup_f=followup_f,
        approval_f=approval_f, member_f=member_f,
        kpi_open=kpi_open, kpi_overdue=kpi_overdue,
        kpi_escalated=kpi_escalated, kpi_urgent=kpi_urgent,
        kpi_resolved_today=kpi_resolved_today,
        kpi_pending_approval=kpi_pending_approval,
        categories=QUERY_CATEGORIES, priorities=PRIORITIES,
        statuses=STATUSES + PAYMENT_ARREARS_STATUSES,
        priority_colours=PRIORITY_COLOURS, status_colours=STATUS_COLOURS,
        users=users, today=today_str, member_summaries=member_summaries,
    )


# ── Add ───────────────────────────────────────────────────────────────────────
@queries_bp.route('/add', methods=['GET', 'POST'])
@permission_required(QUERY_PERMISSION)
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
        result = _create_query(db, request.form)
        if result is not None:
            return result

    users = db.execute("SELECT id, full_name FROM users WHERE active=1 ORDER BY full_name").fetchall()
    return render_template(
        'queries/add.html',
        member=member, categories=QUERY_CATEGORIES,
        query_types=QUERY_TYPES, query_type_hints=QUERY_TYPE_HINTS,
        priorities=PRIORITIES, departments=DEPARTMENTS,
        users=users, today=date.today().isoformat(),
        cancellation_payment_methods=CANCELLATION_PAYMENT_METHODS,
        payment_workflow_categories=sorted(PAYMENT_WORKFLOW_CATEGORIES),
        staff_discount_ceiling=STAFF_DISCOUNT_CEILING,
        is_manager=_is_manager(),
    )


def _create_query(db, form):
    """Validate and save a new query. Returns a redirect on success, or None
    after flashing the reason it could not be saved."""
    category    = form.get('category', '').strip()
    query_type  = form.get('query_type', '').strip()
    description = form.get('description', '').strip()
    priority    = form.get('priority', 'medium')
    member_name = form.get('member_name', '').strip()
    contact     = form.get('contact', '').strip()
    due_date    = form.get('due_date', '').strip() or None
    follow_up   = form.get('follow_up_date', '').strip() or None
    # Every new query gets a 5-working-day follow-up window by default.
    if not follow_up:
        follow_up = _add_working_days(date.today(), FOLLOW_UP_WORKING_DAYS).isoformat()
    assigned_to = form.get('assigned_to', '').strip() or None
    department  = form.get('department', '').strip() or None
    mem_id      = form.get('member_id', '').strip() or None
    approval_reason = form.get('approval_reason', '').strip()

    if category not in dict(QUERY_CATEGORIES):
        flash('Choose a category from the list.', 'error')
        return None
    if priority not in dict(PRIORITIES):
        priority = 'medium'
    if not description:
        flash('Category and description are required.', 'error')
        return None
    if mem_id and not str(mem_id).isdigit():
        flash('That member link is not valid.', 'error')
        return None

    is_cancellation = category == 'cancellation'
    cancel_contract_months = 0
    cancel_months_completed = 0
    cancel_monthly_fee = 0.0
    cancel_payment_method = form.get('cancellation_payment_method', '').strip()
    cancel_discount_pct = 0.0
    cancel_member_requested = form.get('cancel_member') == '1'
    if is_cancellation:
        cancel_contract_months = _as_int(form.get('cancellation_contract_months'))
        cancel_monthly_fee = _money(form.get('cancellation_monthly_fee'))
        cancel_discount_pct = _money(form.get('cancellation_discount_pct'))
        if cancel_payment_method and cancel_payment_method not in dict(CANCELLATION_PAYMENT_METHODS):
            cancel_payment_method = ''
        if not mem_id:
            flash('Select the member before logging a cancellation.', 'error')
            return None
        if cancel_contract_months not in CANCELLATION_CONTRACT_MONTHS:
            flash('Select a valid cancellation contract type (12, 24, or 36 months).', 'error')
            return None
        if cancel_monthly_fee <= 0:
            flash('Enter the monthly membership fee before saving the cancellation.', 'error')
            return None
        if str(form.get('cancellation_months_completed', '')).strip() == '':
            # A blank used to count as 0 months — the maximum penalty.
            flash('Enter how many months of the contract the member has completed.', 'error')
            return None
        cancel_months_completed = _as_int(form.get('cancellation_months_completed'))
        if cancel_months_completed < 0 or cancel_months_completed > cancel_contract_months:
            flash('Months completed must be between 0 and the contract length.', 'error')
            return None

    # ── Decision support: snapshot + routing recommendations ────────────────
    snapshot = _account_summary(db, int(mem_id)) if mem_id else None
    if mem_id and not snapshot:
        flash('That member could not be found.', 'error')
        return None

    # Auto-route where the agent didn't override. Type-based routing
    # (e.g. membership Transfer -> Admin) applies even without a linked
    # member; arrears-based priority needs the snapshot.
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
    proposed_pct = 0.0
    approval_needed = []          # human-readable reasons a manager must decide
    if snapshot and category in FINANCIAL_DECISION_CATEGORIES:
        snap_out    = snapshot["total_outstanding"]
        snap_months = snapshot["arrears_months"]
        snap_dpct   = snapshot["discount_percent"]
        snap_damt   = snapshot["discount_amount"]
        snap_settle = snapshot["settlement_amount"]
        snap_action = snapshot["recommended_action"]
        snap_access = snapshot["access_decision"]

    if snapshot and category in PAYMENT_WORKFLOW_CATEGORIES and snapshot["is_owing"]:
        proposed_pct, error = _proposed_discount(form, snapshot)
        if error:
            flash(error, 'error')
            return None
        # The saved snapshot reflects what the agent is actually offering.
        snap_dpct = proposed_pct
        snap_damt = _money(snapshot["total_outstanding"] * proposed_pct / 100)
        snap_settle = _money(snapshot["total_outstanding"] - snap_damt)
        if discount_needs_manager(proposed_pct, snapshot["arrears_months"]):
            if snapshot["manager_discretion"]:
                approval_needed.append(
                    f'{snapshot["arrears_months"]} months in arrears — discount at manager discretion '
                    f'(proposed {proposed_pct:g}%)')
            else:
                approval_needed.append(
                    f'Proposed discount {proposed_pct:g}% is above the {STAFF_DISCOUNT_CEILING}% staff limit')

    cancel_fee = cancel_penalty_months = 0
    cancel_outstanding = cancel_settlement_amount = 0.0
    cancel_settlement_rule = None
    if is_cancellation:
        cancel_fee, cancel_penalty_months = calculate_cancellation_fee(
            cancel_contract_months, cancel_months_completed, cancel_monthly_fee,
        )
        cancel_outstanding = snapshot["total_outstanding"] if snapshot else 0.0
        settlement = calculate_cancellation_settlement(
            cancel_fee, cancel_outstanding, cancel_payment_method, cancel_discount_pct,
        )
        cancel_discount_pct = settlement["discount_pct"]
        cancel_settlement_amount = settlement["settlement_amount"]
        cancel_settlement_rule = settlement["rule"]
        if settlement["rule"] == "none" and cancel_discount_pct > STAFF_DISCOUNT_CEILING:
            approval_needed.append(
                f'Manual cancellation discount {cancel_discount_pct:g}% is above the '
                f'{STAFF_DISCOUNT_CEILING}% staff limit')
        if cancel_member_requested and not _is_manager():
            approval_needed.append('Cancel the member account')

    # A manager logging the query is the approver; nobody else approves their own.
    cancel_member_now = bool(is_cancellation and cancel_member_requested and _is_manager()
                             and not approval_needed)
    approval_status = 'pending' if approval_needed else 'not_required'
    if approval_needed and _is_manager():
        # Managers still record why; the decision is theirs and is logged as such.
        approval_status = 'approved'
        cancel_member_now = bool(is_cancellation and cancel_member_requested)
    snap_mgr = 1 if approval_needed else 0
    status = 'pending_approval' if approval_status == 'pending' else 'open'

    values = dict(
        logged_by=session.get('user_id'),
        member_id=int(mem_id) if mem_id else None,
        member_name=member_name, contact=contact,
        category=category, query_type=query_type, description=description,
        priority=priority, status=status,
        assigned_to=int(assigned_to) if assigned_to and assigned_to.isdigit() else None,
        department=department, due_date=due_date, follow_up_date=follow_up,
        outstanding_snapshot=snap_out, arrears_months_snapshot=snap_months,
        discount_percent_snapshot=snap_dpct, discount_amount_snapshot=snap_damt,
        settlement_amount_snapshot=snap_settle, recommended_action_snapshot=snap_action,
        access_decision_snapshot=snap_access, manager_approval_required=snap_mgr,
        proposed_discount_pct=proposed_pct,
        approval_status=approval_status,
        approval_reason='; '.join(approval_needed) + (f' — {approval_reason}' if approval_reason and approval_needed else '') or None,
        approval_requested_by=session.get('user_id') if approval_needed else None,
        manager_approved_by=session.get('user_id') if approval_status == 'approved' else None,
        cancellation_contract_months=cancel_contract_months if is_cancellation else None,
        cancellation_months_completed=cancel_months_completed if is_cancellation else None,
        cancellation_monthly_fee=cancel_monthly_fee if is_cancellation else 0,
        cancellation_fee=cancel_fee, cancellation_penalty_months=cancel_penalty_months,
        cancellation_payment_method=(cancel_payment_method or None) if is_cancellation else None,
        cancellation_outstanding=cancel_outstanding,
        cancellation_settlement_amount=cancel_settlement_amount,
        cancellation_settlement_rule=cancel_settlement_rule,
        cancellation_discount_pct=cancel_discount_pct if is_cancellation else 0,
        cancel_member_requested=1 if (is_cancellation and cancel_member_requested) else 0,
        member_cancelled=1 if cancel_member_now else 0,
    )
    cols = list(values)
    stamp_cols = []
    if approval_needed:
        stamp_cols.append('approval_requested_at')
    if approval_status == 'approved':
        stamp_cols.append('manager_approved_at')
    col_sql = ', '.join(['reference'] + cols + stamp_cols)
    val_sql = ', '.join(['?'] * (len(cols) + 1) + ["datetime('now','localtime')"] * len(stamp_cols))

    # Two people saving at once can draw the same next reference; retry.
    qid = ref = None
    for _attempt in range(5):
        ref = _next_reference(db)
        try:
            db.execute(f"INSERT INTO queries ({col_sql}) VALUES ({val_sql})",
                       (ref, *[values[c] for c in cols]))
        except Exception as exc:  # noqa: BLE001 — driver-specific IntegrityError
            if not _is_unique_violation(exc):
                raise
            db.rollback()
            continue
        qid = db.execute("SELECT id FROM queries WHERE reference=?", (ref,)).fetchone()['id']
        break
    if qid is None:
        flash('Could not allocate a query reference — please try again.', 'error')
        return None

    _log_note(db, qid, f'Query logged under {_cat_label(category)}'
                       + (f' — {query_type}' if query_type else '') + '.', 'created')
    if approval_status == 'pending':
        _log_note(db, qid, 'Manager approval requested: ' + values['approval_reason'], 'approval')
    elif approval_status == 'approved':
        _log_note(db, qid, 'Logged and approved by manager: ' + values['approval_reason'], 'approval')
    if cancel_member_now:
        _cancel_member(db, qid, int(mem_id))
    db.commit()

    if approval_status == 'pending':
        flash(f'Query {ref} logged and sent for manager approval.', 'success')
    elif cancel_member_now:
        flash(f'Query {ref} logged. Member account cancelled.', 'success')
    else:
        flash(f'Query {ref} created.', 'success')
    if mem_id:
        return redirect(url_for('members.member_detail', mid=mem_id) + '#queries')
    return redirect(url_for('queries.detail', qid=qid))


def _cancel_member(db, qid, member_id):
    """Cancel the member account for an approved cancellation query."""
    db.execute("UPDATE members SET member_status='Cancelled' WHERE id=?", (member_id,))
    db.execute("UPDATE queries SET member_cancelled=1 WHERE id=?", (qid,))
    _log_note(db, qid, 'Member account cancelled (member status set to Cancelled). '
                       'Stop any active debit order / DebiCheck mandate separately.', 'cancelled')
    member_activity(db, member_id, f'Membership cancelled via query #{qid}.')
    audit_log(db, 'query_member_cancelled', old_value=f'query:{qid}', new_value=f'member:{member_id}')


# ── Detail ────────────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>')
@permission_required(QUERY_PERMISSION)
def detail(qid):
    db  = get_db()
    qry = db.execute("""
        SELECT qr.*, m.first_name || ' ' || m.last_name AS member_full_name,
               m.contact AS member_contact, m.id AS member_pk,
               ul.full_name AS logged_by_name,
               ua.full_name AS assigned_to_name,
               uc.full_name AS closed_by_name,
               ur.full_name AS approval_requested_by_name,
               um.full_name AS manager_approved_by_name
        FROM queries qr
        LEFT JOIN members m  ON qr.member_id = m.id
        LEFT JOIN users ul   ON qr.logged_by = ul.id
        LEFT JOIN users ua   ON qr.assigned_to = ua.id
        LEFT JOIN users uc   ON qr.closed_by = uc.id
        LEFT JOIN users ur   ON qr.approval_requested_by = ur.id
        LEFT JOIN users um   ON qr.manager_approved_by = um.id
        WHERE qr.id = ?
    """, (qid,)).fetchone()
    if not qry:
        flash('Query not found.', 'warning')
        return redirect(url_for('queries.index'))

    notes = db.execute("""
        SELECT qn.*, u.full_name AS author
        FROM query_notes qn LEFT JOIN users u ON qn.created_by = u.id
        WHERE qn.query_id = ? ORDER BY qn.created_at ASC, qn.id ASC
    """, (qid,)).fetchall()

    users = db.execute("SELECT id, full_name FROM users WHERE active=1 ORDER BY full_name").fetchall()

    summary = _account_summary(db, qry['member_id']) if qry['member_id'] else None
    # Cancellation/Freeze on an owing account must be acknowledged before closing.
    requires_ack = bool(
        summary and summary['is_owing']
        and qry['category'] in OWING_SENSITIVE_CATEGORIES
    )

    linked_ptp = None
    if qry['ptp_id']:
        linked_ptp = db.execute(
            """SELECT id, ptp_status, promise_amount, promise_date, discount_pct,
                      manager_approval_status, created_at
               FROM ptp_agreements WHERE id=?""",
            (qry['ptp_id'],),
        ).fetchone()

    return render_template(
        'queries/detail.html',
        qry=qry, notes=notes, users=users,
        categories=QUERY_CATEGORIES, query_types=QUERY_TYPES,
        priorities=PRIORITIES, statuses=STATUSES,
        status_options=status_options_for(qry['category']),
        status_label=_status_label,
        departments=DEPARTMENTS,
        priority_colours=PRIORITY_COLOURS, status_colours=STATUS_COLOURS,
        cat_label=_cat_label,
        summary=summary, requires_ack=requires_ack,
        linked_ptp=linked_ptp,
        closure_reasons=CLOSURE_REASONS,
        approval_statuses=APPROVAL_STATUSES,
        is_manager=_is_manager(),
        payment_workflow=qry['category'] in PAYMENT_WORKFLOW_CATEGORIES,
        staff_discount_ceiling=STAFF_DISCOUNT_CEILING,
        today=date.today().isoformat(),
    )


def _load_open_query(db, qid):
    """The query row, or None after flashing why it can't be changed."""
    qry = db.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
    if not qry:
        flash('Query not found.', 'warning')
        return None
    if qry['status'] == 'closed':
        flash('This query is closed.', 'warning')
        return None
    return qry


# ── Add Note ──────────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>/note', methods=['POST'])
@permission_required(QUERY_PERMISSION)
def add_note(qid):
    note = request.form.get('note', '').strip()
    db = get_db()
    if not db.execute("SELECT 1 FROM queries WHERE id=?", (qid,)).fetchone():
        flash('Query not found.', 'warning')
        return redirect(url_for('queries.index'))
    if note:
        _log_note(db, qid, note)
        db.commit()
    return redirect(url_for('queries.detail', qid=qid) + '#timeline')


# ── Update status / assign / priority ─────────────────────────────────────────
@queries_bp.route('/<int:qid>/update', methods=['POST'])
@permission_required(QUERY_PERMISSION)
def update(qid):
    db = get_db()
    qry = _load_open_query(db, qid)
    if not qry:
        return redirect(url_for('queries.index'))

    new_status    = request.form.get('status', '').strip()
    new_priority  = request.form.get('priority', '').strip()
    new_assigned  = request.form.get('assigned_to', '').strip() or None
    new_dept      = request.form.get('department', '').strip() or None
    follow_up     = request.form.get('follow_up_date', '').strip() or None
    due_date      = request.form.get('due_date', '').strip() or None
    action_taken  = request.form.get('action_taken', '').strip() or None

    back = redirect(url_for('queries.detail', qid=qid))
    allowed = {k for k, _ in status_options_for(qry['category'])}
    if new_status and new_status != qry['status']:
        if new_status not in allowed:
            flash('That status cannot be set here. Closing uses Close Query; approvals are decided by a manager.', 'error')
            return back
        if qry['approval_status'] == 'pending':
            flash('This query is waiting for a manager decision — its status changes once approved or rejected.', 'error')
            return back
        if new_status == 'resolved':
            problem = _closing_blocker(db, qry, owing_ack=False)
            if problem:
                flash(problem, 'error')
                return back
    if new_priority and new_priority not in dict(PRIORITIES):
        new_priority = ''
    if new_dept and new_dept not in DEPARTMENTS:
        new_dept = qry['department']
    if new_assigned and not new_assigned.isdigit():
        new_assigned = None

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
        _log_note(db, qid, f"Status changed to {_status_label(new_status)}", 'status_change')
    if new_assigned and str(new_assigned) != str(qry['assigned_to'] or ''):
        user = db.execute("SELECT full_name FROM users WHERE id=?", (int(new_assigned),)).fetchone()
        _log_note(db, qid,
                  f"Assigned to {user['full_name'] if user else 'unknown'}",
                  'assignment')
    db.commit()
    return back


def _closing_blocker(db, qry, owing_ack):
    """Why this query can't be resolved/closed yet, or None."""
    if qry['approval_status'] == 'pending':
        return 'A manager has not decided the approval on this query yet.'
    if qry['member_id'] and qry['category'] in OWING_SENSITIVE_CATEGORIES:
        summary = _account_summary(db, qry['member_id'])
        if summary and summary['is_owing'] and not owing_ack:
            return (
                f"This account is owing R{summary['total_outstanding']:,.2f}. "
                "Use Close Query and confirm the client was informed the outstanding amount "
                "must be settled / will be added before closing this query."
            )
    return None


# ── Close ─────────────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>/close', methods=['POST'])
@permission_required(QUERY_PERMISSION)
def close(qid):
    db = get_db()
    qry = _load_open_query(db, qid)
    if not qry:
        return redirect(url_for('queries.detail', qid=qid))
    resolution = request.form.get('resolution_notes', '').strip()
    reason = request.form.get('closure_reason', '').strip()
    back = redirect(url_for('queries.detail', qid=qid))

    if reason not in dict(CLOSURE_REASONS):
        flash('Choose a closure reason.', 'error')
        return back
    if not resolution:
        flash('Add resolution notes — what was done for the member.', 'error')
        return back
    # Cancellation/Freeze on an owing account: the agent must confirm the client
    # was informed the outstanding amount will be added before we proceed.
    problem = _closing_blocker(db, qry, owing_ack=bool(request.form.get('owing_ack')))
    if problem:
        flash(problem, 'error')
        return back

    db.execute("""
        UPDATE queries SET status='closed', resolution_notes=?, closure_reason=?,
               closed_by=?, closed_at=datetime('now','localtime'),
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (resolution, reason, session.get('user_id'), qid))
    _log_note(db, qid,
              f"Query closed — {dict(CLOSURE_REASONS)[reason]}. Resolution: {resolution}",
              'closed')
    db.commit()
    flash('Query closed.', 'success')
    return back


# ── Escalate ──────────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>/escalate', methods=['POST'])
@permission_required(QUERY_PERMISSION)
def escalate(qid):
    db = get_db()
    qry = _load_open_query(db, qid)
    if not qry:
        return redirect(url_for('queries.index'))
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('Give a reason for escalating.', 'error')
        return redirect(url_for('queries.detail', qid=qid))
    # An escalation goes to Management and lands on today's follow-up list,
    # so it shows on every manager's follow-up badge straight away.
    db.execute("""
        UPDATE queries SET status=CASE WHEN status='pending_approval' THEN status ELSE 'escalated' END,
               priority='urgent', department='Management', follow_up_date=?,
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (date.today().isoformat(), qid))
    _log_note(db, qid, f"Escalated to management. Reason: {reason}", 'escalated')
    db.commit()
    flash('Query escalated to management.', 'success')
    return redirect(url_for('queries.detail', qid=qid))


# ── Approval ──────────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>/request-approval', methods=['POST'])
@permission_required(QUERY_PERMISSION)
def request_approval(qid):
    """An agent proposes a discount (or a member cancellation) on an open
    query and sends it to a manager."""
    db = get_db()
    qry = _load_open_query(db, qid)
    back = redirect(url_for('queries.detail', qid=qid))
    if not qry:
        return back
    if qry['approval_status'] == 'pending':
        flash('This query is already waiting for a manager.', 'warning')
        return back
    reason = request.form.get('approval_reason', '').strip()
    if not reason:
        flash('Explain what you are asking the manager to approve.', 'error')
        return back

    proposed = qry['proposed_discount_pct'] or 0
    asks = []
    if qry['member_id'] and qry['category'] in PAYMENT_WORKFLOW_CATEGORIES:
        summary = _account_summary(db, qry['member_id'])
        if summary and summary['is_owing']:
            proposed, error = _proposed_discount(request.form, summary)
            if error:
                flash(error, 'error')
                return back
            asks.append(f'Discount {proposed:g}% ({summary["discount_range_label"]} for '
                        f'{summary["arrears_months"]} month(s) in arrears)')
    cancel_member = (qry['category'] == 'cancellation' and qry['member_id']
                     and not qry['member_cancelled'] and request.form.get('cancel_member') == '1')
    if cancel_member:
        asks.append('Cancel the member account')
    asks.append(reason)

    db.execute("""
        UPDATE queries SET approval_status='pending', status='pending_approval',
               proposed_discount_pct=?, approval_reason=?, approval_notes=NULL,
               cancel_member_requested=CASE WHEN ? THEN 1 ELSE cancel_member_requested END,
               manager_approval_required=1,
               approval_requested_by=?, approval_requested_at=datetime('now','localtime'),
               manager_approved_by=NULL, manager_approved_at=NULL,
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (proposed, '; '.join(asks), 1 if cancel_member else 0, session.get('user_id'), qid))
    _log_note(db, qid, 'Manager approval requested: ' + '; '.join(asks), 'approval')
    db.commit()
    flash('Sent for manager approval.', 'success')
    return back


@queries_bp.route('/<int:qid>/decide', methods=['POST'])
@roles_required('admin', 'manager')
def decide(qid):
    """A manager approves or rejects what the agent proposed."""
    db = get_db()
    qry = _load_open_query(db, qid)
    back = redirect(url_for('queries.detail', qid=qid))
    if not qry:
        return back
    if qry['approval_status'] != 'pending':
        flash('There is nothing waiting for approval on this query.', 'warning')
        return back
    decision = request.form.get('decision', '').strip()
    notes = request.form.get('approval_notes', '').strip()
    if decision not in ('approved', 'rejected'):
        flash('Choose approve or reject.', 'error')
        return back
    if decision == 'rejected' and not notes:
        flash('Say why the request is rejected so the agent can tell the member.', 'error')
        return back
    if qry['approval_requested_by'] and qry['approval_requested_by'] == session.get('user_id') \
            and session.get('role') != 'admin':
        flash('You requested this approval — another manager must decide it.', 'error')
        return back

    db.execute("""
        UPDATE queries SET approval_status=?, approval_notes=?, status='in_progress',
               manager_approved_by=?, manager_approved_at=datetime('now','localtime'),
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (decision, notes or None, session.get('user_id'), qid))
    label = 'Approved' if decision == 'approved' else 'Rejected'
    _log_note(db, qid, f"{label} by manager." + (f" Notes: {notes}" if notes else ''), 'approval')
    audit_log(db, f'query_approval_{decision}', old_value=f'query:{qid}',
              new_value=(qry['approval_reason'] or '')[:500])
    if decision == 'approved' and qry['cancel_member_requested'] and qry['member_id'] \
            and not qry['member_cancelled']:
        _cancel_member(db, qid, qry['member_id'])
    db.commit()
    flash(f'Request {label.lower()}.', 'success')
    return back


# ── Convert to PTP ────────────────────────────────────────────────────────────
@queries_bp.route('/<int:qid>/convert-ptp', methods=['POST'])
@permission_required(QUERY_PERMISSION)
def convert_ptp(qid):
    """Open the member's PTP form for this query. The PTP itself is created in
    the PTP module; when it is saved it is linked back to this query (ptp_id)
    and the query follows its outcome."""
    db  = get_db()
    qry = _load_open_query(db, qid)
    if not qry:
        return redirect(url_for('queries.index'))
    back = redirect(url_for('queries.detail', qid=qid))
    if not qry['member_id']:
        flash('Query must be linked to a member to convert to PTP.', 'error')
        return back
    if qry['approval_status'] == 'pending':
        flash('Wait for the manager decision before creating the PTP.', 'error')
        return back
    if qry['ptp_id']:
        ptp = db.execute("SELECT ptp_status FROM ptp_agreements WHERE id=?", (qry['ptp_id'],)).fetchone()
        if ptp and ptp['ptp_status'] in ('pending', 'partially_paid'):
            flash(f"This query is already linked to PTP #{qry['ptp_id']}.", 'warning')
            return back
    _log_note(db, qid, 'PTP form opened from this query.', 'ptp')
    db.execute("""
        UPDATE queries SET status='in_progress', updated_at=datetime('now','localtime')
        WHERE id=? AND status IN ('open','arrears_confirmed','discount_offered','pending_member','pending_admin')
    """, (qid,))
    db.commit()
    return redirect(url_for('members.member_detail', mid=qry['member_id'], from_query=qid) + '#ptp')
