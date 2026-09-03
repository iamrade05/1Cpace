import sqlite3
import threading

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from .auth import permission_required
from .database import get_db, audit_log, member_activity
from .encryption import decrypt, decrypt_member
from .integrations import push_member_to_itensity, update_member_in_itensity

applications_bp = Blueprint("applications", __name__, url_prefix="/applications")

# Same convention as queries.py's MANAGER_ROLES — admin counts as manager-or-
# above. Overriding an incomplete application straight to Itensity is a
# compliance call, not a data-entry one, so it's gated to this set rather
# than any authenticated staff member.
MANAGER_ROLES = {"admin", "manager"}

APPLICATION_STATUSES = [
    ("draft", "Draft"),
    ("awaiting_docs", "Awaiting Documents"),
    ("verification", "Verification in Progress"),
    ("manager_approval", "Manager Approval Required"),
    ("ready_to_push", "Ready to Push"),
    ("pushed", "Pushed / Submitted"),
    ("active", "Active Member"),
]

CONTRACT_STATUSES = ["pending", "signed", "failed"]
DEBIT_STATUSES = ["pending", "approved", "failed"]
BANK_STATUSES = ["not_uploaded", "uploaded", "analysed", "failed"]
ID_STATUSES = ["not_uploaded", "uploaded", "verified"]
POS_STATUSES = ["pending", "paid", "failed"]
GUARDIAN_STATUSES = ["not_required", "required", "approved", "rejected"]
MANAGER_STATUSES = ["not_required", "required", "approved", "declined"]
INCOME_OPTIONS = ["yes", "no", "unclear"]
INCOME_FREQUENCIES = ["weekly", "monthly", "irregular"]
RISK_RATINGS = ["low", "medium", "high"]

STATUS_COLORS = {
    "draft": "#6b7280",
    "awaiting_docs": "#f59e0b",
    "verification": "#3b82f6",
    "manager_approval": "#f97316",
    "ready_to_push": "#10b981",
    "pushed": "#8b5cf6",
    "active": "#059669",
}


def ensure_member_application(db, member_id: int, package: str | None = None, lead_id: int | None = None) -> int:
    """Return the member's application, creating its workflow when missing."""
    existing = db.execute(
        """SELECT id FROM membership_applications
           WHERE member_id = ?
           ORDER BY id DESC LIMIT 1""",
        (member_id,),
    ).fetchone()
    if not existing and lead_id:
        existing = db.execute(
            "SELECT id FROM membership_applications WHERE lead_id = ? ORDER BY id DESC LIMIT 1",
            (lead_id,),
        ).fetchone()
    if existing:
        aid = existing["id"]
    else:
        member = db.execute(
            "SELECT package, tariff, uploaded_by_id FROM members WHERE id = ?",
            (member_id,),
        ).fetchone()
        chosen_package = package or (member["package"] or member["tariff"] if member else "")
        cursor = db.execute(
            """INSERT INTO membership_applications
               (member_id, lead_id, package, application_status, created_by)
               VALUES (?, ?, ?, 'awaiting_docs', ?)""",
            (
                member_id, lead_id, chosen_package,
                session.get("user_id") or (member["uploaded_by_id"] if member else None),
            ),
        )
        aid = cursor.lastrowid

    db.execute(
        "INSERT OR IGNORE INTO compliance_checklists (application_id) VALUES (?)",
        (aid,),
    )
    return aid


def _sync_sales_stage_for_application(
    db, aid: int, status: str, actor_id: int | None = None, *, strict: bool = False
) -> bool:
    """Keep the canonical sales pipeline aligned with the application workflow.

    The sales pipeline remains authoritative for prospect progression; this
    bridge only advances it when the application state proves that milestone.
    """
    row = db.execute(
        "SELECT lead_id FROM membership_applications WHERE id = ?", (aid,)
    ).fetchone()
    if not row or not row["lead_id"]:
        return True

    from .sales_pipeline import current_sales_stage, transition_sales_stage
    lead = db.execute("SELECT * FROM leads WHERE id = ?", (row["lead_id"],)).fetchone()
    if not lead:
        return True

    target = {
        "awaiting_docs": "APPLICATION_STARTED",
        "verification": "APPLICATION_STARTED",
        "manager_approval": "APPLICATION_STARTED",
        "ready_to_push": "APPLICATION_SUBMITTED",
        "pushed": "APPLICATION_SUBMITTED",
        "active": "JOINED",
    }.get(status)
    if not target or current_sales_stage(lead) == target:
        return True

    current = current_sales_stage(lead)
    allowed = {
        "APPLICATION_STARTED": {"APPLICATION_INVITED"},
        "APPLICATION_SUBMITTED": {"APPLICATION_STARTED"},
        "JOINED": {"APPLICATION_SUBMITTED"},
    }
    if target not in allowed or current not in allowed[target]:
        if strict:
            raise ValueError(f"Cannot synchronize sales stage {current} -> {target} for application {aid}.")
        return False

    try:
        transition_sales_stage(
            db, row["lead_id"], target, actor_id if actor_id is not None else session.get("user_id"),
            reason="Application workflow synchronized",
            notes=f"Application status: {status}",
            commit=False,
        )
        return True
    except ValueError:
        if strict:
            raise
        # Do not break compliance/application processing because an older
        # record cannot be reconciled into the new canonical sales path.
        return False


