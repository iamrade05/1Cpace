from datetime import date

from onecpase import nupay_transactions as nt
from onecpase.database import get_db
from onecpase.ptp import bulk_member_arrears

AMOUNT = 345.58


def _member(db, number, itensity_ref):
    return db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact, member_status, itensity_ref)
           VALUES (?, 'Test', ?, ?, ?, 'Active', ?)""",
        (f"ELE-{number:010d}", f"Member{number}", f"ID{number:011d}", f"07100{number:05d}", itensity_ref),
    ).lastrowid


def _charge(db, mid, day, amount=AMOUNT, status="pending"):
    db.execute(
        """INSERT INTO collections (member_id, outstanding_balance, amount_paid, collection_date, method, status, notes)
           VALUES (?, ?, 0, ?, 'other', ?, 'Itensity | type=Recurring Fee | description=Recurring Fee: Premium 12')""",
        (mid, amount, day, status),
    )


def _row(ref, action, instalment, status="Success", cycle=None, amount=AMOUNT, **extra):
    return {
        "Mandate ID": "71078982", "Debtor ID": "", "Instalment Amount": str(amount), "Action Date": action,
        "Status": status, "Client Reference": ref, "Instalment": str(instalment), "Total Instalments": "12",
        "Cycle Date": cycle or action[:8] + "20", "Contract Reference": "DCPRD000241NBL", **extra,
    }


def _arrears(db, mid):
    return bulk_member_arrears(db, [mid]).get(mid, {}).get("total_arrears", 0)


def _record(db, rows):
    plan = nt.plan_import(db, rows)
    nt.apply_plan(db, plan)
    db.commit()
    return plan


FOUR_SUCCESSES = [
    _row("1950298", "2026-06-19", 1, cycle="2026-06-20"),
    _row("1950298", "2026-07-20", 2),
    _row("1950298", "2026-08-20", 3),
    _row("1950298", "2026-09-18", 4, cycle="2026-09-20"),
]


def test_four_successful_debits_leave_a_paying_member_with_no_arrears(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 1, "1950298")
        plan = _record(db, FOUR_SUCCESSES)
        assert plan["summary"]["charges_added"] == 4 and plan["summary"]["payments_added"] == 4
        assert _arrears(db, mid) == 0


def test_payments_never_clear_older_debt_without_their_own_charge(app):
    """Her case: 13 old unpaid charges plus a new contract that has paid four times."""
    with app.app_context():
        db = get_db()
        mid = _member(db, 2, "1950298")
        # Jun 2025 - Apr 2026, with September and October billed twice: 13 rows, as on her record.
        for day in ("2025-06-20", "2025-07-20", "2025-08-20", "2025-09-20", "2025-09-20", "2025-10-20",
                    "2025-10-20", "2025-11-20", "2025-12-20", "2026-01-20", "2026-02-20", "2026-03-20", "2026-04-20"):
            _charge(db, mid, day)
        db.commit()
        old_total = round(13 * AMOUNT, 2)
        assert _arrears(db, mid) == old_total
        _record(db, FOUR_SUCCESSES)
        # Payments came in with their own charges, so the old debt is neither hidden
        # nor "paid" by the new contract. Whether it was settled is for a person to prove.
        assert round(_arrears(db, mid), 2) == old_total


def test_running_it_twice_adds_nothing(app):
    with app.app_context():
        db = get_db()
        _member(db, 3, "1950298")
        _record(db, FOUR_SUCCESSES)
        again = nt.plan_import(db, FOUR_SUCCESSES)
        assert again["actions"] == [] and again["summary"]["already_recorded"] == 4
        assert db.execute("SELECT COUNT(*) FROM collections").fetchone()[0] == 8


def test_an_existing_charge_is_settled_not_duplicated(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 4, "1950298")
        _charge(db, mid, "2026-06-20")
        db.commit()
        plan = _record(db, [FOUR_SUCCESSES[0]])
        assert plan["summary"]["charges_added"] == 0 and plan["summary"]["payments_added"] == 1
        assert db.execute("SELECT COUNT(*) FROM collections WHERE notes LIKE '%type=Recurring Fee%'").fetchone()[0] == 1
        assert _arrears(db, mid) == 0


def test_a_failed_debit_marks_the_charge_failed_and_is_idempotent(app):
    with app.app_context():
        db = get_db()
        with_charge = _member(db, 5, "1111111")
        without_charge = _member(db, 6, "2222222")
        _charge(db, with_charge, "2026-09-20")
        db.commit()
        rows = [_row("1111111", "2026-09-18", 4, status="Failed", cycle="2026-09-20"),
                _row("2222222", "2026-09-18", 4, status="Failed", cycle="2026-09-20")]
        _record(db, rows)
        status = lambda mid: db.execute("SELECT status FROM collections WHERE member_id=?", (mid,)).fetchone()["status"]
        assert status(with_charge) == "failed" and status(without_charge) == "failed"
        assert _arrears(db, without_charge) == AMOUNT
        assert nt.plan_import(db, rows)["actions"] == []


def test_unmatched_reversed_and_future_rows_are_not_posted(app):
    with app.app_context():
        db = get_db()
        _member(db, 7, "1950298")
        rows = [_row("9999999", "2026-09-18", 4),
                _row("1950298", "2026-09-18", 4, status="Reversed"),
                _row("1950298", "2026-10-20", 5, status="Future"),
                _row("1950298", "2026-10-19", 5, status="In Tracking")]
        plan = nt.plan_import(db, rows)
        assert plan["actions"] == []
        assert plan["summary"]["unmatched"] == 1 and plan["summary"]["needs_review"] == 1 and plan["summary"]["ignored"] == 2
        assert plan["unmatched"]["9999999"] == 1


def test_csv_headers_are_read_however_nupay_spells_them(app):
    csv_text = "﻿Client Reference,Instalment Amount,Action Date,Status,Mandate ID,Instalment,Cycle Date\n1950298,345.58,2026-06-19,Success,71078982,1,2026-06-20\n"
    rows = nt.parse_csv(csv_text)
    assert rows[0]["client_reference"] == "1950298" and rows[0]["status"] == "Success"
    with app.app_context():
        db = get_db()
        _member(db, 8, "1950298")
        assert nt.plan_import(db, rows)["summary"]["payments_added"] == 1


def test_members_paying_on_nupay_but_in_arrears_are_listed(app):
    with app.app_context():
        db = get_db()
        paying = _member(db, 9, "3333333")
        quiet = _member(db, 10, "4444444")
        for mid in (paying, quiet):
            _charge(db, mid, "2026-08-20")
        db.commit()
        rows = [_row("3333333", "2026-09-18", 4)]
        flagged = nt.paying_but_in_arrears(db, rows, today=date(2026, 10, 6))
        assert [item["member_id"] for item in flagged] == [paying]
        assert flagged[0]["total_arrears"] == AMOUNT
        # Outside the window it is no longer "recently paying".
        assert nt.paying_but_in_arrears(db, rows, today=date(2027, 1, 1)) == []
