import os

# Set these before importing Config: local credentials and .env must never
# participate in the merge gate.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["APP_ENV"] = "testing"
os.environ["ENCRYPTION_KEY"] = "kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8="
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["TURNSTILE_ENABLED"] = "0"
os.environ["WHATSAPP_WEB_ENABLED"] = "0"
os.environ["RATELIMIT_STORAGE_URL"] = "memory://"

import pytest
from onecpase import create_app
from onecpase.config import Config


@pytest.fixture(autouse=True)
def isolated_defaults(tmp_path, monkeypatch):
    # Also cover tests constructing their own app instead of using app().
    for key, value in {
        "DATABASE_URL": "",
        "DATABASE_PATH": str(tmp_path / "default.db"),
        "PLATFORM_DATABASE_PATH": str(tmp_path / "default-platform.db"),
        "TENANTS_DIR": str(tmp_path / "tenants"),
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
    }.items():
        monkeypatch.setattr(Config, key, value)


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
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "SECRET_KEY": "test-secret",
        "ENCRYPTION_KEY": "kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8=",
        "WTF_CSRF_ENABLED": False,
        "TURNSTILE_ENABLED": False,
        # The suite exercises every area, including ones a phased launch keeps
        # closed. Phase gating is covered on its own in test_phases.py.
        "ACTIVE_PHASE": "all",
    })

    yield app


@pytest.fixture
def client(app):
    return app.test_client()
