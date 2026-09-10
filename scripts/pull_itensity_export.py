"""Log into Itensity and download the live member Data Export.

Feeds scripts/import_itensity_members.py — that script is the reconciliation
check (Itensity's member list vs 1Cpase's), this one just gets a fresh copy
of Itensity's side.

KNOWN LIMITATION (2026-08-24): Itensity's own dashboard reports 1,970 total
members, but this export currently returns ~1,838 — a ~132-record gap. This
isn't a bug in this script: the grid's own "export to Excel" button pulls
whatever the grid currently has loaded, and clicking through its pager to
force more pages to load doesn't work reliably from a headless browser (the
pager's own selection state updates correctly, but the underlying row data
never actually refreshes — tested extensively across page sizes, click
methods, and wait times with no success). Until that's resolved, treat this
export as a large, useful sample for reconciliation, not a guaranteed-complete
roster — cross-check anyone it flags as "missing" against Itensity directly
before assuming they're truly absent.

Usage:
    python scripts/pull_itensity_export.py
    python scripts/pull_itensity_export.py --out data/itensity_export.xlsx

Credentials come from the sibling 1Cpace app's .env (ITENSITY_URL,
ITENSITY_USERNAME, ITENSITY_PASSWORD) via RADEPULSE_PATH in this app's own
.env — the same credential hand-off used by the NuPay/Itensity push drivers.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

RADEPULSE_PATH = Path(os.environ.get("RADEPULSE_PATH", ROOT.parent / "1Cpace"))
load_dotenv(RADEPULSE_PATH / ".env", override=False)

BASE = os.environ.get("ITENSITY_URL", "https://elev8healthandfitness.itensityonline.com").rstrip("/")


def _click_visible_text(page: Page, label: str, timeout: int = 5000) -> bool:
    """Click the first *visible* match for exact text — the Itensity SPA
    keeps hidden duplicate nav items in the DOM (mobile/desktop variants),
    and get_by_text().first can resolve to one that's off-screen."""
    for candidate in page.get_by_text(label, exact=True).all():
        try:
            if candidate.is_visible():
                candidate.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


def pull_export(out_path: Path, headless: bool = True) -> Path:
    username = os.environ["ITENSITY_USERNAME"]
    password = os.environ["ITENSITY_PASSWORD"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        page.goto(BASE + "/", wait_until="networkidle", timeout=40000)
        page.fill("input[name=txt_email]", username)
        page.fill("input[name=password]", password)
        page.click("input[type=submit]")
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        if not _click_visible_text(page, "Reports", timeout=10000):
            raise RuntimeError("Could not open the Reports menu")
        page.wait_for_timeout(800)
        if not _click_visible_text(page, "Exports"):
            raise RuntimeError("Could not open the Exports submenu")
        page.wait_for_timeout(800)
        if not _click_visible_text(page, "Data Export"):
            raise RuntimeError("Could not click Data Export")
        page.wait_for_load_state("networkidle", timeout=20000)
        page.wait_for_timeout(1500)

        # The grid's Excel-export icon — a DevExtreme dx-icon-export-excel-button,
        # not a normal link/button, so it has to be found by class rather than text.
        export_icon = page.locator("i.dx-icon-export-excel-button")
        if not export_icon.count():
            raise RuntimeError(
                "Could not find the grid's export-to-Excel icon on the Data Export page"
            )
        with page.expect_download(timeout=30000) as dl_info:
            export_icon.first.click(timeout=5000)
        download = dl_info.value

        out_path.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(str(out_path))

        browser.close()
        return out_path


def pull_dashboard_snapshot(headless: bool = True) -> dict[str, int]:
    """Read the status cards from the authenticated Itensity dashboard."""
    username = os.environ["ITENSITY_USERNAME"]
    password = os.environ["ITENSITY_PASSWORD"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(BASE + "/", wait_until="networkidle", timeout=40000)
        page.fill("input[name=txt_email]", username)
        page.fill("input[name=password]", password)
        page.click("input[type=submit]")
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text()
        browser.close()

    import re
    section = body.split("Member Lookup", 1)[-1].split("Notifications", 1)[0]
    counts = {}
    for label in ("Active", "Blocked", "Unverified", "Inactive"):
        match = re.search(rf"\b{label}\s+(\d+)\b", section)
        if not match:
            raise RuntimeError(f"Itensity dashboard did not show {label} count")
        counts[label.lower()] = int(match.group(1))
    counts["total"] = sum(counts.values())
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path,
        default=ROOT / "data" / f"itensity_export_{datetime.now():%Y%m%d_%H%M%S}.xlsx",
    )
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    args = parser.parse_args()

    dest = pull_export(args.out, headless=not args.headed)
    print(f"Saved Itensity member export to {dest}")
    print("NOTE: this export currently covers ~93% of the true member count "
          "(known gap — see the module docstring). Treat 'missing from "
          "1Cpase' results as a strong lead list, not a guaranteed-complete one.")
    print(f"Next: python scripts/import_itensity_members.py \"{dest}\"  (dry run)")


if __name__ == "__main__":
    main()
