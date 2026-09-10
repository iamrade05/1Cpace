from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .auth import permission_required
from .database import audit_log, current_tenant_name, get_db, next_member_ref
from .encryption import encrypt_member, hash_for_lookup


def sa_id_birth_date(id_number, today=None):
    """Extract date of birth from a 13-digit SA ID number.
    Returns None if the ID is malformed or the date is impossible."""
    today = today or date.today()
    digits = "".join(ch for ch in (id_number or "") if ch.isdigit())
    if len(digits) != 13:
        return None
    yy, mm, dd = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    for century in (2000, 1900):
        try:
            candidate = date(century + yy, mm, dd)
        except ValueError:
            continue
        if candidate <= today and (today - candidate).days // 365 <= 110:
            return candidate
    return None


def sa_id_luhn_valid(id_number):
    """SA ID check-digit validation. Catches transposed/mistyped digits."""
    digits = [int(c) for c in "".join(ch for ch in (id_number or "") if ch.isdigit())]
    if len(digits) != 13:
        return False
    total, parity = 0, len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _age_on(dob, today=None):
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


leads_bp = Blueprint("leads", __name__, url_prefix="/leads")

LEAD_SOURCES = ["Walk-In / Enquiry", "Referral", "Outreach", "Social Media", "Website"]
LEAD_CAMPAIGNS = [
    "",
    "Referral Drive",
    "Open Day",
    "Corporate Wellness",
    "Seasonal Promotion",
]

# Sources that skip the call/appointment pipeline entirely because the
# prospect is (or was) already physically in front of a consultant.
DIRECT_SHOW_SOURCES = {"Walk-In / Enquiry"}

# A lifecycle stage describes where the prospect is now. Individual calls,
# appointments and outcomes are stored separately in lead_activities.
LEAD_STATUSES = [
    ("captured", "Captured"),
    ("assigned", "Assigned"),
    ("contacted", "Contacted"),
    ("recall_queue", "Recall Queue"),
    ("appointment_booked", "Appointment Booked"),
    ("show", "Show"),
    ("hot_prospect_7day", "Decision Follow-Up – 5 Working Days"),
    ("joined", "Joined"),
    ("recycle", "Recycle"),
    ("do_not_contact", "Do Not Contact"),
    ("duplicate", "Duplicate"),
    ("existing_member", "Existing Member"),
]

ACTIVE_STATUSES = {
    "captured", "assigned", "contacted", "recall_queue",
    "appointment_booked", "show", "hot_prospect_7day",
}
FINAL_STATUSES = {
    "joined", "recycle", "do_not_contact", "duplicate", "existing_member"
}

STATUS_COLORS = {
    "captured": "#3b82f6",
    "assigned": "#6366f1",
    "contacted": "#8b5cf6",
    "recall_queue": "#f97316",
    "appointment_booked": "#f59e0b",
    "show": "#0ea5e9",
    "hot_prospect_7day": "#10b981",
    "joined": "#059669",
    "recycle": "#64748b",
    "do_not_contact": "#991b1b",
    "duplicate": "#9ca3af",
    "existing_member": "#334155",
}

KANBAN_COLUMNS = [
    ("captured", "Captured"),
    ("assigned", "Assigned"),
    ("contacted", "Contacted"),
    ("recall_queue", "Recall Queue"),
    ("appointment_booked", "Appointment"),
    ("show", "Show"),
    ("hot_prospect_7day", "Decision Follow-Up – 5 Working Days"),
    ("joined", "Joined"),
]

# Structured "why did this prospect get recycled" taxonomy — kept separate
# from the free-text closure/objection reasons so campaign lists can filter
# on it reliably later (e.g. "everyone who showed but didn't join").
RECYCLE_REASONS = [
    ("not_interested_call", "Not interested after call"),
    ("invalid_number", "Invalid / non-existent number"),
    ("appointment_cancelled", "Appointment cancelled"),
    ("showed_not_interested", "Showed but did not join"),
    ("seven_day_no_conversion", "5-working-day follow-up did not convert"),
    ("unreachable_after_attempts", "Unreachable after multiple attempts"),
]
# System-only values, never offered in the manual picker — set only by the
# one-time lifecycle migration for leads that were already closed before it.
LEGACY_RECYCLE_REASONS = [
    ("legacy_not_interested", "Not interested (legacy)"),
    ("legacy_unreachable", "Unreachable (legacy)"),
]

# Recycle means "not ready now", not permanently lost. These prospects return
# to Recall Queue automatically after the cooling-off period. An invalid number
# cannot be recalled until its contact details are corrected.
RECYCLE_RETURN_DAYS = {
    "not_interested_call": 30,
    "appointment_cancelled": 7,
    "showed_not_interested": 30,
    "seven_day_no_conversion": 30,
    "unreachable_after_attempts": 14,
    "legacy_not_interested": 30,
    "legacy_unreachable": 14,
}

ACTIVITY_TYPES = [
    ("call", "Call"),
    ("whatsapp", "WhatsApp"),
    ("email", "Email"),
    ("follow_up", "Follow-up Task"),
    ("appointment", "Appointment"),
    ("visit", "Gym Visit"),
    ("consultation", "Consultation"),
    ("decision", "5-Working-Day Decision"),
    ("note", "Note"),
]

ACTIVITY_OUTCOMES = {
    "call": [
        ("appointment_booked", "Client made an appointment"),
        ("follow_up_required", "Schedule a follow-up"),
        ("not_interested", "Not interested"),
        ("no_answer", "No answer"),
        ("voicemail", "Voicemail"),
        ("call_dropped", "Call dropped"),
        ("no_response", "No response"),
        ("does_not_exist", "Number does not exist"),
        ("unreachable", "Confirmed unreachable"),
    ],
    "whatsapp": [
        ("appointment_booked", "Client made an appointment"),
        ("follow_up_required", "Schedule a follow-up"),
        ("not_interested", "Not interested"),
        ("no_response", "No response"),
    ],
    "email": [
        ("appointment_booked", "Client made an appointment"),
        ("follow_up_required", "Schedule a follow-up"),
        ("not_interested", "Not interested"),
        ("no_response", "No response"),
    ],
    "follow_up": [("scheduled", "Scheduled"), ("completed", "Completed")],
    "appointment": [
        ("scheduled", "Scheduled"),
        ("confirmed", "Confirmed"),
        ("rescheduled", "Rescheduled"),
        ("cancelled", "Cancelled"),
        ("no_show", "No-show"),
        ("showed", "Showed"),
        ("joined", "Joined"),
        ("not_interested", "Not interested"),
    ],
    "visit": [
        ("arrived", "Walk-in arrived"),
        ("completed", "Visit completed"),
        ("joined", "Joined"),
        ("not_interested", "Not interested"),
    ],
    "consultation": [
        ("interested", "Interested"),
        ("decision_required", "Needs time to decide"),
        ("not_interested", "Not interested"),
    ],
    "decision": [("started", "Decision period started"), ("joined", "Joined"), ("declined", "Declined")],
    "note": [("recorded", "Recorded")],
}

CONTACT_ACTIVITY_TYPES = {"call", "whatsapp", "email"}
MARKETING_ACTIVITY_TYPES = CONTACT_ACTIVITY_TYPES | {"follow_up", "appointment"}


def _contact_methods_for_source(source: str) -> list[tuple[str, str]]:
    """Restrict contact methods using the immutable original lead source."""
    if source == "Referral":
        return [("call", "Call"), ("whatsapp", "WhatsApp"), ("email", "Email")]
    if source in {"Outreach", "Social Media", "Website"}:
        return [("call", "Call"), ("whatsapp", "WhatsApp"), ("email", "Email")]
    return []


