"""Append-only sales KPI recording helpers.

The helpers in this module are deliberately best-effort: KPI instrumentation
must never prevent the lead or application transaction from completing.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

log = logging.getLogger(__name__)

CHECKLIST_FIELDS = (
    "contract_status", "debit_check_status", "bank_statement_status",
    "id_copy_status", "pos_status", "guardian_status",
    "manager_approval_status", "overall_status",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds", sep=" ")


def ensure_sales_kpi_schema(db, backend: str) -> None:
    """Create KPI tables and add columns on either supported backend."""
    integer_id = "SERIAL PRIMARY KEY" if backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    timestamp = "TIMESTAMPTZ" if backend == "postgresql" else "TEXT"
    db.execute(f"""CREATE TABLE IF NOT EXISTS stage_events (
        id {integer_id}, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
        lead_id INTEGER, application_id INTEGER, member_id INTEGER,
        field TEXT NOT NULL, from_value TEXT, to_value TEXT NOT NULL,
        owner_id INTEGER, actor_id INTEGER, occurred_at {timestamp}, notes TEXT
    )""")
    db.execute(f"""CREATE TABLE IF NOT EXISTS mandate_followups (
        id {integer_id}, mandate_id INTEGER NOT NULL, member_id INTEGER,
        application_id INTEGER, checked_at {timestamp}, checked_by INTEGER,
        check_source TEXT DEFAULT 'manual', status_found TEXT,
        failure_reason TEXT, action_taken TEXT, action_at {timestamp},
        action_by INTEGER, resolution TEXT, resolved_at {timestamp},
        notes TEXT, created_at {timestamp}
    )""")
    columns = {
        "debicheck_mandates": (
            ("application_id", "INTEGER"), ("owner_id", "INTEGER"),
            ("last_checked_at", timestamp), ("last_checked_by", "INTEGER"),
            ("failure_reason", "TEXT"), ("recovery_status", "TEXT"),
        ),
        "leads": (("assigned_at", timestamp), ("first_contact_at", timestamp)),
    }
    for table, additions in columns.items():
        if backend == "postgresql":
            from .db_adapter import pg_table_columns
            existing = set(pg_table_columns(db._conn, table))
        else:
            existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in additions:
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    db.commit()


def record_stage_event(
    db, entity_type: str, entity_id: int, field: str,
    from_value: Any, to_value: Any, *, owner_id: int | None = None,
    actor_id: int | None = None, lead_id: int | None = None,
    application_id: int | None = None, member_id: int | None = None,
    notes: str | None = None,
) -> int | None:
    if from_value == to_value or to_value is None:
        return None
    try:
        cur = db.execute(
            """INSERT INTO stage_events
               (entity_type, entity_id, lead_id, application_id, member_id,
                field, from_value, to_value, owner_id, actor_id, occurred_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entity_type, entity_id, lead_id, application_id, member_id, field,
             None if from_value is None else str(from_value), str(to_value),
             owner_id, actor_id, _now(), notes),
        )
        db.commit()
        return cur.lastrowid
    except Exception as exc:
        log.warning("sales_kpi event failed: %s", exc)
        return None


def record_lead_status_change(db, lead_id: int, old_status: str | None,
                              new_status: str, actor_id: int | None = None) -> None:
    try:
        row = db.execute("SELECT assigned_to FROM leads WHERE id = ?", (lead_id,)).fetchone()
        owner_id = row[0] if row else None
    except Exception:
        owner_id = None
    record_stage_event(db, "lead", lead_id, "lead_status", old_status, new_status,
                       owner_id=owner_id, actor_id=actor_id, lead_id=lead_id)
    try:
        if new_status == "assigned":
            db.execute("UPDATE leads SET assigned_at = COALESCE(assigned_at, ?) WHERE id = ?", (_now(), lead_id))
        elif new_status == "contacted":
            db.execute("UPDATE leads SET first_contact_at = COALESCE(first_contact_at, ?) WHERE id = ?", (_now(), lead_id))
        db.commit()
    except Exception as exc:
        log.warning("sales_kpi lead clock failed: %s", exc)
    try:
        from .sales_pipeline import sync_legacy_stage
        sync_legacy_stage(db, lead_id, new_status, actor_id)
    except Exception as exc:
        log.warning("sales_pipeline legacy sync failed: %s", exc)


