"""Photographed/scanned call-list upload, OCR review, and import into leads.

Ported from the reference 1Cpace app's /calls upload-review-import workflow
(Phase 2 of the 1Cpase rewrite). 1Cpace writes imported rows to its own
call_log table; this app has no equivalent table — its sales workflow lives
entirely in leads/lead_activities — so an imported row becomes a lead (or an
activity on an existing one, via the same duplicate-phone match the public
join funnel already uses) instead of a parallel call-tracking record.

OCR results are staged in call_list_upload_rows for human review and are
never written to a lead until a staff member confirms the row.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from .auth import permission_required
from .call_list_parser import CallListParserError, SUPPORTED_EXTENSIONS, extract_call_list, normalize_phone
from .database import get_db
from .leads import _add_activity, _allocate_sales_consultant, _automatic_next_action, _find_duplicate, normalise_phone

call_list_bp = Blueprint("call_list", __name__, url_prefix="/calls")

# call_list_parser's outcomes -> a short note prefix, so the resulting lead
# activity reads naturally regardless of which outcome was picked on review.
_OUTCOME_LABELS = {
    "wrong_number": "Wrong number",
    "not_interested": "Not interested",
    "appointment_set": "Appointment set",
    "call_back_later": "Asked for a call back",
    "voicemail": "Left voicemail",
    "no_answer": "No answer",
    "other": "Call list entry",
}


def _upload_dir() -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "call_lists"
    path.mkdir(parents=True, exist_ok=True)
    return path


@call_list_bp.get("")
@permission_required("capture_leads")
def index():
    db = get_db()
    uploads = db.execute(
        """SELECT u.*, us.full_name AS uploaded_by_name
           FROM call_list_uploads u LEFT JOIN users us ON u.uploaded_by = us.id
           ORDER BY u.created_at DESC LIMIT 50"""
    ).fetchall()
    return render_template("call_list/index.html", uploads=uploads)


@call_list_bp.route("/upload", methods=["GET", "POST"])
@permission_required("capture_leads")
def upload():
    if request.method == "GET":
        return render_template("call_list/upload.html")

    file = request.files.get("document")
    if not file or not file.filename:
        flash("Choose a photo, scan, or PDF of the call list.", "error")
        return redirect(url_for("call_list.upload"))

    extension = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if extension not in SUPPORTED_EXTENSIONS:
        flash("Unsupported file type. Use JPG, PNG, WebP, TIFF, or PDF.", "error")
        return redirect(url_for("call_list.upload"))

    upload_dir = _upload_dir()
    safe_name = secure_filename(file.filename)
    stored_name = f"{int(time.time())}_{safe_name}"
    dest = upload_dir / stored_name
    file.save(str(dest))
    file_hash = hashlib.sha256(dest.read_bytes()).hexdigest()

    db = get_db()
    duplicate_upload = db.execute(
        "SELECT id, status FROM call_list_uploads WHERE file_sha256=?", (file_hash,)
    ).fetchone()
    if duplicate_upload:
        dest.unlink(missing_ok=True)
        flash("This exact file was already uploaded — opening the existing review instead.", "warning")
        return redirect(url_for("call_list.review", upload_id=duplicate_upload["id"]))

    cursor = db.execute(
        """INSERT INTO call_list_uploads
           (original_filename, stored_filename, file_type, file_sha256, status, uploaded_by)
           VALUES (?, ?, ?, ?, 'processing', ?)""",
        (file.filename, stored_name, extension, file_hash, session.get("user_id")),
    )
    upload_id = cursor.lastrowid
    db.commit()

    try:
        result = extract_call_list(dest)
    except CallListParserError as exc:
        db.execute(
            "UPDATE call_list_uploads SET status='failed', parser_message=? WHERE id=?",
            (str(exc), upload_id),
        )
        db.commit()
        flash(str(exc), "error")
        return redirect(url_for("call_list.index"))

    for number, row in enumerate(result.rows, start=1):
        db.execute(
            """INSERT INTO call_list_upload_rows
               (upload_id, row_number, client_name, contact, raw_response,
                mapped_outcome, appointment_date, appointment_time, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (upload_id, number, row.client_name, row.contact, row.raw_response,
             row.mapped_outcome, row.appointment_date, row.appointment_time, row.confidence),
        )
    db.execute(
        "UPDATE call_list_uploads SET status='reviewing', row_count=? WHERE id=?",
        (len(result.rows), upload_id),
    )
    db.execute(
        "UPDATE call_list_uploads SET parser_message=? WHERE id=?",
        (result.message, upload_id),
    )
    db.commit()
    flash(result.message, "success" if result.rows else "warning")
    return redirect(url_for("call_list.review", upload_id=upload_id))


@call_list_bp.get("/<int:upload_id>/review")
@permission_required("capture_leads")
def review(upload_id: int):
    db = get_db()
    upload_row = db.execute("SELECT * FROM call_list_uploads WHERE id=?", (upload_id,)).fetchone()
    if upload_row is None:
        flash("Upload not found.", "error")
        return redirect(url_for("call_list.index"))
    rows = db.execute(
        "SELECT * FROM call_list_upload_rows WHERE upload_id=? ORDER BY row_number", (upload_id,)
    ).fetchall()
    return render_template(
        "call_list/review.html", upload=upload_row, rows=rows, outcomes=list(_OUTCOME_LABELS.items())
    )


