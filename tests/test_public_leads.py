from werkzeug.security import generate_password_hash

from onecpase.database import get_db


def _add_sales_consultants():
    db = get_db()
    for order, name in enumerate(("Sales One", "Sales Two"), 1):
        db.execute(
            """INSERT INTO users
               (username, password_hash, full_name, role, department, active,
                is_sales_consultant, sales_available, sales_rotation_order)
               VALUES (?, ?, ?, 'staff', 'Sales', 1, 1, 1, ?)""",
            (f"sales{order}", generate_password_hash("unused"), name, order),
        )
    db.commit()


def _submit(client, channel: str, phone: str, name: str):
    return client.post(
        f"/join/elev8/{channel}",
        data={
            "full_name": name,
            "phone": phone,
            "email": f"{name.replace(' ', '.').lower()}@example.com",
            "training_experience": "some",
            "training_preference": "personal_trainer",
            "joining_preference": "alone",
            "contact_consent": "1",
        },
    )


def test_public_join_links_capture_source_questions_and_rotate(app):
    with app.app_context(), app.test_client() as client:
        _add_sales_consultants()

        first = _submit(client, "facebook", "0710000001", "Facebook Lead")
        second = _submit(client, "website", "0710000002", "Website Lead")

        assert first.status_code == 200
        assert second.status_code == 200
        rows = get_db().execute(
            """SELECT l.full_name, l.original_source, l.campaign,
                      l.training_experience, l.training_preference,
                      l.joining_preference, u.full_name AS consultant
               FROM leads l JOIN users u ON u.id=l.assigned_to
               ORDER BY l.id"""
        ).fetchall()
        assert [row["consultant"] for row in rows] == ["Sales One", "Sales Two"]
        assert rows[0]["original_source"] == "Social Media"
        assert rows[0]["campaign"] == "Facebook"
        assert rows[1]["original_source"] == "Website"
        assert rows[0]["training_experience"] == "some"
        assert rows[0]["training_preference"] == "personal_trainer"
        assert rows[0]["joining_preference"] == "alone"


def test_repeat_public_enquiry_keeps_original_lead(app):
    with app.app_context(), app.test_client() as client:
        _add_sales_consultants()
        _submit(client, "instagram", "0710000003", "Original Lead")
        response = _submit(client, "tiktok", "071 000 0003", "Changed Name")

        assert response.status_code == 200
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
        lead = db.execute("SELECT * FROM leads").fetchone()
        assert lead["full_name"] == "Original Lead"
        assert lead["campaign"] == "Instagram"
        note = db.execute(
            "SELECT notes FROM lead_activities WHERE lead_id=? AND activity_type='note'",
            (lead["id"],),
        ).fetchone()
        assert "Repeat online enquiry" in note["notes"]
