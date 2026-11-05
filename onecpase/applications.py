import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from .auth import permission_required
from .database import get_db, audit_log, member_activity
from .encryption import decrypt, decrypt_member
from .integrations import ItensityNotSubmitted, push_member_to_itensity, update_member_in_itensity

applications_bp = Blueprint("applications", __name__, url_prefix="/applications")
_push_workers = set()
_push_workers_lock = threading.Lock()

# Managers may review escalations and override the failing-statement gates after
# checking the statement themselves; every other gate is admin-only.
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
    # Later payment or document changes do not undo an actual Itensity activation.
    if previous and previous[0] == "active":
        status = "active"
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
    _sync_compliance_completion(db, aid, actor_id=actor_id)
    return status


def _is_statement_qualified(analysis) -> bool:
    return bool(analysis and analysis["verification_qualified"])


def _is_ready_to_push(cl, analysis=None) -> bool:
    """Require documents, qualified affordability, approved DebiCheck, a signed contract and the paid reg fee."""
    return (
        cl["debit_check_status"] == "approved"
        and cl["contract_status"] == "signed"
        and cl["pos_status"] == "paid"
        and cl["id_copy_status"] in ("uploaded", "verified")
        and cl["bank_statement_status"] == "analysed"
        and _is_statement_qualified(analysis)
        and (cl["guardian_required"] != "required" or cl["guardian_status"] == "approved")
        and cl["manager_approval_status"] in ("not_required", "approved")
    )


def _missing_requirements(cl, analysis=None) -> list[str]:
    """Human-readable list of items still blocking the Itensity push."""
    if cl is None:
        return ["Compliance checklist not started"]
    missing = []
    if cl["debit_check_status"] != "approved":
        missing.append("DebiCheck approval not confirmed")
    if cl["id_copy_status"] not in ("uploaded", "verified"):
        missing.append("ID copy not uploaded")
    if cl["bank_statement_status"] != "analysed":
        missing.append("Bank statement not analysed")
    elif not _is_statement_qualified(analysis):
        missing.append(AFFORDABILITY_BLOCKER)
    if cl["contract_status"] != "signed":
        missing.append(CONTRACT_BLOCKER)
    if cl["pos_status"] != "paid":
        missing.append(REG_FEE_BLOCKER)
    if cl["guardian_required"] == "required" and cl["guardian_status"] != "approved":
        missing.append(GUARDIAN_BLOCKER)
    if cl["manager_approval_status"] not in ("not_required", "approved"):
        missing.append(MANAGER_APPROVAL_BLOCKER)
    return missing


CONTRACT_BLOCKER = "Contract signing not confirmed"
REG_FEE_BLOCKER = "Registration fee not fully paid"
AFFORDABILITY_BLOCKER = "Bank statement does not meet the salary or R3,000 money-in rule"
GUARDIAN_BLOCKER = "Guardian consent not approved"
MANAGER_APPROVAL_BLOCKER = "Manager approval pending"
STATEMENT_MONTHS_BLOCKER = "At least two statement months must be automatically analysed before NuPay submission."
STATEMENT_QUALIFYING_BLOCKER = "A qualifying bank-statement analysis is required; inform the client or escalate to a manager"
MANAGER_OVERRIDABLE_BLOCKERS = frozenset({
    AFFORDABILITY_BLOCKER, STATEMENT_MONTHS_BLOCKER, STATEMENT_QUALIFYING_BLOCKER,
    GUARDIAN_BLOCKER, MANAGER_APPROVAL_BLOCKER,
})
STATEMENT_CHECK_BLOCKERS = frozenset({AFFORDABILITY_BLOCKER, STATEMENT_MONTHS_BLOCKER, STATEMENT_QUALIFYING_BLOCKER})
STATEMENT_OPENED_ACTION = "statement_opened"


def can_override_push(role, missing) -> bool:
    """Admins may push past anything; managers only past affordability, guardian consent and manager approval."""
    if not missing:
        return False
    return role == "admin" or (role == "manager" and MANAGER_OVERRIDABLE_BLOCKERS.issuperset(missing))


def can_override_nupay(role, blockers, money_in_months=0) -> bool:
    """A short statement can only be accepted when R3,000 came in during two calendar months;
    a manager may override that and a failed affordability result, nothing else."""
    if not blockers or (STATEMENT_MONTHS_BLOCKER in blockers and money_in_months < 2):
        return False
    return role == "admin" or (
        role == "manager" and {STATEMENT_QUALIFYING_BLOCKER, STATEMENT_MONTHS_BLOCKER}.issuperset(blockers)
    )


def needs_statement_check(role, missing) -> bool:
    return role == "manager" and bool(STATEMENT_CHECK_BLOCKERS.intersection(missing))


def _latest_statement(db, member_id):
    return db.execute(
        "SELECT id, member_id, original_name, analysis_json FROM member_documents "
        "WHERE member_id=? AND document_type='Bank Statement' ORDER BY id DESC LIMIT 1",
        (member_id,),
    ).fetchone()


