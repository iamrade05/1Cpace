"""Drive Itensity Online's legacy PHP screens with Playwright.

Itensity has no public API, so this fills the same forms staff use: Add Member,
the profile's Attach Contract / Attach Compliance dialogs, and the Payment
Profile's Add Payment and Add Fee forms.

Nothing here may be silently skipped. A required value that cannot be set stops
the run before Submit, because a member saved with the wrong tariff or debit
date becomes a real debit order. Member details are never logged or written to
disk: debug output records field names only.
"""
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

DEBUG_DIR     = Path(os.getenv("ITENSITY_DEBUG_DIR") or Path(tempfile.gettempdir()) / "itensity_debug")
ITENSITY_URL  = os.getenv("ITENSITY_URL", "https://elev8healthandfitness.itensityonline.com").rstrip("/")
ITENSITY_USER = os.getenv("ITENSITY_USERNAME") or os.getenv("Itensity")
ITENSITY_PASS = os.getenv("ITENSITY_PASSWORD") or os.getenv("Itensity_PASSWORD")


class ItensityPushError(RuntimeError):
    """A run stopped. `submitted` says whether Itensity may have saved anything."""

    def __init__(self, message: str, submitted: bool = False):
        super().__init__(message)
        self.submitted = submitted


# DebiCheck-matched bank → branch code
BRANCH_CODES = {
    "ABSA":           "632005",
    "African Bank":   "430000",
    "Capitec":        "470010",
    "Discovery Bank": "679000",
    "FNB / RMB":      "250655",
    "Investec":       "580105",
    "Nedbank":        "198765",
    "PostBank":       "460005",
    "Standard Bank":  "051001",
    "Tyme Bank":      "678910",
    "U-Bank":         "431010",
}

# 1Cpace bank name → Itensity dropdown label
BANK_LABEL_MAP = {
    "ABSA":           "ABSA",
    "African Bank":   "African Bank",
    "Capitec":        "Capitec",
    "Discovery Bank": "Discovery Bank",
    "FNB / RMB":      "FNB/RMB",
    "Investec":       "Investec",
    "Nedbank":        "Nedbank",
    "PostBank":       "PostBank",
    "Standard Bank":  "Standard Bank",
    "Tyme Bank":      "Tyme Bank",
    "U-Bank":         "U-Bank",
}

# 1Cpace tariff → Itensity tariff, where the two names differ. Any other name
# must match Itensity's list exactly or the push stops.
TARIFF_LABEL_MAP = {
    "Premium 24 2026": "Premium 24 Month 2026",
}

ACCOUNT_TYPES = ("Cheque/Current", "Savings", "Transmission")
PAYMENT_METHOD_MAP = {"Debit Order": "Debit Order", "EFT": "PIF", "Complimentary": "PIF"}
PAYMENT_RADIO_MAP = {"Debit Order": "si_4", "EFT": "rad_eft", "Complimentary": "rad_comp"}
ACCESS_LEVEL = "Local"

REG_FEE_WORDS = re.compile(r"joining|card fee|registration|\breg fee\b", re.IGNORECASE)


def normalize_itensity_ref(itensity_ref: str) -> str:
    """Return the visible Itensity account number without EHF or leading zeros."""
    raw = str(itensity_ref or "").strip()
    if raw.upper().startswith("E8-"):
        return raw
    match = re.search(r"EHF0*(\d+)", raw, re.IGNORECASE)
    if match:
        return match.group(1)
    digits = re.sub(r"\D+", "", raw)
    return str(int(digits)) if digits else raw


def _save_itensity_ref(member_id: int, itensity_ref: str) -> None:
    """Replaced by the caller for the length of a push to capture the reference."""


def dob_from_id(id_number: str) -> str | None:
    """Extract YYYY-MM-DD from a 13-digit SA ID number."""
    if not id_number or len(id_number) < 6:
        return None
    try:
        yy = int(id_number[:2])
        mm = id_number[2:4]
        dd = id_number[4:6]
        current_yy = int(str(date.today().year)[2:])
        year = 1900 + yy if yy > current_yy else 2000 + yy
        return f"{year}-{mm}-{dd}"
    except (ValueError, IndexError):
        return None


def gender_from_id(id_number: str) -> str | None:
    """Extract gender from SA ID (digits 7–10: >=5000 = Male)."""
    if not id_number or len(id_number) < 10:
        return None
    try:
        return "Male" if int(id_number[6:10]) >= 5000 else "Female"
    except ValueError:
        return None


