"""
Platform-operator area: add/manage gyms (tenants) on this 1Cpase install.

A platform admin is a different principal from a gym's own role="admin"
user — gym admins only exist inside that one gym's isolated database and
can't see or manage other tenants. Platform admins live in platform.db and
use their own session key (session["platform_admin_id"]), never
session["user_id"], so the two never collide.
"""
import re
import secrets
import sqlite3
from functools import wraps
from pathlib import Path

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from .database import _init_db_on_connection
from .extensions import limiter
from .platform_db import get_platform_db
from .tenants import TENANT_COOKIE_MAX_AGE, TENANT_COOKIE_NAME

platform_bp = Blueprint("platform", __name__, url_prefix="/platform")

_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "svg", "webp"}
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _platform_login_required(view):
    @wraps(view)
    def wrapped(**kwargs):
        if not session.get("platform_admin_id"):
            return redirect(url_for("platform.login"))
        return view(**kwargs)
    return wrapped


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def _allowed_logo(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in _LOGO_EXTENSIONS


def _audit(action: str, tenant_id: int | None = None) -> None:
    """Record security-relevant power-user activity in the platform DB."""
    admin_id = session.get("platform_admin_id")
    if not admin_id:
        return
    db = get_platform_db()
    db.execute(
        """INSERT INTO platform_audit_log
           (platform_admin_id, tenant_id, action, ip_address, user_agent)
           VALUES (?, ?, ?, ?, ?)""",
        (
            admin_id,
            tenant_id,
            action,
            (request.remote_addr or "")[:64],
            str(request.user_agent)[:255],
        ),
    )
    db.commit()


def _leave_tenant_session() -> None:
    """Remove tenant authority while keeping the platform principal signed in."""
    admin_id = session.get("platform_admin_id")
    username = session.get("platform_admin_username", "")
    session.clear()
    if admin_id:
        session["platform_admin_id"] = admin_id
        session["platform_admin_username"] = username


def _power_user_for_tenant(tenant, admin):
    """Return an internal tenant-local user used only for platform entry.

    A real local user row keeps existing created_by/updated_by foreign keys
    valid.  It is excluded from tenant password login and tenant user screens.
    """
    conn = sqlite3.connect(tenant["db_path"], detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    try:
        _init_db_on_connection(conn, "sqlite")
        user = conn.execute(
            """SELECT * FROM users
               WHERE is_platform_user = 1 AND platform_admin_id = ?""",
            (admin["id"],),
        ).fetchone()
        password_hash = generate_password_hash(secrets.token_urlsafe(48))
        full_name = f"1Cpase Power User ({admin['username']})"
        if user:
            conn.execute(
                """UPDATE users
                   SET password_hash = ?, full_name = ?, role = 'admin', active = 1,
                       must_change_password = 0, is_platform_user = 1
                   WHERE id = ?""",
                (password_hash, full_name, user["id"]),
            )
            user_id = user["id"]
            username = user["username"]
        else:
            # The random suffix avoids taking over any pre-existing username.
            username = f"__1cpase_power_{admin['id']}_{secrets.token_hex(6)}"
            cursor = conn.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, department, active,
                    must_change_password, is_platform_user, platform_admin_id)
                   VALUES (?, ?, ?, 'admin', '1Cpase Platform', 1, 0, 1, ?)""",
                (username, password_hash, full_name, admin["id"]),
            )
            user_id = cursor.lastrowid
        conn.execute(
            "UPDATE users SET last_login = datetime('now') WHERE id = ?", (user_id,)
        )
        conn.commit()
        return {
            "id": user_id,
            "username": username,
            "full_name": full_name,
        }
    finally:
        conn.close()


# ── Auth ─────────────────────────────────────────────────────────────────────

@platform_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if session.get("platform_admin_id"):
        return redirect(url_for("platform.tenants_index"))

    if request.method == "POST":
        username = str(request.form.get("username", "")).strip().lower()
        password = str(request.form.get("password", ""))
        admin = get_platform_db().execute(
            "SELECT * FROM platform_admins WHERE username = ?", (username,)
        ).fetchone()
        if admin and check_password_hash(admin["password_hash"], password):
            session.clear()
            session["platform_admin_id"] = admin["id"]
            session["platform_admin_username"] = admin["username"]
            _audit("platform_login")
            return redirect(url_for("platform.tenants_index"))
        flash("Invalid username or password.", "error")

    return render_template("platform/login.html")


@platform_bp.get("/logout")
def logout():
    _audit("platform_logout")
    session.clear()
    response = redirect(url_for("platform.login"))
    response.delete_cookie(TENANT_COOKIE_NAME)
    return response


# ── Tenants ──────────────────────────────────────────────────────────────────

@platform_bp.route("/")
@_platform_login_required
def index():
    return redirect(url_for("platform.tenants_index"))

@platform_bp.route("/tenants")
@_platform_login_required
def tenants_index():
    tenants = get_platform_db().execute(
        "SELECT * FROM tenants ORDER BY name"
    ).fetchall()
    return render_template("platform/tenants.html", tenants=tenants)


@platform_bp.post("/tenants/<int:tenant_id>/enter")
@_platform_login_required
@limiter.limit("30 per minute")
def tenant_enter(tenant_id):
    if current_app.config.get("DATABASE_URL"):
        flash("Tenant switching is unavailable in single-database mode.", "error")
        return redirect(url_for("platform.tenants_index"))

    db = get_platform_db()
    tenant = db.execute(
        "SELECT * FROM tenants WHERE id = ? AND active = 1", (tenant_id,)
    ).fetchone()
    admin = db.execute(
        "SELECT * FROM platform_admins WHERE id = ?",
        (session["platform_admin_id"],),
    ).fetchone()
    if not tenant:
        flash("That gym is not active or no longer exists.", "error")
        return redirect(url_for("platform.tenants_index"))
    if not admin:
        session.clear()
        flash("Your power-user session is no longer valid.", "error")
        return redirect(url_for("platform.login"))

    try:
        local_user = _power_user_for_tenant(tenant, admin)
    except Exception:
        current_app.logger.exception(
            "Power user %s could not enter tenant %s", admin["id"], tenant["slug"]
        )
        flash("The gym could not be opened. Please check its database.", "error")
        return redirect(url_for("platform.tenants_index"))

    # Audit before rebuilding the session because _audit uses its platform ID.
    _audit("tenant_enter", tenant["id"])
    session.clear()
    session.update({
        "platform_admin_id":       admin["id"],
        "platform_admin_username": admin["username"],
        "is_power_user":           True,
        "power_user_tenant_slug":  tenant["slug"],
        "power_user_tenant_name":  tenant["name"],
        "user_id":                 local_user["id"],
        "username":                local_user["username"],
        "full_name":               local_user["full_name"],
        "role":                    "admin",
        "permissions":             [],
        "must_change_password":    False,
        "tenant_slug":             tenant["slug"],
    })
    response = redirect(url_for("dashboard.dashboard"))
    response.set_cookie(
        TENANT_COOKIE_NAME,
        tenant["slug"],
        max_age=TENANT_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )
    return response


@platform_bp.post("/tenant/exit")
@_platform_login_required
def tenant_exit():
    tenant = get_platform_db().execute(
        "SELECT id FROM tenants WHERE slug = ?",
        (session.get("power_user_tenant_slug", ""),),
    ).fetchone()
    if session.get("is_power_user"):
        _audit("tenant_exit", tenant["id"] if tenant else None)
    _leave_tenant_session()
    flash("You returned to the 1Cpase platform.", "info")
    response = redirect(url_for("platform.tenants_index"))
    response.delete_cookie(TENANT_COOKIE_NAME)
    return response


@platform_bp.route("/tenants/add", methods=["GET", "POST"])
@_platform_login_required
@limiter.limit("5 per hour")
def tenants_add():
    if request.method == "POST":
        name = str(request.form.get("name", "")).strip()
        slug = _slugify(request.form.get("slug", "") or name)
        admin_username = str(request.form.get("admin_username", "")).strip().lower()
        admin_full_name = str(request.form.get("admin_full_name", "")).strip()
        admin_email = str(request.form.get("admin_email", "")).strip()
        admin_password = request.form.get("admin_password", "")
        logo = request.files.get("logo")

        errors = []
        if not name:
            errors.append("Gym name is required.")
        if not slug or not _SLUG_RE.match(slug):
            errors.append("Slug must be lowercase letters, numbers and hyphens only.")
        elif get_platform_db().execute(
            "SELECT id FROM tenants WHERE slug = ?", (slug,)
        ).fetchone():
            errors.append(f"Slug '{slug}' is already in use.")
        if not admin_username or not admin_full_name:
            errors.append("Initial admin username and full name are required.")
        if len(admin_password) < 8:
            errors.append("Initial admin password must be at least 8 characters.")
        if logo and logo.filename and not _allowed_logo(logo.filename):
            errors.append("Logo must be a PNG, JPG, SVG or WEBP file.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("platform/tenant_form.html", tenant=None,
                                   form=request.form)

        db_path = str(Path(current_app.config["TENANTS_DIR"]) / f"{slug}.db")
        Path(current_app.config["TENANTS_DIR"]).mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        try:
            _init_db_on_connection(conn, "sqlite")
            conn.execute(
                """INSERT INTO users (username, password_hash, full_name, role, email,
                   must_change_password) VALUES (?, ?, ?, 'admin', ?, 1)""",
                (admin_username, generate_password_hash(admin_password),
                 admin_full_name, admin_email),
            )
            conn.commit()
        finally:
            conn.close()

        logo_filename = None
        if logo and logo.filename:
            ext = secure_filename(logo.filename).rsplit(".", 1)[1].lower()
            logo_filename = f"{slug}.{ext}"
            dest_dir = Path(current_app.static_folder) / "tenant_logos"
            dest_dir.mkdir(parents=True, exist_ok=True)
            logo.save(str(dest_dir / logo_filename))

        get_platform_db().execute(
            """INSERT INTO tenants (slug, name, logo_filename, db_path, active)
               VALUES (?, ?, ?, ?, 1)""",
            (slug, name, logo_filename, db_path),
        )
        get_platform_db().commit()

        flash(f"{name} is ready — share the login link and initial admin credentials.", "success")
        return redirect(url_for("platform.tenants_index"))

    return render_template("platform/tenant_form.html", tenant=None, form={})


@platform_bp.route("/tenants/<int:tenant_id>/edit", methods=["GET", "POST"])
@_platform_login_required
def tenants_edit(tenant_id):
    db = get_platform_db()
    tenant = db.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    if not tenant:
        flash("Gym not found.", "error")
        return redirect(url_for("platform.tenants_index"))

    if request.method == "POST":
        name = str(request.form.get("name", "")).strip()
        logo = request.files.get("logo")

        if not name:
            flash("Gym name is required.", "error")
            return render_template("platform/tenant_form.html", tenant=tenant, form=request.form)

        logo_filename = tenant["logo_filename"]
        if logo and logo.filename:
            if not _allowed_logo(logo.filename):
                flash("Logo must be a PNG, JPG, SVG or WEBP file.", "error")
                return render_template("platform/tenant_form.html", tenant=tenant, form=request.form)
            ext = secure_filename(logo.filename).rsplit(".", 1)[1].lower()
            logo_filename = f"{tenant['slug']}.{ext}"
            dest_dir = Path(current_app.static_folder) / "tenant_logos"
            dest_dir.mkdir(parents=True, exist_ok=True)
            logo.save(str(dest_dir / logo_filename))

        db.execute(
            "UPDATE tenants SET name = ?, logo_filename = ? WHERE id = ?",
            (name, logo_filename, tenant_id),
        )
        db.commit()
        flash("Gym updated.", "success")
        return redirect(url_for("platform.tenants_index"))

    return render_template("platform/tenant_form.html", tenant=tenant, form=tenant)


@platform_bp.post("/tenants/<int:tenant_id>/toggle")
@_platform_login_required
def tenants_toggle(tenant_id):
    db = get_platform_db()
    db.execute("UPDATE tenants SET active = 1 - active WHERE id = ?", (tenant_id,))
    db.commit()
    return redirect(url_for("platform.tenants_index"))
