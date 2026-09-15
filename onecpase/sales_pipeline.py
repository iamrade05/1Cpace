"""Controlled sales-stage progression layered onto the existing CRM."""
from collections import deque
from datetime import datetime, timezone

from flask import Blueprint, flash, jsonify, redirect, request, session, url_for

from .auth import permission_required
from .database import get_db


SALES_STAGES = (
    "CAPTURED", "ASSIGNED", "CONTACT_ATTEMPTED", "CONTACTED", "QUALIFIED",
    "INVITED", "APPOINTMENT_BOOKED", "APPOINTMENT_CONFIRMED", "SHOW",
    "NO_SHOW", "CONSULTATION", "DECISION_FOLLOW_UP", "APPLICATION_INVITED", "APPLICATION_STARTED",
    "APPLICATION_SUBMITTED", "JOINED", "RECYCLED", "LOST",
)

ALLOWED_TRANSITIONS = {
    "CAPTURED": {"ASSIGNED", "SHOW", "LOST"},
    "ASSIGNED": {"CONTACT_ATTEMPTED", "LOST"},
    "CONTACT_ATTEMPTED": {"CONTACTED", "ASSIGNED", "RECYCLED", "LOST"},
    "CONTACTED": {"QUALIFIED", "RECYCLED", "LOST"},
    "QUALIFIED": {"INVITED", "RECYCLED", "LOST"},
    "INVITED": {"APPOINTMENT_BOOKED", "RECYCLED", "LOST"},
    "APPOINTMENT_BOOKED": {"APPOINTMENT_CONFIRMED", "NO_SHOW", "SHOW", "RECYCLED", "LOST"},
    "APPOINTMENT_CONFIRMED": {"SHOW", "NO_SHOW", "RECYCLED"},
    "SHOW": {"CONSULTATION", "APPLICATION_INVITED", "LOST"},
    "NO_SHOW": {"INVITED", "APPOINTMENT_BOOKED", "RECYCLED", "LOST"},
    "CONSULTATION": {"DECISION_FOLLOW_UP", "APPLICATION_INVITED", "RECYCLED", "LOST"},
    "DECISION_FOLLOW_UP": {"APPLICATION_INVITED", "RECYCLED", "LOST"},
    "APPLICATION_INVITED": {"APPLICATION_STARTED", "RECYCLED", "LOST"},
    "APPLICATION_STARTED": {"APPLICATION_SUBMITTED", "RECYCLED", "LOST"},
    "APPLICATION_SUBMITTED": {"JOINED", "RECYCLED", "LOST"},
    "JOINED": set(),
    "RECYCLED": {"ASSIGNED", "CONTACT_ATTEMPTED", "LOST"},
    "LOST": set(),
}

SALES_STAGE_LABELS = {
    stage: stage.replace("_", " ").title() for stage in SALES_STAGES
}

LEGACY_STAGE_MAP = {
    "captured": "CAPTURED",
    "assigned": "ASSIGNED",
    "contacted": "CONTACTED",
    "recall_queue": "CONTACT_ATTEMPTED",
    "appointment_booked": "APPOINTMENT_BOOKED",
    "show": "SHOW",
    "hot_prospect_7day": "DECISION_FOLLOW_UP",
    "joined": "JOINED",
    "recycle": "RECYCLED",
    "do_not_contact": "LOST",
    "duplicate": "LOST",
    "existing_member": "LOST",
}