def _add_workdays(start: date, workdays: int) -> date:
    """Add weekdays, excluding Saturday and Sunday."""
    target = start
    added = 0
    while added < workdays:
        target += timedelta(days=1)
        if target.weekday() < 5:
            added += 1
    return target


def normalise_phone(value: str | None) -> str:
    """Normalise formatting differences before prospect duplicate checks."""
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if digits.startswith("0027"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        return "27" + digits[1:]
    return digits


def _clean_datetime(value: str | None) -> str:
    return (value or "").strip().replace("T", " ")


def _next_work_time(days: int = 1) -> str:
    """Return 09:00 on the next suitable weekday."""
    target = date.today() + timedelta(days=days)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return datetime.combine(target, time(hour=9)).isoformat(sep=" ")


def _today_contact_time() -> str:
    """Place newly captured contact work in today's queue immediately."""
    return datetime.now().isoformat(timespec="seconds", sep=" ")


def _automatic_next_action(stage: str, outcome: str = "") -> tuple[str, str]:
    """Choose routine sales work automatically instead of requiring free text."""
    if stage in FINAL_STATUSES:
        return "", ""
    if outcome in {"no_answer", "voicemail", "call_dropped"}:
        return "Retry phone contact", _next_work_time()
    defaults = {
        "captured": "Initial contact call",
        "assigned": "Initial contact call",
        "contacted": "Book appointment",
        "recall_queue": "Retry phone contact",
        "appointment_booked": "Confirm and complete appointment",
        "show": "Complete consultation",
        "hot_prospect_7day": "Decision follow-up",
    }
    when = _today_contact_time() if stage in {"captured", "assigned"} else _next_work_time()
    return defaults.get(stage, "Follow up with prospect"), when


def _capture_context(db) -> tuple[object | None, str, list[str]]:
    """Resolve which source choices the signed-in staff member may capture."""
    user = db.execute(
        "SELECT * FROM users WHERE id=? AND active=1", (session.get("user_id"),)
    ).fetchone()
    department = str(user["department"] or "").strip().lower() if user else ""
    role = str(user["role"] or "").strip().lower() if user else ""
    if role != "admin" and department == "reception":
        return user, "reception", ["Walk-In / Enquiry"]
    if role != "admin" and (department == "sales" or (user and user["is_sales_consultant"])):
        return user, "sales", ["Referral", "Outreach", "Social Media"]
    return user, "oversight", list(LEAD_SOURCES)


def _allocate_sales_consultant(db, capture_user, source: str):
    """Assign a lead, consuming any temporary Category 1/2 skipped turns."""
    if (
        source in {"Referral", "Outreach", "Social Media", "Website"}
        and capture_user
        and capture_user["is_sales_consultant"]
    ):
        return capture_user

    consultants = db.execute(
        """SELECT * FROM users
           WHERE active=1 AND COALESCE(is_platform_user, 0)=0
             AND is_sales_consultant=1 AND sales_available=1
           ORDER BY CASE WHEN last_lead_assigned_at IS NULL THEN 0 ELSE 1 END,
                    last_lead_assigned_at,
                    sales_rotation_order,
                    id
           """
    ).fetchall()
    skipped_at = datetime.now().isoformat(timespec="microseconds", sep=" ")
    for consultant in consultants:
        if (consultant["sales_skip_remaining"] or 0) > 0:
            db.execute(
                """UPDATE users
                   SET sales_skip_remaining=sales_skip_remaining-1,
                       last_lead_assigned_at=?
                   WHERE id=? AND sales_skip_remaining>0""",
                (skipped_at, consultant["id"]),
            )
            continue
        return consultant
    return None


# An application still needs work until it activates or is turned away.
OPEN_APPLICATION_STATUSES_EXCLUDED = {"active", "declined"}


def _journey_step(db, lead) -> str:
    """Choose the single guided activity card appropriate to this prospect."""
    application = db.execute(
        """SELECT application_status FROM membership_applications
           WHERE lead_id=? ORDER BY id DESC LIMIT 1""",
        (lead["id"],),
    ).fetchone()
    application_open = bool(application) and str(
        application["application_status"] or ""
    ).lower() not in OPEN_APPLICATION_STATUSES_EXCLUDED
    # Conversion marks the lead joined and opens the application in the same
    # step, so 'joined' on its own must not close the journey — finishing that
    # application is precisely the work still outstanding.
    if application_open:
        return "membership"
    if lead["lead_status"] in FINAL_STATUSES:
        return "closed"
    if lead["entry_path"] == "direct_show" and lead["lead_status"] == "show":
        return "walk_in"
    latest_appointment = db.execute(
        """SELECT outcome FROM lead_activities
           WHERE lead_id=? AND activity_type='appointment'
           ORDER BY occurred_at DESC, id DESC LIMIT 1""",
        (lead["id"],),
    ).fetchone()
    if lead["lead_status"] == "show":
        return "membership_decision"
    if lead["lead_status"] == "appointment_booked":
        return "appointment_result"
    if lead["lead_status"] == "recall_queue" and latest_appointment and latest_appointment["outcome"] == "no_show":
        return "appointment_follow_up"
    return "contact"


def _find_duplicate(db, phone: str, exclude_id: int | None = None):
    normalised = normalise_phone(phone)
    if not normalised:
        return None
    query = "SELECT id, full_name, lead_status FROM leads WHERE phone_normalized = ?"
    params: list[object] = [normalised]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    query += " AND lead_status != 'duplicate' ORDER BY id LIMIT 1"
    return db.execute(query, tuple(params)).fetchone()


def _add_activity(
    db,
    lead_id: int,
    activity_type: str,
    *,
    outcome: str = "",
    status: str = "completed",
    notes: str = "",
    occurred_at: str = "",
    scheduled_for: str = "",
    next_action: str = "",
    follow_up_at: str = "",
    reason: str = "",
    offer_amount: float | None = None,
    discount_percent: float = 0,
    approval_status: str = "not_required",
):
    occurred_at = occurred_at or datetime.now().isoformat(timespec="seconds", sep=" ")
    return db.execute(
        """INSERT INTO lead_activities
           (lead_id, activity_type, outcome, status, notes, occurred_at,
            scheduled_for, completed_at, next_action, follow_up_at, reason,
            offer_amount, discount_percent, manager_approval_status, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            lead_id, activity_type, outcome or None, status, notes or None, occurred_at,
            scheduled_for or None, occurred_at if status == "completed" else None,
            next_action or None, follow_up_at or None, reason or None,
            offer_amount, discount_percent, approval_status, session.get("user_id"),
        ),
    )


def _expire_decisions(db) -> None:
    """Move overdue 7-day decision periods to Recycle so they cannot remain forever."""
    expired = db.execute(
        """SELECT id FROM leads
           WHERE lead_status = 'hot_prospect_7day' AND decision_date IS NOT NULL
             AND decision_date < date('now')"""
    ).fetchall()
    for row in expired:
        db.execute(
            """UPDATE leads SET lead_status='recycle',
                   recycle_reason='seven_day_no_conversion',
                   closure_reason='Seven-day decision period expired',
                   recycle_due_at=datetime('now', '+30 days'),
                   next_action=NULL, next_action_at=NULL,
                   updated_at=datetime('now') WHERE id=?""",
            (row["id"],),
        )
        from .sales_kpi import record_lead_status_change
        record_lead_status_change(db, row["id"], "hot_prospect_7day", "recycle")
        _add_activity(
            db, row["id"], "status_change", outcome="recycle",
            notes="Decision period expired without a final decision.",
        )
    if expired:
        db.commit()


def _recycle_due_at(reason: str, start: datetime | None = None) -> str | None:
    days = RECYCLE_RETURN_DAYS.get(reason)
    if days is None:
        return None
    return ((start or datetime.now()) + timedelta(days=days)).isoformat(
        timespec="seconds", sep=" "
    )


def _reactivate_due_recycles(db) -> None:
    """Schedule legacy recycle rows, then return due prospects to Recall Queue."""
    unscheduled = db.execute(
        """SELECT id, recycle_reason, updated_at FROM leads
           WHERE lead_status='recycle' AND recycle_due_at IS NULL"""
    ).fetchall()
    for lead in unscheduled:
        start = None
        try:
            start = datetime.fromisoformat(str(lead["updated_at"]))
        except (TypeError, ValueError):
            pass
        due_at = _recycle_due_at(lead["recycle_reason"] or "", start)
        if due_at:
            db.execute(
                "UPDATE leads SET recycle_due_at=? WHERE id=?", (due_at, lead["id"])
            )

    due = db.execute(
        """SELECT id FROM leads
           WHERE lead_status='recycle' AND recycle_due_at IS NOT NULL
             AND recycle_due_at <= datetime('now', 'localtime')"""
    ).fetchall()
    for lead in due:
        follow_up_at = _today_contact_time()
        db.execute(
            """UPDATE leads SET lead_status='recall_queue',
                   next_action='Recall recycled prospect', next_action_at=?,
                   closure_reason=NULL, recycle_due_at=NULL,
                   updated_at=datetime('now') WHERE id=?""",
            (follow_up_at, lead["id"]),
        )
        from .sales_kpi import record_lead_status_change
        record_lead_status_change(db, lead["id"], "recycle", "recall_queue")
        _add_activity(
            db, lead["id"], "status_change", outcome="recall_queue",
            notes="Automatically returned from Recycle to Recall Queue.",
            next_action="Recall recycled prospect", follow_up_at=follow_up_at,
        )
        _add_activity(
            db, lead["id"], "follow_up", outcome="scheduled", status="scheduled",
            notes="Automatic recall after recycle cooling-off period.",
            scheduled_for=follow_up_at,
        )
    if unscheduled or due:
        db.commit()


def _today_pipeline_metrics(db) -> dict[str, int]:
    """Return today's client movement, counting each prospect once per metric."""
    activity_counts = db.execute(
        """SELECT
             COUNT(DISTINCT CASE WHEN activity_type IN ('call','whatsapp','email')
                                  THEN lead_id END) AS calls,
             COUNT(DISTINCT CASE WHEN (activity_type = 'appointment' AND outcome = 'scheduled')
                                      OR (activity_type IN ('call','whatsapp','email')
                                          AND outcome = 'appointment_booked')
                                 THEN lead_id END) AS appointments,
             COUNT(DISTINCT CASE WHEN activity_type = 'appointment'
                                      AND outcome = 'rescheduled'
                                 THEN lead_id END) AS rescheduled,
             COUNT(DISTINCT CASE WHEN activity_type = 'visit'
                                      OR (activity_type = 'appointment' AND outcome IN ('showed','joined'))
                                 THEN lead_id END) AS visits
           FROM lead_activities
           WHERE date(occurred_at) = date('now', 'localtime')"""
    ).fetchone()
    captured = db.execute(
        """SELECT COUNT(*) FROM leads
           WHERE date(created_at, 'localtime') = date('now', 'localtime')"""
    ).fetchone()[0]
    return {
        "captured": captured,
        "calls": activity_counts["calls"] or 0,
        "appointments": activity_counts["appointments"] or 0,
        "rescheduled": activity_counts["rescheduled"] or 0,
        "visits": activity_counts["visits"] or 0,
    }


@leads_bp.route("/")
@permission_required("view_leads")
def leads_index():
    db = get_db()
    _expire_decisions(db)
    _reactivate_due_recycles(db)
    today_metrics = _today_pipeline_metrics(db)
    available_consultants = db.execute(
        """SELECT id, full_name, sales_rotation_order FROM users
           WHERE active=1 AND is_sales_consultant=1 AND sales_available=1
             AND COALESCE(is_platform_user, 0)=0
           ORDER BY sales_rotation_order, id"""
    ).fetchall()

    restrict_to_own = bool(session.get("is_sales_consultant"))
    own_id = session.get("user_id")

    leads_by_status = {}
    for status_key, _ in KANBAN_COLUMNS:
        sql = """SELECT l.*, u.full_name AS salesperson_name
               FROM leads l LEFT JOIN users u ON l.assigned_to = u.id
               WHERE l.lead_status = ?"""
        params = [status_key]
        if restrict_to_own:
            sql += " AND l.assigned_to = ?"
            params.append(own_id)
        sql += " ORDER BY l.next_action_at, l.updated_at DESC"
        leads_by_status[status_key] = db.execute(sql, params).fetchall()

    expiring_sql = """SELECT l.*, u.full_name AS salesperson_name
           FROM leads l LEFT JOIN users u ON l.assigned_to = u.id
           WHERE l.lead_status = 'hot_prospect_7day'
             AND l.decision_date <= date('now', '+1 day')"""
    expiring_params = []
    if restrict_to_own:
        expiring_sql += " AND l.assigned_to = ?"
        expiring_params.append(own_id)
    expiring_sql += " ORDER BY l.decision_date"
    expiring_soon = db.execute(expiring_sql, expiring_params).fetchall()

    count_scope_sql = " AND assigned_to = ?" if restrict_to_own else ""
    count_scope_params = [own_id] if restrict_to_own else []

    total = db.execute(
        "SELECT COUNT(*) FROM leads WHERE 1=1" + count_scope_sql, count_scope_params
    ).fetchone()[0]
    active = db.execute(
        "SELECT COUNT(*) FROM leads WHERE lead_status NOT IN ('joined','recycle','do_not_contact','duplicate','existing_member')"
        + count_scope_sql,
        count_scope_params,
    ).fetchone()[0]
    joined_month = db.execute(
        """SELECT COUNT(*) FROM leads WHERE lead_status = 'joined'
           AND strftime('%Y-%m', updated_at) = strftime('%Y-%m', 'now')"""
        + count_scope_sql,
        count_scope_params,
    ).fetchone()[0]
    due_followups_sql = """SELECT COUNT(*) FROM lead_activities la
           JOIN leads l ON la.lead_id = l.id
           WHERE la.activity_type='follow_up' AND la.status='scheduled'
             AND la.scheduled_for <= datetime('now')"""
    due_followups_params = []
    if restrict_to_own:
        due_followups_sql += " AND l.assigned_to = ?"
        due_followups_params.append(own_id)
    due_followups = db.execute(due_followups_sql, due_followups_params).fetchone()[0]
    in_recall_queue = db.execute(
        "SELECT COUNT(*) FROM leads WHERE lead_status = 'recall_queue'" + count_scope_sql,
        count_scope_params,
    ).fetchone()[0]
    closed = db.execute(
        "SELECT COUNT(*) FROM leads WHERE lead_status IN ('recycle','do_not_contact','duplicate','existing_member')"
        + count_scope_sql,
        count_scope_params,
    ).fetchone()[0]
    closed_sql = """SELECT l.*, u.full_name AS salesperson_name
           FROM leads l LEFT JOIN users u ON l.assigned_to = u.id
               WHERE l.lead_status IN ('recycle','do_not_contact','duplicate','existing_member')"""
    closed_params = []
    if restrict_to_own:
        closed_sql += " AND l.assigned_to = ?"
        closed_params.append(own_id)
    closed_sql += " ORDER BY l.updated_at DESC LIMIT 50"
    closed_prospects = db.execute(closed_sql, closed_params).fetchall()

    return render_template(
        "leads/index.html",
        today_metrics=today_metrics,
        today_label=date.today().strftime("%d %b %Y"),
        available_consultants=available_consultants,
        leads_by_status=leads_by_status,
        kanban_columns=KANBAN_COLUMNS,
        status_colors=STATUS_COLORS,
        expiring_soon=expiring_soon,
        total=total,
        active=active,
        joined_month=joined_month,
        due_followups=due_followups,
        in_recall_queue=in_recall_queue,
        closed=closed,
        closed_prospects=closed_prospects,
        recycle_reason_labels=dict(RECYCLE_REASONS + LEGACY_RECYCLE_REASONS),
        join_links={
            channel: url_for(
                "join.public_join", tenant_slug=g.get("tenant_slug") or "elev8", channel=channel
            )
            for channel in ("facebook", "instagram", "tiktok", "website")
        },
    )


@leads_bp.route("/add", methods=["GET", "POST"])
@permission_required("capture_leads")
def leads_add():
    db = get_db()
    capture_user, capture_mode, allowed_sources = _capture_context(db)

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        source = request.form.get("source", "Walk-In / Enquiry").strip()
        referrer = request.form.get("referrer", "").strip()
        campaign = request.form.get("campaign", "").strip()
        notes = request.form.get("notes", "").strip()
        marketing_consent = 1 if request.form.get("marketing_consent") == "1" else 0
        do_not_contact = 1 if request.form.get("do_not_contact") == "1" else 0
        duplicate = _find_duplicate(db, phone)

        if not full_name:
            flash("Full name is required.", "error")
        elif not phone and not email:
            flash("At least a phone number or email is required.", "error")
        elif source not in allowed_sources:
            flash("That lead source is not available for your capture role.", "error")
        elif duplicate:
            flash(
                f"That phone number already belongs to prospect #{duplicate['id']} — {duplicate['full_name']}. Open the existing prospect and add another activity instead.",
                "error",
            )
        else:
            try:
                lead_channel = "outreach" if source in {"Outreach", "Social Media", "Website"} else "in_reach"
                direct_show = source in DIRECT_SHOW_SOURCES
                entry_path = "direct_show" if direct_show else "standard"
                consultant = _allocate_sales_consultant(db, capture_user, source)
                if consultant is None:
                    flash(
                        "No sales consultant is currently available. Mark at least one "
                        "sales consultant as available in User Management.",
                        "error",
                    )
                    return render_template(
                        "leads/add.html", sources=allowed_sources,
                        campaigns=LEAD_CAMPAIGNS, capture_mode=capture_mode,
                    )
                assigned_to = consultant["id"]

                if do_not_contact:
                    lead_status = "do_not_contact"
                elif direct_show:
                    lead_status = "show"
                else:
                    lead_status = "assigned"

                next_action, next_action_at = _automatic_next_action(lead_status)
                if direct_show:
                    next_action, next_action_at = "Record walk-in outcome", _today_contact_time()
                cur = db.execute(
                    """INSERT INTO leads
                       (full_name, phone, phone_normalized, email, source, original_source,
                        referrer, campaign, assigned_to, lead_status, interested_package,
                        next_action, next_action_at, marketing_consent, do_not_contact,
                        closure_reason, notes, created_by, last_activity_at,
                        lead_channel, entry_path, outreach_needs_appointment)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?)""",
                    (
                        full_name, phone, normalise_phone(phone), email, source, source,
                        referrer, campaign, assigned_to,
                        lead_status, None, next_action or None,
                        next_action_at or None, 0 if do_not_contact else marketing_consent,
                        do_not_contact, "Do not contact requested" if do_not_contact else None,
                        notes, session.get("user_id"),
                        lead_channel, entry_path, None,
                    ),
                )
                db.execute(
                    "UPDATE users SET last_lead_assigned_at=? WHERE id=?",
                    (datetime.now().isoformat(timespec="microseconds", sep=" "), assigned_to),
                )
                lid = cur.lastrowid
                from .sales_kpi import record_lead_status_change
                record_lead_status_change(
                    db, lid, None, lead_status, actor_id=session.get("user_id")
                )
                _add_activity(
                    db, lid, "prospect_created", outcome=lead_status, notes=notes,
                    next_action=next_action, follow_up_at=next_action_at,
                )
                if direct_show:
                    _add_activity(
                        db, lid, "visit", outcome="arrived",
                        notes="Reception captured the client as a walk-in.",
                    )
                elif next_action_at and not do_not_contact:
                    _add_activity(
                        db, lid, "follow_up", outcome="scheduled", status="scheduled",
                        notes=next_action, scheduled_for=next_action_at,
                    )
                audit_log(db, "prospect_created", lead_id=lid, new_value=full_name)
                db.commit()
                if direct_show:
                    flash(
                        f"Walk-in '{full_name}' assigned to {consultant['full_name']} — "
                        "record the walk-in outcome below.", "success",
                    )
                else:
                    flash(
                        f"Prospect '{full_name}' assigned to {consultant['full_name']} "
                        "for contact today.", "success",
                    )
                return redirect(url_for("leads.lead_detail", lid=lid))
            except Exception as exc:
                db.rollback()
                flash(f"Error: {exc}", "error")

    return render_template(
        "leads/add.html", sources=allowed_sources, campaigns=LEAD_CAMPAIGNS,
        capture_mode=capture_mode,
    )


@leads_bp.route("/<int:lid>")
@permission_required("view_leads")
def lead_detail(lid: int):
    db = get_db()
    _expire_decisions(db)
    lead = db.execute(
        """SELECT l.*, u.full_name AS salesperson_name,
                  dupe.full_name AS duplicate_name
           FROM leads l
           LEFT JOIN users u ON l.assigned_to = u.id
           LEFT JOIN leads dupe ON l.duplicate_of_lead_id = dupe.id
           WHERE l.id = ?""",
        (lid,),
    ).fetchone()
    if lead is None:
        flash("Prospect not found.", "warning")
        return redirect(url_for("leads.leads_index"))

    application = db.execute(
        "SELECT * FROM membership_applications WHERE lead_id = ? ORDER BY id DESC LIMIT 1", (lid,)
    ).fetchone()
    activities = db.execute(
        """SELECT la.*, u.full_name AS user_name,
                  approver.full_name AS approver_name
           FROM lead_activities la
           LEFT JOIN users u ON la.created_by = u.id
           LEFT JOIN users approver ON la.manager_approved_by = approver.id
           WHERE la.lead_id = ? ORDER BY la.occurred_at DESC, la.id DESC""",
        (lid,),
    ).fetchall()
    consultants = db.execute(
        "SELECT id, full_name FROM users WHERE active=1 ORDER BY full_name"
    ).fetchall()
    from .sales_pipeline import pipeline_context
    sales_pipeline = pipeline_context(db, lid)
    journey_step = _journey_step(db, lead)

    decision_days_remaining = None
    if lead["decision_date"]:
        try:
            decision_days_remaining = (date.fromisoformat(str(lead["decision_date"])[:10]) - date.today()).days
        except (TypeError, ValueError):
            pass

    return render_template(
        "leads/detail.html",
        lead=lead,
        application=application,
        journey_step=journey_step,
        activities=activities,
        statuses=LEAD_STATUSES,
        status_colors=STATUS_COLORS,
        status_labels=dict(LEAD_STATUSES),
        consultants=consultants,
        activity_types=ACTIVITY_TYPES,
        activity_outcomes=ACTIVITY_OUTCOMES,
        contact_methods=_contact_methods_for_source(lead["original_source"] or lead["source"]),
        active_statuses=ACTIVE_STATUSES,
        decision_days_remaining=decision_days_remaining,
        contact_now=datetime.now().strftime("%d %b %Y %H:%M"),
        today=date.today().isoformat(),
        recycle_reasons=RECYCLE_REASONS,
        recycle_reason_labels=dict(RECYCLE_REASONS + LEGACY_RECYCLE_REASONS),
        five_workday_default=_add_workdays(date.today(), 5).isoformat(),
        sales_pipeline=sales_pipeline,
    )


@leads_bp.route("/<int:lid>/edit", methods=["GET", "POST"])
@permission_required("capture_leads")
def lead_edit(lid: int):
    db = get_db()
    lead = db.execute(
        """SELECT l.*, u.full_name AS salesperson_name
           FROM leads l LEFT JOIN users u ON l.assigned_to=u.id
           WHERE l.id=?""",
        (lid,),
    ).fetchone()
    if lead is None:
        flash("Prospect not found.", "warning")
        return redirect(url_for("leads.leads_index"))
    consultants = db.execute(
        "SELECT id, full_name FROM users WHERE active=1 ORDER BY full_name"
    ).fetchall()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        referrer = request.form.get("referrer", "").strip()
        campaign = request.form.get("campaign", "").strip()
        notes = request.form.get("notes", "").strip()
        marketing_consent = 1 if request.form.get("marketing_consent") == "1" else 0
        do_not_contact = 1 if request.form.get("do_not_contact") == "1" else 0
        duplicate = _find_duplicate(db, phone, exclude_id=lid)

        if not full_name or (not phone and not email):
            flash("Full name and at least one contact method are required.", "error")
        elif duplicate:
            flash(
                f"That phone number already belongs to prospect #{duplicate['id']} — {duplicate['full_name']}.",
                "error",
            )
        else:
            new_status = "do_not_contact" if do_not_contact else lead["lead_status"]
            if new_status == "captured" and lead["assigned_to"]:
                new_status = "assigned"
            closure_reason = "Do not contact requested" if do_not_contact else lead["closure_reason"]
            next_action = lead["next_action"] or ""
            next_action_at = lead["next_action_at"] or ""
            if new_status in ACTIVE_STATUSES and (not next_action or not next_action_at):
                next_action, next_action_at = _automatic_next_action(new_status)
            try:
                db.execute(
                    """UPDATE leads SET full_name=?, phone=?, phone_normalized=?, email=?,
                       referrer=?, campaign=?,
                       next_action=?, next_action_at=?, marketing_consent=?, do_not_contact=?,
                       lead_status=?, closure_reason=?, notes=?, updated_at=datetime('now')
                       WHERE id=?""",
                    (
                        full_name, phone, normalise_phone(phone), email, referrer, campaign,
                        None if do_not_contact else next_action,
                        None if do_not_contact else (next_action_at or None),
                        0 if do_not_contact else marketing_consent, do_not_contact,
                        new_status, closure_reason, notes, lid,
                    ),
                )
                audit_log(db, "prospect_updated", lead_id=lid, new_value=full_name)
                db.commit()
                flash("Prospect profile updated. Original attribution was preserved.", "success")
                return redirect(url_for("leads.lead_detail", lid=lid))
            except Exception as exc:
                db.rollback()
                flash(f"Error: {exc}", "error")

    return render_template(
        "leads/edit.html", lead=lead, sources=LEAD_SOURCES, campaigns=LEAD_CAMPAIGNS,
        consultants=consultants
    )


@leads_bp.post("/<int:lid>/status")
@permission_required("capture_leads")
def update_status(lid: int):
    db = get_db()
    lead = db.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
    if lead is None:
        flash("Prospect not found.", "warning")
        return redirect(url_for("leads.leads_index"))

    new_status = request.form.get("status", "").strip()
    closure_reason = request.form.get("closure_reason", "").strip()
    objection_reason = request.form.get("objection_reason", "").strip()
    recycle_reason = request.form.get("recycle_reason", "").strip()
    duplicate_of = request.form.get("duplicate_of_lead_id", "").strip()
    next_action, next_action_at = _automatic_next_action(new_status)

    if new_status not in dict(LEAD_STATUSES):
        flash("Invalid lifecycle stage.", "error")
    elif new_status == "joined":
        flash("Use Convert to Member so the prospect, application and membership remain linked.", "error")
    elif new_status == "recycle" and recycle_reason not in dict(RECYCLE_REASONS):
        flash("Select a recycle reason.", "error")
    elif new_status in FINAL_STATUSES and new_status != "recycle" and not closure_reason:
        flash("A closure reason is required for an end state.", "error")
    elif new_status == "duplicate" and not duplicate_of:
        flash("Select the original prospect record for a duplicate.", "error")
    else:
        try:
            if new_status == "recycle":
                closure_reason = dict(RECYCLE_REASONS).get(recycle_reason, closure_reason)
            recycle_due_at = _recycle_due_at(recycle_reason) if new_status == "recycle" else None
            db.execute(
                """UPDATE leads SET lead_status=?, closure_reason=?, objection_reason=?,
                   recycle_reason=?, duplicate_of_lead_id=?, next_action=?, next_action_at=?,
                   do_not_contact=?, marketing_consent=?, recycle_due_at=?, updated_at=datetime('now')
                   WHERE id=?""",
                (
                    new_status,
                    closure_reason or None,
                    objection_reason or None,
                    recycle_reason if new_status == "recycle" else lead["recycle_reason"],
                    int(duplicate_of) if duplicate_of else None,
                    next_action or None if new_status in ACTIVE_STATUSES else None,
                    next_action_at or None if new_status in ACTIVE_STATUSES else None,
                    1 if new_status == "do_not_contact" else lead["do_not_contact"],
                    0 if new_status == "do_not_contact" else lead["marketing_consent"],
                    recycle_due_at,
                    lid,
                ),
            )
            from .sales_kpi import record_lead_status_change
            record_lead_status_change(
                db, lid, lead["lead_status"], new_status,
                actor_id=session.get("user_id"),
            )
            if new_status in FINAL_STATUSES:
                db.execute(
                    """UPDATE lead_activities SET status='cancelled'
                       WHERE lead_id=? AND status='scheduled'""", (lid,)
                )
            _add_activity(
                db, lid, "status_change", outcome=new_status,
                notes=closure_reason or objection_reason,
                next_action=next_action, follow_up_at=next_action_at,
            )
            audit_log(
                db, "lifecycle_changed", lead_id=lid,
                old_value=lead["lead_status"], new_value=new_status,
            )
            db.commit()
            flash(f"Lifecycle stage updated to {dict(LEAD_STATUSES)[new_status]}.", "success")
            return redirect(url_for("leads.lead_detail", lid=lid))
        except (TypeError, ValueError):
            db.rollback()
            flash("The duplicate prospect reference is invalid.", "error")

    return redirect(url_for("leads.lead_detail", lid=lid))


@leads_bp.post("/<int:lid>/activities")
@permission_required("capture_leads")
def add_activity(lid: int):
    db = get_db()
    lead = db.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
    if lead is None:
        flash("Prospect not found.", "warning")
        return redirect(url_for("leads.leads_index"))

    activity_type = request.form.get("activity_type", "").strip()
    outcome = request.form.get("outcome", "").strip()
    activity_status = request.form.get("activity_status", "completed").strip()
    notes = request.form.get("notes", "").strip()
    occurred_at = _clean_datetime(request.form.get("occurred_at"))
    scheduled_for = _clean_datetime(request.form.get("scheduled_for"))
    next_action = ""
    follow_up_at = ""
    reason = request.form.get("reason", "").strip()
    decision_date = (request.form.get("decision_date") or "").strip()

    valid_types = {key for key, _ in ACTIVITY_TYPES}
    valid_outcomes = {key for key, _ in ACTIVITY_OUTCOMES.get(activity_type, [])}
    errors = []
    if activity_type not in valid_types:
        errors.append("Select a valid activity type.")
    if valid_outcomes and outcome not in valid_outcomes:
        errors.append("Select a valid activity outcome.")
    source = lead["original_source"] or lead["source"]
    allowed_contact_types = {key for key, _ in _contact_methods_for_source(source)}
    if activity_type in CONTACT_ACTIVITY_TYPES and activity_type not in allowed_contact_types:
        errors.append("That contact method is not available for this lead source.")
    if lead["do_not_contact"] and activity_type in MARKETING_ACTIVITY_TYPES:
        errors.append("Marketing activity is blocked because this prospect is Do Not Contact.")
    if activity_type == "appointment" and outcome in {"scheduled", "confirmed", "rescheduled"} and not scheduled_for:
        errors.append("An appointment date and time is required.")
    if activity_type == "appointment" and outcome in {"cancelled", "no_show"} and not reason:
        errors.append("Record a cancellation or no-show reason separately.")
    if activity_type == "appointment" and outcome == "not_interested" and not reason:
        errors.append("Record why the client was not interested.")
    if activity_type == "visit" and outcome == "not_interested" and not reason:
        errors.append("Record why the walk-in was not interested.")
    if activity_type in CONTACT_ACTIVITY_TYPES and outcome == "not_interested" and not reason:
        errors.append("Not interested requires the actual objection or rejection reason.")
    if activity_type in CONTACT_ACTIVITY_TYPES and outcome == "appointment_booked" and not scheduled_for:
        errors.append("Enter the appointment date and time agreed with the client.")
    if activity_type in CONTACT_ACTIVITY_TYPES and outcome == "follow_up_required":
        if not scheduled_for:
            errors.append("Enter the follow-up date and time agreed with the client.")
        if not reason:
            errors.append("Select why the client needs a follow-up.")
        if scheduled_for:
            try:
                follow_up_day = datetime.fromisoformat(scheduled_for).date()
                if follow_up_day < date.today() or follow_up_day > _add_workdays(date.today(), 5):
                    errors.append("The follow-up must be scheduled within five working days.")
            except ValueError:
                errors.append("Enter a valid follow-up date and time.")
    if activity_type == "consultation" and outcome == "not_interested" and not reason:
        errors.append("Not interested requires the actual objection or rejection reason.")
    if activity_type == "follow_up" and outcome == "scheduled" and not scheduled_for:
        errors.append("A follow-up task requires a scheduled date and time.")
    if activity_type == "offer":
        errors.append("Offers and discounts are not available in the Leads stage.")
    if activity_type == "decision":
        if not decision_date:
            errors.append("A seven-day decision requires a defined decision date.")
        else:
            try:
                end = date.fromisoformat(decision_date)
                if end < date.today() or end > _add_workdays(date.today(), 5):
                    errors.append("The decision date must be within five working days.")
                follow_up_day = min(date.today() + timedelta(days=2), end)
                follow_up_at = datetime.combine(follow_up_day, time(hour=9)).isoformat(sep=" ")
            except ValueError:
                errors.append("Enter a valid decision and follow-up date.")

    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("leads.lead_detail", lid=lid))

    lead_is_closed = lead["lead_status"] in FINAL_STATUSES
    new_stage = lead["lead_status"]
    new_recycle_reason = ""
    if not lead_is_closed:
        if activity_type in CONTACT_ACTIVITY_TYPES and outcome == "appointment_booked":
            new_stage = "appointment_booked"
        elif activity_type in CONTACT_ACTIVITY_TYPES and outcome == "follow_up_required":
            new_stage = "recall_queue"
        elif activity_type in CONTACT_ACTIVITY_TYPES and outcome == "not_interested":
            new_stage, new_recycle_reason = "recycle", "not_interested_call"
        elif activity_type in CONTACT_ACTIVITY_TYPES and outcome in {"no_answer", "voicemail", "call_dropped", "no_response"}:
            new_stage = "recall_queue"
        elif activity_type == "call" and outcome == "does_not_exist":
            new_stage, new_recycle_reason = "recycle", "invalid_number"
        elif activity_type == "call" and outcome == "unreachable":
            new_stage, new_recycle_reason = "recycle", "unreachable_after_attempts"
        elif activity_type == "appointment" and outcome in {"scheduled", "confirmed", "rescheduled"}:
            new_stage = "appointment_booked"
        elif activity_type == "appointment" and outcome in {"showed", "joined"}:
            new_stage = "show"
        elif activity_type == "appointment" and outcome == "no_show":
            new_stage = "recall_queue"
        elif activity_type == "appointment" and outcome == "cancelled":
            new_stage, new_recycle_reason = "recycle", "appointment_cancelled"
        elif activity_type == "appointment" and outcome == "not_interested":
            new_stage, new_recycle_reason = "recycle", "showed_not_interested"
        elif activity_type == "visit" and outcome == "not_interested":
            new_stage, new_recycle_reason = "recycle", "showed_not_interested"
        elif activity_type == "visit":
            new_stage = "show"
        elif activity_type == "consultation" and outcome == "not_interested":
            new_stage, new_recycle_reason = "recycle", "showed_not_interested"
        elif activity_type == "consultation":
            new_stage = "show"
        elif activity_type == "decision" and outcome == "started":
            new_stage = "hot_prospect_7day"
        elif activity_type == "decision" and outcome == "declined":
            new_stage, new_recycle_reason = "recycle", "seven_day_no_conversion"
    elif activity_type in {"call", "appointment", "visit", "consultation", "decision"}:
        flash(
            "This prospect's record is already closed — the activity was logged, "
            "but the lifecycle stage was not changed.", "info",
        )

    if activity_type in CONTACT_ACTIVITY_TYPES:
        next_action, follow_up_at = _automatic_next_action(new_stage, outcome)
    elif activity_type == "visit" or (activity_type == "appointment" and outcome in {"showed", "joined"}):
        next_action, follow_up_at = _automatic_next_action(new_stage)
    elif activity_type == "consultation" and outcome != "not_interested":
        next_action, follow_up_at = _automatic_next_action(new_stage)
    elif activity_type == "decision" and outcome == "started":
        next_action = "Decision follow-up"

    approval_status = "not_required"
    if activity_type == "appointment" and outcome in {"scheduled", "confirmed", "rescheduled"}:
        activity_status = "scheduled"
    elif activity_type == "appointment" and outcome == "no_show":
        activity_status = "no_show"
    else:
        activity_status = "completed"
    if activity_status not in {"completed", "scheduled", "cancelled", "no_show"}:
        activity_status = "completed"

    try:
        _add_activity(
            db, lid, activity_type, outcome=outcome, status=activity_status,
            notes=notes, occurred_at=occurred_at, scheduled_for=scheduled_for,
            next_action=next_action, follow_up_at=follow_up_at, reason=reason,
            approval_status=approval_status,
        )
        if activity_type in CONTACT_ACTIVITY_TYPES:
            from .sales_kpi import record_first_contact
            record_first_contact(db, lid)

        effective_next_action = next_action
        effective_follow_up = follow_up_at
        if activity_type == "follow_up" and outcome == "scheduled":
            effective_next_action = notes or "Complete scheduled follow-up"
            effective_follow_up = scheduled_for
        if activity_type in CONTACT_ACTIVITY_TYPES and outcome == "appointment_booked":
            effective_next_action = "Confirm and complete appointment"
            effective_follow_up = scheduled_for
        if activity_type in CONTACT_ACTIVITY_TYPES and outcome == "follow_up_required":
            effective_next_action = "Complete scheduled follow-up"
            effective_follow_up = scheduled_for
        if activity_type == "appointment" and outcome in {"scheduled", "confirmed", "rescheduled"}:
            effective_next_action = "Confirm and complete appointment"
            effective_follow_up = scheduled_for
        if activity_type == "appointment" and outcome == "no_show":
            auto_followup = datetime.combine(date.today() + timedelta(days=1), time(hour=9)).isoformat(sep=" ")
            effective_next_action = "Follow up after no-show"
            effective_follow_up = auto_followup
            _add_activity(
                db, lid, "follow_up", outcome="scheduled", status="scheduled",
                notes="Automatic follow-up created after appointment no-show.",
                scheduled_for=auto_followup,
            )

        closure_reason = lead["closure_reason"]
        objection_reason = lead["objection_reason"]
        recycle_reason = lead["recycle_reason"]
        if new_recycle_reason:
            recycle_reason = new_recycle_reason
            closure_reason = dict(RECYCLE_REASONS).get(new_recycle_reason, closure_reason)
            objection_reason = reason or objection_reason
            effective_next_action = ""
            effective_follow_up = ""

        recycle_due_at = (
            _recycle_due_at(new_recycle_reason) if new_recycle_reason else lead["recycle_due_at"]
        )

        db.execute(
            """UPDATE leads SET lead_status=?, appointment_date=COALESCE(?, appointment_date),
               decision_start_date=COALESCE(?, decision_start_date),
               decision_date=COALESCE(?, decision_date), next_action=?, next_action_at=?,
               closure_reason=?, objection_reason=?, recycle_reason=?, last_activity_at=datetime('now'),
               recycle_due_at=?, updated_at=datetime('now') WHERE id=?""",
            (
                new_stage,
                scheduled_for if (
                    (activity_type == "appointment" and outcome in {"scheduled", "confirmed", "rescheduled"})
                    or (activity_type in CONTACT_ACTIVITY_TYPES and outcome == "appointment_booked")
                ) else None,
                date.today().isoformat() if activity_type == "decision" and outcome == "started" else None,
                decision_date or None,
                effective_next_action or None,
                effective_follow_up or None,
                closure_reason,
                objection_reason,
                recycle_reason,
                recycle_due_at,
                lid,
            ),
        )
        from .sales_kpi import record_lead_status_change
        record_lead_status_change(
            db, lid, lead["lead_status"], new_stage,
            actor_id=session.get("user_id"),
        )
        if new_recycle_reason:
            db.execute(
                """UPDATE lead_activities SET status='cancelled'
                   WHERE lead_id=? AND status='scheduled'""", (lid,)
            )
        audit_log(db, f"sales_{activity_type}", lead_id=lid, new_value=outcome)
        db.commit()
        if outcome == "joined" and activity_type in {"appointment", "visit"}:
            flash("Attendance confirmed — continue directly to membership.", "success")
            return redirect(url_for("leads.convert_lead", lid=lid))
        flash("Activity added to the prospect timeline.", "success")
    except Exception as exc:
        db.rollback()
        flash(f"Error: {exc}", "error")
    return redirect(url_for("leads.lead_detail", lid=lid))


@leads_bp.post("/activities/<int:activity_id>/complete")
@permission_required("capture_leads")
def complete_activity(activity_id: int):
    db = get_db()
    activity = db.execute(
        "SELECT * FROM lead_activities WHERE id=?", (activity_id,)
    ).fetchone()
    if activity is None:
        flash("Activity not found.", "warning")
        return redirect(url_for("leads.leads_index"))
    db.execute(
        """UPDATE lead_activities SET status='completed', completed_at=datetime('now')
           WHERE id=?""",
        (activity_id,),
    )
    db.execute(
        "UPDATE leads SET last_activity_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
        (activity["lead_id"],),
    )
    audit_log(db, "follow_up_completed", lead_id=activity["lead_id"], new_value=str(activity_id))
    db.commit()
    flash("Follow-up task completed.", "success")
    return redirect(url_for("leads.lead_detail", lid=activity["lead_id"]))


@leads_bp.post("/activities/<int:activity_id>/approve")
@permission_required("approve_sales_offers")
def approve_offer(activity_id: int):
    db = get_db()
    activity = db.execute(
        "SELECT * FROM lead_activities WHERE id=? AND activity_type='offer'", (activity_id,)
    ).fetchone()
    if activity is None:
        flash("Offer not found.", "warning")
        return redirect(url_for("leads.leads_index"))
    db.execute(
        """UPDATE lead_activities SET manager_approval_status='approved',
               manager_approved_by=? WHERE id=?""",
        (session.get("user_id"), activity_id),
    )
    audit_log(db, "sales_offer_approved", lead_id=activity["lead_id"], new_value=str(activity_id))
    db.commit()
    flash("Offer approved.", "success")
    return redirect(url_for("leads.lead_detail", lid=activity["lead_id"]))


@leads_bp.route("/funnel")
@permission_required("view_leads")
def funnel_report():
    db = get_db()
    _expire_decisions(db)
    prospects = db.execute(
        """SELECT l.*, u.full_name AS consultant_name,
                  COALESCE(m.monthly_installment, 0) AS sales_value
           FROM leads l
           LEFT JOIN users u ON l.assigned_to = u.id
           LEFT JOIN membership_applications ma ON ma.lead_id = l.id
           LEFT JOIN members m ON m.id = ma.member_id"""
    ).fetchall()
    activity_rows = db.execute(
        "SELECT lead_id, activity_type, outcome FROM lead_activities"
    ).fetchall()
    activity_map: dict[int, set[tuple[str, str]]] = defaultdict(set)
    for row in activity_rows:
        activity_map[row["lead_id"]].add((row["activity_type"], row["outcome"] or ""))

    def milestones(prospect):
        events = activity_map[prospect["id"]]
        stage = prospect["lead_status"]
        return {
            "assigned": bool(prospect["assigned_to"]),
            "contacted": any(kind in CONTACT_ACTIVITY_TYPES for kind, _ in events) or stage in {"contacted", "appointment_booked", "show", "hot_prospect_7day", "joined"},
            "appointment": any(
                kind == "appointment" or (kind in CONTACT_ACTIVITY_TYPES and result == "appointment_booked")
                for kind, result in events
            ) or stage in {"appointment_booked", "show", "hot_prospect_7day", "joined"},
            "showed": ("appointment", "showed") in events or any(kind == "visit" for kind, _ in events) or stage in {"show", "hot_prospect_7day", "joined"},
            "consulted": any(kind == "consultation" for kind, _ in events) or stage in {"hot_prospect_7day", "joined"},
            "joined": stage == "joined",
        }

    stage_keys = ("assigned", "contacted", "appointment", "showed", "consulted", "joined")

    def new_stats():
        return {"prospects": 0, **{key: 0 for key in stage_keys}, "sales_value": 0.0}

    overall = new_stats()
    groups = {
        "consultants": defaultdict(new_stats),
        "sources": defaultdict(new_stats),
        "channels": defaultdict(new_stats),
        "campaigns": defaultdict(new_stats),
        "referrers": defaultdict(new_stats),
    }
    channel_labels = {"in_reach": "In-Reach", "outreach": "Outreach"}
    for prospect in prospects:
        flags = milestones(prospect)
        value = float(prospect["sales_value"] or 0) if flags["joined"] else 0.0
        targets = [
            overall,
            groups["consultants"][prospect["consultant_name"] or "Unassigned"],
            groups["sources"][prospect["original_source"] or prospect["source"] or "Unknown"],
            groups["channels"][channel_labels.get(prospect["lead_channel"], "Unknown")],
            groups["campaigns"][prospect["campaign"] or "No campaign"],
            groups["referrers"][prospect["referrer"] or "No referrer"],
        ]
        for target in targets:
            target["prospects"] += 1
            target["sales_value"] += value
            for key in stage_keys:
                target[key] += int(flags[key])

    rejection_reasons = Counter(
        prospect["objection_reason"] for prospect in prospects
        if prospect["lead_status"] == "recycle" and prospect["objection_reason"]
    ).most_common()
    recycle_reason_labels = dict(RECYCLE_REASONS + LEGACY_RECYCLE_REASONS)
    recycle_reason_counts = Counter(
        recycle_reason_labels.get(prospect["recycle_reason"], prospect["recycle_reason"])
        for prospect in prospects
        if prospect["lead_status"] == "recycle" and prospect["recycle_reason"]
    ).most_common()

    return render_template(
        "leads/funnel.html",
        overall=overall,
        groups={name: sorted(values.items(), key=lambda item: item[1]["joined"], reverse=True)
                for name, values in groups.items()},
        stage_keys=stage_keys,
        rejection_reasons=rejection_reasons,
        recycle_reason_counts=recycle_reason_counts,
    )


@leads_bp.route("/<int:lid>/convert", methods=["GET", "POST"])
@permission_required("capture_leads")
def convert_lead(lid: int):
    db = get_db()
    lead = db.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
    if lead is None:
        flash("Prospect not found.", "warning")
        return redirect(url_for("leads.leads_index"))
    existing = db.execute(
        "SELECT id FROM membership_applications WHERE lead_id=?", (lid,)
    ).fetchone()
    if existing:
        flash("This prospect already has an application.", "info")
        return redirect(url_for("applications.application_detail", aid=existing["id"]))
    if lead["lead_status"] in {"duplicate", "existing_member", "do_not_contact"}:
        flash("This closed prospect cannot be converted. Reopen it first if appropriate.", "error")
        return redirect(url_for("leads.lead_detail", lid=lid))
    attendance = db.execute(
        """SELECT 1 FROM lead_activities
           WHERE lead_id=? AND (
             activity_type='visit'
             OR (activity_type='appointment' AND outcome IN ('showed','joined'))
           ) LIMIT 1""",
        (lid,),
    ).fetchone()
    if not attendance:
        flash(
            "Membership can only start after the client's walk-in or appointment "
            "attendance has been recorded.", "error",
        )
        return redirect(url_for("leads.lead_detail", lid=lid))

    if request.method == "POST":
        package = request.form.get("package", lead["interested_package"] or "").strip()
        id_number = request.form.get("id_number", "").strip()
        date_of_birth = request.form.get("date_of_birth", "")
        if not id_number:
            flash("ID number is required to create a member record.", "error")
            return render_template("leads/convert.html", lead=lead)

        id_hash = hash_for_lookup(id_number)
        if id_hash:
            duplicate = db.execute(
                "SELECT id FROM members WHERE id_number_hash = ?", (id_hash,)
            ).fetchone()
        else:
            duplicate = db.execute(
                "SELECT id FROM members WHERE id_number = ?", (id_number,)
            ).fetchone()
        if duplicate:
            flash(
                f"A member with that ID number already exists (member #{duplicate['id']}). Link or close this prospect as Existing Member.",
                "error",
            )
            return render_template("leads/convert.html", lead=lead)

        if not sa_id_luhn_valid(id_number):
            flash(
                "That ID number failed the standard check-digit test — please "
                "confirm it against the client's ID document. Conversion has "
                "continued, but the number may be mistyped.",
                "warning",
            )

        try:
            parts = lead["full_name"].strip().split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""
            encrypted = encrypt_member({"id_number": id_number})
            member_ref = next_member_ref(db, current_tenant_name())
            mem_cur = db.execute(
                """INSERT INTO members
                   (member_ref, first_name, last_name, id_number, id_number_hash, contact, email,
                    join_date, package, member_status, outcome, entry_type, source, opt_in)
                   VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'Unverified', 'application', 'lead', ?, ?)""",
                (
                    member_ref, first_name, last_name, encrypted["id_number"], encrypted.get("id_number_hash"),
                    lead["phone"], lead["email"], package,
                    lead["original_source"] or lead["source"], lead["marketing_consent"],
                ),
            )
            member_id = mem_cur.lastrowid

            dob = sa_id_birth_date(id_number)
            if dob is None and date_of_birth:
                try:
                    dob = date.fromisoformat(date_of_birth)
                except ValueError:
                    dob = None
            age_category = "above_23"
            if dob is not None:
                age_category = "below_23" if _age_on(dob) < 23 else "above_23"

            app_cur = db.execute(
                """INSERT INTO membership_applications
                   (lead_id, member_id, package, application_status, created_by)
                   VALUES (?, ?, ?, 'awaiting_docs', ?)""",
                (lid, member_id, package, session.get("user_id")),
            )
            app_id = app_cur.lastrowid
            guardian_req = "required" if age_category == "below_23" else "not_required"
            mgr_req = "required" if age_category == "below_23" else "not_required"
            db.execute(
                """INSERT INTO compliance_checklists
                   (application_id, age_category, guardian_required, guardian_status,
                    manager_approval_status)
                   VALUES (?, ?, ?, ?, ?)""",
                (app_id, age_category, guardian_req, guardian_req, mgr_req),
            )
            from .sales_pipeline import current_sales_stage, transition_sales_stage
            stage = current_sales_stage(lead)
            if stage == "SHOW":
                transition_sales_stage(db, lid, "APPLICATION_INVITED", session.get("user_id"),
                                       reason="Membership application initiated", commit=False)
                transition_sales_stage(db, lid, "APPLICATION_STARTED", session.get("user_id"),
                                       reason="Membership application created", commit=False)
            elif stage == "CONSULTATION":
                transition_sales_stage(db, lid, "APPLICATION_INVITED", session.get("user_id"),
                                       reason="Membership application initiated", commit=False)
                transition_sales_stage(db, lid, "APPLICATION_STARTED", session.get("user_id"),
                                       reason="Membership application created", commit=False)
            else:
                raise ValueError("Lead must be at SHOW or CONSULTATION before an application can be started.")
            db.execute(
                """UPDATE leads SET lead_status='joined', converted_member_id=?,
                   closure_reason=NULL, next_action='Complete membership application', next_action_at=datetime('now'),
                   last_activity_at=datetime('now'), updated_at=datetime('now') WHERE id=?""",
                (member_id, lid),
            )
            _add_activity(
                db, lid, "conversion", outcome="joined",
                notes=f"Application #{app_id}; member #{member_id}; status=Unverified",
            )
            audit_log(
                db, "converted_to_member", lead_id=lid, application_id=app_id,
                new_value=f"member_id={member_id}",
            )
            db.commit()
            flash(
                f"Prospect converted — Application #{app_id} opened and the same journey is linked to member #{member_id}.",
                "success",
            )
            return redirect(url_for("members.member_edit", mid=member_id))
        except Exception as exc:
            db.rollback()
            flash(f"Error: {exc}", "error")

    return render_template("leads/convert.html", lead=lead)
