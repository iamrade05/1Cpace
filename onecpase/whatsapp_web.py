"""Send WhatsApp messages by driving a real WhatsApp Web session, for use
when the Meta Cloud API (comms.py's send_whatsapp_message) isn't set up.

Runs one persistent, headless Playwright browser inside this process, owned
by a single background thread. That's only safe because gunicorn.conf.py
pins beta to workers=1 - the same guarantee onecpase/scheduler.py relies on
for the same reason (two workers would each try to drive their own copy of
the same logged-in session and fight over it).

This is real WhatsApp Web, not an official API - it carries an ongoing risk
that WhatsApp's abuse detection rate-limits or bans whatever phone number
gets linked, for behaving like a bot. Off by default (WHATSAPP_WEB_ENABLED);
a number is only ever linked by a person deliberately scanning the QR code
shown at /admin/whatsapp-web, or typing the pairing code shown there into
their phone (for phones that can't read the QR code off a screen).

WhatsApp Web's page structure isn't a published API and changes over time -
the selectors below matched at the time this was written but may need
updating; failures surface in get_status()['last_error'] rather than
silently vanishing.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, send_file, url_for,
)

from .admin import _admin_required
from .extensions import limiter

whatsapp_web_bp = Blueprint("whatsapp_web", __name__, url_prefix="/admin/whatsapp-web")

QR_SELECTOR = 'canvas[aria-label*="Scan" i], canvas[data-testid="qrcode"]'
QR_EXPIRED_SELECTOR = 'button[data-testid="link_device_qr_expired_refresh_button"], button:has-text("reload QR")'
# The "log in with phone number" route: WhatsApp shows an 8-character code
# instead of a QR code, which the person types into their phone.
PHONE_LINK_SELECTOR = '[data-testid="link_device_qr_phone_number_link"]'
PHONE_INPUT_SELECTOR = '[data-testid="phone-number-input"]'
PHONE_NEXT_SELECTOR = 'button:has-text("Next")'
# WhatsApp draws the code one character at a time, so the page text reads
# "A B C D - E F G H" rather than "ABCD-EFGH" - allow whitespace between them.
PAIRING_CODE_RE = re.compile(r"(?<![A-Z0-9])((?:[A-Z0-9]\s*){4})-\s*((?:[A-Z0-9]\s*){4})(?![A-Z0-9])")
# WhatsApp refuses further linking for a while after too many tries ("Too many
# attempts - There were too many attempts to link a device. Please try again
# later."). It doesn't say how long, and every further try can lengthen the wait,
# so after seeing it we hold off on asking again for a good while.
TOO_MANY_ATTEMPTS_RE = re.compile(r"too many attempts", re.I)
LINK_COOLDOWN = timedelta(hours=3)


class LinkBlocked(Exception):
    """WhatsApp is refusing new linking attempts for this number for now."""
LOGGED_IN_SELECTOR = '[data-testid="chat-list"], div#pane-side'
COMPOSE_SELECTOR = 'div[data-testid="conversation-compose-box-input"], footer div[contenteditable="true"]'

# WhatsApp Web refuses to load at all - showing a generic "works with Chrome
# 100+" message instead of the real page - for Playwright's default headless
# browser, because it reports itself as an automated client (navigator.webdriver
# is true, and Chromium's own default UA differs subtly from a real desktop
# Chrome's). Both need to be masked or WhatsApp never even shows the QR code.
REAL_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
HIDE_WEBDRIVER_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

_send_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
# Requests from the admin page ("phone", digits) / ("qr", ""). Playwright's sync
# API may only be used from the thread that started it, so a web request can't
# touch the browser itself - it queues the work for the worker thread.
_commands: "queue.Queue[tuple[str, str]]" = queue.Queue()
_state_lock = threading.Lock()
_state: dict = {
    "enabled": False,
    "started": False,
    "logged_in": False,
    "last_qr_at": None,
    "last_error": None,
    "last_sent_at": None,
    "pairing_code": None,
    "pairing_status": None,
    "pairing_blocked_until": None,
}
_worker_thread: threading.Thread | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set_state(**kwargs) -> None:
    with _state_lock:
        _state.update(kwargs)


def get_status() -> dict:
    with _state_lock:
        status = dict(_state)
    status["queue_length"] = _send_queue.qsize()
    return status


def queue_message(phone: str, text: str) -> None:
    """Queue a message for background sending. Never raises and never
    blocks the caller - a WhatsApp Web hiccup must not make a registration
    (or anything else that wants to notify someone) fail."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if not digits or not text:
        return
    _send_queue.put((digits, text))


def normalise_phone(text: str) -> str | None:
    """The full international digits for a number typed on the admin page, or
    None if it can't be one. A leading 0 is read as South African (this
    gym's numbers); any other number must already carry its country code."""
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "27" + digits[1:]
    return digits if 8 <= len(digits) <= 15 else None


