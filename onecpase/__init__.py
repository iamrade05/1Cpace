import logging
from logging.handlers import RotatingFileHandler

from flask import Flask, abort, flash, g, jsonify, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from . import phases
from .permissions import MODULE_PERMISSIONS
from .config import Config
from .local_time import sast
from .database import close_db, init_db
from .extensions import cache, mail, csrf, limiter
from .platform_db import get_platform_db, close_platform_db, init_platform_db
from .auth import auth_bp
from .dashboard import dashboard_bp
from .health import health_bp
from .members import members_bp
from .collections import collections_bp
from .hr import hr_bp
from .reports import reports_bp
from .fitness import fitness_bp
from .onboarding import onboarding_bp, add_onboarding_columns
from .fitness_journey import journey_bp
from .operations import operations_bp
from .leads import leads_bp
from .public_leads import join_bp
from .applications import applications_bp
from .contracts import contracts_bp
from .admin import admin_bp
from .debicheck_routes import debicheck_bp
from .reconciliation import reconciliation_bp
from .ptp import ptp_bp
from .access_report import access_report_bp
from .call_list import call_list_bp
from .pbx import pbx_bp
from .comms import comms_bp, webhooks_bp
from .internal_comms import internal_comms_bp, unread_counts
from .events import events_bp, admin_events_bp
from .whatsapp_web import whatsapp_web_bp
from .queries import queries_bp
from .sales_pipeline import sales_pipeline_bp
from .turnstile_routes import turnstile_bp
from .tenants import tenants_bp, TENANT_COOKIE_NAME
from .platform_admin import platform_bp
from .screen_recordings import screen_recordings_bp
from .first_month_collections import first_month_bp
from .notifications import notifications_bp
from .training import training_bp
from .mobile_api import mobile_bp


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(Config)
    app.add_template_filter(sast)
    if test_config:
        app.config.update(test_config)

    # Trust proxy headers only when explicitly configured. This is required
    # for correct client-IP handling behind nginx/load balancers, while the
    # default remains fail-closed for deployments that have not configured a
    # trusted proxy.
    proxy_count = int(app.config.get("RATELIMIT_TRUSTED_PROXY_COUNT", 0) or 0)
    if proxy_count > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_count, x_proto=proxy_count, x_host=proxy_count)

    # Secure browser/session defaults in production. These are Flask's own
    # cookie controls and do not change the existing application session model.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    if app.config.get("ENVIRONMENT") == "production":
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = app.config.get("SESSION_COOKIE_SAMESITE") or "Lax"

    if app.config.get("INSPECT_MODE"):
        from .inspect_mode import enable_inspect_mode
        enable_inspect_mode(app)

    # ── Structured logging ────────────────────────────────────────────────────
    if not app.debug and not app.testing and not any(isinstance(item, RotatingFileHandler) for item in app.logger.handlers):
        handler = RotatingFileHandler("1cpase.log", maxBytes=1_000_000, backupCount=5)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)

    # Security-critical secrets fail closed in production. Development/test
    # environments may deliberately use their own configured test secrets.
    if app.config.get("ENVIRONMENT") == "production":
        if not app.config.get("SECRET_KEY") or app.config.get("SECRET_KEY") == "change-me":
            raise RuntimeError("SECRET_KEY must be explicitly configured in production.")
        if not app.config.get("ENCRYPTION_KEY"):
            raise RuntimeError("ENCRYPTION_KEY must be explicitly configured in production.")
        storage_url = str(app.config.get("RATELIMIT_STORAGE_URL", ""))
        if not storage_url or storage_url.startswith("memory://"):
            raise RuntimeError("RATELIMIT_STORAGE_URL must use shared production storage; memory:// is not permitted in production.")
        if int(app.config.get("RATELIMIT_TRUSTED_PROXY_COUNT", 0) or 0) < 0:
            raise RuntimeError("RATELIMIT_TRUSTED_PROXY_COUNT cannot be negative.")
        if not app.config.get("OTP_ENABLED", True):
            raise RuntimeError("OTP_ENABLED cannot be turned off in production: the emailed code is what proves who is signing in.")
        if not app.config.get("PBX_WEBHOOK_SECRET"):
            raise RuntimeError("PBX_WEBHOOK_SECRET must be explicitly configured in production.")
        meta_enabled = any(app.config.get(key) for key in (
            "WA_TOKEN", "FB_PAGE_ACCESS_TOKEN", "WA_RECEPTION_PHONE_ID", "WA_SALES_PHONE_ID", "FB_PAGE_ID",
        ))
        if meta_enabled and not str(app.config.get("FB_APP_SECRET") or "").strip():
            raise RuntimeError("FB_APP_SECRET must be explicitly configured in production when a Meta integration is configured.")

    # Validate an explicitly configured encryption key early. Do not silently
    # downgrade encryption because a malformed key was supplied.
    if app.config.get("ENCRYPTION_KEY"):
        from .encryption import _get_fernet
        with app.app_context():
            _get_fernet()

    # ── Initialize extensions ─────────────────────────────────────────────────
    cache.init_app(app)
    mail.init_app(app)
    csrf.init_app(app)
    # Keep runtime/test overrides of the deployment's URL wired to the extension.
    app.config["RATELIMIT_STORAGE_URI"] = app.config["RATELIMIT_STORAGE_URL"]
    limiter.init_app(app)

    # ── Register blueprints ───────────────────────────────────────────────────
    app.register_blueprint(auth_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(mobile_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(members_bp)
    app.register_blueprint(collections_bp)
    app.register_blueprint(hr_bp)
    from .hr_workforce_routes import workforce_bp
    app.register_blueprint(workforce_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(fitness_bp)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(journey_bp)
    app.register_blueprint(operations_bp)
    from .reception_admin import reception_admin_bp
    app.register_blueprint(reception_admin_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(join_bp)
    app.register_blueprint(applications_bp)
    app.register_blueprint(contracts_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(debicheck_bp)
    app.register_blueprint(ptp_bp)
    app.register_blueprint(reconciliation_bp)
    app.register_blueprint(access_report_bp)
    app.register_blueprint(call_list_bp)
    app.register_blueprint(pbx_bp)
    app.register_blueprint(comms_bp)
    app.register_blueprint(internal_comms_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(admin_events_bp)
    app.register_blueprint(whatsapp_web_bp)
    app.register_blueprint(webhooks_bp)
    from . import query_forms  # Register the shared signed-query routes.
    app.register_blueprint(queries_bp)
    app.register_blueprint(sales_pipeline_bp)
    app.register_blueprint(turnstile_bp)
    app.register_blueprint(tenants_bp)
    app.register_blueprint(platform_bp)
    app.register_blueprint(screen_recordings_bp)
    app.register_blueprint(first_month_bp)

    # ── Database ──────────────────────────────────────────────────────────────
    # Startup opens connections too. Register cleanup before that context
    # exits, otherwise those handles can outlive initialization on Windows.
    app.teardown_appcontext(close_db)
    app.teardown_appcontext(close_platform_db)
    with app.app_context():
        init_db()
        add_onboarding_columns()
        init_platform_db()

    from .turnstile import start_turnstile_monitor
    start_turnstile_monitor(app)

    from .scheduler import init_scheduler
    init_scheduler(app)

    from .whatsapp_web import init_whatsapp_web
    init_whatsapp_web(app)

    # ── Tenant resolution ─────────────────────────────────────────────────────
    @app.before_request
    def _resolve_tenant():
        """Resolve which gym this request belongs to from the remembered
        cookie, so get_db() (database.py) opens the right tenant's SQLite
        file. Left as None for static assets and the platform-operator area,
        which isn't scoped to any one tenant."""
        g.tenant = None
        g.tenant_slug = None
        if request.endpoint in (None, "static") or request.blueprint in {"platform", "mobile"}:
            return
        if app.config.get("DATABASE_URL"):
            return  # legacy single-tenant Postgres mode — tenant system inert

        if request.endpoint == 'queries.member_form':
            tenant = get_platform_db().execute('SELECT * FROM tenants WHERE slug=? AND active=1',
                (request.view_args['tenant_slug'],)).fetchone()
            if not tenant:
                abort(404)
            g.tenant, g.tenant_slug = dict(tenant), tenant['slug']
            return

        slug = request.cookies.get(TENANT_COOKIE_NAME)
        if not slug:
            return

        # Tenant deactivation must revoke access on the next request.
        row = get_platform_db().execute(
            "SELECT * FROM tenants WHERE slug = ? AND active = 1", (slug,)
        ).fetchone()
        tenant = dict(row) if row else None
        if tenant:
            g.tenant = tenant
            g.tenant_slug = tenant["slug"]

    @app.before_request
    def _enforce_staff_access():
        if request.endpoint in {'queries.member_form', 'contracts.public_share'}:
            return None  # The expiring link binds the tenant; browser staff cookies do not.
        if request.blueprint == 'mobile':
            return None  # Native API uses tenant-bound bearer identity, never browser cookies.
        from .auth import refresh_staff_session

        if not session.get('user_id') or request.blueprint in {'platform', 'tenants', 'health', 'comms_webhooks', 'join'} or request.endpoint in {None, 'static'}:
            return None
        if request.blueprint == 'auth' and request.endpoint != 'auth.change_password':
            return None
        denied = refresh_staff_session()
        if denied is not None:
            return denied
        profile = session['access_profile']
        if not profile or request.blueprint in {'dashboard', 'auth'}:
            return None
        common = {'dashboard', 'members', 'queries', 'turnstile', 'hr', 'workforce', 'contracts', 'screen_recordings', 'notifications', 'training'}
        allowed = common | ({'fitness', 'onboarding', 'fitness_journey'} if profile == 'fitness' else {'leads', 'sales_pipeline', 'applications', 'call_list', 'first_month_collections', 'comms', 'internal_comms', 'pbx', 'debicheck'})
        if profile == 'reception':
            allowed |= {'collections', 'ptp', 'access_report', 'fitness_journey', 'reception_admin'}
        if request.blueprint not in allowed:
            return jsonify(ok=False, error="You don't have permission to access that area."), 403

    # ── Phased go-live ────────────────────────────────────────────────────────
    @app.before_request
    def _enforce_active_phase():
        """Refuse areas whose phase has not opened yet.

        The nav hides them, but a hidden link is not a closed door — staff
        bookmark URLs, and a half-finished area answering a typed URL is how a
        phased launch leaks.
        """
        blueprint = request.blueprint
        active = app.config.get("ACTIVE_PHASE", 1)
        if phases.is_enabled(blueprint, active):
            return None

        number = phases.phase_of(blueprint)
        area = phases.phase_name(number) if number else (blueprint or "That area")
        if request.method == "GET" and request.accept_mimetypes.accept_html:
            flash(
                f"{area} is not open yet — it arrives in phase {number}.",
                "info",
            )
            return redirect(url_for("dashboard.dashboard"))
        return jsonify(ok=False, error=f"{area} is not open yet."), 403

    # ── Template context processors ───────────────────────────────────────────
    @app.context_processor
    def inject_phases():
        active = app.config.get("ACTIVE_PHASE", 1)

        def phase_open(name: str) -> bool:
            """True for an open nav section or an open blueprint."""
            if name in phases.NAV_SECTIONS:
                return phases.nav_section_enabled(name, active)
            return phases.is_enabled(name, active)

        return {"phase_open": phase_open, "active_phase": phases.normalise(active)}

    @app.context_processor
    def inject_internal_unread():
        """Unread counts for the Communication nav badge.

        Only for a signed-in request, and skipped entirely if the tables are
        not there yet — a context processor that raises takes down every page,
        including the ones that would tell you why.
        """
        if not session.get("user_id"):
            return {"internal_unread": {"messages": 0, "announcements": 0, "total": 0}}
        try:
            from .database import get_db
            return {"internal_unread": unread_counts(get_db(), session["user_id"])}
        except Exception:  # pragma: no cover - defensive only
            app.logger.warning("internal unread counts unavailable", exc_info=True)
            return {"internal_unread": {"messages": 0, "announcements": 0, "total": 0}}

    @app.context_processor
    def inject_notification_count():
        from .auth import session_tenant_ok
        if not session.get("user_id") or not session_tenant_ok():
            return {"staff_notification_count": 0}
        from .database import get_db
        from .notifications import pending_count
        return {"staff_notification_count": pending_count(get_db(), session["user_id"])}

    @app.context_processor
    def inject_permissions():
        role = session.get("role", "")
        perms = set(session.get("permissions", []))

        def has_perm(perm: str) -> bool:
            from .hr_access import OPEN_HR_PERMISSIONS, can_access_payroll
            if perm == 'hr_payroll':
                return can_access_payroll()
            if perm in OPEN_HR_PERMISSIONS:
                return bool(session.get('user_id'))
            return role == "admin" or perm in perms

        from .hr_workforce import is_admin as hr_is_admin, granted as hr_granted
        from .database import get_db
        hr_admin = bool(session.get('user_id')) and request.blueprint != 'platform' and hr_is_admin(get_db())
        hr_calendar = hr_admin or (bool(session.get('user_id')) and request.blueprint != 'platform' and
                                  (hr_granted(get_db(),'hr_leave_approve') or hr_granted(get_db(),'hr_timesheet_approve')))
        from .reception_admin import access as reception_access
        reception_allowed, reception_manager = reception_access(get_db())
        return {"has_perm": has_perm, "module_permissions": MODULE_PERMISSIONS,
                'reception_admin_allowed': reception_allowed, 'reception_admin_manager': reception_manager,
                'hr_can_manage': hr_admin, 'hr_can_calendar': hr_calendar}

    @app.context_processor
    def inject_dashboard_access():
        from .dashboard_access import current_scope,home_endpoint
        from .auth import session_tenant_ok
        if not session.get('user_id') or not session_tenant_ok() or request.blueprint=='platform':
            return {'dashboard_scope':'work','department_home':'dashboard.home'}
        return {'dashboard_scope':current_scope(),'department_home':home_endpoint()}

    @app.context_processor
    def inject_followups():
        """Expose the follow-up due count for the floating badge on every page."""
        # Platform pages are deliberately outside every tenant database, even
        # while a Power User also has an active tenant session.
        if request.blueprint == "platform" or not session.get("user_id"):
            return {"followup_due_count": 0, "followup_scope": "mine"}
        try:
            from .database import get_db
            from .queries import followup_due_count
            count, scope = followup_due_count(get_db())
        except Exception:
            app.logger.exception("Failed to compute follow-up badge count")
            count, scope = 0, "mine"
        return {"followup_due_count": count, "followup_scope": scope}

    @app.after_request
    def _security_headers(response):
        if app.config.get("SECURITY_HSTS_ENABLED") and app.config.get("ENVIRONMENT") == "production":
            hsts = f"max-age={app.config.get('SECURITY_HSTS_MAX_AGE', 31536000)}"
            if app.config.get("SECURITY_HSTS_INCLUDE_SUBDOMAINS", True):
                hsts += "; includeSubDomains"
            response.headers.setdefault("Strict-Transport-Security", hsts)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if app.config.get("SECURITY_CSP_ENABLED") and app.config.get("ENVIRONMENT") == "production":
            header = "Content-Security-Policy-Report-Only" if app.config.get("SECURITY_CSP_REPORT_ONLY") else "Content-Security-Policy"
            response.headers.setdefault(header, "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        return response

    return app
