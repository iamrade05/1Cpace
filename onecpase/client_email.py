"""Departmental email routing and deduplicated new-member communications."""
from datetime import timedelta
import json
from pathlib import Path

from flask import current_app, g
from flask_mail import Message

from .encryption import decrypt, encrypt
from .extensions import mail
from .reporting_cycle import cycle_key, today
from .staff_kpi import business_day


def department_message(department, subject, recipients, **kwargs):
    """Use the confirmed shared mailbox only for the Elev8 tenant."""
    if (current_app.config.get("DEPARTMENT_MAIL_ENABLED")
            and g.get("tenant_slug") == "elev8"):
        key = "MAIL_SALES_SENDER" if department == "sales" else "MAIL_RECEPTION_SENDER"
        sender = current_app.config.get(key)
        if not sender:
            raise ValueError("Department email sender is not configured")
        kwargs.update(sender=sender, reply_to=sender)
    return Message(subject, recipients=recipients, **kwargs)


def ensure_schema(db, backend="sqlite"):
    identity = "SERIAL PRIMARY KEY" if backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    db.execute(f"""CREATE TABLE IF NOT EXISTS client_email_outbox (
        id {identity}, member_id INTEGER NOT NULL REFERENCES members(id),
        kind TEXT NOT NULL, period_key TEXT NOT NULL, recipient TEXT NOT NULL,
        payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL, sent_at TEXT, error TEXT,
        UNIQUE(member_id,kind,period_key))""")


def welcome_content(member):
    name = decrypt(member["first_name"]) or "there"
    reception = current_app.config["MAIL_RECEPTION_SENDER"]
    text = (
        f"Hi {name}, welcome to Elev8 Health & Fitness!\n\n"
        "Thank you for choosing us and trusting us with your fitness journey.\n\n"
        "1. Trainer support for seven days\n"
        "During the next seven days, Elev8 can pair you with a fitness instructor to help you get started. "
        "Would you like to take part? Please reply Yes or No.\n\n"
        "2. Personal training after seven days\n"
        "If you feel you need further assistance after seven days, would you be interested in optional "
        "personal training? Please reply Yes or No.\n\n"
        "Personal training fees are agreed directly with your personal trainer.\n\n"
        f"Your Reception team\n{reception}"
    )
    html = current_app.jinja_env.get_template("email/member_welcome.html").render(
        first_name=name, reception_email=reception)
    return dict(subject="Welcome to Elev8 Health & Fitness", body=text, html=html)


def _queue(db, member, kind, period_key, content):
    recipient = (decrypt(member["email"]) or "").strip()
    if not recipient:
        return
    from datetime import datetime, timezone
    db.execute("""INSERT INTO client_email_outbox(member_id,kind,period_key,recipient,payload,created_at)
                  VALUES(?,?,?,?,?,?) ON CONFLICT(member_id,kind,period_key) DO NOTHING""",
               (member["id"], kind, period_key, encrypt(recipient), encrypt(json.dumps(content)),
                datetime.now(timezone.utc).isoformat()))


def compliance_content(name, missing, sales):
    text = (f"Hi {name},\n\nPlease contact the Elev8 Sales team to complete your membership requirements.\n\n"
            + "\n".join("- " + item for item in missing)
            + f"\n\nContact {sales} for help completing these items.\n\nYour Elev8 Sales team")
    return dict(subject="Elev8 membership requirements", body=text,
                html=current_app.jinja_env.get_template("email/member_compliance.html").render(
                    first_name=name, missing=missing, sales_email=sales))


def queue_client_emails(db):
    """Welcome recent joins once and remind incomplete applicants once per cycle."""
    from .applications import compliance_completion_blockers

    as_of = today()
    recent = as_of - timedelta(days=9)
    for member in db.execute("""SELECT id,first_name,email,join_date FROM members
                              WHERE member_status='Active' AND NULLIF(TRIM(email),'') IS NOT NULL""").fetchall():
        joined = business_day(member["join_date"])
        if joined and recent <= joined <= as_of:
            _queue(db, member, "welcome", "welcome-v1", welcome_content(member))
    applications = db.execute("""
        SELECT m.id,m.first_name,m.email,a.id AS application_id
        FROM members m JOIN membership_applications a ON a.id=(
            SELECT MAX(ma.id) FROM membership_applications ma WHERE ma.member_id=m.id)
        WHERE m.member_status IN ('Active','Unverified')
          AND a.application_status <> 'declined' AND NULLIF(TRIM(m.email),'') IS NOT NULL
    """).fetchall()
    for member in applications:
        missing = compliance_completion_blockers(db, member["application_id"])
        if not missing:
            continue
        name = decrypt(member["first_name"]) or "there"
        sales = current_app.config["MAIL_SALES_SENDER"]
        _queue(db, member, "compliance", cycle_key(as_of), compliance_content(name, missing, sales))
    db.commit()


