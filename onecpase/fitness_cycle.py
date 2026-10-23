"""Member joining cohorts in complete 15th–14th business cycles."""
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime

from .encryption import decrypt_member


def today():
    return datetime.now(ZoneInfo('Africa/Johannesburg')).date()


def cycle_key(day):
    end_month = day.replace(day=1)
    if day.day >= 15:
        end_month = (end_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return end_month.strftime('%Y-%m')


def cycle_bounds(key):
    end_month = date.fromisoformat(str(key) + '-01')
    previous_month = end_month - timedelta(days=1)
    return previous_month.replace(day=15), end_month.replace(day=14)


def joining_window(args, day=None):
    day = day or today()
    key = args.get('cycle') or cycle_key(day)
    start, end = cycle_bounds(key)
    # Default to the current Monday–Sunday week, bounded by the full cycle.
    anchor = min(max(day, start), end)
    monday = anchor - timedelta(days=anchor.weekday())
    default_from, default_to = max(start, monday), min(end, monday + timedelta(days=6))
    from_day = date.fromisoformat(args.get('from') or default_from.isoformat())
    to_day = date.fromisoformat(args.get('to') or default_to.isoformat())
    if not start <= from_day <= to_day <= end:
        raise ValueError('Choose a joining period inside the selected 15th–14th cycle.')
    return key, start, end, from_day, to_day


def joiners(db, start, end):
    rows = db.execute(
        'SELECT * FROM members WHERE substr(join_date,1,10) BETWEEN ? AND ? '
        'ORDER BY substr(join_date,1,10), id',
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [decrypt_member(row) for row in rows]


def onboarding_cohort(db, args):
    filters = dict(args)
    key = filters.get('cycle') or cycle_key(today())
    first, last = cycle_bounds(key)
    # The operational page defaults to every joiner in the current full cycle.
    filters.setdefault('from', first.isoformat())
    filters.setdefault('to', last.isoformat())
    key, first, last, start, end = joining_window(filters)
    full = joiners(db, first, last)
    options = []
    anchor = cycle_bounds(cycle_key(today()))[1]
    for _ in range(12):
        option_key = anchor.strftime('%Y-%m')
        a, b = cycle_bounds(option_key)
        options.append((option_key, a, b))
        anchor = a.replace(day=14)
    if key not in {item[0] for item in options}:
        options.insert(0, (key, first, last))
    return dict(error=None, members=[m for m in full if start.isoformat() <= str(m['join_date'])[:10] <= end.isoformat()],
                cycle=key, cycle_start=first, cycle_end=last, from_day=start, to_day=end,
                cycle_options=options, full_count=len(full))
