"""Shared South African reporting cycles, named for their closing month."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def today():
    return datetime.now(ZoneInfo('Africa/Johannesburg')).date()


def shift_month(day, months):
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def cycle_key(day):
    return shift_month(day, 1 if day.day >= 15 else 0).strftime('%Y-%m')


def cycle_bounds(key):
    end = date.fromisoformat(str(key) + '-01')
    return shift_month(end, -1).replace(day=15), end.replace(day=14)


def current_cycle(day=None):
    return cycle_bounds(cycle_key(day or today()))


def cycle_label(key):
    first, last = cycle_bounds(key)
    return f'{first:%d %b %Y} – {last:%d %b %Y}'


def history_start(months, day=None):
    closing = date.fromisoformat(cycle_key(day or today()) + '-01')
    return cycle_bounds(shift_month(closing, 1 - months).strftime('%Y-%m'))[0].isoformat()
