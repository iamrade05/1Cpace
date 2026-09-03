"""Public tenant picker: the welcome page and the cookie that remembers
which gym a browser last chose. No login required for any route here."""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from .platform_db import get_platform_db

tenants_bp = Blueprint("tenants", __name__)

TENANT_COOKIE_NAME = "onecpase_tenant"
TENANT_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


@tenants_bp.route("/")
def welcome():
    if session.get("user_id") and session.get("tenant_slug"):
        return redirect(url_for("dashboard.dashboard"))

    tenants = get_platform_db().execute(
        "SELECT slug, name, logo_filename FROM tenants WHERE active = 1 ORDER BY name"
    ).fetchall()
    return render_template("welcome.html", tenants=tenants)


@tenants_bp.post("/select-tenant")
def select_tenant():
    slug = str(request.form.get("tenant_slug", "")).strip()
    tenant = get_platform_db().execute(
        "SELECT slug FROM tenants WHERE slug = ? AND active = 1", (slug,)
    ).fetchone()
    if not tenant:
        flash("Please choose a gym from the list.", "error")
        return redirect(url_for("tenants.welcome"))

    resp = redirect(url_for("auth.login"))
    resp.set_cookie(
        TENANT_COOKIE_NAME, slug,
        max_age=TENANT_COOKIE_MAX_AGE, httponly=True, samesite="Lax",
    )
    return resp


@tenants_bp.get("/switch-tenant")
def switch_tenant():
    session.clear()
    resp = redirect(url_for("tenants.welcome"))
    resp.delete_cookie(TENANT_COOKIE_NAME)
    return resp
