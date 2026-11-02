"""Data for the Collections dashboard and case queue.

Everything here reads collection cases as the workflow object: stage, priority,
owner and next action come from collections_engine, never recomputed in a
template. The financial figures are summed from the same cases and the same
reconciled payments the rest of Collections uses.
"""
from __future__ import annotations
from .reporting_cycle import today as business_today, cycle_key, cycle_bounds

from datetime import date

OPEN = "('open','active','pending')"

# key -> (label, extra WHERE clause on the case alias "c", needs user id)
QUEUE_FILTERS = {
    "all": ("All open cases", "1=1", False),
    "mine": (
        "My queue",
        """(c.owner_staff_id = ? OR c.member_id IN (
              SELECT member_id FROM collection_call_tasks
              WHERE assigned_to = ? AND task_date = ?))""",
        True,
    ),
    "sales_first_debit": ("Sales — first debit", "c.owner_type = 'SALES'", False),
    "reception_2plus": (
        "Reception — 2+ months", "c.owner_type = 'RECEPTION' AND c.months_owing >= 2", False,
    ),
    "no_contact": (
        "No contact yet",
        "NOT EXISTS (SELECT 1 FROM collection_communications cc WHERE cc.case_id = c.id)",
        False,
    ),
    "ptp_due": ("PTP due", "c.collection_stage = 'PTP_DUE'", False),
    "broken_ptp": ("Broken PTP", "c.collection_stage = 'PTP_BROKEN'", False),
    "no_debicheck": (
        "No DebiCheck",
        "LOWER(COALESCE(c.debicheck_status,'')) NOT IN ('active','approved','not_required')",
        False,
    ),
    "pending_approval": ("Pending approval", "c.collection_stage = 'APPROVAL_PENDING'", False),
    "restricted": ("Access restricted", "c.collection_stage = 'ACCESS_RESTRICTED'", False),
    "six_plus": ("6+ months", "c.months_owing >= 6", False),
}


def case_queue(db, filter_key="all", *, user_id=None, today=None, limit=500):
    """Open cases for one queue filter, most urgent first."""
    _label, clause, needs_user = QUEUE_FILTERS.get(filter_key, QUEUE_FILTERS["all"])
    params = []
    if needs_user:
        if user_id:
            params = [user_id, user_id, (today or date.today()).isoformat()]
        else:
            clause = "1=0"
    rows = db.execute(
        f"""SELECT c.id, c.member_id, c.arrears_amount, c.months_owing, c.collection_stage,
                   c.priority, c.owner_type, c.owner_staff_id, c.next_action, c.debicheck_status,
                   c.access_status, c.original_sales_consultant_id,
                   m.first_name, m.last_name, m.contact,
                   u.full_name AS owner_name,
                   (SELECT MAX(cc.created_at) FROM collection_communications cc
                     WHERE cc.case_id = c.id) AS last_contact_at,
                   (SELECT p.ptp_status || ' ' || COALESCE(p.promise_date, '')
                      FROM ptp_agreements p WHERE p.member_id = c.member_id
                       AND p.ptp_status IN ('pending','partially_paid','broken')
                      ORDER BY p.id DESC LIMIT 1) AS ptp_summary
            FROM collections_cases c
            JOIN members m ON m.id = c.member_id
            LEFT JOIN users u ON u.id = c.owner_staff_id
            WHERE c.status IN {OPEN} AND {clause}
            ORDER BY COALESCE(c.priority, 3), c.arrears_amount DESC, c.id
            LIMIT {int(limit)}""",
        params,
    ).fetchall()
    return rows


def queue_counts(db, *, user_id=None, today=None):
    """How many cases each queue filter holds, for the filter chips and KPIs."""
    return {key: len(case_queue(db, key, user_id=user_id, today=today)) for key in QUEUE_FILTERS}


def _scalar(db, sql, params=()):
    row = db.execute(sql, params).fetchone()
    return (row[0] if row else 0) or 0


