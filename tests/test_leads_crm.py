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
        # A "visit" activity with outcome "joined" must actually record the
        # prospect as joined — it used to fall through to the generic visit
        # branch and silently rewrite lead_status back to "show" instead.
        assert get_db().execute(
            "SELECT lead_status FROM leads WHERE id=?", (prospect["id"],)
        ).fetchone()["lead_status"] == "joined"
        # Conversion requires the canonical SHOW/CONSULTATION stage, and the
        # pipeline only moves one stage at a time with each stage's own
        # evidence in place.
        db = get_db()
        from onecpase.sales_pipeline import transition_sales_stage
        transition_sales_stage(db, prospect["id"], "CONTACT_ATTEMPTED", 1)
        transition_sales_stage(db, prospect["id"], "CONTACTED", 1)
        db.execute(
            """INSERT INTO lead_qualifications
               (lead_id, interested, needs_identified, ready_to_join, qualified_by, qualified_at)
               VALUES (?, 1, 1, 1, 1, datetime('now'))""",
            (prospect["id"],),
        )
        db.commit()
        transition_sales_stage(db, prospect["id"], "QUALIFIED", 1)
        transition_sales_stage(db, prospect["id"], "INVITED", 1)
        db.execute(
            """INSERT INTO sales_appointments (lead_id, appointment_at, status)
               VALUES (?, datetime('now'), 'BOOKED')""",
            (prospect["id"],),
        )
        db.commit()
        transition_sales_stage(db, prospect["id"], "APPOINTMENT_BOOKED", 1)
        transition_sales_stage(db, prospect["id"], "SHOW", 1, reason="Prospect attended a visit", commit=True)
        response = client.post(
            f"/members/add?lead_id={prospect['id']}",
            data={"first_name": "Converted", "last_name": "Prospect", "id_number": "9001015009087", "contact": "0800000000", "payment_type": "Cash", "package": "Premium 12"},
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
        # A visit with outcome "joined" must record the prospect as joined,
        # not "show" — this literally was the audit trail proving the bug:
        # it asserted ("assigned", "show", 1, 1) as the expected/correct value.
        assert tuple(event) == ("assigned", "joined", 1, 1)

        response = client.get(f"/sales-dashboard?from={date.today() - timedelta(days=30)}&to={date.today()}")
        assert response.status_code == 200
        assert b"Shows" in response.data
        assert b"Activated" in response.data
        assert b"Credited sales in selected period" in response.data


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
        # Attendance and the join decision are two separate questions, asked
        # one at a time. The Appointment Result card only asks whether the
        # client showed — there is no "Client Joined" shortcut here any more.
        page = client.get(f"/leads/{lid}")
        assert b"Appointment Result" in page.data
        assert b"Client Attended" in page.data
        assert b"Client Joined" not in page.data
        assert b"Client Didn't Show" in page.data
        assert b"Not Interested" in page.data

        client.post(
            f"/leads/{lid}/activities",
            data={"activity_type": "appointment", "outcome": "showed"},
        )
        assert get_db().execute(
            "SELECT lead_status, sales_stage FROM leads WHERE id=?", (lid,)
        ).fetchone()["lead_status"] == "show"

        # Attendance now confirmed, the Client Attendance Result card asks
        # the second question: did they actually join?
        page = client.get(f"/leads/{lid}")
        assert b"Client Attendance Result" in page.data
        assert b"Agreed to Join" in page.data

        response = client.post(
            f"/leads/{lid}/activities",
            data={"activity_type": "visit", "outcome": "joined"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/leads/{lid}/convert")
        row = get_db().execute(
            "SELECT lead_status, sales_stage FROM leads WHERE id=?", (lid,)
        ).fetchone()
        assert row["lead_status"] == "joined"
        # The canonical stage must hold at SHOW here, not race ahead to
        # JOINED — convert_lead()'s own "must be at SHOW or CONSULTATION"
        # gate is what actually creates the application, and JOINED is a
        # terminal stage with no further legal transition out of it.
        assert row["sales_stage"] == "SHOW"

        convert_response = client.post(
            f"/members/add?lead_id={lid}",
            data={"first_name": "Converted", "last_name": "Prospect", "id_number": "9001015009087", "contact": "0800000000", "payment_type": "Cash", "package": "Premium 12"},
            follow_redirects=False,
        )
        assert convert_response.status_code == 302
        application = get_db().execute(
            "SELECT id FROM membership_applications WHERE lead_id=?", (lid,)
        ).fetchone()
        assert application is not None


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


def test_reception_sees_once_whose_enquiry_a_new_walk_in_is(app):
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
                is_sales_consultant, sales_available, sales_rotation_order)
               VALUES (20, 'sales1', 'x', 'Sales Consultant One', 'staff', 'Sales', 1, 1, 1, 1)"""
        )
        db.commit()

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 10
            session["username"] = "reception"
            session["role"] = "reception"
            session["permissions"] = ["capture_leads", "view_leads"]

        response = client.post(
            "/leads/add",
            data={"full_name": "Popup Walk In", "phone": "0820003000", "source": "Walk-In / Enquiry"},
        )
        page = client.get(response.headers["Location"]).get_data(as_text=True)
        popup = page[page.index('<dialog id="walkInPopup"'):page.index("</dialog>")]
        assert "New walk-in: Popup Walk In" in popup
        assert "This enquiry belongs to" in popup and "Sales Consultant One" in popup

        again = client.get(response.headers["Location"]).get_data(as_text=True)
        assert "walkInPopup" not in again


def _trial_prospect(app, client, status="show", came_in="2026-11-01 10:00:00"):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, department, active)
               VALUES (10, 'reception', 'x', 'Reception User', 'reception', 'Reception', 1)"""
        )
        lid = db.execute(
            "INSERT INTO leads (full_name, phone, lead_status, source) VALUES ('Trial Client', '0820004000', ?, 'Walk-In / Enquiry')",
            (status,),
        ).lastrowid
        if came_in:
            db.execute(
                "INSERT INTO lead_activities (lead_id, activity_type, outcome, occurred_at) VALUES (?, 'visit', 'arrived', ?)",
                (lid, came_in),
            )
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=10, username="reception", role="reception", permissions=["capture_leads", "view_leads"])
    return lid