def sync_application_status(db, aid: int, actor_id: int | None = None) -> str:
    """Recalculate and persist an application's status from its checklist."""
    previous = db.execute(
        "SELECT application_status FROM membership_applications WHERE id = ?", (aid,)
    ).fetchone()
    checklist = db.execute(
        "SELECT * FROM compliance_checklists WHERE application_id = ?", (aid,)
    ).fetchone()
    analysis = db.execute(
        "SELECT * FROM statement_analyses WHERE application_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (aid,),
    ).fetchone()
    status = _derive_status(checklist, analysis) if checklist else "awaiting_docs"
    db.execute(
        """UPDATE membership_applications
           SET application_status = ?, updated_at = datetime('now') WHERE id = ?""",
        (status, aid),
    )
    from .sales_kpi import record_application_status_change
    record_application_status_change(
        db, aid, previous[0] if previous else None, status, actor_id=actor_id,
    )
    _sync_sales_stage_for_application(db, aid, status, actor_id=actor_id)
    return status


def _is_statement_qualified(analysis) -> bool:
    return bool(analysis and analysis["verification_qualified"])


def _is_ready_to_push(cl, analysis=None) -> bool:
    return (
        cl["contract_status"] == "signed"
        and cl["debit_check_status"] == "approved"
        and cl["bank_statement_status"] == "analysed"
        and _is_statement_qualified(analysis)
        and cl["id_copy_status"] in ("uploaded", "verified")
        and cl["pos_status"] == "paid"
        and (cl["guardian_required"] != "required" or cl["guardian_status"] == "approved")
        and cl["manager_approval_status"] in ("not_required", "approved")
    )


def _missing_requirements(cl, analysis=None) -> list[str]:
    """Human-readable list of compliance items still outstanding."""
    if cl is None:
        return ["Compliance checklist not started"]
    missing = []
    if cl["contract_status"] != "signed":
        missing.append("Contract not signed")
    if cl["debit_check_status"] != "approved":
        missing.append("DebiCheck not approved")
    if cl["bank_statement_status"] != "analysed":
        missing.append("Bank statement not analysed")
    elif not _is_statement_qualified(analysis):
        missing.append("Bank statement does not meet the two-month income or 30% balance rule")
    if cl["id_copy_status"] not in ("uploaded", "verified"):
        missing.append("ID copy not uploaded")
    if cl["pos_status"] != "paid":
        missing.append("POS payment not completed")
    if cl["guardian_required"] == "required" and cl["guardian_status"] != "approved":
        missing.append("Guardian consent not approved")
    if cl["manager_approval_status"] not in ("not_required", "approved"):
        missing.append("Manager approval pending")
    return missing


def _derive_status(cl, analysis=None) -> str:
    """Derive application state from the checklist *and* latest statement analysis.

    Bank-statement analysis is part of the verification gate; a checklist row
    saying ``analysed`` alone is not sufficient to make an application ready.
    """
    if cl["manager_approval_status"] == "required":
        return "manager_approval"
    if _is_ready_to_push(cl, analysis):
        return "ready_to_push"
    if (cl["contract_status"] != "pending"
            or cl["bank_statement_status"] != "not_uploaded"
            or cl["debit_check_status"] != "pending"
            or cl["id_copy_status"] != "not_uploaded"
            or cl["pos_status"] != "pending"):
        return "verification"
    return "awaiting_docs"


# ── Application list ─────────────────────────────────────────────────────────

