import pytest

from onecpase import create_app


def test_production_rejects_memory_rate_limit_storage():
    with pytest.raises(RuntimeError, match="RATELIMIT_STORAGE_URL"):
        create_app({
            "TESTING": True,
            "ENVIRONMENT": "production",
            "SECRET_KEY": "test-secret",
            "ENCRYPTION_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "RATELIMIT_STORAGE_URL": "memory://",
        })


def test_trusted_proxy_count_is_configurable():
    app = create_app({
        "TESTING": True,
        "ENVIRONMENT": "test",
        "SECRET_KEY": "test-secret",
        "RATELIMIT_STORAGE_URL": "memory://",
        "RATELIMIT_TRUSTED_PROXY_COUNT": 1,
    })
    assert app.config["RATELIMIT_TRUSTED_PROXY_COUNT"] == 1


def test_negative_trusted_proxy_count_is_rejected_in_production():
    with pytest.raises(RuntimeError, match="RATELIMIT_TRUSTED_PROXY_COUNT"):
        create_app({
            "TESTING": True,
            "ENVIRONMENT": "production",
            "SECRET_KEY": "test-secret",
            "ENCRYPTION_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "RATELIMIT_STORAGE_URL": "redis://localhost:6379/0",
            "RATELIMIT_TRUSTED_PROXY_COUNT": -1,
        })
