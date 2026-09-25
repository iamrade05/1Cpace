"""onecpase/db_adapter.py's SQLite -> PostgreSQL SQL translator. Pure string
transformation, so these are pure unit tests on _translate() - no live
Postgres needed. Confirmed real call sites this covers:
  - contracts.py:253  datetime('now','+7 days')
  - leads.py:429      datetime('now', '+30 days')
  - reports.py        date('now','-6 months') / date('now','-30 days') (x5)
  - collections.py:211  date(?, '-7 days')  (bound param, not literal 'now')
"""
from onecpase.db_adapter import _translate


def test_datetime_plus_days_translates_for_postgres():
    # contracts.py:253 / leads.py:429 pattern: "datetime(" + plural "days" -
    # the old translator only handled date('now','+N day') (singular, and
    # only the date() function), so this passed through untouched.
    out = _translate("VALUES (datetime('now','+7 days'))")
    assert "datetime(" not in out.lower()
    assert "NOW() + INTERVAL '+7 days'" in out


def test_datetime_plus_days_translates_without_leading_sign():
    out = _translate("VALUES (datetime('now', '+30 days'))")
    assert "NOW() + INTERVAL '+30 days'" in out


def test_date_minus_months_translates_for_postgres():
    # reports.py pattern - no minus/month rule existed at all before this fix.
    out = _translate("WHERE x >= date('now','-6 months')")
    assert "date(" not in out.lower()
    assert "CURRENT_DATE + INTERVAL '-6 months'" in out


def test_date_minus_days_translates_for_postgres():
    out = _translate("WHERE x >= date('now','-30 days')")
    assert "CURRENT_DATE + INTERVAL '-30 days'" in out


def test_date_bound_param_with_interval_translates_and_keeps_the_placeholder():
    # collections.py:211 pattern: first argument is a bound `?`, not the
    # literal 'now'. _translate() runs its `?` -> `%s` pass last, so the
    # placeholder must land in the right position for that pass to convert.
    out = _translate("WHERE x >= date(?, '-7 days')")
    assert "%s::date + INTERVAL '-7 days'" in out


def test_existing_plus_one_day_pattern_still_works():
    # Regression guard: the one pattern the old code already handled.
    out = _translate("date('now','+1 day')")
    assert "CURRENT_DATE + INTERVAL '+1 day'" in out


def test_bare_datetime_now_is_unaffected_by_the_new_interval_rules():
    # No comma -> must not be swallowed by the new comma-requiring rules.
    assert _translate("datetime('now')") == "NOW()"
    assert _translate("datetime('now', 'localtime')") == "NOW()"
    assert _translate("date('now')") == "CURRENT_DATE"
