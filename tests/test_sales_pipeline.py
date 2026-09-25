from onecpase.database import get_db
from onecpase.sales_pipeline import (
    current_sales_stage,
    transition_sales_stage,
)


def _login(client, role, department="Sales"):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = role
        session["permissions"] = ["capture_leads"]
    with client.application.app_context():
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO users
               (id, username, password_hash, full_name, role, department, active)
               VALUES (1, 'pipeline-actor', 'x', 'Pipeline Actor', ?, ?, 1)""",
            (role, department),
        )
        db.commit()


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


# ── Manual stage moves are a manager action, not a consultant one ────────────
# capture_leads is granted to every Sales department member, consultants
# included, so the permission check alone does not stop a consultant from
# manually overriding the controlled stage. Only the role check in the
# transition_stage view does.

def test_consultant_cannot_manually_move_the_stage(client):
    _login(client, "staff")
    with client.application.app_context():
        db = get_db()
        lead_id = _lead(db, 1)
        db.commit()

    response = client.post(f"/sales/leads/{lead_id}/stage", json={"new_stage": "LOST"})
    assert response.status_code == 403

    with client.application.app_context():
        lead = get_db().execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    assert current_sales_stage(lead) == "CAPTURED"


def test_manager_can_manually_move_the_stage(client):
    _login(client, "manager")
    with client.application.app_context():
        db = get_db()
        lead_id = _lead(db, 1)
        db.commit()

    response = client.post(f"/sales/leads/{lead_id}/stage", json={"new_stage": "LOST"})
    assert response.status_code == 200

    with client.application.app_context():
        lead = get_db().execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    assert current_sales_stage(lead) == "LOST"


# ── Atomic compound endpoints (qualify / invite / book) ──────────────────────
#
# save_qualification, invite_to_application and book_sales_appointment each
# insert an evidence row (qualification / invitation / appointment) and then
# advance the canonical stage. Both used to commit separately: if the stage
# advance was rejected, the endpoint's `except: db.rollback()` had nothing
# left to undo, so the evidence row survived with no matching stage change -
# and every retry inserted (or upserted onto) it again. The fix commits the
# evidence and the stage advance together via transition_sales_stage(...,
# commit=False), so a rejected transition rolls back the evidence row too.

def test_a_rejected_qualification_leaves_no_orphan_row(client):
    """A lead still at CAPTURED cannot be QUALIFIED (only CONTACTED can be) -
    the qualification endpoint must reject it and leave no trace."""
    _login(client, "manager")
    with client.application.app_context():
        db = get_db()
        lead_id = _lead(db, 1)
        db.commit()

    response = client.post(
        f"/sales/leads/{lead_id}/qualification",
        json={"interested": "1", "needs_identified": "1"},
    )
    assert response.status_code == 400

    with client.application.app_context():
        db = get_db()
        orphan = db.execute(
            "SELECT id FROM lead_qualifications WHERE lead_id=?", (lead_id,)
        ).fetchone()
        assert orphan is None, "a rejected qualification must not leave a row behind"
        stage = db.execute("SELECT sales_stage FROM leads WHERE id=?", (lead_id,)).fetchone()
        assert stage["sales_stage"] != "QUALIFIED"


def test_a_rejected_application_invitation_leaves_no_orphan_row(client):
    """APPLICATION_INVITED requires SHOW/CONSULTATION/DECISION_FOLLOW_UP - a
    lead still at CAPTURED cannot reach it."""
    _login(client, "manager")
    with client.application.app_context():
        db = get_db()
        lead_id = _lead(db, 1)
        db.commit()

    response = client.post(f"/sales/leads/{lead_id}/application-invitation", json={})
    assert response.status_code == 400

    with client.application.app_context():
        db = get_db()
        orphan = db.execute(
            "SELECT id FROM application_invitations WHERE lead_id=?", (lead_id,)
        ).fetchone()
        assert orphan is None, "a rejected invitation must not leave a row behind"


def test_a_rejected_appointment_booking_leaves_no_orphan_row(client):
    """APPOINTMENT_BOOKED is only reachable from INVITED - a lead still at
    CAPTURED cannot reach it."""
    _login(client, "manager")
    with client.application.app_context():
        db = get_db()
        lead_id = _lead(db, 1)
        db.commit()

    response = client.post(
        f"/sales/leads/{lead_id}/appointment",
        json={"appointment_at": "2026-10-01T10:00:00"},
    )
    assert response.status_code == 400

    with client.application.app_context():
        db = get_db()
        orphan = db.execute(
            "SELECT id FROM sales_appointments WHERE lead_id=?", (lead_id,)
        ).fetchone()
        assert orphan is None, "a rejected appointment must not leave a row behind"


def test_a_successful_qualification_still_commits_both_rows(client):
    """The fix must not turn a legitimate qualification into a no-op - a lead
    at CONTACTED really does end up QUALIFIED with its evidence saved."""
    _login(client, "manager")
    with client.application.app_context():
        db = get_db()
        lead_id = _lead(db, 1)
        transition_sales_stage(db, lead_id, "ASSIGNED", 1)
        transition_sales_stage(db, lead_id, "CONTACT_ATTEMPTED", 1)
        transition_sales_stage(db, lead_id, "CONTACTED", 1)
        db.commit()

    response = client.post(
        f"/sales/leads/{lead_id}/qualification",
        json={"interested": "1", "needs_identified": "1"},
    )
    assert response.status_code == 200

    with client.application.app_context():
        db = get_db()
        row = db.execute(
            "SELECT interested FROM lead_qualifications WHERE lead_id=?", (lead_id,)
        ).fetchone()
        assert row is not None and row["interested"] == 1
        stage = db.execute("SELECT sales_stage FROM leads WHERE id=?", (lead_id,)).fetchone()
        assert stage["sales_stage"] == "QUALIFIED"
