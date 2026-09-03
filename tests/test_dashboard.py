from datetime import datetime

from onecpase.dashboard import (
    _build_trend_ranges,
    _effective_member_counts,
    build_itensity_reconciliation,
)
from onecpase.database import get_db


def test_reconciliation_sync_route_uses_reconciliation_payload(app, monkeypatch):
    def fake_sync(db):
        return {
            "snapshot": {"active_count": 2},
            "export_path": "/tmp/itensity.xlsx",
            "reconciliation": {
                "matched_count": 2,
                "status_mismatches": [],
                "missing_in_app": [],
                "missing_in_itensity": [],
            },
            "skipped": {},
        }

    monkeypatch.setattr("onecpase.dashboard.sync_itensity_members_to_local", fake_sync)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["tenant_slug"] = None

        response = client.post("/dashboard/reconciliation/sync")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard/reconciliation")


def test_sales_dashboard_renders_filtered_empty_funnel(app):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["tenant_slug"] = None

        response = client.get("/sales-dashboard?from=2026-08-01&to=2026-08-28")

    assert response.status_code == 200
    assert b"Sales Funnel" in response.data
    assert b"No untouched leads in this window." in response.data


def test_sales_dashboard_counts_legacy_activity_milestones(app):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES (?, ?, ?)",
            ("sales", "hash", "Sales User"),
        )
        user_id = db.execute("SELECT id FROM users WHERE username='sales'").fetchone()[0]
        db.execute(
            "INSERT INTO leads (full_name, assigned_to, lead_status, created_at) VALUES (?, ?, ?, ?)",
            ("Legacy prospect", user_id, "show", "2026-08-01 09:00:00"),
        )
        lead_id = db.execute("SELECT MAX(id) FROM leads").fetchone()[0]
        db.execute(
            "INSERT INTO lead_activities (lead_id, activity_type, outcome, created_by, occurred_at) VALUES (?, ?, ?, ?, ?)",
            (lead_id, "call", "appointment_booked", user_id, "2026-08-01 10:00:00"),
        )
        db.commit()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["tenant_slug"] = None
        response = client.get("/sales-dashboard?from=2026-08-01&to=2026-08-28")

    assert response.status_code == 200
    assert b">1<" in response.data


def test_dashboard_trends_cover_hours_through_year():
    now = datetime(2026, 8, 14, 12, 0)
    collections = [
        {"event_date": "2026-08-14", "amount": 120},
        {"event_date": "2026-08-13", "amount": 60},
        {"event_date": "2025-09-01", "amount": 30},
    ]
    members = [
        {"event_date": "2026-08-14"},
        {"event_date": "2026-08-08"},
    ]

    ranges = _build_trend_ranges(collections, members, now)

    assert list(ranges) == ["24h", "7d", "30d", "6m", "1y"]
    assert len(ranges["24h"]["labels"]) == 8
    assert ranges["24h"]["revenue"]["total"] == 120
    assert ranges["24h"]["revenue"]["delta"] == 100
    assert ranges["7d"]["members"]["total"] == 2
    assert len(ranges["1y"]["labels"]) == 12
    assert ranges["1y"]["revenue"]["total"] == 210


def test_snapshot_overrides_local_member_totals():
    active_members, total_members, inactive_members = _effective_member_counts(
        120,
        250,
        {"active_count": 1020, "inactive_count": 559, "total_count": 1999},
    )

    assert active_members == 1020
    assert total_members == 1999
    assert inactive_members == 559


def test_itensity_reconciliation_detects_drift():
    summary = build_itensity_reconciliation(
        {
            "Active": 120,
            "Blocked": 12,
            "Unverified": 9,
            "Inactive": 100,
        },
        {
            "Active": 1020,
            "Blocked": 389,
            "Unverified": 31,
            "Inactive": 559,
        },
    )

    assert summary["local_total"] == 241
    assert summary["live_total"] == 1999
    assert summary["drift_total"] == 1758
    assert summary["status_rows"][0]["status"] == "Active"
    assert summary["status_rows"][0]["delta"] == 900