def validate_member(member: dict) -> list[str]:
    errors: list[str] = []
    required = {
        "first_name": "First name",
        "last_name": "Last name",
        "id_number": "ID number",
        "contact": "Cell number",
        "join_date": "Join date",
        "tariff": "Tariff",
        "contract_duration": "Contract duration",
        "payment_type": "Payment type",
        "sales_person": "Sales representative",
    }
    for field, label in required.items():
        if not str(member.get(field) or "").strip():
            errors.append(f"{label} is required")

    id_number = str(member.get("id_number") or "").strip()
    if id_number and not re.fullmatch(r"\d{13}", id_number):
        errors.append("ID number must be 13 digits")

    payment_type = str(member.get("payment_type") or "")
    if payment_type and payment_type not in PAYMENT_METHOD_MAP:
        errors.append(f"Payment type '{payment_type}' cannot be pushed to Itensity")

    if payment_type == "Debit Order":
        for field, label in {
            "bank": "Bank",
            "account_number": "Bank account number",
            "account_type": "Bank account type",
            "debit_order_date": "Debit order date",
        }.items():
            if not str(member.get(field) or "").strip():
                errors.append(f"{label} is required for Debit Order")
        if member.get("account_type") and member["account_type"] not in ACCOUNT_TYPES:
            errors.append("Bank account type is not one Itensity offers")

    return errors


def safe_fill(page: Page, selector: str, value: str | None) -> None:
    """Fill a field that is allowed to be missing. Required ones use must_fill."""
    if not value:
        return
    try:
        el = page.query_selector(selector)
        if el:
            el.fill(str(value), timeout=5000)
            return
    except Exception:
        pass

    try:
        page.evaluate(
            """
            ({selector, value}) => {
                const el = document.querySelector(selector);
                if (!el) return false;
                el.value = value;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }
            """,
            {"selector": selector, "value": str(value)},
        )
    except Exception:
        pass


def field_value(page: Page, selector: str) -> str | None:
    """The field's current value, or the chosen option's label for a dropdown."""
    return page.evaluate(
        """
        (selector) => {
            const el = document.querySelector(selector);
            if (!el) return null;
            if (el.tagName === 'SELECT') {
                const option = el.options[el.selectedIndex];
                return option ? option.textContent.trim().replace(/\\s+/g, ' ') : '';
            }
            return el.value || '';
        }
        """,
        selector,
    )


def must_fill(page: Page, selector: str, value: str, label: str) -> None:
    safe_fill(page, selector, value)
    if field_value(page, selector) is None:
        raise ItensityPushError(f"Itensity's form has no {label} field")


def must_select(page: Page, selector: str, wanted: str, label: str, by_digits: bool = False, wait_ms: int = 8000) -> None:
    """Choose the option whose label is exactly `wanted`, waiting for it to load.

    Partial matches are deliberately refused: '1' must not pick '21st', and
    'Premium 24' must not pick 'Premium 24 2025 Exl Levy'. With by_digits, a
    debit date of '25' may match the option '25th'.
    """
    deadline = time.monotonic() + wait_ms / 1000
    while True:
        chosen = page.evaluate(
            """
            ({selector, wanted, byDigits}) => {
                const el = document.querySelector(selector);
                if (!el) return false;
                const normalize = (value) => (value || '').trim().replace(/\\s+/g, ' ');
                const digits = (value) => (value || '').replace(/\\D+/g, '');
                const options = Array.from(el.options || []);
                const option = options.find((opt) => normalize(opt.textContent) === wanted)
                    || options.find((opt) => normalize(opt.value) === wanted)
                    || (byDigits && digits(wanted)
                        ? options.find((opt) => digits(opt.textContent) === digits(wanted))
                        : undefined);
                if (!option) return false;
                el.value = option.value;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }
            """,
            {"selector": selector, "wanted": " ".join(str(wanted).split()), "byDigits": by_digits},
        )
        if chosen:
            return
        if time.monotonic() >= deadline:
            raise ItensityPushError(f"Itensity does not offer the {label} '{wanted}'")
        page.wait_for_timeout(500)


def click_next(page: Page, text: str) -> bool:
    """Click a Next/navigation button by partial text."""
    clicked = page.evaluate(
        """
        (needle) => {
            const matches = Array.from(document.querySelectorAll('button, input, a'))
                .filter((el) => {
                    const rect = el.getBoundingClientRect();
                    const visible = !!(rect.width && rect.height);
                    const label = (el.value || el.textContent || '').trim();
                    return visible && label.includes(needle);
                });
            if (!matches.length) return false;
            matches[matches.length - 1].scrollIntoView({block: 'center'});
            matches[matches.length - 1].click();
            return true;
        }
        """,
        text,
    )
    if clicked:
        page.wait_for_timeout(2000)
        return True

    for sel in [
        f"button:has-text('{text}')",
        f"input[value*='{text}']",
        f"a:has-text('{text}')",
    ]:
        try:
            btn = page.query_selector(sel)
            if btn:
                btn.scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                btn.click(force=True, timeout=5000, no_wait_after=True)
                page.wait_for_timeout(2000)
                return True
        except Exception:
            continue
    return False


