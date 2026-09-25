"""The WhatsApp Web sender's testable surface: the queue and status that the
rest of the app talks to, and the admin routes' access control. The actual
browser automation (_run_worker, _send_one, _poll_login_state) needs a real
logged-in WhatsApp Web session to exercise and isn't covered here - see the
module docstring in onecpase/whatsapp_web.py.
"""
import queue as queue_module

from onecpase import whatsapp_web
from onecpase.database import get_db


def _reset_module_state():
    """The queue and status dict are module-level (one real sender per
    process) - each test needs to start clean rather than see another
    test's leftovers."""
    whatsapp_web._send_queue = queue_module.Queue()
    whatsapp_web._commands = queue_module.Queue()
    with whatsapp_web._state_lock:
        whatsapp_web._state.update({
            "enabled": False, "started": False, "logged_in": False,
            "last_qr_at": None, "last_error": None, "last_sent_at": None,
            "pairing_code": None, "pairing_status": None, "pairing_blocked_until": None,
        })


def _login_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, active) "
            "VALUES (1, 'admin', 'x', 'Test Admin', 'admin', 1)"
        )
        db.commit()


def test_queueing_a_message_adds_it_and_does_not_raise():
    _reset_module_state()
    whatsapp_web.queue_message("082 123 4567", "Hello")
    assert whatsapp_web.get_status()["queue_length"] == 1


def test_queueing_strips_non_digits_from_the_phone_number():
    _reset_module_state()
    whatsapp_web.queue_message("082 123-4567", "Hello")
    phone, text = whatsapp_web._send_queue.get_nowait()
    assert phone == "0821234567"
    assert text == "Hello"


def test_queueing_a_blank_phone_or_text_is_silently_ignored():
    """A message with nowhere to go, or nothing to say, must not clog the
    queue or crash whatever triggered the send."""
    _reset_module_state()
    whatsapp_web.queue_message("", "Hello")
    whatsapp_web.queue_message("0821234567", "")
    assert whatsapp_web.get_status()["queue_length"] == 0


def test_status_reflects_the_current_queue_length():
    _reset_module_state()
    whatsapp_web.queue_message("0821234567", "One")
    whatsapp_web.queue_message("0821234568", "Two")
    assert whatsapp_web.get_status()["queue_length"] == 2


def test_set_state_updates_are_visible_via_get_status():
    _reset_module_state()
    whatsapp_web._set_state(logged_in=True, last_error="boom")
    status = whatsapp_web.get_status()
    assert status["logged_in"] is True
    assert status["last_error"] == "boom"


class _FakePage:
    """Just enough of Playwright's page to drive _refresh_expired_qr."""

    def __init__(self, expired_visible, click_raises=False):
        self.expired_visible = expired_visible
        self.click_raises = click_raises
        self.clicked = False
        self.waited_ms = 0
        self.selectors = []

    def locator(self, selector):
        self.selectors.append(selector)
        return self

    @property
    def first(self):
        return self

    def is_visible(self, timeout=None):
        return self.expired_visible

    def click(self, timeout=None):
        if self.click_raises:
            raise RuntimeError("detached")
        self.clicked = True

    def wait_for_timeout(self, ms):
        self.waited_ms += ms


def test_expired_qr_is_reloaded_by_clicking_its_button():
    page = _FakePage(expired_visible=True)
    whatsapp_web._refresh_expired_qr(page)
    assert page.selectors == [whatsapp_web.QR_EXPIRED_SELECTOR]
    assert page.clicked is True
    assert page.waited_ms > 0  # gives the new code time to draw before it is captured


def test_a_live_qr_is_left_alone():
    page = _FakePage(expired_visible=False)
    whatsapp_web._refresh_expired_qr(page)
    assert page.clicked is False
    assert page.waited_ms == 0


def test_a_failed_reload_click_never_raises():
    """The poll loop must survive WhatsApp changing its page under us."""
    page = _FakePage(expired_visible=True, click_raises=True)
    whatsapp_web._refresh_expired_qr(page)
    assert page.clicked is False


# ── Linking with a code instead of a QR scan ─────────────────────────────────

