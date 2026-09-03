from datetime import date

from onecpase.collections import _active_debit_cycle, _ensure_daily_call_tasks
from onecpase.database import get_db


def _seed_callers_and_members(member_count=90):
    db = get_db()
    for uid, name, caller in (
        (10, "Mfundentle Mahanjane", 1),
        (11, "Colin Busi Mabena", 1),
        (12, "Not A Collections Caller", 0),
    ):
        db.execute(
            """INSERT INTO users
               (id, username, password_hash, full_name, role, department, active,
                is_collections_caller)
               VALUES (?, ?, 'x', ?, 'reception', 'Reception', 1, ?)""",
            (uid, f"caller{uid}", name, caller),
        )
    for number in range(member_count):
        cursor = db.execute(
            """INSERT INTO members
               (member_ref, first_name, last_name, id_number, contact,
                member_status, debit_order_date)
               VALUES (?, 'Cycle', ?, ?, ?, 'Active', '15')""",
            (
                f"ELE-{number + 1:010d}",
                f"Member {number:03d}",
                f"ID{number:04d}",
                f"071{number:07d}",
            ),
        )
        # notes must carry the Itensity `type=` tag the reconciled balance
        # calculation (_build_payment_profile) keys off of — matches real
        # imported data shape, not a bare/untagged manual entry.
        if number >= 85:
            db.execute(
                """INSERT INTO collections
                   (member_id, outstanding_balance, amount_paid, collection_date, status, notes)
                   VALUES (?, 350, 0, '2026-07-15', 'failed', 'type=Recurring Fee')""",
                (cursor.lastrowid,),
            )
        else:
            db.execute(
                """INSERT INTO collections
                   (member_id, outstanding_balance, amount_paid, collection_date, status, notes)
                   VALUES (?, 250, 0, '2026-07-15', 'pending', 'type=Recurring Fee')""",
                (cursor.lastrowid,),
            )
    db.commit()


def test_weekend_cycle_uses_friday_and_two_reminder_days(app):
    with app.app_context():
        _seed_callers_and_members(2)
        cycle = _active_debit_cycle(get_db(), date(2026, 8, 12))
        assert cycle["scheduled"] == date(2026, 8, 15)
        assert cycle["operational"] == date(2026, 8, 14)
        assert cycle["phase"] == "reminder"


def test_only_configured_callers_receive_40_daily_calls(app):
    with app.app_context():
        _seed_callers_and_members(90)
        db = get_db()
        _ensure_daily_call_tasks(db, date(2026, 8, 12))
        rows = db.execute(
            """SELECT assigned_to, COUNT(*) AS total
               FROM collection_call_tasks GROUP BY assigned_to ORDER BY assigned_to"""
        ).fetchall()
        assert [(row["assigned_to"], row["total"]) for row in rows] == [(10, 40), (11, 40)]
        assert db.execute(
            "SELECT COUNT(*) FROM collection_call_tasks WHERE assigned_to=12"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM collection_call_tasks WHERE queue_type='reminder'"
        ).fetchone()[0] == 80


def test_call_result_counts_attempt_and_successful_contact(app):
    with app.app_context():
        _seed_callers_and_members(2)
        db = get_db()
        _ensure_daily_call_tasks(db, date(2026, 8, 12))
        task = db.execute(
            "SELECT id, assigned_to FROM collection_call_tasks ORDER BY id LIMIT 1"
        ).fetchone()
        assert task is not None

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = task["assigned_to"]
            sess["username"] = "collections-caller"
            sess["role"] = "reception"
            sess["permissions"] = ["collections_call_queue"]
        response = client.post(
            f"/collections/call-queue/{task['id']}/result",
            data={"outcome": "reminder_delivered", "notes": "Reminder confirmed"},
        )
        assert response.status_code == 302
        row = get_db().execute(
            "SELECT status, successful_contact FROM collection_call_tasks WHERE id=?",
            (task["id"],),
        ).fetchone()
        assert row["status"] == "completed"
        assert row["successful_contact"] == 1


def test_call_list_opens_client_brief_profile(app):
    with app.app_context():
        _seed_callers_and_members(2)
        db = get_db()
        _ensure_daily_call_tasks(db, date(2026, 8, 12))
        task = db.execute(
            "SELECT id, member_id, assigned_to FROM collection_call_tasks ORDER BY id LIMIT 1"
        ).fetchone()
        db.execute(
            """INSERT INTO turnstile_events
               (raw_report_hex, member_id, decision, direction, created_at)
               VALUES ('00', ?, 'allowed', 'entry', datetime('now'))""",
            (task["member_id"],),
        )
        db.commit()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = task["assigned_to"]
            sess["username"] = "collections-caller"
            sess["role"] = "reception"
            sess["permissions"] = ["collections_call_queue"]
        queue = client.get("/collections/call-queue")
        assert b"Open Client Profile" in queue.data
        profile = client.get(f"/collections/call-queue/{task['id']}/profile")
        assert profile.status_code == 200
        for text in (
            b"Total Owing", b"Months Owing", b"Failed Payments",
            b"DebiCheck in place", b"Last Gym Visit", b"Payment Arrangements",
            b"Gym Activity &amp; Queries", b"Record Today",
        ):
            assert text in profile.data


def test_zero_balance_member_is_excluded_and_paid_task_is_removed(app):
    with app.app_context():
        _seed_callers_and_members(3)
        db = get_db()
        paid_member = db.execute(
            "SELECT id FROM members ORDER BY id LIMIT 1"
        ).fetchone()["id"]
        db.execute(
            """UPDATE collections SET amount_paid=outstanding_balance, status='paid'
               WHERE member_id=?""", (paid_member,)
        )
        db.commit()

        _ensure_daily_call_tasks(db, date(2026, 8, 12))
        assert db.execute(
            "SELECT COUNT(*) FROM collection_call_tasks WHERE member_id=?", (paid_member,)
        ).fetchone()[0] == 0

        task = db.execute(
            "SELECT id, member_id FROM collection_call_tasks ORDER BY id LIMIT 1"
        ).fetchone()
        db.execute(
            """UPDATE collections SET amount_paid=outstanding_balance, status='paid'
               WHERE member_id=?""", (task["member_id"],)
        )
        db.commit()
        _ensure_daily_call_tasks(db, date(2026, 8, 12))
        assert db.execute(
            "SELECT COUNT(*) FROM collection_call_tasks WHERE id=?", (task["id"],)
        ).fetchone()[0] == 0
