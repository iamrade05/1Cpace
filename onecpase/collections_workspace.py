"""Data for the Collections dashboard and case queue.

Everything here reads collection cases as the workflow object: stage, priority,
owner and next action come from collections_engine, never recomputed in a
template. The financial figures are summed from the same cases and the same
reconciled payments the rest of Collections uses.
"""
from __future__ import annotations

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


def dashboard_metrics(db, *, user_id=None, today=None):
    """Financial position, today's work, and the figures each role cares about."""
    today = today or date.today()
    month = today.strftime("%Y-%m")
    day = today.isoformat()

    financial = {
        "total_outstanding": float(_scalar(
            db, f"SELECT SUM(arrears_amount) FROM collections_cases WHERE status IN {OPEN}")),
        "members_in_arrears": _scalar(
            db, f"SELECT COUNT(DISTINCT member_id) FROM collections_cases WHERE status IN {OPEN}"),
        "failed_debits": _scalar(
            db, f"SELECT COUNT(*) FROM collections_cases WHERE status IN {OPEN} AND failed_debit_date IS NOT NULL"),
        "collected_this_month": float(_scalar(
            db, """SELECT SUM(amount_paid) FROM collections
                   WHERE substr(collection_date,1,7)=? AND status IN ('paid','partial')""", (month,))),
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
                 AND substr(c.closed_at,1,7)=? GROUP BY 1""", (month,)).fetchall()
    }
    manager = {"open_by_owner": owners, "recovered_this_month_by_owner": recoveries}
    return {"financial": financial, "workload": workload, "sales": sales, "manager": manager}
