"""Shared collections policy, workflow, and audit helpers.

The route modules remain responsible for HTTP concerns. This module owns the
business decisions that must be consistent across Collections, PTP, DebiCheck,
and access control.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")
DEFAULT_COLLECTION_RULES = {
    "daily_call_target": ("40", "integer"),
    "upfront_min_percent": ("30", "decimal"),
    "upfront_max_percent": ("40", "decimal"),
    "minimum_arrears_installment": ("30.00", "decimal"),
    "inactive_after_months": ("6", "integer"),
    "reminder_offset_days": ("2", "integer"),
}

COMMUNICATION_ORDER = ("CALL", "WHATSAPP", "SMS", "EMAIL")
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
    min_upfront = _decimal_rule(rules, "upfront_min_percent", "30")
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
    required_upfront = required_upfront_amount(total, _decimal_rule(rules, "upfront_min_percent", "30"))
    decision = evaluate_collection_case(
        months, total, debicheck_active,
        upfront_paid=verified_paid >= required_upfront if months == 2 else False,
        upfront_amount=verified_paid,
        full_settlement=total > 0 and verified_paid >= money(total),
        rules=rules, oldest_arrears_month=oldest, today=today,
    )
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
            id {id_type}, case_id INTEGER, member_id INTEGER NOT NULL, reason TEXT NOT NULL,
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
        "CREATE INDEX IF NOT EXISTS idx_collections_cases_member_status ON collections_cases(member_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_collection_communications_member ON collection_communications(member_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_collection_exceptions_status ON collection_exceptions(status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_access_decisions_member ON access_decisions(member_id, evaluated_at)",
    ]


def ensure_collections_engine_schema(db, backend="sqlite"):
    for statement in _engine_table_statements(backend):
        db.execute(statement)
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
        return existing["id"]
    cursor = db.execute(
        """INSERT INTO collections_cases
           (member_id, collection_id, failed_debit_date, failure_reason, arrears_amount,
            months_owing, monthly_installment, debicheck_status, access_status, assigned_to)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (member_id, *values),
    )
    return cursor.lastrowid


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
    return cursor.lastrowid


def record_access_decision(db, member_id, access_status, reason, *, case_id=None, source="collections_engine"):
    db.execute(
        """INSERT INTO access_decisions
           (case_id, member_id, access_status, reason, source)
           VALUES (?,?,?,?,?)""",
        (case_id, member_id, access_status.upper(), reason, source),
    )


def create_collection_exception(db, member_id, reason, *, case_id=None, requested_action=None, notes=None, created_by=None):
    cursor = db.execute(
        """INSERT INTO collection_exceptions
           (case_id, member_id, reason, requested_action, notes, created_by)
           VALUES (?,?,?,?,?,?)""",
        (case_id, member_id, reason, requested_action, notes, created_by),
    )
    return cursor.lastrowid