def visible_page_errors(page: Page) -> list[str]:
    """Return visible Itensity validation messages that should stop the push."""
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return []

    known_errors = [
        "Please enter a different ID number",
        "Please enter a valid ID number",
        "ID number already exists",
    ]
    return [msg for msg in known_errors if msg.lower() in text.lower()]


def raise_if_page_errors(page: Page, context: str) -> None:
    errors = visible_page_errors(page)
    if errors:
        raise ItensityPushError(f"{context}: " + "; ".join(errors))


def extract_itensity_ref(page: Page) -> str | None:
    url_match = re.search(
        r"[?&](?:id|mem|member_id|MemberID|user)=(\w[\w-]+)",
        page.url,
        re.IGNORECASE,
    )
    if url_match:
        return url_match.group(1)

    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        text = ""

    for pattern in (
        r"PERSONAL INFORMATION\s*-\s*(\w[\w-]+)",
        r"\b(EHF\d+)\b",
        r"Member\s*(?:ID|No|Number)[:\s#]+(\w[\w-]+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def form_field_state(page: Page) -> list[str]:
    """Which fields are filled, by name only. Never the values."""
    fields = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('input, select, textarea')).map((el) => ({
            name: el.getAttribute('name') || el.getAttribute('id') || el.tagName,
            filled: !!el.value,
            required: !!el.required,
        }))
        """
    )
    return [
        f"{item['name']}: {'filled' if item['filled'] else 'empty'}{' (required)' if item['required'] else ''}"
        for item in fields
    ]


def save_debug_artifacts(page: Page, prefix: str) -> None:
    """Keep what's needed to diagnose a failed run with no member details in it:
    screenshots and page text would hold ID and bank numbers in plain text."""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (DEBUG_DIR / f"{prefix}_fields.txt").write_text(
            "\n".join([page.url.split("?")[0], *form_field_state(page)]),
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        pass


def recaptcha_challenge_visible(page: Page) -> bool:
    """Whether Google is showing a picture challenge that a script can't answer."""
    try:
        return page.evaluate(
            """
            () => Array.from(document.querySelectorAll('iframe[src*="recaptcha"][src*="bframe"]'))
                .some((frame) => {
                    const rect = frame.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0
                        && getComputedStyle(frame).visibility !== 'hidden';
                })
            """
        )
    except Exception:
        return False


def invalid_required_fields(page: Page) -> list[str]:
    """Names of on-screen fields the browser would refuse to submit."""
    return page.evaluate(
        """
        () => {
            const form = document.getElementById('addmember');
            if (!form) return ['addmember form missing'];
            return Array.from(form.elements)
                .filter((el) => el.willValidate && !el.checkValidity())
                .filter((el) => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                })
                .map((el) => el.name || el.id || el.tagName);
        }
        """
    )


def form_stop_reason(page: Page) -> str | None:
    """Why Itensity's own checks say this can't go through as a new member.

    Submit stays enabled for a person already on record: Itensity would reuse
    them, so an enabled button proves nothing. Headings only, no member details.
    """
    try:
        state = page.evaluate(
            """
            () => {
                const heading = (id) => {
                    const el = document.getElementById(id);
                    return el ? (el.innerText || '').trim().split('\\n')[0].slice(0, 120) : '';
                };
                const exist = document.getElementById('use_exist');
                return {errors: heading('form_errors'), message: heading('form_message'), exist: exist ? exist.value : '0'};
            }
            """
        )
    except Exception:
        return None
    if state["errors"]:
        return f"Itensity says: {state['errors']}"
    if state["exist"] not in ("", "0"):
        return "this ID number is already on record in Itensity"
    if state["message"]:
        return f"Itensity says: {state['message']}"
    return None


def answer_dialogs(page: Page) -> list[str]:
    """Answer Itensity's pop-ups on purpose. Playwright would cancel them all silently.

    Only 'no promotion selected' is accepted: a promotion isn't part of a 1Cpace
    application. Anything else, such as agreeing to share a bank account, is the
    client's permission to give, so it's refused and handed back as a stop reason.
    """
    questions: list[str] = []

    def answer(dialog) -> None:
        if "not selected a promotion" in dialog.message:
            print("Itensity asked about a promotion; answered that none applies.")
            dialog.accept()
        else:
            questions.append(dialog.message[:150])
            dialog.dismiss()

    page.on("dialog", answer)
    return questions


def bank_check_state(page: Page) -> dict:
    return page.evaluate(
        """
        () => ({
            valid: typeof BankValid !== 'undefined' && BankValid === true,
            waiting: !!(document.getElementById('tt3_ref') || {}).value,
            text: (document.getElementById('bvalid') || {}).innerText || '',
        })
        """
    )


