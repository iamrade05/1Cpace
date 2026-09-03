from datetime import date, datetime, timedelta

from onecpase.database import get_db


def _login_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"
    with client.application.app_context():
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO users
               (id, username, password_hash, full_name, role, department, active)
               VALUES (1, 'admin', 'not-used-in-tests', 'Test Sales Admin',
                       'admin', 'Sales', 1)"""
        )
        db.execute(
            """UPDATE users SET department='Sales', is_sales_consultant=1,
               sales_available=1, sales_rotation_order=1 WHERE id=1"""
        )
        db.commit()


def _create_prospect(client, phone="082 123 4567", name="CRM Prospect"):
    return client.post(
        "/leads/add",
        data={
            "full_name": name,
            "phone": phone,
            "email": "prospect@example.com",
            "source": "Referral",
            "referrer": "Existing Member",
            "assigned_to": "1",
            "marketing_consent": "1",
        },
        follow_redirects=False,
    )


def test_prospect_capture_preserves_attribution_and_blocks_duplicate_phone(app):
    with app.test_client() as client:
        _login_admin(client)
        response = _create_prospect(client)
        assert response.status_code == 302

        prospect = get_db().execute(
            "SELECT * FROM leads WHERE full_name=?", ("CRM Prospect",)
        ).fetchone()
        assert prospect["phone_normalized"] == "27821234567"
        assert prospect["original_source"] == "Referral"
        assert prospect["referrer"] == "Existing Member"
        assert prospect["campaign"] == ""
        assert prospect["lead_status"] == "assigned"
        assert prospect["next_action"] == "Initial contact call"
        assert prospect["next_action_at"] is not None

        response = _create_prospect(client, phone="+27 82 123 4567", name="Duplicate")
        assert response.status_code == 200
        assert b"already belongs to prospect" in response.data
        assert get_db().execute(
            "SELECT COUNT(*) FROM leads WHERE phone_normalized=?", ("27821234567",)
        ).fetchone()[0] == 1


def test_no_show_keeps_prospect_active_and_creates_follow_up(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client)
        prospect = get_db().execute(
            "SELECT id FROM leads WHERE full_name='CRM Prospect'"
        ).fetchone()

        response = client.post(
            f"/leads/{prospect['id']}/activities",
            data={
                "activity_type": "appointment",
                "outcome": "no_show",
                "activity_status": "no_show",
                "reason": "Client did not arrive",
                "notes": "Reception confirmed no attendance",
            },
        )
        assert response.status_code == 302
        row = get_db().execute("SELECT * FROM leads WHERE id=?", (prospect["id"],)).fetchone()
        assert row["lead_status"] == "recall_queue"
        assert row["next_action"] == "Follow up after no-show"
        follow_up = get_db().execute(
            """SELECT * FROM lead_activities
               WHERE lead_id=? AND activity_type='follow_up' AND status='scheduled'""",
            (prospect["id"],),
        ).fetchone()
        assert follow_up is not None


def test_do_not_contact_blocks_marketing_activity(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client)
        prospect = get_db().execute(
            "SELECT id FROM leads WHERE full_name='CRM Prospect'"
        ).fetchone()
        get_db().execute(
            "UPDATE leads SET do_not_contact=1, marketing_consent=0, lead_status='do_not_contact' WHERE id=?",
            (prospect["id"],),
        )
        get_db().commit()

        response = client.post(
            f"/leads/{prospect['id']}/activities",
            data={"activity_type": "call", "outcome": "successful_contact"},
            follow_redirects=True,
        )
        assert b"Marketing activity is blocked" in response.data


def test_decision_period_expires_to_recycle(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client)
        prospect = get_db().execute(
            "SELECT id FROM leads WHERE full_name='CRM Prospect'"
        ).fetchone()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        get_db().execute(
            "UPDATE leads SET lead_status='hot_prospect_7day', decision_date=? WHERE id=?",
            (yesterday, prospect["id"]),
        )
        get_db().commit()

        response = client.get("/leads/")
        assert response.status_code == 200
        row = get_db().execute("SELECT * FROM leads WHERE id=?", (prospect["id"],)).fetchone()
        assert row["lead_status"] == "recycle"
        assert row["recycle_reason"] == "seven_day_no_conversion"
        assert row["closure_reason"] == "Seven-day decision period expired"


def test_recycled_prospect_returns_to_recall_queue_automatically(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client)
        db = get_db()
        lead = db.execute("SELECT id FROM leads WHERE full_name='CRM Prospect'").fetchone()
        db.execute(
            """UPDATE leads SET lead_status='recycle',
                   recycle_reason='not_interested_call',
                   recycle_due_at=datetime('now', '-1 minute'),
                   next_action=NULL, next_action_at=NULL WHERE id=?""",
            (lead["id"],),
        )
        db.commit()

        response = client.get("/leads/")
        assert response.status_code == 200
        row = get_db().execute("SELECT * FROM leads WHERE id=?", (lead["id"],)).fetchone()
        assert row["lead_status"] == "recall_queue"
        assert row["next_action"] == "Recall recycled prospect"
        assert row["recycle_due_at"] is None


def test_do_not_contact_never_returns_to_recall_queue(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client)
        db = get_db()
        lead = db.execute("SELECT id FROM leads WHERE full_name='CRM Prospect'").fetchone()
        db.execute(
            """UPDATE leads SET lead_status='do_not_contact', do_not_contact=1,
                   marketing_consent=0, recycle_due_at=datetime('now', '-1 minute')
               WHERE id=?""",
            (lead["id"],),
        )
        db.commit()

        client.get("/leads/")
        row = get_db().execute("SELECT * FROM leads WHERE id=?", (lead["id"],)).fetchone()
        assert row["lead_status"] == "do_not_contact"


def test_conversion_links_prospect_application_and_member(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client)
        prospect = get_db().execute(
            "SELECT id FROM leads WHERE full_name='CRM Prospect'"
        ).fetchone()
        client.post(
            f"/leads/{prospect['id']}/activities",
            data={"activity_type": "visit", "outcome": "joined"},
        )
        # Conversion requires the canonical SHOW/CONSULTATION stage.
        db = get_db()
        from onecpase.sales_pipeline import transition_sales_stage
        transition_sales_stage(db, prospect["id"], "SHOW", 1, reason="Prospect attended a visit", commit=True)
        response = client.post(
            f"/leads/{prospect['id']}/convert",
            data={"package": "Premium 12", "id_number": "9001015009087"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        row = get_db().execute("SELECT * FROM leads WHERE id=?", (prospect["id"],)).fetchone()
        application = get_db().execute(
            "SELECT * FROM membership_applications WHERE lead_id=?", (prospect["id"],)
        ).fetchone()
        assert row["lead_status"] == "joined"
        assert row["converted_member_id"] == application["member_id"]
        assert application["member_id"] is not None


def test_funnel_report_renders(app):
    with app.test_client() as client:
        _login_admin(client)
        response = client.get("/leads/funnel")
        assert response.status_code == 200
        assert b"Overall Funnel" in response.data


def test_sales_process_feeds_sales_kpi_report(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client, name="KPI Process Prospect")
        lead = get_db().execute(
            "SELECT id FROM leads WHERE full_name=?", ("KPI Process Prospect",)
        ).fetchone()

        response = client.post(
            f"/leads/{lead['id']}/activities",
            data={"activity_type": "visit", "outcome": "joined"},
        )
        assert response.status_code == 302

        event = get_db().execute(
            """SELECT from_value, to_value, owner_id, actor_id FROM stage_events
               WHERE lead_id=? AND field='lead_status' ORDER BY id DESC LIMIT 1""",
            (lead["id"],),
        ).fetchone()
        assert tuple(event) == ("assigned", "show", 1, 1)

        response = client.get("/sales-dashboard?from=2000-01-01&to=2099-12-31")
        assert response.status_code == 200
        assert b"Shows" in response.data
        assert b"Joined" in response.data


def test_leads_pipeline_does_not_show_removed_kpi_blocks(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client)
        prospect = get_db().execute(
            "SELECT id FROM leads WHERE full_name='CRM Prospect'"
        ).fetchone()

        # Multiple contact activities still count the client once today.
        client.post(
            f"/leads/{prospect['id']}/activities",
            data={"activity_type": "whatsapp", "outcome": "no_response"},
        )
        client.post(
            f"/leads/{prospect['id']}/activities",
            data={
                "activity_type": "appointment",
                "outcome": "scheduled",
                "scheduled_for": f"{date.today().isoformat()}T14:00",
            },
        )
        client.post(
            f"/leads/{prospect['id']}/activities",
            data={
                "activity_type": "appointment",
                "outcome": "rescheduled",
                "scheduled_for": f"{(date.today() + timedelta(days=1)).isoformat()}T15:00",
            },
        )
        _create_prospect(client, phone="083 555 0101", name="Walk In Prospect")
        walk_in = get_db().execute(
            "SELECT id FROM leads WHERE full_name='Walk In Prospect'"
        ).fetchone()
        get_db().execute(
            """INSERT INTO lead_activities
               (lead_id, activity_type, outcome, status, occurred_at, completed_at, created_by)
               VALUES (?, 'visit', 'completed', 'completed', datetime('now', 'localtime'),
                       datetime('now', 'localtime'), 1)""",
            (walk_in["id"],),
        )
        get_db().commit()

        response = client.get("/leads/")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'data-testid="today-captured"' not in html
        assert "Clients Captured" not in html
        assert "All Prospects" not in html
        assert "Active Pipeline" not in html


def test_guided_referral_contact_to_appointment_to_membership_journey(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client)
        prospect = get_db().execute(
            "SELECT id FROM leads WHERE full_name='CRM Prospect'"
        ).fetchone()
        lid = prospect["id"]

        page = client.get(f"/leads/{lid}")
        assert b"Contact Activity" in page.data
        assert b"Add Sales Activity" not in page.data
        assert b'option value="whatsapp"' in page.data
        assert b'option value="email"' in page.data
        assert b'option value="call"' in page.data
        assert page.data.index(b'option value="call"') < page.data.index(b'option value="whatsapp"')
        assert b"Offer Amount" not in page.data
        assert b"Discount" not in page.data

        client.post(
            f"/leads/{lid}/activities",
            data={
                "activity_type": "whatsapp",
                "outcome": "appointment_booked",
                "scheduled_for": f"{date.today().isoformat()}T15:00",
            },
        )
        page = client.get(f"/leads/{lid}")
        assert b"Appointment Result" in page.data
        assert b"Client Joined" in page.data
        assert b"Client Didn't Show" in page.data
        assert b"Not Interested" in page.data

        response = client.post(
            f"/leads/{lid}/activities",
            data={"activity_type": "appointment", "outcome": "joined"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/leads/{lid}/convert")


def test_offer_and_membership_are_blocked_before_gym_attendance(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client)
        lid = get_db().execute(
            "SELECT id FROM leads WHERE full_name='CRM Prospect'"
        ).fetchone()["id"]

        response = client.post(
            f"/leads/{lid}/activities",
            data={"activity_type": "offer", "outcome": "presented", "offer_amount": "500"},
            follow_redirects=True,
        )
        assert b"not available in the Leads stage" in response.data
        assert get_db().execute(
            "SELECT COUNT(*) FROM lead_activities WHERE lead_id=? AND activity_type='offer'",
            (lid,),
        ).fetchone()[0] == 0

        response = client.get(f"/leads/{lid}/convert", follow_redirects=True)
        assert b"Membership can only start after" in response.data


def test_referral_source_is_immutable_and_follow_up_is_limited_to_five_workdays(app):
    with app.test_client() as client:
        _login_admin(client)
        _create_prospect(client)
        prospect = get_db().execute(
            "SELECT * FROM leads WHERE full_name='CRM Prospect'"
        ).fetchone()
        lid = prospect["id"]

        # A profile edit cannot replace the captured Referral attribution.
        client.post(
            f"/leads/{lid}/edit",
            data={
                "full_name": prospect["full_name"],
                "phone": prospect["phone"],
                "email": prospect["email"],
                "source": "Outreach",
                "marketing_consent": "1",
            },
        )
        row = get_db().execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
        assert row["original_source"] == "Referral"
        assert row["source"] == "Referral"

        too_late = (date.today() + timedelta(days=10)).isoformat()
        response = client.post(
            f"/leads/{lid}/activities",
            data={
                "activity_type": "whatsapp",
                "outcome": "follow_up_required",
                "reason": "Client is busy",
                "scheduled_for": f"{too_late}T09:00",
            },
            follow_redirects=True,
        )
        assert b"within five working days" in response.data
        assert get_db().execute(
            "SELECT COUNT(*) FROM lead_activities WHERE lead_id=? AND outcome='follow_up_required'",
            (lid,),
        ).fetchone()[0] == 0


def test_outreach_and_social_media_allow_call_whatsapp_and_email(app):
    with app.test_client() as client:
        _login_admin(client)
        for source, phone, name in (
            ("Outreach", "0821110001", "Outreach Prospect"),
            ("Social Media", "0821110002", "Social Prospect"),
        ):
            client.post(
                "/leads/add",
                data={
                    "full_name": name,
                    "phone": phone,
                    "email": f"{name.split()[0].lower()}@example.com",
                    "source": source,
                    "marketing_consent": "1",
                },
            )
            lid = get_db().execute("SELECT id FROM leads WHERE full_name=?", (name,)).fetchone()["id"]
            page = client.get(f"/leads/{lid}")
            assert b'option value="call"' in page.data
            assert b'option value="whatsapp"' in page.data
            assert b'option value="email"' in page.data


def test_reception_walk_ins_rotate_across_available_sales_consultants(app):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO users
               (id, username, password_hash, full_name, role, department, active)
               VALUES (10, 'reception', 'x', 'Reception User', 'reception', 'Reception', 1)"""
        )
        for uid, order in ((20, 1), (21, 2)):
            db.execute(
                """INSERT INTO users
                   (id, username, password_hash, full_name, role, department, active,
                    is_sales_consultant, sales_available, sales_rotation_order)
                   VALUES (?, ?, 'x', ?, 'staff', 'Sales', 1, 1, 1, ?)""",
                (uid, f"sales{order}", f"Sales Consultant {order}", order),
            )
        db.commit()

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 10
            session["username"] = "reception"
            session["role"] = "reception"
            session["permissions"] = ["capture_leads", "view_leads"]

        page = client.get("/leads/add")
        assert b"Walk-In / Enquiry" in page.data
        assert b'<option value="Referral"' not in page.data
        assert b'<option value="Outreach"' not in page.data

        for number in range(3):
            response = client.post(
                "/leads/add",
                data={
                    "full_name": f"Walk In {number}",
                    "phone": f"082000100{number}",
                    "source": "Walk-In / Enquiry",
                    "marketing_consent": "1",
                },
                follow_redirects=False,
            )
            assert response.status_code == 302

        assignments = [
            row["assigned_to"] for row in get_db().execute(
                "SELECT assigned_to FROM leads ORDER BY id"
            ).fetchall()
        ]
        assert assignments == [20, 21, 20]
        page = client.get(response.headers["Location"])
        assert b"Walk-In Details" in page.data


