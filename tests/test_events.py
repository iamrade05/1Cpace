"""Event registration: the public link, and the staff-facing view of who signed up.

Deliberately separate from leads.py - an event registration is not a sales
lead working the pipeline, it is a one-off signup for a dated event.
"""
from unittest.mock import patch

from onecpase.database import get_db


def _configure_cloud_api(client):
    """WA_TOKEN is empty by default (nothing configured in this environment)
    so _send_ticket_message would silently skip both send paths - set the
    Cloud API config so tests that patch send_whatsapp_message actually
    exercise that path, matching what they're testing."""
    client.application.config["WA_TOKEN"] = "test-token"
    client.application.config["WA_RECEPTION_PHONE_ID"] = "test-phone-id"


def _login_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
    with client.application.app_context():
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO users
               (id, username, password_hash, full_name, role, active)
               VALUES (1, 'admin', 'x', 'Test Admin', 'admin', 1)"""
        )
        db.commit()


def _create_event(client, **overrides):
    data = {
        "name": "Elev8 Your Heritage Fun Run",
        "tagline": "Bring Your Energy, Bring a Friend!",
        "event_date": "2026-09-19",
        "start_time": "7:00 AM",
        "end_time": "12:00 PM",
        "location": "Kwa-Thema, Emajudeni",
        "distance": "5km Fun Run",
    }
    data.update(overrides)
    client.post("/admin/events/add", data=data, follow_redirects=True)
    with client.application.app_context():
        return get_db().execute(
            "SELECT * FROM events WHERE name=?", (data["name"],)
        ).fetchone()


def _registration_form(**overrides):
    data = {
        "first_name": "Thabo", "last_name": "Mokoena", "phone": "083 000 1111",
        "email": "thabo@example.com", "date_of_birth": "1990-05-12", "gender": "Male",
        "pace": "Run", "referral_source": "ELEV8 Gym member",
        "emergency_contact_name": "Nomsa Mokoena", "emergency_contact_phone": "083 999 8888",
        "emergency_contact_relation": "Spouse / Partner", "waiver_accepted": "1",
    }
    data.update(overrides)
    return data


# ── Admin: creating an event ─────────────────────────────────────────────────

def test_admin_creates_an_event_and_gets_a_link(client):
    _login_admin(client)
    event = _create_event(client)
    assert event is not None
    assert event["slug"] == "elev8-your-heritage-fun-run"
    # No banner was uploaded, so there is none yet - event_register.html
    # falls back to a plain navy header rather than a broken image.
    assert event["banner_filename"] is None
    assert event["active"] == 1


def test_event_needs_a_name(client):
    _login_admin(client)
    client.post("/admin/events/add", data={"name": ""}, follow_redirects=True)
    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_two_events_cannot_share_a_registration_link(client):
    _login_admin(client)
    _create_event(client, name="First Event", slug="fun-run")
    client.post("/admin/events/add", data={"name": "Second Event", "slug": "fun-run"},
               follow_redirects=True)
    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


# ── Admin: banner upload ──────────────────────────────────────────────────────
# Banners are saved under UPLOAD_FOLDER, never onecpase/static/ - a deploy
# replaces the onecpase package wholesale, which would silently wipe out
# anything saved under static/. See events._banner_dir.

def _png_file(name="banner.png"):
    import io
    return (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 32), name)


def test_uploading_a_banner_on_creation_is_saved_and_served(client):
    _login_admin(client)
    response = client.post(
        "/admin/events/add",
        data={"name": "Banner Test Event", "banner": _png_file()},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        event = get_db().execute(
            "SELECT * FROM events WHERE name='Banner Test Event'"
        ).fetchone()
    assert event["banner_filename"] == f"{event['slug']}.png"

    image = client.get(f"/events/{event['slug']}/banner")
    assert image.status_code == 200
    assert image.mimetype == "image/png"

    with client.session_transaction() as session:
        session.clear()
    page = client.get(f"/events/{event['slug']}")
    assert f'/events/{event["slug"]}/banner'.encode() in page.data


def test_an_unsupported_banner_file_type_is_rejected(client):
    _login_admin(client)
    import io
    response = client.post(
        "/admin/events/add",
        data={"name": "Bad Banner Event", "banner": (io.BytesIO(b"not an image"), "banner.gif")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"JPG, PNG, or WEBP" in response.data
    with client.application.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM events WHERE name='Bad Banner Event'"
        ).fetchone()[0] == 0


def test_an_event_without_a_banner_has_no_banner_image(client):
    _login_admin(client)
    event = _create_event(client)
    assert client.get(f"/events/{event['slug']}/banner").status_code == 404


def test_editing_an_event_can_add_a_banner_after_creation(client):
    _login_admin(client)
    event = _create_event(client)
    assert event["banner_filename"] is None

    response = client.post(
        f"/admin/events/{event['id']}/edit",
        data={"name": event["name"], "banner": _png_file()},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        updated = get_db().execute("SELECT * FROM events WHERE id=?", (event["id"],)).fetchone()
    assert updated["banner_filename"] == f"{event['slug']}.png"
    assert client.get(f"/events/{event['slug']}/banner").status_code == 200


def test_editing_an_event_without_a_new_banner_keeps_the_existing_one(client):
    _login_admin(client)
    event = _create_event(client)
    client.post(
        f"/admin/events/{event['id']}/edit",
        data={"name": event["name"], "banner": _png_file()},
        content_type="multipart/form-data",
    )
    # Edit again, this time without attaching a file.
    client.post(
        f"/admin/events/{event['id']}/edit",
        data={"name": event["name"], "location": "New Location"},
        content_type="multipart/form-data",
    )
    with client.application.app_context():
        updated = get_db().execute("SELECT * FROM events WHERE id=?", (event["id"],)).fetchone()
    assert updated["banner_filename"] == f"{event['slug']}.png"
    assert updated["location"] == "New Location"


def test_only_admin_can_create_or_view_events(client):
    with client.session_transaction() as session:
        session["user_id"] = 2
        session["role"] = "staff"
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, active) "
            "VALUES (2,'staff','x','Staff','staff',1)"
        )
        db.commit()
    response = client.get("/admin/events/", follow_redirects=True)
    assert b"Admin access required" in response.data


# ── Public registration ──────────────────────────────────────────────────────

def test_registration_page_is_reachable_without_signing_in(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()
    response = client.get(f"/events/{event['slug']}")
    assert response.status_code == 200
    assert b"Elev8 Your Heritage Fun Run" in response.data


def test_unknown_event_link_is_a_404(client):
    assert client.get("/events/does-not-exist").status_code == 404


def test_closed_event_is_not_reachable(client):
    _login_admin(client)
    event = _create_event(client)
    client.post(f"/admin/events/{event['id']}/toggle", follow_redirects=True)
    with client.session_transaction() as session:
        session.clear()
    assert client.get(f"/events/{event['slug']}").status_code == 404


def test_a_full_registration_is_stored(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    with patch("onecpase.events.send_whatsapp_message"):
        response = client.post(
            f"/events/{event['slug']}", data=_registration_form(), follow_redirects=True
        )
    assert response.status_code == 200
    assert b"You're Registered" in response.data

    with client.application.app_context():
        row = get_db().execute(
            "SELECT * FROM event_registrations WHERE event_id=?", (event["id"],)
        ).fetchone()
    assert row["first_name"] == "Thabo"
    assert row["phone"] == "0830001111"  # normalised, punctuation stripped
    assert row["waiver_accepted"] == 1


def test_registration_requires_the_waiver(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    client.post(
        f"/events/{event['slug']}",
        data=_registration_form(waiver_accepted="0"),
    )
    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM event_registrations").fetchone()[0] == 0


def test_registration_requires_a_valid_phone_number(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    client.post(f"/events/{event['slug']}", data=_registration_form(phone="123"))
    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM event_registrations").fetchone()[0] == 0


def test_bringing_a_friend_requires_their_name(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    client.post(
        f"/events/{event['slug']}",
        data=_registration_form(bringing_friend="1", friend_name=""),
    )
    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM event_registrations").fetchone()[0] == 0


def test_the_same_phone_cannot_register_twice_for_one_event(client):
    """A person double-submitting must not create two rows, and a genuine
    second signup under the same number for the same event is not real."""
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    with patch("onecpase.events.send_whatsapp_message"):
        client.post(f"/events/{event['slug']}", data=_registration_form())
    response = client.post(f"/events/{event['slug']}", data=_registration_form(
        first_name="Someone", last_name="Else",
    ))
    assert b"Already Registered" in response.data
    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM event_registrations").fetchone()[0] == 1


def test_the_honeypot_silently_drops_bot_submissions(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    response = client.post(
        f"/events/{event['slug']}",
        data=_registration_form(company_website="https://spam.example"),
    )
    assert b"You're Registered" in response.data  # looks successful to the bot
    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM event_registrations").fetchone()[0] == 0


def test_two_different_events_each_get_their_own_registrations(client):
    _login_admin(client)
    event_a = _create_event(client, name="Heritage Run", slug="heritage")
    event_b = _create_event(client, name="Spring Fun Walk", slug="spring")
    with client.session_transaction() as session:
        session.clear()

    with patch("onecpase.events.send_whatsapp_message"):
        client.post(f"/events/{event_a['slug']}", data=_registration_form(phone="0830001111"))
        client.post(f"/events/{event_b['slug']}", data=_registration_form(phone="0830002222"))

    with client.application.app_context():
        db = get_db()
        assert db.execute(
            "SELECT COUNT(*) FROM event_registrations WHERE event_id=?", (event_a["id"],)
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM event_registrations WHERE event_id=?", (event_b["id"],)
        ).fetchone()[0] == 1


# ── Admin: viewing registrations ─────────────────────────────────────────────

def test_admin_sees_registrations_and_can_export_csv(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()
    with patch("onecpase.events.send_whatsapp_message"):
        client.post(f"/events/{event['slug']}", data=_registration_form())

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"

    page = client.get(f"/admin/events/{event['id']}/registrations")
    assert b"Thabo" in page.data

    csv_response = client.get(f"/admin/events/{event['id']}/registrations/export.csv")
    assert csv_response.status_code == 200
    assert b"text/csv" in csv_response.headers["Content-Type"].encode()
    assert b"Thabo" in csv_response.data


def test_phase_one_reaches_both_public_and_admin_event_routes():
    from onecpase.phases import enabled_blueprints
    open_at_one = enabled_blueprints(1)
    assert "events" in open_at_one
    assert "admin_events" in open_at_one


# ── Registration numbers and personal tickets ────────────────────────────────

def test_each_registration_gets_a_sequential_number(client):
    _login_admin(client)
    _configure_cloud_api(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    with patch("onecpase.events.send_whatsapp_message") as wa:
        client.post(f"/events/{event['slug']}", data=_registration_form(phone="0830001111"))
        client.post(f"/events/{event['slug']}", data=_registration_form(
            first_name="Palesa", last_name="Nkosi", phone="0830002222",
        ))
    with client.application.app_context():
        rows = get_db().execute(
            "SELECT first_name, registration_number FROM event_registrations "
            "WHERE event_id=? ORDER BY registration_number", (event["id"],)
        ).fetchall()
    assert [(r["first_name"], r["registration_number"]) for r in rows] == [
        ("Thabo", 1), ("Palesa", 2),
    ]
    assert wa.call_count == 2


def test_registering_sends_a_whatsapp_ticket_link(client):
    _login_admin(client)
    _configure_cloud_api(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    with patch("onecpase.events.send_whatsapp_message") as wa:
        client.post(f"/events/{event['slug']}", data=_registration_form())

    assert wa.call_count == 1
    _phone_id, to, text = wa.call_args[0]
    assert to == "0830001111"
    assert "registration #1" in text
    assert f"/events/{event['slug']}/ticket/" in text


def test_the_ticket_page_shows_the_registration_number_and_i_am_here_button(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    with patch("onecpase.events.send_whatsapp_message"):
        client.post(f"/events/{event['slug']}", data=_registration_form())
    with client.application.app_context():
        token = get_db().execute(
            "SELECT ticket_token FROM event_registrations WHERE phone='0830001111'"
        ).fetchone()[0]

    page = client.get(f"/events/{event['slug']}/ticket/{token}")
    assert page.status_code == 200
    assert b"#1" in page.data
    assert b"I Am Here" in page.data


def test_an_unknown_ticket_token_is_a_404(client):
    _login_admin(client)
    event = _create_event(client)
    assert client.get(f"/events/{event['slug']}/ticket/not-a-real-token").status_code == 404


def test_checking_in_marks_arrival_and_is_idempotent(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    with patch("onecpase.events.send_whatsapp_message"):
        client.post(f"/events/{event['slug']}", data=_registration_form())
    with client.application.app_context():
        token = get_db().execute(
            "SELECT ticket_token FROM event_registrations WHERE phone='0830001111'"
        ).fetchone()[0]

    client.post(f"/events/{event['slug']}/ticket/{token}/checkin")
    page = client.get(f"/events/{event['slug']}/ticket/{token}")
    assert b"You're Checked In" in page.data

    with client.application.app_context():
        db = get_db()
        first = db.execute(
            "SELECT checked_in, checked_in_at FROM event_registrations WHERE phone='0830001111'"
        ).fetchone()
        assert first["checked_in"] == 1
        first_timestamp = first["checked_in_at"]

    # Tapping "I Am Here" again must not change the recorded arrival time.
    client.post(f"/events/{event['slug']}/ticket/{token}/checkin")
    with client.application.app_context():
        second_timestamp = get_db().execute(
            "SELECT checked_in_at FROM event_registrations WHERE phone='0830001111'"
        ).fetchone()[0]
    assert second_timestamp == first_timestamp


# ── Bringing a friend now gives the friend their own ticket ─────────────────

def test_bringing_a_friend_requires_their_phone_number(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    client.post(
        f"/events/{event['slug']}",
        data=_registration_form(bringing_friend="1", friend_name="Sipho Dlamini", friend_phone=""),
    )
    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM event_registrations").fetchone()[0] == 0


def test_a_friend_gets_their_own_registration_number_and_ticket(client):
    _login_admin(client)
    _configure_cloud_api(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    with patch("onecpase.events.send_whatsapp_message") as wa:
        response = client.post(
            f"/events/{event['slug']}",
            data=_registration_form(
                bringing_friend="1", friend_name="Sipho Dlamini", friend_phone="0821112222",
            ),
        )
    assert response.status_code == 200
    assert wa.call_count == 2  # primary registrant + friend

    with client.application.app_context():
        db = get_db()
        primary = db.execute("SELECT * FROM event_registrations WHERE phone='0830001111'").fetchone()
        friend = db.execute("SELECT * FROM event_registrations WHERE phone='0821112222'").fetchone()

    assert friend is not None
    assert friend["first_name"] == "Sipho"
    assert friend["last_name"] == "Dlamini"
    assert friend["registration_number"] == primary["registration_number"] + 1
    assert friend["invited_by_registration_id"] == primary["id"]
    assert friend["ticket_token"] and friend["ticket_token"] != primary["ticket_token"]

    friend_call = wa.call_args_list[1][0]
    assert friend_call[1] == "0821112222"
    assert "Thabo Mokoena has registered you" in friend_call[2]


def test_a_friend_who_already_registered_separately_does_not_get_a_duplicate_row(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    with patch("onecpase.events.send_whatsapp_message"):
        # The friend registers under their own name first.
        client.post(f"/events/{event['slug']}", data=_registration_form(
            first_name="Sipho", last_name="Dlamini", phone="0821112222",
        ))
        # Then Thabo registers and lists Sipho as the friend he's bringing.
        client.post(f"/events/{event['slug']}", data=_registration_form(
            bringing_friend="1", friend_name="Sipho Dlamini", friend_phone="0821112222",
        ))

    with client.application.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) FROM event_registrations WHERE phone='0821112222'"
        ).fetchone()[0]
    assert count == 1


# ── Admin views reflect registration numbers and check-ins ──────────────────

def test_admin_registrations_page_shows_numbers_and_checked_in_count(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()
    with patch("onecpase.events.send_whatsapp_message"):
        client.post(f"/events/{event['slug']}", data=_registration_form())
    with client.application.app_context():
        token = get_db().execute(
            "SELECT ticket_token FROM event_registrations WHERE phone='0830001111'"
        ).fetchone()[0]
    client.post(f"/events/{event['slug']}/ticket/{token}/checkin")

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"

    page = client.get(f"/admin/events/{event['id']}/registrations")
    assert b"#1" in page.data
    assert b"1 checked in" in page.data
    assert b"Arrived" in page.data

    csv_response = client.get(f"/admin/events/{event['id']}/registrations/export.csv")
    assert b"Checked in" in csv_response.data
    assert b"Yes" in csv_response.data


# ── WhatsApp Web fallback, for when the Cloud API isn't configured ──────────

def test_whatsapp_web_is_used_when_the_cloud_api_is_not_configured(client):
    """WA_TOKEN is empty by default in this environment - _send_ticket_message
    must fall back to queueing through the WhatsApp Web sender rather than
    silently sending nothing."""
    _login_admin(client)
    client.application.config["WHATSAPP_WEB_ENABLED"] = True
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    with patch("onecpase.whatsapp_web.queue_message") as queue_message:
        client.post(f"/events/{event['slug']}", data=_registration_form())

    assert queue_message.call_count == 1
    phone, text = queue_message.call_args[0]
    assert phone == "0830001111"
    assert "registration #1" in text
    with client.application.app_context():
        assert get_db().execute(
            "SELECT ticket_sent FROM event_registrations WHERE phone='0830001111'"
        ).fetchone()[0] == 1


def test_neither_whatsapp_path_configured_does_not_mark_ticket_sent(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()

    client.post(f"/events/{event['slug']}", data=_registration_form())

    with client.application.app_context():
        assert get_db().execute(
            "SELECT ticket_sent FROM event_registrations WHERE phone='0830001111'"
        ).fetchone()[0] == 0


# ── Backfilling registrations from before this feature existed ──────────────

def test_backfill_assigns_numbers_and_tokens_to_pre_existing_registrations(app):
    from onecpase.events import ensure_events_schema

    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO events (slug, name) VALUES ('legacy-event', 'Legacy Event')"""
        )
        event_id = db.execute("SELECT id FROM events WHERE slug='legacy-event'").fetchone()[0]
        # Simulate two registrations captured before registration_number and
        # ticket_token existed - registered out of insertion order, to prove
        # the backfill orders by registered_at, not by id.
        db.execute(
            """INSERT INTO event_registrations
               (event_id, first_name, last_name, phone, pace,
                emergency_contact_name, emergency_contact_phone, waiver_accepted, registered_at)
               VALUES (?, 'Second', 'Person', '0830009999', 'Run', 'EC', '0830001234', 1, '2026-01-02 10:00:00')""",
            (event_id,),
        )
        db.execute(
            """INSERT INTO event_registrations
               (event_id, first_name, last_name, phone, pace,
                emergency_contact_name, emergency_contact_phone, waiver_accepted, registered_at)
               VALUES (?, 'First', 'Person', '0830008888', 'Run', 'EC', '0830001234', 1, '2026-01-01 09:00:00')""",
            (event_id,),
        )
        db.commit()

        ensure_events_schema(db, "sqlite")

        rows = db.execute(
            "SELECT first_name, registration_number, ticket_token FROM event_registrations "
            "WHERE event_id=? ORDER BY registration_number", (event_id,)
        ).fetchall()
    assert [r["first_name"] for r in rows] == ["First", "Second"]
    assert [r["registration_number"] for r in rows] == [1, 2]
    assert all(r["ticket_token"] for r in rows)
    assert rows[0]["ticket_token"] != rows[1]["ticket_token"]


