"""The gate between recording a PTP and granting it.

A PTP is only granted once every DebiCheck mandate it needs has been confirmed
as approved. A submitted mandate, a verified upfront payment or a manager
clearance is not enough on its own: each is a condition, none is the grant.
"""
from __future__ import annotations

from .debicheck import normalise_debicheck_status

AWAITING_PREREQUISITES = "awaiting_prerequisites"
AWAITING_DEBICHECK = "awaiting_debicheck"
GRANTED = "granted"
DECLINED = "declined"

PURPOSE_ARREARS = "Arrangement (PTP)"
PURPOSE_NORMAL = "Normal instalment"


def has_approved_mandate(db, member_id) -> bool:
    rows = db.execute(
        "SELECT status FROM debicheck_mandates WHERE member_id=?", (member_id,)
    ).fetchall()
    return any(normalise_debicheck_status(r["status"]) == "approved" for r in rows)


def required_mandates(db, member_id, ptp_balance, normal_instalment, paying, normal_active=False):
    """(purpose, amount) pairs the arrangement needs approved.

    The arrears mandate is needed whenever a balance is left to collect. The
    normal membership mandate is needed unless it is already collecting, which
    is known from an active mandate (here or found on NuPay).
    """
    needed = []
    if float(ptp_balance or 0) > 0:
        needed.append((PURPOSE_ARREARS, float(ptp_balance)))
    normal_collecting = normal_active or (paying and has_approved_mandate(db, member_id))
    if not normal_collecting and float(normal_instalment or 0) > 0:
        needed.append((PURPOSE_NORMAL, float(normal_instalment)))
    return needed


def record_required(db, ptp_id, needed) -> None:
    db.execute("DELETE FROM ptp_mandate_links WHERE ptp_id=?", (ptp_id,))
    for purpose, amount in needed:
        db.execute(
            "INSERT INTO ptp_mandate_links (ptp_id, purpose, amount) VALUES (?,?,?)",
            (ptp_id, purpose, amount),
        )
    db.execute(
        "UPDATE ptp_agreements SET grant_status=? WHERE id=?",
        (AWAITING_DEBICHECK if needed else AWAITING_PREREQUISITES, ptp_id),
    )


def link_mandate(db, ptp_id, purpose, mandate_id) -> bool:
    """Attach a newly created mandate to the open requirement it satisfies."""
    row = db.execute(
        "SELECT id FROM ptp_mandate_links WHERE ptp_id=? AND purpose=? AND mandate_id IS NULL "
        "ORDER BY id LIMIT 1", (ptp_id, purpose)).fetchone()
    if row is None:
        return False
    db.execute("UPDATE ptp_mandate_links SET mandate_id=? WHERE id=?", (mandate_id, row["id"]))
    return True


def evaluate_grant(db, ptp_id) -> str | None:
    """Recheck every condition and grant the PTP at most once."""
    ptp = db.execute("SELECT * FROM ptp_agreements WHERE id=?", (ptp_id,)).fetchone()
    if ptp is None or ptp["ptp_status"] not in ("pending", "partially_paid"):
        return None
    if ptp["grant_status"] == GRANTED:
        return GRANTED

    links = db.execute(
        """SELECT l.purpose, l.mandate_id, d.status, d.contract_reference
           FROM ptp_mandate_links l LEFT JOIN debicheck_mandates d ON d.id = l.mandate_id
           WHERE l.ptp_id=?""", (ptp_id,)).fetchall()
    states = [normalise_debicheck_status(l["status"]) if l["mandate_id"] else None for l in links]

    if any(s == "failed" for s in states):
        status = DECLINED
    elif (not links and not (ptp["existing_mandate_ok"] or has_approved_mandate(db, ptp["member_id"]))) \
            or any(s != "approved" for s in states):
        status = AWAITING_DEBICHECK
    elif requires_statement_evidence(ptp) and not statement_evidence_ready(db, ptp['member_id']):
        status = AWAITING_PREREQUISITES
    elif ptp["manager_approval_status"] not in ("not_required", "approved"):
        status = AWAITING_PREREQUISITES
    elif float(ptp["upfront_amount"] or 0) > 0 and not ptp["upfront_verified"]:
        status = AWAITING_PREREQUISITES
    else:
        status = GRANTED

    if status == GRANTED:
        reference = ", ".join(l["contract_reference"] for l in links if l["contract_reference"])
        db.execute(
            "UPDATE ptp_agreements SET grant_status=?, granted_at=datetime('now','localtime'), "
            "grant_reference=? WHERE id=? AND COALESCE(grant_status,'') <> ?",
            (GRANTED, reference or None, ptp_id, GRANTED))
        if ptp["access_unblock"] == "yes":
            db.execute(
                "UPDATE members SET gym_access_status='temp_unblocked', access_blocked_until=?, "
                "access_block_reason=NULL WHERE id=?", (ptp["unblock_until"], ptp["member_id"]))
    else:
        db.execute("UPDATE ptp_agreements SET grant_status=? WHERE id=?", (status, ptp_id))
    return status