def dashboard_metrics(db, *, user_id=None, today=None,date_from=None,date_to=None):
    """Financial position, today's work, and the figures each role cares about."""
    today = today or business_today()
    month = cycle_key(today)
    day = today.isoformat()
    first,last=cycle_bounds(month)
    start=date_from or first.isoformat();end=date_to or last.isoformat()
    from .members import _is_discount
    period_payments=db.execute("SELECT amount_paid,notes FROM collections WHERE substr(collection_date,1,10)>=? AND substr(collection_date,1,10)<=? AND status IN ('paid','partial')",(start,end)).fetchall()
    period_collected=round(sum(float(row['amount_paid'] or 0) for row in period_payments if not _is_discount(row)),2)

    financial = {
        "total_outstanding": float(_scalar(
            db, f"SELECT SUM(arrears_amount) FROM collections_cases WHERE status IN {OPEN}")),
        "members_in_arrears": _scalar(
            db, f"SELECT COUNT(DISTINCT member_id) FROM collections_cases WHERE status IN {OPEN}"),
        "failed_debits": _scalar(
            db, f"SELECT COUNT(*) FROM collections_cases WHERE status IN {OPEN} AND failed_debit_date IS NOT NULL"),
        "collected_this_month": period_collected,
        "active_ptps": _scalar(
            db, "SELECT COUNT(*) FROM ptp_agreements WHERE ptp_status IN ('pending','partially_paid')"),
        "broken_ptps": _scalar(
            db, f"SELECT COUNT(*) FROM collections_cases WHERE status IN {OPEN} AND collection_stage='PTP_BROKEN'"),
        "pending_approvals": _scalar(
            db, f"SELECT COUNT(*) FROM collections_cases WHERE status IN {OPEN} AND collection_stage='APPROVAL_PENDING'"),
        "access_restricted": _scalar(
            db, f"SELECT COUNT(*) FROM collections_cases WHERE status IN {OPEN} AND collection_stage='ACCESS_RESTRICTED'"),
    }

    by_action = {
        row["next_action"]: row["n"] for row in db.execute(
            f"""SELECT COALESCE(next_action,'NO_ACTION') AS next_action, COUNT(*) AS n
                FROM collections_cases WHERE status IN {OPEN} GROUP BY 1""").fetchall()
    }
    workload = {
        "first_month_sales": _scalar(
            db, f"SELECT COUNT(*) FROM collections_cases WHERE status IN {OPEN} AND owner_type='SALES'"),
        "calls_due": _scalar(
            db, "SELECT COUNT(*) FROM collection_call_tasks WHERE task_date=? AND status='pending'", (day,)),
        "ptps_due_today": _scalar(
            db, "SELECT COUNT(*) FROM ptp_agreements WHERE ptp_status='pending' AND promise_date=?", (day,)),
        "by_next_action": by_action,
    }

    sales = {"open": 0, "contacted_today": 0, "resolved": 0}
    if user_id:
        sales["open"] = _scalar(
            db, f"SELECT COUNT(*) FROM collections_cases WHERE status IN {OPEN} AND owner_type='SALES' AND owner_staff_id=?",
            (user_id,))
        sales["contacted_today"] = _scalar(
            db, """SELECT COUNT(DISTINCT case_id) FROM collection_communications
                   WHERE created_by=? AND substr(created_at,1,10)=?""", (user_id, day))
        sales["resolved"] = _scalar(
            db, """SELECT COUNT(*) FROM collections_cases
                   WHERE status='closed' AND original_sales_consultant_id=?""", (user_id,))

    owners = {
        row["owner_type"]: row["n"] for row in db.execute(
            f"SELECT COALESCE(owner_type,'RECEPTION') AS owner_type, COUNT(*) AS n "
            f"FROM collections_cases WHERE status IN {OPEN} GROUP BY 1").fetchall()
    }
    # Who held a case when it was resolved: its last assignment.
    recoveries = {
        row["owner_type"]: row["n"] for row in db.execute(
            """SELECT h.to_owner_type AS owner_type, COUNT(*) AS n
               FROM collections_cases c
               JOIN collection_assignment_history h ON h.id = (
                    SELECT MAX(id) FROM collection_assignment_history WHERE case_id = c.id)
               WHERE c.status='closed' AND c.resolution_type='PAID_IN_FULL'
                 AND substr(c.closed_at,1,10)>=? AND substr(c.closed_at,1,10)<=? GROUP BY 1""", (start,end)).fetchall()
    }
    manager = {"open_by_owner": owners, "recovered_this_month_by_owner": recoveries}
    first, last = cycle_bounds(month)
    return {"financial": financial, "workload": workload, "sales": sales, "manager": manager,
            "cycle_start": start, "cycle_end": end}


def _ts(value):
    return str(value)[:19] if value else ""


def _name(row, key="staff"):
    return row[key] or "System"


