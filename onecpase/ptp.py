"""PTP / Collection module for 1Cpase."""
import os
import time
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.utils import secure_filename

from .auth import permission_required, roles_required
from .database import get_db, member_activity
from .collections_engine import (
    apply_manager_exception,
    calculate_realistic_recovery_plan,
    create_collection_exception,
    evaluate_collection_case,
    evaluate_member_access,
    evaluate_ptp,
    get_collection_rules,
    grant_exception_access,
    record_access_decision,
    reconcile_open_cases,
    refresh_member_cases,
    record_discount_request,
    return_discount_request,
    settle_exception_for_arrangement,
    supersede_open_ptps,
    sync_collection_case,
)

ptp_bp = Blueprint("ptp", __name__)

UPFRONT_RECOVERY_PERCENT = 30    # upfront share of the discounted balance
ARREARS_RESTRICTION_MONTHS = 2   # Rule §7/§12: more than two months owing
INACTIVE_AFTER_MONTHS = 6        # Rule §15: unpaid for more than six months

CALL_CODES = {
    1:  "Agreement made for arrears",
    2:  "Appointment booked",
    3:  "Not available / no answer",
    4:  "Straight to voicemail",
    5:  "Appointment cancelled",
    6:  "Cancellation with fee",
    7:  "Cancellation: membership expired",
    8:  "Medically unfit — proof required",
    9:  "Relocation — proof required",
    10: "Appointment rescheduled",
}

PTP_PAYMENT_METHODS = [
    ("cash",         "Cash"),
    ("eft",          "EFT"),
    ("debit_order",  "Debit Order"),
    ("card",         "Card Payment"),
    ("salary_debit", "Salary Date Debit"),
]

ARRANGEMENT_TYPES = [
    ("full",        "Full Payment"),
    ("split",       "Split into Two"),
    ("topup",       "Top-Up Method"),
    ("extension",   "Extension Method"),
    ("end_of_term", "End-of-Term Addition"),
]

PTP_STATUSES = [
    ("pending",         "Pending"),
    ("paid",            "Paid"),
    ("partially_paid",  "Partially Paid"),
    ("broken",          "Broken"),
    ("rescheduled",     "Rescheduled"),
    ("cancelled",       "Cancelled"),
    ("escalated",       "Escalated"),
    ("superseded",      "Superseded"),
]

ALLOWED_RECEIPT_EXT = {"pdf", "jpg", "jpeg", "png"}