def _statement_analysis(statement) -> dict:
    import json
    try:
        analysis = json.loads(statement["analysis_json"] or "null") if statement else None
    except (TypeError, ValueError):
        analysis = None
    return analysis if isinstance(analysis, dict) else {}


def _latest_statement_analysis(db, member_id) -> dict:
    return _statement_analysis(db.execute(
        "SELECT analysis_json FROM member_documents WHERE member_id=? AND document_type='Bank Statement' ORDER BY id DESC LIMIT 1",
        (member_id,),
    ).fetchone())


def statement_check_context(db, member_id) -> dict | None:
    """What the system read from the latest statement, for a manager to compare with the file."""
    statement = _latest_statement(db, member_id)
    if not statement:
        return None
    return {"document": statement, "analysis": _statement_analysis(statement)}


def mark_statement_accepted(db, member_id) -> None:
    """A manager or admin accepted this statement, so the later steps needn't ask again.
    It is kept on the document: a newer statement starts over, and so does analysing it again."""
    import json
    statement = db.execute(
        "SELECT id, analysis_json FROM member_documents WHERE member_id=? AND document_type='Bank Statement' ORDER BY id DESC LIMIT 1",
        (member_id,),
    ).fetchone()
    if statement:
        analysis = _statement_analysis(statement)
        analysis["manager_accepted"] = True
        db.execute("UPDATE member_documents SET analysis_json=? WHERE id=?", (json.dumps(analysis), statement["id"]))


def statement_accepted(db, member_id) -> bool:
    return _latest_statement_analysis(db, member_id).get("manager_accepted") is True


def _push_analysis(db, member_id, analysis):
    """The affordability result for the Itensity push: a manager's acceptance counts as a pass."""
    return {"verification_qualified": 1} if statement_accepted(db, member_id) else analysis


def statement_money_in_months(db, member_id) -> int:
    """Months with R3,000 coming in, on a latest statement that was read without errors."""
    analysis = _latest_statement_analysis(db, member_id)
    if analysis.get("error") or analysis.get("batch_error"):
        return 0
    months = analysis.get("money_in_months")
    return months if isinstance(months, int) and not isinstance(months, bool) else 0


def _statement_problems(db, member_id, blockers) -> str:
    """What was wrong with the statement, in words for the client's activity log."""
    analysis = _latest_statement_analysis(db, member_id)
    problems = []
    if STATEMENT_MONTHS_BLOCKER in blockers:
        cash = analysis.get("cashflow") if isinstance(analysis.get("cashflow"), dict) else {}
        problems.append(f"it covers only about {cash.get('months') or 0} month(s), not two")
    if STATEMENT_QUALIFYING_BLOCKER in blockers:
        if (analysis.get("money_in_months") or 0) >= 2:
            problems.append("the instalment is more than 30% of the money coming in")
        else:
            problems.append("no salary was found and R3,000 did not come in during two months")
    for field, label in (("failed_debits", "failed"), ("disputed_debits", "disputed")):
        if analysis.get(field):
            problems.append(f"{analysis[field]} {label} debit order(s)")
    known = {STATEMENT_MONTHS_BLOCKER, STATEMENT_QUALIFYING_BLOCKER}
    problems.extend(blocker for blocker in blockers if blocker not in known)
    return "; ".join(problems)


def nupay_override_note(db, member_id, blockers) -> str:
    """The note on the client's activity once an overridden mandate has gone to NuPay."""
    who = session.get("full_name") or session.get("username") or "a manager"
    note = f"Pushed to NuPay via Override by {who}. What was wrong with the bank statement: {_statement_problems(db, member_id, blockers)}."
    finding = request.form.get("statement_note", "").strip()
    return f"{note} Manager's check: {finding}" if finding else note


def statement_check_problem(db, member_id) -> str | None:
    """Why this manager can't override yet: the file must be opened and the finding written down."""
    statement = _latest_statement(db, member_id)
    if not statement:
        return "Upload a bank statement before overriding."
    opened = db.execute(
        "SELECT 1 FROM audit_logs WHERE action=? AND user_id=? AND new_value=? LIMIT 1",
        (STATEMENT_OPENED_ACTION, session.get("user_id"), str(statement["id"])),
    ).fetchone()
    if not opened:
        return "Open the latest bank statement and check it before overriding."
    if request.form.get("statement_checked") != "1" or not request.form.get("statement_note", "").strip():
        return "Confirm you checked the statement and write what you found before overriding."
    return None


def _statement_check_record(db, member_id) -> str:
    statement = _latest_statement(db, member_id)
    return f"Statement document {statement['id']} checked: {request.form.get('statement_note', '').strip()}"


def statement_months_analysed(db, member_id):
    import json
    document = db.execute(
        "SELECT analysis_json FROM member_documents WHERE member_id=? AND document_type='Bank Statement' ORDER BY id DESC LIMIT 1",
        (member_id,),
    ).fetchone()
    try:
        result = json.loads(document['analysis_json'] or 'null') if document else None
        cash = result.get('cashflow') if isinstance(result, dict) else None
        if not isinstance(cash, dict) or result.get('error') or result.get('batch_error'):
            return False
        months = cash.get('months')
        return isinstance(months, (int, float)) and not isinstance(months, bool) and months >= 2 and cash.get('enough_months') is True
    except (TypeError, ValueError):
        return False