def account_summary(db, member_id):
    """The collections view of one account: what is owed against the instalment,
    whether the client may be called, and what an arrangement would look like.

    Balance, mandate and PTP facts come from the same summary the Queries page
    uses, so the two screens agree.
    """
    from .collections import below_installment_not_callable
    from .collections_engine import category_offer, ptp_debit_orders_needed
    from .ptp import bulk_member_arrears
    from .queries import _account_summary

    base = _account_summary(db, member_id)
    if base is None:
        return None
    owing = float(base["total_outstanding"])
    installment = float(base["monthly_installment"])
    failed = db.execute(
        """SELECT COUNT(*) AS n, MAX(collection_date) AS last_date FROM collections
           WHERE member_id=? AND status='failed'
             AND COALESCE(outstanding_balance,0)>COALESCE(amount_paid,0)""",
        (member_id,)).fetchone()
    last_paid = db.execute(
        """SELECT collection_date, amount_paid FROM collections
           WHERE member_id=? AND COALESCE(amount_paid,0)>0 AND status IN ('paid','partial')
           ORDER BY collection_date DESC, id DESC LIMIT 1""", (member_id,)).fetchone()
    last_call = db.execute(
        """SELECT outcome, COALESCE(called_at, task_date) AS at FROM collection_call_tasks
           WHERE member_id=? AND status='completed' ORDER BY id DESC LIMIT 1""",
        (member_id,)).fetchone()
    has_failed = bool(failed["n"])
    below = owing > 0 and below_installment_not_callable(owing, installment, has_failed)
    orders, paying = ptp_debit_orders_needed(db, member_id, installment or 0, installment)
    months = int(bulk_member_arrears(db, [member_id]).get(member_id, {}).get("months_in_arrears", 0) or 0)
    return {
        "summary": base,
        "owing": owing,
        "installment": installment,
        "installments_owed": round(owing / installment, 2) if installment else None,
        "below_installment": below,
        "callable": not below,
        "call_note": (
            "Owes less than one instalment and no failed debit. Check the recurring amount "
            "against what the client is paying before calling." if below else
            "Can be called." if owing > 0 else "No balance owing."),
        "failed_debits": failed["n"] or 0,
        "last_failed": failed["last_date"],
        "last_payment": dict(last_paid) if last_paid else None,
        "paying": paying,
        "mandate_status": base["debit_order_status"],
        "has_debit_order": base["has_debit_order"],
        "payment_type": base["payment_type"],
        "debit_order_date": base["debit_order_date"],
        "access_status": base["access_status"],
        "open_ptp_id": base["open_ptp_id"],
        "open_ptp_status": base["open_ptp_status"],
        "last_call": dict(last_call) if last_call else None,
        "offer_no_upfront": _plain(category_offer(owing, 0, months)),
        "offer_half_upfront": _plain(category_offer(owing, owing / 2, months)),
        "months_owing": months,
        "ptp": _ptp_arrangement(db, member_id),
    }


def _plain(offer):
    return {k: (float(v) if hasattr(v, "quantize") else v) for k, v in offer.items()}


