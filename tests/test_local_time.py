from datetime import date, datetime, timezone

import pytest

from onecpase.local_time import sast


@pytest.mark.parametrize('stored, shown', [
    ('2026-11-03 09:37:06', '2026-11-03 11:37'),
    ('2026-11-03T09:37:06', '2026-11-03 11:37'),
    ('2026-11-03T09:37:06.123456', '2026-11-03 11:37'),
    ('2026-11-03T09:37:06+00:00', '2026-11-03 11:37'),
    ('2026-11-03T09:37:06Z', '2026-11-03 11:37'),
    ('2026-11-03T11:37:06+02:00', '2026-11-03 11:37'),
    ('2026-11-03 22:30:00', '2026-11-04 00:30'),
    (datetime(2026, 11, 3, 9, 37, tzinfo=timezone.utc), '2026-11-03 11:37'),
    (datetime(2026, 11, 3, 9, 37), '2026-11-03 11:37'),
])
def test_system_stamps_are_shown_in_sast(stored, shown):
    assert sast(stored) == shown


@pytest.mark.parametrize('stored, shown', [
    (None, ''),
    ('', ''),
    ('2026-11-03', '2026-11-03'),
    (date(2026, 11, 3), '2026-11-03'),
    ('not a time', 'not a time'),
])
def test_values_without_a_time_are_left_alone(stored, shown):
    assert sast(stored) == shown


def test_format_can_keep_seconds():
    assert sast('2026-11-03 09:37:06', '%Y-%m-%d %H:%M:%S') == '2026-11-03 11:37:06'


def test_audit_trail_entry_renders_in_sast(app):
    template = app.jinja_env.from_string('{{ entry.timestamp|sast }}')
    assert template.render(entry={'timestamp': '2026-11-03 09:37:06'}) == '2026-11-03 11:37'
