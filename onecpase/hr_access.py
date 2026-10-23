"""Shared HR access policy for routes, templates, and salary updates."""
from flask import session

from .database import get_db
from .query_routing import staff

OPEN_HR_PERMISSIONS = frozenset({'hr_module', 'hr_staff', 'hr_leave', 'hr_timesheets'})


def can_access_payroll():
    if not session.get('user_id'):
        return False
    user = get_db().execute(
        'SELECT email, active, is_platform_user FROM users WHERE id=?',
        (session['user_id'],),
    ).fetchone()
    if not user or not user['active'] or user['is_platform_user']:
        return False
    for name in ('mvuleni', 'ayrton'):
        allowed = staff(get_db(), name)
        if allowed and allowed['id'] == session['user_id']:
            return True
    return False