def link_blocked_message() -> str | None:
    """Why a new code shouldn't be requested right now, or None if it's fine."""
    until = get_status().get("pairing_blocked_until")
    if not until:
        return None
    remaining = datetime.fromisoformat(until) - datetime.now(timezone.utc)
    if remaining.total_seconds() <= 0:
        return None
    minutes = int(remaining.total_seconds() // 60) + 1
    wait = f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"
    return ("WhatsApp is refusing new linking attempts for this number (\"too many attempts\"). "
            f"Please wait about {wait} before trying again - asking sooner can make the wait longer.")


def request_phone_link(phone_text: str) -> bool:
    """Ask the worker to fetch a pairing code for this phone. False if the
    number isn't usable; never blocks."""
    phone = normalise_phone(phone_text)
    if not phone:
        return False
    _set_state(pairing_code=None, pairing_status="Asking WhatsApp for a code")
    _commands.put(("phone", phone))
    return True


def request_qr_mode() -> None:
    """Ask the worker to go back to showing a QR code."""
    _set_state(pairing_code=None, pairing_status=None)
    _commands.put(("qr", ""))


def _open_phone_screen(page) -> None:
    """Get from WhatsApp Web's QR screen to its "enter phone number" screen.
    The link is on the page a moment before the page will act on a click (a
    click just after a reload is silently ignored), so wait for the QR code to
    draw first and click again if the screen hasn't changed."""
    page.locator(QR_SELECTOR).first.wait_for(state="visible", timeout=45000)
    box = page.locator(PHONE_INPUT_SELECTOR).first
    for _ in range(4):
        if box.is_visible():
            return
        try:
            page.locator(PHONE_LINK_SELECTOR).first.click(timeout=3000)
        except Exception:
            pass  # the link is gone once the screen has changed
        try:
            box.wait_for(state="visible", timeout=5000)
            return
        except Exception:
            continue
    raise TimeoutError("WhatsApp's phone number screen did not open")


def _start_phone_link(page, phone: str) -> None:
    """From WhatsApp Web's QR screen, switch to "log in with phone number",
    enter `phone` and read the 8-character code WhatsApp then shows."""
    _open_phone_screen(page)
    box = page.locator(PHONE_INPUT_SELECTOR).first
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("+" + phone, delay=40)
    page.locator(PHONE_NEXT_SELECTOR).first.click(timeout=5000)
    for _ in range(30):
        text = page.evaluate("document.body ? document.body.innerText : ''") or ""
        match = PAIRING_CODE_RE.search(text)
        if match:
            first, second = ("".join(group.split()) for group in match.groups())
            _set_state(pairing_code=f"{first}-{second}", pairing_status=None, last_error=None)
            return
        if TOO_MANY_ATTEMPTS_RE.search(text):
            raise LinkBlocked("WhatsApp says there were too many attempts to link a device")
        page.wait_for_timeout(1000)
    raise TimeoutError("WhatsApp did not show a code")


def _handle_command(page, command: str, phone: str) -> None:
    """Run one admin-page request on the worker's own thread. Always starts
    from a freshly loaded WhatsApp Web so it never depends on what screen the
    previous attempt left behind."""
    try:
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
        if command == "phone":
            _start_phone_link(page, phone)
    except LinkBlocked as exc:
        until = datetime.now(timezone.utc) + LINK_COOLDOWN
        _set_state(pairing_code=None, pairing_status=None,
                   pairing_blocked_until=until.isoformat(),
                   last_error=f"{exc}. WhatsApp doesn't say how long to wait, and asking again "
                              "sooner can make it longer - try again in a few hours.")
    except Exception as exc:
        _set_state(pairing_code=None, pairing_status=None, last_error=f"Could not get a linking code: {exc}")


def _qr_path(session_dir: Path) -> Path:
    return session_dir / "qr.png"


def _refresh_expired_qr(page) -> None:
    """WhatsApp Web stops rotating the QR code after about three minutes and
    covers it with a "Select to reload QR code" button. Nobody is at this
    headless browser to click it, so without this the admin page ends up
    showing that black overlay instead of a scannable code."""
    try:
        button = page.locator(QR_EXPIRED_SELECTOR).first
        if button.is_visible(timeout=500):
            button.click(timeout=3000)
            page.wait_for_timeout(3000)  # let the new code draw before it is captured
    except Exception:
        pass


def _poll_login_state(page, session_dir: Path) -> bool:
    """Refresh logged_in/last_qr_at from the page's current DOM. Returns
    the current logged_in value."""
    try:
        if page.locator(LOGGED_IN_SELECTOR).first.is_visible(timeout=1000):
            _set_state(logged_in=True, pairing_code=None, pairing_status=None, pairing_blocked_until=None)
            return True
    except Exception:
        pass
    _refresh_expired_qr(page)
    try:
        qr = page.locator(QR_SELECTOR).first
        if qr.is_visible(timeout=1000):
            qr.screenshot(path=str(_qr_path(session_dir)))
            _set_state(logged_in=False, last_qr_at=_now_iso())
            return False
    except Exception:
        pass
    return get_status()["logged_in"]


def _send_one(page, phone: str, text: str) -> None:
    from urllib.parse import quote
    page.goto(
        f"https://web.whatsapp.com/send?phone={phone}&text={quote(text)}",
        wait_until="domcontentloaded",
    )
    box = page.locator(COMPOSE_SELECTOR).first
    box.wait_for(state="visible", timeout=30000)
    # A phone number web.whatsapp.com doesn't recognise as a valid WhatsApp
    # account shows an alert dialog instead of the compose box - the wait
    # above already would have timed out in that case.
    page.wait_for_timeout(500)
    box.click()
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)


