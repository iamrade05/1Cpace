"""The 15-minute collections sweep must not write new records when nothing changed."""
from datetime import date, timedelta

import pytest

from onecpase.database import get_db
from onecpase.ptp import _run_collections_automations, bulk_member_arrears

TODAY = date.today().isoformat()


def _member(db, ref="1", **columns):
    values = dict(member_ref=f"ELE-{ref}", first_name="Sweep", last_name="Member", id_number=f"ID{ref}",
                  member_status="Active", monthly_installment=599, join_date="2020-01-01", **columns)
    return db.execute(
        f"INSERT INTO members ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", tuple(values.values()),
    ).lastrowid


def _charge(db, member_id, day, status="pending", paid=0, notes="Recurring Fee"):
    db.execute(
        """INSERT INTO collections (member_id, outstanding_balance, amount_paid, collection_date, method, status, notes)
           VALUES (?, 599, ?, ?, 'debit', ?, ?)""",
        (member_id, paid, day, status, notes),
    )


def _no_arrears_member(db, **columns):
    """An unpaid row the sweep selects, but matched payments leave nothing owing."""
    member_id = _member(db, **columns)
    _charge(db, member_id, (date.today() + timedelta(days=20)).isoformat())
    assert bulk_member_arrears(db, [member_id])[member_id]["total_arrears"] == 0
    return member_id


def _owing_member(db, ref="2"):
    member_id = _member(db, ref)
    _charge(db, member_id, (date.today() - timedelta(days=40)).isoformat(), notes="Ledger | type=Recurring Fee | Monthly fee")
    assert bulk_member_arrears(db, [member_id])[member_id]["total_arrears"] > 0
    return member_id


def _count(db, table, member_id):
    return db.execute(f"SELECT COUNT(*) FROM {table} WHERE member_id=?", (member_id,)).fetchone()[0]


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def test_a_member_owing_nothing_gets_no_case_however_often_the_sweep_runs(db):
    member_id = _no_arrears_member(db)

    for _ in range(3):
        _run_collections_automations(db, TODAY)
        db.commit()

    assert _count(db, "collections_cases", member_id) == 0
    assert _count(db, "access_decisions", member_id) == 0
    assert _count(db, "collection_assignment_history", member_id) == 0
    assert _count(db, "collection_notes", member_id) == 0


def test_a_member_who_owes_keeps_one_case_and_one_decision(db):
    member_id = _owing_member(db)

    for _ in range(3):
        _run_collections_automations(db, TODAY)
        db.commit()

    cases = db.execute("SELECT status FROM collections_cases WHERE member_id=?", (member_id,)).fetchall()
    assert [row["status"] for row in cases] in (["open"], ["active"], ["pending"])
    assert _count(db, "access_decisions", member_id) == 1
    assert _count(db, "collection_assignment_history", member_id) == 1


def test_a_changed_decision_is_still_logged(db):
    from onecpase.collections_engine import record_access_decision

    member_id = _member(db)
    record_access_decision(db, member_id, "ALLOWED", "ok", case_id=None)
    record_access_decision(db, member_id, "allowed", "ok", case_id=None)
    record_access_decision(db, member_id, "BLOCKED", "arrears", case_id=None)
    record_access_decision(db, member_id, "ALLOWED", "ok", case_id=None)

    assert _count(db, "access_decisions", member_id) == 3


def test_access_is_still_restored_for_a_member_who_owes_nothing(db):
    member_id = _no_arrears_member(db, gym_access_status="blocked", access_block_reason="arrears_no_arrangement")

    _run_collections_automations(db, TODAY)
    db.commit()

    member = db.execute("SELECT gym_access_status, access_block_reason FROM members WHERE id=?", (member_id,)).fetchone()
    assert member["gym_access_status"] == "allowed"
    assert member["access_block_reason"] is None
    assert _count(db, "collections_cases", member_id) == 0


def test_an_open_case_is_still_closed_once_the_member_owes_nothing(db):
    member_id = _owing_member(db)
    _run_collections_automations(db, TODAY)
    db.commit()

    db.execute("UPDATE collections SET status='paid', amount_paid=599, notes='Paid' WHERE member_id=?", (member_id,))
    _charge(db, member_id, (date.today() + timedelta(days=20)).isoformat())
    assert bulk_member_arrears(db, [member_id])[member_id]["total_arrears"] == 0
    _run_collections_automations(db, TODAY)
    db.commit()

    case = db.execute("SELECT status, resolution_type FROM collections_cases WHERE member_id=?", (member_id,)).fetchone()
    assert case["status"] == "closed" and case["resolution_type"] == "PAID_IN_FULL"
    assert _count(db, "collections_cases", member_id) == 1