@applications_bp.route("/")
@permission_required("all_members")
def applications_index():
    db = get_db()
    status_filter = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()

    query = """
        SELECT ma.*, l.full_name AS lead_name,
               m.first_name, m.last_name,
               cc.contract_status, cc.debit_check_status,
               cc.bank_statement_status, cc.id_copy_status, cc.pos_status,
               cc.guardian_required, cc.guardian_status, cc.manager_approval_status,
               CAST((
                   CASE WHEN cc.contract_status = 'signed' THEN 1 ELSE 0 END +
                   CASE WHEN cc.debit_check_status = 'approved' THEN 1 ELSE 0 END +
                   CASE WHEN cc.bank_statement_status IN ('uploaded','analysed') THEN 1 ELSE 0 END +
                   CASE WHEN cc.id_copy_status IN ('uploaded','verified') THEN 1 ELSE 0 END +
                   CASE WHEN cc.pos_status = 'paid' THEN 1 ELSE 0 END +
                   CASE WHEN cc.manager_approval_status IN ('not_required','approved') THEN 1 ELSE 0 END +
                   CASE WHEN cc.guardian_required = 'required' AND cc.guardian_status = 'approved'
                        THEN 1 ELSE 0 END
               ) * 100 / CASE WHEN cc.guardian_required = 'required' THEN 7 ELSE 6 END AS INTEGER) AS progress_pct,
               u.full_name AS created_by_name
        FROM membership_applications ma
        LEFT JOIN leads l ON ma.lead_id = l.id
        LEFT JOIN members m ON ma.member_id = m.id
        LEFT JOIN compliance_checklists cc ON cc.application_id = ma.id
        LEFT JOIN users u ON ma.created_by = u.id
        WHERE 1=1
    """
    params = []
    if status_filter:
        query += " AND ma.application_status = ?"
        params.append(status_filter)
    if q:
        like = f"%{q}%"
        query += """ AND (
            m.first_name LIKE ? OR m.last_name LIKE ?
            OR (m.first_name || ' ' || m.last_name) LIKE ?
            OR l.full_name LIKE ?
        )"""
        params += [like, like, like, like]
    query += " ORDER BY ma.updated_at DESC"

    applications = db.execute(query, params).fetchall()

    counts = {
        row["application_status"]: row["cnt"]
        for row in db.execute(
            "SELECT application_status, COUNT(*) AS cnt FROM membership_applications GROUP BY application_status"
        ).fetchall()
    }

    pending_approvals = db.execute(
        "SELECT COUNT(*) FROM approvals WHERE status = 'pending'"
    ).fetchone()[0]

    return render_template(
        "applications/index.html",
        applications=applications,
        status_filter=status_filter,
        search=q,
        statuses=APPLICATION_STATUSES,
        status_colors=STATUS_COLORS,
        counts=counts,
        pending_approvals=pending_approvals,
    )


# ── Application detail / compliance checklist ─────────────────────────────────

@applications_bp.route("/<int:aid>")
@permission_required("all_members")
def application_detail(aid: int):
    db = get_db()
    app = db.execute(
        """SELECT ma.*, l.full_name AS lead_name, l.phone AS lead_phone,
                  m.first_name, m.last_name, m.id_number, m.contact, m.email,
                  u.full_name AS created_by_name
           FROM membership_applications ma
           LEFT JOIN leads l ON ma.lead_id = l.id
           LEFT JOIN members m ON ma.member_id = m.id
           LEFT JOIN users u ON ma.created_by = u.id
           WHERE ma.id = ?""",
        (aid,),
    ).fetchone()
    if app is None:
        flash("Application not found.", "warning")
        return redirect(url_for("applications.applications_index"))

    app = dict(app)
    app["id_number"] = decrypt(app.get("id_number"))

    checklist = db.execute(
        "SELECT * FROM compliance_checklists WHERE application_id = ?", (aid,)
    ).fetchone()

    analysis = db.execute(
        """SELECT sa.*, u.full_name AS analyst_name FROM statement_analyses sa
           LEFT JOIN users u ON sa.created_by = u.id
           WHERE sa.application_id = ? ORDER BY sa.created_at DESC LIMIT 1""",
        (aid,),
    ).fetchone()

    pending_approval = db.execute(
        """SELECT ap.*, u.full_name AS requested_by_name FROM approvals ap
           LEFT JOIN users u ON ap.requested_by = u.id
           WHERE ap.application_id = ? AND ap.status = 'pending'
           ORDER BY ap.created_at DESC LIMIT 1""",
        (aid,),
    ).fetchone()

    trail = db.execute(
        """SELECT al.*, u.full_name AS user_name FROM audit_logs al
           LEFT JOIN users u ON al.user_id = u.id
           WHERE al.application_id = ? ORDER BY al.timestamp DESC LIMIT 25""",
        (aid,),
    ).fetchall()

    ready = (
        _is_ready_to_push(checklist, analysis)
        and app["push_status"] not in ("pushed", "pushing")
        if checklist else False
    )

    return render_template(
        "applications/detail.html",
        app=app,
        checklist=checklist,
        analysis=analysis,
        pending_approval=pending_approval,
        trail=trail,
        ready_to_push=ready,
        missing_items=_missing_requirements(checklist, analysis) if checklist else [],
        is_manager=session.get("role") in MANAGER_ROLES,
        contract_statuses=CONTRACT_STATUSES,
        debit_statuses=DEBIT_STATUSES,
        bank_statuses=BANK_STATUSES,
        id_statuses=ID_STATUSES,
        pos_statuses=POS_STATUSES,
        guardian_statuses=GUARDIAN_STATUSES,
        manager_statuses=MANAGER_STATUSES,
        income_options=INCOME_OPTIONS,
        income_frequencies=INCOME_FREQUENCIES,
        risk_ratings=RISK_RATINGS,
        status_colors=STATUS_COLORS,
        APPLICATION_STATUSES=APPLICATION_STATUSES,
    )


