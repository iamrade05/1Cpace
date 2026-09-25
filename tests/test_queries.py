"""onecpase/queries.py's _account_summary() dispute count.

The LIKE '%dispute%' pattern used to be inlined into the SQL text. On
PostgreSQL, once db_adapter.py's `?` -> `%s` translation runs and psycopg2
does its own %-style substitution over the whole query (because a non-empty
params tuple is passed), a literal '%d' inside '%dispute%' collides with
that and breaks the query. Binding the pattern as a parameter, the same way
member_id already is, sidesteps the problem entirely.
"""
import inspect

from onecpase.database import get_db
from onecpase.queries import _account_summary


def _seed_member_with_query(db, description="Client disputes this amount", query_type="Payments"):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact, member_status)
           VALUES ('T-1', 'Test', 'Member', 'ID1', '0710000000', 'Active')"""
    )
    mid = db.execute("SELECT id FROM members WHERE member_ref='T-1'").fetchone()["id"]
    db.execute(
        """INSERT INTO queries (member_id, category, query_type, description, status)
           VALUES (?, 'Payments', ?, ?, 'open')""",
        (mid, query_type, description),
    )
    db.commit()
    return mid


def test_dispute_pattern_is_bound_not_inlined():
    # The pattern legitimately still appears as a *parameter value* (e.g.
    # `"%dispute%"` in the params tuple) after the fix - what must be gone is
    # the pattern written directly into the SQL text as `LIKE '%dispute%'`,
    # which is what collides with psycopg2's %-substitution on Postgres.
    source = inspect.getsource(_account_summary)
    assert "LIKE '%dispute%'" not in source


def test_dispute_count_is_still_correct_after_parameterizing(app):
    with app.app_context():
        db = get_db()
        mid = _seed_member_with_query(db)
        assert _account_summary(db, mid)["dispute_count"] == 1


def test_dispute_count_matches_on_query_type_too(app):
    with app.app_context():
        db = get_db()
        mid = _seed_member_with_query(db, description="Unrelated note", query_type="Dispute")
        assert _account_summary(db, mid)["dispute_count"] == 1


def test_dispute_count_is_zero_when_nothing_matches(app):
    with app.app_context():
        db = get_db()
        mid = _seed_member_with_query(db, description="Unrelated note", query_type="Payments")
        assert _account_summary(db, mid)["dispute_count"] == 0


def test_inlined_like_pattern_breaks_percent_style_substitution():
    # Mirrors psycopg2's own %-style substitution over the whole query text
    # once params are passed - the same failure mode a hardcoded '%dispute%'
    # would hit on Postgres. Demonstrates *why* the fix matters.
    import pytest

    broken = "SELECT 1 WHERE x=%s AND y LIKE '%dispute%'"
    with pytest.raises((TypeError, ValueError)):
        broken % (1,)


def test_bound_like_pattern_survives_percent_style_substitution():
    fixed = "SELECT 1 WHERE x=%s AND y LIKE %s"
    assert fixed % (1, "%dispute%") == "SELECT 1 WHERE x=1 AND y LIKE %dispute%"
