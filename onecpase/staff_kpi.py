"""Daily staff scorecards, credited from recorded activity in South African time."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
SALES_TARGETS = (("connects", "Connected calls", 30), ("referrals", "Referrals", 30),
                 ("appointments", "Appointments booked", 10), ("joined", "Joined members", 3))
RECEPTION_TARGETS = (("calls", "Calls made", 100), ("connects", "Connected calls", 10),
                     ("phone_ptps", "Phone PTPs", 2), ("walk_in_ptps", "Walk-in PTPs", 2),
                     ("debicheck", "Authenticated DebiChecks", 2))


def _columns(db, table, backend=None):
    if backend == "postgresql" or hasattr(db, "_conn"):
        from .db_adapter import pg_table_columns
        return set(pg_table_columns(db._conn, table))
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_schema(db, backend):
    """Add attribution clocks without inventing historical authentication dates."""
    columns = _columns(db, "debicheck_mandates", backend)
    first_install = "kpi_authentication_seen" not in columns
    for name, definition in (("kpi_authenticated_at", "TEXT"),
                             ("kpi_authentication_seen", "INTEGER DEFAULT 0")):
        if name not in columns:
            db.execute(f"ALTER TABLE debicheck_mandates ADD COLUMN {name} {definition}")
    if first_install:
        from .debicheck import normalise_debicheck_status
        for row in db.execute("SELECT id,status FROM debicheck_mandates").fetchall():
            if normalise_debicheck_status(row["status"]) == "approved":
                db.execute("UPDATE debicheck_mandates SET kpi_authentication_seen=1 WHERE id=?", (row["id"],))
    # Correct legacy defaults, preserving separately configured targets.
    if _columns(db, "collection_rules", backend):
        db.execute("UPDATE collection_rules SET rule_value='100' "
                   "WHERE rule_key='daily_call_target' AND rule_value IN ('80','40')")
    columns = _columns(db, "ptp_agreements", backend)
    for name, definition in (("kpi_contact_channel", "TEXT DEFAULT 'unknown'"),
                             ("kpi_recorded_at", "TEXT")):
        if name not in columns:
            db.execute(f"ALTER TABLE ptp_agreements ADD COLUMN {name} {definition}")


def record_authentication(db, mandate_id, status, *, at=None):
    """Record the first confirmed approval, inside the caller's transaction."""
    from .debicheck import normalise_debicheck_status
    if normalise_debicheck_status(status) != "approved":
        return
    # Older integrations/test schemas can synchronise compliance without KPI columns.
    if not {"kpi_authentication_seen", "kpi_authenticated_at"} <= _columns(db, "debicheck_mandates"):
        return
    stamp = at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise ValueError("Authentication timestamps must include a timezone")
    db.execute("""UPDATE debicheck_mandates
                  SET kpi_authentication_seen=1, kpi_authenticated_at=?
                  WHERE id=? AND COALESCE(kpi_authentication_seen,0)=0""",
               (stamp.astimezone(timezone.utc).isoformat(timespec="seconds"), mandate_id))


def record_ptp_channel(db, ptp_id, channel):
    if channel not in {"call", "walk_in", "other", "unknown"}:
        raise ValueError("Select a valid PTP contact channel")
    db.execute("UPDATE ptp_agreements SET kpi_contact_channel=?, kpi_recorded_at=? WHERE id=?",
               (channel, datetime.now(timezone.utc).isoformat(timespec="seconds"), ptp_id))


def business_day(value, *, naive_zone=timezone.utc):
    if not value:
        return None
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=naive_zone)
        return stamp.astimezone(SAST).date()
    except (TypeError, ValueError):
        return None


def selected_day(value=None):
    try:
        return date.fromisoformat(value) if value else datetime.now(SAST).date()
    except (TypeError, ValueError):
        return datetime.now(SAST).date()