def run_bank_check(page: Page, questions: list[str], wait_s: int = 150) -> None:
    """Run Itensity's bank check once and wait for its answer before Submit.

    Pressing Submit too early would send a second check to the bank. The link
    Itensity offers to bypass a failed check is never used.
    """
    if bank_check_state(page)["valid"]:
        return
    if not click_next(page, "Check Now"):
        raise ItensityPushError("Itensity's form did not offer its bank check")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        state = bank_check_state(page)
        if state["valid"]:
            return
        if questions:
            raise ItensityPushError(f"Itensity's bank check asked: {questions[-1]}")
        if state["waiting"]:
            raise ItensityPushError("Itensity is waiting for the account holder's bank to approve the mandate")
        if "invalid" in state["text"].lower():
            raise ItensityPushError("Itensity says the bank details are invalid")
        reason = form_stop_reason(page)
        if reason:
            raise ItensityPushError(f"Bank check stopped: {reason}")
        page.wait_for_timeout(2000)
    raise ItensityPushError("Itensity's bank check gave no answer")


def ref_from_id_check(page: Page, id_number: str) -> str | None:
    """Ask Add Member's own ID check who holds this ID. Reads only."""
    goto_with_retry(page, f"{ITENSITY_URL}/AddMembers.php", "Itensity Add Member page")
    page.wait_for_selector("input[name='txt_email']", timeout=20000)
    page.wait_for_timeout(5000)
    replies: list = []
    # An active member comes back with use_exist left at 0 and the id only in the reply.
    page.on("response", lambda r: replies.append(r) if "validate_email" in r.url.lower() else None)
    page.fill("input[name='txt_email']", id_number)
    page.locator("input[name='txt_email']").dispatch_event("change")
    page.wait_for_timeout(6000)
    found = page.evaluate(
        """
        () => ['use_id_reserved', 'use_exist']
            .map((id) => (document.getElementById(id) || {}).value || '')
            .find((value) => value.length > 1) || null
        """
    )
    if found:
        return found
    for reply in replies:
        try:
            user = reply.json().get("user")
        except Exception:
            continue
        if user and len(str(user)) > 1:
            return str(user)
    return None


def safe_click(page: Page, selector: str) -> bool:
    try:
        el = page.query_selector(selector)
        if not el:
            return False
        try:
            el.click(force=True, timeout=5000)
            return True
        except Exception:
            page.evaluate(
                """
                (selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return false;
                    el.click();
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
                """,
                selector,
            )
            return True
    except Exception:
        return False


def digits_only(value: str | None) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def goto_with_retry(page: Page, url: str, label: str) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=120000)
            return
        except Exception as exc:
            last_error = exc
            print(f"{label} load attempt {attempt} failed: {type(exc).__name__}")
            page.wait_for_timeout(5000)
    raise ItensityPushError(f"Could not load {label}: {last_error}")


def fill_address_via_maps(page: Page, member: dict) -> None:
    """
    Use Itensity's Google Maps autocomplete to fill address.
    Falls back to direct field fill if no suburb/city.
    """
    suburb  = member.get("suburb", "")
    city    = member.get("city", "")
    street  = f"{member.get('street_number', '')} {member.get('street_name', '')}".strip()
    postal  = member.get("postal_code", "")
    country = member.get("country", "South Africa")

    # Build search query
    query = ", ".join(p for p in [street, suburb, city, postal, "South Africa"] if p)
    if not query.strip(", "):
        return

    autocomplete = page.query_selector("input#autocomplete")
    if autocomplete:
        autocomplete.click()
        autocomplete.fill("")
        page.wait_for_timeout(300)
        autocomplete.type(query, delay=80)
        page.wait_for_timeout(2000)  # wait for Google suggestions

        # Select first suggestion from dropdown
        suggestion = page.query_selector(".pac-item")
        if suggestion:
            suggestion.click()
            page.wait_for_timeout(1000)
            return  # Google filled the fields

    # Fallback: fill sub-fields directly
    safe_fill(page, "input#street_number",       member.get("street_number"))
    safe_fill(page, "input#route",               member.get("street_name"))
    safe_fill(page, "input#sublocality_level_1", suburb)
    safe_fill(page, "input#locality",            city)
    safe_fill(page, "input#postal_code",         postal)
    safe_fill(page, "input#country",             country)


def login(page: Page) -> None:
    if not ITENSITY_USER or not ITENSITY_PASS:
        raise ItensityPushError("ITENSITY_USERNAME and ITENSITY_PASSWORD must be set in .env")
    goto_with_retry(page, f"{ITENSITY_URL}/", "Itensity login page")
    page.wait_for_selector("input[name='txt_email']", timeout=20000)
    page.fill("input[name='txt_email']", ITENSITY_USER)
    page.fill("input[name='password']", ITENSITY_PASS)
    page.click("input[type='submit']", no_wait_after=True, timeout=10000)
    page.wait_for_timeout(5000)
    if page.locator("input[name='password']").count():
        raise ItensityPushError("Itensity login failed - check username/password")


