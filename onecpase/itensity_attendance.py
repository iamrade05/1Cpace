"""Read a member's access log from Itensity, without changing their account."""
from datetime import datetime, timedelta
from html.parser import HTMLParser
import re
import threading
from zoneinfo import ZoneInfo

from flask import current_app, g, has_request_context

ZONE = ZoneInfo('Africa/Johannesburg')
_CACHE = {}
_CACHE_LOCK = threading.Lock()


class _AccessLog(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = []
        self.headers = []
        self.tag = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('td', 'th'):
            self.tag, self.parts = tag, []

    def handle_data(self, data):
        if self.tag:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == self.tag:
            (self.headers if tag == 'th' else self.cells).append(''.join(self.parts).strip())
            self.tag = None


def parse_last_visit(html, *, now=None):
    """Reject login/error pages; take the latest valid entry, not the first row."""
    parser = _AccessLog()
    parser.feed(html)
    # The classic portal misspells its single access-log heading as "Dtae".
    if parser.headers not in (['Dtae & Time Accessed'], ['Date & Time Accessed']):
        raise ValueError('Itensity did not return the member access log.')
    now = now or datetime.now(ZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZONE)
    dates = []
    for cell in parser.cells:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', cell):
            continue
        try:
            stamp = datetime.fromisoformat(cell).replace(tzinfo=ZONE)
        except ValueError:
            continue
        if stamp <= now:
            dates.append(stamp)
    return max(dates, default=None)


def fetch_last_visit(ref):
    """Use the portal's observed per-member read-only report request."""
    ref = str(ref or '').strip()
    if not re.fullmatch(r'[1-9]\d*', ref):
        raise ValueError('A numeric Itensity member reference is required.')
    from playwright.sync_api import sync_playwright
    from scripts.itensity_portal import BASE, credentials, login
    from .playwright_lock import PLAYWRIGHT_LOCK
    with PLAYWRIGHT_LOCK, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            login(page, *credentials())
            page.goto(BASE + '/Access_Report.php', wait_until='domcontentloaded', timeout=60000)
            response = page.request.post(BASE + '/XML/access_report.php',
                form={'type':'0', 'name':ref, 'date':'null'}, timeout=60000)
            if not response.ok:
                raise ValueError('Itensity access report is unavailable.')
            return parse_last_visit(response.text())
        finally:
            browser.close()


def configured_for_request():
    return bool(has_request_context() and g.get('tenant_slug') and
                g.tenant_slug == current_app.config.get('ITENSITY_TENANT_SLUG', 'elev8'))


def member_last_visit(member):
    """Tenant-scoped cache; never fall back to an unrelated gym or SQL mirror."""
    if not configured_for_request():
        return None
    ref = str(dict(member).get('itensity_ref') or '').strip()
    if not re.fullmatch(r'[1-9]\d*', ref):
        return None
    key = (current_app.config['PLATFORM_DATABASE_PATH'], g.tenant_slug, ref)
    now = datetime.now(ZONE)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached and cached['expires'] > now:
        stamp, checked_at = cached['stamp'], cached['checked_at']
    else:
        try:
            stamp = fetch_last_visit(ref)
        except Exception:
            # Missing or unavailable is unknown attendance, never "never visited".
            current_app.logger.warning('Itensity attendance lookup unavailable; no visit claimed.')
            stamp = None
        checked_at = datetime.now(ZONE)
        with _CACHE_LOCK:
            _CACHE[key] = {'stamp':stamp, 'checked_at':checked_at,
                           'expires':checked_at + timedelta(minutes=15 if stamp else 1)}
    if stamp is None:
        return None
    return {'last_visited':stamp, 'days_ago':(now.date() - stamp.date()).days,
            'shift':'Morning' if 5 <= stamp.hour <= 12 else 'Afternoon' if 13 <= stamp.hour <= 20 else 'Other',
            'source':'Itensity member access log', 'checked_at':checked_at}