def _run_worker(app, session_dir: Path, delay: float) -> None:
    from playwright.sync_api import sync_playwright

    session_dir.mkdir(parents=True, exist_ok=True)
    _set_state(started=True)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(session_dir), headless=True,
                user_agent=REAL_CHROME_UA,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.add_init_script(HIDE_WEBDRIVER_SCRIPT)
                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

                while True:
                    logged_in = _poll_login_state(page, session_dir)
                    try:
                        command, argument = _commands.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        if not logged_in:
                            _handle_command(page, command, argument)
                        continue
                    try:
                        phone, text = _send_queue.get(timeout=5)
                    except queue.Empty:
                        continue
                    if not logged_in:
                        # Nobody has scanned the QR code yet - keep the
                        # message rather than lose it, and don't hammer the
                        # queue while waiting.
                        _send_queue.put((phone, text))
                        time.sleep(5)
                        continue
                    try:
                        _send_one(page, phone, text)
                        _set_state(last_sent_at=_now_iso(), last_error=None)
                    except Exception as exc:
                        with app.app_context():
                            app.logger.exception("WhatsApp Web send failed for %s", phone)
                        _set_state(last_error=f"Send to {phone} failed: {exc}")
                    time.sleep(delay)
            finally:
                context.close()
    except Exception as exc:
        with app.app_context():
            app.logger.exception("WhatsApp Web worker crashed")
        _set_state(last_error=f"Worker crashed: {exc}", started=False)


def init_whatsapp_web(app) -> None:
    """Start the background session if enabled. Idempotent - safe to call
    even if something else already started it (e.g. the reloader)."""
    global _worker_thread
    if not app.config.get("WHATSAPP_WEB_ENABLED") or app.testing:
        return
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _set_state(enabled=True)
    session_dir = Path(app.config["WHATSAPP_WEB_SESSION_DIR"])
    delay = float(app.config.get("WHATSAPP_WEB_SEND_DELAY", 8))
    _worker_thread = threading.Thread(
        target=_run_worker, args=(app, session_dir, delay),
        name="whatsapp-web-sender", daemon=True,
    )
    _worker_thread.start()


# ── Admin: status + QR code ──────────────────────────────────────────────────

# The admin page refreshes itself every 6 seconds and each refresh fetches the
# page and the QR image, so it legitimately makes ~20 requests a minute - far
# over the app-wide default of 60 an hour, which would lock the admin out of
# their own QR code within minutes ("Too Many Requests"). Still bounded, just
# sized for a page that polls.
POLLING_LIMIT = "2000 per hour"


@whatsapp_web_bp.get("/")
@_admin_required
@limiter.limit(POLLING_LIMIT)
def status_page():
    return render_template("admin/whatsapp_web.html", status=get_status(), link_blocked=link_blocked_message())


@whatsapp_web_bp.get("/status.json")
@_admin_required
@limiter.limit(POLLING_LIMIT)
def status_json():
    return jsonify(get_status())


@whatsapp_web_bp.post("/link-by-phone")
@_admin_required
@limiter.limit("6 per minute")
def link_by_phone():
    blocked = link_blocked_message()
    if blocked:
        flash(blocked, "error")
    elif request_phone_link(request.form.get("phone", "")):
        flash("Asking WhatsApp for a code - it appears here in a few seconds.", "info")
    else:
        flash("Enter the phone's full WhatsApp number, e.g. 082 123 4567.", "error")
    return redirect(url_for("whatsapp_web.status_page"))


@whatsapp_web_bp.post("/show-qr")
@_admin_required
def show_qr():
    request_qr_mode()
    return redirect(url_for("whatsapp_web.status_page"))


@whatsapp_web_bp.get("/qr.png")
@_admin_required
@limiter.limit(POLLING_LIMIT)
def qr_image():
    session_dir = Path(current_app.config["WHATSAPP_WEB_SESSION_DIR"])
    path = _qr_path(session_dir)
    if not path.exists():
        abort(404)
    return send_file(str(path), mimetype="image/png", max_age=0)
