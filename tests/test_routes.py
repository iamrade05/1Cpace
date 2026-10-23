from onecpase import create_app


def test_root_shows_tenant_welcome_page(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200


def test_healthz_endpoint(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert "checks" in data


def test_healthz_rejects_missing_database_without_creating_it(app, client, tmp_path):
    path = tmp_path / "missing.db"
    app.config["DATABASE_PATH"] = str(path)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.get_json()["checks"]["database"]["message"] == "Database unavailable"
    assert not path.exists()
    assert str(path).encode() not in response.data


def test_healthz_rejects_corrupt_database(app, client, tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a database")
    app.config["DATABASE_PATH"] = str(path)
    assert client.get("/healthz").status_code == 503


def test_healthz_checks_postgres_connection(app, client, monkeypatch):
    from onecpase import database

    app.config["DATABASE_URL"] = "postgresql://test.invalid/test"
    def unavailable():
        raise ConnectionError("private connection details")
    monkeypatch.setattr(database, "get_db", unavailable)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert b"private connection details" not in response.data
