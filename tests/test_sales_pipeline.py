from onecpase.database import get_db
from onecpase.sales_pipeline import (
    current_sales_stage,
    transition_sales_stage,
)


def _user(db, username="pipeline-test"):
    db.execute(
        "INSERT INTO users (username, password_hash, full_name) VALUES (?, ?, ?)",
        (username, "hash", username),
    )
    return db.execute(
        "SELECT id FROM users WHERE username=?", (username,)
    ).fetchone()[0]


def _lead(db, user_id, **extra):
    cols = ["full_name", "assigned_to", "lead_status"]
    vals = ["Pipeline Test Lead", user_id, "captured"]
    for key, value in extra.items():
        cols.append(key)
        vals.append(value)
    marks = ", ".join(["?"] * len(vals))
    db.execute(
        f"INSERT INTO leads ({', '.join(cols)}) VALUES ({marks})", vals
    )
    return db.execute(
        "SELECT id FROM leads WHERE full_name='Pipeline Test Lead' ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]


def test_decision_follow_up_is_distinct_from_consultation(app):
    with app.app_context():
        db = get_db()
        user_id = _user(db, "decision-user")
        lead_id = _lead(db, user_id)

        transition_sales_stage(db, lead_id, "ASSIGNED", user_id)
        transition_sales_stage(db, lead_id, "CONTACT_ATTEMPTED", user_id)
        transition_sales_stage(db, lead_id, "CONTACTED", user_id)

        db.execute(
            """INSERT INTO lead_qualifications
               (lead_id, interested, needs_identified, ready_to_join,
                qualified_by, qualified_at)
               VALUES (?, 1, 1, 1, ?, '2026-09-01 10:00:00')""",
            (lead_id, user_id),
        )
        db.commit()
        transition_sales_stage(db, lead_id, "QUALIFIED", user_id)
        transition_sales_stage(db, lead_id, "INVITED", user_id)
        db.execute(
            """INSERT INTO sales_appointments
               (lead_id, appointment_at, status)
               VALUES (?, '2026-09-02 10:00:00', 'BOOKED')""",
            (lead_id,),
        )
        db.commit()
        transition_sales_stage(db, lead_id, "APPOINTMENT_BOOKED", user_id)
        transition_sales_stage(db, lead_id, "SHOW", user_id)
        transition_sales_stage(db, lead_id, "CONSULTATION", user_id)
        transition_sales_stage(db, lead_id, "DECISION_FOLLOW_UP", user_id)

        lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        assert current_sales_stage(lead) == "DECISION_FOLLOW_UP"
        assert lead["decision_follow_up_at"]
        assert db.execute(
            """SELECT to_stage FROM sales_stage_history
               WHERE lead_id=? ORDER BY id DESC LIMIT 1""",
            (lead_id,),
        ).fetchone()[0] == "DECISION_FOLLOW_UP"


def test_direct_show_requires_walk_in_source(app):
    with app.app_context():
        db = get_db()
        user_id = _user(db, "walkin-user")

        lead_id = _lead(db, user_id, source="Website")
        try:
            transition_sales_stage(db, lead_id, "SHOW", user_id)
        except ValueError as exc:
            assert "genuine walk-in" in str(exc)
        else:
            raise AssertionError("Non-walk-in leads must not bypass appointment flow")

        db.execute(
            "UPDATE leads SET source='Walk-In / Enquiry', entry_path='direct_show' WHERE id=?",
            (lead_id,),
        )
        db.commit()
        transition_sales_stage(db, lead_id, "SHOW", user_id)
        assert current_sales_stage(
            db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        ) == "SHOW"


def test_canonical_transition_also_writes_stage_event(app):
    with app.app_context():
        db = get_db()
        user_id = _user(db, "event-user")
        lead_id = _lead(db, user_id)
        transition_sales_stage(db, lead_id, "ASSIGNED", user_id, notes="Assigned by rotation")

        event = db.execute(
            """SELECT field, from_value, to_value, actor_id
               FROM stage_events
               WHERE lead_id=? AND field='sales_stage'
               ORDER BY id DESC LIMIT 1""",
            (lead_id,),
        ).fetchone()
        assert tuple(event) == ("sales_stage", "CAPTURED", "ASSIGNED", user_id)