def bank_statement_qualified(db, member_id):
    import json
    document = db.execute(
        "SELECT analysis_json FROM member_documents WHERE member_id=? AND document_type='Bank Statement' ORDER BY id DESC LIMIT 1",
        (member_id,),
    ).fetchone()
    try:
        result = json.loads(document['analysis_json'] or 'null') if document else None
        return bool(isinstance(result, dict) and not result.get('error') and not result.get('batch_error') and result.get('verification_qualified') is True)
    except (TypeError, ValueError):
        return False


def nupay_submission_checklist(db, member_id):
    document = db.execute("SELECT id FROM member_documents WHERE member_id=? AND document_type='Bank Statement' ORDER BY id DESC LIMIT 1", (member_id,)).fetchone()
    return [
        {'label': 'Bank statement uploaded', 'complete': bool(document)},
        {'label': 'Latest bank statement automatically analysed', 'complete': not bank_statement_activation_blockers(db, member_id)},
        {'label': 'At least two statement months analysed', 'complete': statement_months_analysed(db, member_id)},
        {'label': 'Qualifying affordability analysis', 'complete': bank_statement_qualified(db, member_id)},
    ]


def bank_statement_activation_blockers(db, member_id):
    """Statement evidence required unless an admin explicitly overrides."""
    import json
    document = db.execute(
        "SELECT analysis_json FROM member_documents WHERE member_id=? AND document_type='Bank Statement' ORDER BY id DESC LIMIT 1",
        (member_id,),
    ).fetchone()
    if not document:
        return ['Upload a bank statement before activating DebitCheck.']
    try:
        result = json.loads(document['analysis_json'] or 'null')
    except (TypeError, ValueError):
        result = None
    if (not isinstance(result, dict) or result.get('error') or result.get('batch_error')
            or 'estimated_income' not in result or 'rating' not in result):
        return ['The latest bank statement must complete automatic analysis before activating DebitCheck.']
    return []


def nupay_submission_blockers(db, member_id: int) -> list[str]:
    """Bank-statement evidence gates shared by every NuPay submission entry point."""
    missing = bank_statement_activation_blockers(db, member_id)
    if not statement_months_analysed(db, member_id):
        missing.append(STATEMENT_MONTHS_BLOCKER)
    if not bank_statement_qualified(db, member_id):
        missing.append(STATEMENT_QUALIFYING_BLOCKER)
    return missing


def authorize_nupay_override(db, member_id: int, blockers: list[str]) -> bool:
    """Record the explicit admin or checked manager exception before allowing a NuPay submission."""
    role = session.get("role")
    if not can_override_nupay(role, blockers, statement_money_in_months(db, member_id)) or request.form.get("override") != "1":
        return False
    record = "Missing requirements: " + "; ".join(blockers)
    if needs_statement_check(role, blockers):
        problem = statement_check_problem(db, member_id)
        if problem:
            flash(problem, "error")
            return False
        record += "; " + _statement_check_record(db, member_id)
    application = db.execute("SELECT id FROM membership_applications WHERE member_id=? ORDER BY id DESC LIMIT 1", (member_id,)).fetchone()
    audit_log(db, f"nupay_{role}_override", application_id=application["id"] if application else None,
              new_value=record)
    if STATEMENT_CHECK_BLOCKERS.intersection(blockers):
        mark_statement_accepted(db, member_id)
    return True


def _itensity_hard_blockers(db, aid, member_id):
    """Evidence gates; only an explicit, audited override may bypass them (see can_override_push)."""
    missing = nupay_submission_blockers(db, member_id)
    if statement_accepted(db, member_id):
        missing = [blocker for blocker in missing if blocker not in STATEMENT_CHECK_BLOCKERS]
    identity_checklist = db.execute("SELECT id_copy_status FROM compliance_checklists WHERE application_id=?", (aid,)).fetchone()
    identity_document = db.execute("SELECT id FROM member_documents WHERE member_id=? AND document_type='SA ID Copy' LIMIT 1", (member_id,)).fetchone()
    if not identity_checklist or identity_checklist['id_copy_status'] not in ('uploaded', 'verified') or not identity_document:
        missing.append("ID copy not uploaded")
    cl = db.execute("SELECT debit_check_status,contract_status,pos_status FROM compliance_checklists WHERE application_id=?", (aid,)).fetchone()
    mandate = db.execute("SELECT status FROM debicheck_mandates WHERE member_id=? ORDER BY id DESC LIMIT 1", (member_id,)).fetchone()
    from .debicheck import normalise_debicheck_status
    if not cl or cl["debit_check_status"] != "approved" or not mandate or normalise_debicheck_status(mandate["status"]) != "approved":
        missing.append("DebiCheck approval not confirmed for the latest mandate")
    if not cl or cl["contract_status"] != "signed" or not _signed_contract_on_file(db, member_id):
        missing.append(CONTRACT_BLOCKER)
    if not cl or cl["pos_status"] != "paid" or not registration_fee_paid(db, member_id):
        missing.append(REG_FEE_BLOCKER)
    return missing