def _ptp_arrangement(db, member_id):
    """The latest arrangement with its DebiCheck requirements and next step."""
    ptp = db.execute(
        """SELECT * FROM ptp_agreements WHERE member_id=? AND ptp_status <> 'superseded'
           ORDER BY id DESC LIMIT 1""", (member_id,)).fetchone()
    if ptp is None:
        return None
    links = db.execute(
        """SELECT l.purpose, l.amount, l.mandate_id, d.status
           FROM ptp_mandate_links l LEFT JOIN debicheck_mandates d ON d.id = l.mandate_id
           WHERE l.ptp_id=? ORDER BY l.id""", (ptp["id"],)).fetchall()
    from .debicheck import normalise_debicheck_status
    mandates = [{"purpose": l["purpose"], "amount": float(l["amount"]),
                 "status": normalise_debicheck_status(l["status"]) if l["mandate_id"] else "not prepared"}
                for l in links]
    grant = ptp["grant_status"] or "legacy"
    upfront_paid = float(db.execute(
        "SELECT COALESCE(SUM(amount_paid),0) AS t FROM ptp_receipts "
        "WHERE ptp_id=? AND verification_status='verified'", (ptp["id"],)).fetchone()["t"])
    next_action = {
        "awaiting_debicheck": "Prepare or obtain approval for the DebiCheck mandate(s).",
        "awaiting_prerequisites": "Clear manager review or verify the upfront payment.",
        "declined": "A required mandate was declined. Agree a new arrangement.",
        "granted": "Monitor repayments.",
        "legacy": "Recorded before the approval gate; review for DebiCheck evidence.",
    }.get(grant, "")
    return {
        "id": ptp["id"], "category": ptp["arrears_category"],
        "arrears": float(ptp["arrears_amount"] or 0), "upfront": float(ptp["upfront_amount"] or 0),
        "discount": float(ptp["discount_amount"] or 0), "balance": float(ptp["remaining_balance"] or 0),
        "payments": ptp["payment_count"] or 0, "installment": float(ptp["recovery_installment"] or 0),
        "first_date": ptp["first_collection_date"], "manager": ptp["manager_approval_status"],
        "grant_status": grant, "granted_at": ptp["granted_at"], "reference": ptp["grant_reference"],
        "mandates": mandates, "next_action": next_action,
        "upfront_paid": upfront_paid,
        "upfront_due": max(round(float(ptp["upfront_amount"] or 0) - upfront_paid, 2), 0),
        "can_record_upfront": grant in ("awaiting_prerequisites", "awaiting_debicheck")
        and float(ptp["upfront_amount"] or 0) - upfront_paid > 0.005,
    }


