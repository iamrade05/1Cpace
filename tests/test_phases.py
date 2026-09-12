"""Phased go-live: what a closed phase must not show, and must not serve.

The conftest runs the rest of the suite at ACTIVE_PHASE=all so every area is
reachable. Phase gating is therefore only covered here.

Both halves matter and they failed independently: the request gate refused
closed blueprints from the start, but base.html never called phase_open, so
every link to a closed area stayed in the nav and simply bounced whoever
clicked it.
"""

import pytest

from onecpase import create_app
from onecpase.phases import (
    enabled_blueprints,
    is_enabled,
    nav_section_enabled,
    phase_of,
)


# One representative endpoint per area that Phase 1 keeps closed.
CLOSED_AT_PHASE_ONE = (
    "collections.collections_index",
    "ptp.ptp_dashboard",
    "queries.index",
    "fitness.fitness_index",
    "reports.index",
    "hr.staff_index",
    "operations.equipment_index",
    "turnstile.index",
    "access_report.index",
)

# Sales-process areas that must stay reachable at Phase 1.
OPEN_AT_PHASE_ONE = (
    "leads.leads_index",
    "members.members_index",
    "debicheck.index",
)


def build_app(tmp_path, phase):
    return create_app({
        "TESTING": True,
        "DATABASE_PATH": str(tmp_path / "test.db"),
        "PLATFORM_DATABASE_PATH": str(tmp_path / "platform.db"),
        "TENANTS_DIR": str(tmp_path / "tenants"),
        "SECRET_KEY": "test-secret",
        "ENCRYPTION_KEY": "kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8=",
        "WTF_CSRF_ENABLED": False,
        "TURNSTILE_ENABLED": False,
        "ACTIVE_PHASE": phase,
    })


def render_nav(app):
    """The dashboard as an admin sees it — admin passes every has_perm check,
    so anything missing from this nav is missing because of the phase."""
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 1
            session["tenant_slug"] = None
            session["role"] = "admin"
        return client.get("/dashboard", follow_redirects=True).get_data(as_text=True)


def paths_for(app, endpoints):
    with app.test_request_context():
        from flask import url_for
        return {name: url_for(name) for name in endpoints}


def links_to(html, path):
    """True if the nav links to *path*.

    Matched without the closing quote because some nav entries append a query
    string — the Reports link is url_for('reports.index') + "?tab=overview".
    """
    return f'href="{path}"' in html or f'href="{path}?' in html


def test_phase_one_nav_hides_every_closed_area(tmp_path):
    app = build_app(tmp_path, "1")
    html = render_nav(app)
    closed = paths_for(app, CLOSED_AT_PHASE_ONE)
    still_shown = [name for name, path in closed.items() if links_to(html, path)]
    assert not still_shown, f"closed areas still linked in the nav: {still_shown}"


def test_phase_one_nav_still_shows_the_sales_process(tmp_path):
    app = build_app(tmp_path, "1")
    html = render_nav(app)
    for name, path in paths_for(app, OPEN_AT_PHASE_ONE).items():
        assert links_to(html, path), f"{name} should be open at phase 1"


def test_phase_all_shows_the_closed_areas(tmp_path):
    """Proves the phase-1 assertion above is meaningful rather than vacuous."""
    app = build_app(tmp_path, "all")
    html = render_nav(app)
    for name, path in paths_for(app, CLOSED_AT_PHASE_ONE).items():
        assert links_to(html, path), f"{name} should be visible at ACTIVE_PHASE=all"


def test_closed_area_is_refused_not_merely_hidden(tmp_path):
    """A hidden link is not a closed door — typing the URL must fail too."""
    app = build_app(tmp_path, "1")
    closed = paths_for(app, CLOSED_AT_PHASE_ONE)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 1
            session["tenant_slug"] = None
            session["role"] = "admin"
        for name, path in closed.items():
            response = client.get(path, follow_redirects=False)
            assert response.status_code in (302, 403), (
                f"{name} at {path} answered {response.status_code}; "
                "a closed phase must redirect or refuse"
            )


def test_dashboard_does_not_leak_closed_phase_figures(tmp_path):
    html = render_nav(build_app(tmp_path, "1"))
    assert "Arrears exposure" not in html
    assert "Outstanding exposure" not in html
    assert "Collection efficiency" not in html
    assert "Leave approvals waiting" not in html


@pytest.mark.parametrize("phase,expected", [(1, False), (2, True), ("all", True)])
def test_nav_section_follows_its_phase(phase, expected):
    assert nav_section_enabled("connections", phase) is expected
    assert nav_section_enabled("sales", phase) is True


def test_earlier_phases_stay_open_as_later_ones_arrive():
    at_three = enabled_blueprints(3)
    assert {"leads", "members", "debicheck"} <= at_three   # phase 1
    assert {"collections", "ptp"} <= at_three              # phase 2
    assert "queries" in at_three                           # phase 3
    assert "hr" not in at_three                            # phase 6, not yet


def test_unknown_blueprint_is_refused_rather_than_waved_through():
    """A new area has to be placed in a phase deliberately."""
    assert is_enabled("some_new_area", "1") is False
    assert is_enabled("some_new_area", "all") is False
    assert phase_of("some_new_area") is None


def test_unreadable_phase_falls_back_to_one():
    assert is_enabled("leads", "not-a-number") is True
    assert is_enabled("collections", "not-a-number") is False