def _trials(app, lid):
    with app.app_context():
        return get_db().execute(
            "SELECT scheduled_for, notes FROM lead_activities WHERE lead_id=? AND activity_type='trial'", (lid,)
        ).fetchall()


def test_seven_day_trial_runs_consecutively_from_the_day_they_came(app, client, monkeypatch):
    import onecpase.leads as leads

    class Nov3(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 3)

    monkeypatch.setattr(leads, "date", Nov3)
    lid = _trial_prospect(app, client)
    assert b">7 Day Trial</button>" in client.get(f"/leads/{lid}").data

    client.post(f"/leads/{lid}/seven-day-trial")
    client.post(f"/leads/{lid}/seven-day-trial")

    trials = _trials(app, lid)
    assert len(trials) == 1 and trials[0]["scheduled_for"] == "2026-11-01"
    page = client.get(f"/leads/{lid}").get_data(as_text=True)
    assert "01 Nov 2026 to 07 Nov 2026" in page and "Day 3 of 7" in page
    assert ">7 Day Trial</button>" not in page
    with app.app_context():
        lead = get_db().execute("SELECT lead_status, decision_date FROM leads WHERE id=?", (lid,)).fetchone()
    assert lead["lead_status"] == "show" and lead["decision_date"] is None


def test_no_seven_day_trial_before_they_come_in_or_after_the_seven_days(app, client, monkeypatch):
    import onecpase.leads as leads

    class Nov8(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 8)

    monkeypatch.setattr(leads, "date", Nov8)
    booked = _trial_prospect(app, client, status="appointment_booked", came_in=None)
    assert b"7 Day Trial" not in client.get(f"/leads/{booked}").data
    client.post(f"/leads/{booked}/seven-day-trial")
    assert not _trials(app, booked)

    late = _trial_prospect(app, client)
    assert b">7 Day Trial</button>" not in client.get(f"/leads/{late}").data
    client.post(f"/leads/{late}/seven-day-trial")
    assert not _trials(app, late)


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


