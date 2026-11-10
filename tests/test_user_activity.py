"""User activity: every sign-in attempt, page opened and change is visible to admins.

Page visits are written when the response is closed, as Gunicorn does; the test
client only does that with ``buffered=True``.

Recording must never get in the way: a page that fails keeps its half-finished
writes uncommitted, and background JSON refreshes are not counted as visits.
"""
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from flask import jsonify
from werkzeug.security import generate_password_hash

from onecpase import user_activity
from onecpase.database import get_db


@pytest.fixture
def people(app):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO users (id, username, password_hash, full_name, role, active, email, must_change_password)
               VALUES (90, 'thandi', ?, 'Thandi Nkosi', 'staff', 1, 'thandi@example.com', 0)""",
            (generate_password_hash("Her-Password-1"),),
        )
        db.execute(
            """INSERT INTO users (id, username, password_hash, full_name, role, active, email, must_change_password)
               VALUES (91, 'sipho', ?, 'Sipho Dlamini', 'staff', 0, 'sipho@example.com', 0)""",
            (generate_password_hash("His-Password-1"),),
        )
        db.execute(
            """INSERT INTO users (id, username, password_hash, full_name, role, active, email, must_change_password)
               VALUES (92, 'boss', 'x', 'Owner Admin', 'admin', 1, 'boss@example.com', 0)"""
        )
        db.commit()


def _sign_ins(app):
    with app.app_context():
        return [dict(r) for r in get_db().execute(
            "SELECT user_id, username, event, ip, user_agent FROM user_sign_ins ORDER BY id").fetchall()]


def _visits(app):
    with app.app_context():
        return [dict(r) for r in get_db().execute(
            "SELECT user_id, method, path, endpoint, view_args, status FROM user_page_visits ORDER BY id").fetchall()]


def _as(client, user_id, role):
    with client.session_transaction() as s:
        s["user_id"] = user_id
        s["role"] = role
        s["username"] = "boss" if role == "admin" else "thandi"


def test_every_kind_of_sign_in_attempt_is_recorded(app, client, people):
    client.post("/select-tenant", data={"tenant_slug": "elev8"})
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36"}

    client.post("/login", data={"username": "thandi", "password": "wrong"}, headers=headers)
    client.post("/login", data={"username": "nobody", "password": "x"}, headers=headers)
    client.post("/login", data={"username": "sipho", "password": "His-Password-1"}, headers=headers)

    with patch("onecpase.auth._generate_otp", lambda: "123456"), \
            patch("onecpase.auth._send_otp_email", lambda *a: True):
        client.post("/login", data={"username": "thandi", "password": "Her-Password-1"}, headers=headers)
    client.post("/login/verify", data={"otp": "000000"}, headers=headers)
    client.post("/login/verify", data={"otp": "123456"}, headers=headers)
    client.get("/logout", headers=headers)

    events = _sign_ins(app)
    assert [(e["user_id"], e["event"]) for e in events] == [
        (90, "wrong_password"),
        (None, "unknown_username"),
        (91, "disabled_account"),
        (90, "code_sent"),
        (90, "wrong_code"),
        (90, "signed_in"),
        (90, "signed_out"),
    ]
    assert events[1]["username"] == "nobody"
    assert all(e["ip"] == "127.0.0.1" for e in events)
    assert user_activity.describe_device(events[0]["user_agent"]) == "Chrome on Windows"


def test_too_many_wrong_codes_is_recorded_as_a_lock_out(app, client, people):
    client.post("/select-tenant", data={"tenant_slug": "elev8"})
    with patch("onecpase.auth._generate_otp", lambda: "123456"), \
            patch("onecpase.auth._send_otp_email", lambda *a: True):
        client.post("/login", data={"username": "thandi", "password": "Her-Password-1"})
    for _ in range(5):
        client.post("/login/verify", data={"otp": "000000"})
    events = [e["event"] for e in _sign_ins(app)]
    assert events.count("wrong_code") == 4
    assert events[-1] == "too_many_codes"
    with client.session_transaction() as s:
        assert not s.get("user_id")


def test_pages_forms_and_downloads_are_recorded_but_background_refreshes_are_not(app, client, people):
    app.add_url_rule("/_t/json", "t_json", lambda: jsonify(ok=True))
    app.add_url_rule("/_t/form", "t_form", lambda: jsonify(ok=True), methods=["POST"])
    _as(client, 92, "admin")

    assert client.get("/admin/users", buffered=True).status_code == 200
    assert client.get("/_t/json", buffered=True).status_code == 200
    assert client.post("/_t/form", buffered=True).status_code == 200
    client.get("/static/css/components.css", buffered=True)

    visits = _visits(app)
    assert [(v["method"], v["endpoint"]) for v in visits] == [("GET", "admin.users_index"), ("POST", "t_form")]
    assert all(v["user_id"] == 92 for v in visits)


def test_signed_out_visitors_are_not_recorded(app, client, people):
    client.get("/", buffered=True)
    assert _visits(app) == []


def test_recording_a_failed_page_never_commits_its_half_finished_work(app, client, people):
    def boom():
        get_db().execute("UPDATE users SET full_name='Half Written' WHERE id=90")
        raise RuntimeError("page failed")

    app.add_url_rule("/_t/boom", "t_boom", boom)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    _as(client, 92, "admin")

    assert client.get("/_t/boom", buffered=True).status_code == 500
    with app.app_context():
        assert get_db().execute("SELECT full_name FROM users WHERE id=90").fetchone()[0] == "Thandi Nkosi"
    assert [(v["endpoint"], v["status"]) for v in _visits(app)] == [("t_boom", 500)]


def test_a_recording_failure_does_not_break_the_page(app, client, people):
    _as(client, 92, "admin")
    with patch("onecpase.user_activity._activity_connection", side_effect=RuntimeError("disk full")):
        assert client.get("/admin/users", buffered=True).status_code == 200


def test_old_page_visits_are_pruned_but_sign_ins_are_kept(app, client, people):
    old = (datetime.now(timezone.utc) - timedelta(days=user_activity.PAGE_VISIT_DAYS + 5)).strftime("%Y-%m-%d %H:%M:%S")
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO user_page_visits (user_id, method, path, created_at) VALUES (90, 'GET', '/old', ?)", (old,))
        db.execute("INSERT INTO user_sign_ins (user_id, event, created_at) VALUES (90, 'signed_in', ?)", (old,))
        db.commit()
    user_activity._last_prune.clear()
    _as(client, 92, "admin")
    client.get("/admin/users", buffered=True)
    assert [v["path"] for v in _visits(app)] == ["/admin/users"]
    assert len(_sign_ins(app)) == 1


def _seed_activity(app):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO members (id, first_name, last_name, id_number, contact)
               VALUES (501, 'Gugu', 'Maphumulo', 'enc', '0820000000')"""
        )
        db.execute(
            """INSERT INTO user_sign_ins (user_id, username, event, ip, user_agent, created_at)
               VALUES (90, 'thandi', 'signed_in', '41.1.2.3', 'Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile', ?)""",
            (now,),
        )
        db.execute(
            """INSERT INTO user_sign_ins (user_id, username, event, ip, created_at)
               VALUES (NULL, 'hacker', 'unknown_username', '8.8.8.8', ?)""",
            (now,),
        )
        db.execute(
            """INSERT INTO user_page_visits (user_id, method, path, endpoint, view_args, status, ip, created_at)
               VALUES (90, 'GET', '/members/501', 'members.member_detail', ?, 200, '41.1.2.3', ?)""",
            (json.dumps({"mid": 501}), now),
        )
        db.execute(
            "INSERT INTO audit_logs (user_id, action, new_value) VALUES (90, 'table_export', ?)",
            (json.dumps({"page": "/members", "title": "Members", "format": "xlsx", "rows": 1800, "columns": []}),),
        )
        db.execute(
            "INSERT INTO hr_audit (actor_id, subject, action, payload, created_at) VALUES (90, '7', 'payroll_updated', ?, ?)",
            (json.dumps({"salary": "SECRET-SALARY-99"}), datetime.now(timezone.utc).isoformat()),
        )
        db.commit()