TIMESTAMP_FIELDS = {
    "ASSIGNED": "assigned_at",
    "CONTACTED": "first_contact_at",
    "QUALIFIED": "qualified_at",
    "INVITED": "invited_at",
    "APPOINTMENT_BOOKED": "appointment_at",
    "APPOINTMENT_CONFIRMED": "confirmed_at",
    "SHOW": "show_at",
    "CONSULTATION": "consultation_at",
    "DECISION_FOLLOW_UP": "decision_follow_up_at",
    "APPLICATION_INVITED": "application_invited_at",
    "APPLICATION_STARTED": "application_started_at",
    "APPLICATION_SUBMITTED": "application_submitted_at",
    "JOINED": "joined_at",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds", sep=" ")


def ensure_sales_pipeline_schema(db, backend: str) -> None:
    """Create controlled-pipeline tables and additive lead columns."""
    integer_id = "SERIAL PRIMARY KEY" if backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    timestamp = "TIMESTAMPTZ" if backend == "postgresql" else "TEXT"
    columns = {
        "sales_stage": "TEXT",
        **{field: timestamp for field in TIMESTAMP_FIELDS.values()},
    }
    for name, definition in columns.items():
        if backend == "postgresql":
            from .db_adapter import pg_table_columns
            existing = set(pg_table_columns(db._conn, "leads"))
        else:
            existing = {row[1] for row in db.execute("PRAGMA table_info(leads)").fetchall()}
        if name not in existing:
            db.execute(f"ALTER TABLE leads ADD COLUMN {name} {definition}")

    db.execute(f"""CREATE TABLE IF NOT EXISTS sales_stage_history (
        id {integer_id}, lead_id INTEGER NOT NULL, from_stage TEXT,
        to_stage TEXT NOT NULL, changed_by INTEGER, changed_at {timestamp},
        reason TEXT, notes TEXT
    )""")
    db.execute(f"""CREATE TABLE IF NOT EXISTS lead_qualifications (
        id {integer_id}, lead_id INTEGER NOT NULL UNIQUE,
        interested INTEGER NOT NULL, needs_identified INTEGER NOT NULL,
        ready_to_join INTEGER NOT NULL, suitable_plan TEXT,
        preferred_start_date TEXT, qualification_notes TEXT,
        qualified_by INTEGER, qualified_at {timestamp}
    )""")
    db.execute(f"""CREATE TABLE IF NOT EXISTS sales_appointments (
        id {integer_id}, lead_id INTEGER NOT NULL, appointment_at {timestamp} NOT NULL,
        status TEXT NOT NULL DEFAULT 'BOOKED', confirmed_at {timestamp},
        show_at {timestamp}, no_show_at {timestamp}, notes TEXT
    )""")
    db.execute(f"""CREATE TABLE IF NOT EXISTS application_invitations (
        id {integer_id}, lead_id INTEGER NOT NULL, invited_by INTEGER,
        invited_at {timestamp}, membership_plan TEXT, notes TEXT,
        application_started INTEGER DEFAULT 0, application_submitted INTEGER DEFAULT 0
    )""")
    for legacy, controlled in LEGACY_STAGE_MAP.items():
        db.execute(
            "UPDATE leads SET sales_stage=? WHERE sales_stage IS NULL AND lead_status=?",
            (controlled, legacy),
        )
    db.execute("CREATE INDEX IF NOT EXISTS idx_sales_stage_history_lead ON sales_stage_history(lead_id, changed_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_leads_sales_stage ON leads(sales_stage, assigned_to)")
    db.commit()


def _normalise_stage(stage: str) -> str:
    value = str(stage or "").strip().upper()
    if value not in SALES_STAGES:
        raise ValueError("Invalid controlled sales stage.")
    return value


def _lead(db, lead_id: int):
    lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if lead is None:
        raise ValueError("Lead not found.")
    return lead


def current_sales_stage(lead) -> str:
    return lead["sales_stage"] or LEGACY_STAGE_MAP.get(lead["lead_status"], "CAPTURED")


def _write_transition(db, lead, new_stage: str, user_id: int | None,
                      reason: str | None, notes: str | None, *,
                      validate_requirements: bool, commit: bool = True):
    old_stage = current_sales_stage(lead)
    if new_stage == old_stage:
        raise ValueError("Lead is already at this sales stage.")
    if new_stage not in ALLOWED_TRANSITIONS.get(old_stage, set()):
        raise ValueError(f"Invalid sales transition: {old_stage} -> {new_stage}")

    if validate_requirements and new_stage == "ASSIGNED" and not lead["assigned_to"]:
        raise ValueError("A consultant must be assigned before the lead can be assigned.")
    if validate_requirements and new_stage == "QUALIFIED":
        qualification = db.execute(
            "SELECT interested, needs_identified FROM lead_qualifications WHERE lead_id=?",
            (lead["id"],),
        ).fetchone()
        if not qualification:
            raise ValueError("Qualification must be completed before the lead can be qualified.")
        if not qualification["interested"] or not qualification["needs_identified"]:
            raise ValueError("The lead must be interested and have identified needs before qualification.")
    if validate_requirements and new_stage == "APPOINTMENT_BOOKED":
        appointment = db.execute(
            "SELECT id FROM sales_appointments WHERE lead_id=? AND status='BOOKED' ORDER BY id DESC LIMIT 1",
            (lead["id"],),
        ).fetchone()
        if not appointment:
            raise ValueError("A booked appointment is required before this stage.")
    if validate_requirements and new_stage == "SHOW":
        # A normal prospect must have a booked/confirmed appointment. The
        # explicit direct-show branch is reserved for genuine walk-ins.
        entry_path = (lead["entry_path"] or "standard").strip().lower() if "entry_path" in lead.keys() else "standard"
        source = (lead["source"] if "source" in lead.keys() else "") or ""
        direct_show = entry_path in {"direct_show", "walk_in", "walk-in"} or source.strip().lower() == "walk-in / enquiry"
        if old_stage == "CAPTURED" and not direct_show:
            raise ValueError("A direct SHOW from CAPTURED is only valid for a genuine walk-in/direct-show lead.")
        if old_stage not in {"APPOINTMENT_BOOKED", "APPOINTMENT_CONFIRMED", "CAPTURED"}:
            raise ValueError("SHOW requires a booked/confirmed appointment or an approved direct walk-in.")
    if validate_requirements and new_stage == "CONSULTATION" and old_stage != "SHOW":
        raise ValueError("Consultation requires a recorded SHOW.")
    if validate_requirements and new_stage == "DECISION_FOLLOW_UP" and old_stage != "CONSULTATION":
        raise ValueError("Decision follow-up requires a completed consultation.")
    if validate_requirements and new_stage == "APPLICATION_INVITED" and old_stage not in {"SHOW", "CONSULTATION", "DECISION_FOLLOW_UP"}:
        raise ValueError("Application invitation requires a completed show, consultation, or decision follow-up.")

    now = _utc_now()
    timestamp_field = TIMESTAMP_FIELDS.get(new_stage)
    assignments = ["sales_stage=?", "updated_at=?"]
    values: list[object] = [new_stage, now]
    if timestamp_field:
        assignments.append(f"{timestamp_field}=COALESCE({timestamp_field}, ?)")
        values.append(now)
    values.append(lead["id"])
    db.execute(f"UPDATE leads SET {', '.join(assignments)} WHERE id=?", values)
    db.execute(
        """INSERT INTO sales_stage_history
           (lead_id, from_stage, to_stage, changed_by, changed_at, reason, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (lead["id"], old_stage, new_stage, user_id, now, reason, notes),
    )

    # Keep the cross-module sales event stream aligned with the canonical
    # pipeline. This is written in the same transaction as sales_stage_history
    # so an audit event can never exist without the corresponding transition.
    try:
        owner_id = lead["assigned_to"] if "assigned_to" in lead.keys() else None
        db.execute(
            """INSERT INTO stage_events
               (entity_type, entity_id, lead_id, field, from_value, to_value,
                owner_id, actor_id, occurred_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "lead", lead["id"], lead["id"], "sales_stage",
                old_stage, new_stage, owner_id, user_id, now,
                notes or reason,
            ),
        )
    except Exception:
        # KPI/audit instrumentation must never block a valid sales transition.
        pass

    if commit:
        db.commit()
    return _lead(db, lead["id"])


def transition_sales_stage(db, lead_id: int, new_stage: str, user_id: int | None,
                           reason: str | None = None, notes: str | None = None,
                           *, commit: bool = True):
    """Strictly move a lead one controlled stage at a time."""
    return _write_transition(
        db, _lead(db, lead_id), _normalise_stage(new_stage), user_id,
        reason, notes, validate_requirements=True, commit=commit,
    )


def _stage_path(start: str, target: str) -> list[str] | None:
    """Shortest chain of single ALLOWED_TRANSITIONS hops from start to target.

    Returns the intermediate/target stages only (start excluded), or None if
    target cannot be reached from start at all by following only edges that
    ALLOWED_TRANSITIONS already permits.
    """
    if start == target:
        return []
    came_from: dict[str, str] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == target:
            break
        for nxt in ALLOWED_TRANSITIONS.get(node, set()):
            if nxt not in came_from:
                came_from[nxt] = node
                queue.append(nxt)
    if target not in came_from:
        return None
    path = []
    node = target
    while node != start:
        path.append(node)
        node = came_from[node]
    path.reverse()
    return path


# These four stages require real evidence a legacy lead_status string cannot
# supply - an actual application, actually submitted - and are set only by
# convert_lead()'s own controlled steps and the Itensity handoff. The generic
# bridge below must never manufacture them out of a status write alone: doing
# so races ahead of convert_lead()'s own SHOW/CONSULTATION -> APPLICATION_*
# walk and leaves it nothing to do but fail its own "must be at SHOW or
# CONSULTATION" check, because by the time it runs the canonical stage has
# already jumped straight past both to the terminal JOINED.
CONTROLLED_ONLY_STAGES = frozenset(
    {"APPLICATION_INVITED", "APPLICATION_STARTED", "APPLICATION_SUBMITTED", "JOINED"}
)


def sync_legacy_stage(db, lead_id: int, legacy_stage: str, user_id: int | None = None) -> None:
    """Bridge existing CRM writes into the controlled stage when valid.

    Existing shortcuts remain visible in the legacy CRM, but cannot silently
    advance the canonical stage past a missing controlled step: every hop
    still has to be one ALLOWED_TRANSITIONS already permits, walked one at a
    time and each recorded in sales_stage_history.

    This used to only ever attempt a single hop - if the legacy stage's
    canonical target was not a direct neighbour of wherever the lead's
    canonical stage already stood, the call silently did nothing. In real use
    the ordinary CRM path (call/WhatsApp -> appointment booked -> show ->
    joined) almost never lines up with a single canonical hop from ASSIGNED,
    so the canonical stage got stranded there for nearly every lead - which
    is what made every application downstream fail its "must be at SHOW or
    CONSULTATION" check. Walking the full chain of legitimate hops fixes that
    without weakening the "only via an allowed step" rule the docstring
    describes: every stage in the path was already a legal move from the one
    before it.
    """
    lead = _lead(db, lead_id)
    target = LEGACY_STAGE_MAP.get(legacy_stage)
    if not target or target in CONTROLLED_ONLY_STAGES:
        return
    if not lead["sales_stage"]:
        db.execute("UPDATE leads SET sales_stage=? WHERE id=?", (target, lead_id))
        db.execute(
            """INSERT INTO sales_stage_history
               (lead_id, from_stage, to_stage, changed_by, changed_at, reason)
               VALUES (?, NULL, ?, ?, ?, ?)""",
            (lead_id, target, user_id, _utc_now(), "Legacy CRM stage initialized"),
        )
        db.commit()
        return
    path = _stage_path(current_sales_stage(lead), target)
    if not path:
        return
    for stage in path:
        lead = _write_transition(
            db, lead, stage, user_id, "Legacy CRM stage synchronized", None,
            validate_requirements=False,
        )


def pipeline_context(db, lead_id: int) -> dict:
    lead = _lead(db, lead_id)
    stage = current_sales_stage(lead)
    history = db.execute(
        "SELECT * FROM sales_stage_history WHERE lead_id=? ORDER BY changed_at DESC, id DESC LIMIT 20",
        (lead_id,),
    ).fetchall()
    return {
        "stage": stage,
        "stage_label": SALES_STAGE_LABELS[stage],
        "next_stages": sorted(ALLOWED_TRANSITIONS.get(stage, set())),
        "history": history,
    }


def _add_workdays(start: datetime, days: int) -> datetime:
    current = start
    remaining = max(0, int(days))
    while remaining:
        current = current.replace(hour=12) + __import__("datetime").timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


sales_pipeline_bp = Blueprint("sales_pipeline", __name__, url_prefix="/sales")


def _payload():
    return request.get_json(silent=True) or request.form


def _actor_id():
    return session.get("user_id")


def _response(message: str, *, success: bool, status: int, lead_id: int):
    if request.is_json:
        return jsonify({"success": success, "lead_id": lead_id, "message": message}), status
    flash(message, "success" if success else "error")
    return redirect(url_for("leads.lead_detail", lid=lead_id))


@sales_pipeline_bp.post("/leads/<int:lead_id>/stage")
@permission_required("capture_leads")
def transition_stage(lead_id: int):
    data = _payload()
    try:
        lead = transition_sales_stage(
            get_db(), lead_id, data.get("new_stage", ""),
            user_id=_actor_id(),
            reason=data.get("reason"), notes=data.get("notes"),
        )
        return _response(f"Sales stage updated to {SALES_STAGE_LABELS[lead['sales_stage']]}.", success=True, status=200, lead_id=lead_id)
    except (TypeError, ValueError) as exc:
        return _response(str(exc), success=False, status=400, lead_id=lead_id)


@sales_pipeline_bp.post("/leads/<int:lead_id>/qualification")
@permission_required("capture_leads")
def save_qualification(lead_id: int):
    data = _payload()
    db = get_db()
    try:
        _lead(db, lead_id)
        values = (
            lead_id, int(str(data.get("interested", "0")).lower() in {"1", "true", "yes", "on"}),
            int(str(data.get("needs_identified", "0")).lower() in {"1", "true", "yes", "on"}),
            int(str(data.get("ready_to_join", "0")).lower() in {"1", "true", "yes", "on"}),
            data.get("suitable_plan"), data.get("preferred_start_date"), data.get("notes"),
            _actor_id(), _utc_now(),
        )
        db.execute(
            """INSERT INTO lead_qualifications
               (lead_id, interested, needs_identified, ready_to_join, suitable_plan,
                preferred_start_date, qualification_notes, qualified_by, qualified_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(lead_id) DO UPDATE SET interested=excluded.interested,
                needs_identified=excluded.needs_identified, ready_to_join=excluded.ready_to_join,
                suitable_plan=excluded.suitable_plan, preferred_start_date=excluded.preferred_start_date,
                qualification_notes=excluded.qualification_notes, qualified_by=excluded.qualified_by,
                qualified_at=excluded.qualified_at""",
            values,
        )
        db.commit()
        lead = transition_sales_stage(db, lead_id, "QUALIFIED", _actor_id(), "Qualification completed")
        return _response(f"Sales stage updated to {SALES_STAGE_LABELS[lead['sales_stage']]}.", success=True, status=200, lead_id=lead_id)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return _response(str(exc), success=False, status=400, lead_id=lead_id)


@sales_pipeline_bp.post("/leads/<int:lead_id>/application-invitation")
@permission_required("capture_leads")
def invite_to_application(lead_id: int):
    data = _payload()
    db = get_db()
    try:
        lead = _lead(db, lead_id)
        db.execute(
            """INSERT INTO application_invitations
               (lead_id, invited_by, invited_at, membership_plan, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (lead_id, _actor_id(), _utc_now(), data.get("membership_plan"), data.get("notes")),
        )
        db.commit()
        lead = transition_sales_stage(
            db, lead_id, "APPLICATION_INVITED", _actor_id(), "Application invitation issued"
        )
        return _response(
            f"Sales stage updated to {SALES_STAGE_LABELS[lead['sales_stage']]}." ,
            success=True, status=200, lead_id=lead_id
        )
    except (TypeError, ValueError) as exc:
        db.rollback()
        return _response(str(exc), success=False, status=400, lead_id=lead_id)


@sales_pipeline_bp.post("/leads/<int:lead_id>/appointment")
@permission_required("capture_leads")
def book_sales_appointment(lead_id: int):
    data = _payload()
    db = get_db()
    try:
        _lead(db, lead_id)
        appointment_at = data.get("appointment_at")
        if not appointment_at:
            raise ValueError("Appointment date and time is required.")
        db.execute(
            "INSERT INTO sales_appointments (lead_id, appointment_at, status, notes) VALUES (?, ?, 'BOOKED', ?)",
            (lead_id, appointment_at, data.get("notes")),
        )
        db.commit()
        lead = transition_sales_stage(db, lead_id, "APPOINTMENT_BOOKED", _actor_id(), "Appointment booked")
        return _response(f"Sales stage updated to {SALES_STAGE_LABELS[lead['sales_stage']]}.", success=True, status=200, lead_id=lead_id)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return _response(str(exc), success=False, status=400, lead_id=lead_id)