def record_first_contact(db, lead_id: int) -> None:
    try:
        db.execute("UPDATE leads SET first_contact_at = COALESCE(first_contact_at, ?) WHERE id = ?", (_now(), lead_id))
        db.commit()
    except Exception as exc:
        log.warning("sales_kpi contact clock failed: %s", exc)


def _application_context(db, application_id: int) -> tuple[Any, Any, Any]:
    try:
        row = db.execute(
            """SELECT ma.lead_id, ma.member_id, COALESCE(l.assigned_to, ma.created_by)
               FROM membership_applications ma LEFT JOIN leads l ON l.id = ma.lead_id
               WHERE ma.id = ?""", (application_id,)).fetchone()
        return tuple(row) if row else (None, None, None)
    except Exception:
        return None, None, None


def record_application_status_change(db, application_id: int, old_status: str | None,
                                     new_status: str, actor_id: int | None = None) -> None:
    lead_id, member_id, owner_id = _application_context(db, application_id)
    record_stage_event(db, "application", application_id, "application_status",
                       old_status, new_status, owner_id=owner_id, actor_id=actor_id,
                       lead_id=lead_id, application_id=application_id, member_id=member_id)


def snapshot_checklist(db, application_id: int) -> dict[str, Any]:
    try:
        row = db.execute(
            f"SELECT {', '.join(CHECKLIST_FIELDS)} FROM compliance_checklists WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        return {field: row[i] for i, field in enumerate(CHECKLIST_FIELDS)} if row else {}
    except Exception:
        return {}


def record_checklist_changes(db, application_id: int, before: dict[str, Any],
                             actor_id: int | None = None) -> None:
    after = snapshot_checklist(db, application_id)
    lead_id, member_id, owner_id = _application_context(db, application_id)
    for field in CHECKLIST_FIELDS:
        if before.get(field) != after.get(field):
            record_stage_event(db, "compliance", application_id, field,
                               before.get(field), after.get(field), owner_id=owner_id,
                               actor_id=actor_id, lead_id=lead_id,
                               application_id=application_id, member_id=member_id)


def _report_rows(db, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in db.execute(sql, params).fetchall()]
    except Exception as exc:
        log.warning("sales_kpi report query failed: %s", exc)
        return []


def _report_date(value: Any):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).replace(tzinfo=None).date()
    except (TypeError, ValueError):
        return None


def _report_pct(value: int, total: int) -> float | None:
    return round(value * 100.0 / total, 1) if total else None


