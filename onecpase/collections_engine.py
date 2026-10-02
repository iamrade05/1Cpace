"""Shared collections policy, workflow, and audit helpers.

The route modules remain responsible for HTTP concerns. This module owns the
business decisions that must be consistent across Collections, PTP, DebiCheck,
and access control.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")
DEFAULT_COLLECTION_RULES = {
    "daily_call_target": ("40", "integer"),
    "upfront_min_percent": ("30", "decimal"),
    "upfront_max_percent": ("40", "decimal"),
    "minimum_arrears_installment": ("30.00", "decimal"),
    "inactive_after_months": ("6", "integer"),
    "reminder_offset_days": ("2", "integer"),
    "sales_transfer_months": ("2", "integer"),
    "first_debit_window_days": ("45", "integer"),
}

OWNER_SALES = "SALES"
OWNER_RECEPTION = "RECEPTION"
REASON_FIRST_MONTH = "FIRST_MONTH_FAILED_DEBIT"
REASON_SECOND_MONTH = "SECOND_MONTH_ARREARS"
REASON_STANDARD = "STANDARD_ARREARS"

# Workflow stage is separate from ageing: a member can be 3 months owing and
# have an active PTP. Precedence lives in determine_collection_stage.
STAGE_CONTACT_REQUIRED = "CONTACT_REQUIRED"
STAGE_CONTACTING = "CONTACTING"
STAGE_CONTACTED = "CONTACTED"
STAGE_ARRANGEMENT_REQUIRED = "ARRANGEMENT_REQUIRED"
STAGE_PTP_ACTIVE = "PTP_ACTIVE"
STAGE_PTP_DUE = "PTP_DUE"
STAGE_PTP_BROKEN = "PTP_BROKEN"
STAGE_APPROVAL_PENDING = "APPROVAL_PENDING"
STAGE_ACCESS_RESTRICTED = "ACCESS_RESTRICTED"
STAGE_INACTIVE = "INACTIVE"
STAGE_RESOLVED = "RESOLVED"

PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_NORMAL = 1, 2, 3

NEXT_ACTION_BY_STAGE = {
    STAGE_CONTACT_REQUIRED: "CALL_MEMBER",
    STAGE_CONTACTING: "FOLLOW_UP",
    STAGE_CONTACTED: "CREATE_PTP",
    STAGE_ARRANGEMENT_REQUIRED: "CREATE_ARRANGEMENT",
    STAGE_PTP_ACTIVE: "MONITOR_PTP",
    STAGE_PTP_DUE: "VERIFY_PAYMENT",
    STAGE_PTP_BROKEN: "FOLLOW_BROKEN_PTP",
    STAGE_APPROVAL_PENDING: "MANAGER_REVIEW",
    STAGE_ACCESS_RESTRICTED: "REVIEW_ACCESS",
    STAGE_INACTIVE: "NO_ACTION",
    STAGE_RESOLVED: "NO_ACTION",
}

COMMUNICATION_ORDER = ("CALL", "WHATSAPP", "SMS", "EMAIL")

# Authoritative arrears settlement policy, used by both the PTP recovery plan
# and the Queries account decision so the two cannot drift apart.
# Discount is applied FIRST. The upfront contribution is then calculated
# against the discounted balance. Higher discounts require manager approval.
ARREARS_DISCOUNT_RULES = ((6, 75), (4, 50), (2, 25))
ARREARS_DISCOUNT_MANAGER_RULES = ((6, True), (4, True), (2, False))


def arrears_discount_policy(months_owing):
    """Return the approved discount tier and whether manager approval is required."""
    try:
        months = int(months_owing or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("months_owing must be an integer") from exc
    if months < 0:
        raise ValueError("months_owing cannot be negative")

    discount_percent = 0
    manager_approval_required = False
    for min_months, percent in ARREARS_DISCOUNT_RULES:
        if months >= min_months:
            discount_percent = percent
            break
    for min_months, requires_manager in ARREARS_DISCOUNT_MANAGER_RULES:
        if months >= min_months:
            manager_approval_required = requires_manager
            break

    return {
        "discount_percent": discount_percent,
        "manager_approval_required": manager_approval_required,
    }

VALID_CALL_OUTCOMES = {
    "reminder_delivered",
    "promised_to_pay",
    "asked_callback",
    "already_paid",
    "disputed",
    "refused",
    "no_answer",
    "busy",
    "voicemail",
    "wrong_number",
}
SUCCESSFUL_CALL_OUTCOMES = {
    "reminder_delivered",
    "promised_to_pay",
    "asked_callback",
    "already_paid",
    "disputed",
    "refused",
}
UNSUCCESSFUL_CALL_OUTCOMES = {"no_answer", "busy", "voicemail", "wrong_number"}


def money(value) -> Decimal:
    """Convert a number-like value into two-decimal currency safely."""
    try:
        return Decimal(str(value or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid monetary value: {value!r}") from exc


def _decimal_rule(rules, key: str, default: str) -> Decimal:
    value = rules.get(key, default) if rules else default
    return money(value)


def _integer_rule(rules, key: str, default: int) -> int:
    value = rules.get(key, default) if rules else default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer rule {key}: {value!r}") from exc


def _effective_upfront_percent(rules) -> Decimal:
    """Never demand more upfront than the configured ceiling (Rule 6).

    Under any sane configuration (min below max, e.g. the seeded 30/40) this
    is a no-op - min_percent already governs required_upfront_amount below
    it. It only matters if upfront_min_percent is ever configured above
    upfront_max_percent, in which case it stops the required amount from
    silently exceeding the policy's own stated ceiling.
    """
    min_percent = _decimal_rule(rules, "upfront_min_percent", "30")
    max_percent = _decimal_rule(rules, "upfront_max_percent", "40")
    return min(min_percent, max_percent)


def calculate_arrears_installment(
    arrears,
    months,
    minimum=Decimal("30.00"),
):
    arrears = money(arrears)
    minimum = money(minimum)
    if months <= 0:
        return Decimal("0.00")
    calculated = money(arrears / Decimal(months))
    return max(calculated, minimum)


def calculate_debicheck_options(
    arrears,
    normal_installment,
    remaining_term=None,
    minimum=Decimal("30.00"),
):
    """Return standard DebiCheck arrears recovery choices."""
    arrears = money(arrears)
    normal_installment = money(normal_installment)
    terms = [3, 6]
    if remaining_term and int(remaining_term) > 0:
        terms.append(int(remaining_term))

    options = []
    seen_terms = set()
    for term in terms:
        if term in seen_terms:
            continue
        seen_terms.add(term)
        arrears_installment = calculate_arrears_installment(arrears, term, minimum)
        options.append({
            "term": term,
            "arrears_installment": arrears_installment,
            "new_monthly_amount": money(normal_installment + arrears_installment),
        })
    return options



def is_debicheck_approved(status) -> bool:
    """Return True only for a confirmed/active DebiCheck mandate."""
    value = str(status or "").strip().lower()
    return value in {"approved", "authorised", "authorized", "accepted", "active", "completed"}


def upfront_percentage(arrears, amount) -> Decimal:
    """Calculate the actual upfront contribution percentage against arrears."""
    arrears = money(arrears)
    amount = money(amount)
    if arrears <= 0:
        return Decimal("0")
    return (amount * Decimal("100") / arrears).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def required_upfront_amount(arrears, percent) -> Decimal:
    return money(money(arrears) * money(percent) / Decimal("100"))


def calculate_realistic_recovery_plan(
    arrears,
    normal_installment,
    discount_percent=0,
    upfront_percent=30,
):
    """Calculate an affordable arrears recovery plan.

    The order is authoritative: discount the arrears first, then calculate the
    upfront contribution from the discounted balance. The remaining balance is
    recovered using the member's normal monthly instalment, with a smaller final
    instalment where necessary.
    """
    arrears = money(arrears)
    normal_installment = money(normal_installment)
    discount_percent = money(discount_percent)
    upfront_percent = money(upfront_percent)

    if arrears < 0 or normal_installment < 0:
        raise ValueError("arrears and normal_installment cannot be negative")
    if discount_percent < 0 or discount_percent > 100:
        raise ValueError("discount_percent must be between 0 and 100")
    if upfront_percent < 0 or upfront_percent > 100:
        raise ValueError("upfront_percent must be between 0 and 100")

    discount_amount = money(arrears * discount_percent / Decimal("100"))
    discounted_balance = money(arrears - discount_amount)
    upfront_amount = required_upfront_amount(discounted_balance, upfront_percent)
    remaining_balance = money(discounted_balance - upfront_amount)

    if remaining_balance > 0 and normal_installment > 0:
        recovery_months = int((remaining_balance / normal_installment).to_integral_value(rounding=ROUND_CEILING))
        if money(normal_installment * recovery_months) < remaining_balance:
            recovery_months += 1
    else:
        recovery_months = 0

    if remaining_balance > 0 and normal_installment > 0:
        regular_months = max(recovery_months - 1, 0)
        final_installment = money(remaining_balance - normal_installment * regular_months)
        recovery_installment = normal_installment
    else:
        regular_months = 0
        final_installment = Decimal("0.00")
        recovery_installment = Decimal("0.00")

    return {
        "arrears": arrears,
        "normal_installment": normal_installment,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
        "discounted_balance": discounted_balance,
        "upfront_percent": upfront_percent,
        "upfront_amount": upfront_amount,
        "remaining_balance": remaining_balance,
        "recovery_installment": recovery_installment,
        "recovery_months": recovery_months,
        "regular_recovery_months": regular_months,
        "final_recovery_installment": final_installment,
        "monthly_amount_during_recovery": money(normal_installment + recovery_installment),
    }


def _parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            parsed = datetime.strptime(str(value)[:10], fmt).date()
            return parsed.replace(day=1) if fmt == "%Y-%m" else parsed
        except ValueError:
            continue
    return None


def _older_than_inactive_threshold(oldest_arrears_month, today, months) -> bool:
    oldest = _parse_date(oldest_arrears_month)
    current = _parse_date(today) or date.today()
    return bool(oldest and current - oldest > timedelta(days=months * 30))


def evaluate_collection_case(
    months_owing,
    arrears,
    debicheck_active,
    upfront_paid=False,
    upfront_amount=0,
    full_settlement=False,
    *,
    rules=None,
    oldest_arrears_month=None,
    today=None,
):
    """Return the authoritative decision for an arrears case.

    ``debicheck_active`` means the mandate is valid for the current case. The
    caller can therefore require both a submitted mandate and a live PTP.
    """
    try:
        months_owing = int(months_owing)
    except (TypeError, ValueError) as exc:
        raise ValueError("months_owing must be an integer") from exc
    if months_owing < 0:
        raise ValueError("months_owing cannot be negative")

    arrears = money(arrears)
    upfront_amount = money(upfront_amount)
    inactive_after = _integer_rule(rules, "inactive_after_months", 6)
    min_upfront = _effective_upfront_percent(rules)
    required_upfront = required_upfront_amount(arrears, min_upfront)
    inactive = months_owing > inactive_after or _older_than_inactive_threshold(
        oldest_arrears_month, today, inactive_after
    )

    if months_owing <= 0:
        return {
            "status": "CURRENT",
            "access": "ALLOWED",
            "debicheck_required": False,
            "upfront_required": False,
            "escalation": False,
            "action": "NO_ACTION",
        }

    if inactive:
        return {
            "status": "INACTIVE",
            "access": "BLOCKED",
            "debicheck_required": False,
            "upfront_required": False,
            "escalation": True,
            "action": "MANAGER_REVIEW_OR_LEGAL_REFERRAL",
            "required_upfront_amount": required_upfront,
        }

    if months_owing == 1:
        return {
            "status": "ONE_MONTH_ARREARS",
            "access": "ALLOWED",
            "debicheck_required": False,
            "upfront_required": False,
            "escalation": False,
            "action": "COLLECTION_FOLLOW_UP",
        }

    if months_owing == 2:
        if debicheck_active:
            return {
                "status": "TWO_MONTHS_DEBICHECK",
                "access": "ALLOWED",
                "debicheck_required": True,
                "upfront_required": False,
                "escalation": False,
                "action": "CREATE_PTP_AND_DEBICHECK_PLAN",
            }
        upfront_condition_met = upfront_paid or upfront_amount >= required_upfront
        if upfront_condition_met:
            return {
                "status": "TWO_MONTHS_NO_DEBICHECK_UPFRONT",
                "access": "ALLOWED",
                "debicheck_required": False,
                "upfront_required": True,
                "upfront_amount": upfront_amount,
                "required_upfront_amount": required_upfront,
                "escalation": False,
                "action": "CREATE_PAYMENT_PLAN",
            }
        return {
            "status": "TWO_MONTHS_RESTRICTED",
            "access": "BLOCKED",
            "debicheck_required": False,
            "upfront_required": True,
            "required_upfront_amount": required_upfront,
            "escalation": False,
            "action": "WAIT_FOR_UPFRONT_PAYMENT",
        }

    if full_settlement:
        return {
            "status": "FULL_SETTLEMENT",
            "access": "ALLOWED",
            "debicheck_required": False,
            "upfront_required": False,
            "escalation": False,
            "action": "CLEAR_ARREARS",
        }
    if debicheck_active:
        return {
            "status": "THREE_PLUS_DEBICHECK",
            "access": "ALLOWED",
            "debicheck_required": True,
            "upfront_required": False,
            "escalation": False,
            "action": "CREATE_FORMAL_PTP",
        }
    return {
        "status": "THREE_PLUS_NO_DEBICHECK",
        "access": "BLOCKED",
        "debicheck_required": True,
        "upfront_required": False,
        "escalation": False,
        "action": "DEBICHECK_REQUIRED_OR_FULL_SETTLEMENT",
    }


def next_communication(last_outcome=None):
    """Return the next channel in the fixed Call→WhatsApp→SMS→Email flow."""
    if last_outcome is None:
        return "CALL"
    outcome = str(last_outcome).strip().upper()
    if outcome in {"CONTACTED", "REMINDER_DELIVERED", "PROMISED_TO_PAY", "ASKED_CALLBACK", "ALREADY_PAID", "DISPUTED", "REFUSED"}:
        return None
    if outcome in {"NO_ANSWER", "BUSY", "UNREACHABLE", "WRONG_NUMBER", "VOICEMAIL"}:
        return "WHATSAPP"
    if outcome == "WHATSAPP_FAILED":
        return "SMS"
    if outcome == "SMS_FAILED":
        return "EMAIL"
    if outcome == "EMAIL_FAILED":
        return None
    return None


def complete_call(outcome):
    normalized = str(outcome or "").strip().lower()
    if normalized not in VALID_CALL_OUTCOMES:
        raise ValueError("A valid call outcome is required.")
    return {
        "completed": True,
        "outcome": normalized,
        "successful_contact": normalized in SUCCESSFUL_CALL_OUTCOMES,
        "next_action": next_communication(normalized),
    }


def calculate_daily_call_kpi(calls_completed, target=40, contacts=0, calls_attempted=None):
    target = int(target)
    completed = max(int(calls_completed), 0)
    attempted = completed if calls_attempted is None else max(int(calls_attempted), 0)
    remaining = max(target - completed, 0)
    completion_percentage = round((completed / target) * 100, 2) if target else 0
    contact_rate = round((int(contacts) / attempted) * 100, 2) if attempted else 0
    return {
        "target": target,
        "completed": completed,
        "remaining": remaining,
        "completion_percentage": completion_percentage,
        "calls_attempted": attempted,
        "contacts": max(int(contacts), 0),
        "contact_rate": contact_rate,
    }


def evaluate_ptp(promised_date, payment_received, today=None):
    promised = _parse_date(promised_date)
    current = _parse_date(today) or date.today()
    if payment_received:
        return "PAID"
    if promised and current > promised:
        return "BROKEN"
    return "ACTIVE"


def evaluate_member_access(db, member_id: int, *, today=None) -> dict:
    """Evaluate a member's collections access from live account data.

    This is the enforcement point used by access control and scheduled
    automation. The persisted ``members.gym_access_status`` is treated as a
    cache/audit result, not as the business-rule authority.
    """
    from .ptp import bulk_member_arrears

    member = db.execute(
        """SELECT id, member_status, gym_access_status, access_blocked_until,
                  monthly_installment
           FROM members WHERE id=?""", (member_id,)
    ).fetchone()
    if not member:
        return {"status": "UNKNOWN", "access": "BLOCKED", "action": "MEMBER_NOT_FOUND"}

    arrears = bulk_member_arrears(db, [member_id]).get(member_id, {})
    total = arrears.get("total_arrears", 0)
    months = arrears.get("months_in_arrears", 0)
    oldest = arrears.get("oldest_arrears_month")

    # A qualifying arrangement is a live PTP plus an approved/authorised
    # DebiCheck mandate. Submitted alone is deliberately insufficient.
    debicheck_active = db.execute(
        """SELECT 1 FROM ptp_agreements p
           JOIN debicheck_mandates d ON d.member_id=p.member_id
           WHERE p.member_id=? AND p.ptp_status IN ('pending','partially_paid')
             AND COALESCE(p.manager_approval_status, 'not_required') IN ('not_required','approved')
             AND lower(COALESCE(d.status,'')) IN
                 ('approved','authorised','authorized','accepted','active','completed')
           LIMIT 1""", (member_id,)
    ).fetchone() is not None

    rules = get_collection_rules(db)
    verified_receipts = db.execute(
        "SELECT COALESCE(SUM(amount_paid),0) AS total FROM ptp_receipts WHERE member_id=? AND verification_status='verified'",
        (member_id,),
    ).fetchone()
    verified_paid = money(verified_receipts["total"] if verified_receipts else 0)
    required_upfront = required_upfront_amount(total, _effective_upfront_percent(rules))
    decision = evaluate_collection_case(
        months, total, debicheck_active,
        upfront_paid=verified_paid >= required_upfront if months == 2 else False,
        upfront_amount=verified_paid,
        full_settlement=total > 0 and verified_paid >= money(total),
        rules=rules, oldest_arrears_month=oldest, today=today,
    )
    decision = apply_manager_exception(db, member_id, decision, today=today)
    decision.update({
        "member_status": member["member_status"],
        "arrears": total,
        "months_owing": months,
        "debicheck_active": debicheck_active,
        "verified_payment_amount": verified_paid,
        "required_upfront_amount": required_upfront,
        "persisted_access": member["gym_access_status"] or "allowed",
    })
    return decision


def determine_access(
    months_owing,
    arrears,
    debicheck_active,
    upfront_paid=False,
    upfront_amount=0,
    full_settlement=False,
    *,
    rules=None,
):
    """Return only the access result for turnstile/access integrations."""
    return evaluate_collection_case(
        months_owing,
        arrears,
        debicheck_active,
        upfront_paid=upfront_paid,
        upfront_amount=upfront_amount,
        full_settlement=full_settlement,
        rules=rules,
    )["access"]


def _engine_table_statements(backend):
    id_type = "BIGSERIAL PRIMARY KEY" if backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    real_type = "DOUBLE PRECISION" if backend == "postgresql" else "REAL"
    timestamp = "TIMESTAMPTZ DEFAULT NOW()" if backend == "postgresql" else "TEXT DEFAULT (datetime('now', 'localtime'))"
    return [
        f"""CREATE TABLE IF NOT EXISTS collection_rules (
            rule_key TEXT PRIMARY KEY, rule_value TEXT NOT NULL, value_type TEXT NOT NULL DEFAULT 'text',
            active INTEGER NOT NULL DEFAULT 1, updated_at {timestamp}
        )""",
        f"""CREATE TABLE IF NOT EXISTS collections_cases (
            id {id_type}, member_id INTEGER NOT NULL, collection_id INTEGER, status TEXT NOT NULL DEFAULT 'open',
            failed_debit_date TEXT, failure_reason TEXT, arrears_amount {real_type} DEFAULT 0,
            months_owing INTEGER DEFAULT 0, monthly_installment {real_type} DEFAULT 0,
            debicheck_status TEXT DEFAULT 'unknown', access_status TEXT DEFAULT 'allowed', assigned_to INTEGER,
            created_at {timestamp}, updated_at {timestamp}, closed_at TEXT,
            FOREIGN KEY (member_id) REFERENCES members(id), FOREIGN KEY (collection_id) REFERENCES collections(id),
            FOREIGN KEY (assigned_to) REFERENCES users(id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS collection_communications (
            id {id_type}, case_id INTEGER, member_id INTEGER NOT NULL, task_id INTEGER,
            channel TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', outcome TEXT, notes TEXT,
            sent_at TEXT, created_by INTEGER, created_at {timestamp},
            FOREIGN KEY (case_id) REFERENCES collections_cases(id), FOREIGN KEY (member_id) REFERENCES members(id),
            FOREIGN KEY (task_id) REFERENCES collection_call_tasks(id), FOREIGN KEY (created_by) REFERENCES users(id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS collection_payments (
            id {id_type}, case_id INTEGER, member_id INTEGER NOT NULL, collection_id INTEGER,
            amount {real_type} NOT NULL, payment_date TEXT NOT NULL, method TEXT, reference TEXT,
            created_by INTEGER, created_at {timestamp},
            FOREIGN KEY (case_id) REFERENCES collections_cases(id), FOREIGN KEY (member_id) REFERENCES members(id),
            FOREIGN KEY (collection_id) REFERENCES collections(id), FOREIGN KEY (created_by) REFERENCES users(id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS collection_exceptions (
            id {id_type}, case_id INTEGER, ptp_id INTEGER, member_id INTEGER NOT NULL, reason TEXT NOT NULL,
            requested_action TEXT, status TEXT NOT NULL DEFAULT 'pending', notes TEXT,
            created_by INTEGER, decided_by INTEGER, decided_at TEXT, created_at {timestamp},
            FOREIGN KEY (case_id) REFERENCES collections_cases(id), FOREIGN KEY (member_id) REFERENCES members(id),
            FOREIGN KEY (created_by) REFERENCES users(id), FOREIGN KEY (decided_by) REFERENCES users(id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS access_decisions (
            id {id_type}, case_id INTEGER, member_id INTEGER NOT NULL, access_status TEXT NOT NULL,
            reason TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'collections_engine', evaluated_at {timestamp},
            FOREIGN KEY (case_id) REFERENCES collections_cases(id), FOREIGN KEY (member_id) REFERENCES members(id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS collection_assignment_history (
            id {id_type}, case_id INTEGER NOT NULL, member_id INTEGER NOT NULL,
            from_owner_type TEXT, from_staff_id INTEGER,
            to_owner_type TEXT NOT NULL, to_staff_id INTEGER,
            reason TEXT NOT NULL, system_generated INTEGER NOT NULL DEFAULT 1,
            transferred_by INTEGER, transferred_at {timestamp},
            FOREIGN KEY (case_id) REFERENCES collections_cases(id), FOREIGN KEY (member_id) REFERENCES members(id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS collection_stage_history (
            id {id_type}, case_id INTEGER NOT NULL, member_id INTEGER NOT NULL,
            from_stage TEXT, to_stage TEXT NOT NULL, reason TEXT,
            changed_by INTEGER, source TEXT NOT NULL DEFAULT 'system', changed_at {timestamp},
            FOREIGN KEY (case_id) REFERENCES collections_cases(id), FOREIGN KEY (member_id) REFERENCES members(id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_collection_stage_case ON collection_stage_history(case_id, changed_at)",
        "CREATE INDEX IF NOT EXISTS idx_collections_cases_member_status ON collections_cases(member_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_collection_assignment_case ON collection_assignment_history(case_id, transferred_at)",
        "CREATE INDEX IF NOT EXISTS idx_collection_communications_member ON collection_communications(member_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_collection_exceptions_status ON collection_exceptions(status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_access_decisions_member ON access_decisions(member_id, evaluated_at)",
    ]


def ensure_collections_engine_schema(db, backend="sqlite"):
    for statement in _engine_table_statements(backend):
        db.execute(statement)
    from .database import _ensure_column

    _ensure_column(db, backend, "collection_exceptions", "ptp_id", "INTEGER")
    _ensure_column(db, backend, "collections_cases", "owner_type", "TEXT DEFAULT 'RECEPTION'")
    _ensure_column(db, backend, "collections_cases", "owner_staff_id", "INTEGER")
    _ensure_column(db, backend, "collections_cases", "original_sales_consultant_id", "INTEGER")
    _ensure_column(db, backend, "collections_cases", "assignment_reason", "TEXT")
    _ensure_column(db, backend, "collections_cases", "assigned_at", "TEXT")
    _ensure_column(db, backend, "collections_cases", "collection_stage", "TEXT")
    _ensure_column(db, backend, "collections_cases", "priority", "INTEGER DEFAULT 3")
    _ensure_column(db, backend, "collections_cases", "next_action", "TEXT")
    insert = "INSERT INTO collection_rules (rule_key, rule_value, value_type) VALUES (?, ?, ?)"
    if backend == "sqlite":
        insert = insert.replace("INSERT INTO", "INSERT OR IGNORE INTO")
    else:
        insert += " ON CONFLICT (rule_key) DO NOTHING"
    for key, (value, value_type) in DEFAULT_COLLECTION_RULES.items():
        db.execute(insert, (key, value, value_type))
    db.commit()


def get_collection_rules(db):
    rows = db.execute(
        "SELECT rule_key, rule_value FROM collection_rules WHERE active=1"
    ).fetchall()
    return {row["rule_key"]: row["rule_value"] for row in rows}


def get_collection_rule(db, key, default=None):
    row = db.execute(
        "SELECT rule_value FROM collection_rules WHERE rule_key=? AND active=1", (key,)
    ).fetchone()
    return row["rule_value"] if row else default


def get_collection_rule_int(db, key, default):
    try:
        return int(get_collection_rule(db, key, default))
    except (TypeError, ValueError):
        return default


def sync_collection_case(
    db,
    member_id,
    *,
    collection_id=None,
    failed_debit_date=None,
    failure_reason=None,
    arrears_amount=0,
    months_owing=0,
    monthly_installment=0,
    debicheck_status="unknown",
    access_status="allowed",
    assigned_to=None,
):
    """Create or refresh the open case for a member and return its id."""
    existing = db.execute(
        """SELECT id FROM collections_cases
           WHERE member_id=? AND status IN ('open','active','pending')
           ORDER BY id DESC LIMIT 1""",
        (member_id,),
    ).fetchone()
    values = (
        collection_id, failed_debit_date, failure_reason, float(money(arrears_amount)),
        int(months_owing), float(money(monthly_installment)), debicheck_status,
        access_status, assigned_to,
    )
    if existing:
        db.execute(
            """UPDATE collections_cases SET collection_id=?, failed_debit_date=?, failure_reason=?,
               arrears_amount=?, months_owing=?, monthly_installment=?, debicheck_status=?,
               access_status=?, assigned_to=?, updated_at=datetime('now','localtime') WHERE id=?""",
            (*values, existing["id"]),
        )
        apply_case_ownership(db, existing["id"], months_owing=months_owing)
        refresh_case_workflow(db, existing["id"], reason="Case refreshed")
        return existing["id"]
    cursor = db.execute(
        """INSERT INTO collections_cases
           (member_id, collection_id, failed_debit_date, failure_reason, arrears_amount,
            months_owing, monthly_installment, debicheck_status, access_status, assigned_to)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (member_id, *values),
    )
    apply_case_ownership(db, cursor.lastrowid, months_owing=months_owing)
    refresh_case_workflow(db, cursor.lastrowid, reason="Case opened")
    return cursor.lastrowid


def find_original_sales_consultant(db, member_id):
    """The consultant who signed the member: the application's lead owner, else
    whoever created the application. Never chosen by hand."""
    row = db.execute(
        """SELECT COALESCE(l.assigned_to, ma.created_by) AS consultant_id
           FROM membership_applications ma LEFT JOIN leads l ON l.id = ma.lead_id
           WHERE ma.member_id=? ORDER BY ma.id DESC LIMIT 1""",
        (member_id,),
    ).fetchone()
    return row["consultant_id"] if row and row["consultant_id"] else None


def is_first_debit_failure(db, member_id, failed_debit_date, months_owing):
    """A new member whose first scheduled debit failed — not merely a member
    who is one month behind. Needs: joined within the first-debit window, no
    earlier recurring payment, and at most one month owing."""
    if int(months_owing or 0) > 1:
        return False
    member = db.execute("SELECT join_date FROM members WHERE id=?", (member_id,)).fetchone()
    joined, failed = _parse_date(member["join_date"] if member else None), _parse_date(failed_debit_date)
    if not joined or not failed or failed < joined:
        return False
    window = get_collection_rule_int(db, "first_debit_window_days", 45)
    if (failed - joined).days > window:
        return False
    earlier_payment = db.execute(
        """SELECT 1 FROM collections
           WHERE member_id=? AND collection_date < ?
             AND (status='paid' OR COALESCE(amount_paid,0) > 0)
             AND LOWER(COALESCE(notes,'')) LIKE '%recurring%' LIMIT 1""",
        (member_id, failed.isoformat()),
    ).fetchone()
    return earlier_payment is None


def determine_owner(*, months_owing, first_debit_failure, original_sales_consultant_id,
                    transfer_months=2):
    """Returns (owner_type, owner_staff_id, reason). Two months owing always
    belongs to Reception; a first-debit failure belongs to the signing consultant."""
    if int(months_owing or 0) >= int(transfer_months):
        return OWNER_RECEPTION, None, REASON_SECOND_MONTH
    if first_debit_failure and original_sales_consultant_id:
        return OWNER_SALES, original_sales_consultant_id, REASON_FIRST_MONTH
    return OWNER_RECEPTION, None, REASON_STANDARD


def apply_case_ownership(db, case_id, *, months_owing, failed_debit_date=None, transferred_by=None):
    """Set or transfer the case owner and record every change. Once a case is
    with Reception it never goes back to Sales, even if part-paid. The same
    case is handed over, so Sales history stays visible. Returns the owner type."""
    case = db.execute(
        """SELECT member_id, owner_type, owner_staff_id, original_sales_consultant_id,
                  assignment_reason, failed_debit_date FROM collections_cases WHERE id=?""",
        (case_id,),
    ).fetchone()
    if case is None:
        return None
    assigned = case["assignment_reason"] is not None
    if assigned and case["owner_type"] == OWNER_RECEPTION:
        return OWNER_RECEPTION
    consultant = case["original_sales_consultant_id"] or find_original_sales_consultant(db, case["member_id"])
    first = is_first_debit_failure(
        db, case["member_id"], failed_debit_date or case["failed_debit_date"], months_owing
    ) if (not assigned or case["owner_type"] == OWNER_SALES) else False
    owner, staff, reason = determine_owner(
        months_owing=months_owing, first_debit_failure=first,
        original_sales_consultant_id=consultant,
        transfer_months=get_collection_rule_int(db, "sales_transfer_months", 2),
    )
    if assigned and (owner, staff) == (case["owner_type"], case["owner_staff_id"]):
        return owner
    db.execute(
        """UPDATE collections_cases SET owner_type=?, owner_staff_id=?, assignment_reason=?,
           original_sales_consultant_id=?, assigned_at=datetime('now','localtime') WHERE id=?""",
        (owner, staff, reason, consultant, case_id),
    )
    db.execute(
        """INSERT INTO collection_assignment_history
           (case_id, member_id, from_owner_type, from_staff_id, to_owner_type, to_staff_id,
            reason, system_generated, transferred_by)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (case_id, case["member_id"], case["owner_type"] if assigned else None,
         case["owner_staff_id"] if assigned else None, owner, staff, reason,
         0 if transferred_by else 1, transferred_by),
    )
    return owner


def transfer_due_sales_cases(db, arrears_by_member=None):
    """Daily sweep: hand any Sales-owned case that has reached the transfer
    threshold to Reception. Returns the number transferred."""
    if arrears_by_member is None:
        from .ptp import bulk_member_arrears
        ids = [r["member_id"] for r in db.execute(
            "SELECT member_id FROM collections_cases WHERE owner_type=? AND status IN ('open','active','pending')",
            (OWNER_SALES,)).fetchall()]
        arrears_by_member = bulk_member_arrears(db, ids) if ids else {}
    moved = 0
    for case in db.execute(
        "SELECT id, member_id FROM collections_cases WHERE owner_type=? AND status IN ('open','active','pending')",
        (OWNER_SALES,),
    ).fetchall():
        months = arrears_by_member.get(case["member_id"], {}).get("months_in_arrears", 0)
        if apply_case_ownership(db, case["id"], months_owing=months) == OWNER_RECEPTION:
            moved += 1
    return moved


def determine_collection_stage(
    *, months_owing, contact_attempts=0, successful_contact=False, ptp_status=None,
    ptp_due=False, approval_pending=False, access_status=None, inactive_after_months=6,
    resolved=False,
):
    """The single place that decides which workflow stage a case is in."""
    if resolved:
        return STAGE_RESOLVED
    if int(months_owing or 0) > int(inactive_after_months):
        return STAGE_INACTIVE
    if str(access_status or "").upper() in {"BLOCKED", "RESTRICTED"}:
        return STAGE_ACCESS_RESTRICTED
    if ptp_status == "broken":
        return STAGE_PTP_BROKEN
    if approval_pending:
        return STAGE_APPROVAL_PENDING
    if ptp_status in {"pending", "partially_paid"}:
        return STAGE_PTP_DUE if ptp_due else STAGE_PTP_ACTIVE
    if int(months_owing or 0) >= 2 and successful_contact:
        return STAGE_ARRANGEMENT_REQUIRED
    if successful_contact:
        return STAGE_CONTACTED
    if contact_attempts:
        return STAGE_CONTACTING
    return STAGE_CONTACT_REQUIRED


def determine_priority(stage, months_owing):
    if stage in {STAGE_PTP_BROKEN, STAGE_ACCESS_RESTRICTED}:
        return PRIORITY_CRITICAL
    if stage in {STAGE_INACTIVE, STAGE_RESOLVED}:
        return PRIORITY_NORMAL
    if stage in {STAGE_CONTACT_REQUIRED, STAGE_ARRANGEMENT_REQUIRED, STAGE_APPROVAL_PENDING,
                 STAGE_PTP_DUE} or int(months_owing or 0) >= 2:
        return PRIORITY_HIGH
    return PRIORITY_NORMAL


def determine_next_action(stage):
    return NEXT_ACTION_BY_STAGE.get(stage, "NO_ACTION")


def refresh_case_workflow(db, case_id, *, today=None, changed_by=None, source="system", reason=None):
    """Recompute stage, priority and next action for one case from what is on
    record, and log any stage change. Returns the stage."""
    case = db.execute(
        """SELECT id, member_id, status, months_owing, access_status, collection_stage
           FROM collections_cases WHERE id=?""", (case_id,),
    ).fetchone()
    if case is None:
        return None
    today = _parse_date(today) or date.today()
    comms = db.execute(
        "SELECT outcome FROM collection_communications WHERE case_id=?", (case_id,)
    ).fetchall()
    ptp = db.execute(
        """SELECT ptp_status, promise_date FROM ptp_agreements
           WHERE member_id=? AND ptp_status IN ('pending','partially_paid','broken')
           ORDER BY id DESC LIMIT 1""", (case["member_id"],),
    ).fetchone()
    promise = _parse_date(ptp["promise_date"]) if ptp else None
    approval = db.execute(
        """SELECT 1 FROM ptp_agreements WHERE member_id=? AND manager_approval_status='pending'
           UNION ALL SELECT 1 FROM collection_exceptions WHERE case_id=? AND status='pending' LIMIT 1""",
        (case["member_id"], case_id),
    ).fetchone()
    stage = determine_collection_stage(
        months_owing=case["months_owing"],
        contact_attempts=len(comms),
        successful_contact=any(c["outcome"] in SUCCESSFUL_CALL_OUTCOMES for c in comms),
        ptp_status=ptp["ptp_status"] if ptp else None,
        ptp_due=bool(promise and promise <= today),
        approval_pending=bool(approval),
        access_status=case["access_status"],
        inactive_after_months=get_collection_rule_int(db, "inactive_after_months", 6),
        resolved=case["status"] in ("closed", "resolved"),
    )
    db.execute(
        "UPDATE collections_cases SET collection_stage=?, priority=?, next_action=? WHERE id=?",
        (stage, determine_priority(stage, case["months_owing"]), determine_next_action(stage), case_id),
    )
    if stage != case["collection_stage"]:
        db.execute(
            """INSERT INTO collection_stage_history
               (case_id, member_id, from_stage, to_stage, reason, changed_by, source)
               VALUES (?,?,?,?,?,?,?)""",
            (case_id, case["member_id"], case["collection_stage"], stage, reason, changed_by, source),
        )
    return stage


def log_collection_communication(
    db,
    member_id,
    channel,
    *,
    case_id=None,
    task_id=None,
    status="completed",
    outcome=None,
    notes=None,
    created_by=None,
):
    cursor = db.execute(
        """INSERT INTO collection_communications
           (case_id, member_id, task_id, channel, status, outcome, notes, sent_at, created_by)
           VALUES (?,?,?,?,?,?,?,datetime('now','localtime'),?)""",
        (case_id, member_id, task_id, channel.upper(), status, outcome, notes, created_by),
    )
    if case_id:
        refresh_case_workflow(db, case_id, changed_by=created_by, reason=f"{channel.upper()} contact logged")
    return cursor.lastrowid


def record_access_decision(db, member_id, access_status, reason, *, case_id=None, source="collections_engine"):
    db.execute(
        """INSERT INTO access_decisions
           (case_id, member_id, access_status, reason, source)
           VALUES (?,?,?,?,?)""",
        (case_id, member_id, access_status.upper(), reason, source),
    )


def create_collection_exception(
    db, member_id, reason, *, case_id=None, ptp_id=None, requested_action=None, notes=None, created_by=None
):
    cursor = db.execute(
        """INSERT INTO collection_exceptions
           (case_id, ptp_id, member_id, reason, requested_action, notes, created_by)
           VALUES (?,?,?,?,?,?,?)""",
        (case_id, ptp_id, member_id, reason, requested_action, notes, created_by),
    )
    return cursor.lastrowid


# ── Manager decisions on Rule §7 exceptions ──────────────────────────────────
#
# One escalated request produces two queue cards: the arrangement awaiting
# approval and the exception. ``ptp_id`` ties them together so a decision on
# either settles both, and it is how an approved exception knows how long it
# lasts - to the promise date of the arrangement it was raised for.

def decide_collection_exception(db, exception_id, decision, decided_by, notes=None):
    """Record a manager decision on a pending exception and settle its arrangement.

    Returns the exception row as it was, or None if it was not pending.
    """
    row = db.execute(
        "SELECT * FROM collection_exceptions WHERE id = ? AND status = 'pending'",
        (exception_id,),
    ).fetchone()
    if row is None:
        return None
    db.execute(
        """UPDATE collection_exceptions
           SET status = ?, notes = ?, decided_by = ?, decided_at = datetime('now','localtime')
           WHERE id = ?""",
        (decision, notes or row["notes"], decided_by, exception_id),
    )
    if row["ptp_id"]:
        db.execute(
            """UPDATE ptp_agreements
               SET manager_approval_status = ?, approved_by = ?,
                   approved_date = datetime('now','localtime'), approval_notes = ?,
                   updated_at = datetime('now','localtime')
               WHERE id = ? AND manager_approval_status = 'pending'""",
            (decision, decided_by, notes or "", row["ptp_id"]),
        )
    return row


def settle_exception_for_arrangement(db, ptp_id, decision, decided_by, notes=None):
    """The other direction: a manager decided the arrangement card, so the
    exception raised for it is decided the same way."""
    db.execute(
        """UPDATE collection_exceptions
           SET status = ?, notes = COALESCE(NULLIF(?, ''), notes), decided_by = ?,
               decided_at = datetime('now','localtime')
           WHERE ptp_id = ? AND status = 'pending'""",
        (decision, notes or "", decided_by, ptp_id),
    )


def apply_manager_exception(db, member_id, decision, *, today=None):
    """Lift the Rule §7 block while a manager-approved exception is live.

    Only THREE_PLUS_NO_DEBICHECK is lifted - an inactive account or a
    two-month restriction is not what these exceptions are raised for. The
    exception lasts through the promise date of its arrangement and ends early
    if that arrangement is no longer pending or partially paid. Every access
    decision (the live check, the sweep) goes through this so they agree.
    """
    if decision.get("status") != "THREE_PLUS_NO_DEBICHECK":
        return decision
    on = (_parse_date(today) or date.today()).isoformat()
    grant = db.execute(
        """SELECT p.promise_date
           FROM collection_exceptions e
           JOIN ptp_agreements p ON p.id = e.ptp_id
           WHERE e.member_id = ? AND e.status = 'approved'
             AND p.ptp_status IN ('pending', 'partially_paid')
             AND p.promise_date >= ?
           ORDER BY p.promise_date DESC LIMIT 1""",
        (member_id, on),
    ).fetchone()
    if grant is None:
        return decision
    return {
        **decision,
        "status": "THREE_PLUS_MANAGER_EXCEPTION",
        "access": "ALLOWED",
        "action": "MANAGER_EXCEPTION_ACTIVE",
        "manager_exception_until": grant["promise_date"],
    }


def grant_exception_access(db, member_id, *, today=None):
    """Bring the stored access status in line once an exception is approved.

    The turnstile also refuses a stored 'blocked', so the live decision alone is
    not enough. The same temporary-unblock the sweep already expires is used, set
    to the promise date. Does nothing unless the live decision now says an
    exception is active. Returns whether access was granted.
    """
    decision = evaluate_member_access(db, member_id, today=today)
    if decision.get("status") != "THREE_PLUS_MANAGER_EXCEPTION":
        return False
    db.execute(
        """UPDATE members SET gym_access_status = 'temp_unblocked',
           access_blocked_until = ?, access_block_reason = NULL WHERE id = ?""",
        (decision["manager_exception_until"], member_id),
    )
    record_access_decision(db, member_id, "ALLOWED", decision["action"], source="manager_exception")
    return True
