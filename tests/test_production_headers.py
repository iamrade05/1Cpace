import pytest

from onecpase import create_app


def production_config(**overrides):
    return {
        "TESTING": True,
        "ENVIRONMENT": "production",
        "SECRET_KEY": "isolated-production-test-secret-32-characters",
        "RATELIMIT_STORAGE_URL": "redis://127.0.0.1:6379/0",
        "RATELIMIT_ENABLED": False,
        "PBX_WEBHOOK_SECRET": "isolated-test-webhook-secret",
        "FB_APP_SECRET": "",
        "WA_TOKEN": "",
        "FB_PAGE_ACCESS_TOKEN": "",
        "WA_RECEPTION_PHONE_ID": "",
        "WA_SALES_PHONE_ID": "",
        "FB_PAGE_ID": "",
        "SECURITY_HSTS_ENABLED": True,
        "SECURITY_HSTS_MAX_AGE": 300,
        "SECURITY_HSTS_INCLUDE_SUBDOMAINS": False,
        "SECURITY_CSP_ENABLED": True,
        "SECURITY_CSP_REPORT_ONLY": True,
        **overrides,
    }


def test_production_cookie_and_staged_headers():
    app = create_app(production_config())
    assert app.config["RATELIMIT_STORAGE_URI"] == app.config["RATELIMIT_STORAGE_URL"]
    response = app.test_client().get("/platform/login", base_url="https://example.test")
    assert response.status_code == 200
    cookie = response.headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert response.headers["Strict-Transport-Security"] == "max-age=300"
    assert "Content-Security-Policy-Report-Only" in response.headers
    assert "Content-Security-Policy" not in response.headers


@pytest.mark.parametrize("key", ["WA_TOKEN", "FB_PAGE_ACCESS_TOKEN", "WA_RECEPTION_PHONE_ID", "WA_SALES_PHONE_ID", "FB_PAGE_ID"])
def test_configured_meta_requires_secret(key):
    with pytest.raises(RuntimeError, match="FB_APP_SECRET"):
        create_app(production_config(**{key: "configured-provider-value"}))


def test_disabled_meta_still_rejects_unsigned_webhooks():
    app = create_app(production_config())
    client = app.test_client()
    assert client.post("/webhooks/whatsapp", json={}).status_code == 401
    assert client.post("/webhooks/facebook", json={}).status_code == 401


def test_csp_can_be_enforced_after_trial():
    app = create_app(production_config(SECURITY_CSP_REPORT_ONLY=False, SECURITY_HSTS_INCLUDE_SUBDOMAINS=True))
    response = app.test_client().get("/healthz")
    assert "Content-Security-Policy" in response.headers
    assert "Content-Security-Policy-Report-Only" not in response.headers
    assert response.headers["Strict-Transport-Security"] == "max-age=300; includeSubDomains"


def test_production_url_selects_shared_storage():
    from limits.storage import RedisStorage
    from onecpase.extensions import limiter
    create_app(production_config(RATELIMIT_ENABLED=True, RATELIMIT_STORAGE_URI="memory://"))
    assert isinstance(limiter.storage, RedisStorage)
