"""Cross-tenant isolation invariants.

Each gym's operational data lives in its own SQLite file; platform.db only
records which tenants exist. These tests pin the boundary: a request must act
on the tenant it is bound to, and nothing else.
"""
import sqlite3
from pathlib import Path

from onecpase.database import _init_db_on_connection, get_db
from onecpase.platform_db import get_platform_db


PBX_SECRET = "pbx-test-secret"


def _provision_tenant(app, slug="othergym", name="Other Gym") -> str:
    """Register a second tenant and build its database, as the platform
    admin screen does when a new gym is added."""
    with app.app_context():
        db_path = Path(app.config["TENANTS_DIR"]) / f"{slug}.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        platform_db = get_platform_db()
        platform_db.execute(
            "INSERT INTO tenants (slug, name, db_path, active) VALUES (?, ?, ?, 1)",
            (slug, name, str(db_path)),
        )
        platform_db.commit()

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            _init_db_on_connection(conn, "sqlite", name)
        finally:
            conn.close()
    return str(db_path)


def _call_payload(call_id):
    return {
        "type": "cdr",
        "msg": {
            "call_id": call_id,
            "call_from": "1000",
            "call_to": "0821112222",
            "call_type": "outbound",
            "disposition": "ANSWERED",
        },
    }


def _post_webhook(client, path, call_id):
    return client.post(
        path,
        json=_call_payload(call_id),
        headers={"X-1Cpace-PBX-Secret": PBX_SECRET},
    )


def _call_count(db_path, call_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM pbx_call_logs WHERE external_call_id = ?", (call_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_new_tenant_database_has_the_pbx_tables(app):
    """PBX tables are additive migrations, so a tenant provisioned later must
    still get them — otherwise calling is broken for every gym but the first."""
    other_db = _provision_tenant(app)
    conn = sqlite3.connect(other_db)
    try:
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pbx%'"
            )
        }
    finally:
        conn.close()
    assert {"pbx_call_logs", "pbx_webhook_events"} <= names


def test_pbx_webhook_writes_to_the_tenant_named_in_its_url(app, client):
    app.config["PBX_WEBHOOK_SECRET"] = PBX_SECRET
    other_db = _provision_tenant(app)

    assert _post_webhook(client, "/pbx/webhook/othergym", "call-tenant-b").status_code == 200

    assert _call_count(other_db, "call-tenant-b") == 1
    # The primary tenant must not see another gym's call activity.
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM pbx_call_logs WHERE external_call_id = ?",
            ("call-tenant-b",),
        ).fetchone()[0] == 0


def test_pbx_webhook_for_an_unknown_tenant_is_rejected(app, client):
    app.config["PBX_WEBHOOK_SECRET"] = PBX_SECRET

    response = _post_webhook(client, "/pbx/webhook/no-such-gym", "call-nowhere")

    assert response.status_code == 404
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM pbx_call_logs WHERE external_call_id = ?",
            ("call-nowhere",),
        ).fetchone()[0] == 0


def test_pbx_webhook_still_requires_the_shared_secret(app, client):
    app.config["PBX_WEBHOOK_SECRET"] = PBX_SECRET
    _provision_tenant(app)

    response = client.post(
        "/pbx/webhook/othergym",
        json=_call_payload("call-unauthorised"),
        headers={"X-1Cpace-PBX-Secret": "wrong"},
    )

    assert response.status_code == 401


def test_legacy_webhook_path_still_serves_the_primary_tenant(app, client):
    """The existing Yeastar install posts to the bare path; it must keep
    working and keep landing in DATABASE_PATH."""
    app.config["PBX_WEBHOOK_SECRET"] = PBX_SECRET
    other_db = _provision_tenant(app)

    assert _post_webhook(client, "/pbx/webhook", "call-primary").status_code == 200

    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM pbx_call_logs WHERE external_call_id = ?",
            ("call-primary",),
        ).fetchone()[0] == 1
    assert _call_count(other_db, "call-primary") == 0


# ── Turnstile binding ─────────────────────────────────────────────────────────

def test_turnstile_monitor_follows_the_configured_tenant(app):
    """The turnstile is wired to one gym, so its events must land in that
    gym's database rather than whatever DATABASE_PATH happens to be."""
    from onecpase.turnstile import _monitor_database_path

    other_db = _provision_tenant(app)
    app.config["TURNSTILE_TENANT_SLUG"] = "othergym"

    resolved = _monitor_database_path(app)

    assert Path(resolved) == Path(other_db).resolve()
    assert Path(resolved) != Path(app.config["DATABASE_PATH"]).resolve()


def test_turnstile_falls_back_to_database_path_when_unbound(app):
    from onecpase.turnstile import _monitor_database_path

    app.config["TURNSTILE_TENANT_SLUG"] = ""

    assert Path(_monitor_database_path(app)) == Path(app.config["DATABASE_PATH"]).resolve()


def test_turnstile_falls_back_when_the_slug_is_not_a_tenant(app):
    from onecpase.turnstile import _monitor_database_path

    app.config["TURNSTILE_TENANT_SLUG"] = "no-such-gym"

    assert Path(_monitor_database_path(app)) == Path(app.config["DATABASE_PATH"]).resolve()


# ── Itensity scripts guard ────────────────────────────────────────────────────

def test_itensity_scripts_guard_tracks_whether_the_package_is_installed(monkeypatch):
    """scripts/ is an external package. When it is missing the Itensity jobs
    cannot run, and registering them just floods the log with ImportErrors —
    so the scheduler asks this guard first."""
    import importlib.util

    from onecpase import scheduler

    # It is installed in this checkout, so the guard must say so.
    assert scheduler.itensity_scripts_available() is True

    # And it must report absence rather than letting the jobs register.
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert scheduler.itensity_scripts_available() is False