def build_sales_report(db, date_from: str, date_to: str,
                       user_id: int | None = None) -> dict[str, Any]:
    """Build the complete sales-to-join report with Analytics-compatible rules."""
    try:
        start = datetime.fromisoformat(date_from).date()
        end = datetime.fromisoformat(date_to).date()
    except ValueError:
        start, end = datetime.now().date().replace(day=1), datetime.now().date()

    def in_window(value):
        stamp = _report_date(value)
        return stamp is not None and start <= stamp <= end

    leads = _report_rows(
        db, """SELECT l.*, u.username AS consultant_name
               FROM leads l LEFT JOIN users u ON u.id = l.assigned_to
               WHERE l.assigned_to IS NOT NULL"""
    )
    activities = _report_rows(
        db, "SELECT lead_id, activity_type, outcome, occurred_at, created_by FROM lead_activities"
    )
    activity_map: dict[int, set[tuple[str, str]]] = {}
    for activity in activities:
        if in_window(activity["occurred_at"]):
            activity_map.setdefault(activity["lead_id"], set()).add(
                (activity["activity_type"], activity["outcome"] or "")
            )

    stages = {"contacted": 0, "appointment": 0, "show": 0, "joined": 0}
    scoped_leads = []
    for lead in leads:
        if user_id is not None and lead["assigned_to"] != user_id:
            continue
        if not in_window(lead["assigned_at"] or lead["created_at"]):
            continue
        events = activity_map.get(lead["id"], set())
        stage = lead["lead_status"]
        flags = {
            "contacted": any(kind in {"call", "whatsapp", "email"} for kind, _ in events)
                or stage in {"contacted", "appointment_booked", "show", "hot_prospect_7day", "joined"},
            "appointment": any(kind == "appointment" or (kind in {"call", "whatsapp", "email"} and outcome == "appointment_booked") for kind, outcome in events)
                or stage in {"appointment_booked", "show", "hot_prospect_7day", "joined"},
            "show": ("appointment", "showed") in events or any(kind == "visit" for kind, _ in events)
                or stage in {"show", "hot_prospect_7day", "joined"},
            "joined": stage == "joined",
        }
        scoped_leads.append(lead)
        for key, reached in flags.items():
            stages[key] += int(reached)

    scoped_lead_ids = {lead["id"] for lead in scoped_leads}
    applications = _report_rows(
        db, """SELECT ma.id, ma.lead_id, ma.application_status, ma.created_by, ma.created_at, ma.updated_at,
                       COALESCE(l.assigned_to, ma.created_by) AS owner_id
                  FROM membership_applications ma
                  LEFT JOIN leads l ON l.id = ma.lead_id"""
    )
    scoped_apps = [app for app in applications if in_window(app["created_at"])
                   and app["lead_id"] in scoped_lead_ids
                   and (user_id is None or app["owner_id"] == user_id)]
    active_apps = sum(app["application_status"] in {"active", "pushed"} for app in scoped_apps)
    stale_cutoff = datetime.now().date() - timedelta(days=7)
    stalled = [app for app in scoped_apps
               if app["application_status"] not in {"active", "pushed"}
               and (_report_date(app["updated_at"]) or datetime.now().date()) <= stale_cutoff]
    untouched = [lead for lead in scoped_leads
                 if lead["lead_status"] in {"captured", "assigned"}
                 and not lead["first_contact_at"]]

    source_mix: dict[str, dict[str, int]] = {}
    for lead in scoped_leads:
        source = lead["original_source"] or lead["source"] or "Unknown"
        row = source_mix.setdefault(source, {"source": source, "leads": 0, "joined": 0})
        row["leads"] += 1
        row["joined"] += int(lead["lead_status"] == "joined")

    funnel_order = ("assigned", "contacted", "appointment", "show", "application", "activated", "joined")
    funnel_counts = (
        len(scoped_leads), stages["contacted"], stages["appointment"], stages["show"],
        len(scoped_apps), active_apps, stages["joined"],
    )
    leakage = []
    for index, stage_name in enumerate(funnel_order):
        previous_count = funnel_counts[index - 1] if index else None
        current_count = funnel_counts[index]
        drop = (previous_count - current_count) if previous_count is not None and current_count <= previous_count else None
        variance = (current_count - previous_count) if previous_count is not None and current_count > previous_count else None
        leakage.append({
            "stage": stage_name, "reached": current_count,
            "drop": drop,
            "drop_pct": _report_pct(drop, previous_count) if drop is not None and previous_count else None,
            "variance": variance,
        })
    stage_events = _report_rows(
        db, """SELECT lead_id, from_value, to_value, occurred_at, owner_id
                  FROM stage_events WHERE field='lead_status'"""
    )
    stage_order = {"captured": 0, "assigned": 1, "contacted": 2,
                   "recall_queue": 3, "appointment_booked": 4, "show": 5,
                   "hot_prospect_7day": 6, "joined": 7}
    backwards = []
    for event in stage_events:
        if not in_window(event["occurred_at"]):
            continue
        if user_id is not None and event["owner_id"] != user_id:
            continue
        old_rank = stage_order.get(event["from_value"])
        new_rank = stage_order.get(event["to_value"])
        if old_rank is not None and new_rank is not None and new_rank < old_rank:
            backwards.append(event)
    duration_events = _report_rows(
        db, """SELECT application_id, field, from_value, to_value, occurred_at, owner_id
                  FROM stage_events WHERE entity_type='compliance'"""
    )
    durations: dict[str, list[float]] = {}
    grouped_events: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    for event in duration_events:
        if in_window(event["occurred_at"]) and (user_id is None or event["owner_id"] == user_id):
            grouped_events.setdefault((event["application_id"], event["field"]), []).append(event)
    for events in grouped_events.values():
        events.sort(key=lambda event: str(event["occurred_at"] or ""))
        for current, following in zip(events, events[1:]):
            current_at = _report_date(current["occurred_at"])
            following_at = _report_date(following["occurred_at"])
            if current_at and following_at and following_at >= current_at:
                durations.setdefault(current["field"], []).append(
                    (following_at - current_at).total_seconds() / 86400
                )
    stage_timing = {field: {"transitions": len(values), "avg_days": round(sum(values) / len(values), 1)}
                    for field, values in durations.items()}

    mandates = _report_rows(
        db, """SELECT m.id, m.owner_id, m.submitted_at, m.created_at,
                       m.last_checked_at
                  FROM debicheck_mandates m"""
    )
    scoped_mandates = [m for m in mandates if in_window(m["submitted_at"] or m["created_at"])
                       and (user_id is None or m["owner_id"] == user_id)]
    checked = sum(bool(m["last_checked_at"]) for m in scoped_mandates)
    checked_sla = 0
    for mandate in scoped_mandates:
        if not mandate["last_checked_at"]:
            continue
        checked_at = _report_date(mandate["last_checked_at"])
        submitted_at = _report_date(mandate["submitted_at"] or mandate["created_at"])
        if checked_at and submitted_at and (checked_at - submitted_at).days <= 2:
            checked_sla += 1
    followups = _report_rows(
        db, """SELECT f.status_found, f.failure_reason, f.resolution,
                       m.owner_id, f.checked_at
                  FROM mandate_followups f JOIN debicheck_mandates m ON m.id = f.mandate_id"""
    )
    scoped_followups = [f for f in followups if in_window(f["checked_at"])
                        and (user_id is None or f["owner_id"] == user_id)]
    failures = [f for f in scoped_followups if f["status_found"] in {"expired", "failed", "not_found", "rejected", "suspended"}]
    capture_errors = sum(f["failure_reason"] in {"wrong_account_number", "wrong_branch_code", "account_name_mismatch", "wrong_account_type", "wrong_cell_number"} for f in failures)
    recovered = sum(f["resolution"] == "recovered" for f in failures)
    compliance_events = _report_rows(
        db, """SELECT application_id, field, from_value, to_value, owner_id, occurred_at
                  FROM stage_events WHERE entity_type = 'compliance'"""
    )
    scoped_compliance = [e for e in compliance_events if in_window(e["occurred_at"])
                         and (user_id is None or e["owner_id"] == user_id)]
    compliance_apps = {e["application_id"] for e in scoped_compliance}
    corrected_apps = set()
    for event in scoped_compliance:
        if str(event["to_value"] or "").lower() in {"failed", "rejected", "declined"}:
            corrected_apps.add(event["application_id"])
    first_time_right = len(compliance_apps - corrected_apps)

    failure_rows = _report_rows(
        db, """SELECT failure_reason, resolution FROM mandate_followups
               WHERE status_found IN ('expired','failed','not_found','rejected','suspended')"""
    )
    failure_mix: dict[str, dict[str, int]] = {}
    for failure in failure_rows:
        reason = failure["failure_reason"] or "unknown"
        item = failure_mix.setdefault(reason, {"reason": reason, "count": 0, "recovered": 0})
        item["count"] += 1
        item["recovered"] += int(failure["resolution"] == "recovered")

    approval_rows = _report_rows(
        db, "SELECT created_at, approved_at, status FROM approvals WHERE approval_type='manager'"
    )
    approval_hours = []
    pending_approvals = 0
    for approval in approval_rows:
        requested = _report_date(approval["created_at"])
        decided = _report_date(approval["approved_at"])
        if requested and start <= requested <= end:
            if decided:
                approval_hours.append(max((decided - requested).days * 24, 0))
            elif approval["status"] == "pending":
                pending_approvals += 1

    discount_rows = _report_rows(
        db, """SELECT a.discount_percent, a.manager_approval_status, a.occurred_at,
                       l.assigned_to FROM lead_activities a JOIN leads l ON l.id = a.lead_id
               WHERE a.discount_percent > 0"""
    )
    discounts = [row for row in discount_rows if in_window(row["occurred_at"])
                 and (user_id is None or row["assigned_to"] == user_id)]
    active_missing = _report_rows(
        db, """SELECT mem.id AS member_id, mem.first_name, mem.last_name,
                       mem.member_status, d.status AS mandate_status
                  FROM members mem LEFT JOIN debicheck_mandates d
                    ON d.id = (SELECT MAX(d2.id) FROM debicheck_mandates d2 WHERE d2.member_id=mem.id)
                 WHERE mem.member_status='Active'
                   AND (d.id IS NULL OR LOWER(COALESCE(d.status,'unknown')) NOT IN ('active','cancelled'))"""
    )
    quality_rows = _report_rows(
        db, """SELECT ma.date_joined, ma.created_at, mem.member_status,
                       d.status AS mandate_status, ma.created_by
                  FROM membership_applications ma JOIN members mem ON mem.id=ma.member_id
                  LEFT JOIN debicheck_mandates d ON d.id=(SELECT MAX(d2.id) FROM debicheck_mandates d2 WHERE d2.member_id=ma.member_id)"""
    )
    quality = [row for row in quality_rows if in_window(row["date_joined"] or row["created_at"])
               and (user_id is None or row["created_by"] == user_id)
               and (_report_date(row["date_joined"] or row["created_at"]) or end) <= end - timedelta(days=30)]
    quality_good = sum(row["member_status"] == "Active" and str(row["mandate_status"] or "").lower() == "active" for row in quality)

    consultants = _report_rows(
        db, """SELECT id, username FROM users
               WHERE (is_sales_consultant = 1 OR id IN (SELECT assigned_to FROM leads))
                 AND username NOT LIKE '__1cpase_power_%'"""
    )
    consultant_cards = []
    for consultant in consultants:
        card = build_sales_report(db, date_from, date_to, consultant["id"]) if user_id is None else None
        if card is not None:
            consultant_cards.append({"user_id": consultant["id"], "username": consultant["username"],
                                     "funnel": card["funnel"], "application_rate_pct": card["application_rate_pct"]})

    funnel = {
        "assigned": len(scoped_leads), "contacted": stages["contacted"],
        "appointments": stages["appointment"], "shows": stages["show"],
        "applications": len(scoped_apps), "activated": active_apps,
        "joined": stages["joined"],
    }
    return {
        "date_from": start.isoformat(), "date_to": end.isoformat(),
        "funnel": funnel,
        "contact_rate_pct": _report_pct(funnel["contacted"], funnel["assigned"]),
        "appointment_rate_pct": _report_pct(funnel["appointments"], funnel["contacted"]),
        "show_rate_pct": _report_pct(funnel["shows"], funnel["appointments"]),
        "show_to_application_pct": _report_pct(funnel["applications"], funnel["shows"]),
        "application_rate_pct": _report_pct(funnel["activated"], funnel["applications"]),
        "join_rate_pct": _report_pct(funnel["joined"], funnel["shows"]),
        "untouched_leads": untouched, "stalled_applications": stalled,
        "source_mix": sorted(source_mix.values(), key=lambda row: row["leads"], reverse=True),
        "funnel_leakage": leakage,
        "funnel_variances": [row for row in leakage if row["variance"] is not None],
        "backwards_moves": backwards,
        "stage_timing": stage_timing,
        "consultants": consultant_cards,
        "application_quality": {
            "started": len(scoped_apps), "activated": active_apps,
            "completion_rate_pct": _report_pct(active_apps, len(scoped_apps)),
            "stalled": len(stalled),
            "first_time_right": first_time_right,
            "first_time_right_pct": _report_pct(first_time_right, len(compliance_apps)),
        },
        "mandate_discipline": {
            "mandates": len(scoped_mandates), "checked": checked,
            "check_rate_pct": _report_pct(checked, len(scoped_mandates)),
            "checked_within_sla_pct": _report_pct(checked_sla, len(scoped_mandates)),
            "failures": len(failures), "recovered": recovered,
            "recovery_rate_pct": _report_pct(recovered, len(failures)),
            "capture_errors": capture_errors,
        },
        "failure_mix": sorted(failure_mix.values(), key=lambda row: row["count"], reverse=True),
        "approval_turnaround": {
            "requests": len(approval_hours) + pending_approvals,
            "pending": pending_approvals,
            "avg_hours": round(sum(approval_hours) / len(approval_hours), 1) if approval_hours else None,
        },
        "discount_usage": {
            "offers": len(discounts),
            "average_pct": round(sum(row["discount_percent"] for row in discounts) / len(discounts), 1) if discounts else None,
            "approved": sum(row["manager_approval_status"] == "approved" for row in discounts),
        },
        "uncollectable_active_members": active_missing,
        "quality_30_day": {
            "cohort": len(quality), "good": quality_good,
            "rate_pct": _report_pct(quality_good, len(quality)),
        },
    }