@call_list_bp.post("/<int:upload_id>/row/<int:row_id>")
@permission_required("capture_leads")
def update_row(upload_id: int, row_id: int):
    """Let staff correct an OCR misread before it's imported."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM call_list_upload_rows WHERE id=? AND upload_id=?", (row_id, upload_id)
    ).fetchone()
    if row is None:
        flash("Row not found.", "error")
        return redirect(url_for("call_list.review", upload_id=upload_id))
    if row["imported_lead_id"]:
        flash("This row was already imported and can't be edited.", "warning")
        return redirect(url_for("call_list.review", upload_id=upload_id))

    db.execute(
        """UPDATE call_list_upload_rows
           SET client_name=?, contact=?, raw_response=?, mapped_outcome=?,
               appointment_date=?, appointment_time=?
           WHERE id=?""",
        (
            request.form.get("client_name", "").strip(),
            normalize_phone(request.form.get("contact", "")),
            request.form.get("raw_response", "").strip(),
            request.form.get("mapped_outcome", "other"),
            request.form.get("appointment_date", "").strip() or None,
            request.form.get("appointment_time", "").strip() or None,
            row_id,
        ),
    )
    db.commit()
    flash("Row updated.", "success")
    return redirect(url_for("call_list.review", upload_id=upload_id))


def _import_row_to_lead(db, row) -> int | None:
    """Find-or-create a lead for one confirmed call-list row and log the
    call outcome on it. Returns the lead id, or None if the row has no
    usable phone number to key off."""
    normalised = normalise_phone(row["contact"])
    if not normalised:
        return None

    duplicate = _find_duplicate(db, row["contact"])
    label = _OUTCOME_LABELS.get(row["mapped_outcome"], "Call list entry")
    note = f"{label}: {row['raw_response']}".strip(": ")

    if duplicate:
        lead_id = duplicate["id"]
    else:
        consultant = _allocate_sales_consultant(db, None, "Outreach")
        assigned_to = consultant["id"] if consultant else None
        lead_status = "assigned" if consultant else "captured"
        cursor = db.execute(
            """INSERT INTO leads
               (full_name, phone, phone_normalized, source, original_source,
                campaign, assigned_to, lead_status, marketing_consent,
                do_not_contact, last_activity_at, lead_channel, entry_path)
               VALUES (?, ?, ?, 'Outreach', 'Outreach', 'Call List', ?, ?, 1, 0,
                       datetime('now'), 'outreach', 'standard')""",
            (row["client_name"] or "Unknown", row["contact"], normalised, assigned_to, lead_status),
        )
        lead_id = cursor.lastrowid
        if consultant:
            db.execute(
                "UPDATE users SET last_lead_assigned_at=datetime('now') WHERE id=?", (assigned_to,)
            )

    next_action, next_action_at = _automatic_next_action(
        "captured", row["mapped_outcome"] if row["mapped_outcome"] in ("no_answer", "voicemail") else ""
    )
    _add_activity(
        db, lead_id, "call", outcome=row["mapped_outcome"], notes=note,
        next_action=next_action or None,
        follow_up_at=row["appointment_date"] or next_action_at or None,
    )
    return lead_id


@call_list_bp.post("/<int:upload_id>/import")
@permission_required("capture_leads")
def import_rows(upload_id: int):
    db = get_db()
    upload_row = db.execute("SELECT * FROM call_list_uploads WHERE id=?", (upload_id,)).fetchone()
    if upload_row is None:
        flash("Upload not found.", "error")
        return redirect(url_for("call_list.index"))

    selected_ids = {int(v) for v in request.form.getlist("row_id")}
    if not selected_ids:
        flash("Select at least one row to import.", "warning")
        return redirect(url_for("call_list.review", upload_id=upload_id))

    rows = db.execute(
        "SELECT * FROM call_list_upload_rows WHERE upload_id=? AND imported_lead_id IS NULL",
        (upload_id,),
    ).fetchall()

    imported = 0
    skipped_no_phone = 0
    for row in rows:
        if row["id"] not in selected_ids:
            continue
        lead_id = _import_row_to_lead(db, row)
        if lead_id is None:
            skipped_no_phone += 1
            continue
        db.execute("UPDATE call_list_upload_rows SET imported_lead_id=? WHERE id=?", (lead_id, row["id"]))
        imported += 1

    remaining = db.execute(
        "SELECT COUNT(*) FROM call_list_upload_rows WHERE upload_id=? AND imported_lead_id IS NULL",
        (upload_id,),
    ).fetchone()[0]
    new_status = "imported" if remaining == 0 else "reviewing"
    db.execute(
        """UPDATE call_list_uploads
           SET imported_count = imported_count + ?, status=?,
               imported_at = CASE WHEN ? = 'imported' THEN datetime('now') ELSE imported_at END
           WHERE id=?""",
        (imported, new_status, new_status, upload_id),
    )
    db.commit()

    message = f"Imported {imported} row(s) into leads."
    if skipped_no_phone:
        message += f" {skipped_no_phone} row(s) had no usable phone number and were skipped."
    flash(message, "success" if imported else "warning")
    return redirect(url_for("call_list.review", upload_id=upload_id))
