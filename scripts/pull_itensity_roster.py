"""Pull Itensity's full member roster — all of it, plus the counts to prove it.

The Data Export report (scripts/pull_itensity_export.py) can only ever return
fully-confirmed memberships: its server-side SQL ends in
``ADMIN_CONFIRM = 1 AND MEM_CONFIRM = 1``, which is 1,892 rows against a real
roster of ~2,049. This pulls /XML/active_members.php instead, which returns
every person, and enriches the confirmed ones from the detail reports.

Writes data/itensity_roster_<stamp>.json:

    {"snapshot": {...Itensity's own counts...},
     "sources": {...row counts per endpoint...},
     "rows":    [...one entry per person, details merged...]}

The snapshot is read in the same session as the roster, so the two cannot drift
apart while staff work in the portal. Nothing here writes to Itensity or to any
1Cpase database — scripts/itensity_reconcile.py decides whether the roster is
complete enough to import.

Usage:
    python scripts/pull_itensity_roster.py
    python scripts/pull_itensity_roster.py --headed --out data/roster.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

try:  # run as a script (scripts/ on sys.path) or imported as scripts.<module>
    from itensity_portal import (
        BASE, DATA_REPORT_PAGE, MEMBERS_PAGE, NEW_PORTAL, ROOT, credentials,
        dashboard_chips, fetch_json, fetch_paged, login, open_context_page, rows_of,
    )
except ImportError:  # pragma: no cover - import path differs under the scheduler
    from scripts.itensity_portal import (  # type: ignore[no-redef]
        BASE, DATA_REPORT_PAGE, MEMBERS_PAGE, NEW_PORTAL, ROOT, credentials,
        dashboard_chips, fetch_json, fetch_paged, login, open_context_page, rows_of,
    )

ROSTER_ENDPOINT = "/XML/active_members.php"
DETAIL_ENDPOINTS = {
    "personal": "/XML/personal_details_extract_report.php",
    "membership": "/XML/membership_details_extract_report.php",
    "banking": "/XML/banking_details_extract_report.php",
    "extract": "/XML/data_extract_report.php?skip=0&take=5000&filter=",
}


def ref_of(value: Any) -> str:
    """Itensity's user id, as text — the join key between every report.

    active_members calls it ``userid``; the detail reports call it ``id``.
    """
    text = str(value or "").strip()
    return text[:-2] if text.endswith(".0") else text


def index_by_ref(rows: list[dict[str, Any]], key: str = "id") -> dict[str, dict[str, Any]]:
    return {ref_of(row.get(key)): row for row in rows if ref_of(row.get(key))}


def merge_roster(
    roster: list[dict[str, Any]], details: dict[str, dict[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """One record per person: the roster row, plus whatever detail exists.

    Only the confirmed ~1,869 appear in the detail reports, so ``sources`` on
    each row records which ones actually contributed — that is what tells the
    importer whether a person is thinly known (Unverified) or fully described.
    """
    merged = []
    for row in roster:
        ref = ref_of(row.get("userid"))
        record: dict[str, Any] = {"ref": ref, "roster": row}
        present = []
        for name, index in details.items():
            match = index.get(ref)
            if match:
                record[name] = match
                present.append(name)
        record["sources"] = present
        merged.append(record)
    return merged


def capture_reporting_api(page: Page) -> list[dict[str, Any]]:
    """The new portal's SPA calls a reporting API for the same figures the
    chips show. Captured as corroboration; the chips are the primary source."""
    captured: list[dict[str, Any]] = []

    def on_response(response) -> None:
        if "reporting.api.itensityonline.com" in response.url and "report/query" in response.url:
            try:
                captured.append({"url": response.url, "body": response.json()})
            except Exception:
                pass

    page.on("response", on_response)
    try:
        page.goto(NEW_PORTAL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(12000)
    except Exception:
        pass
    page.remove_listener("response", on_response)
    return captured


def pull_roster(headless: bool = True) -> dict[str, Any]:
    username, password = credentials()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_context().new_page()
        try:
            home_text = login(page, username, password)
            chips = dashboard_chips(home_text)

            opened: set[str] = set()
            open_context_page(page, MEMBERS_PAGE, opened)
            roster = fetch_paged(page, ROSTER_ENDPOINT, take=100)

            open_context_page(page, DATA_REPORT_PAGE, opened)
            details = {
                name: index_by_ref(rows_of(fetch_json(page, path)))
                for name, path in DETAIL_ENDPOINTS.items()
            }

            reporting = capture_reporting_api(page)
        finally:
            browser.close()

    return {
        "pulled_at": datetime.now().isoformat(" ", "seconds"),
        "base": BASE,
        "snapshot": {"chips": chips, "reporting_api": reporting},
        "sources": {
            "roster": len(roster),
            **{name: len(index) for name, index in details.items()},
        },
        "rows": merge_roster(roster, details),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / f"itensity_roster_{datetime.now():%Y%m%d_%H%M%S}.json",
    )
    args = parser.parse_args()

    payload = pull_roster(headless=not args.headed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    print("Itensity chips:", payload["snapshot"]["chips"] or "(not found)")
    print("Rows pulled:", payload["sources"])
    members = sum(1 for row in payload["rows"] if str(row["roster"].get("role")) == "Member")
    print(f"People: {len(payload['rows'])}  (members: {members})")
    print(f"Saved: {args.out}")
    print(f"Next: python scripts/itensity_reconcile.py \"{args.out}\"")


if __name__ == "__main__":
    main()
