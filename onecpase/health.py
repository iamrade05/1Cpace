from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from flask import Blueprint, jsonify, current_app

health_bp = Blueprint("health", __name__)


@health_bp.get("/healthz")
def healthz():
    checks = {}
    status = "ok"

    try:
        if current_app.config.get("DATABASE_URL"):
            from .database import get_db
            get_db().execute("SELECT COUNT(*) FROM users").fetchone()
        else:
            # Read-only mode must not create a missing database and report it
            # healthy. Reading a required table also detects invalid files or
            # an installation whose schema was never initialized.
            uri = Path(current_app.config["DATABASE_PATH"]).resolve().as_uri() + "?mode=ro"
            db = sqlite3.connect(uri, uri=True, timeout=2)
            try:
                db.execute("SELECT COUNT(*) FROM users").fetchone()
            finally:
                db.close()
        checks["database"] = {"ok": True, "message": "ok"}
    except Exception:
        current_app.logger.exception("Database readiness check failed")
        checks["database"] = {"ok": False, "message": "Database unavailable"}
        status = "degraded"

    return (
        jsonify(
            {
                "status": status,
                "checks": checks,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ),
        200 if status == "ok" else 503,
    )
