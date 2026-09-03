"""Background job runner — replaces the manual "Run Automations" button and
the hand-run Itensity import scripts with jobs that fire on a clock.

APScheduler, in-process: this is a single-server, SQLite-per-tenant Flask
app with no broker/worker infrastructure today, so a BackgroundScheduler
thread inside the existing process is the right tradeoff over Celery (which
would need a Redis/RabbitMQ broker and separate worker process(es) stood up
and kept running just for three small jobs). Tradeoff accepted: jobs only
run while this process is up, and only from one process — see the
WERKZEUG_RUN_MAIN guard below.

Each job opens its own plain sqlite3 connection per tenant (no Flask `g` /
request context available in a background thread) — the same pattern
turnstile.py already uses for its monitor thread.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


# ── Tenant lookup ──────────────────────────────────────────────────────────

def _repo_root(app) -> Path:
    return Path(app.root_path).parent


def _active_tenants(app) -> list[sqlite3.Row]:
    """Every active tenant, read directly from platform.db (no request
    context to route through get_platform_db())."""
    path = Path(app.config["PLATFORM_DATABASE_PATH"])
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM tenants WHERE active = 1").fetchall()
    finally:
        conn.close()


def _find_tenant(app, slug: str) -> sqlite3.Row | None:
    for tenant in _active_tenants(app):
        if tenant["slug"] == slug:
            return tenant
    return None


def _backup_before_write(root: Path, db_path: str, label: str) -> Path:
    """Snapshot *db_path* before a scheduled write — same safety net the
    manual import scripts take before --commit."""
    backup_dir = root / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{label}_{stamp}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return backup_path


# ── Job 1: PTP / collections automations ────────────────────────────────────

def _job_ptp_automations(app) -> None:
    from .ptp import _run_collections_automations
    from .collections import _ensure_daily_call_tasks

    today = date.today().isoformat()
    for tenant in _active_tenants(app):
        conn = sqlite3.connect(tenant["db_path"])
        conn.row_factory = sqlite3.Row
        try:
            counts = _run_collections_automations(conn, today)
            cycle = _ensure_daily_call_tasks(conn, date.today())
            logger.info(
                "[scheduler] PTP/collections automations for %s: %s; call-cycle=%s",
                tenant["name"], counts, cycle,
            )
        except Exception:
            logger.exception(
                "[scheduler] PTP automations failed for tenant %r", tenant["slug"]
            )
        finally:
            conn.close()


# ── Job 1b: frequent collections workflow enforcement ─────────────────────

def _job_collections_workflow(app) -> None:
    """Continuously enforce collections state and build the daily work queue.

    This deliberately does not depend on a receptionist opening the Collections
    screen. Failed debits, arrears thresholds, access decisions, PTP expiry,
    and today's call workload are maintained in the background.
    """
    from .ptp import _run_collections_automations
    from .collections import _ensure_daily_call_tasks

    today = date.today()
    for tenant in _active_tenants(app):
        conn = sqlite3.connect(tenant["db_path"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            counts = _run_collections_automations(conn, today.isoformat())
            cycle = _ensure_daily_call_tasks(conn, today)
            logger.info(
                "[scheduler] collections workflow for %s: %s; cycle=%s",
                tenant["name"], counts, cycle,
            )
        except Exception:
            logger.exception(
                "[scheduler] collections workflow failed for tenant %r", tenant["slug"]
            )
        finally:
            conn.close()


# ── Job 1c: sales decision follow-up enforcement ───────────────────────────

def _job_sales_decision_followups(app) -> None:
    """Enforce the five-working-day Decision Follow-Up rule.

    When a lead has remained in DECISION_FOLLOW_UP for five working days,
    create a due follow-up activity and move the operational next-action date
    to today. The lead is not silently advanced to an application stage; a
    consultant must complete the decision/application action.
    """
    from .sales_pipeline import current_sales_stage
    from .leads import _add_activity, _add_workdays

    now = datetime.now()
    for tenant in _active_tenants(app):
        conn = sqlite3.connect(tenant["db_path"])
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT * FROM leads WHERE sales_stage='DECISION_FOLLOW_UP'"""
            ).fetchall()
            changed = 0
            for lead in rows:
                last = conn.execute(
                    """SELECT changed_at FROM sales_stage_history
                       WHERE lead_id=? AND to_stage='DECISION_FOLLOW_UP'
                       ORDER BY changed_at DESC, id DESC LIMIT 1""",
                    (lead["id"],),
                ).fetchone()
                if not last or not last["changed_at"]:
                    continue
                try:
                    started = datetime.fromisoformat(str(last["changed_at"]).replace("Z", ""))
                except ValueError:
                    continue
                due = _add_workdays(started.date(), 5)
                if now.date() < due:
                    continue
                existing = conn.execute(
                    """SELECT 1 FROM lead_activities
                       WHERE lead_id=? AND activity_type='follow_up'
                         AND next_action='Decision Follow-Up Due'
                         AND occurred_at >= ? LIMIT 1""",
                    (lead["id"], started.isoformat(sep=" ")),
                ).fetchone()
                if not existing:
                    _add_activity(conn, lead["id"], "follow_up", status="pending",
                                  notes="System-generated: five-working-day decision follow-up is due.",
                                  next_action="Decision Follow-Up Due", follow_up_at=now.isoformat(timespec="seconds", sep=" "))
                    conn.execute(
                        "UPDATE leads SET next_action=?, next_action_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
                        ("Decision Follow-Up Due", lead["id"]),
                    )
                    changed += 1
            conn.commit()
            logger.info("[scheduler] decision follow-ups for %s: %s created", tenant["name"], changed)
        except Exception:
            conn.rollback()
            logger.exception("[scheduler] decision follow-up enforcement failed for %s", tenant["slug"])
        finally:
            conn.close()


