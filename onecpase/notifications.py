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
    from .database import _ensure_column
    # 1 when the recipient owns the work and can finish it themselves (a follow-up,
    # not an approval they are waiting on). Only these lock the app when due.
    _ensure_column(db, backend, "staff_notifications", "blocks_work", "INTEGER NOT NULL DEFAULT 0")


def _self_actionable(kind, event, title):
    """Work the owner finishes alone. Approvals and escalations wait on someone else."""
    if kind == "query":
        return event.startswith(("followup:", "workflow:", "outcome:"))
    if kind == "sale":
        return title.endswith("follow-up due")
    return False


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
        # Signed-form handoffs are actionable immediately, even when the
        # query's ordinary follow-up date is still in the future.
        for r in db.execute("""SELECT q.id,q.reference,q.due_date,w.stage,w.owner_id,w.updated_at
                FROM query_workflows w JOIN queries q ON q.id=w.query_id
                WHERE q.status NOT IN ('resolved','closed')
                  AND w.stage NOT IN ('intake','awaiting_member','awaiting_information','awaiting_settlement','complete','rejected')""").fetchall():
            sources.append(('query',r['id'],f"workflow:{r['stage']}:{r['updated_at']}",r['owner_id'],
                            f"Query {r['reference'] or '#' + str(r['id'])}: {r['stage'].replace('_',' ')}",
                            r['due_date'] if r['stage'] in ('manager_cancel_debit','cancelled_awaiting_mandate_review') else str(r['updated_at'])[:10]))
        for r in db.execute("""SELECT q.id,q.reference,COALESCE(q.assigned_to,q.logged_by) AS reception_owner_id,w.stage,w.updated_at FROM query_workflows w
            JOIN queries q ON q.id=w.query_id WHERE w.stage IN ('complete','rejected') AND q.status='closed'""").fetchall():
            sources.append(('query',r['id'],f"outcome:{r['stage']}:{r['updated_at']}",r['reception_owner_id'],
                            f"Query {r['reference'] or '#' + str(r['id'])}: completed — confirm outcome with client",str(r['updated_at'])[:10]))
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
        actionable = _self_actionable(kind, event, title)
        for recipient in recipients:
            active_keys.add((kind, sid, event, recipient))
            blocks = 1 if actionable and recipient == owner_id else 0
            db.execute("""INSERT INTO staff_notifications
                (source_kind,source_id,event_key,recipient_id,title,due_date,created_at,blocks_work)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source_kind,source_id,event_key,recipient_id) DO NOTHING""",
                (kind, sid, event, recipient, title, due, stamp, blocks))
            db.execute("""UPDATE staff_notifications SET title=?, due_date=?, blocks_work=?
                WHERE source_kind=? AND source_id=? AND event_key=? AND recipient_id=? AND resolved_at IS NULL""",
                (title, due, blocks, kind, sid, event, recipient))
            db.execute("""UPDATE staff_notifications SET resolved_at=NULL, acknowledged_at=NULL,
                reminder_count=0, last_reminder_date=NULL WHERE source_kind=? AND source_id=?
                AND event_key=? AND recipient_id=? AND resolved_at IS NOT NULL""",
                (kind, sid, event, recipient))
    for row in db.execute("SELECT * FROM staff_notifications WHERE resolved_at IS NULL").fetchall():
        if row['source_kind'] in {'hr_leave', 'hr_week', 'fitness_offer'}:
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


def work_links(db, rows):
    """notification id -> the page where its work is done, when this user may open it."""
    links = {}
    def can_view(permission):
        return session.get("role") == "admin" or permission in session.get("permissions", [])
    for r in rows:
        if r['source_kind']=='fitness_offer' and can_view('fitness_module'):
            links[r['id']]=url_for('fitness_journey.allocations')
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
    return links


def _today():
    return datetime.now(ZoneInfo("Africa/Johannesburg")).date().isoformat()


def blocking_items(db, user_id, today=None):
    """The user's own follow-ups due today or earlier: they lock the app until done.

    Doing the work (or setting a new follow-up date) resolves the notification on
    the next refresh; acknowledging does not, except for "confirm the outcome with
    the client", which nothing else records. An item whose work page this user
    cannot open never locks them, or they could never get out.
    """
    rows = db.execute(
        """SELECT * FROM staff_notifications WHERE recipient_id=? AND resolved_at IS NULL
           AND blocks_work=1 AND COALESCE(due_date, ?) <= ?
           AND NOT (event_key LIKE 'outcome:%' AND acknowledged_at IS NOT NULL)
           ORDER BY due_date, id""",
        (user_id, today or _today(), today or _today())).fetchall()
    links = work_links(db, rows)
    return [r for r in rows if r["id"] in links], links