def _allowed_receipt(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_RECEIPT_EXT


def compute_arrears_from_profile(payment_profile: list) -> dict:
    """Derive arrears info from a pre-built payment_profile list."""
    arrears_rows = [r for r in payment_profile if r["balance"] > 0]
    total_arrears = sum(r["balance"] for r in arrears_rows)
    months_in_arrears = len(arrears_rows)
    oldest_arrears_month = (
        min((r["date"] or "")[:7] for r in arrears_rows if r["date"])
        if arrears_rows else None
    )

    if months_in_arrears == 0:
        cat, label, action = None, "Up to date", "No action required."
    elif months_in_arrears == 1:
        cat, label, action = (
            "A", "Category A: 1 Month in Arrears",
            "Call client and request immediate payment.",
        )
    elif months_in_arrears <= 3:
        cat, label, action = (
            "B", f"Category B: {months_in_arrears} Months in Arrears",
            "Call client, consider blocking access, arrange PTP or payment plan.",
        )
    else:
        cat, label, action = (
            "C", f"Category C: {months_in_arrears} Months in Arrears",
            "Call client, arrange payment plan, or escalate to management.",
        )

    return {
        "total_arrears": total_arrears,
        "months_in_arrears": months_in_arrears,
        "oldest_arrears_month": oldest_arrears_month,
        "category": cat,
        "category_label": label,
        "recommended_action": action,
    }


def bulk_member_arrears(db, member_ids=None) -> dict:
    """Reconciled arrears per member — same payment-matching logic as the
    member account page, batched into one query instead of N+1 per-member
    fetches. Each billing cycle is stored as a separate charge row and a
    separate payment row; only compute_arrears_from_profile() correctly
    matches them, unlike a raw SUM(outstanding_balance)."""
    from collections import defaultdict
    from .members import _build_payment_profile

    query = "SELECT * FROM collections"
    params: list = []
    if member_ids is not None:
        member_ids = list(member_ids)
        if not member_ids:
            return {}
        query += f" WHERE member_id IN ({','.join('?' for _ in member_ids)})"
        params = member_ids
    query += " ORDER BY member_id, collection_date DESC, id DESC"

    by_member = defaultdict(list)
    for row in db.execute(query, params).fetchall():
        by_member[row["member_id"]].append(row)

    return {
        mid: compute_arrears_from_profile(_build_payment_profile(rows))
        for mid, rows in by_member.items()
    }


def _debt_collection_member_rows(db) -> list[dict]:
    """Build the debt collection working set once for the dashboard and legal views."""
    base_rows = db.execute("""
        SELECT
            m.id,
            m.member_ref,
            m.first_name,
            m.last_name,
            m.contact,
            m.member_status,
            COALESCE(m.gym_access_status, 'allowed') AS gym_access_status,
            COALESCE(m.monthly_installment, 0) AS monthly_installment,
            m.tariff,
            u.full_name AS consultant_name,
            MAX(CASE WHEN COALESCE(c.amount_paid, 0) > 0 THEN c.collection_date ELSE NULL END) AS last_payment,
            MAX(CASE WHEN c.status = 'failed' THEN c.collection_date ELSE NULL END) AS last_failed
        FROM members m
        JOIN collections c ON c.member_id = m.id
        LEFT JOIN users u ON m.uploaded_by_id = u.id
        WHERE m.member_status IN ('Active', 'Frozen', 'Cancelled', 'Inactive')
        GROUP BY m.id
    """).fetchall()

    arrears_by_member = bulk_member_arrears(db, (r["id"] for r in base_rows))

    ptp_index = {}
    for row in db.execute("""
        SELECT member_id, ptp_status, promise_date, promise_amount, id
        FROM ptp_agreements
        WHERE ptp_status IN ('pending', 'partially_paid', 'broken')
        ORDER BY created_at DESC
    """).fetchall():
        ptp_index.setdefault(row["member_id"], dict(row))

    contact_index = {}
    for row in db.execute("""
        SELECT cl.member_id, cl.created_at, cl.call_code, cl.contact_method,
               u.full_name AS consultant_name
        FROM contact_logs cl
        LEFT JOIN users u ON cl.consultant_id = u.id
        ORDER BY cl.created_at DESC
    """).fetchall():
        contact_index.setdefault(row["member_id"], dict(row))

    legal_index = {}
    for row in db.execute("""
        SELECT id, member_id, referred_at, status, arrears_months, arrears_amount, reason, notes
        FROM legal_referrals
        ORDER BY referred_at DESC
    """).fetchall():
        legal_index.setdefault(row["member_id"], dict(row))

    def _category(total_arrears, monthly):
        if not monthly or monthly <= 0:
            return "C", "Cat C"
        months = total_arrears / monthly
        if months < 1.5:
            return "A", "Cat A"
        if months <= 3.5:
            return "B", "Cat B"
        return "C", "Cat C"

    members = []
    for row in base_rows:
        arrears = arrears_by_member.get(row["id"], {})
        months_in_arrears = arrears.get("months_in_arrears", 0)
        if months_in_arrears <= ARREARS_RESTRICTION_MONTHS:
            continue
        cat, cat_label = _category(arrears.get("total_arrears", 0), row["monthly_installment"])
        ptp = ptp_index.get(row["id"])
        contact = contact_index.get(row["id"])
        legal = legal_index.get(row["id"])
        members.append({
            "id": row["id"],
            "member_ref": row["member_ref"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "name": f"{row['first_name']} {row['last_name']}",
            "contact": row["contact"],
            "member_status": row["member_status"],
            "access_status": row["gym_access_status"],
            "monthly_installment": row["monthly_installment"],
            "tariff": row["tariff"],
            "consultant": row["consultant_name"],
            "total_arrears": arrears.get("total_arrears", 0),
            "months_in_arrears": months_in_arrears,
            "oldest_arrears_month": arrears.get("oldest_arrears_month"),
            "last_payment": row["last_payment"],
            "last_failed": row["last_failed"],
            "category": cat,
            "category_label": cat_label,
            "ptp_status": ptp["ptp_status"] if ptp else None,
            "ptp_promise_date": ptp["promise_date"] if ptp else None,
            "ptp_id": ptp["id"] if ptp else None,
            "last_contact_at": contact["created_at"] if contact else None,
            "last_contact_code": contact["call_code"] if contact else None,
            "last_contact_consultant": contact["consultant_name"] if contact else None,
            "legal_status": legal["status"] if legal else None,
            "legal_referral_id": legal["id"] if legal else None,
            "legal_referred_at": legal["referred_at"] if legal else None,
            "legal_reason": legal["reason"] if legal else None,
        })

    members.sort(key=lambda row: (row["months_in_arrears"], row["total_arrears"]), reverse=True)
    return members


# ── Dashboard ──────────────────────────────────────────────────────────────

@ptp_bp.route("/ptp/")
@permission_required("all_collections")
def ptp_dashboard():
    db  = get_db()
    today = date.today().isoformat()
    members = _debt_collection_member_rows(db)

    # KPIs
    total_clients  = len(members)
    total_arrears_r = sum(m["total_arrears"] for m in members)
    cat_a = sum(1 for m in members if m["category"] == "A")
    cat_b = sum(1 for m in members if m["category"] == "B")
    cat_c = sum(1 for m in members if m["category"] == "C")
    blocked = sum(1 for m in members if m["access_status"] == "blocked")
    legal_candidate_count = sum(
        1 for m in members
        if m["months_in_arrears"] > INACTIVE_AFTER_MONTHS and m["legal_status"] != "open"
    )

    ptps_pending  = db.execute("SELECT COUNT(*) FROM ptp_agreements WHERE ptp_status='pending'").fetchone()[0]
    ptps_due_today = db.execute(
        "SELECT COUNT(*) FROM ptp_agreements WHERE promise_date=? AND ptp_status='pending'", (today,)
    ).fetchone()[0]
    ptps_broken = db.execute("SELECT COUNT(*) FROM ptp_agreements WHERE ptp_status='broken'").fetchone()[0]
    restricted_count = db.execute(
        "SELECT COUNT(*) FROM members WHERE gym_access_status='blocked' AND access_block_reason='arrears_no_arrangement'"
    ).fetchone()[0]
    inactive_count = db.execute(
        "SELECT COUNT(*) FROM members WHERE member_status='Inactive'"
    ).fetchone()[0]
    legal_referral_count = db.execute(
        "SELECT COUNT(*) FROM legal_referrals WHERE status='open'"
    ).fetchone()[0]

    # The rule engine already produces these; without them on the dashboard a
    # manager has no signal that anything is waiting on a decision, and no
    # visibility of whether today's call workload is actually being worked.
    from .collections import daily_call_progress, pending_manager_decisions
    call_progress = daily_call_progress(db)
    manager_queue = pending_manager_decisions(db)

    # Filters
    cat_filter    = request.args.get("cat", "").strip()
    status_filter = request.args.get("status", "").strip()
    search        = request.args.get("q", "").strip()

    filtered = members
    if cat_filter:
        filtered = [m for m in filtered if m["category"] == cat_filter]
    if status_filter == "blocked":
        filtered = [m for m in filtered if m["access_status"] == "blocked"]
    elif status_filter == "ptp_pending":
        filtered = [m for m in filtered if m["ptp_status"] == "pending"]
    elif status_filter == "ptp_broken":
        filtered = [m for m in filtered if m["ptp_status"] == "broken"]
    elif status_filter == "ptp_due":
        filtered = [m for m in filtered if m["ptp_promise_date"] == today]
    elif status_filter == "legal":
        filtered = [
            m for m in filtered
            if m["months_in_arrears"] > INACTIVE_AFTER_MONTHS and m["legal_status"] != "open"
        ]
    if search:
        sl = search.lower()
        filtered = [m for m in filtered
                    if sl in m["name"].lower()
                    or (m["contact"] and sl in m["contact"])]

    return render_template(
        "ptp/dashboard.html",
        members=filtered,
        total_clients=total_clients,
        total_arrears=total_arrears_r,
        cat_a=cat_a, cat_b=cat_b, cat_c=cat_c,
        blocked=blocked,
        ptps_pending=ptps_pending,
        ptps_due_today=ptps_due_today,
        ptps_broken=ptps_broken,
        restricted_count=restricted_count,
        inactive_count=inactive_count,
        legal_candidate_count=legal_candidate_count,
        legal_referral_count=legal_referral_count,
        call_progress=call_progress,
        manager_queue=manager_queue,
        cat_filter=cat_filter,
        status_filter=status_filter,
        search=search,
        today=today,
        call_codes=CALL_CODES,
    )


@ptp_bp.route("/ptp/legal")
@permission_required("all_collections")
def legal_referrals():
    db = get_db()
    members = _debt_collection_member_rows(db)
    candidates = [
        row for row in members
        if row["months_in_arrears"] > INACTIVE_AFTER_MONTHS and row["legal_status"] != "open"
    ]
    open_referrals = db.execute("""
        SELECT lr.id, lr.member_id, lr.referred_at, lr.status, lr.arrears_months,
               lr.arrears_amount, lr.reason, lr.notes, lr.resolved_at,
               m.first_name, m.last_name, m.member_ref, m.contact, m.member_status,
               u.full_name AS referred_by_name
        FROM legal_referrals lr
        JOIN members m ON m.id = lr.member_id
        LEFT JOIN users u ON u.id = lr.referred_by
        WHERE lr.status = 'open'
        ORDER BY lr.referred_at DESC, lr.id DESC
    """).fetchall()
    return render_template(
        "ptp/legal.html",
        today=date.today().isoformat(),
        candidates=candidates,
        open_referrals=open_referrals,
        candidate_count=len(candidates),
        open_referral_count=len(open_referrals),
        legal_threshold=INACTIVE_AFTER_MONTHS,
    )


@ptp_bp.post("/members/<int:mid>/legal/refer")
@permission_required("all_collections")
def refer_to_legal(mid: int):
    db = get_db()
    member = db.execute(
        "SELECT id, first_name, last_name FROM members WHERE id=?",
        (mid,),
    ).fetchone()
    if not member:
        abort(404)

    summary = bulk_member_arrears(db, [mid]).get(mid, {})
    months_in_arrears = summary.get("months_in_arrears", 0)
    total_arrears = summary.get("total_arrears", 0)
    if months_in_arrears <= INACTIVE_AFTER_MONTHS:
        flash("This client must owe more than 6 months before a legal referral can be recorded.", "warning")
        return redirect(url_for("ptp.legal_referrals"))

    existing = db.execute(
        "SELECT id, status FROM legal_referrals WHERE member_id=?",
        (mid,),
    ).fetchone()
    reason = (request.form.get("reason") or "More than 6 months in arrears").strip()
    notes = (request.form.get("notes") or "").strip()

    if existing:
        db.execute(
            """UPDATE legal_referrals
               SET referred_by=?, referred_at=datetime('now','localtime'),
                   status='open', arrears_months=?, arrears_amount=?, reason=?, notes=?,
                   resolved_by=NULL, resolved_at=NULL
               WHERE member_id=?""",
            (
                session.get("user_id"),
                months_in_arrears,
                total_arrears,
                reason,
                notes,
                mid,
            ),
        )
    else:
        db.execute(
            """INSERT INTO legal_referrals
               (member_id, referred_by, referred_at, status, arrears_months,
                arrears_amount, reason, notes)
               VALUES (?, ?, datetime('now','localtime'), 'open', ?, ?, ?, ?)""",
            (
                mid,
                session.get("user_id"),
                months_in_arrears,
                total_arrears,
                reason,
                notes,
            ),
        )

    member_activity(
        db,
        mid,
        f"Referred to Legal for {months_in_arrears} months in arrears. {reason}".strip(),
    )
    db.commit()
    flash(f"{member['first_name']} {member['last_name']} referred to Legal.", "success")
    return redirect(url_for("ptp.legal_referrals"))


@ptp_bp.post("/members/<int:mid>/legal/resolve")
@permission_required("all_collections")
def resolve_legal_referral(mid: int):
    db = get_db()
    referral = db.execute(
        "SELECT id FROM legal_referrals WHERE member_id=? AND status='open'",
        (mid,),
    ).fetchone()
    if not referral:
        flash("No open legal referral found for that client.", "warning")
        return redirect(url_for("ptp.legal_referrals"))

    db.execute(
        """UPDATE legal_referrals
           SET status='resolved', resolved_by=?, resolved_at=datetime('now','localtime')
           WHERE id=?""",
        (session.get("user_id"), referral["id"]),
    )
    member = db.execute("SELECT first_name, last_name FROM members WHERE id=?", (mid,)).fetchone()
    member_activity(db, mid, "Legal referral marked as resolved.")
    db.commit()
    flash(f"{member['first_name']} {member['last_name']} marked as resolved.", "success")
    return redirect(url_for("ptp.legal_referrals"))


# ── Automations ────────────────────────────────────────────────────────────

def _has_qualifying_arrangement(db, member_id: int) -> bool:
    """PTP + manager-approved (when required) confirmed DebiCheck mandate."""
    return db.execute(
        """SELECT 1 FROM ptp_agreements p
           JOIN debicheck_mandates d ON d.member_id = p.member_id
           WHERE p.member_id = ? AND p.ptp_status IN ('pending', 'partially_paid')
             AND COALESCE(p.manager_approval_status, 'not_required') IN ('not_required','approved')
             AND lower(COALESCE(d.status,'')) IN
                 ('approved','authorised','authorized','accepted','active','completed')
           LIMIT 1""",
        (member_id,),
    ).fetchone() is not None


def _has_submitted_mandate(db, member_id: int) -> bool:
    """Compatibility helper: only a confirmed mandate qualifies for rules."""
    return db.execute(
        """SELECT 1 FROM debicheck_mandates
           WHERE member_id=? AND lower(COALESCE(status,'')) IN
             ('approved','authorised','authorized','accepted','active','completed')
           LIMIT 1""",
        (member_id,),
    ).fetchone() is not None


RULE_7_EXCEPTION_REASON = (
    "Three or more months owing with no qualifying DebiCheck arrangement or full settlement"
)

AUTO_SETTLEMENT_DISCOUNT_CEILING = 50
MINIMUM_RECOVERABLE_BALANCE = 1800.0
MINIMUM_RECOVERY_INSTALLMENT = 150.0
NORMAL_RECOVERY_TERM_MONTHS = 12


def _broken_promise_hold(db, member_id: int) -> bool:
    """True while a broken promise still holds a member's access blocked: the
    promise was broken and no later promise has been honoured (paid). The
    member paying their balance also lifts it, but that is judged by the
    caller, which knows whether anything is still owing."""
    return db.execute(
        """SELECT 1 FROM ptp_agreements b
           WHERE b.member_id=? AND b.ptp_status='broken'
             AND NOT EXISTS (
                 SELECT 1 FROM ptp_agreements p
                 WHERE p.member_id=b.member_id AND p.ptp_status='paid' AND p.id>b.id
             )
           LIMIT 1""",
        (member_id,),
    ).fetchone() is not None


def _run_collections_automations(db, today: str, dry_run: bool = False) -> dict:
    """Sweep PTPs, temp unblocks, and arrears thresholds — used by the manual
    'Run Automations' button. With dry_run=True, only counts what *would*
    happen (no writes, no commit) so the dashboard can preview the impact
    before staff confirm the real run."""
    broken_count = expired_count = restricted_count = inactive_count = 0

    # Mark broken PTPs: the per-row BROKEN/ACTIVE/PAID call is evaluate_ptp()'s
    # job (collections_engine.py) - this query only fetches what it needs to
    # decide, one row of overdue-by-itself pending PTPs would previously have
    # decided in raw SQL.
    candidates = db.execute("""
        SELECT id, member_id, auto_block_if_failed, promise_date,
               EXISTS (
                   SELECT 1 FROM ptp_receipts r
                   WHERE r.ptp_id = ptp_agreements.id
                     AND r.verification_status = 'verified'
               ) AS has_verified_payment
        FROM ptp_agreements
        WHERE ptp_status = 'pending'
    """).fetchall()
    overdue = [
        ptp for ptp in candidates
        if evaluate_ptp(ptp["promise_date"], bool(ptp["has_verified_payment"]), today=today) == "BROKEN"
    ]

    for ptp in overdue:
        if not dry_run:
            db.execute(
                "UPDATE ptp_agreements SET ptp_status='broken', updated_at=datetime('now','localtime') WHERE id=?",
                (ptp["id"],),
            )
            if ptp["auto_block_if_failed"]:
                db.execute(
                    "UPDATE members SET gym_access_status='blocked', access_block_reason='ptp_broken' WHERE id=?",
                    (ptp["member_id"],),
                )
            member_activity(db, ptp["member_id"],
                            "PTP broken automatically: promise date passed without verified payment.")
            refresh_member_cases(db, ptp["member_id"], source="ptp_monitor",
                                 reason="Promise date passed without verified payment")
        broken_count += 1

    # Expire temp unblocks
    expired = db.execute("""
        SELECT id FROM members
        WHERE gym_access_status = 'temp_unblocked'
          AND access_blocked_until IS NOT NULL
          AND access_blocked_until < ?
    """, (today,)).fetchall()

    for m in expired:
        if not dry_run:
            db.execute(
                """UPDATE members SET gym_access_status='blocked', access_blocked_until=NULL,
                   access_block_reason='temp_unblock_expired' WHERE id=?""",
                (m["id"],),
            )
            member_activity(db, m["id"], "Temporary unblock expired — access blocked automatically.")
        expired_count += 1

    # Members with any outstanding balance, evaluated for auto-restriction / auto-inactive
    owing_members = db.execute("""
        SELECT DISTINCT m.id, m.gym_access_status, m.member_status, m.access_block_reason,
               COALESCE(m.monthly_installment, 0) AS monthly_installment
        FROM members m JOIN collections c ON c.member_id = m.id
        WHERE c.status IN ('failed', 'pending', 'partial')
          AND COALESCE(c.outstanding_balance, 0) > COALESCE(c.amount_paid, 0)
    """).fetchall()
    arrears_by_member = bulk_member_arrears(db, (m["id"] for m in owing_members))
    collection_rules = get_collection_rules(db)

    for member in owing_members:
        arrears = arrears_by_member.get(member["id"])
        if not arrears:
            continue

        oldest = arrears["oldest_arrears_month"]
        decision = evaluate_collection_case(
            arrears["months_in_arrears"],
            arrears["total_arrears"],
            _has_qualifying_arrangement(db, member["id"]),
            rules=collection_rules,
            oldest_arrears_month=oldest,
            today=today,
        )
        decision = apply_manager_exception(db, member["id"], decision, today=today)
        should_restrict = (
            decision["access"] == "BLOCKED"
            and member["gym_access_status"] != "blocked"
        )
        should_restore = (
            decision["access"] == "ALLOWED"
            and member["gym_access_status"] == "blocked"
            and str(member["access_block_reason"] or "").startswith(("arrears_", "ptp_", "temp_unblock_"))
            # A broken promise keeps access blocked until it is honoured or the
            # member pays; the policy allowing access at low arrears is not enough.
            and not (member["access_block_reason"] == "ptp_broken"
                     and _broken_promise_hold(db, member["id"]))
        )
        should_inactivate = (
            decision["status"] == "INACTIVE"
            and member["member_status"] not in ("Inactive", "Cancelled")
        )

        if should_restrict:
            restricted_count += 1
        if should_inactivate:
            inactive_count += 1

        if not dry_run:
            case_id = sync_collection_case(
                db,
                member["id"],
                arrears_amount=arrears["total_arrears"],
                months_owing=arrears["months_in_arrears"],
                monthly_installment=member["monthly_installment"],
                debicheck_status="active" if _has_qualifying_arrangement(db, member["id"]) else "required",
                access_status=decision["access"].lower(),
                failed_debit_date=oldest,
                failure_reason="Outstanding collection balance",
            )
            record_access_decision(
                db,
                member["id"],
                decision["access"],
                decision["action"],
                case_id=case_id,
            )
            if should_restrict:
                reason = (
                    "arrears_upfront_required"
                    if decision["status"] == "TWO_MONTHS_RESTRICTED"
                    else "arrears_no_arrangement"
                )
                db.execute(
                    """UPDATE members SET gym_access_status='blocked', access_block_reason=?
                       WHERE id=?""",
                    (reason, member["id"]),
                )
                member_activity(
                    db, member["id"],
                    "ACCESS RESTRICTED — PAYMENT CONDITION REQUIRED: "
                    f"{arrears['months_in_arrears']} months in arrears, "
                    f"R{arrears['total_arrears']:,.2f} outstanding.",
                )
            if should_restore:
                db.execute(
                    "UPDATE members SET gym_access_status='allowed', access_blocked_until=NULL, access_block_reason=NULL WHERE id=?",
                    (member["id"],),
                )
                member_activity(
                    db, member["id"],
                    "ACCESS RESTORED automatically — collections policy conditions are now satisfied.",
                )
            if should_inactivate:
                db.execute("UPDATE members SET member_status='Inactive' WHERE id=?", (member["id"],))
                member_activity(
                    db, member["id"],
                    "Account flagged INACTIVE — outstanding balance unpaid for more than 6 months.",
                )

    # A member who has paid everything is no longer in the owing list above, so
    # a broken-promise block would otherwise never be lifted for them.
    owing_ids = {m["id"] for m in owing_members}
    for held in db.execute(
        "SELECT id FROM members WHERE gym_access_status='blocked' AND access_block_reason='ptp_broken'"
    ).fetchall():
        if held["id"] in owing_ids:
            continue
        if not dry_run:
            db.execute(
                "UPDATE members SET gym_access_status='allowed', access_blocked_until=NULL, access_block_reason=NULL WHERE id=?",
                (held["id"],),
            )
            member_activity(db, held["id"], "ACCESS RESTORED automatically — the member has paid their balance.")

    cases_resolved = 0
    if not dry_run:
        # Payments can arrive from imports and POS pushes, not only receipts,
        # so every open case is re-checked against the reconciled balance.
        cases_resolved = reconcile_open_cases(db, source="daily_sweep")
        db.commit()
    return {
        "broken_count": broken_count,
        "expired_count": expired_count,
        "restricted_count": restricted_count,
        "inactive_count": inactive_count,
        "cases_resolved": cases_resolved,
    }


@ptp_bp.route("/ptp/run-automations/preview")
@permission_required("all_collections")
def preview_automations():
    """Read-only counts of what 'Run Automations' would do, fetched by the
    dashboard right before the confirm dialog — not computed on every page
    load, since it scans every member with an outstanding balance."""
    db = get_db()
    counts = _run_collections_automations(db, date.today().isoformat(), dry_run=True)
    return jsonify(counts)


@ptp_bp.route("/ptp/run-automations", methods=["POST"])
@permission_required("all_collections")
def run_automations():
    db     = get_db()
    today  = date.today().isoformat()
    counts = _run_collections_automations(db, today)
    flash(
        f"Automations complete: {counts['broken_count']} PTP(s) marked broken, "
        f"{counts['expired_count']} temp unblock(s) expired, "
        f"{counts['restricted_count']} account(s) access-restricted, "
        f"{counts['inactive_count']} account(s) flagged inactive.",
        "info",
    )
    return redirect(url_for("ptp.ptp_dashboard"))


# ── Create PTP ─────────────────────────────────────────────────────────────

@ptp_bp.route("/members/<int:mid>/ptp/create", methods=["POST"])
@permission_required("all_collections")
def ptp_create(mid: int):
    db = get_db()
    member = db.execute(
        "SELECT id, monthly_installment FROM members WHERE id=?", (mid,)
    ).fetchone()
    if not member:
        abort(404)

    f = request.form
    promise_amount   = (f.get("promise_amount") or "0").strip()
    promise_date     = (f.get("promise_date") or "").strip()
    payment_method   = (f.get("payment_method") or "").strip()
    arrangement_type = (f.get("arrangement_type") or "full").strip()
    access_unblock   = (f.get("access_unblock") or "no").strip()
    unblock_until    = (f.get("unblock_until") or "").strip() or None
    auto_block       = 1 if f.get("auto_block_if_failed") != "no" else 0
    notes            = (f.get("notes") or "").strip()
    arrears_amount   = (f.get("arrears_amount") or "0").strip()
    needs_approval   = f.get("needs_approval") == "yes"
    try:
        discount_pct = float((f.get("discount_pct") or "0").strip() or 0)
        promise_value = float(promise_amount or 0)
        arrears_value = float(arrears_amount or 0)
    except ValueError:
        flash("Amounts and discount must be valid numbers.", "error")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")
    if discount_pct < 0 or discount_pct > 100 or promise_value <= 0:
        flash("Enter a valid positive promise amount and discount between 0% and 100%.", "error")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")

    if not (promise_amount and promise_date and payment_method and notes):
        flash("Amount, promise date, payment method, and notes are required.", "error")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")

    # A collector may approve automatically only within the proposed 50%
    # discount ceiling and minimum recoverable balance. The remaining
    # prerequisites are checked below and failures go to manager approval.
    discount_amount = 0.0
    discount_basis = None
    discount_auto_approved = True
    is_cash_upfront = payment_method == "cash" and arrangement_type == "full"
    if discount_pct > 0:
        # The PTP half of "DebiCheck + PTP" is the record being created here, so
        # the gate is a confirmed mandate. _has_qualifying_arrangement() also
        # requires an already-saved pending PTP, which no first arrangement can
        # satisfy — using it here made this tier unreachable.
        if _has_submitted_mandate(db, mid) and discount_pct <= AUTO_SETTLEMENT_DISCOUNT_CEILING:
            discount_basis = "debicheck_ptp"
        elif is_cash_upfront and discount_pct <= AUTO_SETTLEMENT_DISCOUNT_CEILING:
            discount_basis = "cash_upfront"
        else:
            discount_auto_approved = False
        discount_amount = round(float(arrears_amount or 0) * discount_pct / 100, 2)

    # A no-upfront arrangement has no assumed cash contribution: its full
    # recoverable balance determines the term. A full cash settlement retains
    # the legacy upfront calculation. Recovery uses the member's normal
    # monthly instalment as the affordable arrears payment.
    try:
        recovery_plan = calculate_realistic_recovery_plan(
            arrears_value,
            float(member["monthly_installment"] or 0),
            discount_pct,
            UPFRONT_RECOVERY_PERCENT if is_cash_upfront else 0,
        )
    except (TypeError, ValueError):
        recovery_plan = calculate_realistic_recovery_plan(
            arrears_value, 0, discount_pct,
            UPFRONT_RECOVERY_PERCENT if is_cash_upfront else 0,
        )

    # Evidence currently means a bank statement document is on the member
    # record. This does not claim that income was independently verified; where
    # the app cannot establish that the rule is met, a manager must review it.
    bank_documents = db.execute(
        """SELECT analysis_json FROM member_documents
           WHERE member_id=? AND lower(COALESCE(document_type,'')) LIKE '%bank%'
           ORDER BY uploaded_at DESC""",
        (mid,),
    ).fetchall()
    has_bank_evidence = False
    for bank_document in bank_documents:
        try:
            analysis = json.loads(bank_document["analysis_json"] or "{}")
        except (TypeError, ValueError):
            analysis = {}
        if analysis.get("verification_qualified"):
            has_bank_evidence = True
            break
    manager_reasons = []
    minimum_recoverable = max(
        MINIMUM_RECOVERABLE_BALANCE, round(arrears_value * 0.50, 2)
    )
    if discount_pct > AUTO_SETTLEMENT_DISCOUNT_CEILING:
        manager_reasons.append("discount exceeds 50%")
    if not is_cash_upfront:
        if float(recovery_plan["discounted_balance"]) < minimum_recoverable:
            manager_reasons.append("recoverable balance is below the automatic minimum")
        if not _has_submitted_mandate(db, mid):
            manager_reasons.append("authenticated DebiCheck is not on file")
        if not has_bank_evidence:
            manager_reasons.append("bank statement evidence is not on file")
        if float(member["monthly_installment"] or 0) < MINIMUM_RECOVERY_INSTALLMENT:
            manager_reasons.append("monthly instalment is below R150")
        if int(recovery_plan["recovery_months"]) > NORMAL_RECOVERY_TERM_MONTHS:
            manager_reasons.append("term exceeds 12 months or needs a hardship exception")
    if db.execute(
        "SELECT 1 FROM ptp_agreements WHERE member_id=? AND ptp_status='broken' LIMIT 1",
        (mid,),
    ).fetchone():
        manager_reasons.append("member has a previously defaulted arrangement")
    if db.execute(
        "SELECT 1 FROM legal_referrals WHERE member_id=? AND status='open' LIMIT 1",
        (mid,),
    ).fetchone():
        manager_reasons.append("account is currently in the legal pathway")

    mgr_status = "pending" if (
        needs_approval or not discount_auto_approved or manager_reasons
    ) else "not_required"

    cursor = db.execute("""
        INSERT INTO ptp_agreements
            (member_id, created_by, arrears_amount, promise_amount, promise_date,
             payment_method, arrangement_type, ptp_status, access_unblock, unblock_until,
             auto_block_if_failed, notes, manager_approval_status,
             discount_pct, discount_amount, discount_basis, discounted_balance,
             upfront_percent, upfront_amount, remaining_balance, recovery_installment,
             recovery_months)
        VALUES (?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (mid, session.get("user_id"), float(arrears_amount or 0),
          float(promise_amount), promise_date, payment_method, arrangement_type,
          access_unblock,
          unblock_until if access_unblock == "yes" else None,
          auto_block, notes, mgr_status,
          discount_pct, discount_amount, discount_basis,
          float(recovery_plan["discounted_balance"]),
          float(recovery_plan["upfront_percent"]),
          float(recovery_plan["upfront_amount"]),
          float(recovery_plan["remaining_balance"]),
          float(recovery_plan["recovery_installment"]),
          int(recovery_plan["recovery_months"])))
    ptp_id = cursor.lastrowid
    supersede_open_ptps(db, mid, ptp_id)
    refresh_member_cases(db, mid, changed_by=session.get("user_id"), source="ptp", reason="New promise to pay recorded")

    # Surface the exact recovery calculation to the receptionist so the PTP
    # is based on the member's real monthly affordability.
    flash(
        f"Recovery plan: discounted balance R{float(recovery_plan['discounted_balance']):,.2f}; "
        f"{float(recovery_plan['upfront_percent']):g}% upfront "
        f"R{float(recovery_plan['upfront_amount']):,.2f}; "
        f"remaining R{float(recovery_plan['remaining_balance']):,.2f}; "
        f"arrears recovery R{float(recovery_plan['recovery_installment']):,.2f} per month "
        f"for up to {int(recovery_plan['recovery_months'])} month(s).",
        "info",
    )

    # Handle split payment plan items
    if arrangement_type == "split":
        half = float(promise_amount) / 2
        try:
            pd = datetime.strptime(promise_date, "%Y-%m-%d")
            d2 = (pd + timedelta(days=15)).strftime("%Y-%m-%d")
        except ValueError:
            d2 = promise_date
        db.execute("""
            INSERT INTO payment_plan_items (ptp_id, member_id, instalment_number, amount, due_date)
            VALUES (?,?,1,?,?),(?,?,2,?,?)
        """, (ptp_id, mid, half, promise_date, ptp_id, mid, half, d2))

    # Evaluated after the insert on purpose: a PTP plus an approved DebiCheck
    # mandate IS the qualifying arrangement, so the new record is part of what
    # the policy judges.
    policy = evaluate_member_access(db, mid, today=date.today())

    # Rule §7: at three or more months owing, only full settlement or a
    # qualifying DebiCheck arrangement is standard. Anything else is a
    # non-standard request that reception cannot settle on its own, so it is
    # recorded and escalated to the management exception queue rather than
    # quietly accepted.
    escalated = False
    # An approved exception covers the arrangement it was raised for, not
    # whatever is filed next - so a live one still escalates a new request.
    if policy.get("status") in ("THREE_PLUS_NO_DEBICHECK", "THREE_PLUS_MANAGER_EXCEPTION"):
        already_open = db.execute(
            """SELECT 1 FROM collection_exceptions
               WHERE member_id = ? AND status = 'pending' AND reason = ?
               LIMIT 1""",
            (mid, RULE_7_EXCEPTION_REASON),
        ).fetchone()
        if mgr_status != "approved":
            mgr_status = "pending"
            db.execute(
                "UPDATE ptp_agreements SET manager_approval_status='pending' WHERE id=?",
                (ptp_id,),
            )
        if not already_open:
            create_collection_exception(
                db, mid, RULE_7_EXCEPTION_REASON,
                ptp_id=ptp_id,
                requested_action=(
                    f"R{float(promise_amount):,.2f} by {promise_date} via "
                    f"{payment_method} ({arrangement_type})"
                ),
                notes=notes or None,
                created_by=session.get("user_id"),
            )
        escalated = True

    # Access is never granted merely because a PTP was created. The policy
    # engine must say ALLOWED and any required manager approval must already be
    # resolved. Otherwise the request is recorded but access remains blocked.
    access_granted = False
    if access_unblock == "yes":
        decision = policy
        if decision.get("access") == "ALLOWED" and mgr_status in {"not_required", "approved"}:
            db.execute(
                "UPDATE members SET gym_access_status='temp_unblocked', access_blocked_until=?, access_block_reason=NULL WHERE id=?",
                (unblock_until, mid),
            )
            access_granted = True
        elif policy.get("status") != "THREE_PLUS_MANAGER_EXCEPTION":
            # A live exception keeps the access the manager already approved;
            # only the new request waits for its own decision.
            db.execute(
                "UPDATE members SET gym_access_status='blocked', access_blocked_until=NULL, access_block_reason=? WHERE id=?",
                ("ptp_pending_approval" if mgr_status == "pending" else "arrears_no_arrangement", mid),
            )

    discount_note = ""
    if discount_pct > 0:
        discount_note = (
            f" Discount: {discount_pct:.0f}% (R{discount_amount:,.2f}) via "
            f"{discount_basis or 'no qualifying basis'} — "
            f"{'auto-approved' if discount_auto_approved and not needs_approval else 'awaiting manager approval'}."
        )
    if manager_reasons:
        discount_note += " Manager review: " + "; ".join(manager_reasons) + "."
    escalation_note = (
        " Escalated to the management exception queue: three or more months "
        "owing without a qualifying DebiCheck arrangement." if escalated else ""
    )
    member_activity(
        db, mid,
        f"PTP created: R{float(promise_amount):,.2f} promised by {promise_date} "
        f"via {payment_method} ({arrangement_type}). "
        f"Access: {'temp unblock until ' + (unblock_until or '?') if access_granted else ('requested but not granted' if access_unblock == 'yes' else 'no change')}."
        f"{discount_note}{escalation_note} "
        f"Notes: {notes[:120]}",
    )
    # Recorded last so it reflects any escalation to a manager above.
    record_discount_request(db, ptp_id, requested_by=session.get("user_id"))
    db.commit()
    if escalated:
        flash(
            "PTP recorded and sent to the management exception queue: three or more "
            "months owing without a qualifying DebiCheck arrangement needs a manager decision.",
            "warning",
        )
    else:
        flash("PTP created successfully.", "success")
    # A PTP is only as good as the mandate behind it. Open the DebiCheck form
    # straight away, filled in from what the system already holds for this
    # client (the receptionist edits it; nothing is typed from scratch). Skipped
    # when a usable mandate exists, or this user cannot create one.
    can_create_mandate = session.get("role") == "admin" or "new_debicheck" in (session.get("permissions") or [])
    if can_create_mandate and not _has_usable_mandate(db, mid):
        flash("Now confirm the DebiCheck details below. They are filled in from the client's record.", "info")
        return redirect(url_for("debicheck.add", member_id=mid, from_ptp=ptp_id))
    return redirect(url_for("members.member_detail", mid=mid) + "#ptp")


def _has_usable_mandate(db, member_id: int) -> bool:
    """True when the member has a mandate that is confirmed or still waiting on the
    client. A request left unapproved for more than a day has lapsed and does not count."""
    from .collections_decision import authorisation_lapsed

    rows = db.execute(
        """SELECT status, submitted_at FROM debicheck_mandates
           WHERE member_id=? AND lower(COALESCE(status,'')) NOT IN
             ('failed','rejected','declined','cancelled','canceled','expired')""",
        (member_id,),
    ).fetchall()
    return any(not authorisation_lapsed(r["status"], r["submitted_at"]) for r in rows)


# ── Log Contact ────────────────────────────────────────────────────────────

@ptp_bp.route("/members/<int:mid>/ptp/contact", methods=["POST"])
@permission_required("all_collections")
def ptp_log_contact(mid: int):
    db = get_db()
    f              = request.form
    contact_method = (f.get("contact_method") or "call").strip()
    call_code_raw  = f.get("call_code", "").strip()
    notes          = (f.get("notes") or "").strip()
    next_followup  = (f.get("next_followup_date") or "").strip() or None

    call_code = int(call_code_raw) if call_code_raw.isdigit() else None

    db.execute("""
        INSERT INTO contact_logs
            (member_id, consultant_id, contact_method, call_code, notes, next_followup_date)
        VALUES (?,?,?,?,?,?)
    """, (mid, session.get("user_id"), contact_method, call_code, notes, next_followup))

    code_label = CALL_CODES.get(call_code, f"Code {call_code}") if call_code else "No code"
    member_activity(
        db, mid,
        f"Contact logged: {contact_method.upper()} | {code_label}. "
        + (notes[:100] if notes else ""),
    )
    db.commit()
    flash("Contact logged.", "success")
    return redirect(url_for("members.member_detail", mid=mid) + "#ptp")


# ── Upload Receipt ─────────────────────────────────────────────────────────

@ptp_bp.route("/members/<int:mid>/ptp/receipt", methods=["POST"])
@permission_required("all_collections")
def ptp_upload_receipt(mid: int):
    db   = get_db()
    f    = request.form
    file = request.files.get("receipt_file")

    if not file or not file.filename:
        flash("No file selected.", "error")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")
    if not _allowed_receipt(file.filename):
        flash("File type not allowed. Use PDF, JPG, or PNG.", "error")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")

    amount_paid    = (f.get("receipt_amount") or "0").strip()
    payment_date   = (f.get("receipt_date") or "").strip()
    payment_method = (f.get("receipt_method") or "cash").strip()
    reference      = (f.get("receipt_reference") or "").strip()
    ptp_id_raw     = f.get("ptp_id", "").strip()
    ptp_id         = int(ptp_id_raw) if ptp_id_raw.isdigit() else None

    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "ptp_receipts" / str(mid)
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{int(time.time())}_{secure_filename(file.filename)}"
    file.save(str(upload_dir / stored_name))

    db.execute("""
        INSERT INTO ptp_receipts
            (ptp_id, member_id, original_name, stored_name,
             amount_paid, payment_date, payment_method, reference, uploaded_by)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (ptp_id, mid, file.filename, stored_name,
          float(amount_paid or 0), payment_date, payment_method,
          reference, session.get("user_id")))

    member_activity(
        db, mid,
        f"Receipt uploaded: R{float(amount_paid or 0):,.2f} on {payment_date} "
        f"via {payment_method} (ref: {reference or 'none'}). Pending admin verification.",
    )
    db.commit()
    flash("Receipt uploaded. Pending admin verification before arrears are cleared.", "success")
    return redirect(url_for("members.member_detail", mid=mid) + "#ptp")


# ── Verify Receipt (admin) ─────────────────────────────────────────────────

@ptp_bp.route("/members/<int:mid>/ptp/receipt/<int:rid>/verify", methods=["POST"])
@roles_required("admin", "manager")
def ptp_verify_receipt(mid: int, rid: int):
    db = get_db()
    decision = (request.form.get("decision") or "verified").strip().lower()
    if decision not in {"verified", "rejected"}:
        flash("Invalid receipt verification decision.", "error")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")
    receipt = db.execute(
        "SELECT * FROM ptp_receipts WHERE id=? AND member_id=?", (rid, mid)
    ).fetchone()
    if not receipt:
        flash("Receipt not found.", "warning")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")
    if receipt["verification_status"] != "pending":
        flash("This receipt has already been processed.", "warning")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")

    db.execute(
        """UPDATE ptp_receipts SET verification_status=?, verified_by=?,
           verified_at=datetime('now','localtime') WHERE id=? AND member_id=?""",
        (decision, session.get("user_id"), rid, mid),
    )

    if decision == "verified":
        amount = float(receipt["amount_paid"] or 0)
        remaining = amount
        # Apply verified money to oldest outstanding ledger rows first.
        rows = db.execute(
            """SELECT id, outstanding_balance, amount_paid FROM collections
               WHERE member_id=? AND COALESCE(outstanding_balance,0)>COALESCE(amount_paid,0)
               ORDER BY collection_date ASC, id ASC""", (mid,)
        ).fetchall()
        for row in rows:
            if remaining <= 0:
                break
            open_amount = max(float(row["outstanding_balance"] or 0) - float(row["amount_paid"] or 0), 0)
            applied = min(open_amount, remaining)
            new_paid = float(row["amount_paid"] or 0) + applied
            new_status = "paid" if new_paid >= float(row["outstanding_balance"] or 0) else "partial"
            db.execute("UPDATE collections SET amount_paid=?, status=? WHERE id=?", (new_paid, new_status, row["id"]))
            remaining -= applied

        if receipt["ptp_id"]:
            ptp = db.execute("SELECT promise_amount FROM ptp_agreements WHERE id=? AND member_id=?", (receipt["ptp_id"], mid)).fetchone()
            paid_for_ptp = db.execute(
                "SELECT COALESCE(SUM(amount_paid),0) AS total FROM ptp_receipts WHERE ptp_id=? AND verification_status='verified'",
                (receipt["ptp_id"],),
            ).fetchone()["total"] if ptp else 0
            if ptp:
                new_ptp_status = "paid" if float(paid_for_ptp) >= float(ptp["promise_amount"] or 0) else "partially_paid"
                db.execute("UPDATE ptp_agreements SET ptp_status=?, updated_at=datetime('now','localtime') WHERE id=?", (new_ptp_status, receipt["ptp_id"]))
                refresh_member_cases(db, mid, changed_by=session.get("user_id"), source="payment",
                                     reason=f"Verified payment: promise {new_ptp_status}")
                reconcile_open_cases(db, [mid], changed_by=session.get("user_id"))

        from .collections_engine import evaluate_member_access, record_access_decision
        access = evaluate_member_access(db, mid, today=date.today())
        record_access_decision(db, mid, access["access"], "Verified payment receipt reconciled")
        db.execute(
            "UPDATE members SET gym_access_status=?, access_blocked_until=CASE WHEN ?='ALLOWED' THEN NULL ELSE access_blocked_until END, access_block_reason=CASE WHEN ?='ALLOWED' THEN NULL ELSE access_block_reason END WHERE id=?",
            (access["access"].lower(), access["access"], access["access"], mid),
        )
    member_activity(db, mid, f"Receipt #{rid} {decision} by manager/admin.")
    db.commit()
    flash(f"Receipt marked as {decision} and payment reconciliation completed." if decision == "verified" else "Receipt rejected.", "success")
    return redirect(url_for("members.member_detail", mid=mid) + "#ptp")


# ── PTP Status Update ──────────────────────────────────────────────────────

@ptp_bp.route("/members/<int:mid>/ptp/<int:ptp_id>/status", methods=["POST"])
@permission_required("all_collections")
def ptp_update_status(mid: int, ptp_id: int):
    db         = get_db()
    new_status = (request.form.get("ptp_status") or "").strip()
    # "superseded" is set only when a newer promise replaces this one.
    valid      = {s for s, _ in PTP_STATUSES} - {"superseded"}

    if new_status not in valid:
        flash("Invalid status.", "error")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")
    if new_status in {"paid", "partially_paid"}:
        flash("Payment status is system-controlled and can only be set by verified payment reconciliation.", "error")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")

    db.execute(
        "UPDATE ptp_agreements SET ptp_status=?, updated_at=datetime('now','localtime') WHERE id=? AND member_id=?",
        (new_status, ptp_id, mid),
    )

    if new_status == "broken":
        db.execute(
            "UPDATE members SET gym_access_status='blocked', access_block_reason='ptp_broken' WHERE id=?",
            (mid,),
        )
    elif new_status == "paid":
        db.execute(
            """UPDATE members SET gym_access_status='allowed', access_blocked_until=NULL,
               access_block_reason=NULL WHERE id=?""",
            (mid,),
        )

    member_activity(db, mid, f"PTP #{ptp_id} status updated to: {new_status}.")
    refresh_member_cases(db, mid, changed_by=session.get("user_id"), source="ptp",
                         reason=f"Promise marked {new_status}")
    db.commit()
    flash(f"PTP status updated to {new_status}.", "success")
    return redirect(url_for("members.member_detail", mid=mid) + "#ptp")


# ── Manager Approval ───────────────────────────────────────────────────────

def _approval_redirect(mid: int):
    """Send the manager back where they decided from.

    Whitelisted rather than taking a URL, so this can never become an open
    redirect.
    """
    if request.form.get("return_to") == "exception_queue":
        return redirect(url_for("collections.exception_queue"))
    return redirect(url_for("members.member_detail", mid=mid) + "#ptp")


@ptp_bp.route("/members/<int:mid>/ptp/<int:ptp_id>/approve", methods=["POST"])
@roles_required("admin", "manager")
def ptp_approve(mid: int, ptp_id: int):
    db       = get_db()
    decision = (request.form.get("decision", "approved") or "").strip().lower()
    notes    = (request.form.get("approval_notes") or "").strip()
    if decision not in {"approved", "declined", "returned"}:
        flash("Manager decision must be approved, declined or returned.", "error")
        return _approval_redirect(mid)
    if decision == "returned":
        if not notes:
            flash("Returning a request needs a reason.", "error")
            return _approval_redirect(mid)
        return_discount_request(db, ptp_id, session.get("user_id"), notes)
        member_activity(db, mid, f"PTP #{ptp_id} returned to staff for information: {notes[:100]}")
        db.commit()
        flash("Request returned for more information.", "success")
        return _approval_redirect(mid)

    db.execute("""
        UPDATE ptp_agreements
        SET manager_approval_status=?, approved_by=?,
            approved_date=datetime('now','localtime'),
            approval_notes=?, updated_at=datetime('now','localtime')
        WHERE id=? AND member_id=?
    """, (decision, session.get("user_id"), notes, ptp_id, mid))

    # The same request raised a Rule §7 exception; the manager has now decided it.
    settle_exception_for_arrangement(db, ptp_id, decision, session.get("user_id"), notes)
    if decision == "approved" and grant_exception_access(db, mid):
        member_activity(db, mid, "Access granted under manager exception until the promise date.")

    member_activity(
        db, mid,
        f"PTP #{ptp_id} manager approval: {decision}. "
        + (f"Notes: {notes[:100]}" if notes else ""),
    )
    db.commit()
    flash(f"PTP {decision}.", "success")
    return _approval_redirect(mid)


# ── Access Control ─────────────────────────────────────────────────────────

@ptp_bp.route("/members/<int:mid>/ptp/access", methods=["POST"])
@roles_required("admin", "manager")
def ptp_update_access(mid: int):
    db            = get_db()
    access_status = (request.form.get("gym_access_status") or "").strip()
    blocked_until = (request.form.get("access_blocked_until") or "").strip() or None
    valid         = {"allowed", "blocked", "temp_unblocked"}

    if access_status not in valid:
        flash("Invalid access status.", "error")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("A manager/admin access override requires a reason.", "error")
        return redirect(url_for("members.member_detail", mid=mid) + "#ptp")
    if access_status in {"allowed", "temp_unblocked"}:
        from .collections_engine import evaluate_member_access
        decision = evaluate_member_access(db, mid, today=date.today())
        if decision.get("access") != "ALLOWED":
            flash(f"Access override blocked by collections policy: {decision.get('action') or decision.get('status')}.", "error")
            return redirect(url_for("members.member_detail", mid=mid) + "#ptp")

    db.execute(
        """UPDATE members SET gym_access_status=?,
           access_blocked_until=?, access_block_reason=? WHERE id=?""",
        (access_status,
         blocked_until if access_status == "temp_unblocked" else None,
         f"manager_override:{reason[:180]}" if access_status == "blocked" else f"manager_override:{reason[:180]}",
         mid),
    )
    detail = f" until {blocked_until}" if access_status == "temp_unblocked" and blocked_until else ""
    member_activity(db, mid, f"Access status set to: {access_status}{detail}.")
    db.commit()
    flash(f"Access updated to {access_status}.", "success")
    return redirect(url_for("members.member_detail", mid=mid) + "#ptp")
