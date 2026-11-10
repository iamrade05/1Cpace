"""Elev8 query owners and assignment email notifications."""
from flask import current_app, g, has_request_context, has_app_context
from flask_mail import Message

from .extensions import mail

STAFF_EMAILS = {
    "ayrton": "ayrton@elev8healthandfitnessgym.com",
    "mvuleni": "mvuleni@elev8healthandfitnessgym.com",
    "thato": "sales@elev8healthandfitnessgym.com",
}
SALES_MEMBERSHIP_TYPES = {"Renewal", "Upgrade/downgrade", "Reactivation"}


def enabled():
    """Never route another gym's work to Elev8 staff."""
    if not has_app_context():
        return False
    if current_app.config.get("ELEV8_QUERY_ROUTING") is not None:
        return bool(current_app.config["ELEV8_QUERY_ROUTING"])
    return has_request_context() and g.get("tenant_slug") == "elev8"


def staff(db, name):
    rows = db.execute(
        "SELECT id, full_name, username, email, role FROM users "
        "WHERE active=1 AND COALESCE(is_platform_user,0)=0"
    ).fetchall()
    exact = [u for u in rows if str(u["email"] or "").lower() == STAFF_EMAILS[name]]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    matches = [u for u in rows if str(u["full_name"] or "").lower().split()[:1] == [name]
               or str(u["username"] or "").lower() == name]
    return matches[0] if len(matches) == 1 else None


def route(db, category, query_type=None):
    if not enabled():
        return None
    if category=='complaint':
        from .query_forms import staff_id
        return {'department':'Management','assigned_to':staff_id(db,'QUERY_APPROVER_USER_ID',True)}
    if category == "refund":
        owner = staff(db, "ayrton")
        return {"department": "Management", "assigned_to": owner["id"] if owner else None}
    if category == "sales" or (category == "membership" and query_type in SALES_MEMBERSHIP_TYPES):
        owner = staff(db, "thato")
        return {"department": "Sales", "assigned_to": owner["id"] if owner else None}
    return {"department": "Admin", "assigned_to": None}


def notify_assignment(db, qid, escalated=False):
    """Send after commit; report delivery failure without losing the query."""
    if not enabled():
        return None
    qry = db.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
    if not qry:
        return None
    recipients = []
    if escalated or qry['status'] == 'escalated':
        recipients.extend([STAFF_EMAILS['mvuleni'], STAFF_EMAILS['ayrton']])
    elif qry['department'] == 'Sales':
        recipients.extend([STAFF_EMAILS['thato'], STAFF_EMAILS['ayrton']])
    elif qry["assigned_to"]:
        user = db.execute("SELECT * FROM users WHERE id=? AND active=1", (qry["assigned_to"],)).fetchone()
        if user:
            for name, email in STAFF_EMAILS.items():
                match = staff(db, name)
                if match and match["id"] == user["id"]:
                    recipients.append(email)
                    break
            else:
                if user["email"]:
                    recipients.append(user["email"])
    elif qry["category"] == "refund":
        recipients.append(STAFF_EMAILS["ayrton"])
    else:
        for user in db.execute("SELECT id, email FROM users WHERE active=1 AND role='admin' "
                               "AND COALESCE(is_platform_user,0)=0").fetchall():
            mvuleni = staff(db, "mvuleni")
            email = STAFF_EMAILS["mvuleni"] if mvuleni and mvuleni["id"] == user["id"] else user["email"]
            if email:
                recipients.append(email)
    if not recipients or not current_app.config.get("MAIL_USERNAME"):
        return False
    try:
        # Send separately so recipients do not expose each other's addresses.
        for recipient in sorted(set(recipients)):
            action = 'escalated' if escalated or qry['status'] == 'escalated' else 'assigned'
            from .client_email import department_message
            message = department_message("reception", f"1Cpace — Query {qry['reference'] or qid} {action}",
                                         [recipient])
            message.body = (f"Query: {qry['reference'] or qid}\n"
                            f"Category: {qry['category']}\nDepartment: {qry['department']}\n"
                            f"Priority: {qry['priority']}\nFollow-up: {qry['follow_up_date'] or 'Not set'}\n\n"
                            "Sign in to 1Cpace and open Queries to review this assignment.")
            mail.send(message)
        return True
    except Exception:
        current_app.logger.exception("Query assignment email failed for query %s", qid)
        return False