def test_admin_sees_who_did_what_with_client_names(app, client, people):
    _seed_activity(app)
    _as(client, 92, "admin")
    page = client.get("/admin/activity").get_data(as_text=True)

    assert "Thandi Nkosi" in page
    assert "Client Gugu Maphumulo" in page
    assert "Members: Member detail" in page
    assert "Exported a table" in page and "1800 rows as XLSX" in page
    assert "41.1.2.3" in page and "Chrome on Android" in page
    assert "1 sign-in attempt in this period used a username that does not exist" in page
    assert "HR: Payroll updated" in page
    assert "SECRET-SALARY-99" not in page  # payroll detail stays out, even for admins


def test_filters_narrow_by_person_kind_and_date(app, client, people):
    _seed_activity(app)
    _as(client, 92, "admin")

    only_changes = client.get("/admin/activity?user=90&kind=changes").get_data(as_text=True)
    assert "Exported a table" in only_changes
    assert "Members: Member detail" not in only_changes

    failed = client.get("/admin/activity?failed=1").get_data(as_text=True)
    assert "Username typed: hacker" in failed
    assert "Members: Member detail" not in failed

    last_year = (date.today() - timedelta(days=400)).isoformat()
    empty = client.get(f"/admin/activity?start={last_year}&end={last_year}").get_data(as_text=True)
    assert "Nothing recorded for this person and period." in empty