# ── Guided journey after conversion ───────────────────────────────────────────

def _lead_with_application(app, application_status):
    """A converted lead: marked joined, with an application at *status*."""
    from onecpase.database import get_db as _get_db

    with app.app_context():
        db = _get_db()
        lead_id = db.execute(
            """INSERT INTO leads (full_name, phone, phone_normalized, lead_status, sales_stage)
               VALUES ('Journey Prospect', '082 999 0000', '27829990000', 'joined', 'APPLICATION_STARTED')"""
        ).lastrowid
        db.execute(
            """INSERT INTO membership_applications (lead_id, application_status)
               VALUES (?, ?)""",
            (lead_id, application_status),
        )
        db.commit()
        lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        from onecpase.leads import _journey_step
        return _journey_step(db, lead)


def test_joined_lead_with_an_open_application_still_shows_the_membership_step(app):
    """Conversion sets lead_status='joined' and opens the application in one
    step. Treating 'joined' as closed hid the application work that had only
    just started."""
    assert _lead_with_application(app, "verification") == "membership"


def test_joined_lead_closes_once_the_application_is_active(app):
    assert _lead_with_application(app, "active") == "closed"


def test_joined_lead_closes_when_the_application_was_declined(app):
    assert _lead_with_application(app, "declined") == "closed"


def test_joined_lead_with_no_application_at_all_offers_to_complete_it(app):
    """Client Joined -> Membership marks the lead joined immediately, then
    hands off to convert_lead() to create the application. If that handoff
    is abandoned, the lead must not silently close - the consultant needs a
    way back to finish the conversion, not a dead 'Sales Journey Complete'."""
    from onecpase.database import get_db as _get_db
    from onecpase.leads import _journey_step

    with app.app_context():
        db = _get_db()
        lead_id = db.execute(
            """INSERT INTO leads (full_name, phone, phone_normalized, lead_status, sales_stage)
               VALUES ('Orphaned Join', '082 999 1111', '27829991111', 'joined', 'APPLICATION_INVITED')"""
        ).lastrowid
        db.commit()
        lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        assert _journey_step(db, lead) == "convert_pending"