# ── Update compliance checklist ───────────────────────────────────────────────

@applications_bp.post("/<int:aid>/checklist")
@permission_required("add_member")
def update_checklist(aid: int):
    db = get_db()
    from .sales_kpi import record_checklist_changes, snapshot_checklist
    before = snapshot_checklist(db, aid)
    previous_status = db.execute(
        "SELECT application_status FROM membership_applications WHERE id=?", (aid,)
    ).fetchone()

    # Manager approval is a controlled decision, not a normal checklist field.
    # Non-managers may update evidence fields but cannot self-approve or decline
    # an application. Preserve an existing manager decision when they save the
    # rest of the checklist.
    existing_cl = db.execute(
        "SELECT * FROM compliance_checklists WHERE application_id=?", (aid,)
    ).fetchone()
    requested_manager_status = request.form.get("manager_approval_status")
    if session.get("role") in MANAGER_ROLES and requested_manager_status in MANAGER_STATUSES:
        manager_status = requested_manager_status
    elif existing_cl:
        manager_status = existing_cl["manager_approval_status"]
    else:
        manager_status = "not_required"

    # Evidence-backed checklist states are owned by their source workflow.
    # The generic checklist form may not manufacture a signed contract, an
    # approved DebiCheck mandate, an analysed bank statement, a verified ID,
    # or a paid POS transaction. Those states are written by their respective
    # controlled workflows.
    editable = {
        "contract_status": {"pending", "failed"},
        "debit_check_status": {"pending"},
        "bank_statement_status": {"not_uploaded", "uploaded"},
        "id_copy_status": {"not_uploaded", "uploaded"},
        "pos_status": {"pending", "failed"},
        "guardian_status": set(GUARDIAN_STATUSES),
    }
    requested = {
        "contract_status": request.form.get("contract_status", "pending"),
        "debit_check_status": request.form.get("debit_check_status", "pending"),
        "bank_statement_status": request.form.get("bank_statement_status", "not_uploaded"),
        "id_copy_status": request.form.get("id_copy_status", "not_uploaded"),
        "pos_status": request.form.get("pos_status", "pending"),
        "guardian_status": request.form.get("guardian_status", "not_required"),
    }
    current_values = {key: existing_cl[key] if existing_cl else None for key in editable}
    fields_values = []
    for key in ("contract_status", "debit_check_status", "bank_statement_status", "id_copy_status", "pos_status", "guardian_status"):
        value = requested[key]
        if value not in editable[key]:
            # Preserve an existing source-controlled state. If none exists,
            # fall back to the safe initial state rather than accepting a
            # forged terminal state from the form.
            fallback = current_values[key]
            if fallback not in {None, ""}:
                value = fallback
            else:
                value = {
                    "contract_status": "pending", "debit_check_status": "pending",
                    "bank_statement_status": "not_uploaded", "id_copy_status": "not_uploaded",
                    "pos_status": "pending", "guardian_status": "not_required",
                }[key]
            flash(f"{key.replace('_', ' ').title()} is controlled by its verification workflow.", "warning")
        fields_values.append(value)
    fields = (*fields_values, manager_status)

    existing = db.execute(
        "SELECT id FROM compliance_checklists WHERE application_id=?", (aid,)
    ).fetchone()
    if existing:
        db.execute(
            """UPDATE compliance_checklists
               SET contract_status=?, debit_check_status=?, bank_statement_status=?,
                   id_copy_status=?, pos_status=?, guardian_status=?,
                   manager_approval_status=?, updated_at=datetime('now')
               WHERE application_id=?""",
            (*fields, aid),
        )
    else:
        db.execute(
            """INSERT INTO compliance_checklists
               (application_id, contract_status, debit_check_status, bank_statement_status,
                id_copy_status, pos_status, guardian_status, manager_approval_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (aid, *fields),
        )

    cl = db.execute("SELECT * FROM compliance_checklists WHERE application_id=?", (aid,)).fetchone()
    analysis = db.execute(
        "SELECT * FROM statement_analyses WHERE application_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
        (aid,),
    ).fetchone()
    new_status = _derive_status(cl, analysis)
    db.execute(
        "UPDATE membership_applications SET application_status=?, updated_at=datetime('now') WHERE id=?",
        (new_status, aid),
    )
    record_checklist_changes(db, aid, before, actor_id=session.get("user_id"))
    from .sales_kpi import record_application_status_change
    record_application_status_change(
        db, aid, previous_status[0] if previous_status else None, new_status,
        actor_id=session.get("user_id"),
    )
    audit_log(db, "checklist_updated", application_id=aid, new_value=new_status)
    db.commit()
    flash("Compliance checklist updated.", "success")
    return redirect(url_for("applications.application_detail", aid=aid))


# ── Bank statement analysis ───────────────────────────────────────────────────

@applications_bp.post("/<int:aid>/analyse")
@permission_required("add_member")
def submit_analysis(aid: int):
    db = get_db()
    from .sales_kpi import record_application_status_change, record_checklist_changes, snapshot_checklist
    before_checklist = snapshot_checklist(db, aid)
    previous_status = db.execute(
        "SELECT application_status FROM membership_applications WHERE id=?", (aid,)
    ).fetchone()

    income_detected = request.form.get("income_detected", "unclear")
    est_income = float(request.form.get("estimated_monthly_income") or 0)
    m1 = float(request.form.get("max_balance_m1") or 0)
    m2 = float(request.form.get("max_balance_m2") or 0)
    m3 = float(request.form.get("max_balance_m3") or 0)
    avg_bal = float(request.form.get("average_balance") or 0)
    returned = int(request.form.get("returned_debits_count") or 0)
    neg_days = int(request.form.get("negative_balance_days") or 0)
    freq = request.form.get("income_frequency", "irregular")
    summary = request.form.get("analysis_summary", "").strip()

    # Risk is system-derived. A manual override is a controlled manager/admin
    # decision and is never accepted silently from ordinary staff.
    if income_detected == "no" or returned > 0 or neg_days > 5:
        auto_risk = "high"
    elif income_detected == "unclear" or neg_days > 0:
        auto_risk = "medium"
    else:
        auto_risk = "low"
    requested_risk = (request.form.get("risk_rating") or "").strip().lower()
    if requested_risk and requested_risk not in RISK_RATINGS:
        requested_risk = ""
    risk_rating = requested_risk if (requested_risk and session.get("role") in MANAGER_ROLES) else auto_risk
    if requested_risk and requested_risk != auto_risk and session.get("role") not in MANAGER_ROLES:
        audit_log(db, "risk_override_blocked", application_id=aid, new_value=f"requested={requested_risk};auto={auto_risk}")
        flash("Risk rating is system-controlled. A manager or admin must approve an override.", "warning")

    db.execute(
        """INSERT INTO statement_analyses
           (application_id, income_detected, estimated_monthly_income,
            max_balance_m1, max_balance_m2, max_balance_m3, average_balance,
            returned_debits_count, negative_balance_days, income_frequency,
            risk_rating, analysis_summary, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (aid, income_detected, est_income, m1, m2, m3, avg_bal,
         returned, neg_days, freq, risk_rating, summary, session.get("user_id")),
    )
    if requested_risk and requested_risk != auto_risk and session.get("role") in MANAGER_ROLES:
        audit_log(db, "risk_override_approved", application_id=aid, new_value=f"auto={auto_risk};override={requested_risk}")

    # Mark bank statement as analysed
    db.execute(
        """UPDATE compliance_checklists
           SET bank_statement_status='analysed', updated_at=datetime('now')
           WHERE application_id=?""",
        (aid,),
    )

    # High-risk evidence always triggers the manager gate. A manager may
    # override the displayed risk rating, but an override cannot erase the
    # underlying approval requirement created by the evidence.
    high_risk_indicators = income_detected == "no" or returned > 0 or neg_days > 5 or auto_risk == "high"
    if high_risk_indicators:
        db.execute(
            """UPDATE compliance_checklists
               SET manager_approval_status='required', updated_at=datetime('now')
               WHERE application_id=? AND manager_approval_status='not_required'""",
            (aid,),
        )
        db.execute(
            """INSERT INTO approvals (application_id, approval_type, requested_by, reason)
               VALUES (?, 'manager', ?, ?)""",
            (aid, session.get("user_id"),
             f"Auto-triggered — risk: {risk_rating}, income: {income_detected}, returned debits: {returned}"),
        )
        flash("High-risk indicators detected. Manager approval triggered automatically.", "warning")

    cl = db.execute("SELECT * FROM compliance_checklists WHERE application_id=?", (aid,)).fetchone()
    latest_analysis = db.execute(
        "SELECT * FROM statement_analyses WHERE application_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
        (aid,),
    ).fetchone()
    derived_status = _derive_status(cl, latest_analysis)
    db.execute(
        "UPDATE membership_applications SET application_status=?, updated_at=datetime('now') WHERE id=?",
        (derived_status, aid),
    )
    record_checklist_changes(db, aid, before_checklist, actor_id=session.get("user_id"))
    record_application_status_change(
        db, aid, previous_status[0] if previous_status else None, derived_status,
        actor_id=session.get("user_id"),
    )
    audit_log(db, "statement_analysed", application_id=aid, new_value=f"risk={risk_rating}")
    db.commit()
    flash("Bank statement analysis saved.", "success")
    return redirect(url_for("applications.application_detail", aid=aid))


# ── Push application ──────────────────────────────────────────────────────────


def _mark_itensity_push_started(db, aid: int) -> bool:
    """Atomically claim the application for a single Itensity push worker."""
    cur = db.execute(
        """UPDATE membership_applications
           SET push_status='pushing', updated_at=datetime('now')
           WHERE id=?
             AND push_status NOT IN ('pushing', 'pushed')
             AND application_status <> 'active'""",
        (aid,),
    )
    return cur.rowcount == 1


def _itensity_result_is_valid(success, itensity_ref) -> bool:
    """A successful API call is not enough; an Itensity member reference is required."""
    return bool(success and itensity_ref and str(itensity_ref).strip())


def _background_itensity_push(
    app, db_path: str, aid: int, member_id: int,
    itensity_ref_existing: str | None, overridden: bool, missing: list[str], user_id: int | None,
) -> None:
    """Runs the (possibly minutes-long) Itensity browser push off the request
    thread. Opens its own sqlite connection since the request's is gone by
    the time this finishes, and needs app_context for current_app.config
    inside the integrations module — but never touches flask.session, since
    there's no request here to read it from."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with app.app_context():
            member = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
            if member is None:
                conn.execute(
                    "UPDATE membership_applications SET push_status='push_failed', updated_at=datetime('now') WHERE id=?",
                    (aid,),
                )
                conn.commit()
                return

            member_data = decrypt_member(member)
            if itensity_ref_existing:
                success, error_msg = update_member_in_itensity(itensity_ref_existing, member_data)
                itensity_ref = itensity_ref_existing
            else:
                success, itensity_ref, error_msg = push_member_to_itensity(member_data)

            if not _itensity_result_is_valid(success, itensity_ref):
                failure = error_msg or "Itensity did not return a valid member reference."
                conn.execute(
                    "UPDATE membership_applications SET push_status='push_failed', updated_at=datetime('now') WHERE id=?",
                    (aid,),
                )
                audit_log(conn, "itensity_push_failed", application_id=aid, new_value=failure, user_id=user_id)
                member_activity(conn, member_id, f"Itensity push failed: {failure}", user_id=user_id)
                conn.commit()
                return

            conn.execute(
                """UPDATE membership_applications
                   SET application_status='active', push_status='pushed',
                       pushed_at=datetime('now'), updated_at=datetime('now')
                   WHERE id=?""",
                (aid,),
            )
            conn.execute(
                """UPDATE members
                   SET itensity_ref = ?,
                       member_status = CASE
                           WHEN member_status = 'Unverified' THEN 'Active'
                           ELSE member_status
                       END
                   WHERE id = ?""",
                (str(itensity_ref), member_id),
            )
            if overridden:
                audit_log(conn, "itensity_push_overridden", application_id=aid,
                          new_value=f"missing: {', '.join(missing)}; itensity_ref={itensity_ref}", user_id=user_id)
                member_activity(
                    conn, member_id,
                    f"Pushed to Itensity via ADMIN OVERRIDE (incomplete: {', '.join(missing)}). Ref: {itensity_ref}",
                    user_id=user_id,
                )
            else:
                audit_log(conn, "application_activated", application_id=aid,
                          new_value=f"itensity_ref={itensity_ref}", user_id=user_id)
                member_activity(conn, member_id, f"Pushed to Itensity and activated with ref: {itensity_ref}", user_id=user_id)
            # The sales-stage JOINED transition is part of the same local
            # transaction as activation. If it cannot be reconciled, roll the
            # local activation back rather than creating an Active Member whose
            # sales record still says APPLICATION_SUBMITTED.
            _sync_sales_stage_for_application(conn, aid, "active", actor_id=user_id, strict=True)
            conn.commit()
    except Exception:
        app.logger.exception("Background Itensity push crashed for application %s", aid)
        try:
            conn.execute(
                "UPDATE membership_applications SET push_status='push_failed', updated_at=datetime('now') WHERE id=?",
                (aid,),
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


@applications_bp.post("/<int:aid>/push")
@permission_required("add_member")
def push_application(aid: int):
    db = get_db()
    application = db.execute(
        "SELECT * FROM membership_applications WHERE id = ?", (aid,)
    ).fetchone()
    if application is None:
        flash("Application not found.", "warning")
        return redirect(url_for("applications.applications_index"))

    if application["push_status"] in ("pushing", "pushed") or application["application_status"] == "active":
        flash(
            "This application is already being processed or has already been pushed to Itensity.",
            "warning",
        )
        return redirect(url_for("applications.application_detail", aid=aid))

    cl = db.execute(
        "SELECT * FROM compliance_checklists WHERE application_id=?", (aid,)
    ).fetchone()

    analysis = db.execute(
        "SELECT * FROM statement_analyses WHERE application_id=? ORDER BY created_at DESC LIMIT 1",
        (aid,),
    ).fetchone()

    missing = _missing_requirements(cl, analysis)
    is_manager = session.get("role") in MANAGER_ROLES
    overridden = bool(missing) and is_manager and request.form.get("override") == "1"

    if missing and not overridden:
        flash(f"Application is missing: {', '.join(missing)}.", "error")
        return redirect(url_for("applications.application_detail", aid=aid))

    if not application["member_id"]:
        flash("Create a member profile before pushing this application to Itensity.", "error")
        return redirect(url_for("applications.application_detail", aid=aid))

    member = db.execute(
        "SELECT * FROM members WHERE id = ?", (application["member_id"],)
    ).fetchone()
    if member is None:
        flash("The member linked to this application could not be found.", "error")
        return redirect(url_for("applications.application_detail", aid=aid))

    db_path = db.execute("PRAGMA database_list").fetchone()["file"]
    app_obj = current_app._get_current_object()
    user_id = session.get("user_id")

    # Claim the push atomically. Two simultaneous requests must never start
    # two Itensity workers for the same application.
    if not _mark_itensity_push_started(db, aid):
        db.rollback()
        flash("Another Itensity push has already started for this application.", "warning")
        return redirect(url_for("applications.application_detail", aid=aid))
    db.commit()

    threading.Thread(
        target=_background_itensity_push,
        args=(app_obj, db_path, aid, member["id"], member["itensity_ref"], overridden, missing, user_id),
        daemon=True,
    ).start()

    flash(
        "Itensity push started in the background — this can take a few minutes. "
        "Refresh this page to check the result.",
        "info",
    )
    return redirect(url_for("applications.application_detail", aid=aid))


# ── Manager approval queue ────────────────────────────────────────────────────

@applications_bp.route("/approvals")
@permission_required("application_approvals")
def approvals_queue():
    db = get_db()
    pending = db.execute(
        """SELECT ap.*, ma.package, ma.application_status,
                  l.full_name AS lead_name, m.first_name, m.last_name,
                  ru.full_name AS requested_by_name,
                  cc.contract_status, cc.debit_check_status, cc.bank_statement_status,
                  cc.age_category, cc.guardian_required,
                  sa.risk_rating, sa.income_detected, sa.estimated_monthly_income,
                  sa.returned_debits_count
           FROM approvals ap
           JOIN membership_applications ma ON ap.application_id = ma.id
           LEFT JOIN leads l ON ma.lead_id = l.id
           LEFT JOIN members m ON ma.member_id = m.id
           LEFT JOIN users ru ON ap.requested_by = ru.id
           LEFT JOIN compliance_checklists cc ON cc.application_id = ma.id
           LEFT JOIN statement_analyses sa ON sa.application_id = ma.id
           WHERE ap.status = 'pending'
           ORDER BY ap.created_at ASC"""
    ).fetchall()
    return render_template("applications/approval.html", pending=pending)


@applications_bp.post("/<int:aid>/approve")
@permission_required("application_approvals")
def approve_application(aid: int):
    db = get_db()
    from .sales_kpi import record_application_status_change, record_checklist_changes, snapshot_checklist
    before_checklist = snapshot_checklist(db, aid)
    previous_status = db.execute(
        "SELECT application_status FROM membership_applications WHERE id=?", (aid,)
    ).fetchone()
    comment = request.form.get("comment", "").strip()
    pending_approval = db.execute(
        "SELECT id FROM approvals WHERE application_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (aid,)
    ).fetchone()
    if not pending_approval:
        flash("No pending manager approval exists for this application.", "warning")
        return redirect(url_for("applications.approvals_queue"))
    db.execute(
        """UPDATE approvals
           SET status='approved', approved_by=?, approved_at=datetime('now'), manager_comment=?
           WHERE application_id=? AND status='pending'""",
        (session.get("user_id"), comment, aid),
    )
    db.execute(
        """UPDATE compliance_checklists
           SET manager_approval_status='approved', updated_at=datetime('now')
           WHERE application_id=?""",
        (aid,),
    )
    cl = db.execute("SELECT * FROM compliance_checklists WHERE application_id=?", (aid,)).fetchone()
    analysis = db.execute(
        "SELECT * FROM statement_analyses WHERE application_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
        (aid,),
    ).fetchone()
    derived_status = _derive_status(cl, analysis)
    db.execute(
        "UPDATE membership_applications SET application_status=?, updated_at=datetime('now') WHERE id=?",
        (derived_status, aid),
    )
    record_checklist_changes(db, aid, before_checklist, actor_id=session.get("user_id"))
    record_application_status_change(
        db, aid, previous_status[0] if previous_status else None, derived_status,
        actor_id=session.get("user_id"),
    )
    audit_log(db, "manager_approved", application_id=aid, new_value=comment)
    db.commit()
    flash("Application approved.", "success")
    return redirect(url_for("applications.approvals_queue"))


@applications_bp.post("/<int:aid>/decline")
@permission_required("application_approvals")
def decline_application(aid: int):
    db = get_db()
    from .sales_kpi import record_application_status_change, record_checklist_changes, snapshot_checklist
    before_checklist = snapshot_checklist(db, aid)
    previous_status = db.execute(
        "SELECT application_status FROM membership_applications WHERE id=?", (aid,)
    ).fetchone()
    comment = request.form.get("comment", "").strip()
    pending_approval = db.execute(
        "SELECT id FROM approvals WHERE application_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (aid,)
    ).fetchone()
    if not pending_approval:
        flash("No pending manager approval exists for this application.", "warning")
        return redirect(url_for("applications.approvals_queue"))
    db.execute(
        """UPDATE approvals
           SET status='declined', approved_by=?, approved_at=datetime('now'), manager_comment=?
           WHERE application_id=? AND status='pending'""",
        (session.get("user_id"), comment, aid),
    )
    db.execute(
        """UPDATE compliance_checklists
           SET manager_approval_status='declined', updated_at=datetime('now')
           WHERE application_id=?""",
        (aid,),
    )
    db.execute(
        "UPDATE membership_applications SET application_status='awaiting_docs', updated_at=datetime('now') WHERE id=?",
        (aid,),
    )
    record_checklist_changes(db, aid, before_checklist, actor_id=session.get("user_id"))
    record_application_status_change(
        db, aid, previous_status[0] if previous_status else None, "awaiting_docs",
        actor_id=session.get("user_id"),
    )
    audit_log(db, "manager_declined", application_id=aid, new_value=comment)
    db.commit()
    flash("Application declined.", "info")
    return redirect(url_for("applications.approvals_queue"))