def member_profile(db, member_id):
    """One member's whole collections history: the current case, the records
    behind it, and a single chronological timeline across all of them."""
    member = db.execute(
        "SELECT id, first_name, last_name, contact, member_status, gym_access_status, "
        "access_block_reason FROM members WHERE id=?", (member_id,)).fetchone()
    if member is None:
        return None
    cases = db.execute(
        """SELECT c.*, u.full_name AS owner_name, s.full_name AS sales_name
           FROM collections_cases c
           LEFT JOIN users u ON u.id = c.owner_staff_id
           LEFT JOIN users s ON s.id = c.original_sales_consultant_id
           WHERE c.member_id=? ORDER BY c.id DESC""", (member_id,)).fetchall()
    current = next((c for c in cases if c["status"] in ("open", "active", "pending")), None)

    contacts = db.execute(
        """SELECT cc.*, u.full_name AS staff FROM collection_communications cc
           LEFT JOIN users u ON u.id = cc.created_by
           WHERE cc.member_id=? ORDER BY cc.id DESC""", (member_id,)).fetchall()
    ptps = db.execute(
        """SELECT p.*, u.full_name AS staff FROM ptp_agreements p
           LEFT JOIN users u ON u.id = p.created_by
           WHERE p.member_id=? ORDER BY p.id DESC""", (member_id,)).fetchall()
    payments = db.execute(
        "SELECT * FROM collections WHERE member_id=? ORDER BY collection_date DESC, id DESC",
        (member_id,)).fetchall()
    receipts = db.execute(
        """SELECT r.*, u.full_name AS staff FROM ptp_receipts r
           LEFT JOIN users u ON u.id = r.uploaded_by WHERE r.member_id=?""", (member_id,)).fetchall()
    exceptions = db.execute(
        """SELECT e.*, u.full_name AS staff, d.full_name AS decider FROM collection_exceptions e
           LEFT JOIN users u ON u.id = e.created_by LEFT JOIN users d ON d.id = e.decided_by
           WHERE e.member_id=?""", (member_id,)).fetchall()
    access = db.execute(
        "SELECT * FROM access_decisions WHERE member_id=? ORDER BY id DESC", (member_id,)).fetchall()
    assignments = db.execute(
        """SELECT h.*, f.full_name AS from_name, t.full_name AS to_name
           FROM collection_assignment_history h
           LEFT JOIN users f ON f.id = h.from_staff_id LEFT JOIN users t ON t.id = h.to_staff_id
           WHERE h.member_id=? ORDER BY h.id""", (member_id,)).fetchall()
    stages = db.execute(
        """SELECT h.*, u.full_name AS staff FROM collection_stage_history h
           LEFT JOIN users u ON u.id = h.changed_by
           WHERE h.member_id=? ORDER BY h.id""", (member_id,)).fetchall()

    notes = db.execute(
        """SELECT n.*, u.full_name AS staff FROM collection_notes n
           LEFT JOIN users u ON u.id = n.staff_id WHERE n.member_id=? ORDER BY n.id DESC""", (member_id,)).fetchall()
    discounts = db.execute(
        """SELECT d.*, r.full_name AS requester, m.full_name AS decider
           FROM collection_discount_requests d
           LEFT JOIN users r ON r.id = d.requested_by LEFT JOIN users m ON m.id = d.decided_by
           WHERE d.member_id=? ORDER BY d.id DESC""", (member_id,)).fetchall()

    events = []
    for n in notes:
        label = "System note" if n["staff_id"] is None else "Note"
        events.append((_ts(n["created_at"]), f"{label} · {n['note_type'].title()}", n["note_text"], n["staff"] or "System"))
    for d in discounts:
        events.append((_ts(d["requested_at"]), "Discount requested",
                       f"{d['requested_percentage']:g}% off R{float(d['original_balance'] or 0):.2f} "
                       f"→ R{float(d['adjusted_balance'] or 0):.2f} ({d['status']})", d["requester"] or "System"))
        if d["decided_at"]:
            events.append((_ts(d["decided_at"]), f"Discount {d['status'].lower()}", d["decision_reason"] or "", d["decider"] or "System"))
    for c in cases:
        events.append((_ts(c["created_at"]), "Case opened", f"Failed debit R{float(c['arrears_amount'] or 0):.2f}", "System"))
        if c["closed_at"]:
            events.append((_ts(c["closed_at"]), "Case resolved", c["resolution_type"] or "Closed", _name({"staff": c["owner_name"]})))
    for a in assignments:
        who = a["to_name"] or a["to_owner_type"].title()
        if a["from_owner_type"]:
            text = f"Transferred {a['from_owner_type'].title()} → {a['to_owner_type'].title()} ({a['reason']})"
        else:
            text = f"Assigned to {who} ({a['reason']})"
        events.append((_ts(a["transferred_at"]), "Ownership", text, "System" if a["system_generated"] else "Manager"))
    for s in stages:
        change = f"{(s['from_stage'] or 'new')} → {s['to_stage']}".replace("_", " ")
        events.append((_ts(s["changed_at"]), "Stage", change + (f" — {s['reason']}" if s["reason"] else ""), _name(s)))
    for cc in contacts:
        text = f"{cc['channel']} — {(cc['outcome'] or 'logged').replace('_', ' ')}" + (f": {cc['notes']}" if cc["notes"] else "")
        events.append((_ts(cc["created_at"]), "Contact", text, _name(cc)))
    for p in ptps:
        events.append((_ts(p["created_at"]), "Promise to pay",
                       f"R{float(p['promise_amount'] or 0):.2f} by {p['promise_date']} ({p['ptp_status']})", _name(p)))
    for r in receipts:
        events.append((_ts(r["uploaded_at"]), "Receipt",
                       f"R{float(r['amount_paid'] or 0):.2f} via {r['payment_method']} ({r['verification_status']})", _name(r)))
    for e in exceptions:
        events.append((_ts(e["created_at"]), "Exception requested", e["reason"] or "", _name(e)))
        if e["decided_at"]:
            events.append((_ts(e["decided_at"]), "Exception " + (e["status"] or "decided"), e["notes"] or "", _name(e, "decider")))
    for d in access:
        events.append((_ts(d["evaluated_at"]), "Access", f"{d['access_status']} — {d['reason']}", "System"))
    for row in payments:
        events.append((_ts(row["collection_date"]), "Debit" if row["status"] == "failed" else "Payment",
                       f"R{float(row['amount_paid'] or 0):.2f} of R{float(row['outstanding_balance'] or 0):.2f} ({row['status']})", "System"))
    events.sort(key=lambda e: e[0], reverse=True)

    return {
        "member": member, "case": current, "cases": cases, "timeline": events,
        "contacts": contacts, "ptps": ptps, "payments": payments, "exceptions": exceptions,
        "access": access, "assignments": assignments, "stages": stages,
        "notes": notes, "discounts": discounts,
        "account": account_summary(db, member_id),
    }


# ── Reports ──────────────────────────────────────────────────────────────────

def report_month(value=None):
    """A closing-month YYYY-MM cycle key; invalid input uses the current cycle."""
    text = str(value or "")
    if len(text) == 7 and text[4] == "-" and text[:4].isdigit() and text[5:].isdigit():
        if 2000 <= int(text[:4]) <= 2100 and 1 <= int(text[5:]) <= 12:
            return text
    return cycle_key(business_today())


