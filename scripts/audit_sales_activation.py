"""Report one row per new Sales application using recorded provider evidence.

Run from the application directory with ``python -m scripts.audit_sales_activation``.
The default cohort starts on the configured automatic Itensity rollout date.
The audit opens SQLite read-only and never starts the web app or its jobs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from flask import Flask, current_app, g

from onecpase.applications import (
    _itensity_hard_blockers, _loaded_by, _missing_requirements, _push_actor, _push_analysis,
)
from onecpase.config import Config
from onecpase.debicheck import itensity_client_ref_number, normalise_debicheck_status
from onecpase.integrations import itensity_writes_allowed

log = logging.getLogger(__name__)
SAST = ZoneInfo("Africa/Johannesburg")


def _read_only(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _reference(value):
    text = str(value or "").strip()
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def _stage(db, row, references, now):
    member_id, application_id = row["member_id"], row["id"]
    member_ref, confirmed_ref = _reference(row["member_ref"]), _reference(row["itensity_confirmed_ref"])
    push = row["push_status"]
    if member_ref and confirmed_ref and member_ref != confirmed_ref:
        return "reference_mismatch", ["Member and application have different Itensity references"]
    if any(references[ref] - {member_id} for ref in (member_ref, confirmed_ref) if ref):
        return "reference_mismatch", ["The Itensity reference is linked to another local member"]
    if push == "reconcile_required":
        return "local_recovery", ["Check the confirmed Itensity result and complete local activation"]
    if push == "push_unknown":
        return "provider_outcome_unknown", ["Check Itensity before retrying the submission"]
    if push == "push_failed":
        return "push_failed", ["Review the failed push in the application history before retrying"]
    if push == "pushing":
        try:
            updated = datetime.fromisoformat(row["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            stale = now - updated >= timedelta(minutes=30)
        except (ValueError, TypeError):
            stale = True
        return ("push_stalled", ["Review the push outcome before retrying"]) if stale else ("push_in_progress", [])
    if row["application_status"] == "active" or push == "pushed":
        if row["application_status"] == "active" and push == "pushed" and member_ref and member_ref == confirmed_ref:
            return "activation_recorded", []
        return "activation_needs_evidence", ["Activation and the confirmed Itensity reference need reconciliation"]
    if not row["linked_member_id"]:
        return "waiting_for_member", ["Link the application to a 1Cpace member record"]
    if member_ref or confirmed_ref:
        return "existing_itensity_reference", ["Review the existing Itensity record instead of creating another one"]
    if row["payment_type"] == "Third-Party Debit Order":
        return "manual_provider_workflow", ["Third-party payer setup requires manual Itensity handling"]

    checks = db.execute("SELECT * FROM compliance_checklists WHERE application_id=?", (application_id,)).fetchone()
    analysis = db.execute("SELECT * FROM statement_analyses WHERE application_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                          (application_id,)).fetchone()
    checklist = _missing_requirements(checks, _push_analysis(db, member_id, analysis)) if checks else _missing_requirements(None)
    blockers = list(dict.fromkeys(checklist + _itensity_hard_blockers(db, application_id, member_id)))
    if blockers:
        return "waiting_for_compliance", blockers
    rollout = current_app.config.get("ITENSITY_AUTO_PUSH_FROM")
    if (push != "not_pushed" or not current_app.config.get("SCHEDULER_ENABLED") or not rollout
            or row["created_day"] < rollout or not itensity_writes_allowed(application_id)):
        return "outside_automatic_push", ["The application is outside the configured automatic push rules"]
    try:
        _push_actor(db, _loaded_by(db, application_id), None)
    except ValueError:
        return "waiting_for_authorized_staff", ["The original loader cannot activate members"]
    return "ready_for_automatic_push", []


def build_audit(db, from_date, through_date, *, now=None, nupay_rows=None, roster=None):
    now = now or datetime.now(timezone.utc)
    rows = db.execute("""
        SELECT a.id, a.member_id, a.application_status, a.push_status, a.updated_at,
               date(a.created_at) AS created_day, a.itensity_confirmed_ref,
               m.id AS linked_member_id, m.itensity_ref AS member_ref, m.payment_type,
               d.id AS mandate_id, d.status AS mandate_status, d.imported_from_nupay,
               CASE WHEN COALESCE(d.nupay_response, '') <> '' THEN 1 ELSE 0 END AS has_nupay_response
        FROM membership_applications a
        LEFT JOIN members m ON m.id=a.member_id
        LEFT JOIN debicheck_mandates d ON d.id=(
            SELECT MAX(dm.id) FROM debicheck_mandates dm WHERE dm.member_id=m.id)
        WHERE date(a.created_at) BETWEEN ? AND ?
        ORDER BY a.id
    """, (from_date.isoformat(), through_date.isoformat())).fetchall()
    references = defaultdict(set)
    for row in db.execute("""
        SELECT id AS member_id, itensity_ref AS ref FROM members WHERE itensity_ref IS NOT NULL
        UNION ALL
        SELECT member_id, itensity_confirmed_ref FROM membership_applications
        WHERE itensity_confirmed_ref IS NOT NULL AND member_id IS NOT NULL
    """):
        if ref := _reference(row["ref"]):
            references[ref].add(row["member_id"])

    sync = None
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='payment_sync_state'").fetchone():
        record = db.execute("SELECT status, last_success_at FROM payment_sync_state WHERE source='nupay'").fetchone()
        sync = dict(record) if record else None
    applications = []
    live_nupay = _nupay_matches(db, rows, nupay_rows) if nupay_rows else {}
    for row in rows:
        stage, reasons = _stage(db, row, references, now)
        mandate = row["mandate_status"]
        entry = {
            "application_id": row["id"], "created_day": row["created_day"], "stage": stage,
            "reasons": reasons, "application_status": row["application_status"], "push_status": row["push_status"],
            "recorded_nupay_status": normalise_debicheck_status(mandate) if mandate else "no_mandate_recorded",
            "nupay_evidence": ("imported_mandate" if row["imported_from_nupay"] else
                               "response_recorded" if row["has_nupay_response"] else "local_record_only" if mandate else "none"),
            "itensity_reference_saved": bool(_reference(row["member_ref"])),
            "itensity_confirmation_saved": bool(_reference(row["itensity_confirmed_ref"])),
        }
        if evidence := live_nupay.get(row["id"]):
            entry["nupay_report_check"] = evidence
        if roster is not None and entry["itensity_reference_saved"]:
            matches = roster[0][_reference(row["member_ref"])]
            entry["itensity_roster_check"] = (
                "found" if matches == 1 else "duplicate" if matches > 1 else "not_found" if roster[1] else "unverified"
            )
        applications.append(entry)
    return {
        "from_date": from_date.isoformat(), "through_date": through_date.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"), "tenant": g.get("tenant_slug"),
        "evidence_scope": "current_provider_checks" if nupay_rows or roster else "recorded_evidence_only",
        "provider_evidence": {
            "nupay": "current_report" if nupay_rows else "recorded_status_only",
            "itensity": "reconciled_roster" if roster and roster[1] else "partial_roster" if roster else "saved_references_only",
        },
        "nupay_sync": sync, "application_count": len(applications),
        "counts_by_stage": dict(Counter(row["stage"] for row in applications)), "applications": applications,
    }


def _nupay_matches(db, applications, report_rows):
    from onecpase.encryption import decrypt_mandate, hash_for_lookup
    from onecpase.nupay_driver import _acceptance_key, match_mandate_row
    from onecpase.nupay_sync import _client_reference_digits, _member_by_reference, _report_status

    refs, identities = defaultdict(list), defaultdict(list)
    for member in db.execute("SELECT id, itensity_ref, join_date, id_number_hash FROM members"):
        if ref := itensity_client_ref_number(_reference(member["itensity_ref"])):
            refs[ref].append(member)
        if identity := member["id_number_hash"]:
            identities[identity].append(member)
    by_ref = {ref: matches[0] for ref, matches in refs.items() if len(matches) == 1}
    approved_members = set()
    for provider_row in report_rows:
        if normalise_debicheck_status(_report_status(provider_row)) == "approved":
            member = _member_by_reference(provider_row, by_ref)
            reference = _client_reference_digits(provider_row)
            if member is None and len(reference) == 13 and identities:
                matches = identities.get(hash_for_lookup(reference), [])
                member = matches[0] if len(matches) == 1 else None
            if member and (not member["join_date"] or _acceptance_key(provider_row) >= member["join_date"]):
                approved_members.add(member["id"])

    results, claims = {}, defaultdict(list)
    for row in applications:
        aid = row["id"]
        if row["mandate_id"] is None:
            if row["member_id"] in approved_members:
                results[aid] = "approved_without_local_record"
            continue
        mandate = db.execute("""SELECT d.*, m.join_date AS member_join_date FROM debicheck_mandates d
                              JOIN members m ON m.id=d.member_id WHERE d.id=?""", (row["mandate_id"],)).fetchone()
        identifiers = decrypt_mandate(mandate)
        # A shared payer's bank account cannot confirm a particular member's mandate.
        identifiers.pop("account_number", None)
        matched, older = match_mandate_row(report_rows, identifiers)
        if matched is None:
            results[aid] = "no_match"
        elif older:
            results[aid] = "older_match"
        else:
            provider_status = normalise_debicheck_status(_report_status(matched))
            local_status = normalise_debicheck_status(row["mandate_status"])
            if provider_status == "unknown":
                results[aid] = "unverified_status"
            else:
                results[aid] = "agrees" if provider_status == local_status else "differs"
            claims[id(matched)].append((aid, row["member_id"]))
    for matched in claims.values():
        if len({member_id for _, member_id in matched}) > 1:
            for aid, _ in matched:
                results[aid] = "ambiguous_match"
    return results


def _read_itensity_roster():
    from playwright.sync_api import sync_playwright
    from onecpase.playwright_lock import PLAYWRIGHT_LOCK
    from scripts.itensity_portal import MEMBERS_PAGE, credentials, dashboard_chips, fetch_paged, login, open_context_page
    from scripts.itensity_reconcile import reconcile
    from scripts.pull_itensity_roster import ROSTER_ENDPOINT

    with PLAYWRIGHT_LOCK, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=20_000)
        try:
            page = browser.new_page()
            page.set_default_timeout(20_000)
            chips = dashboard_chips(login(page, *credentials()))
            open_context_page(page, MEMBERS_PAGE, set())
            rows = fetch_paged(page, ROSTER_ENDPOINT)
        finally:
            browser.close()
    refs = Counter(_reference(row.get("userid")) for row in rows)
    if not refs or "" in refs:
        raise ValueError("Itensity roster has missing member references")
    complete, _ = reconcile({"snapshot": {"chips": chips}, "rows": [{"roster": row} for row in rows]})
    return refs, complete


def _audit_file(app, tenant_slug):
    return Path(app.root_path).parent / "data" / "payment_sync" / tenant_slug / "sales-activation-audit.json"


def refresh_after_nupay(app, tenant, mandate_rows, sync_stamp):
    """Publish the daily result after a committed NuPay import, without touching its transaction."""
    rollout = app.config.get("ITENSITY_AUTO_PUSH_FROM")
    if not rollout or tenant["slug"] != app.config.get("ITENSITY_TENANT_SLUG"):
        return
    start, end = date.fromisoformat(rollout), datetime.now(SAST).date()
    if start > end:
        return
    try:
        roster = _read_itensity_roster()
    except Exception as error:
        log.warning("Sales activation audit: Itensity roster unavailable (%s)", type(error).__name__)
        roster = None
    with closing(_read_only(tenant["db_path"])) as db:
        db.execute("BEGIN")
        sync = db.execute("SELECT last_success_at FROM payment_sync_state WHERE source='nupay'").fetchone()
        if not sync or sync["last_success_at"] != sync_stamp:
            return
        result = build_audit(db, start, end, now=datetime.now(SAST), nupay_rows=mandate_rows, roster=roster)

    path = _audit_file(app, tenant["slug"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(result, output)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def latest_exceptions(app, tenant_slug):
    if not tenant_slug or tenant_slug != app.config.get("ITENSITY_TENANT_SLUG"):
        return None
    try:
        result = json.loads(_audit_file(app, tenant_slug).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(result, dict) or result.get("tenant") != tenant_slug:
        return None
    reasons = {
        "approved_without_local_record": "NuPay shows an approved mandate missing from 1Cpace",
        "no_match": "The recorded mandate could not be matched in the NuPay report",
        "older_match": "Only an older NuPay mandate matched",
        "differs": "NuPay and 1Cpace show different mandate statuses",
        "ambiguous_match": "The NuPay report match is shared by multiple members",
        "unverified_status": "The matching NuPay mandate has an unrecognised status",
    }
    exceptions = []
    for row in result["applications"]:
        # Normal compliance work is already tracked in the application workflow.
        details = [] if row["stage"] == "waiting_for_compliance" else list(row["reasons"])
        nupay_check = row.get("nupay_report_check")
        if nupay_check == "no_match" and row["recorded_nupay_status"] != "approved":
            nupay_check = None
        if reason := reasons.get(nupay_check):
            details.append(reason)
        if row.get("itensity_roster_check") == "not_found":
            details.append("The saved reference was absent from the reconciled Itensity roster")
        if row.get("itensity_roster_check") == "duplicate":
            details.append("The saved reference appeared more than once in the Itensity roster")
        if details:
            exceptions.append(dict(row, reasons=details))
    return dict(result, applications=exceptions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", type=date.fromisoformat)
    parser.add_argument("--through-date", type=date.fromisoformat)
    args = parser.parse_args()
    if Config.DATABASE_URL:
        parser.error("The Sales activation audit requires a registered SQLite tenant.")
    start = args.from_date or (date.fromisoformat(Config.ITENSITY_AUTO_PUSH_FROM) if Config.ITENSITY_AUTO_PUSH_FROM else None)
    end = args.through_date or datetime.now(SAST).date()
    if start is None or start > end:
        parser.error("Provide a start date on or before the end date.")
    with closing(_read_only(Config.PLATFORM_DATABASE_PATH)) as registry:
        tenant = registry.execute("SELECT slug, db_path FROM tenants WHERE slug=? AND active=1",
                                  (Config.ITENSITY_TENANT_SLUG,)).fetchone()
    if tenant is None:
        parser.error("The configured Itensity tenant is not active in the registry.")
    app = Flask(__name__)
    app.config.from_object(Config)
    with app.app_context(), closing(_read_only(tenant["db_path"])) as db:
        g.tenant_slug = tenant["slug"]
        db.execute("BEGIN")
        result = build_audit(db, start, end)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
