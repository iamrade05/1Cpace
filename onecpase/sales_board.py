"""Consultant planning board; actual sales always use existing compliance credit."""
from datetime import timedelta
from math import ceil

from .reporting_cycle import cycle_bounds, cycle_key
from .staff_kpi import business_day, selected_day


def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS sales_board_targets (
        cycle TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
        target INTEGER NOT NULL CHECK(target >= 0), updated_by INTEGER REFERENCES users(id),
        PRIMARY KEY(cycle,user_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS sales_board_commitments (
        day TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
        units INTEGER NOT NULL CHECK(units >= 0), updated_by INTEGER REFERENCES users(id),
        PRIMARY KEY(day,user_id))""")


def can_manage(user):
    return bool(user and str(user['role']).strip().lower() in ('admin', 'manager', 'sales_manager'))


def board_staff(db):
    from .dashboard_access import team_ids
    allowed = set(team_ids(db, 'sales'))
    return [dict(row) for row in db.execute(
        "SELECT id,username,full_name,role FROM users WHERE active=1 ORDER BY full_name,username"
    ).fetchall() if row['id'] in allowed and str(row['role']).strip().lower() not in
            ('admin', 'manager', 'sales_manager')]


def build_board(db, day=None, user_id=None):
    from .sales_kpi import credited_sales
    day = selected_day(day)
    key = cycle_key(day)
    start, end = cycle_bounds(key)
    planning_days = [start + timedelta(days=i) for i in range((end-start).days+1)
                     if (start + timedelta(days=i)).weekday() != 6]
    elapsed = sum(d <= day for d in planning_days)
    remaining = len(planning_days) - elapsed
    staff = [p for p in board_staff(db) if user_id is None or p['id'] == user_id]
    ids = {p['id'] for p in staff}
    targets = {r['user_id']: r['target'] for r in db.execute(
        'SELECT user_id,target FROM sales_board_targets WHERE cycle=?', (key,)).fetchall()}
    commitments = {r['user_id']: r['units'] for r in db.execute(
        'SELECT user_id,units FROM sales_board_commitments WHERE day=?', (day.isoformat(),)).fetchall()}
    def in_window(stamp):
        value = business_day(stamp)
        return value is not None and start <= value <= day
    apps = [dict(r) for r in db.execute('SELECT id,package FROM membership_applications').fetchall()]
    sales = credited_sales(db, in_window, lambda uid: uid in ids, apps)
    rows = []
    for person in staff:
        own = [s for s in sales if s['credited_to'] == person['id']]
        actual = len(own)
        target = targets.get(person['id'])
        balance = max(0, target - actual) if target is not None else None
        packages = {}
        for sale in own:
            label = sale['package'] or 'Unspecified package'
            packages[label] = packages.get(label, 0) + 1
        rows.append({**person, 'name': person['full_name'] or person['username'],
                     'target': target, 'actual': actual, 'balance': balance,
                     'projection': round(actual / elapsed * len(planning_days), 1) if elapsed else None,
                     'daily': ceil(balance * 10 / remaining) / 10 if remaining and balance is not None else None,
                     'percent': round(actual * 100 / target, 1) if target else None,
                     'met': target is not None and target > 0 and actual >= target,
                     'band': ('Target 1' if actual * 100 >= target * 125 else
                              'Target 2' if actual * 100 >= target * 110 else
                              'Target 3' if actual >= target else '') if target else '',
                     'today': sum(business_day(s['credited_at']) == day for s in own),
                     'commitment': commitments.get(person['id']), 'packages': sorted(packages.items())})
    return {'day': day.isoformat(), 'cycle': key, 'start': start.isoformat(), 'end': end.isoformat(),
            'days': len(planning_days), 'elapsed': elapsed, 'remaining': remaining, 'rows': rows,
            'actual': sum(r['actual'] for r in rows), 'target': sum(targets.get(uid, 0) for uid in ids),
            'configured': sum(uid in targets for uid in ids)}