def _signed_contract_on_file(db, member_id: int) -> bool:
    return bool(
        db.execute("SELECT 1 FROM member_documents WHERE member_id=? AND document_type='Signed Contract' LIMIT 1", (member_id,)).fetchone()
        or db.execute("SELECT 1 FROM contract_signatures WHERE member_id=? AND signer_role='member' LIMIT 1", (member_id,)).fetchone()
        or db.execute("SELECT 1 FROM contract_share_tokens WHERE member_id=? AND status='approved' AND verified_at IS NOT NULL AND responded_at IS NOT NULL LIMIT 1", (member_id,)).fetchone()
    )


def registration_fee_due(joining_fee) -> float:
    """The member's own fee, else the fee set for the Itensity gym."""
    fee = float(joining_fee or 0)
    if fee:
        return fee
    from .integrations import itensity_tenant_enabled
    return float(current_app.config.get("REGISTRATION_FEE_AMOUNT") or 0) if itensity_tenant_enabled() else 0.0


def registration_fee_paid(db, member_id: int) -> bool:
    member = db.execute("SELECT joining_fee FROM members WHERE id=?", (member_id,)).fetchone()
    from .financial_reporting import cash_amount
    paid = sum((cash_amount(row) for row in db.execute(
        "SELECT amount_paid,status,notes FROM collections WHERE member_id=? AND payment_purpose='registration_fee' AND status='paid' AND amount_paid > 0",
        (member_id,),
    )),0)
    return bool(paid > 0 and float(paid) >= registration_fee_due(member["joining_fee"])) if member else False


def compliance_completion_blockers(db, aid: int) -> list[str]:
    application = db.execute("SELECT * FROM membership_applications WHERE id=?", (aid,)).fetchone()
    if not application or not application["member_id"]:
        return ["Member profile missing"]
    missing = _itensity_hard_blockers(db, aid, application["member_id"])
    if application["application_status"] != "active" or application["push_status"] != "pushed":
        missing.append("Itensity activation not complete")
    cl = db.execute("SELECT * FROM compliance_checklists WHERE application_id=?", (aid,)).fetchone()
    if cl and cl["guardian_required"] == "required" and cl["guardian_status"] != "approved":
        missing.append("Guardian consent not approved")
    if cl and cl["manager_approval_status"] not in ("not_required", "approved"):
        missing.append("Manager approval pending")
    return missing


def _sync_compliance_completion(db, aid: int, actor_id=None) -> None:
    cl = db.execute("SELECT overall_status FROM compliance_checklists WHERE application_id=?", (aid,)).fetchone()
    if not cl:
        return
    complete = not compliance_completion_blockers(db, aid)
    overall = "complete" if complete else "incomplete"
    if cl["overall_status"] == overall:
        return
    db.execute("UPDATE compliance_checklists SET overall_status=? WHERE application_id=?", (overall, aid))
    from .sales_kpi import _application_context
    lead_id, member_id, owner_id = _application_context(db, aid)
    # Keep the credit milestone in the source transaction, including activation.
    db.execute(
        """INSERT INTO stage_events (entity_type, entity_id, lead_id, application_id, member_id,
           field, from_value, to_value, owner_id, actor_id, occurred_at)
           VALUES ('compliance', ?, ?, ?, ?, 'overall_status', ?, ?, ?, ?, datetime('now'))""",
        (aid, lead_id, aid, member_id, cl["overall_status"], overall, owner_id, actor_id),
    )