def attention_items(db, user_id, today=None):
    """Everything due today or earlier that the user has not dealt with, for the banner and login pop-up."""
    today = today or _today()
    return db.execute(
        """SELECT * FROM staff_notifications WHERE recipient_id=? AND resolved_at IS NULL
           AND (acknowledged_at IS NULL OR blocks_work=1) AND COALESCE(due_date, ?) <= ?
           ORDER BY blocks_work DESC, due_date, id""",
        (user_id, today, today)).fetchall()


def attention_summary(db, user_id, today=None):
    today = today or _today()
    rows = attention_items(db, user_id, today)
    overdue = sum(1 for r in rows if (r["due_date"] or today) < today)
    return {"overdue": overdue, "today": len(rows) - overdue, "total": len(rows),
            "locked": sum(1 for r in rows if r["blocks_work"])}


# Areas still open while locked: the work itself, the phone and messages to do it,
# the member profile, and signing out.
LOCK_OPEN_BLUEPRINTS = {"notifications", "auth", "platform", "tenants", "health", "comms_webhooks",
                        "join", "mobile", "pbx", "comms", "whatsapp_web"}
LOCK_WORK_BLUEPRINTS = {"query": {"queries"}, "sale": {"leads", "sales_pipeline"}}
LOCK_OPEN_ENDPOINTS = {"static", "members.member_detail"}


def _is_page_visit(req):
    if req.method != "GET":
        return False
    mode = req.headers.get("Sec-Fetch-Mode")
    if mode is not None:
        return mode == "navigate"
    return "text/html" in req.headers.get("Accept", "")


def enforce_overdue_lock():
    """before_request: send a page visit to the overdue list while the user's own work is overdue."""
    from flask import request
    if not session.get("user_id") or request.endpoint is None or not _is_page_visit(request):
        return None
    if request.endpoint in LOCK_OPEN_ENDPOINTS or request.blueprint in LOCK_OPEN_BLUEPRINTS:
        return None
    db = get_db()
    now = datetime.now(ZoneInfo("Africa/Johannesburg")).timestamp()
    stale = now - float(session.get("due_lock_synced_at") or 0) > 60
    items, _links = blocking_items(db, session["user_id"])
    if items or stale:
        refresh(db)  # pick up newly due work, or work that has just been done
        session["due_lock_synced_at"] = now
        items, _links = blocking_items(db, session["user_id"])
    if not items:
        return None
    allowed = set().union(*(LOCK_WORK_BLUEPRINTS.get(r["source_kind"], set()) for r in items))
    if request.blueprint in allowed:
        return None
    return redirect(url_for("notifications.overdue"))


@notifications_bp.get("/overdue")
@login_required
def overdue():
    db = get_db()
    refresh(db)
    items, links = blocking_items(db, session["user_id"])
    if not items:
        return redirect(url_for("dashboard.dashboard"))
    blocking_ids = {r["id"] for r in items}
    others = [r for r in attention_items(db, session["user_id"]) if r["id"] not in blocking_ids]
    links.update(work_links(db, others))
    return render_template("notifications/overdue.html", items=items, others=others, links=links, today=_today())


@notifications_bp.get("")
@login_required
def index():
    db = get_db()
    refresh(db)
    rows = db.execute("SELECT * FROM staff_notifications WHERE recipient_id=? AND resolved_at IS NULL ORDER BY acknowledged_at IS NOT NULL, due_date, id DESC", (session["user_id"],)).fetchall()
    links = work_links(db, rows)
    me = db.execute("SELECT supervisor_id FROM users WHERE id=?", (session["user_id"],)).fetchone()
    return render_template("notifications/index.html", notifications=rows, links=links, today=_today(),
                           supervisor_missing=not me or not me["supervisor_id"])


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
    from flask import request
    if request.form.get("next") == "overdue":
        return redirect(url_for("notifications.overdue"))
    return redirect(url_for("notifications.index"))
