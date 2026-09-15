"""Event registration: the public link, and the staff-facing view of who signed up.

Deliberately separate from leads.py - an event registration is not a sales
lead working the pipeline, it is a one-off signup for a dated event.
"""

from onecpase.database import get_db


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
    # The banner filename is set by convention so dropping a photo in later
    # is the only step needed - see event_register.html's graceful fallback.
    assert event["banner_filename"] == "elev8-your-heritage-fun-run.jpg"
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
