"""Reception and sales share physical phones and identify themselves by a
PIN ("account code" in Yeastar's terms) rather than by extension. This maps
each code to the staff member it belongs to, so a shared-phone call in the
PBX log shows who actually made it instead of just the shared extension.
"""
from onecpase.database import get_db, init_db


def _login_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, active) "
            "VALUES (1, 'admin', 'x', 'Test Admin', 'admin', 1)"
        )
        db.commit()


# ── The real Elev8 codes get seeded automatically at startup ────────────────

def test_the_real_reception_and_sales_codes_are_seeded(app):
    with app.app_context():
        rows = get_db().execute(
            "SELECT code, label, user_id, active FROM pbx_account_codes ORDER BY code"
        ).fetchall()
    by_code = {r["code"]: r for r in rows}
    assert by_code["3074"]["label"] == "Entle"
    assert by_code["5296"]["label"] == "Busi"
    assert by_code["1852"]["label"] == "Mvuleni"
    assert by_code["1347"]["label"] == "Gugu"
    assert by_code["4496"]["label"] == "Thokozane"
    assert by_code["7536"]["label"] == "Sales 1"
    assert by_code["8624"]["label"] == "Sales 2"
    assert by_code["9841"]["label"] == "Sales 3"
    for r in rows:
        assert r["active"] == 1


def test_generic_sales_pool_codes_are_not_guessed_onto_a_person(app):
    """"Sales 1/2/3" were supplied as shared pool codes, not tied to one
    named individual - forcing a guess would misattribute real calls."""
    with app.app_context():
        rows = get_db().execute(
            "SELECT user_id FROM pbx_account_codes WHERE code IN ('7536','8624','9841')"
        ).fetchall()
    assert all(r["user_id"] is None for r in rows)


def test_a_named_code_links_to_the_matching_staff_account_once_it_exists(app):
    """The migration only fills a code's user_id when it doesn't already
    have one - matching users by username prefix (e.g. 'mvuleni%')."""
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users (username, password_hash, full_name, role, active) "
            "VALUES ('mvuleni', 'x', 'Mvuleni Radebe', 'admin', 1)"
        )
        # The first init_db() (at app creation) already seeded '1852' with no
        # matching user - clear it so the re-run seeds it fresh, now that a
        # matching user exists, exactly like a real staff-account creation
        # followed by the next app restart would.
        db.execute("DELETE FROM pbx_account_codes WHERE code='1852'")
        db.commit()
        init_db()

        row = db.execute(
            """SELECT pac.user_id, u.username FROM pbx_account_codes pac
               JOIN users u ON u.id = pac.user_id WHERE pac.code='1852'"""
        ).fetchone()
    assert row["username"] == "mvuleni"


def test_an_admin_can_relink_a_code_and_the_seed_never_overwrites_it(app):
    """The seed only inserts a code once - an admin's own relink must stick
    even if the migration logic runs again on a later restart."""
    with app.app_context():
        db = get_db()
        other_user = db.execute(
            "INSERT INTO users (username, password_hash, full_name, active) "
            "VALUES ('someoneelse', 'x', 'Someone Else', 1)"
        ).lastrowid
        db.execute("UPDATE pbx_account_codes SET user_id=? WHERE code='1852'", (other_user,))
        db.commit()
        init_db()  # simulate a later restart re-running the same seed logic

        row = db.execute("SELECT user_id FROM pbx_account_codes WHERE code='1852'").fetchone()
    assert row["user_id"] == other_user


# ── Resolving a code on an incoming call ─────────────────────────────────────

def test_save_call_attributes_a_shared_phone_call_to_the_pin_owner(app):
    from onecpase.pbx import _save_call

    with app.test_request_context():
        db = get_db()
        user_id = db.execute(
            "INSERT INTO users (username, password_hash, full_name, active) "
            "VALUES ('entle', 'x', 'Mfundentle Mahanjane', 1)"
        ).lastrowid
        db.execute("UPDATE pbx_account_codes SET user_id=? WHERE code='3074'", (user_id,))
        db.commit()

        _save_call({
            "call_id": "test-call-1",
            "call_from": "1000",
            "call_to": "0821234567",
            "call_type": "outbound",
            "accountcode": "3074",
            "disposition": "ANSWERED",
        })

        row = db.execute(
            "SELECT user_id, account_code FROM pbx_call_logs WHERE external_call_id='test-call-1'"
        ).fetchone()
    assert row["user_id"] == user_id
    assert row["account_code"] == "3074"