def dispatch_client_emails(db, limit=10):
    """Claim before SMTP. An uncertain delivery is retained for manual review."""
    if (current_app.testing or current_app.config.get("MAIL_SUPPRESS_SEND")
            or not current_app.config.get("DEPARTMENT_MAIL_ENABLED")
            or not current_app.config.get("CLIENT_EMAIL_AUTOMATION_ENABLED")):
        return
    from datetime import datetime, timezone
    from .applications import compliance_completion_blockers
    for row in db.execute("SELECT * FROM client_email_outbox WHERE status='pending' ORDER BY id LIMIT ?",
                          (limit,)).fetchall():
        member = db.execute("SELECT id,first_name,email,join_date,member_status FROM members WHERE id=?",
                            (row["member_id"],)).fetchone()
        applicable = member and member["member_status"] in (
            ("Active",) if row["kind"] == "welcome" else ("Active", "Unverified"))
        if applicable and row["kind"] == "welcome":
            joined = business_day(member["join_date"])
            applicable = joined and today() - timedelta(days=9) <= joined <= today()
        missing = []
        if applicable and row["kind"] == "compliance":
            application = db.execute("SELECT id FROM membership_applications WHERE member_id=? ORDER BY id DESC LIMIT 1",
                                     (row["member_id"],)).fetchone()
            missing = compliance_completion_blockers(db, application["id"]) if application else []
            applicable = bool(missing)
        if not applicable:
            db.execute("UPDATE client_email_outbox SET status='cancelled' WHERE id=? AND status='pending'", (row["id"],))
            db.commit()
            continue
        claimed = db.execute("UPDATE client_email_outbox SET status='sending' WHERE id=? AND status='pending'", (row["id"],))
        db.commit()
        if claimed.rowcount != 1:
            continue
        try:
            recipient = (decrypt(member["email"]) or "").strip()
            if not recipient:
                raise ValueError("Member email is missing")
            if row["kind"] == "welcome":
                payload = welcome_content(member)
            else:
                name = decrypt(member["first_name"]) or "there"
                sales = current_app.config["MAIL_SALES_SENDER"]
                payload = compliance_content(name, missing, sales)
            db.execute("UPDATE client_email_outbox SET recipient=?,payload=? WHERE id=? AND status='sending'",
                       (encrypt(recipient), encrypt(json.dumps(payload)), row["id"]))
            db.commit()
            message = department_message("sales" if row["kind"] == "compliance" else "reception",
                                         payload["subject"], [recipient],
                                         body=payload["body"], html=payload["html"])
            if row["kind"] == "welcome":
                logo = Path(current_app.static_folder) / "tenant_logos/elev8-email.png"
                message.attach("elev8-logo.png", "image/png", logo.read_bytes(), disposition="inline",
                               headers={"Content-ID": "<elev8-logo>"})
                gym = Path(current_app.static_folder) / "images/elev8-welcome-gym.png"
                message.attach("elev8-gym.png", "image/png", gym.read_bytes(), disposition="inline",
                               headers={"Content-ID": "<elev8-gym>"})
            mail.send(message)
            db.execute("UPDATE client_email_outbox SET status='sent',sent_at=?,error=NULL WHERE id=?",
                       (datetime.now(timezone.utc).isoformat(), row["id"]))
        except Exception:
            db.execute("UPDATE client_email_outbox SET status='failed',error=? WHERE id=?",
                       ("Delivery failed. Review mailbox permissions and member email before retrying.", row["id"]))
        db.commit()


def run_client_emails(db):
    if (g.get("tenant_slug") == "elev8" and current_app.config.get("DEPARTMENT_MAIL_ENABLED")
            and current_app.config.get("CLIENT_EMAIL_AUTOMATION_ENABLED")):
        queue_client_emails(db)
        dispatch_client_emails(db)
