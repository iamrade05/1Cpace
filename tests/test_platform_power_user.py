import sqlite3

from werkzeug.security import generate_password_hash

from onecpase.platform_db import get_platform_db


def _seed_power_user(app, username="owner", password="StrongPass123!"):
    with app.app_context():
        db = get_platform_db()
        db.execute(
            "INSERT INTO platform_admins (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        tenant_id = db.execute(
            "SELECT id FROM tenants WHERE slug = 'elev8'"
        ).fetchone()["id"]
        db.commit()
    return tenant_id


def _platform_login(client, username="owner", password="StrongPass123!"):
    return client.post(
        "/platform/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_power_user_can_open_tenant_and_return_to_platform(app, client):
    tenant_id = _seed_power_user(app)

    response = _platform_login(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/platform/tenants")

    response = client.post(
        f"/platform/tenants/{tenant_id}/enter", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert "onecpase_tenant=elev8" in response.headers.get("Set-Cookie", "")

    with client.session_transaction() as sess:
        assert sess["platform_admin_username"] == "owner"
        assert sess["is_power_user"] is True
        assert sess["tenant_slug"] == "elev8"
        local_user_id = sess["user_id"]

    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        local_user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (local_user_id,)
        ).fetchone()
        assert local_user["role"] == "admin"
        assert local_user["is_platform_user"] == 1
        assert local_user["platform_admin_id"] is not None
    finally:
        conn.close()

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"Power User mode" in response.data
    assert b"Return to platform" in response.data

    response = client.post("/platform/tenant/exit", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/platform/tenants")
    with client.session_transaction() as sess:
        assert sess["platform_admin_username"] == "owner"
        assert "user_id" not in sess
        assert "is_power_user" not in sess

    with app.app_context():
        actions = [
            row["action"]
            for row in get_platform_db().execute(
                "SELECT action FROM platform_audit_log ORDER BY id"
            ).fetchall()
        ]
    assert actions == ["platform_login", "tenant_enter", "tenant_exit"]


def test_inactive_tenant_cannot_be_opened(app, client):
    tenant_id = _seed_power_user(app)
    with app.app_context():
        db = get_platform_db()
        db.execute("UPDATE tenants SET active = 0 WHERE id = ?", (tenant_id,))
        db.commit()

    _platform_login(client)
    response = client.post(
        f"/platform/tenants/{tenant_id}/enter", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/platform/tenants")
    with client.session_transaction() as sess:
        assert "user_id" not in sess
        assert "is_power_user" not in sess


def test_tenant_user_cannot_access_platform_tenant_list(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["tenant_slug"] = "elev8"
        sess["role"] = "admin"

    response = client.get("/platform/tenants", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/platform/login")


def test_internal_power_identity_cannot_use_tenant_password_login(app, client):
    tenant_id = _seed_power_user(app)
    _platform_login(client)
    client.post(f"/platform/tenants/{tenant_id}/enter")

    with client.session_transaction() as sess:
        internal_username = sess["username"]

    client.post("/platform/tenant/exit")
    client.post("/select-tenant", data={"tenant_slug": "elev8"})
    response = client.post(
        "/login",
        data={"username": internal_username, "password": "anything"},
        follow_redirects=True,
    )
    assert b"Invalid username or password" in response.data
