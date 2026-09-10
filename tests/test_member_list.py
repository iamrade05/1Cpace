from werkzeug.security import generate_password_hash

from onecpase.database import get_db


def _login_admin(client):
    with client.session_transaction() as session:
        session.update({
            "user_id": 1,
            "role": "admin",
            "full_name": "Test Administrator",
        })


def test_members_index_renders_member_without_join_date(app, client):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users (id, username, password_hash, full_name) VALUES (?, ?, ?, ?)",
            (1, "admin", generate_password_hash("unused"), "Test Administrator"),
        )
        db.execute(
            """INSERT INTO members
               (member_ref, first_name, last_name, id_number, id_number_hash, join_date)
               VALUES ('MEM-TEST-001', 'No', 'Join Date', '9001015009087', 'member-test-hash', NULL)"""
        )
        db.commit()

    _login_admin(client)
    response = client.get("/members")

    assert response.status_code == 200
    assert b"No Join Date" in response.data


def test_conversion_marks_sales_lead_joined_after_member_is_created(app, client):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users (id, username, password_hash, full_name) VALUES (?, ?, ?, ?)",
            (1, "admin", generate_password_hash("unused"), "Test Administrator"),
        )
        lead_id = db.execute(
            """INSERT INTO leads
               (full_name, phone, source, original_source, lead_status, sales_stage)
               VALUES ('Conversion Prospect', '0800000000', 'Walk-In / Enquiry',
                       'Walk-In / Enquiry', 'show', 'SHOW')"""
        ).lastrowid
        db.execute(
            "INSERT INTO lead_activities (lead_id, activity_type, outcome) VALUES (?, 'visit', 'joined')",
            (lead_id,),
        )
        db.commit()

    _login_admin(client)
    response = client.post(
        f"/leads/{lead_id}/convert",
        data={"package": "Premium 12", "id_number": "9001015009087"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        lead = get_db().execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        assert lead["lead_status"] == "joined"
        assert lead["converted_member_id"] is not None
