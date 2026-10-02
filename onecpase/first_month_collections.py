"""A sales consultant's own first-month failed debits.

A new member whose first debit fails stays with the consultant who signed them
until the account reaches the transfer threshold, when the same case moves to
Reception (see collections_engine.apply_case_ownership). This is the
consultant's working list; it lives in Phase 1 because it is part of the sale.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from .auth import login_required
from .collections import CALL_OUTCOMES
from .collections_engine import (
    OWNER_SALES,
    log_collection_communication,
    transfer_due_sales_cases,
)
from .database import get_db, member_activity

first_month_bp = Blueprint(
    "first_month_collections", __name__, url_prefix="/sales/first-month-failed-debits"
)

MANAGER_ROLES = {"admin", "manager"}
CHANNELS = ("CALL", "WHATSAPP", "SMS", "EMAIL")
OUTCOME_LABELS = {key: label for key, label, _ok in CALL_OUTCOMES}
OPEN_STATUSES = ("open", "active", "pending")


def _is_manager() -> bool:
    return session.get("role") in MANAGER_ROLES


@first_month_bp.get("/")
@login_required
def index():
    from .ptp import bulk_member_arrears

    db = get_db()
    transfer_due_sales_cases(db)
    placeholders = ",".join("?" for _ in OPEN_STATUSES)
    params: list = [OWNER_SALES, *OPEN_STATUSES]
    scope = ""
    if not _is_manager():
        scope = " AND c.owner_staff_id=?"
        params.append(session.get("user_id"))
    cases = db.execute(
        f"""SELECT c.id, c.member_id, c.failed_debit_date, c.failure_reason, c.assigned_at,
                   m.first_name, m.last_name, m.contact, m.join_date,
                   u.full_name AS consultant_name,
                   (SELECT MAX(cc.created_at) FROM collection_communications cc
                     WHERE cc.case_id=c.id) AS last_contact_at,
                   (SELECT cc.outcome FROM collection_communications cc
                     WHERE cc.case_id=c.id ORDER BY cc.id DESC LIMIT 1) AS last_outcome
            FROM collections_cases c
            JOIN members m ON m.id=c.member_id
            LEFT JOIN users u ON u.id=c.owner_staff_id
            WHERE c.owner_type=? AND c.status IN ({placeholders}){scope}
            ORDER BY c.failed_debit_date, c.id""",
        params,
    ).fetchall()
    arrears = bulk_member_arrears(db, [row["member_id"] for row in cases]) if cases else {}
    return render_template(
        "collections/first_month.html",
        cases=cases, arrears=arrears, manager_view=_is_manager(),
        outcomes=CALL_OUTCOMES, channels=CHANNELS, outcome_labels=OUTCOME_LABELS,
    )


@first_month_bp.post("/<int:case_id>/contact")
@login_required
def record_contact(case_id: int):
    db = get_db()
    case = db.execute(
        "SELECT id, member_id, owner_type, owner_staff_id FROM collections_cases WHERE id=?",
        (case_id,),
    ).fetchone()
    if case is None or case["owner_type"] != OWNER_SALES:
        flash("That case is no longer with Sales.", "warning")
        return redirect(url_for("first_month_collections.index"))
    if not _is_manager() and case["owner_staff_id"] != session.get("user_id"):
        flash("This case belongs to another consultant.", "error")
        return redirect(url_for("first_month_collections.index"))

    channel = request.form.get("channel", "CALL").strip().upper()
    outcome = request.form.get("outcome", "").strip()
    notes = request.form.get("notes", "").strip()
    if channel not in CHANNELS or outcome not in OUTCOME_LABELS:
        flash("Choose a channel and an outcome.", "error")
        return redirect(url_for("first_month_collections.index"))
    if outcome in {"refused", "disputed"} and not notes:
        flash("Add a note explaining the member's response.", "error")
        return redirect(url_for("first_month_collections.index"))

    log_collection_communication(
        db, case["member_id"], channel, case_id=case_id, outcome=outcome,
        notes=notes or None, created_by=session.get("user_id"),
    )
    member_activity(
        db, case["member_id"],
        f"First-month failed debit follow-up ({channel}): {OUTCOME_LABELS[outcome]}"
        + (f" — {notes}" if notes else ""),
        user_id=session.get("user_id"),
    )
    db.commit()
    flash("Contact recorded.", "success")
    return redirect(url_for("first_month_collections.index"))