def test_phone_numbers_are_normalised_to_international_digits():
    normalise = whatsapp_web.normalise_phone
    assert normalise("082 123 4567") == "27821234567"      # leading 0 = South African
    assert normalise("+27 82 123 4567") == "27821234567"
    assert normalise("0027821234567") == "27821234567"
    assert normalise("+44 7700 900123") == "447700900123"  # other countries keep their own code


def test_unusable_phone_numbers_are_rejected():
    for bad in ("", "abc", "12345", "1" * 16, None):
        assert whatsapp_web.normalise_phone(bad) is None


def test_requesting_a_code_queues_work_for_the_worker_thread():
    _reset_module_state()
    assert whatsapp_web.request_phone_link("082 123 4567") is True
    assert whatsapp_web._commands.get_nowait() == ("phone", "27821234567")
    assert whatsapp_web.get_status()["pairing_status"]


def test_requesting_a_code_for_a_bad_number_queues_nothing():
    _reset_module_state()
    assert whatsapp_web.request_phone_link("nope") is False
    assert whatsapp_web._commands.empty()


def test_going_back_to_the_qr_code_clears_the_pairing_state():
    _reset_module_state()
    whatsapp_web._set_state(pairing_code="ABCD-1234", pairing_status="x")
    whatsapp_web.request_qr_mode()
    assert whatsapp_web._commands.get_nowait() == ("qr", "")
    assert whatsapp_web.get_status()["pairing_code"] is None


class _FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def _box_open(self):
        return (self.selector == whatsapp_web.PHONE_INPUT_SELECTOR
                and self.page.link_clicks >= self.page.clicks_needed)

    def is_visible(self, timeout=None):
        return self._box_open()

    def wait_for(self, state=None, timeout=None):
        if self.selector == whatsapp_web.PHONE_INPUT_SELECTOR and not self._box_open():
            raise TimeoutError("not visible")
        self.page.actions.append(("wait_for", self.selector))

    def click(self, timeout=None):
        if self.selector == whatsapp_web.PHONE_LINK_SELECTOR:
            if self.page.link_clicks >= self.page.clicks_needed:
                raise TimeoutError("link is gone")  # the screen already changed
            self.page.link_clicks += 1
        self.page.actions.append(("click", self.selector))


class _FakePhonePage:
    """Enough of Playwright's page to drive _start_phone_link / _handle_command.

    clicks_needed models WhatsApp Web ignoring clicks made too soon after a
    load: the phone-number box only opens on the Nth click of the link."""

    def __init__(self, texts, goto_raises=False, clicks_needed=1):
        self.texts = list(texts)     # what the page says on each successive read
        self.goto_raises = goto_raises
        self.clicks_needed = clicks_needed
        self.link_clicks = 0
        self.actions = []
        self.keyboard = self

    def goto(self, url, wait_until=None):
        if self.goto_raises:
            raise RuntimeError("offline")
        self.actions.append(("goto", url))

    def locator(self, selector):
        self.actions.append(("locator", selector))
        return _FakeLocator(self, selector)

    def press(self, key):
        self.actions.append(("press", key))

    def type(self, text, delay=None):
        self.actions.append(("type", text))

    def evaluate(self, script):
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]

    def wait_for_timeout(self, ms):
        pass


def test_start_phone_link_types_the_number_and_reads_the_code():
    _reset_module_state()
    page = _FakePhonePage(["Enter phone number", "Enter code on phone\nABCD-1234\nOn your phone…"])
    whatsapp_web._start_phone_link(page, "27821234567")
    assert ("type", "+27821234567") in page.actions
    assert ("locator", whatsapp_web.PHONE_NEXT_SELECTOR) in page.actions
    assert whatsapp_web.get_status()["pairing_code"] == "ABCD-1234"


def test_the_code_is_read_from_whatsapps_one_character_at_a_time_layout():
    """WhatsApp draws the code one character per box, so the page text is
    'A 1 B 2 - C 3 D 4', not 'A1B2-C3D4' - this is the layout it really uses."""
    _reset_module_state()
    screen = ("Download WhatsApp for Windows Enter code on phone Linking WhatsApp account "
              "+27 82 000 0000 (edit) A 1 B 2 - C 3 D 4 1 Open WhatsApp on your phone 2 On Android tap Menu")
    page = _FakePhonePage([screen])
    whatsapp_web._start_phone_link(page, "27820000000")
    assert whatsapp_web.get_status()["pairing_code"] == "A1B2-C3D4"