def requires_statement_evidence(ptp):
    row = dict(ptp)
    return row.get('kpi_contact_channel') == 'call' or float(row.get('discount_amount') or 0) > 0 or float(row.get('discount_pct') or 0) > 0


def statement_evidence_ready(db, member_id):
    from .applications import statement_months_analysed
    return statement_months_analysed(db, member_id)


def refresh_member_grants(db, member_id):
    """Statement uploads can satisfy the last prerequisite without another mandate push."""
    for row in db.execute("SELECT id FROM ptp_agreements WHERE member_id=? AND grant_status IN (?,?)",
                          (member_id, AWAITING_PREREQUISITES, AWAITING_DEBICHECK)).fetchall():
        evaluate_grant(db, row['id'])


def manager_review_reasons(db, member_id, monthly_installment, payment_count) -> list[str]:
    """Why a manager must clear an arrangement before it can be granted."""
    import json

    reasons = []
    qualified = False
    for doc in db.execute(
        """SELECT analysis_json FROM member_documents
           WHERE member_id=? AND lower(COALESCE(document_type,'')) LIKE '%bank%'
           ORDER BY uploaded_at DESC""", (member_id,)).fetchall():
        try:
            analysis = json.loads(doc["analysis_json"] or "{}")
        except (TypeError, ValueError):
            analysis = {}
        if analysis.get("verification_qualified"):
            qualified = True
            break
    if not qualified:
        reasons.append("bank statement evidence is not on file")
    if float(monthly_installment or 0) < 150:
        reasons.append("monthly instalment is below R150")
    if int(payment_count or 0) > 12:
        reasons.append("term exceeds 12 months or needs a hardship exception")
    if db.execute("SELECT 1 FROM ptp_agreements WHERE member_id=? AND ptp_status='broken' LIMIT 1",
                  (member_id,)).fetchone():
        reasons.append("member has a previously defaulted arrangement")
    if db.execute("SELECT 1 FROM legal_referrals WHERE member_id=? AND status='open' LIMIT 1",
                  (member_id,)).fetchone():
        reasons.append("account is currently in the legal pathway")
    return reasons


def next_debit_date(debit_day, today=None):
    """The next date the member's debit day falls on, at least 3 days ahead."""
    from datetime import date, timedelta
    import calendar

    today = today or date.today()
    earliest = today + timedelta(days=3)
    try:
        day = int(debit_day)
    except (TypeError, ValueError):
        return earliest
    year, month = earliest.year, earliest.month
    for _ in range(2):
        candidate = date(year, month, min(max(day, 1), calendar.monthrange(year, month)[1]))
        if candidate >= earliest:
            return candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return earliest


def arrangement_payment_terms(first_date, method, *, debit_day=None):
    """Validate the terms reception reads back; preserve existing debit policy."""
    from datetime import date
    from .debicheck_rules import debit_date_error, earliest_debit_date
    method = str('debicheck' if method is None else method).strip()
    if method != 'debicheck':
        raise ValueError('Monthly arrangement repayments require DebiCheck under the current rules.')
    value = str(next_debit_date(debit_day, today=earliest_debit_date()).isoformat() if first_date is None else first_date).strip()
    if not value:
        raise ValueError('Choose the first arrears repayment date agreed with the client.')
    error = debit_date_error(value)
    if error:
        raise ValueError(error)
    return date.fromisoformat(value).isoformat(), method


