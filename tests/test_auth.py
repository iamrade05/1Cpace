def test_root_shows_tenant_welcome_page(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert b"Select your gym" in response.data or b"gym" in response.data.lower()


def test_login_without_tenant_redirects_to_welcome(client):
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_login_page_after_selecting_tenant(client):
    # The default install auto-seeds an "elev8" tenant on first boot.
    client.post("/select-tenant", data={"tenant_slug": "elev8"}, follow_redirects=False)
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Sign In" in response.data
