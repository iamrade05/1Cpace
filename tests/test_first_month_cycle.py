"""Members who joined in a 15th-14th cycle, their failed debits, and who signed them."""
from onecpase.collections_engine import find_original_sales_consultant, staff_id_for_name
from onecpase.database import get_db
from onecpase.first_month_collections import cycle_failed_debits
from scripts.import_itensity_members import roster_member_values

CYCLE = "2026-01"


def _user(db, user_id, name, role="sales"):
    db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, full_name, role, active) VALUES (?, ?, 'x', ?, ?, 1)",
        (user_id, f"user{user_id}", name, role),
    )


def _member(db, ref, join_date, sold_by=None, payment_type="Debit Order"):
    return db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, join_date, payment_type, sold_by, member_status)
           VALUES (?, 'New', 'Member', ?, ?, ?, ?, 'Active')""",
        (f"ELE-{ref}", f"ID{ref}", join_date, payment_type, sold_by),
    ).lastrowid


def _collection(db, member_id, day, status="failed", purpose="general"):
    db.execute(
        """INSERT INTO collections (member_id, outstanding_balance, amount_paid, collection_date, method, status, payment_purpose)
           VALUES (?, 599, 0, ?, 'debit', ?, ?)""",
        (member_id, day, status, purpose),
    )


def _login(client, user_id=1, role="admin"):
    with client.session_transaction() as sess:
        sess.update({"user_id": user_id, "username": "u", "role": role})


def test_only_members_joined_in_the_cycle_with_a_debit_order_count(app):
    with app.test_request_context():
        db = get_db()
        inside = _member(db, 1, "2025-12-15", "Staff One")
        _member(db, 2, "2026-01-14", "Staff One")
        before = _member(db, 3, "2025-12-14", "Staff One")
        after = _member(db, 4, "2026-01-15", "Staff One")
        _member(db, 5, "2026-01-01", "Staff One", payment_type="EFT/Cash")
        for member_id in (inside, before, after):
            _collection(db, member_id, "2026-01-20")
        from flask import session
        session.update(role="admin")

        cycle = cycle_failed_debits(db, CYCLE)

    assert cycle["joined"] == 2
    assert [m["id"] for m in cycle["failed"]] == [inside]
    assert cycle["rate"] == 50.0
    assert cycle["consultants"] == [{"name": "Staff One", "joined": 2, "failed": 1}]


def test_failures_before_joining_and_registration_fees_are_not_first_month_debits(app):
    with app.test_request_context():
        db = get_db()
        rejoined = _member(db, 1, "2026-01-05", "Staff One")
        _collection(db, rejoined, "2025-09-01")
        fee = _member(db, 2, "2026-01-05", "Staff One")
        _collection(db, fee, "2026-01-06", purpose="registration_fee")
        paid = _member(db, 3, "2026-01-05", "Staff One")
        _collection(db, paid, "2026-01-06", status="paid")
        from flask import session
        session.update(role="admin")

        assert cycle_failed_debits(db, CYCLE)["failed"] == []


def test_failures_are_grouped_under_who_signed_them_ignoring_name_spacing(app):
    with app.test_request_context():
        db = get_db()
        for ref, name in ((1, "Tholoane  Mashiane"), (2, "Tholoane Mashiane"), (3, None)):
            _collection(db, _member(db, ref, "2026-01-05", name), "2026-01-20")
        from flask import session
        session.update(role="admin")

        rows = cycle_failed_debits(db, CYCLE)["consultants"]

    assert {r["name"]: r["failed"] for r in rows} == {"Tholoane Mashiane": 2, "Not recorded": 1}


def test_a_lead_owner_outranks_the_itensity_sales_name(app):
    with app.test_request_context():
        db = get_db()
        _user(db, 7, "Lead Owner")
        member_id = _member(db, 1, "2026-01-05", "Someone Else")
        lead_id = db.execute("INSERT INTO leads (full_name, phone, assigned_to) VALUES ('P', '0820000000', 7)").lastrowid
        db.execute("INSERT INTO membership_applications (member_id, lead_id, package) VALUES (?, ?, 'x')", (member_id, lead_id))
        _collection(db, member_id, "2026-01-20")
        from flask import session
        session.update(role="admin")

        assert cycle_failed_debits(db, CYCLE)["failed"][0]["signed_by"] == "Lead Owner"
        assert find_original_sales_consultant(db, member_id) == 7


def test_the_signing_consultant_comes_from_the_itensity_sales_name(app):
    with app.app_context():
        db = get_db()
        _user(db, 7, "Tholoane Mashiane")
        _user(db, 8, "Same Name")
        _user(db, 9, "Same  Name")
        known = _member(db, 1, "2026-01-05", "Tholoane  Mashiane")
        unknown = _member(db, 2, "2026-01-05", "Left The Company")

        assert find_original_sales_consultant(db, known) == 7
        assert find_original_sales_consultant(db, unknown) is None
        assert staff_id_for_name(db, "Same Name") is None      # two people: never guess


def test_a_consultant_sees_only_their_own_failures_and_a_manager_sees_all(app, client):
    with app.app_context():
        db = get_db()
        _user(db, 1, "Staff One")
        _user(db, 2, "Staff Two")
        mine = _member(db, 1, "2026-01-05", "Staff  One")
        theirs = _member(db, 2, "2026-01-05", "Staff Two")
        db.execute("UPDATE members SET first_name='Mine' WHERE id=?", (mine,))
        db.execute("UPDATE members SET first_name='Theirs' WHERE id=?", (theirs,))
        _collection(db, mine, "2026-01-20")
        _collection(db, theirs, "2026-01-20")
        db.commit()

    _login(client, 1, "sales")
    own = client.get(f"/sales/first-month-failed-debits/?cycle={CYCLE}").get_data(as_text=True)
    assert "Mine Member" in own and "Theirs Member" not in own

    _login(client, 2, "admin")
    everyone = client.get(f"/sales/first-month-failed-debits/?cycle={CYCLE}").get_data(as_text=True)
    assert "Mine Member" in everyone and "Theirs Member" in everyone
    assert "15 Dec 2025" in everyone and "14 Jan 2026" in everyone


def test_a_bad_cycle_falls_back_to_the_current_one(app, client):
    _login(client)
    for bad in ("garbage", "2099-01", "2026-13"):
        response = client.get(f"/sales/first-month-failed-debits/?cycle={bad}")
        assert response.status_code == 200
        assert "Current cycle" in response.get_data(as_text=True)


def test_the_roster_import_keeps_the_sales_name(app):
    record = {"ref": "2167518", "roster": {"name": "New Member", "sales": "Roster Name"},
              "extract": {"salename": "Tholoane  Mashiane"}, "membership": {"sales": "Other"}}
    assert roster_member_values(record)["sold_by"] == "Tholoane Mashiane"
    del record["extract"]
    assert roster_member_values(record)["sold_by"] == "Other"