@contextmanager
def itensity_session(headless: bool = True):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=200)
        try:
            page = browser.new_context().new_page()
            login(page)
            yield page
        finally:
            browser.close()


def _member_expectations(member: dict, dob, gender) -> list[tuple]:
    """(label, selector, wanted, how to compare) for every value that must be right."""
    tariff = TARIFF_LABEL_MAP.get(member["tariff"], member["tariff"])
    expected = [
        ("ID number", "input[name='txt_email']", member["id_number"], "digits"),
        ("First name", "input[name='name']", member["first_name"], "text"),
        ("Surname", "input[name='surname']", member["last_name"], "text"),
        ("Cell number", "input[name='txt_mobile_tel']", member["contact"], "digits"),
        ("Tariff", "select[name='tarrif']", tariff, "text"),
        ("Duration", "select#pp_dur", member["contract_duration"], "text"),
        ("Access level", "select#pp_access", ACCESS_LEVEL, "text"),
        ("Payment method", "select#pp_pm", PAYMENT_METHOD_MAP[member["payment_type"]], "text"),
        ("Join date", "input[name='dt_start']", member["join_date"], "digits"),
        ("Sales representative", "select[name='ddl_sales']", member["sales_person"], "text"),
    ]
    if gender:
        expected.append(("Gender", "select[name='gender']", gender, "text"))
    if member["payment_type"] == "Debit Order":
        bank = BANK_LABEL_MAP.get(member["bank"], member["bank"])
        expected += [
            ("Bank", "select#ddl_bank", bank, "text"),
            ("Account type", "select[name='txt_Type']", member["account_type"], "text"),
            ("Account number", "input[name='txt_Responsible_Acc_NO']", member["account_number"], "digits"),
            ("Account holder ID", "input[name='txt_Responsible_ID']", member["id_number"], "digits"),
            ("Debit date", "select[name='ddl_Date']", member["debit_order_date"], "digits"),
        ]
    if dob:
        expected.append(("Date of birth", "input[name='dob']", dob, "digits"))
    if member.get("monthly_installment"):
        expected.append(("Monthly amount", "input[name='cussamount']", f"{float(member['monthly_installment']):.2f}", "digits"))
    return expected


def wrong_fields(page: Page, expected: list[tuple]) -> list[str]:
    """Labels of required values that aren't what we meant to send. No values."""
    wrong = []
    for label, selector, wanted, compare in expected:
        actual = field_value(page, selector)
        if compare == "digits":
            matches = bool(digits_only(actual)) and digits_only(actual) == digits_only(wanted)
        else:
            matches = " ".join(str(actual or "").split()) == " ".join(str(wanted).split())
        if not matches:
            wrong.append(label)
    return wrong


