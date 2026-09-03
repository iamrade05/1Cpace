import pytest
from onecpase import create_app


@pytest.fixture
def app(tmp_path):
    db_path = tmp_path / "test.db"
    app = create_app({
        "TESTING": True,
        "DATABASE_PATH": str(db_path),
        # Keep the tenant registry isolated per test too — otherwise every
        # test run would share (and pollute) the real project-root platform.db.
        "PLATFORM_DATABASE_PATH": str(tmp_path / "platform.db"),
        "TENANTS_DIR": str(tmp_path / "tenants"),
        "SECRET_KEY": "test-secret",
        "ENCRYPTION_KEY": "kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8=",
        "WTF_CSRF_ENABLED": False,
        "TURNSTILE_ENABLED": False,
    })

    yield app


@pytest.fixture
def client(app):
    return app.test_client()
