from collections import Counter
from contextlib import closing
from datetime import date

from flask import g
import pytest

from onecpase import nupay_driver, payment_sync, scheduler
from onecpase.database import get_db
from scripts import audit_sales_activation as audit_service
from scripts.audit_sales_activation import _read_only, build_audit
from tests.test_itensity import _login
from tests.test_itensity_auto_push import _ready, _sweep


def test_sales_audit_distinguishes_activation_evidence_and_reference_conflict(app):
    application_id, member_id, path = _ready(app)
    app.config.update(SCHEDULER_ENABLED=True, ITENSITY_AUTO_PUSH_FROM="2000-01-01")

    def audit():
        with app.app_context(), closing(_read_only(path)) as db:
            g.tenant_slug = app.config["ITENSITY_TENANT_SLUG"]
            report = build_audit(db, date(2000, 1, 1), date(2099, 1, 1))
        assert report["application_count"] == 1
        assert report["applications"][0]["application_id"] == application_id
        return report["applications"][0]

    assert audit()["stage"] == "ready_for_automatic_push"
    _sweep(app, path)
    assert audit()["stage"] == "activation_recorded"
    with app.app_context():
        db = get_db()
        db.execute("UPDATE membership_applications SET itensity_confirmed_ref=NULL WHERE id=?", (application_id,))
        db.commit()
    assert audit()["stage"] == "activation_needs_evidence"
    with app.app_context():
        db = get_db()
        db.execute("UPDATE membership_applications SET itensity_confirmed_ref='IT-99' WHERE id=?", (application_id,))
        db.execute("UPDATE members SET itensity_ref='IT-100' WHERE id=?", (member_id,))
        db.commit()
    assert audit()["stage"] == "reference_mismatch"


def test_sales_audit_uses_live_activation_gates_for_waiting_reasons(app):
    _, member_id, path = _ready(app)
    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM member_documents WHERE member_id=? AND document_type='Signed Contract'", (member_id,))
        db.commit()
        g.tenant_slug = app.config["ITENSITY_TENANT_SLUG"]
        with closing(_read_only(path)) as reader:
            report = build_audit(reader, date(2000, 1, 1), date(2099, 1, 1))
    assert report["counts_by_stage"] == {"waiting_for_compliance": 1}
    assert "Contract signing not confirmed" in report["applications"][0]["reasons"]


def test_sales_audit_requires_a_direct_reference_for_an_unrecorded_mandate(app):
    _, member_id, path = _ready(app)
    with app.app_context():
        g.tenant_slug = app.config["ITENSITY_TENANT_SLUG"]
        db = get_db()
        db.execute("UPDATE members SET itensity_ref='2167518', join_date='2026-10-22' WHERE id=?", (member_id,))
        db.execute("DELETE FROM debicheck_mandates WHERE member_id=?", (member_id,))
        db.commit()
        report = [
            {"client_reference": "2167518", "status": "Active", "date_created": "2026-10-21"},
            {"client_reference": "1234567", "status": "Active", "date_created": "2026-10-23"},
        ]
        with closing(_read_only(path)) as reader:
            start, end = date(2000, 1, 1), date(2099, 1, 1)
            row = build_audit(reader, start, end, nupay_rows=report)["applications"][0]
            assert "nupay_report_check" not in row
            report[0]["date_created"] = "2026-10-22"
            row = build_audit(reader, start, end, nupay_rows=report)["applications"][0]
            assert row["nupay_report_check"] == "approved_without_local_record"


def test_nupay_sync_updates_member_compliance_exceptions(app, client, tmp_path, monkeypatch):
    application_id, _, path = _ready(app)
    app.config.update(ITENSITY_TENANT_SLUG="elev8", ITENSITY_AUTO_PUSH_FROM="2000-01-01")
    report_path = tmp_path / "audit.json"
    monkeypatch.setattr(audit_service, "_audit_file", lambda *args: report_path)
    monkeypatch.setattr(nupay_driver, "fetch_transaction_report_rows", lambda *args: [])
    monkeypatch.setattr(nupay_driver, "fetch_mandate_report_rows", lambda: ([{
        "client_reference": "ITENSITY-001", "status": "Active", "date_created": "2099-01-01",
    }], ""))
    monkeypatch.setattr(scheduler, "_backup_before_write", lambda *args: None)
    monkeypatch.setattr(audit_service, "_read_itensity_roster", lambda: (Counter({"IT-99": 1}), False))
    monkeypatch.setattr(payment_sync, "Path", lambda value: tmp_path / "onecpase")
    tenant = dict(slug="elev8", db_path=path)

    _sweep(app, path)
    _login(client)
    url = "/reports/analytics/compliance"
    assert b"Waiting for the first scheduled audit" in client.get(url).data
    payment_sync.run_source(app, tenant, "nupay")

    result = audit_service.latest_exceptions(app, "elev8")
    assert result["application_count"] == 1
    assert result["nupay_sync"]["status"] == "success"
    assert result["applications"] == []
    assert audit_service.latest_exceptions(app, "another-tenant") is None
    page = client.get(url)
    assert page.status_code == 200
    assert b"No exceptions found in the available evidence" in page.data
    assert b"The Itensity roster was incomplete" in page.data

    with app.app_context():
        db = get_db()
        db.execute("UPDATE membership_applications SET itensity_confirmed_ref='IT-100' WHERE id=?", (application_id,))
        db.commit()
    payment_sync.run_source(app, tenant, "nupay")
    page = client.get(url)
    assert page.status_code == 200
    assert f'href="/applications/{application_id}"'.encode() in page.data
    assert b"Member and application have different Itensity references" in page.data


@pytest.mark.parametrize("fail_fetch", [False, True])
def test_nupay_import_status_is_independent_of_sales_audit(app, tmp_path, monkeypatch, fail_fetch):
    _, _, path = _ready(app)
    app.config.update(ITENSITY_TENANT_SLUG="elev8", ITENSITY_AUTO_PUSH_FROM="2000-01-01")
    monkeypatch.setattr(payment_sync, "Path", lambda value: tmp_path / "onecpase")
    monkeypatch.setattr(scheduler, "_backup_before_write", lambda *args: None)
    monkeypatch.setattr(nupay_driver, "fetch_transaction_report_rows", lambda *args: [])
    monkeypatch.setattr(nupay_driver, "fetch_mandate_report_rows", lambda: (None if fail_fetch else [], "unavailable"))
    calls = []

    def fail_audit(*args):
        calls.append(args)
        raise OSError("audit destination unavailable")

    monkeypatch.setattr(audit_service, "refresh_after_nupay", fail_audit)
    payment_sync.run_source(app, dict(slug="elev8", db_path=path), "nupay")
    with closing(_read_only(path)) as db:
        row = db.execute("SELECT status, last_success_at FROM payment_sync_state WHERE source='nupay'").fetchone()
    assert row["status"] == ("failed" if fail_fetch else "success")
    if fail_fetch:
        assert calls == []
    else:
        assert len(calls) == 1
        assert calls[0][-1] == row["last_success_at"]