def test_the_code_is_read_when_each_character_is_on_its_own_line():
    _reset_module_state()
    page = _FakePhonePage(["Enter code on phone\nA\n1\nB\n2\n-\nC\n3\nD\n4\nOpen WhatsApp on your phone"])
    whatsapp_web._start_phone_link(page, "27820000000")
    assert whatsapp_web.get_status()["pairing_code"] == "A1B2-C3D4"


def test_ordinary_whatsapp_screens_are_not_mistaken_for_a_code():
    pattern = whatsapp_web.PAIRING_CODE_RE
    assert pattern.search("Scan to log in Link with phone number instead. 1 Scan the QR code with your phone's camera") is None
    assert pattern.search("Enter phone number Select a country and enter your phone number. South Africa +27 Next") is None


def test_a_link_click_that_is_ignored_is_retried():
    """Right after a reload WhatsApp Web silently ignores the click that opens
    the phone-number screen - it has to be clicked again."""
    page = _FakePhonePage(["ABCD-1234"], clicks_needed=3)
    whatsapp_web._open_phone_screen(page)
    assert page.link_clicks == 3


def test_the_phone_screen_is_left_alone_when_already_open():
    page = _FakePhonePage(["x"], clicks_needed=0)
    whatsapp_web._open_phone_screen(page)
    assert page.link_clicks == 0


def test_opening_the_phone_screen_gives_up_eventually():
    page = _FakePhonePage(["x"], clicks_needed=99)
    try:
        whatsapp_web._open_phone_screen(page)
    except TimeoutError:
        assert page.link_clicks == 4  # bounded, not an endless loop
    else:
        raise AssertionError("expected a TimeoutError")


def test_start_phone_link_times_out_when_no_code_ever_appears():
    _reset_module_state()
    page = _FakePhonePage(["Enter phone number"])
    try:
        whatsapp_web._start_phone_link(page, "27821234567")
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected a TimeoutError")


def test_a_failed_link_attempt_is_reported_not_raised():
    """The worker loop must survive WhatsApp changing under it."""
    _reset_module_state()
    whatsapp_web._handle_command(_FakePhonePage(["x"], goto_raises=True), "phone", "27821234567")
    status = whatsapp_web.get_status()
    assert "Could not get a linking code" in status["last_error"]
    assert status["pairing_code"] is None


def test_qr_command_just_reloads_whatsapp_web():
    _reset_module_state()
    page = _FakePhonePage(["x"])
    whatsapp_web._handle_command(page, "qr", "")
    assert page.actions == [("goto", "https://web.whatsapp.com")]


# ── Admin routes ──────────────────────────────────────────────────────────────