def push(member: dict, headless: bool = False, submit: bool = False):
    """Fill Add Member. Without submit, stop before saving and return a report of
    field names and problems; nothing is saved in Itensity. With submit, press
    Submit once and hand the new reference to _save_itensity_ref."""
    validation_errors = validate_member(member)
    if validation_errors:
        raise ItensityPushError("; ".join(validation_errors))

    # Derive DOB and gender from ID if not stored
    dob    = member.get("date_of_birth") or dob_from_id(member.get("id_number", ""))
    gender = member.get("gender") or gender_from_id(member.get("id_number", ""))
    submitted = False

    try:
        with itensity_session(headless=headless) as page:
            goto_with_retry(page, f"{ITENSITY_URL}/AddMembers.php", "Itensity Add Member page")
            page.wait_for_timeout(5000)

            # ── Tab 1: Personal Details ────────────────────────────────
            page.wait_for_selector("input[name='txt_email']", timeout=10000)
            page.fill("input[name='txt_email']", member["id_number"])
            page.wait_for_timeout(800)

            must_fill(page, "input[name='name']", member["first_name"], "first name")
            must_fill(page, "input[name='surname']", member["last_name"], "surname")

            if dob:
                # DOB field may be read-only (auto-filled by Itensity from ID)
                page.evaluate(
                    """(dob) => { const el = document.querySelector("input[name='dob']"); if (el) el.value = dob; }""",
                    dob,
                )

            fill_address_via_maps(page, member)
            must_fill(page, "input[name='name']", member["first_name"], "first name")
            must_fill(page, "input[name='surname']", member["last_name"], "surname")

            must_fill(page, "input[name='txt_mobile_tel']", digits_only(member.get("contact")), "cell number")
            if gender:
                must_select(page, "select[name='gender']", gender, "gender")

            if not click_next(page, "Price Presenter"):
                raise ItensityPushError("Could not move from Personal Details to Price Presenter")
            raise_if_page_errors(page, "Personal Details rejected by Itensity")

            # ── Tab 2: Price Presenter ─────────────────────────────────
            # Duration, access and payment method decide which tariffs are
            # listed, so they go first and the tariff is checked again at the end.
            page.wait_for_timeout(1500)
            must_select(page, "select#pp_dur", member["contract_duration"], "contract duration")
            must_select(page, "select#pp_access", ACCESS_LEVEL, "access level")
            must_select(page, "select#pp_pm", PAYMENT_METHOD_MAP[member["payment_type"]], "payment method")
            page.wait_for_timeout(1500)
            tariff = TARIFF_LABEL_MAP.get(member["tariff"], member["tariff"])
            must_select(page, "select[name='tarrif']", tariff, "tariff")
            page.wait_for_timeout(2000)

            # Itensity builds the amount box only after a tariff is chosen.
            if member.get("monthly_installment"):
                must_fill(page, "input[name='cussamount']", f"{float(member['monthly_installment']):.2f}", "monthly amount")

            if not click_next(page, "Membership Details"):
                raise ItensityPushError("Could not move from Price Presenter to Membership Details")
            raise_if_page_errors(page, "Price Presenter rejected by Itensity")

            # ── Tab 3: Membership Details ──────────────────────────────
            page.wait_for_timeout(1500)
            must_fill(page, "input[name='dt_start']", member["join_date"], "join date")

            if not click_next(page, "Additional Details"):
                raise ItensityPushError("Could not move from Membership Details to Additional Details")
            raise_if_page_errors(page, "Membership Details rejected by Itensity")

            # ── Tab 4: Additional Details ──────────────────────────────
            page.wait_for_timeout(1500)
            safe_fill(page, "input[name='Fie4']", member.get("email"))
            safe_click(page, "#OptInYes" if member.get("opt_in", 1) else "#OptInNo")

            if not click_next(page, "PAR-Q"):
                raise ItensityPushError("Could not move from Additional Details to PAR-Q")
            raise_if_page_errors(page, "Additional Details rejected by Itensity")

            # ── Tab 5: PAR-Q (all No) ──────────────────────────────────
            page.wait_for_timeout(1500)
            if not click_next(page, "Payment Details"):
                raise ItensityPushError("Could not move from PAR-Q to Payment Details")
            raise_if_page_errors(page, "PAR-Q rejected by Itensity")

            # ── Tab 6: Payment Details ─────────────────────────────────
            page.wait_for_timeout(1500)
            ptype = member["payment_type"]
            if not safe_click(page, f"#{PAYMENT_RADIO_MAP[ptype]}"):
                raise ItensityPushError(f"Itensity's form has no {ptype} option")
            page.wait_for_timeout(600)
            if not safe_click(page, "#payersame"):
                raise ItensityPushError("Itensity's form has no 'payer is the member' option")
            page.wait_for_timeout(400)

            if ptype == "Debit Order":
                bank_label = BANK_LABEL_MAP.get(member["bank"], member["bank"])
                must_select(page, "select#ddl_bank", bank_label, "bank")
                must_select(page, "select[name='txt_Type']", member["account_type"], "account type")

                branch = member.get("branch_code") or BRANCH_CODES.get(member["bank"], "")
                must_fill(page, "input[name='txt_Responsible_Branch']", branch, "branch code")
                must_fill(page, "input[name='txt_Responsible_Acc_NO']", member["account_number"], "account number")
                must_fill(page, "input[name='txt_Responsible_Init']", member["first_name"][:1], "account holder initial")
                must_fill(page, "input[name='txt_Responsible_Surname']", member["last_name"], "account holder surname")
                must_fill(page, "input[name='txt_Responsible_ID']", member["id_number"], "account holder ID")
                must_fill(page, "input[name='txt_Responsible_Mobile']", digits_only(member.get("contact")), "account holder cell")
                if dob:
                    must_fill(page, "input[name='txt_Responsible_DOB']", dob, "account holder date of birth")
                safe_fill(page, "input[name='txt_pay_email']", member.get("email"))
                must_select(page, "select[name='ddl_Date']", str(member["debit_order_date"]), "debit date", by_digits=True)

            must_select(page, "select[name='ddl_sales']", member["sales_person"], "sales representative")

            raise_if_page_errors(page, "Payment Details rejected by Itensity")

            must_fill(page, "input[name='name']", member["first_name"], "first name")
            must_fill(page, "input[name='surname']", member["last_name"], "surname")

            # ── Check everything before anything is saved ──────────────
            expected = _member_expectations(member, dob, gender)
            wrong = wrong_fields(page, expected)
            invalid = invalid_required_fields(page)
            stop_reason = form_stop_reason(page)
            if not submit:
                return {
                    "checked": [label for label, *_ in expected],
                    "wrong": wrong,
                    "invalid_required": invalid,
                    "stop_reason": stop_reason,
                    "submit_button_found": page.locator("#btnSub").count() > 0,
                    "recaptcha_on_form": page.locator("#addmember [name='g-recaptcha-response']").count() > 0,
                }
            if wrong or invalid or stop_reason:
                save_debug_artifacts(page, "not_submitted")
                problems = [f"wrong value: {label}" for label in wrong]
                problems += [f"required field empty: {name}" for name in invalid]
                problems += [stop_reason] if stop_reason else []
                raise ItensityPushError("Stopped before Submit: " + "; ".join(problems))

            questions = answer_dialogs(page)
            if ptype == "Debit Order":
                run_bank_check(page, questions)

            # ── Submit, once. Never click again: a second click could save a second member. ──
            posted: list[str] = []
            page.on("request", lambda request: posted.append(request.url)
                    if request.method == "POST" and "scripts/addmember" in request.url.lower() else None)
            submitted = True
            try:
                page.locator("#btnSub").click(no_wait_after=True, timeout=10000)
            except Exception as exc:
                submitted = bool(posted)
                raise ItensityPushError(f"Could not press Itensity's Submit button: {type(exc).__name__}") from exc
            page.wait_for_timeout(3000)

            itensity_ref = None
            send_deadline = time.monotonic() + 60
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if posted:
                    if "addmembers.php" not in page.url.lower():
                        itensity_ref = extract_itensity_ref(page)
                        if itensity_ref:
                            break
                    raise_if_page_errors(page, "Itensity submit failed")
                else:
                    problem = questions[-1] if questions else form_stop_reason(page)
                    if recaptcha_challenge_visible(page):
                        problem = "Itensity showed a reCAPTCHA check that a script cannot answer"
                    if problem or time.monotonic() >= send_deadline:
                        submitted = False
                        save_debug_artifacts(page, "not_sent")
                        raise ItensityPushError(f"Itensity did not send the form: {problem or 'no reason shown'}")
                page.wait_for_timeout(3000)

            if not posted:
                submitted = False
                raise ItensityPushError("Itensity did not send the form")
            if not itensity_ref:
                save_debug_artifacts(page, "submit_no_confirmation")
                print("Landed on:", page.url.split("?")[0])
                itensity_ref = ref_from_id_check(page, member["id_number"])
            if not itensity_ref:
                raise ItensityPushError("Itensity received the form but did not confirm a member. Check Itensity before trying again")

            try:
                open_profile(page, itensity_ref, member["last_name"])
            except Exception as exc:
                raise ItensityPushError(
                    f"Itensity received the form, but reference {itensity_ref} could not be proved to be this member. "
                    "Check Itensity before trying again"
                ) from exc

            _save_itensity_ref(member.get("id"), itensity_ref)
            return itensity_ref
    except ItensityPushError as exc:
        exc.submitted = submitted
        raise
    except Exception as exc:
        raise ItensityPushError(f"Itensity push stopped: {type(exc).__name__}", submitted=submitted) from exc