def test_convert_resumes_from_a_stage_a_manager_already_advanced(client):
    """A manager can push the canonical sales stage straight to
    APPLICATION_INVITED (Controlled Sales Stage) before convert_lead() ever
    runs. Requiring an exact SHOW/CONSULTATION stage stranded that lead -
    conversion must resume from wherever the manual move left it."""
    _login_admin(client)
    _create_prospect(client, phone="082 999 2222", name="Manually Advanced")
    with client.application.app_context():
        db = get_db()
        lead_id = db.execute(
            "SELECT id FROM leads WHERE full_name='Manually Advanced'"
        ).fetchone()[0]
        db.execute(
            """INSERT INTO lead_activities (lead_id, activity_type, outcome, status, occurred_at)
               VALUES (?, 'visit', 'joined', 'completed', datetime('now'))""",
            (lead_id,),
        )
        db.execute(
            "UPDATE leads SET lead_status='joined', sales_stage='APPLICATION_INVITED' WHERE id=?",
            (lead_id,),
        )
        db.commit()

    response = client.post(
        f"/members/add?lead_id={lead_id}",
        data={"first_name": "Converted", "last_name": "Prospect", "id_number": "9001015009087", "contact": "0800000000", "payment_type": "Cash", "package": "Premium 12"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    # The full member profile is captured first; saving then lands on the new
    # application's document-upload step.
    assert "/applications/" in response.headers["Location"]
    application_page = client.get(response.headers["Location"])
    assert b"Documents" in application_page.data
    assert b"Upload" in application_page.data
    with client.application.app_context():
        db = get_db()
        application = db.execute(
            "SELECT * FROM membership_applications WHERE lead_id=?", (lead_id,)
        ).fetchone()
        assert application is not None
        lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        assert lead["converted_member_id"] is not None


def test_convert_opens_prefilled_add_member_form_before_documents(client):
    """Start Membership Application captures the full member profile on the
    Add Member form, prefilled from the prospect, before the document step."""
    _login_admin(client)
    _create_prospect(client, phone="082 999 3333", name="Thato Mokoena")
    with client.application.app_context():
        db = get_db()
        lead_id = db.execute(
            "SELECT id FROM leads WHERE full_name='Thato Mokoena'"
        ).fetchone()[0]
        db.execute(
            """INSERT INTO lead_activities (lead_id, activity_type, outcome, status, occurred_at)
               VALUES (?, 'visit', 'joined', 'completed', datetime('now'))""",
            (lead_id,),
        )
        db.execute("UPDATE leads SET lead_status='joined', sales_stage='SHOW' WHERE id=?", (lead_id,))
        db.commit()

    response = client.get(f"/leads/{lead_id}/convert")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/members/add?lead_id={lead_id}")
    page = client.get(response.headers["Location"])
    assert b"Prospect being converted: Thato Mokoena" in page.data
    assert b'value="Thato"' in page.data
    assert b'value="Mokoena"' in page.data
    assert b"Payment &amp; Banking" in page.data

    response = client.post(
        f"/members/add?lead_id={lead_id}",
        data={
            "first_name": "Thato", "last_name": "Mokoena", "id_number": "0510040213083",
            "contact": "0829993333", "payment_type": "Cash", "package": "Premium 12",
            "street_name": "Main Road", "city": "Johannesburg",
            "emergency_contact_name": "Lerato Mokoena", "source": "Forged source",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/applications/" in response.headers["Location"]
    with client.application.app_context():
        db = get_db()
        lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        member = db.execute("SELECT * FROM members WHERE id=?", (lead["converted_member_id"],)).fetchone()
        application = db.execute(
            "SELECT * FROM membership_applications WHERE lead_id=?", (lead_id,)
        ).fetchone()
        checklist = db.execute(
            "SELECT * FROM compliance_checklists WHERE application_id=?", (application["id"],)
        ).fetchone()
        assert lead["lead_status"] == "joined"
        assert lead["sales_stage"] == "APPLICATION_STARTED"
        assert application["member_id"] == member["id"]
        assert member["street_name"] == "Main Road"
        assert member["emergency_contact_name"] == "Lerato Mokoena"
        assert member["entry_type"] == "lead"
        assert member["source"] != "Forged source"
        assert member["member_status"] == "Unverified"
        assert checklist["age_category"] == "below_23"
        assert checklist["guardian_required"] == "required"
    assert client.get(f"/leads/{lead_id}/convert").headers["Location"].endswith(
        f"/applications/{application['id']}"
    )


def test_pipeline_renders_as_a_vertical_accordion(client):
    """The board is a vertical list of collapsed stages, not a horizontal scroll.

    Each stage is a <details> sharing one `name`, which is what makes the
    browser close the previously open stage — the "expand only when selected"
    behaviour, with no JavaScript to go wrong.
    """
    _login_admin(client)
    with client.application.app_context():
        db = get_db()
        for name, status in (
            ("Accordion Alice", "captured"),
            ("Accordion Bongani", "captured"),
            ("Accordion Chipo", "contacted"),
        ):
            db.execute(
                "INSERT INTO leads (full_name, phone, lead_status, source) VALUES (?,?,?,?)",
                (name, "082 000 0000", status, "Walk-In / Enquiry"),
            )
        db.execute("UPDATE leads SET assigned_to=1 WHERE full_name LIKE 'Accordion %'")
        db.commit()

    html = client.get("/leads/", follow_redirects=True).get_data(as_text=True)

    # Every kanban stage is a collapsible section in one exclusive group.
    assert html.count('class="pipeline-stage"') == 8
    assert html.count('name="pipeline"') == 8

    # Exactly one stage starts open: the first that actually has prospects.
    assert html.count('name="pipeline" open') == 1
    captured = html.index("Captured")
    contacted = html.index("Contacted")
    assert html.index('name="pipeline" open') < captured < contacted

    # The old horizontal board is gone.
    assert "min-width:max-content" not in html

    # Prospects still render inside their stage.
    for name in ("Accordion Alice", "Accordion Bongani", "Accordion Chipo"):
        assert name in html