# ── Job 2: Itensity member export pull + import ─────────────────────────────

def _job_itensity_member_import(app) -> None:
    tenant = _find_tenant(app, app.config["ITENSITY_TENANT_SLUG"])
    if tenant is None:
        logger.warning(
            "[scheduler] Itensity member import skipped — tenant %r not found",
            app.config["ITENSITY_TENANT_SLUG"],
        )
        return

    from scripts.pull_itensity_export import pull_dashboard_snapshot, pull_export
    from scripts.import_itensity_members import import_members, read_members

    root = _repo_root(app)
    out_path = root / "data" / f"itensity_export_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    try:
        snapshot = pull_dashboard_snapshot(headless=True)
    except Exception:
        logger.exception("[scheduler] Itensity dashboard snapshot failed")
        snapshot = None
    try:
        pull_export(out_path, headless=True)
    except Exception:
        logger.exception("[scheduler] Itensity export pull failed")
        return

    members, skipped = read_members(out_path)
    conn = sqlite3.connect(tenant["db_path"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if snapshot:
            conn.execute(
                """INSERT INTO itensity_live_snapshot
                   (id, active_count, blocked_count, unverified_count, inactive_count, total_count, fetched_at)
                   VALUES (1, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(id) DO UPDATE SET
                     active_count=excluded.active_count, blocked_count=excluded.blocked_count,
                     unverified_count=excluded.unverified_count, inactive_count=excluded.inactive_count,
                     total_count=excluded.total_count, fetched_at=excluded.fetched_at""",
                (snapshot["active"], snapshot["blocked"], snapshot["unverified"],
                 snapshot["inactive"], snapshot["total"]),
            )
        backup_path = _backup_before_write(
            root, tenant["db_path"], f"{tenant['slug']}_before_scheduled_member_import"
        )
        result = import_members(conn, members)
        conn.commit()
        logger.info(
            "[scheduler] Itensity member import for %s: %s (skipped=%s, backup=%s)",
            tenant["name"], dict(result), dict(skipped), backup_path,
        )
    except Exception:
        conn.rollback()
        logger.exception(
            "[scheduler] Itensity member import failed for %s", tenant["name"]
        )
    finally:
        conn.close()


# ── Job 3: Itensity transactions inbox sweep ─────────────────────────────────
#
# There's no automated puller for Itensity's transaction ledger yet (unlike
# the member Data Export, which pull_itensity_export.py already drives via
# Playwright) — see scripts/import_itensity_transactions.py's docstring.
# Until that exists, staff drop the CSV they export by hand from Itensity's
# ledger UI into data/itensity_transactions_inbox/, and this job picks it up
# and commits it on the next sweep instead of someone having to remember to
# run the script. Files are moved to data/itensity_transactions_processed/
# on success, or left in place (for retry / investigation) on failure.

def _job_itensity_transactions_sweep(app) -> None:
    tenant = _find_tenant(app, app.config["ITENSITY_TENANT_SLUG"])
    if tenant is None:
        return

    root = _repo_root(app)
    inbox = root / "data" / "itensity_transactions_inbox"
    processed = root / "data" / "itensity_transactions_processed"
    inbox.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(inbox.glob("*.csv"))
    if not csv_files:
        return

    try:
        from scripts.import_itensity_transactions import import_transactions, read_transactions
    except ImportError:
        logger.error("[scheduler] Itensity transaction importer is not present; transaction sweep disabled until the importer is installed.")
        return

    for csv_path in csv_files:
        conn = sqlite3.connect(tenant["db_path"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            rows = read_transactions(csv_path)
            backup_path = _backup_before_write(
                root, tenant["db_path"],
                f"{tenant['slug']}_before_scheduled_transaction_import",
            )
            result, unmatched_refs = import_transactions(conn, rows)
            conn.commit()
            logger.info(
                "[scheduler] Itensity transactions import (%s) for %s: %s "
                "(unmatched=%d, backup=%s)",
                csv_path.name, tenant["name"], dict(result),
                sum(unmatched_refs.values()), backup_path,
            )
            processed.mkdir(parents=True, exist_ok=True)
            csv_path.rename(processed / f"{datetime.now():%Y%m%d_%H%M%S}_{csv_path.name}")
        except Exception:
            conn.rollback()
            logger.exception(
                "[scheduler] Itensity transactions import failed for %s — "
                "left in inbox for retry", csv_path.name,
            )
        finally:
            conn.close()


# ── Wiring ───────────────────────────────────────────────────────────────────

def init_scheduler(app) -> None:
    """Start the background scheduler for this process, once."""
    global _scheduler
    if _scheduler is not None:
        return
    if not app.config.get("SCHEDULER_ENABLED") or app.testing:
        return
    if app.config.get("DATABASE_URL"):
        # Legacy single-tenant Postgres mode — the tenant loop these jobs
        # rely on is inert there; skip rather than run against nothing.
        return
    # The Werkzeug reloader re-executes this module in a parent watcher
    # process and a child worker process; only the child (WERKZEUG_RUN_MAIN
    # set) should actually own the scheduler, or every job runs twice.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    scheduler = BackgroundScheduler(timezone="Africa/Johannesburg")
    scheduler.add_job(
        lambda: _job_itensity_member_import(app),
        CronTrigger(hour=2, minute=0),
        id="itensity_member_import",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        lambda: _job_ptp_automations(app),
        CronTrigger(hour=2, minute=30),
        id="ptp_automations",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        lambda: _job_collections_workflow(app),
        IntervalTrigger(minutes=15),
        id="collections_workflow",
        replace_existing=True,
        misfire_grace_time=600,
        max_instances=1,
    )
    scheduler.add_job(
        lambda: _job_sales_decision_followups(app),
        IntervalTrigger(minutes=15),
        id="sales_decision_followups",
        replace_existing=True,
        misfire_grace_time=600,
        max_instances=1,
    )
    scheduler.add_job(
        lambda: _job_itensity_transactions_sweep(app),
        IntervalTrigger(minutes=15),
        id="itensity_transactions_sweep",
        replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.start()
    _scheduler = scheduler
    app.logger.info(
        "Scheduler started: itensity_member_import (02:00), "
        "ptp_automations (02:30), collections_workflow (every 15m), "
        "itensity_transactions_sweep (every 15m), sales_decision_followups (every 15m)"
    )
