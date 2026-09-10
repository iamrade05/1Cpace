"""Shared session helpers for Itensity's classic portal.

Every caller needs the same three things: a signed-in browser, a way to call an
/XML/ endpoint from inside that session, and the dashboard's own member counts.
Keeping them here stops the probe and the roster puller drifting apart.

Credentials come from this app's .env, falling back to the sibling 1Cpace app's
.env via RADEPULSE_PATH.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
RADEPULSE_PATH = Path(os.environ.get("RADEPULSE_PATH", ROOT.parent / "1Cpace"))
load_dotenv(RADEPULSE_PATH / ".env", override=False)

BASE = os.environ.get(
    "ITENSITY_URL", "https://elev8healthandfitness.itensityonline.com"
).rstrip("/")
NEW_PORTAL = "https://elev8healthandfitness.new.itensityonline.com/"

# The /XML/ endpoints build their query from session state their owning report
# page sets up. Called cold they answer 200 with an empty list, which reads as
# "no members" rather than "wrong page open" — so always open these first.
MEMBERS_PAGE = "/Members.php"
DATA_REPORT_PAGE = "/Data_Report.php"

FETCH_JSON = """async url => {
    const response = await fetch(url, {credentials: 'include'});
    const text = await response.text();
    let parsed = null;
    try { parsed = JSON.parse(text); } catch (err) { parsed = null; }
    return {status: response.status, bytes: text.length, json: parsed,
            head: parsed === null ? text.slice(0, 200) : null};
}"""


def credentials() -> tuple[str, str]:
    return os.environ["ITENSITY_USERNAME"], os.environ["ITENSITY_PASSWORD"]


def login(page: Page, username: str, password: str) -> str:
    """Sign in and prove it worked, returning the landing page's text.

    A failed sign-in still answers 200 with an empty list on every /XML/
    endpoint, so an unverified login looks exactly like an empty gym.
    """
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


def fetch_json(page: Page, url: str) -> Any:
    result = page.evaluate(FETCH_JSON, url if url.startswith("http") else BASE + url)
    if result["json"] is None:
        raise RuntimeError(
            f"{url} answered {result['status']} with non-JSON: {result['head']!r}"
        )
    return result["json"]


def rows_of(payload: Any) -> list[dict[str, Any]]:
    """Both shapes appear in this portal: a bare list, or {totalCount, items}."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        return [row for row in (payload.get("items") or []) if isinstance(row, dict)]
    return []


def fetch_paged(page: Page, path: str, take: int = 100, cap: int = 6000) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    total: int | None = None
    for skip in range(0, cap, take):
        payload = fetch_json(page, f"{path}?skip={skip}&take={take}")
        batch = rows_of(payload)
        if isinstance(payload, dict) and payload.get("totalCount"):
            total = int(payload["totalCount"])
        collected.extend(batch)
        if not batch or (total is not None and len(collected) >= total):
            break
    if total is not None and len(collected) != total:
        raise RuntimeError(
            f"{path} reported totalCount={total} but only {len(collected)} rows came back"
        )
    return collected


def dashboard_chips(home_text: str) -> dict[str, int]:
    """The status chips on the classic portal's landing page."""
    section = home_text.split("Member Lookup", 1)[-1].split("Notifications", 1)[0]
    counts: dict[str, int] = {}
    for label in ("Active", "Blocked", "Unverified", "Inactive"):
        match = re.search(rf"\b{label}\s+(\d+)\b", section)
        if match:
            counts[label.lower()] = int(match.group(1))
    if counts:
        counts["total"] = sum(counts.values())
    return counts
