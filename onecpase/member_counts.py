"""Canonical member population counts for application-facing summaries."""


def get_member_counts(db, *, uploaded_by_id=None):
    """Return local member totals, optionally limited to one staff member.

    The ``members`` table is the application's source of truth. External
    systems such as Itensity are reconciled separately and never replace
    these counts.
    """
    where_clause = ""
    parameters = ()
    if uploaded_by_id is not None:
        where_clause = " WHERE uploaded_by_id = ?"
        parameters = (uploaded_by_id,)

    rows = db.execute(
        """SELECT COALESCE(NULLIF(TRIM(member_status), ''), '<blank>') AS status,
                  COUNT(*) AS count
           FROM members""" + where_clause + " GROUP BY status",
        parameters,
    ).fetchall()
    by_status = {row["status"]: row["count"] for row in rows}

    return {
        "total": sum(by_status.values()),
        "active": by_status.get("Active", 0),
        "inactive": by_status.get("Inactive", 0),
        "blocked": by_status.get("Blocked", 0),
        "unverified": by_status.get("Unverified", 0),
        "by_status": by_status,
    }