# ── After the member exists ────────────────────────────────────────────────


def open_profile(page: Page, ref: str, surname: str) -> None:
    """Open a member's profile and prove it's theirs, so nothing lands on the wrong client."""
    goto_with_retry(page, f"{ITENSITY_URL}/Scripts/select.php?id={normalize_itensity_ref(ref)}", "Itensity profile")
    page.wait_for_function("typeof UploadContract === 'function'", timeout=30000)
    page.wait_for_timeout(3000)
    body = page.locator("body").inner_text(timeout=10000)
    if surname.strip().lower() not in body.lower():
        raise ItensityPushError("The Itensity profile does not match this member's surname")


def attachment_listed(page: Page, title: str) -> bool:
    return title.lower() in page.locator("body").inner_text(timeout=10000).lower()


def attach_document(page: Page, ref: str, surname: str, path: str, title: str, kind: str) -> str:
    """Upload one file to the profile. Returns 'sent' or 'already_attached'.

    Raises with submitted=True when the upload may have gone through but the
    file couldn't be seen afterwards.
    """
    open_profile(page, ref, surname)
    if attachment_listed(page, title):
        return "already_attached"

    page.evaluate("(kind) => UploadContract(kind)", kind)
    page.wait_for_selector("#ModalInner input[name='fil_file']", state="attached", timeout=20000)
    use_id = page.input_value("#ModalInner input[name='use_id']")
    if digits_only(use_id) != digits_only(normalize_itensity_ref(ref)):
        raise ItensityPushError("Itensity's upload form is for a different member")
    page.fill("#ModalInner input[name='name']", title)
    page.set_input_files("#ModalInner input[name='fil_file']", path)

    button = page.locator("#ModalInner form [type='submit'], #ModalInner form button").first
    if not button.count():
        raise ItensityPushError("Itensity's upload form has no upload button")
    try:
        button.click(timeout=10000)
        page.wait_for_timeout(5000)
        open_profile(page, ref, surname)
    except Exception as exc:
        raise ItensityPushError(f"Upload not confirmed: {type(exc).__name__}", submitted=True) from exc
    if not attachment_listed(page, title):
        raise ItensityPushError("The upload finished but the file isn't listed on the profile", submitted=True)
    return "sent"


