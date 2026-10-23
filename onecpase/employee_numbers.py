"""Employee attribution for newly loaded DebiCheck mandates."""
from flask import g, session

from .database import get_db


def current_employee_number():
    if not session.get("user_id"):
        return ""
    if "debicheck_employee_number" not in g:
        user = get_db().execute(
            "SELECT employee_number FROM users WHERE id=?", (session["user_id"],)
        ).fetchone()
        number = str(user["employee_number"] or "").strip() if user else ""
        g.debicheck_employee_number = number if len(number) == 4 and number.isascii() and number.isdigit() else ""
    return g.debicheck_employee_number


def employee_number_context():
    return {"employee_number": current_employee_number()}
