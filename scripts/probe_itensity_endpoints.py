"""Find which Itensity endpoint returns the *full* member roster.

Read-only. It logs into the portal, calls each candidate endpoint from inside
the authenticated page, and writes a summary. It never posts to Itensity and
never touches a 1Cpase database.

Why: the Data Export report (/XML/data_extract_report.php) is filtered
server-side by ``ADMIN_CONFIRM = 1 AND MEM_CONFIRM = 1``, so it returns 1,892
rows — 1,869 members — while Itensity's own dashboard counts ~2,049. The gap is
by design, not truncation, so the fix is a different source, not a better
scrape. This script establishes which source that is.

Usage:
    python scripts/probe_itensity_endpoints.py
    python scripts/probe_itensity_endpoints.py --headed
    python scripts/probe_itensity_endpoints.py --reports    # also drive the
                                                            # Member Reports UI

Credentials come from the sibling 1Cpace app's .env (ITENSITY_URL,
ITENSITY_USERNAME, ITENSITY_PASSWORD) via RADEPULSE_PATH — the same hand-off
scripts/pull_itensity_export.py uses.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

RADEPULSE_PATH = Path(os.environ.get("RADEPULSE_PATH", ROOT.parent / "1Cpace"))
load_dotenv(RADEPULSE_PATH / ".env", override=False)

BASE = os.environ.get(
    "ITENSITY_URL", "https://elev8healthandfitness.itensityonline.com"
).rstrip("/")
NEW_PORTAL = "https://elev8healthandfitness.new.itensityonline.com/"

# Candidates, cheapest first. The paged one is fetched page by page; the rest
# are single calls that return either a bare list or {totalCount, items}.
#
# Each carries the page that has to be open first: these /XML/ endpoints build
# their query from session state the owning report page sets up, and answer an
# empty list if it was never visited.
SINGLE_PROBES = [
    ("data_extract_report", "/XML/data_extract_report.php?skip=0&take=5000&filter=", "/Data_Report.php"),
    ("personal_details", "/XML/personal_details_extract_report.php", "/Data_Report.php"),
    ("membership_details", "/XML/membership_details_extract_report.php", "/Data_Report.php"),
    ("banking_details", "/XML/banking_details_extract_report.php", "/Data_Report.php"),
]
PAGED_PROBES = [("active_members", "/XML/active_members.php", 100, "/Members.php")]

REPORT_PAGES = [
    "/member_report.php",
    "/dynamic_report.php?report_id=7",
    "/dynamic_report.php?report_id=109",
    "/dynamic_report.php?report_id=219",
    "/dynamic_report.php?report_id=271",
]

FETCH_JSON = """async url => {
    const response = await fetch(url, {credentials: 'include'});
    const text = await response.text();
    let parsed = null;
    try { parsed = JSON.parse(text); } catch (err) { parsed = null; }
    return {status: response.status, bytes: text.length, json: parsed,
            head: parsed === null ? text.slice(0, 200) : null};
}"""


def login(page: Page, username: str, password: str) -> str:
    """Log in and prove it worked — a failed login still answers 200 with an
    empty list on every /XML/ endpoint, which reads as 'no members' rather than
    'not signed in'."""
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
    page.fill("input[name=txt_email]", username)
    page.fill("input[name=password]", password)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_timeout(3000)

    body = page.locator("body").inner_text()
    if "incorrect" in body.lower() or page.locator("input[name=txt_email]").count():
        raise RuntimeError(
            "Itensity rejected the sign-in — check ITENSITY_USERNAME/PASSWORD "
            f"(page still at {page.url})"
        )
    return body


def open_context_page(page: Page, path: str, opened: set[str]) -> None:
    if path in opened:
        return
    page.goto(BASE + path, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    opened.add(path)


def rows_of(payload: Any) -> list[dict[str, Any]]:
    """Both shapes appear in this portal: a bare list, or {totalCount, items}."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        return [row for row in (payload.get("items") or []) if isinstance(row, dict)]
    return []


def summarise(name: str, payload: Any, status: int, byte_count: int) -> dict[str, Any]:
    rows = rows_of(payload)
    total = payload.get("totalCount") if isinstance(payload, dict) else None
    roles = Counter(
        str(row.get("role") or row.get("Role") or "").strip() or "(none)" for row in rows
    )
    return {
        "endpoint": name,
        "http_status": status,
        "bytes": byte_count,
        "total_count": total,
        "rows": len(rows),
        "members": roles.get("Member", 0),
        "roles": dict(roles.most_common(10)),
        "columns": list(rows[0]) if rows else [],
    }


def probe_single(page: Page, name: str, path: str) -> dict[str, Any]:
    result = page.evaluate(FETCH_JSON, BASE + path)
    if result["json"] is None:
        return {
            "endpoint": name,
            "http_status": result["status"],
            "bytes": result["bytes"],
            "error": "response was not JSON",
            "head": result["head"],
        }
    return summarise(name, result["json"], result["status"], result["bytes"])