def test_category_one_skips_three_rotation_turns_then_restores_consultant(app):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO users
               (id, username, password_hash, full_name, role, department, active)
               VALUES (10, 'reception', 'x', 'Reception User', 'reception', 'Reception', 1)"""
        )
        db.execute(
            """INSERT INTO users
               (id, username, password_hash, full_name, role, department, active,
                is_sales_consultant, sales_available, sales_rotation_order,
                sales_disqualification_category, sales_skip_remaining)
               VALUES (20, 'sales1', 'x', 'Penalized Consultant', 'staff', 'Sales',
                       1, 1, 1, 1, 1, 3)"""
        )
        db.execute(
            """INSERT INTO users
               (id, username, password_hash, full_name, role, department, active,
                is_sales_consultant, sales_available, sales_rotation_order)
               VALUES (21, 'sales2', 'x', 'Other Consultant', 'staff', 'Sales',
                       1, 1, 1, 2)"""
        )
        db.commit()

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 10
            session["username"] = "reception"
            session["role"] = "reception"
            session["permissions"] = ["capture_leads", "view_leads"]

        for number in range(4):
            response = client.post(
                "/leads/add",
                data={
                    "full_name": f"Penalty Walk In {number}",
                    "phone": f"082000200{number}",
                    "source": "Walk-In / Enquiry",
                    "marketing_consent": "1",
                },
            )
            assert response.status_code == 302

        assignments = [
            row["assigned_to"]
            for row in get_db().execute(
                "SELECT assigned_to FROM leads ORDER BY id"
            ).fetchall()
        ]
        assert assignments == [21, 21, 21, 20]
        penalized = get_db().execute(
            "SELECT sales_disqualification_category, sales_skip_remaining FROM users WHERE id=20"
        ).fetchone()
        assert penalized["sales_disqualification_category"] == 1
        assert penalized["sales_skip_remaining"] == 0
