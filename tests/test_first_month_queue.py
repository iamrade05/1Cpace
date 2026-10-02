from onecpase.collections_engine import sync_collection_case
from onecpase.database import get_db
from onecpase.phases import enabled_blueprints


def _user(db, uid, name, role="staff"):
    db.execute(
        """INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, department)
           VALUES (?, ?, 'x', ?, ?, 'Sales')""",
        (uid, f"cons{uid}", name, role),
    )


def _sales_case(db, mid, consultant):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, join_date, gym_access_status)
           VALUES (?, ?, 'Queue', ?, ?, 'Active', '2026-09-20', 'allowed')""",
        (f"FM-{mid}", f"Mem{mid}", f"ID-FM-{mid}", f"0730000{mid:03d}"),
    )
    member_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("INSERT INTO membership_applications (member_id, created_by) VALUES (?, ?)",
               (member_id, consultant))
    case_id = sync_collection_case(db, member_id, failed_debit_date="2026-10-01",
                                   arrears_amount=324, months_owing=1)
    db.commit()
    return case_id


def _as(client, uid, role="staff"):
    with client.session_transaction() as session:
        session["user_id"] = uid
        session["role"] = role
        session["username"] = f"user{uid}"


def test_queue_is_open_in_phase_one():
    assert "first_month_collections" in enabled_blueprints(1)
    assert "collections" not in enabled_blueprints(1)


def test_consultant_sees_only_their_own_cases(app, client):
    with app.app_context():
        db = get_db()
        _user(db, 71, "Thandi Sales")
        _user(db, 72, "Sipho Sales")
        _sales_case(db, 1, 71)
        _sales_case(db, 2, 72)
    _as(client, 71)
    page = client.get("/sales/first-month-failed-debits/").get_data(as_text=True)
    assert "Mem1 Queue" in page
    assert "Mem2 Queue" not in page


def test_manager_sees_every_consultants_cases(app, client):
    with app.app_context():
        db = get_db()
        _user(db, 71, "Thandi Sales")
        _user(db, 72, "Sipho Sales")
        _sales_case(db, 1, 71)
        _sales_case(db, 2, 72)
    _as(client, 1, role="manager")
    page = client.get("/sales/first-month-failed-debits/").get_data(as_text=True)
    assert "Mem1 Queue" in page and "Mem2 Queue" in page


def test_consultant_records_contact_on_own_case(app, client):
    with app.app_context():
        db = get_db()
        _user(db, 71, "Thandi Sales")
        case_id = _sales_case(db, 1, 71)
    _as(client, 71)
    client.post(f"/sales/first-month-failed-debits/{case_id}/contact",
                data={"channel": "CALL", "outcome": "promised_to_pay", "notes": "Pays Friday"})
    with app.app_context():
        row = get_db().execute(
            "SELECT channel, outcome, created_by FROM collection_communications WHERE case_id=?",
            (case_id,)).fetchone()
        assert (row["channel"], row["outcome"], row["created_by"]) == ("CALL", "promised_to_pay", 71)


def test_consultant_cannot_record_on_someone_elses_case(app, client):
    with app.app_context():
        db = get_db()
        _user(db, 71, "Thandi Sales")
        _user(db, 72, "Sipho Sales")
        case_id = _sales_case(db, 2, 72)
    _as(client, 71)
    client.post(f"/sales/first-month-failed-debits/{case_id}/contact",
                data={"channel": "CALL", "outcome": "no_answer"})
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM collection_communications WHERE case_id=?", (case_id,)
        ).fetchone()[0] == 0


def test_refusal_requires_a_note(app, client):
    with app.app_context():
        db = get_db()
        _user(db, 71, "Thandi Sales")
        case_id = _sales_case(db, 1, 71)
    _as(client, 71)
    client.post(f"/sales/first-month-failed-debits/{case_id}/contact",
                data={"channel": "CALL", "outcome": "refused", "notes": ""})
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM collection_communications WHERE case_id=?", (case_id,)
        ).fetchone()[0] == 0
