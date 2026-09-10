"""Admin-only browser screen recording storage and review."""
from __future__ import annotations

import time
import uuid
from functools import wraps
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file, session
from werkzeug.utils import secure_filename

from .auth import login_required
from .database import audit_log, get_db


screen_recordings_bp = Blueprint("screen_recordings", __name__, url_prefix="/screen-recordings")

_ALLOWED_MIME_TYPES = {"video/webm", "video/mp4"}


def _admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _recordings_dir() -> Path:
    directory = Path(current_app.config["UPLOAD_FOLDER"]) / "screen_recordings"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _duration_seconds(raw_value: str | None) -> float | None:
    try:
        value = float(raw_value or "")
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 86_400 else None


@screen_recordings_bp.get("/")
@login_required
@_admin_required
def index():
    recordings = get_db().execute(
        """SELECT r.*, u.full_name AS created_by_name
           FROM screen_recordings r
           JOIN users u ON u.id = r.created_by
           ORDER BY r.created_at DESC, r.id DESC
           LIMIT 100"""
    ).fetchall()
    return render_template("screen_recordings/index.html", recordings=recordings)


@screen_recordings_bp.post("/upload")
@login_required
@_admin_required
def upload():
    recording = request.files.get("recording")
    if recording is None or not recording.filename:
        return jsonify(error="Choose a recording first."), 400
    if recording.mimetype not in _ALLOWED_MIME_TYPES:
        return jsonify(error="Only WebM and MP4 recordings are supported."), 400

    title = request.form.get("title", "").strip()[:120]
    original_filename = secure_filename(recording.filename) or "screen-recording.webm"
    extension = Path(original_filename).suffix.lower()
    if extension not in {".webm", ".mp4"}:
        extension = ".webm" if recording.mimetype == "video/webm" else ".mp4"
    stored_filename = f"{int(time.time())}_{uuid.uuid4().hex}{extension}"
    path = _recordings_dir() / stored_filename

    try:
        recording.save(path)
        file_size = path.stat().st_size
        if file_size <= 0:
            path.unlink(missing_ok=True)
            return jsonify(error="The recording was empty."), 400

        db = get_db()
        cursor = db.execute(
            """INSERT INTO screen_recordings
               (title, original_filename, stored_filename, mime_type, file_size,
                duration_seconds, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                title or "Screen recording",
                original_filename,
                stored_filename,
                recording.mimetype,
                file_size,
                _duration_seconds(request.form.get("duration_seconds")),
                session["user_id"],
            ),
        )
        audit_log(
            db,
            "screen_recording_created",
            new_value=title or original_filename,
            user_id=session["user_id"],
        )
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return jsonify(message="Recording saved.", recording_id=cursor.lastrowid), 201


@screen_recordings_bp.get("/<int:recording_id>/download")
@login_required
@_admin_required
def download(recording_id: int):
    recording = get_db().execute(
        "SELECT * FROM screen_recordings WHERE id=?", (recording_id,)
    ).fetchone()
    if recording is None:
        abort(404)

    path = _recordings_dir() / recording["stored_filename"]
    if not path.exists() or path.name != recording["stored_filename"]:
        abort(404)
    return send_file(path, as_attachment=True, download_name=recording["original_filename"])
