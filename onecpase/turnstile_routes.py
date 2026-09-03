from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from .auth import permission_required
from .database import get_db, member_activity
from .turnstile import evaluate_access, get_turnstile_status


turnstile_bp = Blueprint("turnstile", __name__, url_prefix="/turnstile")


@turnstile_bp.route("/")
@permission_required("all_members")
def index():
    db = get_db()
    events = db.execute(
        """SELECT te.*, m.first_name, m.last_name, m.member_status,
                  m.gym_access_status
           FROM turnstile_events te
           LEFT JOIN members m ON m.id = te.member_id
           ORDER BY te.created_at DESC, te.id DESC LIMIT 100"""
    ).fetchall()
    linked_members = db.execute(
        "SELECT COUNT(*) FROM members WHERE access_credential IS NOT NULL AND access_credential != ''"
    ).fetchone()[0]
    return render_template(
        "turnstile/index.html",
        status=get_turnstile_status(),
        events=events,
        linked_members=linked_members,
    )


@turnstile_bp.post("/scan")
@permission_required("all_members")
def scan_credential():
    payload = request.get_json(silent=True) or request.form
    credential = str(payload.get("credential", "")).strip()
    if not credential:
        return jsonify(ok=False, error="No barcode was received."), 400
    if len(credential) > 256:
        return jsonify(ok=False, error="Barcode is too long."), 400

    db = get_db()
    member, decision, reason = evaluate_access(db, credential)
    event = db.execute(
        """INSERT INTO turnstile_events
           (device_serial, raw_report_hex, credential, member_id, decision, reason, direction)
           VALUES ('Datalogic S/N G17G66169', ?, ?, ?, ?, ?, 'entry')""",
        (
            credential.encode("utf-8").hex().upper(), credential,
            member["id"] if member else None, decision, reason,
        ),
    )
    db.commit()
    return jsonify(
        ok=True,
        event_id=event.lastrowid,
        credential=credential,
        decision=decision,
        reason=reason,
        member={
            "id": member["id"],
            "name": f"{member['first_name']} {member['last_name']}",
        } if member else None,
    )


@turnstile_bp.post("/events/<int:event_id>/assign")
@permission_required("add_member")
def assign_credential(event_id: int):
    db = get_db()
    event = db.execute(
        "SELECT * FROM turnstile_events WHERE id=?", (event_id,)
    ).fetchone()
    member_id = request.form.get("member_id", "").strip()
    if event is None or not event["credential"]:
        flash("That event has no credential to assign.", "error")
        return redirect(url_for("turnstile.index"))
    if not member_id.isdigit():
        flash("Enter a valid member ID.", "error")
        return redirect(url_for("turnstile.index"))
    member = db.execute("SELECT id, first_name, last_name FROM members WHERE id=?", (int(member_id),)).fetchone()
    if member is None:
        flash("Member not found.", "error")
        return redirect(url_for("turnstile.index"))
    existing = db.execute(
        "SELECT id FROM members WHERE access_credential=? AND id!=?",
        (event["credential"], member["id"]),
    ).fetchone()
    if existing:
        flash(f"That credential is already linked to member #{existing['id']}.", "error")
        return redirect(url_for("turnstile.index"))

    db.execute(
        "UPDATE members SET access_credential=? WHERE id=?",
        (event["credential"], member["id"]),
    )
    db.execute(
        "UPDATE turnstile_events SET member_id=? WHERE credential=?",
        (member["id"], event["credential"]),
    )
    member_activity(db, member["id"], f"Turnstile credential linked from event #{event_id}")
    db.commit()
    flash(f"Credential linked to {member['first_name']} {member['last_name']}.", "success")
    return redirect(url_for("turnstile.index"))