def _outstanding_followups(cl) -> list[dict]:
    """Completion summary; every item here also blocks the Itensity push."""
    if cl is None:
        return []
    return [
        {"label": "Contract signed", "done": cl["contract_status"] == "signed"},
        {"label": "DebiCheck mandate approved", "done": cl["debit_check_status"] == "approved"},
        {"label": "ID copy uploaded", "done": cl["id_copy_status"] in ("uploaded", "verified")},
        {"label": "Registration fee paid", "done": cl["pos_status"] == "paid"},
    ]


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
           WHERE sa.application_id = ? ORDER BY sa.created_at DESC, sa.id DESC LIMIT 1""",
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
        _is_ready_to_push(checklist, _push_analysis(db, app["member_id"], analysis))
        and not _itensity_hard_blockers(db, aid, app["member_id"])
        and app["push_status"] not in ("pushed", "pushing", "push_unknown")
        if checklist else False
    )

    documents = []
    if app["member_id"]:
        import json as _json
        raw_docs = db.execute(
            """SELECT d.*, u.full_name AS uploader
               FROM member_documents d
               LEFT JOIN users u ON d.uploaded_by = u.id
               WHERE d.member_id = ? AND d.document_type IN ('Signed Contract', 'Bank Statement', 'SA ID Copy')
               ORDER BY d.uploaded_at DESC""",
            (app["member_id"],),
        ).fetchall()
        for d in raw_docs:
            row = dict(d)
            row["analysis"] = _json.loads(d["analysis_json"]) if d["analysis_json"] else None
            documents.append(row)

    push_missing = list(dict.fromkeys(
        _missing_requirements(checklist, _push_analysis(db, app["member_id"], analysis))
        + _itensity_hard_blockers(db, aid, app["member_id"])
    )) if checklist else []

    from .itensity_followups import followup_status
    intake_context = request.args.get("intake") == "1" and app["application_status"] != "active"
    nupay_blockers = nupay_submission_blockers(db, app["member_id"]) if app["member_id"] else ["Create a member profile first"]
    latest_mandate = db.execute("SELECT id, status FROM debicheck_mandates WHERE member_id=? ORDER BY id DESC LIMIT 1", (app["member_id"],)).fetchone()
    if intake_context and not nupay_blockers and (session.get("role") == "admin" or "new_debicheck" in session.get("permissions", [])):
        if latest_mandate:
            return redirect(url_for("debicheck.detail", mandate_id=latest_mandate["id"]))
        return redirect(url_for("debicheck.add", member_id=app["member_id"]))

    return render_template(
        "applications/detail.html",
        app=app,
        checklist=checklist,
        analysis=analysis,
        pending_approval=pending_approval,
        trail=trail,
        ready_to_push=ready,
        missing_items=push_missing,
        can_override_push=can_override_push(session.get("role"), push_missing),
        statement_check=(
            statement_check_context(db, app["member_id"])
            if can_override_push(session.get("role"), push_missing) and needs_statement_check(session.get("role"), push_missing)
            else None
        ),
        followups=_outstanding_followups(checklist),
        documents=documents,
        itensity_sent=followup_status(db, app["member_id"]),
        compliance_blockers=compliance_completion_blockers(db, aid),
        doc_types=["Signed Contract", "Bank Statement", "SA ID Copy"],
        is_manager=session.get("role") in MANAGER_ROLES,
        is_admin=session.get("role") == "admin",
        intake_context=intake_context,
        nupay_blockers=nupay_blockers,
        latest_mandate=latest_mandate,
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
    new_status = sync_application_status(db, aid, actor_id=session.get("user_id"))
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

    # Risk is system-derived. A manual override is a controlled admin
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
    risk_rating = requested_risk if (requested_risk and session.get("role") == "admin") else auto_risk
    if requested_risk and requested_risk != auto_risk and session.get("role") != "admin":
        audit_log(db, "risk_override_blocked", application_id=aid, new_value=f"requested={requested_risk};auto={auto_risk}")
        flash("Risk rating is system-controlled. An admin must approve an override.", "warning")

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
    if requested_risk and requested_risk != auto_risk and session.get("role") == "admin":
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
    derived_status = sync_application_status(db, aid, actor_id=session.get("user_id"))
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
           SET push_status='pushing', push_started_at=datetime('now'), updated_at=datetime('now')
           WHERE id=?
             AND push_status NOT IN ('pushing', 'pushed', 'push_unknown')
             AND (application_status <> 'active' OR push_status='reconcile_required')""",
        (aid,),
    )
    return cur.rowcount == 1


def _itensity_result_is_valid(success, itensity_ref) -> bool:
    """A successful API call is not enough; an Itensity member reference is required."""
    return bool(success and itensity_ref and str(itensity_ref).strip())


def _push_actor(conn, user_id, session_version):
    from .permissions import effective_permissions_for
    actor = conn.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
    granted = [r['permission'] for r in conn.execute('SELECT permission FROM user_permissions WHERE user_id=?',(user_id,))]
    if (not actor or not actor['active'] or
            (session_version is not None and session_version != actor['session_version']) or
            (actor['role'] != 'admin' and 'add_member' not in effective_permissions_for(
                actor['role'],actor['department'],granted,actor['is_sales_consultant']))):
        raise ValueError('The requesting account no longer has authority to activate members.')
    return actor


