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
import shutil
import sqlite3
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


def _test_config(tmp_path):
    return {
        "TESTING": True,
        "DATABASE_URL": "",
        "DATABASE_PATH": str(tmp_path / "test.db"),
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
        # Real browser work is faked in tests; the off switch has its own test.
        "ITENSITY_PUSH_ALLOWED": "all",
    }


@pytest.fixture(scope='session')
def database_baseline(tmp_path_factory):
    """Migrate once; every ordinary test still gets independent database files."""
    folder=tmp_path_factory.mktemp('schema-baseline')
    config=_test_config(folder)
    create_app(config)  # Real startup/migrations, with no operational data.
    for key in ('DATABASE_PATH','PLATFORM_DATABASE_PATH'):
        with sqlite3.connect(config[key]) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    return config


@pytest.fixture
def app(tmp_path, database_baseline, monkeypatch):
    config=_test_config(tmp_path)
    for key in ('DATABASE_PATH','PLATFORM_DATABASE_PATH'):
        shutil.copy2(database_baseline[key],config[key])
    with sqlite3.connect(config['PLATFORM_DATABASE_PATH']) as db:
        db.execute('UPDATE tenants SET db_path=? WHERE slug=?',(config['DATABASE_PATH'],'elev8'))
    # Only the cloned fixture's initial factory call skips repeated DDL. Tests
    # calling create_app themselves (including migration/startup tests) retain
    # the real initialization functions, restored before this fixture yields.
    with monkeypatch.context() as factory_patch:
        factory_patch.setattr('onecpase.init_db',lambda:None)
        factory_patch.setattr('onecpase.add_onboarding_columns',lambda:None)
        factory_patch.setattr('onecpase.init_platform_db',lambda:None)
        app=create_app(config)

    yield app


@pytest.fixture
def client(app):
    return app.test_client()
