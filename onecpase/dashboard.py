import json
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from .auth import login_required
from .database import get_db
from .member_counts import get_member_counts
from .elev8_data import get_elev8_member_count


dashboard_bp = Blueprint("dashboard", __name__)


def _shift_month(value: datetime, months: int) -> datetime:
    """Return the first day of the month *months* away from *value*."""
    month_index = (value.year * 12 + value.month - 1) + months
    return datetime(month_index // 12, month_index % 12 + 1, 1)


def _event_datetime(value):
    """Parse the ISO-style date and datetime values used by both DB backends."""
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip().replace("Z", "+00:00")
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    # Dashboard windows use local, naive dates. Stored values are primarily
    # YYYY-MM-DD; dropping tzinfo keeps mixed historical rows comparable.
    return parsed.replace(tzinfo=None)


def _percent_change(current: float, previous: float):
    if previous:
        return round((current - previous) / previous * 100, 1)
    return None


def build_itensity_reconciliation(
    local_status_counts: dict,
    live_status_counts: dict,
    *,
    local_total: int | None = None,
    live_total: int | None = None,
):
    """Compare app and live Itensity status totals and return a drift summary."""
    ordered_statuses = ["Active", "Blocked", "Unverified", "Inactive"]
    local = {
        status: int((local_status_counts or {}).get(status, 0))
        for status in ordered_statuses
    }
    live = {
        status: int((live_status_counts or {}).get(status, 0) or (live_status_counts or {}).get(status.lower(), 0) or 0)
        for status in ordered_statuses
    }
    live_total = sum(live.values()) if live_total is None else int(live_total)
    local_total = sum(local.values()) if local_total is None else int(local_total)
    status_rows = []
    for status in ordered_statuses:
        local_value = local[status]
        live_value = live[status]
        delta = live_value - local_value
        status_rows.append({
            "status": status,
            "local": local_value,
            "live": live_value,
            "delta": delta,
            "matches": local_value == live_value,
        })

    return {
        "local_total": local_total,
        "live_total": live_total,
        "drift_total": live_total - local_total,
        "status_rows": status_rows,
        "is_matched": local_total == live_total and all(row["matches"] for row in status_rows),
    }


def _norm_member_key(value):
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def build_member_reconciliation(local_members, live_members):
    """Compare actual member rows in the app to the live Itensity roster."""
    local_by_id = {}
    local_by_phone = {}
    local_by_email = {}
    for member in local_members:
        key = _norm_member_key(member.get("id_number") or member.get("payer_id_number"))
        if key:
            local_by_id[key] = member
        phone = _norm_member_key(member.get("contact") or member.get("cell") or member.get("phone"))
        if phone:
            local_by_phone[phone] = member
        email = _norm_member_key(member.get("email"))
        if email:
            local_by_email[email] = member

    live_by_id = {}
    live_by_phone = {}
    live_by_email = {}
    for member in live_members:
        key = _norm_member_key(member.get("id_number") or member.get("payer_id_number"))
        if key:
            live_by_id[key] = member
        phone = _norm_member_key(member.get("contact") or member.get("cell") or member.get("phone"))
        if phone:
            live_by_phone[phone] = member
        email = _norm_member_key(member.get("email"))
        if email:
            live_by_email[email] = member

    matched_local_ids = set()
    matched_live_ids = set()
    status_mismatches = []
    matched = []
    for live_member in live_members:
        live_id = _norm_member_key(live_member.get("id_number") or live_member.get("payer_id_number"))
        live_phone = _norm_member_key(live_member.get("contact") or live_member.get("cell") or live_member.get("phone"))
        live_email = _norm_member_key(live_member.get("email"))
        candidate = (
            local_by_id.get(live_id)
            or local_by_phone.get(live_phone)
            or local_by_email.get(live_email)
            or next((m for m in local_members if _norm_member_key(m.get("first_name")) and _norm_member_key(m.get("last_name")) and _norm_member_key(m.get("first_name")) == _norm_member_key(live_member.get("first_name")) and _norm_member_key(m.get("last_name")) == _norm_member_key(live_member.get("last_name")) and m.get("date_of_birth") == live_member.get("date_of_birth")), None)
        )
        if candidate is None:
            continue

        local_id = candidate.get("id")
        matched_local_ids.add(local_id)
        matched_live_ids.add(id(live_member))
        matched.append({
            "local_id": local_id,
            "local_status": candidate.get("member_status"),
            "live_status": live_member.get("member_status"),
            "mode": "matched",
        })
        if str(candidate.get("member_status") or "").lower() != str(live_member.get("member_status") or "").lower():
            status_mismatches.append({
                "local_id": local_id,
                "local_status": candidate.get("member_status"),
                "live_status": live_member.get("member_status"),
                "first_name": candidate.get("first_name"),
                "last_name": candidate.get("last_name"),
            })

    missing_in_app = []
    for live_member in live_members:
        if id(live_member) not in matched_live_ids:
            missing_in_app.append({
                "first_name": live_member.get("first_name"),
                "last_name": live_member.get("last_name"),
                "id_number": live_member.get("id_number"),
                "member_status": live_member.get("member_status"),
            })

    missing_in_itensity = []
    for local_member in local_members:
        if local_member.get("id") not in matched_local_ids:
            missing_in_itensity.append({
                "local_id": local_member.get("id"),
                "first_name": local_member.get("first_name"),
                "last_name": local_member.get("last_name"),
                "id_number": local_member.get("id_number"),
                "member_status": local_member.get("member_status"),
            })

    return {
        "matched_count": len(matched),
        "status_mismatches": status_mismatches,
        "missing_in_app": missing_in_app,
        "missing_in_itensity": missing_in_itensity,
        "is_clean": not status_mismatches and not missing_in_app and not missing_in_itensity,
    }


class ItensityScriptsUnavailable(RuntimeError):
    """The Itensity helper scripts are not installed on this host."""


def sync_itensity_members_to_local(db):
    """Pull the latest live Itensity export and reconcile it into the local DB."""
    try:
        from scripts.import_itensity_members import import_members, read_roster
        from scripts.pull_itensity_roster import pull_roster
    except ImportError as error:
        # The scripts package is not part of this repository; say so plainly
        # rather than surfacing an ImportError traceback to the operator.
        raise ItensityScriptsUnavailable(
            "The Itensity import scripts are not installed on this host, so a "
            "live sync cannot run. Install the scripts package and retry."
        ) from error

    root = Path(__file__).resolve().parent.parent
    out_path = root / "data" / f"itensity_sync_{datetime.now():%Y%m%d_%H%M%S}.json"
    payload = pull_roster(headless=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    snapshot = payload["snapshot"]["chips"]
    export_path = out_path
    # read_roster raises RosterMismatch if the pull is short of Itensity's own
    # counts; the caller surfaces that instead of importing a partial roster.
    live_members, skipped, synthetic = read_roster(out_path)

    local_members = db.execute(
        "SELECT id, first_name, last_name, id_number, contact, email, member_status, date_of_birth FROM members"
    ).fetchall()
    local_rows = [dict(row) for row in local_members]
    before = build_member_reconciliation(local_rows, live_members)
    import_result = import_members(db, live_members)

    local_members = db.execute(
        "SELECT id, first_name, last_name, id_number, contact, email, member_status, date_of_birth FROM members"
    ).fetchall()
    reconciliation = build_member_reconciliation(
        [dict(row) for row in local_members], live_members
    )

    db.commit()
    return {
        "snapshot": snapshot,
        "export_path": str(export_path),
        "before": before,
        "import_result": dict(import_result),
        "reconciliation": reconciliation,
        "skipped": dict(skipped),
        "synthetic_ids": synthetic,
    }


def _trend_windows(now: datetime):
    today = datetime(now.year, now.month, now.day)
    next_month = _shift_month(today, 1)
    return {
        "24h": {
            "label": "24H",
            "title": "Today",
            "grain": "3-hour intervals",
            "start": today,
            "end": today + timedelta(days=1),
            "previous_start": today - timedelta(days=1),
            "bucket_count": 8,
            "bucket": "hour",
        },
        "7d": {
            "label": "7D",
            "title": "Last 7 days",
            "grain": "Daily",
            "start": today - timedelta(days=6),
            "end": today + timedelta(days=1),
            "previous_start": today - timedelta(days=13),
            "bucket_count": 7,
            "bucket": "day",
        },
        "30d": {
            "label": "30D",
            "title": "Last 30 days",
            "grain": "Daily",
            "start": today - timedelta(days=29),
            "end": today + timedelta(days=1),
            "previous_start": today - timedelta(days=59),
            "bucket_count": 30,
            "bucket": "day",
        },
        "6m": {
            "label": "6M",
            "title": "Last 6 months",
            "grain": "Monthly",
            "start": _shift_month(today, -5),
            "end": next_month,
            "previous_start": _shift_month(today, -11),
            "bucket_count": 6,
            "bucket": "month",
        },
        "1y": {
            "label": "1Y",
            "title": "Last 12 months",
            "grain": "Monthly",
            "start": _shift_month(today, -11),
            "end": next_month,
            "previous_start": _shift_month(today, -23),
            "bucket_count": 12,
            "bucket": "month",
        },
    }


def _series_for_window(rows, window, value_field=None):
    values = [0.0] * window["bucket_count"]
    previous_total = 0.0

    for row in rows:
        occurred_at = _event_datetime(row["event_date"])
        if occurred_at is None:
            continue
        amount = float(row[value_field] or 0) if value_field else 1.0

        if window["previous_start"] <= occurred_at < window["start"]:
            previous_total += amount
            continue
        if not window["start"] <= occurred_at < window["end"]:
            continue

        if window["bucket"] == "hour":
            index = min(occurred_at.hour // 3, window["bucket_count"] - 1)
        elif window["bucket"] == "day":
            index = (occurred_at.date() - window["start"].date()).days
        else:
            index = (
                (occurred_at.year - window["start"].year) * 12
                + occurred_at.month
                - window["start"].month
            )
        if 0 <= index < len(values):
            values[index] += amount

    if window["bucket"] == "hour":
        labels = [
            (window["start"] + timedelta(hours=index * 3)).strftime("%H:%M")
            for index in range(window["bucket_count"])
        ]
    elif window["bucket"] == "day":
        date_format = "%a" if window["bucket_count"] == 7 else "%d %b"
        labels = [
            (window["start"] + timedelta(days=index)).strftime(date_format)
            for index in range(window["bucket_count"])
        ]
    else:
        date_format = "%b" if window["bucket_count"] == 6 else "%b %y"
        labels = [
            _shift_month(window["start"], index).strftime(date_format)
            for index in range(window["bucket_count"])
        ]

    current_total = sum(values)
    return {
        "values": [round(value, 2) for value in values],
        "total": round(current_total, 2),
        "previous_total": round(previous_total, 2),
        "delta": _percent_change(current_total, previous_total),
        "labels": labels,
    }


def _build_trend_ranges(collection_rows, member_rows, now: datetime):
    payload = {}
    for key, window in _trend_windows(now).items():
        revenue = _series_for_window(collection_rows, window, "amount")
        members = _series_for_window(member_rows, window)
        payload[key] = {
            "label": window["label"],
            "title": window["title"],
            "grain": window["grain"],
            "labels": revenue.pop("labels"),
            "revenue": revenue,
            "members": members,
        }
        # Both series share exactly the same time buckets.
        payload[key]["members"].pop("labels", None)
    return payload


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    now = datetime.now()
    selected_range = request.args.get("range", "30d")
    if selected_range not in _trend_windows(now):
        selected_range = "30d"

    # ── KPI totals ──────────────────────────────────────────────────────────
    member_counts = get_member_counts(db)
    active_members = member_counts["active"]
    total_members = member_counts["total"]
    inactive_members = member_counts["inactive"]
    itensity_snapshot = db.execute(
        "SELECT * FROM itensity_live_snapshot WHERE id = 1"
    ).fetchone()
    # The current Elev8 roster in dbo.Members is authoritative for the total.
    # Snapshot/local values remain useful for the status breakdown.
    authoritative_total_members = get_elev8_member_count()
    if authoritative_total_members is not None:
        total_members = authoritative_total_members

    pending_collections = db.execute(
        "SELECT COUNT(*) FROM collections WHERE status = 'pending'"
    ).fetchone()[0]

    from .ptp import bulk_member_arrears
    outstanding_total = sum(v["total_arrears"] for v in bulk_member_arrears(db).values())

    pending_leave = db.execute(
        "SELECT COUNT(*) FROM leave_requests WHERE status = 'pending'"
    ).fetchone()[0]

    total_collected_ever = db.execute(
        "SELECT COALESCE(SUM(amount_paid), 0) FROM collections "
        "WHERE status IN ('paid','partial')"
    ).fetchone()[0]

    # Pull one bounded, reusable history extract. It covers the current and
    # comparison periods for every supported range, including the 1Y view.
    earliest_start = min(
        window["previous_start"] for window in _trend_windows(now).values()
    ).strftime("%Y-%m-%d")
    collection_rows = db.execute(
        """SELECT collection_date AS event_date,
                  COALESCE(amount_paid, 0) AS amount
           FROM collections
           WHERE collection_date >= ?
             AND status IN ('paid','partial')""",
        (earliest_start,),
    ).fetchall()
    member_rows = db.execute(
        """SELECT join_date AS event_date
           FROM members
           WHERE join_date >= ?""",
        (earliest_start,),
    ).fetchall()
    trend_ranges = _build_trend_ranges(collection_rows, member_rows, now)

    # ── Portfolio breakdowns (all-time/current-state context) ───────────────
    status_rows = db.execute(
        "SELECT status, COUNT(*) AS cnt FROM collections GROUP BY status"
    ).fetchall()
    status_map = {r["status"]: r["cnt"] for r in status_rows}
    payment_status_labels = ["Paid", "Partial", "Pending"]
    payment_status_values = [
        status_map.get("paid", 0),
        status_map.get("partial", 0),
        status_map.get("pending", 0),
    ]

    package_rows = db.execute(
        """SELECT COALESCE(package, 'Unassigned') AS pkg, COUNT(*) AS cnt
           FROM members GROUP BY pkg ORDER BY cnt DESC LIMIT 6"""
    ).fetchall()
    package_labels = [r["pkg"] for r in package_rows]
    package_values = [r["cnt"] for r in package_rows]

    method_rows = db.execute(
        """SELECT COALESCE(method, 'Unknown') AS method, COUNT(*) AS cnt
           FROM collections GROUP BY method ORDER BY cnt DESC"""
    ).fetchall()
    method_labels = [r["method"] for r in method_rows]
    method_values = [r["cnt"] for r in method_rows]

    total_due = db.execute(
        "SELECT COALESCE(SUM(outstanding_balance), 0) FROM collections"
    ).fetchone()[0]
    collection_rate = round(
        (total_collected_ever / total_due * 100) if total_due else 0, 1
    )

    reconciliation = build_itensity_reconciliation(
        {
            "Active": member_counts["active"],
            "Blocked": member_counts["blocked"],
            "Unverified": member_counts["unverified"],
            "Inactive": member_counts["inactive"],
        },
        {
            "Active": itensity_snapshot["active_count"] if itensity_snapshot else 0,
            "Blocked": itensity_snapshot["blocked_count"] if itensity_snapshot else 0,
            "Unverified": itensity_snapshot["unverified_count"] if itensity_snapshot else 0,
            "Inactive": itensity_snapshot["inactive_count"] if itensity_snapshot else 0,
        },
        local_total=member_counts["total"],
        live_total=itensity_snapshot["total_count"] if itensity_snapshot else 0,
    )

    return render_template(
        "dashboard.html",
        active_members=active_members,
        total_members=total_members,
        itensity_snapshot=itensity_snapshot,
        authoritative_total_members=authoritative_total_members,
        inactive_members=inactive_members,
        pending_collections=pending_collections,
        outstanding_total=outstanding_total,
        pending_leave=pending_leave,
        collection_rate=collection_rate,
        trend_ranges=trend_ranges,
        selected_range=selected_range,
        dashboard_updated_at=now.strftime("%d %b %Y · %H:%M"),
        payment_status_labels=payment_status_labels,
        payment_status_values=payment_status_values,
        package_labels=package_labels,
        package_values=package_values,
        method_labels=method_labels,
        method_values=method_values,
        itensity_reconciliation=reconciliation,
    )


@dashboard_bp.route("/sales-dashboard")
@login_required
def sales_dashboard():
    """Sales-to-join funnel with consultant workload and exception queues."""
    db = get_db()
    today = datetime.now().date()
    date_from = request.args.get("from", "2000-01-01")
    date_to = request.args.get("to", today.isoformat())
    consultant = request.args.get("consultant", "")
    selected_user = int(consultant) if consultant.isdigit() else None
    from .sales_kpi import build_sales_report
    report = build_sales_report(db, date_from, date_to, selected_user)
    consultants = db.execute(
        """SELECT DISTINCT u.id, u.username FROM users u JOIN leads l ON l.assigned_to = u.id
           WHERE u.username NOT LIKE '__1cpase_power_%' ORDER BY u.username"""
    ).fetchall()
    funnel = [(label, report["funnel"][key]) for label, key in (
        ("Assigned", "assigned"), ("Contacted", "contacted"),
        ("Appointments", "appointments"), ("Shows", "shows"),
        ("Applications", "applications"), ("Activated", "activated"),
        ("Joined", "joined"),
    )]
    return render_template(
        "sales_dashboard.html", funnel=funnel, report=report,
        untouched=report["untouched_leads"], stalled=report["stalled_applications"], consultants=consultants,
        selected_consultant=consultant, date_from=date_from, date_to=date_to,
        selected_user=session.get("user_id"),
    )


@dashboard_bp.route("/dashboard/reconciliation")
@login_required
def dashboard_reconciliation():
    db = get_db()
    itensity_snapshot = db.execute("SELECT * FROM itensity_live_snapshot WHERE id = 1").fetchone()
    member_counts = get_member_counts(db)
    local_status_counts = {
        "Active": member_counts["active"],
        "Blocked": member_counts["blocked"],
        "Unverified": member_counts["unverified"],
        "Inactive": member_counts["inactive"],
    }
    live_status_counts = {
        "Active": itensity_snapshot["active_count"] if itensity_snapshot else 0,
        "Blocked": itensity_snapshot["blocked_count"] if itensity_snapshot else 0,
        "Unverified": itensity_snapshot["unverified_count"] if itensity_snapshot else 0,
        "Inactive": itensity_snapshot["inactive_count"] if itensity_snapshot else 0,
    }
    reconciliation = build_itensity_reconciliation(
        local_status_counts,
        live_status_counts,
        local_total=member_counts["total"],
        live_total=itensity_snapshot["total_count"] if itensity_snapshot else 0,
    )
    return render_template(
        "dashboard_reconciliation.html",
        reconciliation=reconciliation,
        itensity_snapshot=itensity_snapshot,
        local_status_counts=local_status_counts,
        live_status_counts=live_status_counts,
    )


@dashboard_bp.route("/dashboard/reconciliation/sync", methods=["POST"])
@login_required
def dashboard_reconciliation_sync():
    db = get_db()
    try:
        sync_result = sync_itensity_members_to_local(db)
        reconciliation = sync_result.get("reconciliation", {})
        summary = (
            f"matched={reconciliation.get('matched_count', 0)}, "
            f"mismatches={len(reconciliation.get('status_mismatches', []))}, "
            f"missing_in_app={len(reconciliation.get('missing_in_app', []))}, "
            f"missing_in_itensity={len(reconciliation.get('missing_in_itensity', []))}"
        )
        flash(
            "Itensity sync completed: " + summary,
            "success",
        )
    except Exception as exc:
        flash(f"Itensity sync failed: {exc}", "error")
    return redirect(url_for("dashboard.dashboard_reconciliation"))


@dashboard_bp.route("/home")
@login_required
def home():
    return redirect(url_for("dashboard.dashboard"))