def test_status_page_requires_admin(client):
    response = client.get("/admin/whatsapp-web/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_admin_can_view_the_status_page(client):
    _reset_module_state()
    _login_admin(client)
    response = client.get("/admin/whatsapp-web/")
    assert response.status_code == 200
    assert b"WhatsApp" in response.data


def test_status_page_shows_disabled_when_not_configured(client):
    _reset_module_state()
    _login_admin(client)
    client.application.config["WHATSAPP_WEB_ENABLED"] = False
    response = client.get("/admin/whatsapp-web/")
    assert b"turned off" in response.data.lower()


def test_qr_image_404s_when_nothing_has_been_captured_yet(client, tmp_path):
    _login_admin(client)
    client.application.config["WHATSAPP_WEB_SESSION_DIR"] = str(tmp_path / "no-session-here")
    response = client.get("/admin/whatsapp-web/qr.png")
    assert response.status_code == 404


def test_status_json_reports_the_real_status(client):
    _reset_module_state()
    _login_admin(client)
    whatsapp_web._set_state(enabled=True, logged_in=True)
    response = client.get("/admin/whatsapp-web/status.json")
    assert response.status_code == 200
    body = response.get_json()
    assert body["enabled"] is True
    assert body["logged_in"] is True


def test_link_by_phone_requires_admin(client):
    response = client.post("/admin/whatsapp-web/link-by-phone", data={"phone": "0821234567"}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_admin_can_request_a_code_for_a_phone(client):
    _reset_module_state()
    _login_admin(client)
    response = client.post("/admin/whatsapp-web/link-by-phone", data={"phone": "082 123 4567"})
    assert response.status_code == 302
    assert whatsapp_web._commands.get_nowait() == ("phone", "27821234567")


def test_a_bad_number_is_refused_with_a_message(client):
    _reset_module_state()
    _login_admin(client)
    response = client.post("/admin/whatsapp-web/link-by-phone", data={"phone": "abc"}, follow_redirects=True)
    assert b"full WhatsApp number" in response.data
    assert whatsapp_web._commands.empty()


def test_status_page_shows_the_pairing_code_when_there_is_one(client):
    _reset_module_state()
    _login_admin(client)
    client.application.config["WHATSAPP_WEB_ENABLED"] = True
    whatsapp_web._set_state(enabled=True, pairing_code="ABCD-1234")
    response = client.get("/admin/whatsapp-web/")
    assert b"ABCD-1234" in response.data
    assert b"Link with phone number instead" in response.data


def test_status_page_offers_the_code_form_when_not_linked(client):
    _reset_module_state()
    _login_admin(client)
    whatsapp_web._set_state(enabled=True)
    response = client.get("/admin/whatsapp-web/")
    assert b"link-by-phone" in response.data


def test_show_qr_goes_back_to_the_qr_code(client):
    _reset_module_state()
    _login_admin(client)
    response = client.post("/admin/whatsapp-web/show-qr")
    assert response.status_code == 302
    assert whatsapp_web._commands.get_nowait() == ("qr", "")


def test_the_polling_endpoints_are_not_throttled_by_the_default_hourly_limit(client):
    """The admin page refreshes itself every 6 seconds and each refresh fetches
    the page and the QR image, so it makes ~20 requests a minute. The app-wide
    default (60 per hour) would lock the admin out of their own QR code in about
    three minutes - 'Too Many Requests' instead of a code to scan."""
    _reset_module_state()
    _login_admin(client)
    for path in ("/admin/whatsapp-web/", "/admin/whatsapp-web/status.json", "/admin/whatsapp-web/qr.png"):
        statuses = {client.get(path).status_code for _ in range(70)}
        assert 429 not in statuses, f"{path} was throttled after under 70 requests"


# ── WhatsApp refusing new linking attempts ("Too many attempts") ─────────────

_TOO_MANY = "Too many attempts There were too many attempts to link a device. Please try again later. OK"


def test_start_phone_link_recognises_whatsapps_too_many_attempts_message():
    _reset_module_state()
    page = _FakePhonePage([_TOO_MANY])
    try:
        whatsapp_web._start_phone_link(page, "27821234567")
    except whatsapp_web.LinkBlocked:
        pass
    else:
        raise AssertionError("expected LinkBlocked")


def test_a_blocked_attempt_reports_the_real_reason_and_holds_off():
    _reset_module_state()
    whatsapp_web._handle_command(_FakePhonePage([_TOO_MANY]), "phone", "27821234567")
    status = whatsapp_web.get_status()
    assert "too many attempts" in status["last_error"].lower()
    assert "did not show a code" not in status["last_error"]
    assert status["pairing_blocked_until"]
    assert whatsapp_web.link_blocked_message()


def test_no_hold_off_when_nothing_has_gone_wrong():
    _reset_module_state()
    assert whatsapp_web.link_blocked_message() is None


def test_the_hold_off_ends_by_itself():
    from datetime import datetime, timedelta, timezone
    _reset_module_state()
    whatsapp_web._set_state(pairing_blocked_until=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    assert whatsapp_web.link_blocked_message() is None


def test_no_new_code_is_requested_while_whatsapp_is_refusing(client):
    from datetime import datetime, timedelta, timezone
    _reset_module_state()
    _login_admin(client)
    whatsapp_web._set_state(pairing_blocked_until=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat())
    response = client.post("/admin/whatsapp-web/link-by-phone", data={"phone": "082 123 4567"}, follow_redirects=True)
    assert b"too many attempts" in response.data.lower()
    assert whatsapp_web._commands.empty()


def test_the_page_says_so_instead_of_offering_the_form_while_blocked(client):
    from datetime import datetime, timedelta, timezone
    _reset_module_state()
    _login_admin(client)
    whatsapp_web._set_state(enabled=True, pairing_blocked_until=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat())
    response = client.get("/admin/whatsapp-web/")
    assert b"too many attempts" in response.data.lower()
    assert b"link-by-phone" not in response.data
