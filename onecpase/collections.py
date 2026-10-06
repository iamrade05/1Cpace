import calendar
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta

from flask import (Blueprint, current_app, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_mail import Message
from .auth import permission_required, roles_required
from .communication import (collection_care_email_html,
                            collection_care_email_subject,
                            collection_care_message)
from .database import get_db, member_activity
from .encryption import decrypt_member
from .extensions import mail
from .integrations import push_pos_payment_to_itensity
from .collections_engine import (
    calculate_daily_call_kpi,
    complete_call,
    decide_collection_exception,
    find_duplicate_collection,
    DEFAULT_CONTACT_TALK_SECONDS,
    get_collection_rule_int,
    grant_exception_access,
    log_collection_communication,
    next_communication,
    reached_sql,
    reconcile_open_cases,
    return_collection_exception,
    sync_collection_case,
    talk_seconds_sql,
    transfer_due_sales_cases,
)

collections_bp = Blueprint("collections", __name__, url_prefix="/collections")

PAYMENT_METHODS = ["cash", "card", "eft", "debicheck", "snapscan", "other"]
STATUSES = ["pending", "paid", "partial", "failed", "reversed"]
MIN_DAILY_CALL_TARGET = 80
DAILY_CALL_TARGET = 100
DAILY_CONTACT_TARGET = 10
# Follow-up question asked after an outcome is picked: (prompt, [choices]).
# "Other" always forces a written note; the notes box stays open on every outcome.
OUTCOME_FOLLOW_UPS = {
    "already_paid": ("What did the client pay?", [
        "Total amount owing", "Paid arrears", "Paid cancellation", "Paid for this month", "Other",
    ]),
    "disputed": ("Which amount is the client disputing?", [
        "Balance", "Installment amount", "Arrears amount", "Premium amount", "Other",
    ]),
    "refused": ("Why is the client refusing to pay?", [
        "Not using the gym", "Cannot afford it", "Unhappy with the service", "Wants to cancel", "Other",
    ]),
}
CALL_OUTCOMES = [
    ("reminder_delivered", "Reminder delivered", True),
    ("want_to_reactivate", "Want to reactivate", True),
    ("promised_to_pay", "Promise to Pay", True),
    ("asked_callback", "Client requested a callback", True),
    ("already_paid", "Client says already paid", True),
    ("disputed", "Client disputes the amount", True),
    ("refused", "Client refused payment", True),
    ("no_answer", "No answer", False),
    ("busy", "Line busy", False),
    ("voicemail", "Voicemail", False),
    ("wrong_number", "Wrong / invalid number", False),
]
# 'payment_arrangement' is retired (merged into 'promised_to_pay') but historical
# collection_call_tasks rows still carry it — keep counting those as successful.
SUCCESSFUL_CALL_OUTCOMES = {key for key, _, successful in CALL_OUTCOMES if successful} | {"payment_arrangement"}
UNSUCCESSFUL_ESCALATION_OUTCOMES = {"no_answer", "busy", "voicemail", "wrong_number"}


def _scheduled_and_operational_cycle(year: int, month: int, day: int) -> tuple[date, date]:
    """Return contractual cycle date and the Friday start for weekend cycles."""
    last_day = calendar.monthrange(year, month)[1]
    scheduled = date(year, month, min(day, last_day))
    if scheduled.weekday() == 5:  # Saturday
        return scheduled, scheduled - timedelta(days=1)
    if scheduled.weekday() == 6:  # Sunday
        return scheduled, scheduled - timedelta(days=2)
    return scheduled, scheduled


def _configured_debit_days(db) -> list[int]:
    days = []
    for row in db.execute(
        """SELECT DISTINCT debit_order_date FROM members
           WHERE debit_order_date IS NOT NULL AND debit_order_date != ''"""
    ).fetchall():
        try:
            day = int(row["debit_order_date"])
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 31:
            days.append(day)
    return sorted(set(days))


def _active_debit_cycle(db, today: date) -> dict | None:
    """Select a reminder cycle first; otherwise the most recent started cycle."""
    reminder_offset = get_collection_rule_int(db, "reminder_offset_days", 2)
    cycles = []
    for month_offset in (-1, 0, 1):
        month_index = today.year * 12 + today.month - 1 + month_offset
        year, zero_month = divmod(month_index, 12)
        month = zero_month + 1
        for debit_day in _configured_debit_days(db):
            scheduled, operational = _scheduled_and_operational_cycle(year, month, debit_day)
            cycles.append((scheduled, operational, debit_day))

    reminders = [
        cycle for cycle in cycles
        if cycle[1] - timedelta(days=reminder_offset) <= today < cycle[1]
    ]
    if reminders:
        scheduled, operational, debit_day = min(reminders, key=lambda cycle: cycle[1])
        return {
            "scheduled": scheduled,
            "operational": operational,
            "debit_day": debit_day,
            "phase": "reminder",
        }
    started = [cycle for cycle in cycles if cycle[1] <= today]
    if not started:
        return None
    scheduled, operational, debit_day = max(started, key=lambda cycle: cycle[1])
    return {
        "scheduled": scheduled,
        "operational": operational,
        "debit_day": debit_day,
        "phase": "collection",
    }


def _collections_callers(db):
    return db.execute(
        """SELECT id, full_name FROM users
           WHERE active=1 AND COALESCE(is_platform_user,0)=0
             AND COALESCE(is_collections_caller,0)=1
           ORDER BY full_name, id"""
    ).fetchall()


def _cycle_candidates(db, cycle: dict) -> list[dict]:
    """Reminder and failed-debit candidates. Balance here is the reconciled
    figure (payments matched to charges, see onecpase/ptp.py:bulk_member_arrears)
    — inclusion only requires a genuine positive balance, not more than one
    installment, since a normal single missed/upcoming payment IS the whole
    point of these two queues."""
    from .ptp import bulk_member_arrears

    cycle_date = cycle["scheduled"].isoformat()
    debit_day = str(cycle["debit_day"])
    success_types = ("reminder",) if cycle["phase"] == "reminder" else ("reminder", "current_cycle")
    placeholders = ",".join("?" for _ in success_types)
    rows = db.execute(
        f"""SELECT DISTINCT m.id AS member_id, m.first_name, m.last_name, m.contact
            FROM members m JOIN collections c ON c.member_id=m.id
            WHERE m.member_status='Active' AND CAST(m.debit_order_date AS TEXT)=?
              AND c.status IN ('failed','pending','partial')
              AND COALESCE(c.outstanding_balance,0)>COALESCE(c.amount_paid,0)
              AND NOT EXISTS (
                  SELECT 1 FROM collection_call_tasks t
                  WHERE t.member_id=m.id AND t.cycle_date=?
                    AND t.successful_contact=1 AND t.queue_type IN ({placeholders})
              )""",
        (debit_day, cycle_date, *success_types),
    ).fetchall()
    arrears_by_member = bulk_member_arrears(db, (row["member_id"] for row in rows))

    candidates = []
    if cycle["phase"] == "reminder":
        for row in rows:
            balance = arrears_by_member.get(row["member_id"], {}).get("total_arrears", 0)
            if balance <= 0:
                continue
            candidates.append({
                "member_id": row["member_id"], "collection_id": None,
                "queue_type": "reminder", "priority": 1,
                "balance": balance,
                "name": f"{row['first_name']} {row['last_name']}",
            })
        candidates.sort(key=lambda c: (-c["balance"], c["name"]))

    if cycle["phase"] == "collection":
        failed_rows = db.execute(
            """SELECT m.id AS member_id, MAX(c.id) AS collection_id, m.first_name, m.last_name
               FROM members m JOIN collections c ON c.member_id=m.id
               WHERE CAST(m.debit_order_date AS TEXT)=?
                 AND c.status IN ('failed','pending','partial')
                 AND COALESCE(c.outstanding_balance,0)>COALESCE(c.amount_paid,0)
                 AND substr(c.collection_date,1,7)=substr(?,1,7)
                 AND NOT EXISTS (
                    SELECT 1 FROM collection_call_tasks t
                    WHERE t.member_id=m.id AND t.cycle_date=?
                      AND t.queue_type='failed_debit' AND t.successful_contact=1
                 )
               GROUP BY m.id, m.first_name, m.last_name""",
            (debit_day, cycle_date, cycle_date),
        ).fetchall()
        failed_arrears = bulk_member_arrears(db, (row["member_id"] for row in failed_rows))
        failed = []
        for row in failed_rows:
            balance = failed_arrears.get(row["member_id"], {}).get("total_arrears", 0)
            if balance <= 0:
                continue
            failed.append({
                "member_id": row["member_id"], "collection_id": row["collection_id"],
                "queue_type": "failed_debit", "priority": 1,
                "balance": balance,
                "name": f"{row['first_name']} {row['last_name']}",
            })
        failed.sort(key=lambda c: (-c["balance"], c["name"]))
        failed_ids = {c["member_id"] for c in failed}
        candidates = [c for c in candidates if c["member_id"] not in failed_ids]
        candidates = failed + candidates
    return candidates


def _old_owing_candidates(db, excluded: set[int], today: date) -> list[dict]:
    """Catch-all queue for stale historical balances. Unlike the reminder and
    failed-debit queues above, this one applies the Rule §7 threshold — a
    member only appears once they're more than two months in arrears, not
    just mid-way through the normal reminder/failed-debit follow-up for a
    single missed payment."""
    from .ptp import bulk_member_arrears, ARREARS_RESTRICTION_MONTHS

    rows = db.execute(
        """SELECT m.id AS member_id, MAX(c.id) AS collection_id, m.first_name, m.last_name
           FROM members m JOIN collections c ON c.member_id=m.id
           WHERE c.status IN ('failed','pending','partial')
             AND COALESCE(c.outstanding_balance,0)>COALESCE(c.amount_paid,0)
             AND NOT EXISTS (
               SELECT 1 FROM collection_call_tasks t
               WHERE t.member_id=m.id AND t.queue_type='old_owing'
                 AND t.successful_contact=1 AND t.task_date>=date(?, '-7 days')
             )
           GROUP BY m.id, m.first_name, m.last_name""",
        (today.isoformat(),),
    ).fetchall()
    arrears_by_member = bulk_member_arrears(db, (row["member_id"] for row in rows))

    candidates = []
    for row in rows:
        if row["member_id"] in excluded:
            continue
        arrears = arrears_by_member.get(row["member_id"], {})
        if arrears.get("months_in_arrears", 0) <= ARREARS_RESTRICTION_MONTHS:
            continue
        candidates.append({
            "member_id": row["member_id"], "collection_id": row["collection_id"],
            "queue_type": "old_owing", "priority": 3,
            "balance": arrears.get("total_arrears", 0),
            "name": f"{row['first_name']} {row['last_name']}",
        })
    candidates.sort(key=lambda c: (-c["balance"], c["name"]))
    return candidates


CALL_REST_DAYS = 5


def _recently_called_member_ids(db, today: date) -> set[int]:
    """Clients phoned in the last CALL_REST_DAYS days, whatever the outcome.

    A client called on a Monday rests for the next five days (Tue-Sat) and is
    next eligible on Sunday, so a "no answer" cannot bounce back into
    tomorrow's queue.
    """
    since = (today - timedelta(days=CALL_REST_DAYS)).isoformat()
    return {
        row["member_id"] for row in db.execute(
            """SELECT DISTINCT member_id FROM collection_call_tasks
               WHERE status='completed' AND substr(COALESCE(called_at, task_date), 1, 10) >= ?""",
            (since,),
        ).fetchall()
    }


def _order_never_contacted_first(db, candidates: list[dict]) -> list[dict]:
    """Reach everyone nobody has phoned before cycling back to anyone else.

    Tiers: never called, then called but never reached, then reached before.
    The existing order (queue, then balance) is kept within each tier.
    """
    rows = db.execute(
        """SELECT member_id, MAX(COALESCE(successful_contact, 0)) AS reached
           FROM collection_call_tasks WHERE status='completed' GROUP BY member_id"""
    ).fetchall()
    history = {row["member_id"]: row["reached"] for row in rows}

    def tier(candidate):
        if candidate["member_id"] not in history:
            return 0
        return 2 if history[candidate["member_id"]] else 1

    return sorted(candidates, key=tier)  # sorted() is stable


def _ensure_daily_call_tasks(db, today: date | None = None) -> dict | None:
    today = today or date.today()
    callers = _collections_callers(db)
    daily_call_target = get_collection_rule_int(db, "daily_call_target", DAILY_CALL_TARGET)
    cycle = _active_debit_cycle(db, today)
    if not callers:
        return cycle
    task_date = today.isoformat()
    # Payment data can change after tasks are generated (including payments
    # that only reconcile once matched against a charge — see
    # bulk_member_arrears). Remove only unworked tasks whose account is now
    # genuinely fully paid; completed calls remain auditable.
    from .ptp import bulk_member_arrears
    pending_member_ids = [
        row["member_id"] for row in db.execute(
            "SELECT DISTINCT member_id FROM collection_call_tasks WHERE task_date=? AND status='pending'",
            (task_date,),
        ).fetchall()
    ]
    arrears_by_member = bulk_member_arrears(db, pending_member_ids)
    settled_ids = [
        mid for mid in pending_member_ids
        if arrears_by_member.get(mid, {}).get("total_arrears", 0) <= 0
    ]
    if settled_ids:
        db.execute(
            f"""DELETE FROM collection_call_tasks WHERE task_date=? AND status='pending'
                AND member_id IN ({','.join('?' for _ in settled_ids)})""",
            (task_date, *settled_ids),
        )
    counts = {
        caller["id"]: db.execute(
            "SELECT COUNT(*) FROM collection_call_tasks WHERE task_date=? AND assigned_to=?",
            (task_date, caller["id"]),
        ).fetchone()[0]
        for caller in callers
    }
    existing = {
        row["member_id"] for row in db.execute(
            "SELECT member_id FROM collection_call_tasks WHERE task_date=?", (task_date,)
        ).fetchall()
    }
    # Called within the last CALL_REST_DAYS days (any outcome) - not eligible again yet.
    existing |= _recently_called_member_ids(db, today)
    current = []
    if cycle:
        current = [candidate for candidate in _cycle_candidates(db, cycle) if candidate["member_id"] not in existing]
    # First-month failed debits stay with the signing consultant until they
    # reach the transfer threshold, so they never count in Reception's workload.
    transfer_due_sales_cases(db, arrears_by_member=None)
    sales_owned = {
        row["member_id"] for row in db.execute(
            "SELECT member_id FROM collections_cases WHERE owner_type='SALES' AND status IN ('open','active','pending')"
        ).fetchall()
    }
    current = [candidate for candidate in current if candidate["member_id"] not in sales_owned]
    excluded = existing | sales_owned | {candidate["member_id"] for candidate in current}
    candidates = _order_never_contacted_first(db, current + _old_owing_candidates(db, excluded, today))
    for candidate in candidates:
        available = [
            caller for caller in callers
            if counts[caller["id"]] < daily_call_target
        ]
        if not available:
            break
        caller = min(available, key=lambda row: (counts[row["id"]], row["full_name"], row["id"]))
        db.execute(
            """INSERT OR IGNORE INTO collection_call_tasks
               (task_date, member_id, collection_id, queue_type, cycle_date,
                priority, assigned_to, next_channel)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'CALL')""",
            (
                task_date, candidate["member_id"], candidate["collection_id"],
                candidate["queue_type"], cycle["scheduled"].isoformat() if cycle else None,
                candidate["priority"], caller["id"],
            ),
        )
        counts[caller["id"]] += 1
    db.commit()
    return cycle


def _sync_application_payment(db, member_id: int) -> None:
    """Keep the member application's POS-payment step aligned with collections."""
    from .applications import ensure_member_application, sync_application_status

    aid = ensure_member_application(db, member_id)
    has_paid_payment = db.execute(
        """SELECT 1 FROM collections
           WHERE member_id = ? AND status = 'paid' AND amount_paid > 0
           LIMIT 1""",
        (member_id,),
    ).fetchone()
    db.execute(
        """UPDATE compliance_checklists
           SET pos_status = ?, updated_at = datetime('now')
           WHERE application_id = ?""",
        ("paid" if has_paid_payment else "pending", aid),
    )
    sync_application_status(db, aid)


@collections_bp.route("/")
@permission_required("all_collections")
def collections_index():
    db = get_db()
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()

    query = """
        SELECT c.id, c.outstanding_balance, c.discount_pct, c.amount_paid,
               c.collection_date, c.method, c.status, c.notes,
               m.id AS member_id, m.first_name, m.last_name, m.package
        FROM collections c
        JOIN members m ON c.member_id = m.id
        WHERE 1=1
    """
    params = []
    if search:
        query += " AND (m.first_name LIKE ? OR m.last_name LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if status_filter:
        query += " AND c.status = ?"
        params.append(status_filter)
    query += " ORDER BY c.collection_date DESC, c.id DESC"

    rows = db.execute(query, params).fetchall()

    from .ptp import bulk_member_arrears
    total_paid = db.execute("SELECT SUM(amount_paid) FROM collections").fetchone()[0]
    totals = {
        "total_paid": total_paid,
        "total_outstanding": sum(v["total_arrears"] for v in bulk_member_arrears(db).values()),
    }

    monthly = db.execute(
        """SELECT SUM(amount_paid) as month_paid FROM collections
           WHERE strftime('%Y-%m', collection_date) = strftime('%Y-%m', 'now')
           AND status IN ('paid','partial')"""
    ).fetchone()

    return render_template(
        "collections/index.html",
        collections=rows,
        search=search,
        status_filter=status_filter,
        statuses=STATUSES,
        totals=totals,
        monthly=monthly,
    )


@collections_bp.route("/dashboard")
@permission_required("all_collections")
def dashboard():
    from . import collections_workspace as workspace

    db = get_db()
    transfer_due_sales_cases(db)
    user_id = session.get("user_id")
    return render_template(
        "collections/dashboard.html",
        metrics=workspace.dashboard_metrics(db, user_id=user_id),
        progress=daily_call_progress(db),
        counts=workspace.queue_counts(db, user_id=user_id),
        filters=workspace.QUEUE_FILTERS,
    )


@collections_bp.route("/member/<int:mid>")
@permission_required("all_collections")
def member_profile(mid: int):
    from . import collections_workspace as workspace

    profile = workspace.member_profile(get_db(), mid)
    if profile is None:
        flash("Member not found.", "warning")
        return redirect(url_for("collections.case_queue"))
    return render_template("collections/member.html", p=profile)


@collections_bp.post("/member/<int:mid>/notes")
@permission_required("all_collections")
def add_note(mid: int):
    from .collections_engine import NOTE_TYPES, add_collection_note

    db = get_db()
    note_type = request.form.get("note_type", "GENERAL").strip().upper()
    try:
        add_collection_note(db, mid, note_type, request.form.get("note_text", ""),
                            staff_id=session.get("user_id"))
        db.commit()
        flash("Note added.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("collections.member_profile", mid=mid) + "#notes")


@collections_bp.post("/discount-requests/<int:request_id>/resubmit")
@permission_required("all_collections")
def resubmit_discount(request_id: int):
    from .collections_engine import resubmit_discount_request

    db = get_db()
    row = db.execute("SELECT member_id FROM collection_discount_requests WHERE id=?", (request_id,)).fetchone()
    if row is None:
        flash("Discount request not found.", "warning")
        return redirect(url_for("collections.case_queue"))
    if resubmit_discount_request(db, request_id, by=session.get("user_id")):
        db.commit()
        flash("Request resubmitted to the manager.", "success")
    else:
        flash("Only a returned request can be resubmitted.", "warning")
    return redirect(url_for("collections.member_profile", mid=row["member_id"]) + "#approvals")


@collections_bp.route("/reports")
@permission_required("all_collections")
def reports():
    from . import collections_workspace as workspace

    db = get_db()
    month = workspace.report_month(request.args.get("month"))
    try:
        report_day = date.fromisoformat(request.args.get("day", "")).isoformat()
    except ValueError:
        report_day = date.today().isoformat()
    return render_template(
        "collections/reports.html",
        month=month,
        daily=workspace.daily_call_report(
            db, report_day,
            get_collection_rule_int(db, "daily_call_target", DAILY_CALL_TARGET),
            DAILY_CONTACT_TARGET,
        ),
        outcome_labels=dict((key, label) for key, label, _ in CALL_OUTCOMES),
        financial=workspace.financial_report(db, month),
        sales=workspace.sales_quality_report(db, month),
        performance=workspace.performance_report(db, month),
    )


@collections_bp.route("/queue")
@permission_required("all_collections")
def case_queue():
    from . import collections_workspace as workspace

    db = get_db()
    transfer_due_sales_cases(db)
    user_id = session.get("user_id")
    active = request.args.get("filter", "all")
    if active not in workspace.QUEUE_FILTERS:
        active = "all"
    return render_template(
        "collections/queue.html",
        cases=workspace.case_queue(db, active, user_id=user_id),
        counts=workspace.queue_counts(db, user_id=user_id),
        filters=workspace.QUEUE_FILTERS,
        active=active,
    )


@collections_bp.route("/call-queue")
@permission_required("collections_call_queue")
def call_queue():
    db = get_db()
    cycle = _ensure_daily_call_tasks(db)
    callers = _collections_callers(db)
    signed_in = db.execute(
        "SELECT is_collections_caller FROM users WHERE id=?", (session.get("user_id"),)
    ).fetchone()
    requested_caller = request.args.get("caller", "").strip()
    if signed_in and signed_in["is_collections_caller"] and session.get("role") != "admin":
        caller_filter = session["user_id"]
    else:
        try:
            caller_filter = int(requested_caller) if requested_caller else None
        except ValueError:
            caller_filter = None

    task_date = date.today().isoformat()
    daily_call_target = get_collection_rule_int(db, "daily_call_target", DAILY_CALL_TARGET)
    query = """SELECT t.*, m.member_ref, m.first_name, m.last_name, m.contact,
                      m.debit_order_date, m.member_status,
                      u.full_name AS caller_name,
                      COALESCE((
                        SELECT SUM(CASE WHEN c.outstanding_balance>COALESCE(c.amount_paid,0)
                                        THEN c.outstanding_balance-COALESCE(c.amount_paid,0)
                                        ELSE 0 END)
                        FROM collections c WHERE c.member_id=m.id
                          AND c.status IN ('failed','pending','partial')
                      ),0) AS balance
               FROM collection_call_tasks t
               JOIN members m ON m.id=t.member_id
               JOIN users u ON u.id=t.assigned_to
               WHERE t.task_date=?"""
    params = [task_date]
    if caller_filter:
        query += " AND t.assigned_to=?"
        params.append(caller_filter)
    query += " ORDER BY t.status='completed', t.priority, t.id"
    tasks = db.execute(query, params).fetchall()
    min_talk = get_collection_rule_int(db, "contact_talk_seconds", DEFAULT_CONTACT_TALK_SECONDS)
    summary_rows = db.execute(
        f"""SELECT u.id, u.full_name,
                  COUNT(t.id) AS assigned,
                  SUM(CASE WHEN t.status='completed' THEN 1 ELSE 0 END) AS calls,
                  SUM(CASE WHEN {reached_sql(min_talk)} THEN 1 ELSE 0 END) AS contacts,
                  SUM({talk_seconds_sql()}) AS talk_seconds
           FROM users u
           LEFT JOIN collection_call_tasks t ON t.assigned_to=u.id AND t.task_date=?
           WHERE u.active=1 AND COALESCE(u.is_collections_caller,0)=1
           GROUP BY u.id, u.full_name ORDER BY u.full_name""",
        (task_date,),
    ).fetchall()
    queue_counts = {
        row["queue_type"]: row["count"]
        for row in db.execute(
            """SELECT queue_type, COUNT(*) AS count FROM collection_call_tasks
               WHERE task_date=? GROUP BY queue_type""", (task_date,)
        ).fetchall()
    }
    return render_template(
        "collections/call_queue.html",
        tasks=tasks,
        callers=callers,
        caller_filter=caller_filter,
        summaries=summary_rows,
        queue_counts=queue_counts,
        cycle=cycle,
        call_outcomes=CALL_OUTCOMES,
        daily_call_target=daily_call_target,
        daily_contact_target=DAILY_CONTACT_TARGET,
        min_talk_seconds=min_talk,
        today_label=date.today().strftime("%d %B %Y"),
    )


def _offer_from_request(db, member_id: int, form) -> dict:
    """Run the shared Promise-to-Pay decision for a member from posted inputs.

    Arrears and months owing always come from the verified account, never from
    the form - the receptionist only chooses the conditions of the offer.
    """
    from .collections_decision import INTENT_INSTALMENTS, INTENT_SETTLE, decide_offer, load_policy
    from .members import _build_payment_profile
    from .ptp import compute_arrears_from_profile

    rows = db.execute(
        "SELECT * FROM collections WHERE member_id=? ORDER BY collection_date DESC, id DESC", (member_id,)
    ).fetchall()
    arrears = compute_arrears_from_profile(_build_payment_profile(rows))
    from .debicheck import normalise_debicheck_status

    mandate = db.execute(
        "SELECT status, submitted_at FROM debicheck_mandates WHERE member_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
        (member_id,),
    ).fetchone()

    def amount(name):
        try:
            return max(float(form.get(name) or 0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def has(sql, *params):
        return db.execute(sql, params).fetchone() is not None

    flags = {
        "open_dispute": has(
            "SELECT 1 FROM queries WHERE member_id=? AND query_type='Debit order dispute' "
            "AND status NOT IN ('resolved','closed') LIMIT 1", member_id),
        "unverified_payment_claim": has(
            "SELECT 1 FROM queries WHERE member_id=? AND query_type='Proof of payment' "
            "AND status NOT IN ('resolved','closed') LIMIT 1", member_id),
        "broken_ptp": has("SELECT 1 FROM ptp_agreements WHERE member_id=? AND ptp_status='broken' LIMIT 1", member_id),
        "legal_referral": has("SELECT 1 FROM legal_referrals WHERE member_id=? AND status='open' LIMIT 1", member_id),
        "bank_statement_on_file": has(
            "SELECT 1 FROM member_documents WHERE member_id=? AND lower(COALESCE(document_type,'')) LIKE '%bank%' LIMIT 1",
            member_id),
    }
    offered = amount("upfront_offered")
    verified = str(form.get("upfront_verified", "")).lower() in ("1", "true", "on", "yes")
    intention = form.get("intention") if form.get("intention") in (INTENT_SETTLE, INTENT_INSTALMENTS) else INTENT_INSTALMENTS
    result = decide_offer(
        flags=flags,
        arrears=arrears["total_arrears"],
        months_owing=arrears["months_in_arrears"],
        debicheck_status=normalise_debicheck_status(mandate["status"]) if mandate else None,
        debicheck_submitted_at=mandate["submitted_at"] if mandate else None,
        upfront_offered=offered,
        upfront_received=offered if verified else 0,
        intention=intention,
        returning_member=str(form.get("returning_member", "")).lower() in ("1", "true", "on", "yes"),
        bank_statement_status=form.get("bank_statement_status"),
        policy=load_policy(db),
    )
    # Where each next step happens. Items with no link are waiting on someone else.
    links = {
        "Set up DebiCheck": url_for("debicheck.add", member_id=member_id),
        "Set up a new DebiCheck": url_for("debicheck.add", member_id=member_id),
        "Record the proof of payment": url_for("members.member_detail", mid=member_id) + "#ptp",
        "Upload the bank statement for review": url_for("members.member_detail", mid=member_id),
        "Verify the payment before working out what is owed": url_for("members.member_detail", mid=member_id),
    }
    for item in result["checklist"]:
        item["url"] = links.get(item["action"])
    return result


@collections_bp.get("/call-queue/<int:task_id>/offer-preview")
@permission_required("collections_call_queue")
def offer_preview(task_id: int):
    db = get_db()
    task = db.execute(
        "SELECT member_id, assigned_to FROM collection_call_tasks WHERE id=?", (task_id,)
    ).fetchone()
    if not task:
        return jsonify(error="Call task not found"), 404
    if session.get("role") != "admin" and task["assigned_to"] != session.get("user_id"):
        return jsonify(error="This client is assigned to another caller"), 403
    return jsonify(_offer_from_request(db, task["member_id"], request.args))


@collections_bp.route("/call-queue/<int:task_id>/profile")
@permission_required("collections_call_queue")
def call_profile(task_id: int):
    db = get_db()
    task = db.execute(
        """SELECT t.*, m.*, u.full_name AS caller_name
           FROM collection_call_tasks t
           JOIN members m ON m.id=t.member_id
           JOIN users u ON u.id=t.assigned_to
           WHERE t.id=?""", (task_id,)
    ).fetchone()
    if not task:
        flash("Collection call task not found.", "warning")
        return redirect(url_for("collections.call_queue"))
    current_user = db.execute(
        "SELECT is_collections_caller FROM users WHERE id=?", (session.get("user_id"),)
    ).fetchone()
    if (
        session.get("role") != "admin"
        and (not current_user or not current_user["is_collections_caller"] or task["assigned_to"] != session.get("user_id"))
    ):
        flash("This client is assigned to another collections caller.", "error")
        return redirect(url_for("collections.call_queue"))

    collections = db.execute(
        "SELECT * FROM collections WHERE member_id=? ORDER BY collection_date DESC, id DESC",
        (task["member_id"],),
    ).fetchall()
    from .members import _build_payment_profile, _get_last_gym_visit
    from .ptp import compute_arrears_from_profile
    payment_profile = _build_payment_profile(collections)
    arrears = compute_arrears_from_profile(payment_profile)
    payment_stats = db.execute(
        """SELECT
             SUM(CASE WHEN amount_paid>0 AND status IN ('paid','partial') THEN 1 ELSE 0 END) AS payments_made,
             SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS payments_failed,
             MAX(CASE WHEN amount_paid>0 THEN collection_date END) AS last_payment_date,
             SUM(CASE WHEN amount_paid>0 THEN amount_paid ELSE 0 END) AS total_paid
           FROM collections WHERE member_id=?""",
        (task["member_id"],),
    ).fetchone()
    payment_methods = db.execute(
        """SELECT method, COUNT(*) AS payments, SUM(amount_paid) AS amount
           FROM collections
           WHERE member_id=? AND amount_paid>0 AND status IN ('paid','partial')
           GROUP BY method ORDER BY amount DESC""",
        (task["member_id"],),
    ).fetchall()
    arrangements = db.execute(
        """SELECT p.*, u.full_name AS created_by_name
           FROM ptp_agreements p LEFT JOIN users u ON u.id=p.created_by
           WHERE p.member_id=? ORDER BY p.created_at DESC LIMIT 10""",
        (task["member_id"],),
    ).fetchall()
    mandate = db.execute(
        """SELECT * FROM debicheck_mandates
           WHERE member_id=? ORDER BY created_at DESC, id DESC LIMIT 1""",
        (task["member_id"],),
    ).fetchone()
    queries = db.execute(
        """SELECT q.*, u.full_name AS assigned_to_name
           FROM queries q LEFT JOIN users u ON u.id=q.assigned_to
           WHERE q.member_id=? ORDER BY q.created_at DESC LIMIT 10""",
        (task["member_id"],),
    ).fetchall()
    previous_calls = db.execute(
        """SELECT t.*, u.full_name AS caller_name
           FROM collection_call_tasks t JOIN users u ON u.id=t.assigned_to
           WHERE t.member_id=? AND t.id!=? AND t.status='completed'
           ORDER BY t.called_at DESC LIMIT 10""",
        (task["member_id"], task_id),
    ).fetchall()
    member_for_visit = db.execute(
        "SELECT * FROM members WHERE id=?", (task["member_id"],)
    ).fetchone()
    tenant_name = g.tenant["name"] if g.get("tenant") else "1Cpace"
    return render_template(
        "collections/call_profile.html",
        task=task,
        arrears=arrears,
        payment_stats=payment_stats,
        payment_methods=payment_methods,
        payment_profile=payment_profile[:12],
        arrangements=arrangements,
        mandate=mandate,
        queries=queries,
        previous_calls=previous_calls,
        last_gym_visit=_get_last_gym_visit(db, member_for_visit),
        initial_offer=_offer_from_request(db, task["member_id"], {}),
        call_outcomes=CALL_OUTCOMES,
        outcome_follow_ups=OUTCOME_FOLLOW_UPS,
        whatsapp_message=collection_care_message(task["first_name"], tenant_name),
    )


def _create_collections_query(db, task, category: str, query_type: str, description: str) -> str:
    """Minimal Query insert reusing queries.py's reference generator — routes
    a disputed/already-paid claim into the existing triage workflow instead
    of building a parallel one."""
    from .queries import _next_reference

    reference = _next_reference(db)
    db.execute(
        """INSERT INTO queries
           (reference, logged_by, member_id, member_name, contact,
            category, query_type, description, priority, department)
           VALUES (?,?,?,?,?,?,?,?,'medium','Admin')""",
        (reference, session.get("user_id"), task["member_id"],
         f"{task['first_name']} {task['last_name']}", task["contact"],
         category, query_type, description),
    )
    return reference


def _send_collection_reminder_email(member) -> bool:
    """Automated email leg of the Call→WhatsApp→SMS→Email escalation (Rules
    §5/§13). Mirrors auth.py:_send_otp_email's pattern exactly — no-ops
    silently if mail isn't configured, since WhatsApp/SMS stay manual."""
    if not member["email"] or not current_app.config.get("MAIL_USERNAME"):
        return False
    tenant_name = g.tenant["name"] if g.get("tenant") else "1Cpace"
    try:
        msg = Message(
            subject=collection_care_email_subject(member["first_name"], tenant_name),
            recipients=[member["email"]],
        )
        msg.body = collection_care_message(member["first_name"], tenant_name)
        msg.html = collection_care_email_html(member["first_name"], tenant_name)
        mail.send(msg)
        return True
    except Exception as exc:
        current_app.logger.error("Collections reminder email failed: %s", exc)
        return False


@collections_bp.post("/call-queue/<int:task_id>/result")
@permission_required("collections_call_queue")
def record_call_result(task_id: int):
    db = get_db()
    task = db.execute(
        """SELECT t.*, m.first_name, m.last_name, m.contact, m.email
           FROM collection_call_tasks t
           JOIN members m ON m.id=t.member_id WHERE t.id=?""", (task_id,)
    ).fetchone()
    if not task:
        flash("Collection call task not found.", "warning")
        return redirect(url_for("collections.call_queue"))
    current_user = db.execute(
        "SELECT is_collections_caller FROM users WHERE id=?", (session.get("user_id"),)
    ).fetchone()
    if (
        session.get("role") != "admin"
        and (not current_user or not current_user["is_collections_caller"] or task["assigned_to"] != session.get("user_id"))
    ):
        flash("This call is assigned to another collections caller.", "error")
        return redirect(url_for("collections.call_queue"))
    outcome = request.form.get("outcome", "").strip()
    notes = request.form.get("notes", "").strip()
    try:
        call_result = complete_call(outcome)
    except ValueError:
        flash("Select a valid call outcome.", "error")
        return redirect(url_for("collections.call_queue"))
    if task["status"] == "completed":
        flash("That call has already been completed.", "warning")
        return redirect(url_for("collections.call_queue"))

    follow_up = OUTCOME_FOLLOW_UPS.get(outcome)
    if follow_up:
        prompt, choices = follow_up
        detail = request.form.get("detail", "").strip()
        if detail and detail not in choices:
            flash("Select one of the listed answers.", "error")
            return redirect(url_for("collections.call_profile", task_id=task_id))
        # A written note alone is still accepted so older clients keep working.
        if not detail and not notes:
            flash(f"{prompt} Select an answer or add a note before saving.", "error")
            return redirect(url_for("collections.call_profile", task_id=task_id))
        if detail == "Other" and not notes:
            flash(f"{prompt} Describe it in the notes when you choose Other.", "error")
            return redirect(url_for("collections.call_profile", task_id=task_id))
        if detail:
            notes = f"{detail} — {notes}" if notes else detail

    if outcome == "promised_to_pay" and request.form.get("intention"):
        # Record the conditional offer exactly as the shared calculation sees it.
        # The promise itself changes no balance and applies no discount.
        offer = _offer_from_request(db, task["member_id"], request.form)
        summary = (
            f"Conditional offer [{offer['policy_version']}]: {offer['decision_label']}. "
            f"Owing R{offer['original_arrears']:,.2f} ({offer['months_owing']} mo), "
            f"discount {offer['discount_percent']:g}% (R{offer['discount_amount']:,.2f}), "
            f"total R{offer['total_to_collect']:,.2f}, upfront received R{offer['upfront_received']:,.2f}, "
            f"remaining R{offer['remaining_to_collect']:,.2f}."
        )
        if offer["conditions"]:
            summary += " Outstanding: " + "; ".join(offer["conditions"]) + "."
        notes = f"{summary} {notes}".strip()

    callback_date = None
    if outcome == "asked_callback":
        callback_date = request.form.get("callback_date", "").strip()
        callback_time = request.form.get("callback_time", "").strip()
        if not callback_date or callback_date <= date.today().isoformat():
            flash("Pick a future date for the callback.", "error")
            return redirect(url_for("collections.call_profile", task_id=task_id))
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", callback_time):
            flash("Enter the time the client wants to be called back.", "error")
            return redirect(url_for("collections.call_profile", task_id=task_id))
        notes = f"Callback {callback_date} at {callback_time}" + (f" — {notes}" if notes else "")

    successful = 1 if call_result["successful_contact"] else 0
    next_channel = call_result["next_action"] or None
    db.execute(
        """UPDATE collection_call_tasks SET status='completed', outcome=?,
               successful_contact=?, notes=?, called_at=datetime('now'),
               next_channel=?, next_action_at=CASE WHEN ? IS NULL THEN NULL ELSE date('now','+1 day') END
           WHERE id=?""",
        (outcome, successful, notes or None, next_channel, next_channel, task_id),
    )
    case = db.execute(
        """SELECT id FROM collections_cases
           WHERE member_id=? AND status IN ('open','active','pending')
           ORDER BY id DESC LIMIT 1""",
        (task["member_id"],),
    ).fetchone()
    log_collection_communication(
        db,
        task["member_id"],
        "CALL",
        case_id=case["id"] if case else None,
        task_id=task_id,
        outcome=outcome,
        notes=notes or None,
        created_by=session.get("user_id"),
    )
    member_activity(
        db, task["member_id"],
        f"Collections call ({task['queue_type']}): {dict((key, label) for key, label, _ in CALL_OUTCOMES)[outcome]}"
        + (f" — {notes}" if notes else ""),
    )

    extra_flash = None
    if outcome == "asked_callback":
        db.execute(
            """INSERT OR IGNORE INTO collection_call_tasks
               (task_date, member_id, collection_id, queue_type, cycle_date, priority, assigned_to)
               VALUES (?, ?, ?, 'callback', ?, 1, ?)""",
            (callback_date, task["member_id"], task["collection_id"], callback_date, task["assigned_to"]),
        )
        extra_flash = f"Callback scheduled for {callback_date}."
    elif outcome == "disputed":
        ref = _create_collections_query(
            db, task, "payments", "Debit order dispute",
            f"Raised from collections call ({task['queue_type']}): {notes}",
        )
        extra_flash = f"Dispute logged as query {ref} for investigation."
    elif outcome == "already_paid":
        ref = _create_collections_query(
            db, task, "payments", "Proof of payment",
            f"Client claims payment already made — raised from collections call ({task['queue_type']}): {notes}",
        )
        extra_flash = f"Claim logged as query {ref} pending verification."
    elif outcome in UNSUCCESSFUL_ESCALATION_OUTCOMES:
        # Do not skip the mandated sequence. The task now points to WHATSAPP;
        # SMS and EMAIL become available only after the preceding channel is
        # recorded. Provider sending is intentionally separate from this audit.
        extra_flash = "Call attempt recorded. The next required contact method is WhatsApp."

    db.commit()

    if outcome == "promised_to_pay":
        from .ptp import bulk_member_arrears
        balance = bulk_member_arrears(db, [task["member_id"]]).get(task["member_id"], {}).get("total_arrears", 0)
        flash("Call recorded — complete the Promise to Pay below.", "success")
        return redirect(
            url_for("members.member_detail", mid=task["member_id"])
            + f"?open_ptp=1&promise_amount={balance:.2f}#ptp"
        )

    flash(
        (extra_flash or "Call recorded as a successful contact.") if successful else
        (extra_flash or "Call attempt recorded; the client remains eligible for retry."),
        "success" if successful else "info",
    )
    return redirect(url_for("collections.call_profile", task_id=task_id))


@collections_bp.post("/call-queue/<int:task_id>/escalation")
@permission_required("collections_call_queue")
def toggle_escalation(task_id: int):
    """WhatsApp/SMS stay staff-actioned — no provider integration exists, so
    this just records that a human sent one, same spirit as the WhatsApp
    'copy message' helper already on the member page.

    'sent' and 'failed' are genuinely different outcomes: a message that was
    sent is awaiting a reply or a later failure decision, so it stays on this
    channel; only a recorded failure (no response / undeliverable) advances
    to the next channel in the CALL->WHATSAPP->SMS->EMAIL sequence, via the
    same next_communication() the CALL step already uses - so WHATSAPP_FAILED
    /SMS_FAILED (collections_engine.py) drive a real event instead of being
    unreachable constants.
    """
    db = get_db()
    channel = request.form.get("channel", "")
    result = request.form.get("result", "sent")
    column = {"whatsapp": "whatsapp_sent", "sms": "sms_sent"}.get(channel)
    if not column:
        flash("Unknown escalation channel.", "error")
        return redirect(url_for("collections.call_profile", task_id=task_id))
    task = db.execute("SELECT * FROM collection_call_tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        flash("Collection call task not found.", "warning")
        return redirect(url_for("collections.call_queue"))
    required = str(task["next_channel"] or "").upper()
    if required and required != channel.upper():
        flash(f"The next required contact method is {required}, not {channel.upper()}.", "error")
        return redirect(url_for("collections.call_profile", task_id=task_id))

    if result == "failed":
        next_channel = next_communication(f"{channel.upper()}_FAILED")
        status, outcome = "failed", f"{channel}_no_response"
    else:
        next_channel = task["next_channel"]  # stays on this channel, awaiting a reply or a later failure
        status, outcome = "sent", None

    db.execute(
        f"UPDATE collection_call_tasks SET {column}=1, next_channel=?, next_action_at=CASE WHEN ? IS NULL THEN NULL ELSE date('now','+1 day') END WHERE id=?",
        (next_channel, next_channel, task_id),
    )
    case = db.execute(
        """SELECT id FROM collections_cases
           WHERE member_id=? AND status IN ('open','active','pending')
           ORDER BY id DESC LIMIT 1""",
        (task["member_id"],),
    ).fetchone()
    log_collection_communication(
        db,
        task["member_id"],
        channel,
        case_id=case["id"] if case else None,
        task_id=task_id,
        status=status,
        outcome=outcome,
        created_by=session.get("user_id"),
    )
    member_activity(
        db, task["member_id"],
        f"Collections escalation: {channel} {'sent' if status == 'sent' else 'failed / no response'}.",
    )
    db.commit()
    if status == "sent":
        flash(f"Marked {channel} as sent.", "success")
    else:
        flash(f"Marked {channel} as failed. Next required contact method is {next_channel or 'none'}.", "info")
    return redirect(url_for("collections.call_profile", task_id=task_id))


@collections_bp.post("/call-queue/<int:task_id>/email")
@permission_required("collections_call_queue")
def send_collection_email(task_id: int):
    """Send the final EMAIL leg only after WhatsApp and SMS escalation."""
    db = get_db()
    task = db.execute(
        """SELECT t.*, m.first_name, m.last_name, m.email
           FROM collection_call_tasks t JOIN members m ON m.id=t.member_id
           WHERE t.id=?""", (task_id,)
    ).fetchone()
    if not task:
        flash("Collection task not found.", "warning")
        return redirect(url_for("collections.call_queue"))
    if str(task["next_channel"] or "").upper() != "EMAIL":
        flash("Email is not the next required contact method.", "error")
        return redirect(url_for("collections.call_profile", task_id=task_id))
    if not _send_collection_reminder_email(task):
        flash("Email could not be sent. Check the member email address and mail configuration.", "error")
        return redirect(url_for("collections.call_profile", task_id=task_id))
    db.execute(
        """UPDATE collection_call_tasks SET email_sent=1, next_channel=NULL, next_action_at=NULL
           WHERE id=?""", (task_id,)
    )
    case = db.execute(
        """SELECT id FROM collections_cases WHERE member_id=?
           AND status IN ('open','active','pending') ORDER BY id DESC LIMIT 1""",
        (task["member_id"],),
    ).fetchone()
    log_collection_communication(
        db, task["member_id"], "EMAIL", case_id=case["id"] if case else None,
        task_id=task_id, status="sent", notes="Final escalation email",
        created_by=session.get("user_id"),
    )
    db.commit()
    flash("Collection email sent and escalation sequence completed.", "success")
    return redirect(url_for("collections.call_profile", task_id=task_id))


def _background_itensity_pos_push(
    app, db_path: str, member_id: int, member_data: dict, amount: float,
    description: str, method: str, user_id: int | None,
) -> None:
    """Runs the Itensity POS browser push off the request thread — see the
    matching helper in applications.py for why (own db connection, app_context
    for current_app.config, never touches flask.session)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with app.app_context():
            pushed, pos_error = push_pos_payment_to_itensity(
                member_data, amount=amount, description=description, payment_type=method, pos="Reception",
            )
            if pushed:
                member_activity(conn, member_id, f"Payment of R{amount:,.2f} pushed to Itensity POS", user_id=user_id)
            else:
                member_activity(conn, member_id, f"Itensity POS push failed: {pos_error}", user_id=user_id)
            conn.commit()
    except Exception:
        app.logger.exception("Background Itensity POS push crashed for member %s", member_id)
    finally:
        conn.close()


@collections_bp.route("/add", methods=["GET", "POST"])
@permission_required("new_collection")
def collections_add():
    db = get_db()
    members = db.execute(
        """SELECT id, first_name, last_name, package, tariff, member_status
           FROM members ORDER BY first_name, last_name"""
    ).fetchall()
    selected_member_id = (
        request.form.get("member_id", "").strip()
        or request.args.get("member_id", "").strip()
    )

    if request.method == "POST":
        member_id = request.form.get("member_id", "").strip()
        outstanding_balance = request.form.get("outstanding_balance", "0").strip()
        discount_pct = request.form.get("discount_pct", "0").strip()
        amount_paid = request.form.get("amount_paid", "0").strip()
        collection_date = request.form.get("collection_date", "")
        method = request.form.get("method", "cash")
        status = request.form.get("status", "pending")
        notes = request.form.get("notes", "").strip()

        if not member_id:
            flash("Please select a member.", "error")
            return render_template(
                "collections/add.html", members=members, methods=PAYMENT_METHODS,
                statuses=STATUSES, selected_member_id=selected_member_id,
            )

        if find_duplicate_collection(db, int(member_id), collection_date, outstanding_balance,
                                     amount_paid, method, status, notes):
            flash("An identical collection record is already on file for this member.", "warning")
            return redirect(url_for("collections.collections_index"))

        try:
            cursor = db.execute(
                """INSERT INTO collections
                   (member_id, outstanding_balance, discount_pct, amount_paid,
                    collection_date, method, status, notes, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(member_id), float(outstanding_balance or 0), float(discount_pct or 0),
                 float(amount_paid or 0), collection_date, method, status, notes, session.get("user_id")),
            )
            if status == "failed":
                sync_collection_case(
                    db,
                    int(member_id),
                    collection_id=cursor.lastrowid,
                    failed_debit_date=collection_date,
                    failure_reason=notes or "Debit order failed",
                    arrears_amount=float(outstanding_balance or 0) - float(amount_paid or 0),
                )
            member_activity(
                db,
                int(member_id),
                f"Added collection: R{float(amount_paid or 0):,.2f} paid, "
                f"R{float(outstanding_balance or 0):,.2f} outstanding ({status})",
            )
            reconcile_open_cases(db, [int(member_id)], changed_by=session.get("user_id"))
            _sync_application_payment(db, int(member_id))
            db.commit()
            flash("Collection record added successfully.", "success")

            if request.form.get("push_to_itensity") == "1" and float(amount_paid or 0) > 0:
                member_row = db.execute("SELECT * FROM members WHERE id = ?", (int(member_id),)).fetchone()
                member_data = decrypt_member(member_row) if member_row else None
                if not member_data:
                    flash("Could not push to Itensity POS: member not found.", "warning")
                else:
                    db_path = db.execute("PRAGMA database_list").fetchone()["file"]
                    app_obj = current_app._get_current_object()
                    threading.Thread(
                        target=_background_itensity_pos_push,
                        args=(app_obj, db_path, int(member_id), member_data, float(amount_paid),
                              notes or "Member payment", method, session.get("user_id")),
                        daemon=True,
                    ).start()
                    flash("Payment saved. Itensity POS sync started in the background.", "info")

            return redirect(url_for("members.member_detail", mid=int(member_id)) + "#payments")
        except Exception as e:
            flash(f"Error saving collection: {e}", "error")

    return render_template(
        "collections/add.html", members=members, methods=PAYMENT_METHODS,
        statuses=STATUSES, selected_member_id=selected_member_id,
    )


@collections_bp.route("/<int:cid>/edit", methods=["GET", "POST"])
@permission_required("new_collection")
def collections_edit(cid: int):
    db = get_db()
    col = db.execute(
        """SELECT c.*, m.first_name, m.last_name FROM collections c
           JOIN members m ON c.member_id = m.id WHERE c.id = ?""",
        (cid,),
    ).fetchone()
    if col is None:
        flash("Collection not found.", "warning")
        return redirect(url_for("collections.collections_index"))

    if request.method == "POST":
        amount_paid = request.form.get("amount_paid", "0").strip()
        status = request.form.get("status", "pending")
        method = request.form.get("method", "cash")
        notes = request.form.get("notes", "").strip()
        collection_date = request.form.get("collection_date", "")
        try:
            db.execute(
                """UPDATE collections
                   SET amount_paid = ?, status = ?, method = ?, notes = ?, collection_date = ?
                   WHERE id = ?""",
                (float(amount_paid or 0), status, method, notes, collection_date, cid),
            )
            if status == "failed":
                sync_collection_case(
                    db,
                    col["member_id"],
                    collection_id=cid,
                    failed_debit_date=collection_date,
                    failure_reason=notes or "Debit order failed",
                    arrears_amount=float(col["outstanding_balance"] or 0) - float(amount_paid or 0),
                )
            member_activity(
                db,
                col["member_id"],
                f"Updated collection #{cid}: R{float(amount_paid or 0):,.2f} paid ({status})",
            )
            reconcile_open_cases(db, [col["member_id"]], changed_by=session.get("user_id"))
            _sync_application_payment(db, col["member_id"])
            db.commit()
            flash("Collection updated.", "success")
            return redirect(url_for("collections.collections_index"))
        except Exception as e:
            flash(f"Error: {e}", "error")

    return render_template("collections/edit.html", col=col, methods=PAYMENT_METHODS, statuses=STATUSES)


@collections_bp.post("/<int:cid>/delete")
@permission_required("new_collection")
def collections_delete(cid: int):
    db = get_db()
    col = db.execute(
        "SELECT member_id, amount_paid FROM collections WHERE id = ?", (cid,)
    ).fetchone()
    db.execute("DELETE FROM collections WHERE id = ?", (cid,))
    if col:
        member_activity(
            db,
            col["member_id"],
            f"Deleted collection #{cid} (R{float(col['amount_paid'] or 0):,.2f} paid)",
        )
        _sync_application_payment(db, col["member_id"])
    db.commit()
    flash("Collection record deleted.", "info")
    return redirect(url_for("collections.collections_index"))


# ── Shared collections KPIs ───────────────────────────────────────────────────

def daily_call_progress(db, today: date | None = None) -> dict:
    """Aggregate call-cycle KPIs for one day.

    Uses the same definitions as the call queue screen — a call is a completed
    task, a contact is a task flagged successful_contact — so the dashboard and
    the queue can never quote different numbers for the same day.
    """
    task_date = (today or date.today()).isoformat()
    target_per_caller = get_collection_rule_int(db, "daily_call_target", DAILY_CALL_TARGET)
    min_talk = get_collection_rule_int(db, "contact_talk_seconds", DEFAULT_CONTACT_TALK_SECONDS)
    row = db.execute(
        f"""SELECT COUNT(t.id) AS assigned,
                  COALESCE(SUM(CASE WHEN t.status='completed' THEN 1 ELSE 0 END), 0) AS calls,
                  COALESCE(SUM(CASE WHEN {reached_sql(min_talk)} THEN 1 ELSE 0 END), 0) AS contacts,
                  COALESCE(SUM({talk_seconds_sql()}), 0) AS talk_seconds
           FROM collection_call_tasks t WHERE t.task_date=?""",
        (task_date,),
    ).fetchone()
    caller_count = len(_collections_callers(db))
    assigned = row["assigned"] or 0
    calls = row["calls"] or 0
    contacts = row["contacts"] or 0
    daily_target = target_per_caller * caller_count
    # calculate_daily_call_kpi (collections_engine.py) is the shared KPI
    # calculation - pass the real team-wide target, not its own 40 default,
    # and round its 2dp figures down to the 1dp this dashboard has always
    # shown so the fix doesn't silently change the displayed precision.
    kpi = calculate_daily_call_kpi(calls, target=daily_target, contacts=contacts, calls_attempted=calls)
    return {
        "task_date": task_date,
        "assigned": assigned,
        "calls": calls,
        "contacts": contacts,
        "talk_seconds": row["talk_seconds"] or 0,
        "min_talk_seconds": min_talk,
        "contact_target": DAILY_CONTACT_TARGET * caller_count,
        "caller_count": caller_count,
        "target_per_caller": target_per_caller,
        "daily_target": daily_target,
        "target_progress_pct": round(kpi["completion_percentage"], 1),
        "contact_rate_pct": round(kpi["contact_rate"], 1),
        "queue_counts": {
            item["queue_type"]: item["count"]
            for item in db.execute(
                """SELECT queue_type, COUNT(*) AS count FROM collection_call_tasks
                   WHERE task_date=? GROUP BY queue_type""",
                (task_date,),
            ).fetchall()
        },
    }


def pending_manager_decisions(db) -> dict:
    """What is sitting in the management exception queue right now."""
    approvals = db.execute(
        "SELECT COUNT(*) FROM ptp_agreements WHERE manager_approval_status='pending'"
    ).fetchone()[0]
    exceptions = db.execute(
        "SELECT COUNT(*) FROM collection_exceptions WHERE status='pending'"
    ).fetchone()[0]
    return {
        "ptp_approvals": approvals,
        "engine_exceptions": exceptions,
        "total": approvals + exceptions,
    }


# ── Management exception queue ────────────────────────────────────────────────

def _ptp_escalation_reason(row) -> str:
    """Why this arrangement is a manager's call and not reception's.

    Mirrors the tiers in ptp.py:ptp_create — anything the rule engine could
    not auto-approve lands here rather than being silently allowed or lost.
    """
    discount = float(row["discount_pct"] or 0)
    if discount > 0 and not row["discount_basis"]:
        return f"{discount:.0f}% discount with no qualifying DebiCheck or upfront-cash basis"
    if discount > 0 and row["discount_basis"] == "query_approval":
        return f"{discount:.0f}% discount approved by a manager on the linked query"
    if discount > 0:
        return f"{discount:.0f}% discount on the {row['discount_basis']} tier"
    return "Non-standard arrangement referred by reception"


def _pending_ptp_approvals(db):
    return db.execute(
        """SELECT p.*, m.member_ref, m.first_name, m.last_name,
                  u.full_name AS requested_by_name
           FROM ptp_agreements p
           JOIN members m ON m.id = p.member_id
           LEFT JOIN users u ON u.id = p.created_by
           WHERE p.manager_approval_status = 'pending'
           ORDER BY p.created_at, p.id"""
    ).fetchall()


def _open_engine_exceptions(db):
    return db.execute(
        """SELECT e.*, m.member_ref, m.first_name, m.last_name,
                  u.full_name AS raised_by_name
           FROM collection_exceptions e
           JOIN members m ON m.id = e.member_id
           LEFT JOIN users u ON u.id = e.created_by
           WHERE e.status = 'pending'
           ORDER BY e.created_at, e.id"""
    ).fetchall()


@collections_bp.get("/exceptions")
@roles_required("admin", "manager")
def exception_queue():
    """Everything waiting on a manager decision, in one place.

    Rule §7: a non-standard request at three or more months owing cannot be
    silently overridden by reception. Until this screen existed the rule
    engine produced these escalations and nobody could see them without
    already knowing which member to open.
    """
    db = get_db()
    ptp_rows = [dict(row) for row in _pending_ptp_approvals(db)]
    for row in ptp_rows:
        row["escalation_reason"] = _ptp_escalation_reason(row)
    return render_template(
        "collections/exceptions.html",
        ptp_approvals=ptp_rows,
        engine_exceptions=_open_engine_exceptions(db),
    )


@collections_bp.post("/exceptions/<int:exception_id>/decide")
@roles_required("admin", "manager")
def exception_decide(exception_id: int):
    db = get_db()
    decision = (request.form.get("decision") or "").strip().lower()
    notes = (request.form.get("notes") or "").strip()
    if decision not in {"approved", "declined", "returned"}:
        flash("A manager decision must be approved, declined or returned.", "error")
        return redirect(url_for("collections.exception_queue"))
    if decision in {"declined", "returned"} and not notes:
        flash(f"A {decision} decision requires a reason.", "error")
        return redirect(url_for("collections.exception_queue"))
    if decision == "returned":
        row = return_collection_exception(db, exception_id, session.get("user_id"), notes)
        if row is None:
            flash("That exception has already been decided.", "warning")
        else:
            member_activity(db, row["member_id"], f"Collections exception #{exception_id} returned for information: {notes[:120]}")
            db.commit()
            flash("Exception returned for more information.", "success")
        return redirect(url_for("collections.exception_queue"))

    row = decide_collection_exception(db, exception_id, decision, session.get("user_id"), notes)
    if row is None:
        flash("That exception has already been decided.", "warning")
        return redirect(url_for("collections.exception_queue"))

    if decision == "approved" and grant_exception_access(db, row["member_id"]):
        member_activity(db, row["member_id"], "Access granted under manager exception until the promise date.")
    member_activity(
        db, row["member_id"],
        f"Collections exception #{exception_id} {decision} by manager. "
        + (f"Condition/reason: {notes[:120]}" if notes else "No condition recorded."),
    )
    db.commit()
    flash(f"Exception {decision}.", "success")
    return redirect(url_for("collections.exception_queue"))
