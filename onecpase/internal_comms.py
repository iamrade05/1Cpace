"""Internal staff communication: direct messages and an announcements board.

Deliberately separate from comms.py, which handles conversations with members
and the public over WhatsApp and Facebook. Nothing here leaves the building:
no external API, no tokens, no webhooks — which is why it works today while
the Meta integration is still waiting on credentials.

Two things, because staff need both:

* direct messages — any two staff, one thread per pair;
* announcements — managers and admins post, everyone reads, and who has read
  what is recorded so a notice can be chased rather than assumed.
"""
from __future__ import annotations

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, session, url_for)

from .auth import login_required
from .database import get_db

internal_comms_bp = Blueprint("internal_comms", __name__, url_prefix="/internal")

# Who may post, pin or archive an announcement. Reading is for everyone.
ANNOUNCER_ROLES = {"admin", "manager"}

MAX_MESSAGE = 4000
MAX_TITLE = 160
MAX_BODY = 8000


def may_announce() -> bool:
    return session.get("role") in ANNOUNCER_ROLES


def _me() -> int:
    return int(session["user_id"])


def _pair(a: int, b: int) -> tuple[int, int]:
    """Order a pair of user ids consistently.

    staff_threads stores the lower id first and has a UNIQUE on the pair, so
    whichever of the two people starts the conversation they land in the same
    thread rather than creating a second one facing the other way.
    """
    return (a, b) if a <= b else (b, a)


def _thread_for(db, me: int, other: int, *, create: bool = False):
    low, high = _pair(me, other)
    row = db.execute(
        "SELECT * FROM staff_threads WHERE user_low_id=? AND user_high_id=?",
        (low, high),
    ).fetchone()
    if row or not create:
        return row
    db.execute(
        "INSERT INTO staff_threads (user_low_id, user_high_id) VALUES (?, ?)",
        (low, high),
    )
    db.commit()
    return db.execute(
        "SELECT * FROM staff_threads WHERE user_low_id=? AND user_high_id=?",
        (low, high),
    ).fetchone()


def unread_counts(db, user_id) -> dict:
    """Unread direct messages and unread announcements for the nav badge.

    Runs on every page render, so both halves are single indexed counts and
    neither joins anything wide.
    """
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return {"messages": 0, "announcements": 0, "total": 0}

    messages = db.execute(
        """SELECT COUNT(*) FROM staff_messages m
           JOIN staff_threads t ON t.id = m.thread_id
           WHERE m.read_at IS NULL AND m.sender_id <> ?
             AND (t.user_low_id = ? OR t.user_high_id = ?)""",
        (user_id, user_id, user_id),
    ).fetchone()[0]

    announcements = db.execute(
        """SELECT COUNT(*) FROM announcements a
           WHERE a.active = 1
             AND NOT EXISTS (SELECT 1 FROM announcement_reads r
                             WHERE r.announcement_id = a.id AND r.user_id = ?)""",
        (user_id,),
    ).fetchone()[0]

    return {
        "messages": messages,
        "announcements": announcements,
        "total": messages + announcements,
    }


# ── Direct messages ──────────────────────────────────────────────────────────

@internal_comms_bp.get("/messages")
@login_required
def messages_index():
    db, me = get_db(), _me()
    threads = db.execute(
        """SELECT t.id,
                  CASE WHEN t.user_low_id = ? THEN t.user_high_id ELSE t.user_low_id END AS other_id,
                  t.last_message_at,
                  (SELECT body FROM staff_messages
                    WHERE thread_id = t.id ORDER BY id DESC LIMIT 1) AS last_body,
                  (SELECT COUNT(*) FROM staff_messages
                    WHERE thread_id = t.id AND read_at IS NULL AND sender_id <> ?) AS unread
           FROM staff_threads t
           WHERE t.user_low_id = ? OR t.user_high_id = ?
           ORDER BY COALESCE(t.last_message_at, '') DESC, t.id DESC""",
        (me, me, me, me),
    ).fetchall()

    names = {
        row["id"]: row
        for row in db.execute(
            "SELECT id, username, full_name, role, department FROM users WHERE active = 1"
        ).fetchall()
    }
    # Anyone active except yourself is someone you can start a thread with.
    colleagues = [row for uid, row in sorted(names.items()) if uid != me]

    return render_template(
        "internal/messages.html",
        threads=threads, names=names, colleagues=colleagues,
    )