def financial_report(db, month):
    """Money in and out of collections for one 15th?14th cycle, plus where it stands now.
    There is no opening balance: balances are not snapshotted, so one would be invented."""
    return {
        "month": month, "cycle_start": cycle_bounds(month)[0].isoformat(), "cycle_end": cycle_bounds(month)[1].isoformat(),
        "new_arrears": float(_scalar(
            db, "SELECT SUM(arrears_amount) FROM collections_cases WHERE strftime('%Y-%m', date(created_at,'-14 days','start of month','+1 month'))=?", (month,))),
        "collected": float(_scalar(
            db, """SELECT SUM(amount_paid) FROM collections
                   WHERE strftime('%Y-%m', date(collection_date,'-14 days','start of month','+1 month'))=? AND status IN ('paid','partial')""", (month,))),
        "discounts_approved": float(_scalar(
            db, """SELECT SUM(discount_amount) FROM ptp_agreements
                   WHERE strftime('%Y-%m', date(created_at,'-14 days','start of month','+1 month'))=? AND COALESCE(discount_amount,0) > 0
                     AND manager_approval_status IN ('approved','not_required')""", (month,))),
        "cases_resolved": _scalar(
            db, "SELECT COUNT(*) FROM collections_cases WHERE status='closed' AND strftime('%Y-%m', date(closed_at,'-14 days','start of month','+1 month'))=?", (month,)),
        "remaining_outstanding": float(_scalar(
            db, f"SELECT SUM(arrears_amount) FROM collections_cases WHERE status IN {OPEN}")),
        "open_cases": _scalar(db, f"SELECT COUNT(*) FROM collections_cases WHERE status IN {OPEN}"),
    }


def sales_quality_report(db, month):
    """Per consultant: new members, first-month failures, recoveries and
    hand-overs to Reception, with the failure rate against new members."""
    consultants = {}

    def row(staff_id, name):
        return consultants.setdefault(staff_id, {
            "id": staff_id, "name": name or f"User {staff_id}", "new_members": 0,
            "failed": 0, "recovered": 0, "transferred": 0, "unresolved": 0,
        })

    for r in db.execute(
        """SELECT COALESCE(l.assigned_to, ma.created_by) AS staff_id, u.full_name AS name,
                  COUNT(DISTINCT m.id) AS n
           FROM members m
           JOIN membership_applications ma ON ma.member_id = m.id
           LEFT JOIN leads l ON l.id = ma.lead_id
           LEFT JOIN users u ON u.id = COALESCE(l.assigned_to, ma.created_by)
           WHERE strftime('%Y-%m', date(m.join_date,'-14 days','start of month','+1 month'))=? AND COALESCE(l.assigned_to, ma.created_by) IS NOT NULL
           GROUP BY 1, 2""", (month,)).fetchall():
        row(r["staff_id"], r["name"])["new_members"] = r["n"]

    for r in db.execute(
        """SELECT h.to_staff_id AS staff_id, u.full_name AS name, COUNT(*) AS n
           FROM collection_assignment_history h LEFT JOIN users u ON u.id = h.to_staff_id
           WHERE h.reason='FIRST_MONTH_FAILED_DEBIT' AND strftime('%Y-%m', date(h.transferred_at,'-14 days','start of month','+1 month'))=?
           GROUP BY 1, 2""", (month,)).fetchall():
        row(r["staff_id"], r["name"])["failed"] = r["n"]

    for r in db.execute(
        """SELECT h.from_staff_id AS staff_id, u.full_name AS name, COUNT(*) AS n
           FROM collection_assignment_history h LEFT JOIN users u ON u.id = h.from_staff_id
           WHERE h.reason='SECOND_MONTH_ARREARS' AND h.from_owner_type='SALES'
             AND strftime('%Y-%m', date(h.transferred_at,'-14 days','start of month','+1 month'))=? GROUP BY 1, 2""", (month,)).fetchall():
        row(r["staff_id"], r["name"])["transferred"] = r["n"]

    # Recovered by Sales: a first-month case paid in full without ever leaving Sales.
    for r in db.execute(
        """SELECT c.original_sales_consultant_id AS staff_id, u.full_name AS name, COUNT(*) AS n
           FROM collections_cases c LEFT JOIN users u ON u.id = c.original_sales_consultant_id
           WHERE c.status='closed' AND c.resolution_type='PAID_IN_FULL' AND strftime('%Y-%m', date(c.closed_at,'-14 days','start of month','+1 month'))=?
             AND c.original_sales_consultant_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM collection_assignment_history h
                              WHERE h.case_id=c.id AND h.to_owner_type='RECEPTION' AND h.from_owner_type='SALES')
             AND EXISTS (SELECT 1 FROM collection_assignment_history h
                          WHERE h.case_id=c.id AND h.reason='FIRST_MONTH_FAILED_DEBIT')
           GROUP BY 1, 2""", (month,)).fetchall():
        row(r["staff_id"], r["name"])["recovered"] = r["n"]

    for r in db.execute(
        f"""SELECT c.owner_staff_id AS staff_id, u.full_name AS name, COUNT(*) AS n
            FROM collections_cases c LEFT JOIN users u ON u.id = c.owner_staff_id
            WHERE c.owner_type='SALES' AND c.status IN {OPEN} GROUP BY 1, 2""").fetchall():
        row(r["staff_id"], r["name"])["unresolved"] = r["n"]

    rows = sorted(consultants.values(), key=lambda r: (-r["failed"], r["name"]))
    for r in rows:
        r["failure_rate"] = round(100.0 * r["failed"] / r["new_members"], 1) if r["new_members"] else None
    return {"month": month, "cycle_start": cycle_bounds(month)[0].isoformat(), "cycle_end": cycle_bounds(month)[1].isoformat(), "rows": rows}


