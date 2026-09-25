import random
import time
from functools import wraps

from flask import (Blueprint, current_app, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from flask_mail import Message
from werkzeug.security import check_password_hash, generate_password_hash

from .database import get_db
from .extensions import mail, limiter

auth_bp = Blueprint("auth", __name__, template_folder="templates/auth")

_OTP_MAX_ATTEMPTS = 5


# ── Permission helpers ────────────────────────────────────────────────────────

def _load_permissions(user_id: int) -> set:
    from .permissions import default_permissions_for

    db = get_db()
    user = db.execute(
        "SELECT role, department FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    rows = db.execute(
        "SELECT permission FROM user_permissions WHERE user_id = ?", (user_id,)
    ).fetchall()
    perms = {r["permission"] for r in rows}
    if user:
        perms |= default_permissions_for(user["role"], user["department"])
    return perms


def _complete_login(user):
    """Finalise session after credentials + OTP both pass."""
    perms = _load_permissions(user["id"])
    session.clear()
    session.update({
        "user_id":              user["id"],
        "username":             user["username"],
        "full_name":            user["full_name"],
        "role":                 user["role"],
        "is_sales_consultant":  bool(user["is_sales_consultant"]),
        "permissions":          list(perms),
        "must_change_password": bool(user["must_change_password"]),
        "tenant_slug":          g.tenant["slug"] if g.get("tenant") else None,
    })
    get_db().execute(
        "UPDATE users SET last_login = datetime('now') WHERE id = ?", (user["id"],)
    )
    get_db().commit()


def session_tenant_ok() -> bool:
    """True if there's no logged-in session, or the session's tenant matches
    the tenant resolved for this request. Guards against a stale session
    reading/writing the wrong tenant's database after the tenant cookie
    changes (e.g. switching gyms in another tab)."""
    if not session.get("user_id"):
        return True
    return session.get("tenant_slug") == g.get("tenant_slug")


def _unauthenticated_response():
    """A fetch()/JSON caller gets a 401 body it can parse, not the login
    page's HTML — otherwise `r.json()` throws on the redirect target and the
    caller never learns it just needs to sign in again."""
    if request.is_json:
        return jsonify(ok=False, error="Login required"), 401
    return redirect(url_for("auth.login"))


def _session_expired_response():
    session.clear()
    if request.is_json:
        return jsonify(ok=False, error="Your session expired. Please sign in again."), 401
    flash("Your session expired. Please sign in again.", "warning")
    return redirect(url_for("auth.login"))


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if not session.get("user_id"):
            return _unauthenticated_response()
        if not session_tenant_ok():
            return _session_expired_response()
        # Force password change before accessing anything else
        if session.get("must_change_password") and request.endpoint != "auth.change_password":
            flash("You must change your password before continuing.", "warning")
            return redirect(url_for("auth.change_password"))
        return view(**kwargs)
    return wrapped_view


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped_view(**kwargs):
            if not session.get("user_id"):
                return redirect(url_for("auth.login"))
            if not session_tenant_ok():
                session.clear()
                flash("Your session expired. Please sign in again.", "warning")
                return redirect(url_for("auth.login"))
            if session.get("must_change_password") and request.endpoint != "auth.change_password":
                flash("You must change your password before continuing.", "warning")
                return redirect(url_for("auth.change_password"))
            if session.get("role") not in roles:
                flash("You don't have permission to access that page.", "warning")
                return redirect(url_for("dashboard.dashboard"))
            return view(**kwargs)
        return wrapped_view
    return decorator


def permission_required(perm: str):
    """Fine-grained permission gate. Admins bypass all checks."""
    def decorator(view):
        @wraps(view)
        def wrapped_view(**kwargs):
            if not session.get("user_id"):
                return _unauthenticated_response()
            if not session_tenant_ok():
                return _session_expired_response()
            if session.get("must_change_password") and request.endpoint != "auth.change_password":
                flash("You must change your password before continuing.", "warning")
                return redirect(url_for("auth.change_password"))
            if session.get("role") == "admin":
                return view(**kwargs)
            if perm not in session.get("permissions", []):
                if request.is_json:
                    return jsonify(ok=False, error="You don't have permission to do that"), 403
                flash("You don't have permission to access that page.", "warning")
                return redirect(url_for("dashboard.dashboard"))
            return view(**kwargs)
        return wrapped_view
    return decorator


# ── OTP helpers ───────────────────────────────────────────────────────────────

def _generate_otp() -> str:
    return f"{random.SystemRandom().randint(0, 999999):06d}"


def _send_otp_email(to_email: str, full_name: str, code: str) -> bool:
    """Returns True on success, False if mail is not configured or fails."""
    if not current_app.config.get("MAIL_USERNAME"):
        return False
    tenant_name = g.tenant["name"] if g.get("tenant") else "1Cpase"
    try:
        msg = Message(
            subject=f"{tenant_name} — Your login code",
            recipients=[to_email],
        )
        msg.html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;
                    border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;">
          <div style="background:#185FA5;padding:24px 32px;">
            <h1 style="color:#fff;margin:0;font-size:1.5rem;font-weight:800;">{tenant_name}</h1>
            <p style="color:#EEF4FA;margin:4px 0 0;font-size:0.85rem;">Powered by 1Cpase</p>
          </div>
          <div style="padding:32px;">
            <p style="color:#374151;font-size:0.95rem;">Hi <strong>{full_name}</strong>,</p>
            <p style="color:#6b7280;font-size:0.875rem;margin-top:8px;">
              Use the code below to complete your sign-in. It expires in
              <strong>{current_app.config['OTP_TTL'] // 60} minutes</strong>.
            </p>
            <div style="text-align:center;margin:28px 0;">
              <span style="display:inline-block;background:#EEF4FA;color:#185FA5;
                           font-size:2.2rem;font-weight:900;letter-spacing:14px;
                           padding:16px 28px;border-radius:10px;border:2px solid #185FA5;">
                {code}
              </span>
            </div>
            <p style="color:#9ca3af;font-size:0.78rem;text-align:center;">
              If you didn't try to sign in, ignore this email — your account is safe.
            </p>
          </div>
        </div>
        """
        mail.send(msg)
        return True
    except Exception as exc:
        current_app.logger.error("OTP email failed: %s", exc)
        return False


# ── Routes ────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if g.tenant is None:
        flash("Please choose your gym to continue.", "info")
        return redirect(url_for("tenants.welcome"))

    if session.get("user_id"):
        if session.get("tenant_slug") == g.tenant["slug"]:
            return redirect(url_for("dashboard.dashboard"))
        session.clear()  # stale session from a different tenant

    if request.method == "POST":
        username = str(request.form.get("username", "")).strip().lower()
        password = str(request.form.get("password", ""))
        db = get_db()
        user = db.execute(
            """SELECT * FROM users
               WHERE username = ? AND active = 1
                 AND COALESCE(is_platform_user, 0) = 0""",
            (username,),
        ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            otp_enabled = current_app.config.get("OTP_ENABLED", True)

            if otp_enabled and user["email"]:
                code = _generate_otp()
                session["_otp"] = {
                    "code":     code,
                    "user_id":  user["id"],
                    "expires":  time.time() + current_app.config["OTP_TTL"],
                    "email":    user["email"],
                    "attempts": 0,
                }
                sent = _send_otp_email(user["email"], user["full_name"], code)
                if sent:
                    flash(f"A 6-digit code was sent to {user['email'][:3]}***{user['email'].split('@')[1]}. "
                          "Enter it below.", "info")
                else:
                    _complete_login(user)
                    return redirect(url_for("dashboard.dashboard"))
                return redirect(url_for("auth.verify_otp"))
            else:
                _complete_login(user)
                return redirect(url_for("dashboard.dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("auth/login.html", tenant=g.tenant)


@auth_bp.route("/login/verify", methods=["GET", "POST"])
@limiter.limit("15 per minute")
def verify_otp():
    otp_data = session.get("_otp")
    if not otp_data:
        return redirect(url_for("auth.login"))

    if time.time() > otp_data["expires"]:
        session.pop("_otp", None)
        flash("Your code expired. Please sign in again.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        entered = "".join(str(request.form.get("otp", "")).split())

        if time.time() > otp_data["expires"]:
            session.pop("_otp", None)
            flash("Code expired. Please sign in again.", "error")
            return redirect(url_for("auth.login"))

        # Track failed attempts and lock out after max
        otp_data["attempts"] = otp_data.get("attempts", 0) + 1
        session["_otp"] = otp_data

        if otp_data["attempts"] > _OTP_MAX_ATTEMPTS:
            session.pop("_otp", None)
            flash("Too many failed attempts. Please sign in again.", "error")
            return redirect(url_for("auth.login"))

        if entered == otp_data["code"]:
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE id = ?", (otp_data["user_id"],)).fetchone()
            session.pop("_otp", None)
            if user:
                _complete_login(user)
                return redirect(url_for("dashboard.dashboard"))
        else:
            remaining_attempts = _OTP_MAX_ATTEMPTS - otp_data["attempts"]
            flash(f"Incorrect code. {remaining_attempts} attempt(s) remaining.", "error")

    remaining = max(0, int(otp_data["expires"] - time.time()))
    return render_template("auth/otp.html", remaining=remaining,
                           masked_email=otp_data.get("email", ""))


@auth_bp.post("/login/resend")
@limiter.limit("3 per minute")
def resend_otp():
    otp_data = session.get("_otp")
    if not otp_data:
        return redirect(url_for("auth.login"))
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (otp_data["user_id"],)).fetchone()
    if user and user["email"]:
        code = _generate_otp()
        session["_otp"] = {
            "code":     code,
            "user_id":  user["id"],
            "expires":  time.time() + current_app.config["OTP_TTL"],
            "email":    user["email"],
            "attempts": 0,
        }
        _send_otp_email(user["email"], user["full_name"], code)
        flash("A new code has been sent.", "success")
    return redirect(url_for("auth.verify_otp"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
def change_password():
    if not session.get("user_id"):
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        current_pw  = request.form.get("current_password", "")
        new_pw      = request.form.get("new_password", "").strip()
        confirm_pw  = request.form.get("confirm_password", "").strip()

        db   = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()

        if not check_password_hash(user["password_hash"], current_pw):
            flash("Current password is incorrect.", "error")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new_pw != confirm_pw:
            flash("Passwords do not match.", "error")
        elif new_pw == current_pw:
            flash("New password must differ from the current password.", "error")
        else:
            db.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                (generate_password_hash(new_pw), session["user_id"]),
            )
            db.commit()
            session["must_change_password"] = False
            flash("Password changed successfully.", "success")
            return redirect(url_for("dashboard.dashboard"))

    return render_template("auth/change_password.html")


@auth_bp.get("/logout")
def logout():
    if session.get("is_power_user") and session.get("platform_admin_id"):
        platform_admin_id = session["platform_admin_id"]
        platform_username = session.get("platform_admin_username", "")
        session.clear()
        session["platform_admin_id"] = platform_admin_id
        session["platform_admin_username"] = platform_username
        flash("You returned to the 1Cpase platform.", "info")
        response = redirect(url_for("platform.tenants_index"))
        from .tenants import TENANT_COOKIE_NAME
        response.delete_cookie(TENANT_COOKIE_NAME)
        return response
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