@internal_comms_bp.route("/messages/<int:other_id>", methods=["GET", "POST"])
@login_required
def thread(other_id: int):
    db, me = get_db(), _me()
    if other_id == me:
        flash("You cannot message yourself.", "error")
        return redirect(url_for("internal_comms.messages_index"))

    other = db.execute(
        "SELECT id, username, full_name, role, department FROM users WHERE id=? AND active=1",
        (other_id,),
    ).fetchone()
    if not other:
        abort(404)

    if request.method == "POST":
        body = (request.form.get("body") or "").strip()
        if not body:
            flash("Write a message first.", "error")
        elif len(body) > MAX_MESSAGE:
            flash(f"Messages are limited to {MAX_MESSAGE} characters.", "error")
        else:
            row = _thread_for(db, me, other_id, create=True)
            db.execute(
                "INSERT INTO staff_messages (thread_id, sender_id, body) VALUES (?, ?, ?)",
                (row["id"], me, body),
            )
            db.execute(
                "UPDATE staff_threads SET last_message_at = datetime('now') WHERE id=?",
                (row["id"],),
            )
            db.commit()
        return redirect(url_for("internal_comms.thread", other_id=other_id))

    row = _thread_for(db, me, other_id)
    messages = []
    if row:
        messages = db.execute(
            "SELECT * FROM staff_messages WHERE thread_id=? ORDER BY id",
            (row["id"],),
        ).fetchall()
        # Opening the thread is what marks their messages read.
        db.execute(
            """UPDATE staff_messages SET read_at = datetime('now')
               WHERE thread_id=? AND sender_id <> ? AND read_at IS NULL""",
            (row["id"], me),
        )
        db.commit()

    return render_template("internal/thread.html", other=other, messages=messages, me=me)


# ── Announcements ────────────────────────────────────────────────────────────

@internal_comms_bp.get("/announcements")
@login_required
def announcements_index():
    db, me = get_db(), _me()
    rows = db.execute(
        """SELECT a.*, u.full_name AS author,
                  EXISTS (SELECT 1 FROM announcement_reads r
                          WHERE r.announcement_id = a.id AND r.user_id = ?) AS seen,
                  (SELECT COUNT(*) FROM announcement_reads r
                    WHERE r.announcement_id = a.id) AS read_count
           FROM announcements a
           LEFT JOIN users u ON u.id = a.created_by
           WHERE a.active = 1
           ORDER BY a.pinned DESC, a.id DESC""",
        (me,),
    ).fetchall()
    staff_total = db.execute("SELECT COUNT(*) FROM users WHERE active = 1").fetchone()[0]
    return render_template(
        "internal/announcements.html",
        announcements=rows, staff_total=staff_total, may_announce=may_announce(),
    )


@internal_comms_bp.post("/announcements/new")
@login_required
def announcement_create():
    if not may_announce():
        abort(403)
    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    if not title or not body:
        flash("An announcement needs a title and a message.", "error")
    elif len(title) > MAX_TITLE or len(body) > MAX_BODY:
        flash("That announcement is too long.", "error")
    else:
        db = get_db()
        db.execute(
            "INSERT INTO announcements (title, body, created_by, pinned) VALUES (?, ?, ?, ?)",
            (title, body, _me(), 1 if request.form.get("pinned") else 0),
        )
        db.commit()
        flash("Announcement posted.", "success")
    return redirect(url_for("internal_comms.announcements_index"))


@internal_comms_bp.post("/announcements/<int:announcement_id>/read")
@login_required
def announcement_read(announcement_id: int):
    db = get_db()
    if not db.execute(
        "SELECT 1 FROM announcements WHERE id=? AND active=1", (announcement_id,)
    ).fetchone():
        abort(404)
    # One row per person per announcement; re-reading is not an error.
    db.execute(
        "INSERT OR IGNORE INTO announcement_reads (announcement_id, user_id) VALUES (?, ?)",
        (announcement_id, _me()),
    )
    db.commit()
    return redirect(url_for("internal_comms.announcements_index"))


@internal_comms_bp.post("/announcements/<int:announcement_id>/archive")
@login_required
def announcement_archive(announcement_id: int):
    if not may_announce():
        abort(403)
    db = get_db()
    # Archived, never deleted — the read record is the evidence it was seen.
    db.execute("UPDATE announcements SET active=0 WHERE id=?", (announcement_id,))
    db.commit()
    flash("Announcement archived.", "success")
    return redirect(url_for("internal_comms.announcements_index"))


@internal_comms_bp.post("/announcements/<int:announcement_id>/pin")
@login_required
def announcement_pin(announcement_id: int):
    if not may_announce():
        abort(403)
    db = get_db()
    row = db.execute(
        "SELECT pinned FROM announcements WHERE id=? AND active=1", (announcement_id,)
    ).fetchone()
    if not row:
        abort(404)
    db.execute(
        "UPDATE announcements SET pinned=? WHERE id=?",
        (0 if row["pinned"] else 1, announcement_id),
    )
    db.commit()
    return redirect(url_for("internal_comms.announcements_index"))