def performance_report(db, month):
    """Reception and collections activity for one 15th?14th cycle."""
    from .collections_engine import reached_sql

    callers = db.execute(
        f"""SELECT t.assigned_to AS staff_id, u.full_name AS name, COUNT(*) AS assigned,
                  SUM(CASE WHEN t.status='completed' THEN 1 ELSE 0 END) AS calls,
                  SUM(CASE WHEN {reached_sql()} THEN 1 ELSE 0 END) AS contacts
           FROM collection_call_tasks t LEFT JOIN users u ON u.id = t.assigned_to
           WHERE strftime('%Y-%m', date(t.task_date,'-14 days','start of month','+1 month'))=? GROUP BY 1, 2 ORDER BY calls DESC, name""", (month,)).fetchall()
    totals = {
        "calls": sum(c["calls"] or 0 for c in callers),
        "contacts": sum(c["contacts"] or 0 for c in callers),
        "ptps_created": _scalar(db, "SELECT COUNT(*) FROM ptp_agreements WHERE strftime('%Y-%m', date(created_at,'-14 days','start of month','+1 month'))=?", (month,)),
        "ptps_honoured": _scalar(
            db, "SELECT COUNT(*) FROM ptp_agreements WHERE ptp_status='paid' AND strftime('%Y-%m', date(updated_at,'-14 days','start of month','+1 month'))=?", (month,)),
        "ptps_broken": _scalar(
            db, "SELECT COUNT(*) FROM ptp_agreements WHERE ptp_status='broken' AND strftime('%Y-%m', date(updated_at,'-14 days','start of month','+1 month'))=?", (month,)),
        "arrangements": _scalar(
            db, """SELECT COUNT(*) FROM ptp_agreements
                   WHERE strftime('%Y-%m', date(created_at,'-14 days','start of month','+1 month'))=? AND COALESCE(arrangement_type,'full') <> 'full'""", (month,)),
        "debichecks": _scalar(
            db, "SELECT COUNT(*) FROM debicheck_mandates WHERE strftime('%Y-%m', date(created_at,'-14 days','start of month','+1 month'))=?", (month,)),
        "amount_collected": float(_scalar(
            db, """SELECT SUM(amount_paid) FROM collections
                   WHERE strftime('%Y-%m', date(collection_date,'-14 days','start of month','+1 month'))=? AND status IN ('paid','partial')""", (month,))),
        "discount_requests": _scalar(
            db, """SELECT COUNT(*) FROM ptp_agreements
                   WHERE strftime('%Y-%m', date(created_at,'-14 days','start of month','+1 month'))=? AND COALESCE(discount_pct,0) > 0""", (month,)),
        "access_restrictions": _scalar(
            db, "SELECT COUNT(*) FROM access_decisions WHERE access_status='BLOCKED' AND strftime('%Y-%m', date(evaluated_at,'-14 days','start of month','+1 month'))=?", (month,)),
    }
    totals["contact_rate"] = round(100.0 * totals["contacts"] / totals["calls"], 1) if totals["calls"] else None
    return {"month": month, "cycle_start": cycle_bounds(month)[0].isoformat(), "cycle_end": cycle_bounds(month)[1].isoformat(), "callers": callers, "totals": totals}