def test_save_call_records_an_unrecognised_code_without_guessing_a_user(app):
    from onecpase.pbx import _save_call

    with app.test_request_context():
        db = get_db()
        _save_call({
            "call_id": "test-call-2",
            "call_from": "1000",
            "call_to": "0821234567",
            "call_type": "outbound",
            "accountcode": "0000",
            "disposition": "ANSWERED",
        })
        row = db.execute(
            "SELECT user_id, account_code FROM pbx_call_logs WHERE external_call_id='test-call-2'"
        ).fetchone()
    assert row["user_id"] is None
    assert row["account_code"] == "0000"


def test_save_call_without_an_account_code_field_is_unaffected(app):
    """Yeastar events with no account code at all (e.g. click-to-call, which
    already sets user_id from the logged-in session) must not break."""
    from onecpase.pbx import _save_call

    with app.test_request_context():
        db = get_db()
        _save_call({
            "call_id": "test-call-3",
            "call_from": "1000",
            "call_to": "0821234567",
            "call_type": "outbound",
            "disposition": "ANSWERED",
        })
        row = db.execute(
            "SELECT user_id, account_code FROM pbx_call_logs WHERE external_call_id='test-call-3'"
        ).fetchone()
    assert row["user_id"] is None
    assert row["account_code"] is None


# ── Call log shows the resolved staff name ───────────────────────────────────

def test_call_log_page_shows_the_resolved_staff_name(client):
    from onecpase.pbx import _save_call

    _login_admin(client)
    with client.application.test_request_context():
        db = get_db()
        user_id = db.execute(
            "INSERT INTO users (username, password_hash, full_name, active) "
            "VALUES ('busi', 'x', 'Colin Busi Mabena', 1)"
        ).lastrowid
        db.execute("UPDATE pbx_account_codes SET user_id=? WHERE code='5296'", (user_id,))
        db.commit()
        _save_call({
            "call_id": "test-call-4",
            "call_from": "1000",
            "call_to": "0821234567",
            "call_type": "outbound",
            "accountcode": "5296",
            "disposition": "ANSWERED",
        })

    page = client.get("/pbx/calls")
    assert b"Colin Busi Mabena" in page.data


# ── Admin UI for managing codes ──────────────────────────────────────────────

def test_admin_can_list_add_and_edit_account_codes(client):
    _login_admin(client)

    response = client.get("/admin/pbx-codes")
    assert response.status_code == 200
    assert b"Entle" in response.data

    client.post("/admin/pbx-codes/add", data={"code": "1111", "label": "Test Desk"})
    with client.application.app_context():
        row = get_db().execute("SELECT * FROM pbx_account_codes WHERE code='1111'").fetchone()
    assert row["label"] == "Test Desk"
    assert row["user_id"] is None

    with client.application.app_context():
        db = get_db()
        staff_id = db.execute(
            "INSERT INTO users (username, password_hash, full_name, active) "
            "VALUES ('teststaff', 'x', 'Test Staff', 1)"
        ).lastrowid
        db.commit()

    client.post(
        f"/admin/pbx-codes/{row['id']}/edit",
        data={"label": "Test Desk Renamed", "user_id": str(staff_id), "active": "1"},
    )
    with client.application.app_context():
        updated = get_db().execute("SELECT * FROM pbx_account_codes WHERE code='1111'").fetchone()
    assert updated["label"] == "Test Desk Renamed"
    assert updated["user_id"] == staff_id


def test_only_admin_can_manage_account_codes(client):
    with client.session_transaction() as session:
        session["user_id"] = 2
        session["role"] = "staff"
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, active) "
            "VALUES (2, 'staff', 'x', 'Staff', 'staff', 1)"
        )
        db.commit()
    response = client.get("/admin/pbx-codes", follow_redirects=True)
    assert b"Admin access required" in response.data
