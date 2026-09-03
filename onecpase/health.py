from datetime import datetime, timezone
from flask import Blueprint, jsonify, current_app

health_bp = Blueprint("health", __name__)


@health_bp.get("/healthz")
def healthz():
    checks = {}
    status = "ok"

    try:
        db = current_app.config["DATABASE_PATH"]
        with open(db, "r"):
            pass
        checks["database"] = {"ok": True, "message": "ok"}
    except FileNotFoundError as exc:
        checks["database"] = {"ok": False, "message": str(exc)}
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
