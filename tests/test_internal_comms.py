"""Internal staff messages and announcements.

The subtle one is thread identity: whichever of two people writes first, they
must land in the same conversation. staff_threads stores the pair lowest-id
first with a UNIQUE on it, and these tests hold that, because the failure mode
is two half-conversations facing each other with neither person seeing the
other's messages.
"""

import pytest

from onecpase.database import get_db
from onecpase.internal_comms import unread_counts
from onecpase.phases import enabled_blueprints


def _user(client, uid, username, role="staff"):
    with client.application.app_context():
        db = get_db()
        db.execute(
            """INSERT OR REPLACE INTO users
               (id, username, password_hash, full_name, role, active)
               VALUES (?, ?, 'x', ?, ?, 1)""",
            (uid, username, username.replace(".", " ").title(), role),
        )
        db.commit()
    return uid


def _as(client, uid, role="staff"):
    with client.session_transaction() as session:
        session["user_id"] = uid
        session["role"] = role
        session["username"] = f"user{uid}"


def _send(client, to_uid, body):
    return client.post(f"/internal/messages/{to_uid}", data={"body": body},
                       follow_redirects=True)


@pytest.fixture
def staff(client):
    _user(client, 101, "alice", "manager")
    _user(client, 102, "bob", "staff")
    _user(client, 103, "carol", "staff")
    return client


# ── Direct messages ──────────────────────────────────────────────────────────

def test_a_message_reaches_the_other_person(staff):
    _as(staff, 101, "manager")
    assert _send(staff, 102, "Did the 10am show up?").status_code == 200

    _as(staff, 102)
    page = staff.get("/internal/messages/101").get_data(as_text=True)
    assert "Did the 10am show up?" in page


def test_one_thread_per_pair_whoever_starts_it(staff):
    """The ordering trick: 101->102 and 102->101 are the same conversation."""
    _as(staff, 101, "manager")
    _send(staff, 102, "from alice")
    _as(staff, 102)
    _send(staff, 101, "from bob")

    with staff.application.app_context():
        db = get_db()
        threads = db.execute("SELECT COUNT(*) FROM staff_threads").fetchone()[0]
        messages = db.execute("SELECT COUNT(*) FROM staff_messages").fetchone()[0]
    assert threads == 1, "a second thread means the pair ordering is broken"
    assert messages == 2

    page = staff.get("/internal/messages/101").get_data(as_text=True)
    assert "from alice" in page and "from bob" in page


def test_opening_a_thread_marks_their_messages_read(staff):
    _as(staff, 101, "manager")
    _send(staff, 102, "please read this")

    with staff.application.app_context():
        db = get_db()
        assert unread_counts(db, 102)["messages"] == 1
        # The sender's own message is never unread for them.
        assert unread_counts(db, 101)["messages"] == 0

    _as(staff, 102)
    staff.get("/internal/messages/101")

    with staff.application.app_context():
        assert unread_counts(get_db(), 102)["messages"] == 0


def test_an_empty_message_is_refused(staff):
    _as(staff, 101, "manager")
    _send(staff, 102, "   ")
    with staff.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM staff_messages").fetchone()[0] == 0


def test_you_cannot_message_yourself(staff):
    _as(staff, 101, "manager")
    _send(staff, 101, "talking to myself")
    with staff.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM staff_messages").fetchone()[0] == 0


def test_a_third_party_cannot_read_someone_elses_thread(staff):
    _as(staff, 101, "manager")
    _send(staff, 102, "between alice and bob")

    _as(staff, 103)
    # Carol's view of Alice is Carol's own thread, which is empty.
    page = staff.get("/internal/messages/101").get_data(as_text=True)
    assert "between alice and bob" not in page
    with staff.application.app_context():
        assert unread_counts(get_db(), 103)["messages"] == 0


# ── Announcements ────────────────────────────────────────────────────────────

def _post_announcement(client, title="Shift change", body="Saturday opens at 06:00"):
    return client.post("/internal/announcements/new",
                       data={"title": title, "body": body}, follow_redirects=True)


def test_a_manager_can_post_and_everyone_sees_it(staff):
    _as(staff, 101, "manager")
    assert _post_announcement(staff).status_code == 200

    _as(staff, 102)
    page = staff.get("/internal/announcements").get_data(as_text=True)
    assert "Shift change" in page and "Saturday opens at 06:00" in page
    with staff.application.app_context():
        assert unread_counts(get_db(), 102)["announcements"] == 1


def test_ordinary_staff_cannot_post(staff):
    _as(staff, 102)
    assert _post_announcement(staff, title="Not allowed").status_code == 403
    with staff.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 0


def test_marking_read_is_recorded_and_repeatable(staff):
    _as(staff, 101, "manager")
    _post_announcement(staff)
    with staff.application.app_context():
        aid = get_db().execute("SELECT id FROM announcements").fetchone()["id"]

    _as(staff, 102)
    staff.post(f"/internal/announcements/{aid}/read", follow_redirects=True)
    staff.post(f"/internal/announcements/{aid}/read", follow_redirects=True)

    with staff.application.app_context():
        db = get_db()
        assert db.execute(
            "SELECT COUNT(*) FROM announcement_reads WHERE announcement_id=?", (aid,)
        ).fetchone()[0] == 1, "a second read must not create a second row"
        assert unread_counts(db, 102)["announcements"] == 0


def test_archiving_hides_it_but_keeps_who_read_it(staff):
    _as(staff, 101, "manager")
    _post_announcement(staff)
    with staff.application.app_context():
        aid = get_db().execute("SELECT id FROM announcements").fetchone()["id"]

    _as(staff, 102)
    staff.post(f"/internal/announcements/{aid}/read", follow_redirects=True)

    _as(staff, 101, "manager")
    staff.post(f"/internal/announcements/{aid}/archive", follow_redirects=True)

    _as(staff, 102)
    assert "Shift change" not in staff.get("/internal/announcements").get_data(as_text=True)
    with staff.application.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM announcement_reads WHERE announcement_id=?", (aid,)
        ).fetchone()[0] == 1


def test_staff_cannot_archive_or_pin(staff):
    _as(staff, 101, "manager")
    _post_announcement(staff)
    with staff.application.app_context():
        aid = get_db().execute("SELECT id FROM announcements").fetchone()["id"]

    _as(staff, 102)
    assert staff.post(f"/internal/announcements/{aid}/archive").status_code == 403
    assert staff.post(f"/internal/announcements/{aid}/pin").status_code == 403
    with staff.application.app_context():
        row = get_db().execute("SELECT active, pinned FROM announcements WHERE id=?", (aid,)).fetchone()
    assert row["active"] == 1 and row["pinned"] == 0


# ── Availability ─────────────────────────────────────────────────────────────

def test_internal_comms_is_open_in_phase_one():
    """Staff need this from day one, not when a later phase opens."""
    assert "internal_comms" in enabled_blueprints(1)


def test_unread_counts_tolerate_a_bad_user_id(client):
    with client.application.app_context():
        assert unread_counts(get_db(), None)["total"] == 0
        assert unread_counts(get_db(), "not-a-number")["total"] == 0
