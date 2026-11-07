"""Own follow-ups due today or earlier lock the app until the work is done."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from onecpase.database import get_db

PAGE = {"Accept": "text/html", "Sec-Fetch-Mode": "navigate"}
TODAY = datetime.now(ZoneInfo("Africa/Johannesburg")).date()


@pytest.fixture
def seeded(app):
    app.config["ACTIVE_PHASE"] = 3
    with app.app_context():
        db = get_db()
        for uid, role in [(11, "staff"), (12, "manager")]:
            db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,email,active) "
                       "VALUES (?,?,'x',?,?,'Sales',?,1)", (uid, f"lock{uid}", f"Lock {uid}", role, f"lock{uid}@example.com"))
        db.execute("UPDATE users SET supervisor_id=12 WHERE id=11")
        db.execute("""INSERT INTO queries(id,category,description,status,assigned_to,logged_by,follow_up_date)
            VALUES (901,'payments','Call back','in_progress',11,11,?)""", (TODAY.isoformat(),))
        db.commit()
    return app


def login(client, uid, role="staff", permissions=("daily_queries",)):
    with client.session_transaction() as s:
        s.update(user_id=uid, role=role, permissions=list(permissions), full_name=f"Lock {uid}")


def locked(response):
    return response.status_code == 302 and response.headers["Location"].endswith("/notifications/overdue")


def test_due_follow_up_locks_until_it_is_rescheduled(seeded, client):
    login(client, 11)
    assert locked(client.get("/debicheck", headers=PAGE))
    page = client.get("/notifications/overdue", headers=PAGE)
    assert page.status_code == 200 and b"Attend to this first" in page.data and b"/queries/901" in page.data
    assert not locked(client.get("/queries/901", headers=PAGE))  # the work itself stays open

    with seeded.app_context():
        db = get_db()
        db.execute("UPDATE queries SET follow_up_date=? WHERE id=901", ((TODAY + timedelta(days=1)).isoformat(),))
        db.commit()
    assert not locked(client.get("/debicheck", headers=PAGE))


def test_acknowledging_does_not_unlock(seeded, client):
    login(client, 11)
    client.get("/notifications/overdue", headers=PAGE)
    with seeded.app_context():
        nid = get_db().execute("SELECT id FROM staff_notifications WHERE recipient_id=11").fetchone()[0]
    client.post(f"/notifications/{nid}/acknowledge")
    assert locked(client.get("/debicheck", headers=PAGE))


def test_background_requests_are_not_redirected(seeded, client):
    login(client, 11)
    client.get("/notifications/overdue", headers=PAGE)
    assert not locked(client.get("/notifications/count", headers={"Accept": "application/json", "Sec-Fetch-Mode": "cors"}))


def test_supervisor_sees_team_work_but_is_not_locked(seeded, client):
    login(client, 11)
    client.get("/notifications/overdue", headers=PAGE)  # creates the notifications
    login(client, 12, role="manager")
    response = client.get("/notifications", headers=PAGE)
    assert response.status_code == 200 and b"due-work-banner" in response.data
    assert not locked(client.get("/debicheck", headers=PAGE))


def test_no_lock_when_the_work_page_cannot_be_opened(seeded, client):
    from flask import session
    from onecpase.notifications import blocking_items, refresh
    with seeded.test_request_context():
        session.update(user_id=11, role="staff", permissions=["daily_queries"])
        db = get_db()
        refresh(db)
        assert len(blocking_items(db, 11)[0]) == 1
        session["permissions"] = []
        assert blocking_items(db, 11)[0] == []


def test_sign_in_shows_due_work_once(seeded, client):
    login(client, 12, role="manager")
    with client.session_transaction() as s:
        s["show_due_popup"] = True
    login(client, 11)  # keep the flag; user 11 owns the follow-up
    with client.session_transaction() as s:
        s["show_due_popup"] = True
    first = client.get("/notifications", headers=PAGE)
    assert b"due-work-dialog" in first.data and b"Due today and overdue" in first.data
    assert b"due-work-dialog" not in client.get("/notifications", headers=PAGE).data