def build_daily_scorecard(db, scope, day=None, *, user_id=None):
    from .dashboard_access import team_ids
    from .collections_engine import get_collection_rule_int
    day = selected_day(day)
    allowed = set(team_ids(db, scope))
    if user_id is not None:
        allowed &= {user_id}
    staff = [dict(row) for row in db.execute(
        "SELECT id,username,full_name FROM users WHERE active=1 "
        "AND LOWER(TRIM(COALESCE(role,''))) NOT IN ('manager','sales_manager','admin') "
        "ORDER BY full_name,username").fetchall()
             if row["id"] in allowed]
    targets = SALES_TARGETS if scope == "sales" else RECEPTION_TARGETS
    if scope == "reception":
        call_target = max(1, get_collection_rule_int(db, "daily_call_target", 100))
        targets = tuple((key, label, call_target if key == "calls" else target) for key, label, target in targets)
    counts = {person["id"]: {key: 0 for key, _, _ in targets} for person in staff}
    min_talk = max(1, get_collection_rule_int(db, "contact_talk_seconds", 60))
    missing_ptp_channel = 0
    low, high = (day - timedelta(days=1)).isoformat(), (day + timedelta(days=2)).isoformat()

    def credit(uid, key, when, *, naive_zone=timezone.utc):
        if uid in counts and business_day(when, naive_zone=naive_zone) == day:
            counts[uid][key] += 1

    if counts:
        placeholders = ",".join("?" for _ in counts)
        ids = tuple(counts)
        calls = db.execute(f"""SELECT user_id,start_time,created_at,talk_duration FROM pbx_call_logs
            WHERE user_id IN ({placeholders}) AND LOWER(direction)='outbound'
            AND COALESCE(NULLIF(CAST(start_time AS TEXT),''),CAST(created_at AS TEXT))>=?
            AND COALESCE(NULLIF(CAST(start_time AS TEXT),''),CAST(created_at AS TEXT))<?""", (*ids, low, high)).fetchall()
        for call in calls:
            when = call["start_time"] or call["created_at"]
            zone = SAST if call["start_time"] else timezone.utc
            if scope == "reception":
                credit(call["user_id"], "calls", when, naive_zone=zone)
            if float(call["talk_duration"] or 0) >= min_talk:
                credit(call["user_id"], "connects", when, naive_zone=zone)
        if scope == "sales":
            referrals = db.execute(f"""SELECT created_by,created_at FROM leads
                WHERE created_by IN ({placeholders}) AND duplicate_of_lead_id IS NULL
                AND LOWER(COALESCE(NULLIF(original_source,''),source))='referral'
                AND created_at>=? AND created_at<?""", (*ids, low, high)).fetchall()
            for referral in referrals:
                credit(referral["created_by"], "referrals", referral["created_at"])
            bookings = db.execute(f"""SELECT h.changed_by,h.changed_at FROM sales_stage_history h
                JOIN (SELECT lead_id,MIN(id) AS first_id FROM sales_stage_history
                      WHERE to_stage='APPOINTMENT_BOOKED' GROUP BY lead_id) first_booking ON h.id=first_booking.first_id
                JOIN leads l ON l.id=h.lead_id
                WHERE h.changed_by IN ({placeholders}) AND l.duplicate_of_lead_id IS NULL
                AND h.changed_at>=? AND h.changed_at<?""", (*ids, low, high)).fetchall()
            for booking in bookings:
                credit(booking["changed_by"], "appointments", booking["changed_at"])
            from .sales_kpi import credited_sales
            for sale in credited_sales(db, lambda stamp: business_day(stamp) == day, lambda uid: uid in counts):
                credit(sale["credited_to"], "joined", sale["credited_at"])
        else:
            ptps = db.execute(f"""SELECT created_by,kpi_contact_channel,kpi_recorded_at,created_at FROM ptp_agreements
                WHERE created_by IN ({placeholders})
                AND COALESCE(ptp_status,'pending') NOT IN ('cancelled','canceled','rejected','declined','superseded')
                AND COALESCE(manager_approval_status,'not_required') NOT IN ('rejected','declined')
                AND COALESCE(kpi_recorded_at,CAST(created_at AS TEXT))>=?
                AND COALESCE(kpi_recorded_at,CAST(created_at AS TEXT))<?""",
                              (*ids, low, high)).fetchall()
            for ptp in ptps:
                when = ptp["kpi_recorded_at"] or ptp["created_at"]
                if business_day(when) != day:
                    continue
                key = {"call": "phone_ptps", "walk_in": "walk_in_ptps"}.get(ptp["kpi_contact_channel"])
                if key:
                    credit(ptp["created_by"], key, when)
                elif ptp["kpi_contact_channel"] != "other":
                    missing_ptp_channel += 1
            mandates = db.execute(f"""SELECT COALESCE(submitted_by,owner_id) AS staff_id,kpi_authenticated_at
                FROM debicheck_mandates WHERE COALESCE(submitted_by,owner_id) IN ({placeholders})
                AND kpi_authenticated_at>=? AND kpi_authenticated_at<?""", (*ids, low, high)).fetchall()
            for mandate in mandates:
                credit(mandate["staff_id"], "debicheck", mandate["kpi_authenticated_at"])
    rows = []
    for person in staff:
        cells = []
        for key, label, target in targets:
            actual = counts[person["id"]][key]
            cells.append({"key": key, "actual": actual, "target": target,
                          "percent": round(actual * 100 / target), "met": actual >= target})
        rows.append({"id": person["id"], "name": person["full_name"] or person["username"], "cells": cells})
    return {"day": day.isoformat(), "scope": scope, "targets": targets, "rows": rows,
            "min_talk": min_talk, "missing_ptp_channel": missing_ptp_channel}
