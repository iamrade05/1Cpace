"""The side menu's wording. The heading of the group holding Payment Records,
Collections Call Queue, PTP / Collection, Legal Referrals and the Exception
Queue is "Collections" - it once read "Connections"."""
from onecpase.database import get_db


def _login_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, active) "
            "VALUES (1, 'admin', 'x', 'Test Admin', 'admin', 1)"
        )
        db.commit()


def test_the_collections_menu_group_is_headed_collections(client):
    _login_admin(client)
    response = client.get("/admin/whatsapp-web/")  # any signed-in page carries the side menu
    html = response.get_data(as_text=True)
    assert 'data-section="connections"' in html, "the menu group should be on the page for an admin"
    assert "<span>Collections</span>" in html
    assert "<span>Connections</span>" not in html
