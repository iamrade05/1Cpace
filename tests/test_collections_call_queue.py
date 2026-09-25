from datetime import date, timedelta

from onecpase.collections import (
    _active_debit_cycle,
    _ensure_daily_call_tasks,
    _scheduled_and_operational_cycle,
)
from onecpase.database import get_db


def _reminder_debit_day(today, reminder_offset=2):
    """Find a debit day whose cycle puts *today* inside the reminder window.

    The call-queue page always works off date.today(), so a test that loads it
    has to anchor its seed to the real current date, not a fixed one."""
    for month_offset in (0, 1):
        month_index = today.year * 12 + today.month - 1 + month_offset
        year, zero_month = divmod(month_index, 12)
        for day in range(1, 29):
            _, operational = _scheduled_and_operational_cycle(year, zero_month + 1, day)
            if operational - timedelta(days=reminder_offset) <= today < operational:
                return day
    raise AssertionError("no debit day puts today inside a reminder window")


def _seed_callers_and_members(member_count=90, debit_day=15):
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
               VALUES (?, 'Cycle', ?, ?, ?, 'Active', ?)""",
            (
                f"ELE-{number + 1:010d}",
                f"Member {number:03d}",
                f"ID{number:04d}",
                f"071{number:07d}",
                str(debit_day),
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
    today = date.today()
    with app.app_context():
        _seed_callers_and_members(2, debit_day=_reminder_debit_day(today))
        db = get_db()
        _ensure_daily_call_tasks(db, today)
        task = db.execute(
            "SELECT id, member_id, assigned_to FROM collection_call_tasks WHERE task_date=? ORDER BY id LIMIT 1",
            (today.isoformat(),),
        ).fetchone()
        assert task is not None
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


# ── Escalation: toggle_escalation delegates to next_communication() ─────────
#
# Previously duplicated next_communication()'s CALL->WHATSAPP->SMS->EMAIL map
# as its own hardcoded dict, and "marked sent" always silently advanced to
# the next channel with no way to record an actual failure - WHATSAPP_FAILED
# /SMS_FAILED (collections_engine.py) were unreachable dead constants.

def _seed_task_awaiting_whatsapp(app):
    """A task that has already had an unsuccessful call outcome recorded,
    so next_channel is WHATSAPP - the state toggle_escalation acts on."""
    with app.app_context():
        _seed_callers_and_members(2)
        db = get_db()
        _ensure_daily_call_tasks(db, date(2026, 8, 12))
        task = db.execute(
            "SELECT id, assigned_to FROM collection_call_tasks ORDER BY id LIMIT 1"
        ).fetchone()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = task["assigned_to"]
            sess["username"] = "collections-caller"
            sess["role"] = "reception"
            sess["permissions"] = ["collections_call_queue"]
        client.post(
            f"/collections/call-queue/{task['id']}/result",
            data={"outcome": "no_answer", "notes": ""},
        )
    with app.app_context():
        row = get_db().execute(
            "SELECT next_channel FROM collection_call_tasks WHERE id=?", (task["id"],)
        ).fetchone()
        assert row["next_channel"] == "WHATSAPP"
    return task["id"], task["assigned_to"]


def _post_escalation(app, task_id, assigned_to, channel, result):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = assigned_to
            sess["username"] = "collections-caller"
            sess["role"] = "reception"
            sess["permissions"] = ["collections_call_queue"]
        return client.post(
            f"/collections/call-queue/{task_id}/escalation",
            data={"channel": channel, "result": result},
        )


def test_marking_whatsapp_sent_leaves_it_on_the_same_channel(app):
    task_id, assigned_to = _seed_task_awaiting_whatsapp(app)
    response = _post_escalation(app, task_id, assigned_to, "whatsapp", "sent")
    assert response.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT next_channel, whatsapp_sent FROM collection_call_tasks WHERE id=?", (task_id,)
        ).fetchone()
        assert row["whatsapp_sent"] == 1
        assert row["next_channel"] == "WHATSAPP"  # unchanged - awaiting a reply or a failure decision


def test_marking_whatsapp_failed_advances_to_sms(app):
    task_id, assigned_to = _seed_task_awaiting_whatsapp(app)
    response = _post_escalation(app, task_id, assigned_to, "whatsapp", "failed")
    assert response.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT next_channel FROM collection_call_tasks WHERE id=?", (task_id,)
        ).fetchone()
        assert row["next_channel"] == "SMS"


def test_marking_sms_failed_advances_to_email(app):
    task_id, assigned_to = _seed_task_awaiting_whatsapp(app)
    _post_escalation(app, task_id, assigned_to, "whatsapp", "failed")
    response = _post_escalation(app, task_id, assigned_to, "sms", "failed")
    assert response.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT next_channel FROM collection_call_tasks WHERE id=?", (task_id,)
        ).fetchone()
        assert row["next_channel"] == "EMAIL"


def test_toggle_escalation_actually_calls_next_communication(app, monkeypatch):
    """Proves wiring, not just matching output: under the old hardcoded
    dict, this input would produce the same values regardless, so a
    black-box test alone couldn't tell 'now calls the engine' apart from
    'still hardcoded'. Monkeypatching the engine function is the only way to
    prove the route actually calls it."""
    from onecpase import collections as collections_module

    task_id, assigned_to = _seed_task_awaiting_whatsapp(app)
    monkeypatch.setattr(collections_module, "next_communication", lambda outcome=None: "CARRIER_PIGEON")
    _post_escalation(app, task_id, assigned_to, "whatsapp", "failed")
    with app.app_context():
        row = get_db().execute(
            "SELECT next_channel FROM collection_call_tasks WHERE id=?", (task_id,)
        ).fetchone()
        assert row["next_channel"] == "CARRIER_PIGEON"


# ── daily_call_progress delegates to calculate_daily_call_kpi() ─────────────

def test_daily_call_progress_delegates_to_the_kpi_engine(app, monkeypatch):
    from onecpase import collections as collections_module

    with app.app_context():
        _seed_callers_and_members(2)
        db = get_db()
        _ensure_daily_call_tasks(db, date(2026, 8, 12))
        task = db.execute(
            "SELECT id FROM collection_call_tasks WHERE task_date='2026-08-12' ORDER BY id LIMIT 1"
        ).fetchone()
        db.execute(
            "UPDATE collection_call_tasks SET status='completed', successful_contact=1 WHERE id=?",
            (task["id"],),
        )
        db.commit()

        monkeypatch.setattr(
            collections_module, "calculate_daily_call_kpi",
            lambda *a, **k: {"completion_percentage": 12.34, "contact_rate": 56.78,
                              "target": 0, "completed": 0, "remaining": 0,
                              "calls_attempted": 0, "contacts": 0},
        )
        result = collections_module.daily_call_progress(db, today=date(2026, 8, 12))
        # Fails on the old code: the function isn't imported/called at all,
        # so the hand-computed real percentages come back instead.
        assert result["target_progress_pct"] == 12.3   # rounded to the dashboard's 1dp, not the engine's 2dp
        assert result["contact_rate_pct"] == 56.8


def test_daily_call_progress_real_percentages_are_unchanged(app):
    # Behavioral regression guard, unpatched: the real numbers the dashboard
    # shows today must come out the same after routing through the engine.
    from onecpase.collections import daily_call_progress

    with app.app_context():
        _seed_callers_and_members(10)
        db = get_db()
        _ensure_daily_call_tasks(db, date(2026, 8, 12))
        tasks = db.execute(
            "SELECT id FROM collection_call_tasks WHERE task_date='2026-08-12'"
        ).fetchall()
        assert len(tasks) >= 4, "seed must produce enough tasks to complete 4 of them"
        for task in tasks[:4]:
            db.execute(
                "UPDATE collection_call_tasks SET status='completed', successful_contact=1 WHERE id=?",
                (task["id"],),
            )
        db.commit()

        result = daily_call_progress(db, today=date(2026, 8, 12))
        assert result["calls"] == 4
        assert result["contacts"] == 4
        assert result["daily_target"] == 80  # 40 per caller x 2 callers
        assert result["target_progress_pct"] == 5.0    # 4/80 * 100
        assert result["contact_rate_pct"] == 100.0      # 4/4 * 100