def create_arrangement(db, member_id, offer, *, user_id, normal_active, notes=""):
    """Record the PTP agreed on the call and list the mandates it needs.

    ``offer`` is the decision from collections_decision.decide_offer. The PTP is
    only recorded here; it is granted later by evaluate_grant.
    """
    from .collections_engine import repayment_schedule, supersede_open_ptps, refresh_member_cases
    from .database import member_activity

    member = db.execute(
        "SELECT monthly_installment, debit_order_date FROM members WHERE id=?", (member_id,)).fetchone()
    from .collection_membership import membership_payment_context
    normal_instalment = membership_payment_context(db, member_id)['normal_instalment']
    plan = offer.get("arrangement") or {}
    payments = int(plan.get("payments") or 0)
    first_date, payment_method = arrangement_payment_terms(
        offer.get('first_collection_date'), offer.get('payment_method'), debit_day=member['debit_order_date'])
    schedule = repayment_schedule(offer["ptp_balance"], payments, first_date) if payments else []
    first_amount = float(schedule[0][1]) if schedule else float(offer["ptp_balance"])
    reasons = manager_review_reasons(db, member_id, member["monthly_installment"], len(schedule))
    approval = "pending" if reasons or offer.get("decision") == "manager_review" else "not_required"
    arrears = float(offer["original_arrears"])
    upfront = float(offer["upfront_offered"])
    cursor = db.execute(
        """INSERT INTO ptp_agreements
               (member_id, created_by, arrears_amount, promise_amount, promise_date, payment_method,
                arrangement_type, ptp_status, access_unblock, auto_block_if_failed, notes,
                manager_approval_status, discount_pct, discount_amount, discount_basis,
                discounted_balance, upfront_percent, upfront_amount, remaining_balance,
                recovery_installment, recovery_months)
           VALUES (?,?,?,?,?,?,?, 'pending','no',1,?,?,?,?,?,?,?,?,?,?,?)""",
        (member_id, user_id, arrears, first_amount, first_date, payment_method,
         "partial" if len(schedule) > 1 else "full", notes or "Arranged on collections call",
         approval, offer["discount_percent"], offer["discount_amount"],
         "category_rule" if offer["discount_percent"] else None,
         arrears - float(offer["discount_amount"]),
         (upfront * 100 / arrears) if arrears else 0, upfront, float(offer["ptp_balance"]),
         first_amount, len(schedule)))
    ptp_id = cursor.lastrowid
    from .staff_kpi import record_ptp_channel
    record_ptp_channel(db, ptp_id, "call")
    db.execute(
        """UPDATE ptp_agreements SET rule_version=?, arrears_category=?, months_owing=?, payment_count=?,
               first_collection_date=?, existing_mandate_ok=? WHERE id=?""",
        (offer["rule_version"], offer["category"], offer["months_owing"], len(schedule),
         first_date if schedule else None, 1 if normal_active else 0, ptp_id))
    paying = False
    record_required(db, ptp_id, required_mandates(
        db, member_id, offer["ptp_balance"], normal_instalment, paying, normal_active))
    supersede_open_ptps(db, member_id, ptp_id)
    refresh_member_cases(db, member_id, changed_by=user_id, source="ptp",
                         reason="Promise to pay arranged on a collections call")
    member_activity(
        db, member_id,
        f"PTP #{ptp_id} arranged on call: R{offer['ptp_balance']:,.2f} over {len(schedule) or 1} payment(s). "
        "Not granted until its DebiCheck mandate(s) are approved."
        + (f" Manager review: {'; '.join(reasons)}." if reasons else ""))
    evaluate_grant(db, ptp_id)
    return ptp_id


def on_mandate_status_changed(db, mandate_id) -> list[int]:
    """Re-evaluate every PTP waiting on this mandate."""
    ids = [r["ptp_id"] for r in db.execute(
        "SELECT DISTINCT ptp_id FROM ptp_mandate_links WHERE mandate_id=?", (mandate_id,)).fetchall()]
    for ptp_id in ids:
        evaluate_grant(db, ptp_id)
    return ids
