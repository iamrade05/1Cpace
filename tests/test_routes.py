from onecpase import create_app


def test_root_shows_tenant_welcome_page(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200


def test_healthz_endpoint(client):
    response = client.get("/healthz")
    # When database exists, returns 200
    assert response.status_code in (200, 503)
    data = response.get_json()
    assert data["status"] in {"ok", "degraded"}
    assert "checks" in data
