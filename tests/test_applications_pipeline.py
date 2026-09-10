from onecpase.database import get_db
from onecpase.applications import _sync_sales_stage_for_application
from onecpase.sales_pipeline import current_sales_stage, transition_sales_stage


def test_application_active_sync_advances_joined_without_request_session(app):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES (?, ?, ?)",
            ("app-pipeline-user", "hash", "Application Pipeline User"),
        )
        user_id = db.execute(
            "SELECT id FROM users WHERE username=?", ("app-pipeline-user",)
        ).fetchone()[0]

        db.execute(
            "INSERT INTO leads (full_name, assigned_to, lead_status) VALUES (?, ?, 'captured')",
            ("Application Pipeline Lead", user_id),
        )
        lead_id = db.execute(
            "SELECT id FROM leads WHERE full_name=?",
            ("Application Pipeline Lead",),
        ).fetchone()[0]

        # Build the canonical path to the application gate.
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
        transition_sales_stage(db, lead_id, "APPLICATION_INVITED", user_id)

        db.execute(
            """INSERT INTO membership_applications
               (lead_id, application_status, created_by)
               VALUES (?, 'ready_to_push', ?)""",
            (lead_id, user_id),
        )
        aid = db.execute(
            "SELECT id FROM membership_applications WHERE lead_id=?",
            (lead_id,),
        ).fetchone()[0]
        db.commit()

        # The bridge advances one controlled stage per application milestone,
        # so the lead has to reach APPLICATION_SUBMITTED before activation can
        # move it to JOINED.
        _sync_sales_stage_for_application(db, aid, "awaiting_docs", actor_id=user_id)
        lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        assert current_sales_stage(lead) == "APPLICATION_STARTED"

        _sync_sales_stage_for_application(db, aid, "ready_to_push", actor_id=user_id)
        lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        assert current_sales_stage(lead) == "APPLICATION_SUBMITTED"

        _sync_sales_stage_for_application(db, aid, "active", actor_id=user_id)

        lead = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        assert current_sales_stage(lead) == "JOINED"


def test_debicheck_approval_satisfies_application_gate(app):
    from onecpase.debicheck import sync_debicheck_to_application

    with app.app_context():
        db = get_db()
        user_id = db.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES (?, ?, ?)",
            ("debi-pipeline-user", "hash", "Debi Pipeline User"),
        ).lastrowid
        lead_id = db.execute(
            "INSERT INTO leads (full_name, assigned_to, lead_status) VALUES (?, ?, 'captured')",
            ("Debi Pipeline Lead", user_id),
        ).lastrowid
        # Reach the application gate.
        for stage in ("ASSIGNED", "CONTACT_ATTEMPTED", "CONTACTED"):
            transition_sales_stage(db, lead_id, stage, user_id)
        db.execute(
            """INSERT INTO lead_qualifications
               (lead_id, interested, needs_identified, ready_to_join, qualified_by, qualified_at)
               VALUES (?, 1, 1, 1, ?, '2026-09-01 10:00:00')""",
            (lead_id, user_id),
        )
        db.commit()
        transition_sales_stage(db, lead_id, "QUALIFIED", user_id)
        transition_sales_stage(db, lead_id, "INVITED", user_id)
        db.execute(
            "INSERT INTO sales_appointments (lead_id, appointment_at, status) VALUES (?, '2026-09-02 10:00:00', 'BOOKED')",
            (lead_id,),
        )
        db.commit()
        for stage in ("APPOINTMENT_BOOKED", "SHOW", "CONSULTATION", "APPLICATION_INVITED", "APPLICATION_STARTED"):
            transition_sales_stage(db, lead_id, stage, user_id)
        aid = db.execute(
            "INSERT INTO membership_applications (lead_id, application_status, created_by) VALUES (?, 'verification', ?)",
            (lead_id, user_id),
        ).lastrowid
        db.execute(
            "INSERT INTO compliance_checklists (application_id, debit_check_status) VALUES (?, 'pending')",
            (aid,),
        )
        mandate_id = db.execute(
            """INSERT INTO debicheck_mandates
               (member_id, merchant_id, client_ref1, client_ref2, account_name, account_type,
                account_number, branch_code, submit_date, status)
               VALUES (?, 'M', 'REF', '1234', 'Test', '1', '123456', '123456', '2026-09-01', 'approved')""",
            (db.execute("INSERT INTO members (first_name, last_name, id_number) VALUES ('D', 'User', 'TEST-DEBI')").lastrowid,),
        ).lastrowid
        # Link the application to the same member.
        member_id = db.execute("SELECT member_id FROM debicheck_mandates WHERE id=?", (mandate_id,)).fetchone()[0]
        db.execute("UPDATE membership_applications SET member_id=? WHERE id=?", (member_id, aid))
        db.commit()

        assert sync_debicheck_to_application(db, mandate_id, actor_id=user_id) == "approved"
        cl = db.execute(
            "SELECT debit_check_status FROM compliance_checklists WHERE application_id=?", (aid,)
        ).fetchone()
        assert cl["debit_check_status"] == "approved"


def test_debicheck_submission_does_not_approve_application_gate(app):
    from onecpase.debicheck import sync_debicheck_to_application

    with app.app_context():
        db = get_db()
        user_id = db.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES (?, ?, ?)",
            ("debi-submit-user", "hash", "Debi Submit User"),
        ).lastrowid
        member_id = db.execute(
            "INSERT INTO members (first_name, last_name, id_number) VALUES ('S', 'User', 'TEST-SUBMIT')"
        ).lastrowid
        aid = db.execute(
            "INSERT INTO membership_applications (member_id, application_status, created_by) VALUES (?, 'verification', ?)",
            (member_id, user_id),
        ).lastrowid
        db.execute(
            "INSERT INTO compliance_checklists (application_id, debit_check_status) VALUES (?, 'pending')",
            (aid,),
        )
        mandate_id = db.execute(
            """INSERT INTO debicheck_mandates
               (member_id, merchant_id, client_ref1, client_ref2, account_name, account_type,
                account_number, branch_code, submit_date, status)
               VALUES (?, 'M', 'REF2', '1234', 'Test', '1', '123456', '123456', '2026-09-01', 'submitted')""",
            (member_id,),
        ).lastrowid
        db.commit()

        assert sync_debicheck_to_application(db, mandate_id, actor_id=user_id) == "submitted"
        cl = db.execute(
            "SELECT debit_check_status FROM compliance_checklists WHERE application_id=?", (aid,)
        ).fetchone()
        assert cl["debit_check_status"] == "pending"


def test_ready_to_push_requires_qualified_statement_analysis(app):
    from onecpase.applications import _derive_status, _is_ready_to_push

    with app.app_context():
        db = get_db()
        aid = db.execute(
            "INSERT INTO membership_applications (application_status) VALUES ('verification')"
        ).lastrowid
        db.execute(
            """INSERT INTO compliance_checklists
               (application_id, contract_status, debit_check_status, bank_statement_status,
                id_copy_status, pos_status, manager_approval_status)
               VALUES (?, 'signed', 'approved', 'analysed', 'verified', 'paid', 'not_required')""",
            (aid,),
        )
        cl = db.execute("SELECT * FROM compliance_checklists WHERE application_id=?", (aid,)).fetchone()

        assert not _is_ready_to_push(cl, None)
        assert _derive_status(cl, None) == "verification"

        analysis = {
            "verification_qualified": 1,
        }
        assert _is_ready_to_push(cl, analysis)
        assert _derive_status(cl, analysis) == "ready_to_push"