def test_backfill_is_a_no_op_once_everything_already_has_a_number(client):
    """Runs on every app startup (ensure_events_schema is called from
    init_db()) - must not renumber people every single time."""
    _login_admin(client)
    _configure_cloud_api(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()
    with patch("onecpase.events.send_whatsapp_message"):
        client.post(f"/events/{event['slug']}", data=_registration_form())

    from onecpase.events import ensure_events_schema
    with client.application.app_context():
        db = get_db()
        before = db.execute(
            "SELECT registration_number FROM event_registrations WHERE phone='0830001111'"
        ).fetchone()[0]
        ensure_events_schema(db, "sqlite")
        after = db.execute(
            "SELECT registration_number FROM event_registrations WHERE phone='0830001111'"
        ).fetchone()[0]
    assert before == after == 1


# ── Catching up registrants who never got a ticket message ──────────────────

def test_resend_tickets_only_messages_people_who_have_not_had_one(client):
    _login_admin(client)
    event = _create_event(client)
    with client.session_transaction() as session:
        session.clear()
    # Neither WhatsApp path is configured, so this registration lands with
    # ticket_sent=0 - exactly the "registered before it worked" scenario.
    client.post(f"/events/{event['slug']}", data=_registration_form())
    client.post(f"/events/{event['slug']}", data=_registration_form(
        first_name="Palesa", last_name="Nkosi", phone="0830002222",
    ))

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
    client.application.config["WHATSAPP_WEB_ENABLED"] = True

    with patch("onecpase.whatsapp_web.queue_message") as queue_message:
        response = client.post(
            f"/admin/events/{event['id']}/registrations/resend-tickets", follow_redirects=True
        )
    assert response.status_code == 200
    assert queue_message.call_count == 2

    # Clicking it again must not message the same people twice.
    with patch("onecpase.whatsapp_web.queue_message") as queue_message_again:
        client.post(f"/admin/events/{event['id']}/registrations/resend-tickets")
    assert queue_message_again.call_count == 0


def test_only_admin_can_trigger_a_resend(client):
    _login_admin(client)
    event = _create_event(client)
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, active) "
            "VALUES (2,'staff','x','Staff','staff',1)"
        )
        db.commit()
    with client.session_transaction() as session:
        session["user_id"] = 2
        session["role"] = "staff"
    response = client.post(
        f"/admin/events/{event['id']}/registrations/resend-tickets", follow_redirects=True
    )
    assert b"Admin access required" in response.data
