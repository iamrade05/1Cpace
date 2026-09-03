from onecpase.database import get_db
from onecpase.turnstile import decode_credential


def _login_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"


def test_decode_turnstile_report():
    assert decode_credential(b"CARD-123\x00\x00") == "CARD-123"
    assert decode_credential(bytes([0xFF, 0x01, 0x00])) == "FF01"


def test_turnstile_dashboard_and_credential_assignment(app):
    with app.test_client() as client:
        _login_admin(client)
        with app.app_context():
            member_id = get_db().execute(
                """INSERT INTO members
                   (first_name, last_name, id_number, member_status, gym_access_status)
                   VALUES ('USB', 'Member', 'USB-001', 'Active', 'allowed')"""
            ).lastrowid
            event_id = get_db().execute(
                """INSERT INTO turnstile_events
                   (device_serial, raw_report_hex, credential, decision, reason)
                   VALUES ('01', '434152442D313233', 'CARD-123', 'unmatched',
                           'Credential is not linked to a member')"""
            ).lastrowid
            get_db().commit()

        response = client.get("/turnstile/")
        assert response.status_code == 200
        assert b"USB Turnstile" in response.data
        assert b"CARD-123" in response.data

        response = client.post(
            f"/turnstile/events/{event_id}/assign",
            data={"member_id": str(member_id)},
            follow_redirects=False,
        )
        assert response.status_code == 302
        with app.app_context():
            credential = get_db().execute(
                "SELECT access_credential FROM members WHERE id=?", (member_id,)
            ).fetchone()[0]
        assert credential == "CARD-123"

        response = client.post(
            "/turnstile/scan", json={"credential": "CARD-123"}
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["decision"] == "granted"
        assert payload["member"]["id"] == member_id
