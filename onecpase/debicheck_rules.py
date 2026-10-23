"""Shared DebiCheck name and first-debit date requirements."""
from datetime import date, datetime, timedelta, timezone
import re


NAME_ERROR = "Enter the account holder's initials and surname, for example J P Smith. Use letters and spaces only; no dots, asterisks, dashes or other symbols."


def minimum_debit_date():
    today = datetime.now(timezone(timedelta(hours=2))).date()
    return today + timedelta(days=8)


def earliest_debit_date():
    """Outside a new sale, any date from today onward is allowed."""
    return datetime.now(timezone(timedelta(hours=2))).date()


def debit_date_error(value, *, sales=False):
    """The 7-day lead time is a sales rule: it applies only to the sign-up of a
    new member. Everywhere else (collections arrangements, standalone mandates,
    edits, retries) the date just cannot be in the past."""
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return "Submit date must be a valid date in YYYY-MM-DD format."
    if sales:
        minimum = minimum_debit_date()
        if parsed < minimum:
            return ("Change the submission / first debit date: it must be more than 7 days from today. "
                    f"The earliest allowed date is {minimum.isoformat()}.")
        return ""
    minimum = earliest_debit_date()
    if parsed < minimum:
        return ("Change the submission / first debit date: it cannot be in the past. "
                f"The earliest allowed date is {minimum.isoformat()}.")
    return ""


def account_holder_error(value, juristic=False):
    name = str(value or "").strip()
    if not name:
        return ""
    if not re.fullmatch(r"[A-Za-z]+(?: +[A-Za-z]+)*", name):
        return NAME_ERROR
    # Juristic account names are company names rather than personal initials.
    if not juristic and (len(name.split()) < 2 or not re.fullmatch(r"[A-Z]{1,6}", name.split()[0])):
        return NAME_ERROR
    return ""


def mandate_rules_context():
    return {
        "debicheck_min_date": minimum_debit_date().isoformat(),       # new sales: more than 7 days out
        "debicheck_earliest_date": earliest_debit_date().isoformat(),  # everything else: today onward
    }
