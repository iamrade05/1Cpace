"""Show system timestamps in South African time.

The server clock runs on UTC, so every stamp the system writes without an
offset (datetime('now'), datetime.now()) is UTC. Only for those stamps: times
staff type in, such as follow-ups and appointments, are already local.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SAST = ZoneInfo('Africa/Johannesburg')


def sast(value, fmt='%Y-%m-%d %H:%M'):
    """Template filter: format a stored system timestamp in SAST."""
    if not value:
        return ''
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        # A date alone has no time of day to convert.
        if len(raw) <= 10:
            return raw
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SAST).strftime(fmt)
