"""Tenant-local staff follow-ups; no external message transport.

One notification per source event and recipient. Acknowledgment stops that
recipient's reminders without closing the business task. New escalations are
new events, so an earlier acknowledgment cannot hide them.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, jsonify, render_template, session, redirect, url_for
from .auth import login_required
from .database import get_db

notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")
SOURCE_BLUEPRINTS = (("query", "queries"), ("sale", "leads"), ("application", "applications"), ("collection", "collections"), ("ptp", "ptp"))


def ensure_schema(db, backend="sqlite"):
    identity = "SERIAL PRIMARY KEY" if backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    db.execute(f"""CREATE TABLE IF NOT EXISTS staff_notifications (
        id {identity}, source_kind TEXT NOT NULL, source_id INTEGER NOT NULL,
        event_key TEXT NOT NULL, recipient_id INTEGER NOT NULL,
        title TEXT NOT NULL, due_date TEXT, created_at TEXT NOT NULL,
        acknowledged_at TEXT, resolved_at TEXT, last_reminder_date TEXT,
        reminder_count INTEGER NOT NULL DEFAULT 0,
        UNIQUE(source_kind, source_id, event_key, recipient_id),
        FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_staff_notifications_recipient ON staff_notifications(recipient_id, resolved_at, acknowledged_at)")


def validate_supervisor(db, employee_id, value):
    """Reject inactive/foreign identities and reporting cycles."""
    if value in (None, ""):
        return None
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        raise ValueError("Choose a valid supervisor.") from None
    seen = {employee_id} if employee_id else set()
    cursor = candidate
    while cursor:
        if cursor in seen:
            raise ValueError("Supervisor assignments cannot form a reporting cycle.")
        seen.add(cursor)
        row = db.execute("SELECT active, is_platform_user, supervisor_id FROM users WHERE id=?", (cursor,)).fetchone()
        if not row or not row["active"] or row["is_platform_user"]:
            raise ValueError("Choose an active staff supervisor in this gym.")
        cursor = row["supervisor_id"]
    return candidate


def sync_notifications(db, now=None, enabled_kinds=None, query_routing_enabled=None):
    """Refresh sources and coalesce overdue reminders to at most once per day.

    Caller owns the transaction. Scheduler and request refreshes use the same
    unique keys and conditional UPDATE, preventing duplicate reminders.
    """
    now = now or datetime.now(ZoneInfo("Africa/Johannesburg"))
    today, stamp = now.date().isoformat(), now.isoformat(timespec="seconds")
    kinds = set(enabled_kinds if enabled_kinds is not None else ("query", "sale", "application", "collection", "ptp"))
    staff = {r["id"]: r for r in db.execute("SELECT id, active, is_platform_user, supervisor_id FROM users").fetchall()}
    sources = []
    if "query" in kinds:
        rows = db.execute("""SELECT q.*,
            (SELECT MAX(n.id) FROM query_notes n WHERE n.query_id=q.id AND n.action_type='escalated') AS escalation_event
            FROM queries q WHERE q.status NOT IN ('resolved','closed')
            AND (q.status='escalated' OR q.approval_status='pending'
                 OR (q.follow_up_date IS NOT NULL AND q.follow_up_date <= ?))""", (today,)).fetchall()
        for r in rows:
            event = (f"approval:{r['approval_requested_at']}:escalation:{r['escalation_event'] or 0}" if r["approval_status"] == "pending"
                     else f"escalated:{r['escalation_event'] or 0}" if r["status"] == "escalated"
                     else f"followup:{r['follow_up_date']}")
            due = r["follow_up_date"] or str(r["approval_requested_at"] or r["created_at"])[:10]
            sources.append(("query", r["id"], event, r["assigned_to"] or r["logged_by"],
                            f"Query {r['reference'] or '#' + str(r['id'])}: {'escalated' if event.startswith('escalated') else 'approval required' if event.startswith('approval') else 'follow-up due'}", due))
    if "sale" in kinds:
        rows = db.execute("""SELECT a.*, l.assigned_to FROM lead_activities a JOIN leads l ON l.id=a.lead_id
            WHERE a.manager_approval_status='pending' OR
            (a.status='pending' AND substr(COALESCE(a.follow_up_at,a.scheduled_for),1,10)<=?)""", (today,)).fetchall()
        for r in rows:
            sources.append(("sale", r["id"], "activity", r["assigned_to"] or r["created_by"],
                            f"Sales lead #{r['lead_id']}: {'approval required' if r['manager_approval_status']=='pending' else 'follow-up due'}",
                            str(r["follow_up_at"] or r["scheduled_for"] or r["created_at"])[:10]))
    if "collection" in kinds:
        for r in db.execute("SELECT * FROM collection_exceptions WHERE status='pending'").fetchall():
            sources.append(("collection", r["id"], "exception", r["created_by"],
                            f"Collections exception #{r['id']}: approval required", str(r["created_at"])[:10]))
    active_keys = set()
    if "application" in kinds:
        rows = db.execute("""SELECT ap.*, COALESCE(l.assigned_to,ma.created_by,ap.requested_by) AS owner_id
            FROM approvals ap JOIN membership_applications ma ON ma.id=ap.application_id
            LEFT JOIN leads l ON l.id=ma.lead_id WHERE ap.status='pending'""").fetchall()
        for r in rows:
            sources.append(("application", r["id"], "approval", r["owner_id"],
                            f"Application #{r['application_id']}: approval required", str(r["created_at"])[:10]))
    if "ptp" in kinds:
        rows = db.execute("""SELECT p.* FROM ptp_agreements p WHERE p.manager_approval_status='pending'
            AND NOT EXISTS (SELECT 1 FROM collection_exceptions e WHERE e.ptp_id=p.id AND e.status='pending')""").fetchall()
        for r in rows:
            sources.append(("ptp", r["id"], "approval", r["created_by"],
                            f"Payment arrangement #{r['id']}: approval required", str(r["created_at"])[:10]))
    for kind, sid, event, owner_id, title, due in sources:
        owner = staff.get(owner_id)
        recipients = {owner_id} if owner and owner["active"] and not owner["is_platform_user"] else set()
        supervisor = staff.get(owner["supervisor_id"]) if owner else None
        if supervisor and supervisor["active"] and not supervisor["is_platform_user"]:
            recipients.add(supervisor["id"])
        if kind == 'query':
            from .query_routing import enabled, staff as routed_staff
            if (enabled() if query_routing_enabled is None else query_routing_enabled):
                query = db.execute('SELECT status, department FROM queries WHERE id=?', (sid,)).fetchone()
                names = ('mvuleni', 'ayrton') if (event.startswith('escalated') or (':escalation:' in event and not event.endswith(':0')) or query['status'] == 'escalated') else (('thato', 'ayrton') if query['department'] == 'Sales' else ())
                for name in names:
                    routed = routed_staff(db, name)
                    if routed:
                        recipients.add(routed['id'])
                if query['department'] == 'Admin':
                    recipients.update(u['id'] for u in db.execute("SELECT id FROM users WHERE role='admin' AND active=1 AND COALESCE(is_platform_user,0)=0").fetchall())
        for recipient in recipients:
            active_keys.add((kind, sid, event, recipient))
            db.execute("""INSERT INTO staff_notifications
                (source_kind,source_id,event_key,recipient_id,title,due_date,created_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(source_kind,source_id,event_key,recipient_id) DO NOTHING""",
                (kind, sid, event, recipient, title, due, stamp))
            db.execute("""UPDATE staff_notifications SET title=?, due_date=?
                WHERE source_kind=? AND source_id=? AND event_key=? AND recipient_id=? AND resolved_at IS NULL""",
                (title, due, kind, sid, event, recipient))
            db.execute("""UPDATE staff_notifications SET resolved_at=NULL, acknowledged_at=NULL,
                reminder_count=0, last_reminder_date=NULL WHERE source_kind=? AND source_id=?
                AND event_key=? AND recipient_id=? AND resolved_at IS NOT NULL""",
                (kind, sid, event, recipient))
    for row in db.execute("SELECT * FROM staff_notifications WHERE resolved_at IS NULL").fetchall():
        if row['source_kind'] in {'hr_leave', 'hr_week'}:
            continue  # HR workflow transactions own these event lifecycles.
        key = (row["source_kind"], row["source_id"], row["event_key"], row["recipient_id"])
        if key not in active_keys:
            db.execute("UPDATE staff_notifications SET resolved_at=? WHERE id=? AND resolved_at IS NULL", (stamp, row["id"]))
    db.execute("""UPDATE staff_notifications SET reminder_count=reminder_count+1, last_reminder_date=?
        WHERE resolved_at IS NULL AND acknowledged_at IS NULL AND due_date < ?
        AND (last_reminder_date IS NULL OR last_reminder_date < ?)""", (today, today, today))


def refresh(db):
    from flask import current_app
    from .phases import is_enabled
    phase = current_app.config.get("ACTIVE_PHASE", 1)
    kinds = [kind for kind, blueprint in SOURCE_BLUEPRINTS if is_enabled(blueprint, phase)]
    sync_notifications(db, enabled_kinds=kinds)
    db.commit()


def pending_count(db, user_id):
    return db.execute("SELECT COUNT(*) FROM staff_notifications WHERE recipient_id=? AND acknowledged_at IS NULL AND resolved_at IS NULL", (user_id,)).fetchone()[0]


@notifications_bp.get("")
@login_required
def index():
    db = get_db()
    refresh(db)
    rows = db.execute("SELECT * FROM staff_notifications WHERE recipient_id=? AND resolved_at IS NULL ORDER BY acknowledged_at IS NOT NULL, due_date, id DESC", (session["user_id"],)).fetchall()
    links = {}
    def can_view(permission):
        return session.get("role") == "admin" or permission in session.get("permissions", [])
    for r in rows:
        if r['source_kind'] == 'hr_leave':
            links[r['id']] = url_for('hr.leave_index')
        elif r['source_kind'] == 'hr_week':
            week = db.execute('SELECT user_id,week_start FROM hr_weeks WHERE id=?', (r['source_id'],)).fetchone()
            if week:
                links[r['id']] = url_for('workforce.weeks',user_id=week['user_id'],week=week['week_start'])
        if r["source_kind"] == "query" and can_view("daily_queries"):
            links[r["id"]] = url_for("queries.detail", qid=r["source_id"])
        elif r["source_kind"] == "sale" and can_view("view_leads"):
            lead = db.execute("SELECT lead_id FROM lead_activities WHERE id=?", (r["source_id"],)).fetchone()
            if lead:
                links[r["id"]] = url_for("leads.lead_detail", lid=lead["lead_id"])
        elif r["source_kind"] == "application" and can_view("all_members"):
            approval = db.execute("SELECT application_id FROM approvals WHERE id=?", (r["source_id"],)).fetchone()
            if approval:
                links[r["id"]] = url_for("applications.application_detail", aid=approval["application_id"])
        elif r["source_kind"] in {"collection", "ptp"}:
            if session.get("role") in {"admin", "manager"}:
                links[r["id"]] = url_for("collections.exception_queue")
            elif can_view("all_members"):
                table = "collection_exceptions" if r["source_kind"] == "collection" else "ptp_agreements"
                case = db.execute(f"SELECT member_id FROM {table} WHERE id=?", (r["source_id"],)).fetchone()
                if case:
                    links[r["id"]] = url_for("members.member_detail", mid=case["member_id"])
    me = db.execute("SELECT supervisor_id FROM users WHERE id=?", (session["user_id"],)).fetchone()
    return render_template("notifications/index.html", notifications=rows, links=links, supervisor_missing=not me or not me["supervisor_id"])


@notifications_bp.get("/count")
@login_required
def count():
    db = get_db()
    refresh(db)
    return jsonify(count=pending_count(db, session["user_id"]))


@notifications_bp.post("/<int:notification_id>/acknowledge")
@login_required
def acknowledge(notification_id):
    db = get_db()
    row = db.execute("SELECT id FROM staff_notifications WHERE id=? AND recipient_id=?", (notification_id, session["user_id"])).fetchone()
    if not row:
        abort(404)
    db.execute("UPDATE staff_notifications SET acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?", (datetime.now(ZoneInfo("Africa/Johannesburg")).isoformat(timespec="seconds"), notification_id))
    db.commit()
    return redirect(url_for("notifications.index"))
