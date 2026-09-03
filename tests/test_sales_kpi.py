from onecpase.database import get_db
from onecpase.sales_kpi import (
    build_sales_report,
    record_checklist_changes,
    record_lead_status_change,
    snapshot_checklist,
)
from onecpase.sales_pipeline import current_sales_stage, transition_sales_stage


def test_sales_kpi_schema_and_lead_event(app):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES (?, ?, ?)",
            ("kpi-user", "hash", "KPI User"),
        )
        user_id = db.execute("SELECT id FROM users WHERE username=?", ("kpi-user",)).fetchone()[0]
        db.execute("INSERT INTO leads (full_name, assigned_to) VALUES (?, ?)", ("KPI Lead", user_id))
        lead_id = db.execute("SELECT id FROM leads WHERE full_name=?", ("KPI Lead",)).fetchone()[0]
        record_lead_status_change(db, lead_id, "captured", "assigned", actor_id=user_id)

        event = db.execute(
            "SELECT field, from_value, to_value, owner_id, actor_id FROM stage_events WHERE lead_id=?",
            (lead_id,),
        ).fetchone()
        assert tuple(event) == ("lead_status", "captured", "assigned", user_id, user_id)
        assert db.execute("SELECT assigned_at FROM leads WHERE id=?", (lead_id,)).fetchone()[0]


def test_checklist_snapshot_records_only_changed_fields(app):
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO membership_applications (application_status) VALUES ('draft')")
        aid = db.execute("SELECT MAX(id) FROM membership_applications").fetchone()[0]
        db.execute("INSERT INTO compliance_checklists (application_id) VALUES (?)", (aid,))
        before = snapshot_checklist(db, aid)
        db.execute(
            "UPDATE compliance_checklists SET contract_status='signed' WHERE application_id=?",
            (aid,),
        )
        record_checklist_changes(db, aid, before)

        fields = db.execute(
            "SELECT field FROM stage_events WHERE application_id=? ORDER BY id", (aid,)
        ).fetchall()
        assert [row[0] for row in fields] == ["contract_status"]


def test_sales_funnel_keeps_applications_within_assigned_lead_cohort(app):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES (?, ?, ?)",
            ("cohort-user", "hash", "Cohort User"),
        )
        user_id = db.execute(
            "SELECT id FROM users WHERE username=?", ("cohort-user",)
        ).fetchone()[0]
        db.execute(
            """INSERT INTO leads
               (full_name, assigned_to, lead_status, assigned_at, created_at)
               VALUES (?, ?, 'joined', ?, ?)""",
            ("Cohort Lead", user_id, "2026-08-10", "2026-08-10"),
        )
        lead_id = db.execute(
            "SELECT id FROM leads WHERE full_name=?", ("Cohort Lead",)
        ).fetchone()[0]
        db.execute(
            """INSERT INTO membership_applications
               (lead_id, application_status, created_by, created_at, updated_at)
               VALUES (?, 'active', ?, ?, ?)""",
            (lead_id, user_id, "2026-08-12", "2026-08-12"),
        )
        db.execute(
            """INSERT INTO membership_applications
               (application_status, created_by, created_at, updated_at)
               VALUES ('active', ?, ?, ?)""",
            (user_id, "2026-08-12", "2026-08-12"),
        )
        db.commit()

        report = build_sales_report(db, "2026-08-01", "2026-08-31")

        assert report["funnel"]["assigned"] == 1
        assert report["funnel"]["applications"] == 1
        assert report["funnel"]["activated"] == 1
        assert [row["stage"] for row in report["funnel_leakage"]] == [
            "assigned", "contacted", "appointment", "show",
            "application", "activated", "joined",
        ]


def test_controlled_pipeline_rejects_stage_jumps_and_records_history(app):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES (?, ?, ?)",
            ("pipeline-user", "hash", "Pipeline User"),
        )
        user_id = db.execute(
            "SELECT id FROM users WHERE username=?", ("pipeline-user",)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO leads (full_name, assigned_to, lead_status) VALUES (?, ?, 'captured')",
            ("Pipeline Lead", user_id),
        )
        lead_id = db.execute(
            "SELECT id FROM leads WHERE full_name=?", ("Pipeline Lead",)
        ).fetchone()[0]
        db.commit()

        transition_sales_stage(db, lead_id, "ASSIGNED", user_id)
        try:
            transition_sales_stage(db, lead_id, "JOINED", user_id)
        except ValueError as exc:
            assert "Invalid sales transition" in str(exc)
        else:
            raise AssertionError("A controlled pipeline jump should be rejected")

        assert current_sales_stage(db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()) == "ASSIGNED"
        history = db.execute(
            "SELECT from_stage, to_stage FROM sales_stage_history WHERE lead_id=? ORDER BY id",
            (lead_id,),
        ).fetchall()
        assert [tuple(row) for row in history] == [("CAPTURED", "ASSIGNED")]