def read_ledger(page: Page) -> list[dict]:
    rows = page.evaluate(
        """() => Array.from(document.querySelectorAll('tr'))
            .map((row) => Array.from(row.children).map((cell) => (cell.innerText || '').trim()))"""
    )
    ledger = []
    for row in rows:
        if len(row) >= 4 and row[0] != "Date" and row[2]:
            ledger.append({
                "date": row[0], "description": row[1], "type": row[2],
                "amount": ledger_amount(row[3]),
            })
    return ledger


def ledger_amount(text: str) -> float | None:
    """'R -200.00  R...' → -200.0. Only the first amount in the cell counts."""
    match = re.search(r"-?\d[\d ,]*\.\d{2}", text or "")
    return float(match.group(0).replace(" ", "").replace(",", "")) if match else None


def reg_fee_rows(ledger: list[dict], amount: float) -> list[dict]:
    """Rows showing this reg fee already on the account, as a charge or a payment."""
    return [
        row for row in ledger
        if REG_FEE_WORDS.search(row["description"] or "")
        and row["amount"] is not None and abs(abs(row["amount"]) - amount) < 0.005
    ]


def open_payment_profile(page: Page, ref: str, surname: str) -> None:
    open_profile(page, ref, surname)
    goto_with_retry(page, f"{ITENSITY_URL}/Scripts/select_pay.php?id={normalize_itensity_ref(ref)}", "Itensity payment profile")
    page.wait_for_function("typeof Get_form === 'function'", timeout=30000)
    page.wait_for_timeout(3000)


def _submit_finance_form(page: Page, form_kind: str, action: str, fields: dict[str, str], choice: str) -> None:
    page.evaluate("(kind) => Get_form(kind)", form_kind)
    form = f"#ModalInner form[action*='{action}']"
    page.wait_for_selector(f"{form} input[name='txt_Amount']", state="attached", timeout=20000)
    for name, value in fields.items():
        page.fill(f"{form} input[name='{name}']", value)
    must_select(page, f"{form} select[name='ddl_type']", choice, "payment type")
    page.locator(f"{form} input[type='submit']").click(timeout=10000)
    page.wait_for_timeout(5000)


def record_reg_fee(page: Page, ref: str, surname: str, amount: float, receipt: str, paid_on: str,
                   payment_type: str, add_fee_first: bool = False) -> dict:
    """Record the fee on the Payment Profile, once.

    Stops without writing if the account already shows it. Add Payment has no
    fee-type box, so Itensity allocates it to what's owing; add_fee_first loads
    the charge first for accounts where it isn't there to be detected.
    """
    open_payment_profile(page, ref, surname)
    before = read_ledger(page)
    if reg_fee_rows(before, amount) or any(receipt in (row["description"] or "") for row in before):
        return {"status": "already_in_itensity", "fee_detected": True}

    money = f"{amount:.2f}"
    submitted = False
    try:
        if add_fee_first:
            submitted = True
            _submit_finance_form(page, "fees", "AddFees.php",
                                 {"txt_Amount": money, "txt_Rec": "joining fee", "txt_Date": paid_on}, "Once-Off")
            open_payment_profile(page, ref, surname)
        submitted = True
        _submit_finance_form(page, "payment", "AddPayment.php",
                             {"txt_Amount": money, "txt_Rec": receipt, "txt_Date": paid_on}, payment_type)
        open_payment_profile(page, ref, surname)
        after = read_ledger(page)
    except Exception as exc:
        raise ItensityPushError(f"Payment not confirmed: {type(exc).__name__}", submitted=submitted) from exc

    new_rows = [row for row in after if row not in before]
    paid = [row for row in new_rows if "payment" in row["type"].lower()
            and row["amount"] is not None and abs(abs(row["amount"]) - amount) < 0.005]
    if not paid:
        raise ItensityPushError("The payment was sent but isn't on the account", submitted=True)
    return {"status": "sent", "fee_detected": bool(reg_fee_rows(after, amount))}


def html_to_pdf(html: str, output_path: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_context(java_script_enabled=False).new_page()
            page.set_content(html, wait_until="load")
            page.pdf(path=output_path, format="A4", print_background=True)
        finally:
            browser.close()