def test_only_admins_can_open_the_activity_page(app, client, people):
    _as(client, 90, "staff")
    response = client.get("/admin/activity")
    assert response.status_code == 302
    assert "/admin/activity" not in response.headers["Location"]


def test_parse_filters_defaults_and_bad_input():
    today = date(2026, 11, 9)
    f = user_activity.parse_filters({}, today=today)
    assert (f["start"], f["end"], f["user_id"], f["kind"]) == (date(2026, 11, 3), today, None, "all")
    f = user_activity.parse_filters({"start": "2026-11-09", "end": "2026-11-01", "user": "x", "kind": "evil"}, today=today)
    assert (f["start"], f["end"], f["user_id"], f["kind"]) == (date(2026, 11, 1), date(2026, 11, 9), None, "all")


@pytest.mark.parametrize("phase", [1, 2, 3, 4, 5, 6, 7, "all"])
def test_activity_page_and_recording_work_in_every_phase(app, client, people, phase):
    from onecpase import phases
    app.config["ACTIVE_PHASE"] = phase
    _as(client, 92, "admin")

    users = client.get("/admin/users", buffered=True)
    assert users.status_code == 200
    assert b"/admin/activity?user=90" in users.data
    assert b"/admin/activity" in client.get("/admin/", buffered=True).data

    # An area closed in this phase stays closed, and the refusal is still recorded.
    closed = phases.normalise(phase) != "all" and "operations" not in phases.enabled_blueprints(phase)
    if closed:
        refused = client.get("/operations/", headers={"Accept": "text/html"}, buffered=True)
        assert refused.status_code == 302
        assert "/operations" not in refused.headers["Location"]

    page = client.get("/admin/activity?kind=pages", buffered=True)
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Admin: Users index" in text
    visits = [(v["endpoint"], v["status"]) for v in _visits(app)]
    assert visits[:2] == [("admin.users_index", 200), ("admin.admin_index", 200)]
    if closed:
        assert ("operations.index", 302) in visits or any(e.startswith("operations.") for e, _ in visits)


def test_every_area_has_a_readable_name(app):
    unnamed = sorted(
        name for name in app.blueprints
        if name not in user_activity._AREA_LABELS and name not in user_activity._SKIP_BLUEPRINTS
    )
    assert unnamed == []
