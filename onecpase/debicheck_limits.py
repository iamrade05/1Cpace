"""How often staff may send a client's DebiCheck mandate to NuPay.

The rule (business decision, 6 Nov 2026), per client and per kind of mandate
(the arrears arrangement and the normal monthly instalment are separate kinds,
because one arrangement can need both on the same day):

* one send a day (South African date), so a client never gets duplicate
  mandates. Nobody can override this.
* the next send waits until the previous one is known to have failed: while
  NuPay still shows it waiting for the client, staff must Check NuPay Status
  first.
* after two sends that ended rejected or not active, staff cannot send a third.
  An admin or manager can, by ticking the extra-attempt box; that is logged.

Only sends that reached NuPay count. A push that errored before NuPay accepted
it (timeout, form error) is forgotten, so staff can retry the same day.

Every send is recorded in ``debicheck_send_log``, because the mandate page's
"Resend Automatically" re-sends the same mandate row: counting mandate rows
would miss resends.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import request, session

from .debicheck import normalise_debicheck_status

SAST = ZoneInfo("Africa/Johannesburg")
MAX_FAILED_SENDS = 2
AWAITING_CLIENT = {"submitted", "pending", "reviewing"}
OVERRIDE_ROLES = {"admin", "manager"}
OVERRIDE_FIELD = "extra_attempt"


def ensure_schema(db, backend: str = "sqlite") -> None:
    identity = "SERIAL PRIMARY KEY" if backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    db.execute(
        f"""CREATE TABLE IF NOT EXISTS debicheck_send_log (
            id {identity},
            member_id INTEGER NOT NULL,
            mandate_id INTEGER NOT NULL,
            purpose TEXT NOT NULL,
            state TEXT NOT NULL,
            sent_day TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            sent_by INTEGER,
            outcome TEXT,
            extra_attempt_by INTEGER)"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_debicheck_send_log_member "
        "ON debicheck_send_log (member_id, purpose)"
    )
    if db.execute("SELECT 1 FROM debicheck_send_log LIMIT 1").fetchone() is None:
        _backfill(db)
    db.commit()


def _backfill(db) -> None:
    """Start the log from the mandates already sent, one send each."""
    rows = db.execute(
        """SELECT id, member_id, submitted_at, submitted_by FROM debicheck_mandates
           WHERE submitted_at IS NOT NULL AND status <> 'failed'
             AND COALESCE(imported_from_nupay, 0) = 0
           ORDER BY id"""
    ).fetchall()
    for row in rows:
        try:
            stamp = datetime.fromisoformat(str(row["submitted_at"]).strip())
        except ValueError:
            continue
        if stamp.tzinfo is None:  # the server writes UTC without an offset
            stamp = stamp.replace(tzinfo=timezone.utc)
        local = stamp.astimezone(SAST)
        db.execute(
            """INSERT INTO debicheck_send_log
               (member_id, mandate_id, purpose, state, sent_day, sent_at, sent_by)
               VALUES (?, ?, ?, 'sent', ?, ?, ?)""",
            (row["member_id"], row["id"], mandate_purpose(db, row["id"]),
             local.date().isoformat(), local.isoformat(timespec="seconds"), row["submitted_by"]),
        )


def today_sast() -> str:
    return datetime.now(SAST).date().isoformat()


def mandate_purpose(db, mandate_id: int) -> str:
    from .ptp_grant import PURPOSE_NORMAL
    link = db.execute(
        "SELECT purpose FROM ptp_mandate_links WHERE mandate_id=? ORDER BY id LIMIT 1", (mandate_id,)
    ).fetchone()
    return link["purpose"] if link else PURPOSE_NORMAL


