"""Read the complete Itensity ledger in bounded date windows; never modify the portal."""
from datetime import date, timedelta
from urllib.parse import urlencode
import json


def read_window(page, start: date, end: date, take=100000):
    from scripts.itensity_portal import fetch_json
    rows, seen = [], set()
    expected = None
    while expected is None or len(rows) < expected:
        query = urlencode(dict(start=start.isoformat(), end=end.isoformat(),
                               skip=len(rows), take=take, filter="",
                               orderby=json.dumps([dict(selector='id',desc=False)])))
        payload = fetch_json(page, "/XML/transactions_report.php?" + query)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError("Itensity transaction report has an unexpected format")
        total = int(payload["totalCount"])
        if expected is not None and total != expected:
            raise RuntimeError("Itensity report changed during pagination; retry required")
        expected = total
        batch = payload["items"]
        for row in batch:
            key = str(row.get("id") or "")
            if not key or key in seen:
                raise RuntimeError("Itensity returned missing or repeated transaction IDs")
            date.fromisoformat(str(row["date"])[:10])
            if not start.isoformat() <= str(row["date"])[:10] <= end.isoformat():
                raise RuntimeError("Itensity returned a transaction outside the requested dates")
            seen.add(key)
        rows.extend(batch)
        if len(rows) > expected or (not batch and len(rows) != expected):
            raise RuntimeError("Itensity transaction report is incomplete")
    return rows


def pull_transactions(start: date, end: date):
    from playwright.sync_api import sync_playwright
    from scripts.itensity_portal import BASE, credentials, login
    from onecpase.playwright_lock import PLAYWRIGHT_LOCK
    rows = []
    with PLAYWRIGHT_LOCK, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            login(page, *credentials())
            page.goto(BASE + "/transaction_report.php", wait_until="domcontentloaded", timeout=60000)
            while start <= end:
                stop = min(start + timedelta(days=30), end)
                rows.extend(read_window(page, start, stop))
                start = stop + timedelta(days=1)
        finally:
            browser.close()
    return rows
