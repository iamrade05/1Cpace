import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")


class Config:
    # Production must provide an explicit strong secret; see create_app().
    SECRET_KEY       = os.environ.get("SECRET_KEY", "change-me")
    ENVIRONMENT      = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "development")).lower()
    # SQLite (dev default) or PostgreSQL (set DATABASE_URL=postgresql://user:pass@host/db)
    DATABASE_URL     = os.environ.get("DATABASE_URL", "")
    DATABASE_PATH    = os.environ.get("DATABASE_PATH", str(BASE_DIR.parent / "1cpase.db"))
    # Multi-tenant registry (separate from any tenant's own data) and the
    # folder new tenant databases are provisioned into.
    PLATFORM_DATABASE_PATH = os.environ.get("PLATFORM_DATABASE_PATH", str(BASE_DIR.parent / "platform.db"))
    TENANTS_DIR      = os.environ.get("TENANTS_DIR", str(BASE_DIR.parent / "tenants"))
    # Field-level encryption key. Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ENCRYPTION_KEY   = os.environ.get("ENCRYPTION_KEY", "")
    # Phased go-live: only the areas belonging to this phase and the ones
    # before it are reachable — see phases.py. "all" opens everything.
    ACTIVE_PHASE     = os.environ.get("ACTIVE_PHASE", "1")
    # /access-report reads a hardcoded external SQL Server that only mirrors
    # this one tenant's data. Restrict the route to that tenant's slug so
    # other tenants get a 404 instead of an error (or a leak) — see
    # access_report.py.
    ACCESS_REPORT_TENANT_SLUG = os.environ.get("ACCESS_REPORT_TENANT_SLUG", "elev8")
    UPLOAD_FOLDER        = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR.parent / "uploads"))
    # Only needed if Tesseract isn't on PATH — call_list_parser.py falls back
    # to the default Windows install location otherwise.
    TESSERACT_CMD        = os.environ.get("TESSERACT_CMD", "")
    MAX_CONTENT_LENGTH   = 15 * 1024 * 1024  # 15 MB max upload
    REQUIRE_REDIS    = os.environ.get("REQUIRE_REDIS", "0")
    REQUIRE_CELERY   = os.environ.get("REQUIRE_CELERY", "0")
    CACHE_TYPE       = "SimpleCache"
    CACHE_DEFAULT_TIMEOUT = 300

    # Itensity member API. Keep these empty to disable the integration.
    ITENSITY_API_URL = os.environ.get("ITENSITY_API_URL", "")
    ITENSITY_API_KEY = os.environ.get("ITENSITY_API_KEY", "")
    ITENSITY_MEMBER_PATH = os.environ.get("ITENSITY_MEMBER_PATH", "/members")
    ITENSITY_TIMEOUT = float(os.environ.get("ITENSITY_TIMEOUT", "10"))

    # NuPay DebiCheck — merchant.nupay.co.za, driven natively by
    # onecpase/nupay_driver.py (Playwright + TOTP 2FA). NuPay has no
    # supported public API.
    NUPAY_MERCHANT_ID = os.environ.get("NUPAY_MERCHANT_ID", "000025500014054")
    NUPAY_NEW_EMAIL = os.environ.get("NUPAY_NEW_EMAIL", "")
    NUPAY_NEW_PASSWORD = os.environ.get("NUPAY_NEW_PASSWORD", "")
    NUPAY_TOTP_SECRET = os.environ.get("NUPAY_TOTP_SECRET", "")
    NUPAY_NEW_ACCESS_ID = os.environ.get("NUPAY_NEW_ACCESS_ID", "000025500014054")
    NUPAY_NEW_ACCESS_TYPE = os.environ.get("NUPAY_NEW_ACCESS_TYPE", "M")
    NUPAY_NEW_HEADED = os.environ.get("NUPAY_NEW_HEADED", "0")
    NUPAY_NEW_AUTH_REQUIRED = os.environ.get("NUPAY_NEW_AUTH_REQUIRED", "0227")
    # Itensity member push still uses the sibling app's Playwright driver.
    RADEPULSE_PATH = os.environ.get("RADEPULSE_PATH") or str(BASE_DIR.parent.parent / "radepulse")

    # ── Background scheduler (onecpase/scheduler.py) ────────────────────────
    # In-process APScheduler — no broker/worker to run alongside a single-
    # server, SQLite-per-tenant deployment. Runs PTP automations and the
    # Itensity/collections import nightly instead of waiting for a staff click.
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "1") == "1"
    # Itensity credentials (RADEPULSE_PATH .env) are one shared login, not
    # per-tenant yet — this is the one tenant the scheduled Itensity jobs run
    # against. Defaults to the same tenant access_report.py already restricts
    # its Itensity-mirroring route to.
    ITENSITY_TENANT_SLUG = os.environ.get("ITENSITY_TENANT_SLUG", ACCESS_REPORT_TENANT_SLUG)

    # WhatsApp Business (Meta Graph API) + Facebook Messenger/comments.
    # Leave blank to keep Communications structurally present but inert —
    # webhooks accept nothing without a matching verify token, and sends
    # fail with a clear "not configured" message.
    WA_TOKEN = os.environ.get("WA_TOKEN", "")
    WA_VERIFY_TOKEN = os.environ.get("WA_VERIFY_TOKEN", "elev8_wa_verify")
    WA_RECEPTION_PHONE_ID = os.environ.get("WA_RECEPTION_PHONE_ID", "")
    WA_SALES_PHONE_ID = os.environ.get("WA_SALES_PHONE_ID", "")
    WA_RECEPTION_DISPLAY = os.environ.get("WA_RECEPTION_DISPLAY", "")
    WA_SALES_DISPLAY = os.environ.get("WA_SALES_DISPLAY", "")
    FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "")
    FB_VERIFY_TOKEN = os.environ.get("FB_VERIFY_TOKEN", "elev8_fb_verify")
    FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "")
    FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "")

    # ── WhatsApp Web sender (onecpase/whatsapp_web.py) ──────────────────────
    # Alternative to the Meta Cloud API above, for when WA_TOKEN isn't set up:
    # drives a real WhatsApp Web session in a background browser instead.
    # Off by default - a real phone number has to be deliberately linked via
    # the QR code before this does anything, and it carries real risk to
    # whatever number gets linked (WhatsApp can rate-limit/ban numbers it
    # detects behaving like a bot) that the Cloud API doesn't have.
    WHATSAPP_WEB_ENABLED = os.environ.get("WHATSAPP_WEB_ENABLED", "0") == "1"
    # Deliberately NOT under onecpase/ - a deploy replaces that package
    # wholesale, which would silently log the linked phone back out.
    WHATSAPP_WEB_SESSION_DIR = os.environ.get(
        "WHATSAPP_WEB_SESSION_DIR", str(BASE_DIR.parent / "whatsapp_web_session")
    )
    # Minimum gap between two outgoing messages, seconds. Sending a burst of
    # messages back-to-back is exactly the pattern WhatsApp's abuse detection
    # looks for.
    WHATSAPP_WEB_SEND_DELAY = float(os.environ.get("WHATSAPP_WEB_SEND_DELAY", "8"))

    # ── Yeastar PBX / CRM calling ───────────────────────────────────────────
    PBX_BASE_URL = os.environ.get("PBX_BASE_URL", "https://pbx.yeastarycm.co.za").rstrip("/")
    PBX_API_VERSION = os.environ.get("PBX_API_VERSION", "openapi/v1.0").strip("/")
    PBX_CLIENT_ID = os.environ.get("PBX_CLIENT_ID", "")
    PBX_CLIENT_SECRET = os.environ.get("PBX_CLIENT_SECRET", "")
    PBX_DEFAULT_EXTENSION = os.environ.get("PBX_DEFAULT_EXTENSION", "")
    PBX_DIAL_PERMISSION = os.environ.get("PBX_DIAL_PERMISSION", "")
    PBX_WEBHOOK_SECRET = os.environ.get("PBX_WEBHOOK_SECRET", "")
    PBX_TIMEOUT = float(os.environ.get("PBX_TIMEOUT", "10"))

    # ── Email / OTP ───────────────────────────────────────────────────────────
    MAIL_SERVER         = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT           = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS        = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME       = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD       = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "1Cpace <noreply@1cpase.local>")
    OTP_TTL             = int(os.environ.get("OTP_TTL", 300))
    OTP_ENABLED         = os.environ.get("OTP_ENABLED", "1") == "1"

    # USB HID turnstile. Output remains disabled until the vendor command
    # protocol has been captured and explicitly approved.
    TURNSTILE_ENABLED = os.environ.get("TURNSTILE_ENABLED", "0") == "1"
    TURNSTILE_VENDOR_ID = int(os.environ.get("TURNSTILE_VENDOR_ID", "4825"), 16)
    TURNSTILE_PRODUCT_ID = int(os.environ.get("TURNSTILE_PRODUCT_ID", "8701"), 16)
    TURNSTILE_OUTPUT_ENABLED = os.environ.get("TURNSTILE_OUTPUT_ENABLED", "0") == "1"
    # The turnstile is USB hardware wired to one gym, so its events belong to
    # one tenant. Blank keeps the legacy behaviour of writing to DATABASE_PATH.
    TURNSTILE_TENANT_SLUG = os.environ.get("TURNSTILE_TENANT_SLUG", "")

    # ── CSRF ─────────────────────────────────────────────────────────────────
    WTF_CSRF_ENABLED    = True
    WTF_CSRF_TIME_LIMIT = 3600  # token valid for 1 hour

    # ── Rate limiting ─────────────────────────────────────────────────────────
    RATELIMIT_STORAGE_URL = os.environ.get("RATELIMIT_STORAGE_URL", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    # Production must use shared limiter storage (for example Redis) rather
    # than process-local memory. Set the number of trusted reverse proxies
    # explicitly so get_remote_address sees the real client IP.
    RATELIMIT_TRUSTED_PROXY_COUNT = int(os.environ.get("RATELIMIT_TRUSTED_PROXY_COUNT", "0"))

    # Browser/session security. HTTPS-only behaviour is enabled automatically
    # in production; local development may override these explicitly.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1" if ENVIRONMENT == "production" else "0") == "1"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    SECURITY_HSTS_ENABLED = os.environ.get("SECURITY_HSTS_ENABLED", "1" if ENVIRONMENT == "production" else "0") == "1"
    SECURITY_HSTS_MAX_AGE = int(os.environ.get("SECURITY_HSTS_MAX_AGE", "31536000"))
    SECURITY_CSP_ENABLED = os.environ.get("SECURITY_CSP_ENABLED", "1" if ENVIRONMENT == "production" else "0") == "1"

    # ── Inspect mode (dev tool) ────────────────────────────────────────────────
    # Click any part of a rendered page to see which template/block and which
    # Python view function produced it. Off by default; opt in locally with
    # INSPECT_MODE=1. Never enable this against real member data in production.
    INSPECT_MODE = os.environ.get("INSPECT_MODE", "0") == "1"