def send_block(db, member_id: int, purpose: str, *, today: str | None = None) -> dict | None:
    """Why this client's *purpose* mandate cannot be sent now, or None.

    Returns ``{"kind", "message", "overridable"}``; kind is ``today``,
    ``awaiting`` or ``failed_limit``.
    """
    today = today or today_sast()
    kind_label = purpose.lower()
    sent_today = db.execute(
        """SELECT state FROM debicheck_send_log
           WHERE member_id=? AND purpose=? AND sent_day=? LIMIT 1""",
        (member_id, purpose, today),
    ).fetchone()
    if sent_today:
        busy = sent_today["state"] == "sending"
        return {
            "kind": "today",
            "message": (
                f"A {kind_label} DebiCheck for this client is being sent to NuPay right now."
                if busy else
                f"A {kind_label} DebiCheck was already sent to NuPay for this client today. "
                "Only one a day is allowed. If it is rejected or not active, send again tomorrow."
            ),
            "overridable": False,
        }

    sends = db.execute(
        """SELECT l.outcome, d.status FROM debicheck_send_log l
           LEFT JOIN debicheck_mandates d ON d.id = l.mandate_id
           WHERE l.member_id=? AND l.purpose=? AND l.state='sent'
           ORDER BY l.id""",
        (member_id, purpose),
    ).fetchall()
    if any(s["outcome"] is None and (s["status"] or "") in AWAITING_CLIENT for s in sends):
        return {
            "kind": "awaiting",
            "message": (
                f"The last {kind_label} DebiCheck sent for this client is still waiting for the "
                "client on NuPay. Use Check NuPay Status first; send again only once it shows "
                "rejected or not active."
            ),
            "overridable": True,
        }
    failed = sum(
        1 for s in sends
        if normalise_debicheck_status(s["outcome"] or s["status"]) == "failed"
    )
    if failed >= MAX_FAILED_SENDS:
        return {
            "kind": "failed_limit",
            "message": (
                f"This client already has {failed} {kind_label} DebiCheck sends that were rejected "
                "or not active. A third can only be sent by a manager."
            ),
            "overridable": True,
        }
    return None


def can_override(block: dict | None, role: str | None) -> bool:
    return bool(block and block["overridable"] and role in OVERRIDE_ROLES)


def mandate_send_block(db, mandate_id: int, member_id: int) -> dict | None:
    """send_block for a mandate's own kind, for the mandate page."""
    return send_block(db, member_id, mandate_purpose(db, mandate_id))


def claim_send(db, mandate_id: int, member_id: int) -> tuple[int | None, str | None]:
    """Reserve today's send before contacting NuPay; ``(claim_id, None)`` or ``(None, reason)``.

    Call it inside the request; commit afterwards so a second click sees the claim.
    Then call ``finish_send`` with the push result.
    """
    from .database import audit_log, member_activity

    purpose = mandate_purpose(db, mandate_id)
    block = send_block(db, member_id, purpose)
    role = session.get("role")
    extra_by = None
    if block:
        if not (can_override(block, role) and request.form.get(OVERRIDE_FIELD) == "1"):
            return None, block["message"]
        extra_by = session.get("user_id")
        who = session.get("full_name") or session.get("username") or role
        member_activity(db, member_id, f"Extra DebiCheck attempt for mandate #{mandate_id} allowed by {who}: {block['message']}")
        audit_log(db, f"debicheck_extra_attempt_{role}", new_value=f"mandate {mandate_id}: {block['kind']}")

    # A resend replaces the mandate's status, so keep what the earlier send ended as.
    current = db.execute("SELECT status FROM debicheck_mandates WHERE id=?", (mandate_id,)).fetchone()
    db.execute(
        "UPDATE debicheck_send_log SET outcome=? WHERE mandate_id=? AND state='sent' AND outcome IS NULL",
        (current["status"] if current else "unknown", mandate_id),
    )
    now = datetime.now(SAST)
    claim_id = db.execute(
        """INSERT INTO debicheck_send_log
           (member_id, mandate_id, purpose, state, sent_day, sent_at, sent_by, extra_attempt_by)
           VALUES (?, ?, ?, 'sending', ?, ?, ?, ?)""",
        (member_id, mandate_id, purpose, now.date().isoformat(),
         now.isoformat(timespec="seconds"), session.get("user_id"), extra_by),
    ).lastrowid
    return claim_id, None


def finish_send(db, claim_id: int | None, success: bool) -> None:
    """Keep the claim as a real send, or drop it when NuPay never accepted the mandate."""
    if claim_id is None:
        return
    if success:
        db.execute("UPDATE debicheck_send_log SET state='sent' WHERE id=?", (claim_id,))
    else:
        db.execute("DELETE FROM debicheck_send_log WHERE id=?", (claim_id,))
