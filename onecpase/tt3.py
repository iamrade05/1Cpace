"""Signed TT3 (manual DebiCheck) forms: after upload, ask NuPay whether the mandate is live.

The form itself is only evidence. The application's DebiCheck gate still moves on
NuPay's own answer alone, through the same sync the daily payment job uses.
"""
import sqlite3
import threading
from datetime import datetime, timedelta

from flask import current_app, session

from .database import member_activity
from .debicheck import (
    member_mandate_on_nupay,
    normalise_debicheck_status,
    sync_debicheck_to_application,
)
from .encryption import decrypt_member

CHECKING = "checking"
# A restart can kill a check part-way, so a "checking" row older than this no longer blocks a new one.
CHECK_STALE_MINUTES = 15


def check_in_progress(db, member_id: int) -> bool:
    latest = db.execute(
        "SELECT state, checked_at FROM nupay_mandate_checks WHERE member_id=? ORDER BY id DESC LIMIT 1",
        (member_id,),
    ).fetchone()
    if not latest or latest["state"] != CHECKING:
        return False
    try:
        started = datetime.fromisoformat(str(latest["checked_at"]))
    except ValueError:
        return False
    return datetime.now() - started < timedelta(minutes=CHECK_STALE_MINUTES)


def start_check(db, member_id: int) -> bool:
    """Start a background NuPay check. Returns False when one is already running.

    The report takes about a minute to read and waits its turn for the browser
    lock, so it cannot run on the request thread."""
    if check_in_progress(db, member_id):
        return False
    user_id = session.get("user_id")
    check_id = db.execute(
        """INSERT INTO nupay_mandate_checks (member_id, state, message, checked_by)
           VALUES (?, ?, ?, ?)""",
        (member_id, CHECKING, "Checking NuPay after the TT3 upload.", user_id),
    ).lastrowid
    db.commit()
    db_path = db.execute("PRAGMA database_list").fetchone()["file"]
    _spawn(current_app._get_current_object(), db_path, check_id, member_id, user_id)
    return True


def _spawn(app, db_path: str, check_id: int, member_id: int, user_id) -> None:
    threading.Thread(
        target=run_check, args=(app, db_path, check_id, member_id, user_id), daemon=True,
    ).start()


def _apply_to_member(db, member_id: int, row: dict, result: dict, actor_id) -> str:
    """Bring this member's mandate in line with their NuPay row. Returns the message to record."""
    from .nupay_sync import sync_from_report
    from .ptp_grant import on_mandate_status_changed
    from .query_forms import refresh_banking_workflows

    # The one row keeps the sync's own rules on who a mandate may be recorded against.
    sync_from_report(db, [row], apply=True, import_missing=True, actor_id=actor_id)
    latest = db.execute(
        "SELECT id, status FROM debicheck_mandates WHERE member_id=? ORDER BY id DESC LIMIT 1", (member_id,),
    ).fetchone()
    if latest:
        sync_debicheck_to_application(db, latest["id"], actor_id=actor_id)
        on_mandate_status_changed(db, latest["id"])
        refresh_banking_workflows(db, latest["id"])
    linked = latest and normalise_debicheck_status(latest["status"]) == "approved"
    if result["state"] == "active" and not linked:
        return result["message"] + " It could not be linked to this member automatically; review it under DebiCheck sync."
    return result["message"]


def run_check(app, db_path: str, check_id: int, member_id: int, user_id) -> None:
    """Runs off the request thread, so it opens its own connection."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with app.app_context():
            member = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
            if member is None:
                return
            try:
                result, row = member_mandate_on_nupay(decrypt_member(member))
                message = _apply_to_member(conn, member_id, row, result, user_id) if row else result["message"]
            except Exception:
                conn.rollback()
                app.logger.exception("TT3 NuPay check failed for member %s", member_id)
                result = {"state": "error", "status": None, "contract_reference": None}
                message = "The NuPay check could not finish. Please try again."
            conn.execute(
                """UPDATE nupay_mandate_checks
                   SET state=?, status=?, contract_reference=?, message=?, checked_at=datetime('now','localtime')
                   WHERE id=?""",
                (result["state"], result["status"], result["contract_reference"], message, check_id),
            )
            member_activity(conn, member_id, "TT3 NuPay check: " + message, user_id=user_id)
            conn.commit()
    except Exception:
        app.logger.exception("TT3 NuPay check could not record its result for member %s", member_id)
    finally:
        conn.close()