def probe_paged(page: Page, name: str, path: str, take: int) -> dict[str, Any]:
    collected: list[dict[str, Any]] = []
    total = None
    pages = 0
    for skip in range(0, 6000, take):
        result = page.evaluate(FETCH_JSON, f"{BASE}{path}?skip={skip}&take={take}")
        if result["json"] is None:
            break
        pages += 1
        batch = rows_of(result["json"])
        if isinstance(result["json"], dict):
            total = int(result["json"].get("totalCount") or 0) or total
        collected.extend(batch)
        if not batch or (total and len(collected) >= total):
            break
    summary = summarise(name, {"totalCount": total, "items": collected}, 200, 0)
    summary["pages_fetched"] = pages
    return summary


def dashboard_chips(home_text: str) -> dict[str, int]:
    """The status chips on the old portal's landing page."""
    section = home_text.split("Member Lookup", 1)[-1].split("Notifications", 1)[0]
    counts: dict[str, int] = {}
    for label in ("Active", "Blocked", "Unverified", "Inactive"):
        match = re.search(rf"\b{label}\s+(\d+)\b", section)
        if match:
            counts[label.lower()] = int(match.group(1))
    if counts:
        counts["total"] = sum(counts.values())
    return counts


def probe_dashboard(page: Page, home_text: str) -> dict[str, Any]:
    """Itensity's own bucket counts — the numbers the roster has to match.

    Two sources: the old portal's chips (already on screen after sign-in) and
    the new portal's reporting API, which the SPA calls for the same figures.
    The new portal is opened on the same context so its session carries over —
    its own login form asks for an ID number, not a staff email, so it is never
    filled in here.
    """
    captured: list[dict[str, Any]] = []

    def on_response(response) -> None:
        if "reporting.api.itensityonline.com" in response.url and "report/query" in response.url:
            try:
                captured.append({"url": response.url, "body": response.json()})
            except Exception:
                pass

    page.on("response", on_response)
    page.goto(NEW_PORTAL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(12000)
    body = page.locator("body").inner_text()
    page.remove_listener("response", on_response)
    return {
        "chips": dashboard_chips(home_text),
        "reporting_api": captured,
        "new_portal_signed_in": "incorrect" not in body.lower(),
        "dashboard_text": body[:4000],
    }


def probe_report_pages(page: Page) -> list[dict[str, Any]]:
    """Fallback: open each report page and record the XHRs it fires."""
    findings = []
    for path in REPORT_PAGES:
        seen: list[str] = []

        def record(response) -> None:
            seen.append(f"{response.status} {response.url}")

        page.on("response", record)
        try:
            page.goto(BASE + path, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            text = page.locator("body").inner_text()[:1500]
        except Exception as exc:  # a report may not exist for this club
            text = f"(failed: {exc})"
        page.remove_listener("response", record)
        findings.append(
            {
                "page": path,
                "xhr": [u for u in seen if "/XML/" in u or "api." in u],
                "text_head": text,
            }
        )
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument(
        "--reports", action="store_true", help="Also open the Member Reports pages"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / f"itensity_probe_{datetime.now():%Y%m%d_%H%M%S}.json",
    )
    args = parser.parse_args()

    username = os.environ["ITENSITY_USERNAME"]
    password = os.environ["ITENSITY_PASSWORD"]
    findings: dict[str, Any] = {"base": BASE, "probed_at": datetime.now().isoformat(" ", "seconds")}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_context().new_page()
        home_text = login(page, username, password)

        results = []
        opened: set[str] = set()
        for name, path, take, context_page in PAGED_PROBES:
            open_context_page(page, context_page, opened)
            results.append(probe_paged(page, name, path, take))
        for name, path, context_page in SINGLE_PROBES:
            try:
                open_context_page(page, context_page, opened)
                results.append(probe_single(page, name, path))
            except Exception as exc:
                results.append({"endpoint": name, "error": str(exc)})
        findings["endpoints"] = results

        if args.reports:
            findings["report_pages"] = probe_report_pages(page)

        findings["dashboard"] = probe_dashboard(page, home_text)
        browser.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(findings, indent=2, default=str), encoding="utf-8")

    print(f"{'endpoint':22} {'http':>5} {'total':>7} {'rows':>7} {'members':>8}")
    for row in findings["endpoints"]:
        if "error" in row:
            print(f"{row['endpoint']:22} ERROR  {row['error'][:60]}")
            continue
        print(
            f"{row['endpoint']:22} {row['http_status']:>5} "
            f"{str(row.get('total_count') or '-'):>7} {row['rows']:>7} {row['members']:>8}"
        )
    print("\ndashboard chips:", findings["dashboard"]["chips"] or "(not found)")
    for capture in findings["dashboard"]["reporting_api"]:
        print("reporting api:", json.dumps(capture["body"])[:300])
    print(f"\nFull detail: {args.out}")


if __name__ == "__main__":
    main()