def _background_itensity_push(
    app, db_path: str, aid: int, member_id: int,
    itensity_ref_existing: str | None, overridden: bool, missing: list[str], user_id: int | None,
    reconcile_only: bool = False,
    session_version: int | None = None,
) -> None:
    """Runs the (possibly minutes-long) Itensity browser push off the request
    thread. Opens its own sqlite connection since the request's is gone by
    the time this finishes, and needs app_context for current_app.config
    inside the integrations module — but never touches flask.session, since
    there's no request here to read it from."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    external_attempted = False
    confirmed_ref = None
    with _push_workers_lock:
        _push_workers.add((db_path, aid))
    try:
        with app.app_context():
            from .integrations import bind_itensity_tenant
            bind_itensity_tenant(db_path)
            saved = conn.execute('SELECT itensity_confirmed_ref FROM membership_applications WHERE id=?', (aid,)).fetchone()
            confirmed_ref = saved['itensity_confirmed_ref'] if saved else None
            member = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
            if member is None:
                conn.execute(
                    "UPDATE membership_applications SET push_status='push_failed', updated_at=datetime('now') WHERE id=?",
                    (aid,),
                )
                conn.commit()
                return

            blockers = _itensity_hard_blockers(conn, aid, member_id)
            actor = _push_actor(conn,user_id,session_version)
            override_allowed = overridden and actor and actor["active"] and can_override_push(actor["role"], blockers)
            if blockers and not override_allowed:
                conn.execute("UPDATE membership_applications SET push_status=?, updated_at=datetime('now') WHERE id=?", ('reconcile_required' if confirmed_ref else 'push_failed', aid))
                member_activity(conn, member_id, "Itensity push blocked: " + "; ".join(blockers), user_id=user_id)
                conn.commit()
                return
            member_data = decrypt_member(member)
            # Itensity has one shared login, so its sales rep field is where the
            # person who loaded the client shows.
            loader = conn.execute("SELECT full_name FROM users WHERE id=?", (_loaded_by(conn, aid),)).fetchone()
            if loader and loader["full_name"]:
                member_data["sales_person"] = loader["full_name"]
            if reconcile_only or (saved and saved['itensity_confirmed_ref']):
                confirmed_ref = saved['itensity_confirmed_ref'] if saved else None
                if not confirmed_ref:
                    raise ValueError('No confirmed external reference is available for recovery.')
                success, itensity_ref, error_msg = True, confirmed_ref, None
            elif itensity_ref_existing:
                external_attempted = True
                success, error_msg = update_member_in_itensity(itensity_ref_existing, member_data)
                itensity_ref = itensity_ref_existing
            else:
                external_attempted = True
                success, itensity_ref, error_msg = push_member_to_itensity(member_data)

            if not _itensity_result_is_valid(success, itensity_ref):
                failure = error_msg or "Itensity did not return a valid member reference."
                # Stopped before Submit means no member can exist, so staff can
                # fix the cause and retry without an outcome review.
                failed_status = 'push_failed' if isinstance(error_msg, ItensityNotSubmitted) else 'push_unknown'
                conn.execute(
                    "UPDATE membership_applications SET push_status=?, updated_at=datetime('now') WHERE id=?",
                    (failed_status, aid),
                )
                audit_log(conn, "itensity_push_failed", application_id=aid, new_value=failure, user_id=user_id)
                member_activity(conn, member_id, f"Itensity push failed: {failure}", user_id=user_id)
                conn.commit()
                return

            # Persist the external outcome before starting local activation.
            # A later local rollback must never erase the identity already created.
            confirmed_ref = str(itensity_ref).strip()
            conn.execute('UPDATE membership_applications SET itensity_confirmed_ref=? WHERE id=?',
                         (confirmed_ref, aid))
            audit_log(conn, 'itensity_external_confirmed', application_id=aid,
                      new_value=confirmed_ref, user_id=user_id)
            conn.commit()

            actor = _push_actor(conn,user_id,session_version)
            blockers = _itensity_hard_blockers(conn,aid,member_id)
            if blockers and not (overridden and can_override_push(actor['role'], blockers)):
                raise ValueError('Activation evidence changed after the external submission: '+ '; '.join(blockers))
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
                    f"Pushed to Itensity via {actor['role'].upper()} OVERRIDE (incomplete: {', '.join(missing)}). Ref: {itensity_ref}",
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
            _sync_compliance_completion(conn, aid, actor_id=user_id)
            conn.commit()
            if app.config.get("ITENSITY_AUTO_FOLLOWUPS"):
                from .itensity_followups import run_followups
                run_followups(app, db_path, aid, member_id, user_id)
    except Exception:
        app.logger.exception("Background Itensity push crashed for application %s", aid)
        try:
            conn.rollback()
            status = 'reconcile_required' if confirmed_ref else 'push_unknown' if external_attempted else 'push_failed'
            conn.execute(
                "UPDATE membership_applications SET push_status=?, itensity_confirmed_ref=COALESCE(?,itensity_confirmed_ref), updated_at=datetime('now') WHERE id=?",
                (status, confirmed_ref, aid),
            )
            audit_log(conn, 'itensity_recovery_required', application_id=aid,
                      new_value=status, user_id=user_id)
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
        with _push_workers_lock:
            _push_workers.discard((db_path, aid))


@applications_bp.post('/<int:aid>/review-push')
@permission_required('add_member')
def review_push(aid):
    """Resolve unknown/interrupted outcomes only after an explicit external check."""
    if session.get('role') != 'admin':
        abort(403)
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    application = db.execute('SELECT * FROM membership_applications WHERE id=?', (aid,)).fetchone()
    if not application:
        abort(404)
    with _push_workers_lock:
        db_path = db.execute('PRAGMA database_list').fetchone()['file']
        running = (db_path, aid) in _push_workers
    started = application['push_started_at'] or application['updated_at']
    if started:
        parsed = datetime.fromisoformat(str(started).replace('Z','+00:00'))
        parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        stale = parsed < datetime.now(timezone.utc) - timedelta(minutes=30)
    else:
        stale = True  # Legacy interrupted records can lack a start timestamp.
    legacy_inconsistent = application['application_status'] == 'active' and application['push_status'] == 'push_failed'
    if running or (application['push_status'] not in ('push_unknown', 'pushing') and not legacy_inconsistent) or (application['push_status'] == 'pushing' and not stale):
        abort(409, description='This push is still running or does not require outcome review.')
    notes = request.form.get('notes', '').strip()
    reference = request.form.get('itensity_ref', '').strip()
    outcome = request.form.get('outcome')
    if not notes or outcome not in ('found', 'not_created') or (outcome == 'found' and not reference):
        abort(400, description='Record the external check and its confirmed outcome.')
    member = db.execute('SELECT itensity_ref FROM members WHERE id=?',(application['member_id'],)).fetchone()
    known_ref = application['itensity_confirmed_ref'] or (member['itensity_ref'] if member else None)
    if outcome == 'not_created' and (known_ref or application['application_status']=='active'):
        abort(409, description='A confirmed external reference already exists; reconcile it instead.')
    if outcome == 'found' and known_ref and reference != str(known_ref).strip():
        abort(409, description='The reviewed reference must match the existing external identity.')
    if outcome == 'found' and db.execute('SELECT id FROM members WHERE itensity_ref=? AND id<>?',
                                        (reference,application['member_id'])).fetchone():
        abort(409, description='This reference is already linked to another member.')
    if outcome == 'found' and db.execute('SELECT id FROM membership_applications WHERE itensity_confirmed_ref=? AND member_id<>?',
                                        (reference,application['member_id'])).fetchone():
        abort(409, description='This reference is already confirmed for another member.')
    db.execute('UPDATE membership_applications SET push_status=?,itensity_confirmed_ref=?,updated_at=datetime(\'now\') WHERE id=?',
               ('reconcile_required' if outcome == 'found' else 'push_failed', reference if outcome == 'found' else None, aid))
    audit_log(db, 'itensity_outcome_reviewed', application_id=aid,
              new_value=f'{outcome}; reference={reference}; {notes}', user_id=session['user_id'])
    db.commit()
    return redirect(url_for('applications.application_detail', aid=aid))


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

    from .integrations import itensity_tenant_enabled, itensity_writes_allowed
    if not itensity_tenant_enabled():
        abort(403, description='The shared Itensity integration is not configured for this gym.')
    # Completing a local activation writes nothing to Itensity.
    if application['push_status'] != 'reconcile_required' and not itensity_writes_allowed(aid):
        flash("Pushing to Itensity is switched off for this application.", "warning")
        return redirect(url_for("applications.application_detail", aid=aid))
    if application["push_status"] in ("pushing", "pushed", "push_unknown") or (application["application_status"] == "active" and application['push_status'] != 'reconcile_required'):
        flash(
            "This application is already being processed, already pushed, or requires an administrator to review its external outcome.",
            "warning",
        )
        return redirect(url_for("applications.application_detail", aid=aid))

    cl = db.execute(
        "SELECT * FROM compliance_checklists WHERE application_id=?", (aid,)
    ).fetchone()

    analysis = db.execute(
        "SELECT * FROM statement_analyses WHERE application_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
        (aid,),
    ).fetchone()

    missing = list(dict.fromkeys(
        _missing_requirements(cl, _push_analysis(db, application["member_id"], analysis))
        + _itensity_hard_blockers(db, aid, application["member_id"])
    ))
    overridden = can_override_push(session.get("role"), missing) and request.form.get("override") == "1"

    if missing and not overridden:
        flash(f"Application is missing: {', '.join(missing)}.", "error")
        return redirect(url_for("applications.application_detail", aid=aid))

    override_record = "Missing requirements: " + "; ".join(missing)
    if overridden and needs_statement_check(session.get("role"), missing):
        problem = statement_check_problem(db, application["member_id"])
        if problem:
            flash(problem, "error")
            return redirect(url_for("applications.application_detail", aid=aid))
        override_record += "; " + _statement_check_record(db, application["member_id"])

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
    if overridden:
        audit_log(db, f"itensity_{session.get('role')}_override_requested", application_id=aid,
                  new_value=override_record, user_id=user_id)
    db.commit()

    threading.Thread(
        target=_background_itensity_push,
        args=(app_obj, db_path, aid, member["id"], member["itensity_ref"], overridden, missing, user_id,
              application['push_status'] == 'reconcile_required', session.get('session_version',0)),
        daemon=True,
    ).start()

    flash(
        "Itensity push started in the background — this can take a few minutes. "
        "Refresh this page to check the result.",
        "info",
    )
    return redirect(url_for("applications.application_detail", aid=aid))


def _loaded_by(conn, application_id: int) -> int | None:
    """The staff member who loaded this client on the app."""
    row = conn.execute(
        """SELECT COALESCE(ma.created_by, m.uploaded_by_id) AS loaded_by FROM membership_applications ma
           JOIN members m ON m.id = ma.member_id WHERE ma.id = ?""",
        (application_id,),
    ).fetchone()
    return row["loaded_by"] if row else None


def push_ready_applications(app, db_path: str, from_date: str) -> list[int]:
    """Push each new application that meets every requirement, one at a time,
    each under the person who loaded the client.

    Only an application that has never been tried is picked up: a stopped push
    needs a person to look at why, and from_date keeps older clients out.
    """
    pushed = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with app.app_context():
            from .integrations import bind_itensity_tenant, itensity_writes_allowed
            bind_itensity_tenant(db_path)
            candidates = conn.execute(
                """SELECT ma.id, ma.member_id FROM membership_applications ma
                   JOIN members m ON m.id = ma.member_id
                   WHERE ma.push_status = 'not_pushed'
                     AND ma.application_status <> 'active'
                     AND ma.itensity_confirmed_ref IS NULL
                     AND (m.itensity_ref IS NULL OR TRIM(m.itensity_ref) = '')
                     AND date(ma.created_at) >= date(?)
                   ORDER BY ma.id""",
                (from_date,),
            ).fetchall()
            for candidate in candidates:
                aid, member_id = candidate["id"], candidate["member_id"]
                if not itensity_writes_allowed(aid):
                    continue
                cl = conn.execute("SELECT * FROM compliance_checklists WHERE application_id=?", (aid,)).fetchone()
                analysis = conn.execute(
                    "SELECT * FROM statement_analyses WHERE application_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                    (aid,),
                ).fetchone()
                if not cl or not _is_ready_to_push(cl, _push_analysis(conn, member_id, analysis)) or _itensity_hard_blockers(conn, aid, member_id):
                    continue
                actor_id = _loaded_by(conn, aid)
                try:
                    _push_actor(conn, actor_id, None)
                except ValueError:
                    app.logger.warning("Automatic Itensity push waiting on application %s: "
                                       "the person who loaded it can't activate members", aid)
                    continue
                if not _mark_itensity_push_started(conn, aid):
                    conn.rollback()
                    continue
                audit_log(conn, "itensity_auto_push_started", application_id=aid,
                          new_value="All requirements in place", user_id=actor_id)
                member_activity(conn, member_id, "Automatic Itensity push started: all requirements in place", user_id=actor_id)
                conn.commit()
                _background_itensity_push(app, db_path, aid, member_id, None, False, [], actor_id)
                pushed.append(aid)
    finally:
        conn.close()
    return pushed


def _start_itensity_followup(aid: int, part: str):
    """Retry one follow-up in the background, after the member exists in Itensity."""
    from .integrations import itensity_tenant_enabled, itensity_writes_allowed
    from .itensity_followups import run_followups
    db = get_db()
    application = db.execute("SELECT * FROM membership_applications WHERE id=?", (aid,)).fetchone()
    if application is None:
        abort(404)
    if not itensity_tenant_enabled():
        abort(403, description='The shared Itensity integration is not configured for this gym.')
    if not itensity_writes_allowed(aid):
        flash("Pushing to Itensity is switched off for this application.", "warning")
    elif application["push_status"] != "pushed":
        flash("Push the member to Itensity first.", "warning")
    else:
        threading.Thread(
            target=run_followups,
            args=(current_app._get_current_object(), db.execute("PRAGMA database_list").fetchone()["file"],
                  aid, application["member_id"], session.get("user_id"), (part,)),
            daemon=True,
        ).start()
        flash("Started in the background. Refresh this page to check the result.", "info")
    return redirect(url_for("applications.application_detail", aid=aid))


@applications_bp.post("/<int:aid>/itensity/documents")
@permission_required("add_member")
def send_itensity_documents(aid: int):
    return _start_itensity_followup(aid, "documents")


@applications_bp.post("/<int:aid>/itensity/reg-fee")
@permission_required("add_member")
def send_itensity_reg_fee(aid: int):
    return _start_itensity_followup(aid, "reg_fee")


@applications_bp.post("/<int:aid>/itensity/reg-fee/review")
@permission_required("add_member")
def review_itensity_reg_fee(aid: int):
    """Settle an uncertain payment only after someone has checked the account."""
    if session.get("role") != "admin":
        abort(403)
    db = get_db()
    application = db.execute("SELECT member_id FROM membership_applications WHERE id=?", (aid,)).fetchone()
    if application is None:
        abort(404)
    notes = request.form.get("notes", "").strip()
    outcome = {"found": "sent", "not_recorded": "failed"}.get(request.form.get("outcome"))
    if not notes or not outcome:
        abort(400, description="Record the external check and its confirmed outcome.")
    updated = db.execute(
        """UPDATE itensity_reg_fee_pushes SET status=?, review_notes=?, reviewed_by=?,
               confirmed_at=CASE WHEN ?='sent' THEN datetime('now') END
           WHERE member_id=? AND status IN ('unknown','sending')""",
        (outcome, notes, session.get("user_id"), outcome, application["member_id"]),
    )
    if updated.rowcount != 1:
        abort(409, description="This payment does not need reviewing.")
    audit_log(db, "itensity_reg_fee_reviewed", application_id=aid,
              new_value=f"{outcome}; {notes}", user_id=session.get("user_id"))
    db.commit()
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
    derived_status = sync_application_status(db, aid, actor_id=session.get("user_id"))
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