def _min_talk(db):
    from .collections_engine import DEFAULT_CONTACT_TALK_SECONDS, get_collection_rule_int

    return get_collection_rule_int(db, "contact_talk_seconds", DEFAULT_CONTACT_TALK_SECONDS)


def daily_call_report(db, day, call_target=80, contact_target=10):
    """Attempts use the actual call date; pending work uses its assigned date.

    Reached follows a saved contact outcome; PBX confirmation is a separate count.
    """
    from .collections_engine import reached_sql, talk_seconds_sql, verified_sql

    min_talk = _min_talk(db)
    reached = reached_sql()
    verified = verified_sql(min_talk)
    talk = talk_seconds_sql()
    rows = db.execute(
        f"""SELECT u.id AS staff_id, u.full_name AS name,
                  SUM(CASE WHEN t.status='completed' AND
                      substr(COALESCE(t.called_at,t.task_date),1,10)=? THEN 1 ELSE 0 END) AS calls,
                  SUM(CASE WHEN t.status='completed' AND {reached} AND
                      substr(COALESCE(t.called_at,t.task_date),1,10)=? THEN 1 ELSE 0 END) AS contacts,
                  SUM(CASE WHEN t.status='completed' AND {verified} AND
                      substr(COALESCE(t.called_at,t.task_date),1,10)=? THEN 1 ELSE 0 END) AS verified,
                  SUM(CASE WHEN t.status='completed' AND
                      substr(COALESCE(t.called_at,t.task_date),1,10)=? THEN {talk} ELSE 0 END) AS talk_seconds,
                  SUM(CASE WHEN t.status='pending' AND t.task_date=? THEN 1 ELSE 0 END) AS pending
           FROM users u LEFT JOIN collection_call_tasks t ON t.assigned_to=u.id
           WHERE (u.active=1 AND u.is_collections_caller=1)
              OR (t.status='completed' AND substr(COALESCE(t.called_at,t.task_date),1,10)=?)
              OR (t.status='pending' AND t.task_date=?)
           GROUP BY u.id,u.full_name ORDER BY calls DESC,u.full_name""",
        (day, day, day, day, day, day, day),
    ).fetchall()
    callers = []
    for row in rows:
        item = dict(row)
        item['talk_seconds'] = item['talk_seconds'] or 0
        item['avg_talk_seconds'] = round(item['talk_seconds'] / item['calls']) if item['calls'] else None
        item['contact_rate'] = round(100 * item['contacts'] / item['calls'], 1) if item['calls'] else None
        item['call_progress'] = round(100 * item['calls'] / call_target, 1) if call_target else 0
        item['contact_progress'] = round(100 * item['contacts'] / contact_target, 1) if contact_target else 0
        callers.append(item)
    outcomes = db.execute(
        """SELECT COALESCE(outcome,'unknown') AS outcome, COUNT(*) AS total
           FROM collection_call_tasks WHERE status='completed'
           AND substr(COALESCE(called_at,task_date),1,10)=?
           GROUP BY outcome ORDER BY total DESC,outcome""", (day,),
    ).fetchall()
    calls = sum(row['total'] for row in outcomes)
    contacts = _scalar(db, f"""SELECT COUNT(*) FROM collection_call_tasks t
        WHERE t.status='completed' AND {reached}
        AND substr(COALESCE(t.called_at,t.task_date),1,10)=?""", (day,))
    verified_total = _scalar(db, f"""SELECT COUNT(*) FROM collection_call_tasks t
        WHERE t.status='completed' AND {verified}
        AND substr(COALESCE(t.called_at,t.task_date),1,10)=?""", (day,))
    pending = _scalar(db, "SELECT COUNT(*) FROM collection_call_tasks WHERE status='pending' AND task_date=?", (day,))
    return {'day': day, 'callers': callers, 'outcomes': outcomes,
            'calls': calls, 'contacts': contacts, 'verified': verified_total, 'pending': pending,
            'talk_seconds': sum(c['talk_seconds'] for c in callers),
            'contact_rate': round(100 * contacts / calls,1) if calls else None,
            'call_target': call_target, 'contact_target': contact_target,
            'min_talk_seconds': min_talk}
