from datetime import date

from flask import g

from onecpase import client_email
from onecpase.database import get_db


def _member(db, ref, joined, email="member@example.test"):
    return db.execute("""INSERT INTO members(member_ref,first_name,last_name,id_number,email,join_date,
                         member_status) VALUES(?,?,'Member',?,?,?,'Active')""",
                      (ref, "Example <script>", ref, email, joined)).lastrowid


def test_welcome_card_and_department_senders(app, monkeypatch):
    monkeypatch.setattr(client_email, "today", lambda: date(2026, 11, 7))
    app.config["DEPARTMENT_MAIL_ENABLED"] = True
    with app.app_context():
        g.tenant_slug = "elev8"
        reception = client_email.department_message("reception", "Welcome", ["member@example.test"])
        sales = client_email.department_message("sales", "Requirements", ["member@example.test"])
        assert reception.sender == reception.reply_to == app.config["MAIL_RECEPTION_SENDER"]
        assert sales.sender == sales.reply_to == app.config["MAIL_SALES_SENDER"]
        db = get_db()
        mid = _member(db, "EMAIL-1", "2026-11-06")
        member = db.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        content = client_email.welcome_content(member)
        assert "7 DAYS" in content["html"] and "10 DAYS" not in content["html"]
        assert content["body"].count("Please reply Yes or No.") == 2
        assert content["html"].count("Please reply Yes or No.") == 2
        assert "trusting us with your fitness journey" in content["body"]
        assert "fees are agreed directly with your personal trainer" in content["html"]
        assert 'src="cid:elev8-logo"' in content["html"]
        assert "#d51f37" in content["html"]
        assert "optional personal training" in content["html"]
        assert "&lt;script&gt;" in content["html"] and "<script>" not in content["html"]
        g.tenant_slug = "another-gym"
        assert client_email.department_message("reception", "Welcome", ["member@example.test"]).sender == app.config["MAIL_DEFAULT_SENDER"]


def test_queue_is_deduplicated_and_limits_welcomes_to_recent_active_members(app, monkeypatch):
    monkeypatch.setattr(client_email, "today", lambda: date(2026, 11, 7))
    with app.app_context():
        db = get_db()
        recent = _member(db, "EMAIL-RECENT", "2026-11-06")
        _member(db, "EMAIL-OLD", "2026-09-01")
        _member(db, "EMAIL-NO-ADDRESS", "2026-11-06", "")
        db.execute("INSERT INTO membership_applications(member_id) VALUES(?)", (recent,))
        db.commit()
        client_email.queue_client_emails(db)
        client_email.queue_client_emails(db)
        rows = db.execute("SELECT member_id,kind,recipient,payload FROM client_email_outbox").fetchall()
        assert {(r["member_id"], r["kind"]) for r in rows} == {(recent, "welcome"), (recent, "compliance")}
        assert all(r["recipient"].startswith("enc:") and r["payload"].startswith("enc:") for r in rows)


def test_dispatch_rechecks_member_email_and_does_not_resend_sent_messages(app, monkeypatch):
    monkeypatch.setattr(client_email, "today", lambda: date(2026, 11, 7))
    app.config.update(DEPARTMENT_MAIL_ENABLED=True, CLIENT_EMAIL_AUTOMATION_ENABLED=True)
    with app.app_context():
        g.tenant_slug = "elev8"
        db = get_db()
        mid = _member(db, "EMAIL-SEND", "2026-11-06")
        db.commit()
        client_email.queue_client_emails(db)
        db.execute("UPDATE members SET email='updated@example.test' WHERE id=?", (mid,))
        db.commit()
        sent = []
        monkeypatch.setattr(client_email.mail, "send", sent.append)
        with monkeypatch.context() as send_patch:
            send_patch.setitem(app.config, "TESTING", False)
            client_email.dispatch_client_emails(db)
            client_email.dispatch_client_emails(db)
        assert len(sent) == 1
        assert sent[0].recipients == ["updated@example.test"]
        assert sent[0].html and sent[0].body
        assert sent[0].attachments[0].headers["Content-ID"] == "<elev8-logo>"
        assert sent[0].attachments[0].content_type == "image/png"
        assert sent[0].attachments[1].headers["Content-ID"] == "<elev8-gym>"
        assert db.execute("SELECT status FROM client_email_outbox").fetchone()["status"] == "sent"


def test_automation_requires_enablement_and_respects_completed_compliance(app, monkeypatch):
    monkeypatch.setattr(client_email, "today", lambda: date(2026, 11, 7))
    with app.app_context():
        g.tenant_slug = "elev8"
        db = get_db()
        mid = _member(db, "EMAIL-COMPLETE", "2026-09-01")
        db.execute("INSERT INTO membership_applications(member_id) VALUES(?)", (mid,))
        db.commit()
        client_email.run_client_emails(db)
        assert db.execute("SELECT COUNT(*) FROM client_email_outbox").fetchone()[0] == 0
        client_email.queue_client_emails(db)
        assert db.execute("SELECT COUNT(*) FROM client_email_outbox").fetchone()[0] == 1
        monkeypatch.setattr("onecpase.applications.compliance_completion_blockers", lambda db, aid: [])
        app.config.update(DEPARTMENT_MAIL_ENABLED=True, CLIENT_EMAIL_AUTOMATION_ENABLED=True)
        with monkeypatch.context() as send_patch:
            send_patch.setitem(app.config, "TESTING", False)
            client_email.dispatch_client_emails(db)
        assert db.execute("SELECT status FROM client_email_outbox").fetchone()["status"] == "cancelled"
