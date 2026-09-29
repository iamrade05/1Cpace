"""
NuPay new merchant portal mandate push (merchant.nupay.co.za).

Ported verbatim from the reference 1Cpace app's nupay_new_push.py (Phase 2 of
the 1Cpase rewrite) so this app owns the driver natively instead of
dynamically importing it from a sibling repo at runtime. The Playwright
automation and the old-portal-code -> new-portal-code translation tables are
left untouched — they encode UI quirks (timing, field names, code schemes)
discovered empirically against the live portal.

Uses merchant.nupay.co.za with Playwright and TOTP credentials from .env.
This is the sole NuPay driver backing the DebiCheck/debit-order workflow.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import pyotp
    from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright
except ImportError as exc:
    raise ImportError(
        f"Missing dependency: {exc.name}. Run: pip install -r requirements.txt"
    ) from exc

from .playwright_lock import PLAYWRIGHT_LOCK


BASE = "https://merchant.nupay.co.za"
LOGIN_URL = f"{BASE}/login"
DEFAULT_ACCESS_ID = "000025500014054"
DEFAULT_ACCESS_TYPE = "M"


@dataclass
class PushResult:
    success: bool
    message: str
    raw_response: str = ""


@dataclass
class MandateStatusResult:
    found: bool
    status: str
    message: str
    details: str = ""
    raw_response: str = ""


_BRANCH_TO_BANK_ID: dict[str, str] = {
    "051001": "1",   # Standard Bank
    "198765": "2",   # Nedbank
    "250655": "3",   # FNB
    "410506": "6",
    "430000": "7",   # African Bank/Ubank
    "450105": "9",
    "470010": "10",  # Capitec
    "632005": "16",  # ABSA
    "462005": "44",  # Bidvest
    "589000": "55",
    "678910": "61",  # Tyme Bank
    "679000": "63",  # Discovery Bank
    "352000": "67",
}


def _required(value: Any) -> str:
    return str(value or "").strip()


def _validate_mandate(mandate: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_fields = {
        "merchant_id": "Merchant",
        "account_name": "Account name",
        "account_type": "Account type",
        "account_number": "Account number",
        "branch_code": "Branch code",
        "no_installments": "No. installments",
        "tracking": "Tracking",
        "submit_date": "Submit date",
        "client_ref1": "Client reference 1",
        "client_ref2": "Client reference 2",
    }
    for field, label in required_fields.items():
        if not _required(mandate.get(field)):
            errors.append(f"{label} is required")

    branch_code = _required(mandate.get("branch_code"))
    if branch_code and branch_code not in _BRANCH_TO_BANK_ID:
        errors.append(f"Unsupported branch code for NuPay bank lookup: {branch_code}")

    submit_date = _required(mandate.get("submit_date"))
    if submit_date:
        try:
            parsed_submit_date = datetime.strptime(submit_date, "%Y-%m-%d").date()
            if parsed_submit_date < date.today():
                errors.append("Submit date cannot be in the past")
        except ValueError:
            errors.append("Submit date must be a valid date")

    id_type = _required(mandate.get("id_type")) or "0"
    if id_type == "0" and not _required(mandate.get("id_number")):
        errors.append("SA ID number is required")
    elif id_type == "1" and not _required(mandate.get("passport")):
        errors.append("Passport number is required")
    elif id_type == "4" and not _required(mandate.get("juristic_account")):
        errors.append("Juristic account is required")

    try:
        amount = float(mandate.get("instalment_amount") or 0)
        if amount <= 0:
            rands = int(mandate.get("instalment_rands", 0) or 0)
            cents = int(mandate.get("instalment_cents", 0) or 0)
            amount = rands + cents / 100
        if amount <= 0:
            errors.append("Instalment amount must be greater than zero")
    except (TypeError, ValueError):
        errors.append("Instalment amount is invalid")

    return errors


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _require_creds() -> list[str]:
    missing = [
        name
        for name in ("NUPAY_NEW_EMAIL", "NUPAY_NEW_PASSWORD", "NUPAY_TOTP_SECRET")
        if not _env(name)
    ]
    return missing


def _safe_fill(page: Page, selector: str, value: Any) -> bool:
    loc = page.locator(selector)
    if not loc.count():
        return False
    loc.first.fill(str(value or ""))
    return True


def _safe_select(page: Page, selector: str, value: Any) -> bool:
    value_text = str(value or "")
    loc = page.locator(selector)
    if not loc.count():
        return False
    loc.first.select_option(value_text)
    return True


def _set_date(page: Page, selector: str, value: str) -> None:
    page.evaluate(
        """([s, v]) => {
            const el = document.querySelector(s);
            if (!el) return;
            el.removeAttribute('readonly');
            el.value = v;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        [selector, value],
    )


def _dismiss_promos(page: Page) -> None:
    try:
        no_btn = page.get_by_role("button", name=re.compile("^NO$", re.I))
        if no_btn.count():
            stop = page.get_by_text("Stop Reminding Me")
            if stop.count():
                checkbox = page.locator("input[type='checkbox']").last
                if checkbox.count() and not checkbox.is_checked():
                    checkbox.check(timeout=2_000)
            no_btn.first.click(timeout=3_000)
    except PlaywrightTimeout:
        pass


def _login(page: Page) -> None:
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
    page.locator("#email").fill(_env("NUPAY_NEW_EMAIL"))
    page.locator("#password").fill(_env("NUPAY_NEW_PASSWORD"))
    terms = page.locator("#terms")
    if terms.count() and not terms.is_checked():
        terms.check()
    page.get_by_role("button", name=re.compile("LOG IN", re.I)).click()

    page.wait_for_url("**/2fa/**", timeout=30_000)
    code = pyotp.TOTP(_env("NUPAY_TOTP_SECRET")).now()
    page.locator("input[name='2fa'], #\\32 fa").first.fill(code)
    page.get_by_role("button", name=re.compile("VERIFY", re.I)).click()
    page.wait_for_url("**/dashboard**", timeout=30_000)
    _dismiss_promos(page)


def _open_upload(page: Page, merchant_id: str) -> None:
    access_id = _env("NUPAY_NEW_ACCESS_ID") or merchant_id or DEFAULT_ACCESS_ID
    access_type = _env("NUPAY_NEW_ACCESS_TYPE") or DEFAULT_ACCESS_TYPE
    page.goto(
        f"{BASE}/debicheck?access_id={access_id}&access_type={access_type}",
        wait_until="networkidle",
        timeout=45_000,
    )
    page.goto(
        f"{BASE}/debicheck/{access_id}/mandate-upload",
        wait_until="networkidle",
        timeout=45_000,
    )


def _client_id(mandate: dict[str, Any]) -> str:
    id_type = str(mandate.get("id_type") or "0")
    if id_type == "1":
        return str(mandate.get("passport") or "")
    if id_type == "4":
        return str(mandate.get("juristic_account") or "")
    return str(mandate.get("id_number") or "")


def _amount(mandate: dict[str, Any]) -> str:
    value = mandate.get("instalment_amount")
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        rands = int(mandate.get("instalment_rands", 0) or 0)
        cents = int(mandate.get("instalment_cents", 0) or 0)
        amount = rands + cents / 100
    return f"{amount:.2f}".rstrip("0").rstrip(".")


# The new merchant portal uses its own code scheme, verified directly against
# the live <select> elements on the mandate-upload form (2026-08-22) — it does
# NOT match the old portal's conventions our own app's dropdowns were built
# around (debicheck.py's mandate_from_member()). Every
# mandate we hand off has to be translated here before it reaches the form.
_TRACKING_TO_NEW: dict[str, str] = {
    "12": "00",  # No Tracking -> 0 Day Tracking
    "13": "01",  # 1 Day
    "01": "02",  # 2 Days
    "14": "03",  # 3 Days
    "02": "04",  # 4 Days
    "03": "05",  # 5 Days
    "04": "06",  # 6 Days
    "15": "07",  # 7 Days
    "05": "08",  # 8 Days
    "06": "09",  # 9 Days
    "07": "10",  # 10 Days
}
_ACCOUNT_TYPE_TO_NEW: dict[str, str] = {"1": "01", "2": "02", "3": "03"}
_ID_TYPE_TO_NEW: dict[str, str] = {"0": "2", "1": "1", "4": "4"}  # RSA/Passport/Juristic
_AUTH_TYPE_TO_NEW: dict[str, str] = {
    "cell": "DELAYED", "tt1": "DELAYED",  # 'TT1' shows up on mandates imported from the old portal
    "rm_realtime": "real time", "rm_batch": "batch",
}
_FREQUENCY_TO_NEW: dict[str, str] = {
    "1": "WEEK", "2": "FRTN", "3": "MNTH",
    "5": "ADHO",  # End of Month -> "Monthly by Rule" + frequency_rule "99" (Last Day)
    "6": "ADHO",  # Last Friday of Month -> "Monthly by Rule" + frequency_rule "05" (Last Friday)
}
_FREQUENCY_RULE_FOR_OLD_CODE: dict[str, str] = {"5": "99", "6": "05"}


def _fill_upload_form(page: Page, mandate: dict[str, Any]) -> None:
    branch_code = str(mandate.get("branch_code") or "")
    bank_id = _BRANCH_TO_BANK_ID.get(branch_code, "")
    client_ref1 = str(mandate.get("client_ref1") or "")
    client_ref2 = str(mandate.get("client_ref2") or "")

    _safe_fill(page, "#id_number_rsa", _client_id(mandate))
    _safe_fill(page, "#client_ref_1", client_ref1)
    _safe_fill(page, "#client_ref_2", client_ref2)
    _safe_fill(page, "#debtor_account_name", mandate.get("account_name"))
    _safe_fill(page, "#debtor_account_number", mandate.get("account_number"))
    _safe_select(page, "#banks", bank_id)
    # Selecting a bank triggers the page's own JS to auto-populate Branch Code
    # asynchronously. Submitting before that settles leaves Branch Code
    # visibly filled but still flagged "This field is required" client-side,
    # which silently blocks Submit from ever opening the Confirm modal
    # (confirmed via a captured screenshot of exactly this state).
    page.wait_for_timeout(1_000)

    id_type = str(mandate.get("id_type") or "0")
    _safe_select(page, "select[name='id_type']", _ID_TYPE_TO_NEW.get(id_type, "2"))

    # No #id attribute on this element on the live page — must select by name.
    account_type = str(mandate.get("account_type") or "1")
    _safe_select(page, "select[name='debtor_account_type']", _ACCOUNT_TYPE_TO_NEW.get(account_type, "01"))

    _safe_fill(page, "#no_of_instalments", mandate.get("no_installments") or "1")
    _set_date(page, "#submit_date", str(mandate.get("submit_date") or ""))
    _safe_fill(page, "#instalment_value", _amount(mandate))

    tracking = str(mandate.get("tracking") or "14")
    _safe_select(page, "#tracking_selected", _TRACKING_TO_NEW.get(tracking, "03"))

    _safe_fill(page, "#employer_code", client_ref2)
    _safe_select(page, "#debtor_auth_required", os.getenv("NUPAY_NEW_AUTH_REQUIRED", "0227"))

    auth_type = str(mandate.get("auth_type") or "cell").strip().lower()
    _safe_select(page, "select[name='auth_type']", _AUTH_TYPE_TO_NEW.get(auth_type, "DELAYED"))

    frequency = str(mandate.get("frequency") or "3")
    _safe_select(page, "select[name='frequency']", _FREQUENCY_TO_NEW.get(frequency, "MNTH"))
    rule = _FREQUENCY_RULE_FOR_OLD_CODE.get(frequency)
    if rule:
        _safe_select(page, "select[name='frequency_rule']", rule)


def _record_dir() -> Path | None:
    """Directory for this run's step-by-step capture, or None when the
    NUPAY_RECORD_DIR env var is unset. Diagnostic only — the push behaves
    identically either way."""
    raw = os.getenv("NUPAY_RECORD_DIR", "").strip()
    if not raw:
        return None
    directory = Path(raw) / datetime.now().strftime("%Y%m%d_%H%M%S")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


_SECRET_PATTERNS = (
    re.compile(r"(password=)[^&\s\"]*", re.I),
    re.compile(r"(\"password\"\s*:\s*\")[^\"]*", re.I),
    re.compile(r"(otp=|totp=|two_factor_code=)[^&\s\"]*", re.I),
)


def _redact(text: str) -> str:
    """Strip credentials before anything is written to disk.

    The login POST body carries the portal password in clear text, and a
    capture directory gets copied around, attached to tickets and synced to
    OneDrive — it must never hold one.
    """
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


def _attach_network_log(
    page: Page,
    record_dir: "Path | None",
    failures: "list[str] | None" = None,
    results: "list[dict] | None" = None,
) -> None:
    """Log the portal's own XHR/fetch traffic to network.log.

    The page text cannot say whether CONFIRM actually submitted anything —
    a push that reaches DONE and creates no mandate looks identical to one
    that works. The request/response pair is the only place that shows it.
    Static assets are skipped; bodies are captured for API calls only."""
    # Capture must attach even without a record_dir: the failure list is
    # how the caller learns the portal rejected the submission.
    log = record_dir / "network.log" if record_dir is not None else None

    def _write(line: str) -> None:
        if log is None:
            return
        try:
            with log.open("a", encoding="utf-8") as handle:
                handle.write(_redact(line).rstrip() + "\n")
        except Exception:
            pass

    def _on_request(request) -> None:
        if request.resource_type in {"xhr", "fetch", "document"}:
            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            _write(f"[{stamp}] --> {request.method} {request.url}")
            if request.method != "GET":
                try:
                    _write(f"           body: {(request.post_data or '')[:1500]}")
                except Exception:
                    pass

    def _on_response(response) -> None:
        request = response.request
        if request.resource_type not in {"xhr", "fetch", "document"}:
            return
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        _write(f"[{stamp}] <-- {response.status} {response.url}")
        body = ""
        if request.resource_type in {"xhr", "fetch"}:
            try:
                body = response.text()[:1500]
            except Exception:
                body = ""
            if body:
                _write(f"           resp: {body}")
        if failures is None or request.method == "GET":
            return
        # 419 is Laravel's expired-CSRF answer, and is how this portal
        # rejects a submission while still rendering a normal-looking page.
        if response.status >= 400:
            failures.append(
                f"HTTP {response.status} from {response.url}: {_redact(body)[:300]}"
            )
            return
        # The portal's JSON reply is the authoritative outcome. The modal DOM
        # is not: on 2026-09-09 a mandate was created successfully and the
        # modal's "Contract Reference:" field stayed blank, so scraping the
        # page reported a false failure for a real mandate.
        try:
            payload = json.loads(body) if body.lstrip().startswith("{") else None
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return
        if "contract_reference" in payload or "success" in payload:
            if results is not None:
                results.append(payload)
            if payload.get("success") is False:
                failures.append(
                    f"portal reported success=false at {response.url}: "
                    f"{_redact(str(payload.get('message') or body))[:300]}"
                )

    page.on("request", _on_request)
    page.on("response", _on_response)
    page.on("console", lambda msg: _write(f"[console:{msg.type}] {msg.text[:500]}"))
    page.on("pageerror", lambda err: _write(f"[pageerror] {str(err)[:500]}"))


def _snap(page: Page, record_dir: "Path | None", label: str) -> None:
    """Save a screenshot plus the page's visible text at one step of the
    push. Never raises — a broken capture must not fail a real push."""
    if record_dir is None:
        return
    try:
        page.screenshot(path=str(record_dir / f"{label}.png"), full_page=True)
        (record_dir / f"{label}.txt").write_text(
            f"url: {page.url}\n\n" + page.locator("body").inner_text(timeout=5_000),
            encoding="utf-8",
        )
    except Exception:
        pass


def push_mandate(mandate: dict[str, Any]) -> PushResult:
    missing = _require_creds()
    if missing:
        return PushResult(False, "Missing .env value(s): " + ", ".join(missing))

    validation_errors = _validate_mandate(mandate)
    if validation_errors:
        return PushResult(False, "; ".join(validation_errors))

    headed = os.getenv("NUPAY_NEW_HEADED", "0") == "1"
    merchant_id = str(mandate.get("merchant_id") or DEFAULT_ACCESS_ID)

    PLAYWRIGHT_LOCK.acquire()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            record_dir = _record_dir()
            # The portal answers a rejected submission with an HTTP error and
            # a normal-looking page, so page text alone cannot decide success.
            api_failures: list[str] = []
            api_results: list[dict] = []
            _attach_network_log(page, record_dir, api_failures, api_results)
            _login(page)
            _snap(page, record_dir, "01_after_login")
            _open_upload(page, merchant_id)
            _snap(page, record_dir, "02_upload_form")
            _fill_upload_form(page, mandate)
            _snap(page, record_dir, "03_form_filled")

            # NuPay answers a submission made too soon after the form renders
            # with HTTP 419 and "Please take a moment to review all fields
            # before submitting" — a dwell guard on its custom _form_nonce,
            # not a CSRF failure (every /livewire/update in the same session
            # returns 200). Give the form the time it asks for. The threshold
            # is undocumented, hence the override.
            dwell = float(os.getenv("NUPAY_SUBMIT_DELAY_SECONDS", "12") or 0)
            if dwell > 0:
                page.wait_for_timeout(int(dwell * 1000))
            page.locator("#btnSubmit").click(timeout=15_000)
            _snap(page, record_dir, "04_after_submit")
            try:
                # The dwell guard applies to the confirmation step too. On
                # 2026-09-09 the form POST returned 200, CONFIRM was clicked
                # 0.3s later, the page reloaded to a blank form and nothing
                # reached the Mandate Report. Let the modal settle, then give
                # it the same pause the submit needed.
                confirm = page.get_by_role("button", name=re.compile("^CONFIRM$", re.I)).first
                confirm.wait_for(state="visible", timeout=15_000)
                if dwell > 0:
                    page.wait_for_timeout(int(dwell * 1000))
                confirm.click(timeout=15_000)
                _snap(page, record_dir, "05_after_confirm")
            except PlaywrightTimeout:
                debug_dir = Path(__file__).resolve().parent / "nupay_debug"
                debug_dir.mkdir(exist_ok=True)
                shot_path = debug_dir / f"confirm_timeout_{datetime.now():%Y%m%d_%H%M%S}.png"
                page.screenshot(path=str(shot_path), full_page=True)
                body = page.locator("body").inner_text(timeout=5_000)
                browser.close()
                return PushResult(
                    False,
                    f"NuPay new portal never showed a CONFIRM button after Submit "
                    f"(screenshot saved: {shot_path.name}): {body[:400]}",
                    body[:2000],
                )

            success_text = ""
            try:
                done = page.get_by_role("button", name=re.compile("DONE", re.I))
                done.wait_for(timeout=30_000)
                # NuPay reuses #confirmation-modal for the review pass AND the
                # eventual result — the DONE button appears immediately while
                # the modal still holds review boilerplate ("Please take a
                # moment to review all fields before submitting…"), then an
                # async call swaps that text in-place for the real outcome
                # ("Mandate created successfully. Contract Reference: ...").
                # DONE itself navigates away (confirmed against a real manual
                # run's DevTools recording), so the result has to be read
                # BEFORE clicking it — but only once it has actually arrived.
                try:
                    page.wait_for_function(
                        r"""() => {
                            const el = document.querySelector('#confirmation-modal');
                            if (!el) return false;
                            const t = el.innerText.trim();
                            if (!t) return false;
                            const low = t.toLowerCase();
                            // The review boilerplate is NOT always the first
                            // line — the modal opens with an empty
                            // "Contract Reference:" and the boilerplate sits
                            // below it, so startsWith() let the review pass
                            // through as if it were the result (see the
                            // 2026-09-09 08:17 push, which stored exactly
                            // that with a blank reference).
                            if (low.includes('please take a moment to review')) return false;
                            if (low.includes('please confirm the information')) return false;
                            // A reference with an actual value is the result.
                            if (/contract reference:[^\S\r\n]*\S/i.test(t)) return true;
                            return /(success|fail|error|declined|rejected|response code)/i.test(low);
                        }""",
                        # Confirmed against real pushes: the async result can
                        # take 30-35s to land, not just a couple of seconds —
                        # 20s was giving up on genuine successes while they
                        # were still in flight. 60s leaves real headroom.
                        timeout=60_000,
                    )
                except PlaywrightTimeout:
                    pass
                success_text = page.locator("#confirmation-modal").inner_text(timeout=5_000)
                _snap(page, record_dir, "06_modal_result")
                # DONE is the click that actually commits the mandate. It
                # returns as soon as the click is dispatched, and the browser
                # was being closed immediately afterwards in the finally
                # below — cancelling the request in flight. Confirmed against
                # a real push on 2026-09-09: the app reported "mandate
                # submitted" and NuPay's report held no such mandate at all.
                # Wait for the portal to settle before letting go.
                done.click()
                try:
                    page.wait_for_load_state("networkidle", timeout=60_000)
                except PlaywrightTimeout:
                    pass
                _snap(page, record_dir, "07_after_done")
                try:
                    after_done = page.locator("#confirmation-modal").inner_text(timeout=5_000)
                except Exception:
                    after_done = ""
                if after_done.strip():
                    success_text = after_done
            except PlaywrightTimeout:
                body = page.locator("body").inner_text(timeout=5_000)
                return PushResult(False, f"NuPay new portal did not confirm submission: {body[:400]}", body[:2000])
            finally:
                browser.close()

            stripped = success_text.strip()
            # The portal's own JSON reply decides the outcome. The modal DOM
            # is unreliable: on 2026-09-09 NuPay answered
            # {"success":true,...,"contract_reference":"DCPRD00029TTXL"} while
            # the modal's Contract Reference field stayed blank, so scraping
            # the page failed a mandate that had really been created.
            created = next(
                (r for r in api_results if r.get("contract_reference")), None
            )
            if created:
                reference = str(created["contract_reference"]).strip()
                note = str(created.get("message") or "Mandate created successfully.").strip()
                return PushResult(True, f"{note} Contract Reference: {reference}"[:400], reference)
            if api_failures:
                return PushResult(
                    False,
                    "NuPay rejected the submission: " + api_failures[0][:300],
                    "; ".join(api_failures)[:2000],
                )
            if re.search(r"\b\d{6}\s*-", stripped):
                return PushResult(
                    False,
                    f"NuPay rejected the mandate: {stripped[:300]}",
                    success_text[:2000],
                )
            if not stripped:
                return PushResult(False, "NuPay completed the confirmation flow without a response.")
            return PushResult(
                False,
                "NuPay returned no contract reference, so the mandate was not "
                "registered: " + " ".join(stripped.split())[:250],
                success_text[:2000],
            )
    except Exception as exc:
        return PushResult(False, f"NuPay new portal push failed: {exc}")
    finally:
        PLAYWRIGHT_LOCK.release()


def push_mandate_manual_review(mandate: dict[str, Any]) -> PushResult:
    """Fill the mandate form in a *visible* browser window and hand off to a
    human for Submit / CONFIRM / DONE — the driver only fills fields and
    watches, it never clicks the submission chain itself.

    This exists because the automated push_mandate() has repeatedly guessed
    wrong about when NuPay's confirmation-modal (id="confirmation-modal",
    reused for every stage) holds a real result vs. still-stale review
    boilerplate, which produced false "success" pushes. Letting a person
    make the actual submit/confirm decision — and only capturing whatever
    text NuPay reports once it stops looking like boilerplate — removes
    that guesswork entirely. Always runs headed regardless of
    NUPAY_NEW_HEADED, since a headless window can't be reviewed by anyone.
    """
    missing = _require_creds()
    if missing:
        return PushResult(False, "Missing .env value(s): " + ", ".join(missing))

    validation_errors = _validate_mandate(mandate)
    if validation_errors:
        return PushResult(False, "; ".join(validation_errors))

    merchant_id = str(mandate.get("merchant_id") or DEFAULT_ACCESS_ID)
    closed_early = {"flag": False}

    PLAYWRIGHT_LOCK.acquire()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.on("close", lambda: closed_early.__setitem__("flag", True))

            _login(page)
            _open_upload(page, merchant_id)
            _fill_upload_form(page, mandate)

            # Everything past this point is the human's decision — we only
            # wait for what they do in the window and read the result back.
            try:
                page.wait_for_selector(
                    "#confirmation-modal", state="visible", timeout=0
                )
                done = page.get_by_role("button", name=re.compile("DONE", re.I))
                done.wait_for(timeout=0)
                page.wait_for_function(
                    r"""() => {
                        const el = document.querySelector('#confirmation-modal');
                        if (!el) return false;
                        const t = el.innerText.trim();
                        if (!t) return false;
                        const low = t.toLowerCase();
                        // Same reuse trap as push_mandate: match anywhere,
                        // not just at the start of the modal text.
                        if (low.includes('please take a moment to review')) return false;
                        if (low.includes('please confirm the information')) return false;
                        if (/contract reference:[^\S\r\n]*\S/i.test(t)) return true;
                        return /(success|fail|error|declined|rejected|response code)/i.test(low);
                    }""",
                    timeout=0,
                )
                success_text = page.locator("#confirmation-modal").inner_text(timeout=5_000)
            except Exception:
                if closed_early["flag"]:
                    return PushResult(
                        False,
                        "Browser window was closed before NuPay showed a final result "
                        "(nothing was recorded — if you actually submitted it, use "
                        "'Check NuPay Status' to pick up the real outcome).",
                    )
                raise
            finally:
                if not closed_early["flag"]:
                    browser.close()
    except Exception as exc:
        return PushResult(False, f"NuPay manual-review push failed: {exc}")
    finally:
        PLAYWRIGHT_LOCK.release()

    stripped = success_text.strip()
    if not stripped or stripped.lower().startswith((
        "please take a moment to review",
        "please confirm the information",
    )):
        return PushResult(
            False,
            f"NuPay never showed a real result: {stripped[:300]}",
            success_text[:2000],
        )
    return PushResult(True, stripped[:400], success_text[:2000])


def _find_balanced_array_end(text: str, start: int) -> int | None:
    """`start` points at a '['; returns the index just past its matching ']',
    respecting quoted strings (and escaped quotes) along the way."""
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                in_string = False
            continue
        if ch in ("'", '"'):
            in_string = True
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _parse_tabulator_rows(x_data_value: str) -> list[dict[str, Any]] | None:
    """Pull the row-data array out of an Alpine `x-data="tabulatorComponent(
    'name', [columns], [rows])"` attribute value (already HTML-entity-decoded,
    e.g. via Playwright's getAttribute()). Returns the parsed row list, or
    None if the shape doesn't match what's expected."""
    first_bracket = x_data_value.find("[")
    if first_bracket == -1:
        return None
    first_end = _find_balanced_array_end(x_data_value, first_bracket)
    if first_end is None:
        return None
    second_bracket = x_data_value.find("[", first_end)
    if second_bracket == -1:
        return None
    second_end = _find_balanced_array_end(x_data_value, second_bracket)
    if second_end is None:
        return None
    try:
        return json.loads(x_data_value[second_bracket:second_end])
    except json.JSONDecodeError:
        return None


# NuPay's own status wording, taken from the status[] checkboxes on the
# mandate-search form: "Active", "Pending Authorisation", "Rejected
# Authorisation", "In Active" and "Suspended". Only "Active" happens to
# coincide with this app's vocabulary, so the portal's value has to be mapped
# rather than lower-cased and used as-is — doing the latter stored
# "pending authorisation" and "in active" straight into
# debicheck_mandates.status, which nothing downstream recognises.
#
# Order matters. "In Active" contains "active" and "Rejected Authorisation"
# contains "authorisation", so the negative wordings are tested first.
_STATUS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("rejected",  r"reject|declin|unsuccessful|\bfail"),
    ("cancelled", r"\bin[\s_-]*active\b|inactive|suspend|cancel|expire|terminat|abandon|dormant"),
    ("pending",   r"pending|awaiting|waiting|processing|initiated|in progress|loaded|submitted"),
    ("active",    r"\bactive\b|accept|approved|authenticated|successful|registered|complete"),
)


def _normalize_mandate_status(*values: Any) -> str | None:
    """Map NuPay's status wording onto this app's mandate vocabulary
    (active / pending / rejected / cancelled), taking the first value that
    carries a recognisable status. Returns None when none of them do."""
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
        if not text:
            continue
        for normalized, pattern in _STATUS_PATTERNS:
            if re.search(pattern, text):
                return normalized
    return None


def fetch_mandate_report_rows(
    mandate: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, str]:
    """Log in to NuPay once and read its whole Mandate Report.

    Returns (rows, message); rows is None when the report could not be read. The
    single-mandate check and the bulk sync both read it this way: the report
    always holds every mandate, so looking up one member costs the same login
    and download as looking up all of them."""
    missing = _require_creds()
    if missing:
        return None, "Missing .env value(s): " + ", ".join(missing)

    merchant_id = str((mandate or {}).get("merchant_id") or DEFAULT_ACCESS_ID)
    access_id = _env("NUPAY_NEW_ACCESS_ID") or merchant_id or DEFAULT_ACCESS_ID
    headed = os.getenv("NUPAY_NEW_HEADED", "0") == "1"

    PLAYWRIGHT_LOCK.acquire()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            try:
                _login(page)
                page.goto(
                    f"{BASE}/debicheck/{access_id}/mandate-search",
                    wait_until="networkidle",
                    timeout=45_000,
                )
                # The results table only renders after the search form is
                # submitted — leave all criteria at their defaults (every
                # status type pre-checked, "Screen Enquiry" delivery) except
                # the date range, which defaults to today-only (a flatpickr
                # widget, readonly — has to be set via JS, not .fill()).
                # Widen it so mandates from any date are actually found.
                earliest = os.getenv("NUPAY_NEW_STATUS_FROM_DATE", "2020-01-01")
                _set_date(page, "#from_date", earliest)
                # A DebiCheck mandate is normally dated a few days ahead, so a
                # report window that ends today can never contain the newest
                # ones — precisely the mandates a status check is run for.
                # (2026-09-09: DCPRD00029TTXL was created with submit_date
                # 2026-09-11 and was invisible to this search.)
                forward = int(os.getenv("NUPAY_NEW_STATUS_FORWARD_DAYS", "180") or 0)
                _set_date(page, "#to_date", (date.today() + timedelta(days=forward)).isoformat())
                page.get_by_role("button", name=re.compile("^CONTINUE$", re.I)).first.click(timeout=15_000)
                page.wait_for_selector('[x-data^="tabulatorComponent("]', timeout=45_000)
                raw = page.evaluate(
                    r"""() => {
                        const el = document.querySelector('[x-data^="tabulatorComponent("]');
                        return el ? el.getAttribute('x-data') : null;
                    }"""
                )
            finally:
                browser.close()
    except Exception as exc:
        return None, f"NuPay new portal status check failed: {exc}"
    finally:
        PLAYWRIGHT_LOCK.release()

    if not raw:
        return None, "Mandate report table not found on the new portal page."

    rows = _parse_tabulator_rows(raw)
    if rows is None:
        return None, "Could not parse the new portal's mandate report table."
    return rows, f"{len(rows)} rows read"


def _acceptance_key(row: dict[str, Any]) -> str:
    # Full-precision timestamp when available ('2024-09-18 11:33:03.843'),
    # else the plain created date - both sort correctly as ISO-ish strings,
    # newest last.
    return str(row.get("mandate_acceptance_date") or row.get("date_created") or "")


def match_mandate_row(
    rows: list[dict[str, Any]], mandate: dict[str, Any]
) -> tuple[dict[str, Any] | None, bool]:
    """Pick the report row that belongs to *mandate*.

    Returns (row, pre_join_only). pre_join_only is True when the only rows found
    were accepted before the member joined, so the row probably belongs to
    someone else's earlier mandate and its status must not be applied."""
    # Match against the specific field each identifier corresponds to, not
    # the whole row — and only when long enough to be reliably unique.
    # client_ref2 (a 4-digit staff number) is deliberately excluded: it's
    # far too short to avoid false positives across a report with thousands
    # of rows (confirmed empirically — it collided with an unrelated
    # mandate's employer_code on the very first real test).
    contract_reference = str(mandate.get("contract_reference") or "").strip().lower()
    client_ref1 = str(mandate.get("client_ref1") or "").strip().lower()
    account_number = str(mandate.get("account_number") or "").strip().lower()
    member_join_date = str(mandate.get("member_join_date") or "").strip()

    def _field_match(value: str, row_value: Any) -> bool:
        if not value or len(value) < 6:
            return False
        row_str = str(row_value or "").strip().lower()
        if not row_str:
            return False
        return value in row_str or row_str.endswith(value)

    # Client reference is the definitive match — it's the identifier this
    # app itself assigns and controls, unlike contract_reference/account
    # number which can coincidentally collide or belong to an old attempt
    # (confirmed for real: two members paying from the same bank account —
    # searching by the newer member's own client reference found nothing,
    # fell back to matching on the shared account number, and silently
    # returned the OTHER member's older contract as this one's status).
    # Collect candidates from whichever strategy actually finds something,
    # but apply the same join-date guard to all of them either way — the
    # fallback fields are exactly the ones most likely to collide across
    # members who happen to share a bank account.
    client_ref_matches = [
        row for row in rows
        if client_ref1 and _field_match(client_ref1, row.get("client_reference"))
    ]
    if client_ref_matches:
        candidates = client_ref_matches
    else:
        candidates = [
            row for row in rows
            if (contract_reference and _field_match(contract_reference, row.get("contract_reference")))
            or (account_number and _field_match(account_number, row.get("debtor_account_number")))
        ]

    stale_pre_join = False
    if candidates:
        # Only trust matches accepted on/after the member's own join date;
        # if none qualify, that's a real signal this record belongs to
        # someone else's earlier mandate (e.g. a shared bank account), not
        # to this membership.
        post_join = [
            row for row in candidates
            if not member_join_date or _acceptance_key(row) >= member_join_date
        ]
        if post_join:
            match = max(post_join, key=_acceptance_key)
        else:
            match = max(candidates, key=_acceptance_key)
            stale_pre_join = True
    else:
        match = None

    return match, stale_pre_join


def refresh_mandate_status(mandate: dict[str, Any]) -> MandateStatusResult:
    """Look up a mandate's live status via the new portal's Mandate Report
    page (Alpine/Tabulator table embedded directly in the page HTML)."""
    missing = _require_creds()
    if missing:
        return MandateStatusResult(False, "unknown", "Missing .env value(s): " + ", ".join(missing))

    contract_reference = str(mandate.get("contract_reference") or "").strip().lower()
    client_ref1 = str(mandate.get("client_ref1") or "").strip().lower()
    account_number = str(mandate.get("account_number") or "").strip().lower()
    member_join_date = str(mandate.get("member_join_date") or "").strip()
    if not (contract_reference or len(client_ref1) >= 6 or len(account_number) >= 6):
        return MandateStatusResult(False, "unknown", "No sufficiently specific reference available for NuPay lookup.")

    rows, read_message = fetch_mandate_report_rows(mandate)
    if rows is None:
        return MandateStatusResult(False, "unknown", read_message)

    match, stale_pre_join = match_mandate_row(rows, mandate)

    if match is None:
        return MandateStatusResult(
            False, "unknown",
            f"Mandate not found in the new portal's Mandate Report ({len(rows)} rows checked).",
        )

    status = _normalize_mandate_status(
        match.get("status"), match.get("status_reason"), match.get("mandate_status")
    ) or "unknown"
    message = (
        f"Contract {match.get('contract_reference', '?')}: {match.get('status', '?')}"
        f" ({match.get('status_reason', '')}) — {str(match.get('response_description', '')).strip()}"
    ).strip()
    if stale_pre_join:
        message = (
            f"⚠ Only a pre-join match found (accepted {_acceptance_key(match)}, "
            f"before this member's join date {member_join_date}) — likely belongs "
            f"to someone else's earlier mandate (e.g. a shared bank account), not "
            f"this membership. Not applying this status automatically. {message}"
        )
        # found=False so the caller doesn't overwrite the mandate's local
        # status with a record that isn't this membership's own — the
        # caveat message is still saved so a human can investigate.
        return MandateStatusResult(False, status, message, details="", raw_response=json.dumps(match)[:2000])

    return MandateStatusResult(True, status, message, details="", raw_response=json.dumps(match)[:2000])


def wait_for_mandate_status(
    mandate: dict[str, Any],
    attempts: int = 1,
    delay_seconds: int = 0,
) -> MandateStatusResult:
    return refresh_mandate_status(mandate)


# ── Cancelling one mandate (Transaction Maintenance) ─────────────────────────
#
# Done by hand this way: Transaction Maintenance -> Mandate Maintenance (No
# Authentication required) -> Change Type "CC" (cancellation) -> search by contract
# reference -> CONTINUE lists the mandate -> tick the header checkbox, FILTER SELECTED,
# tick again -> SUBMIT. That header checkbox selects EVERY listed row, so this only ever
# goes ahead when exactly one mandate is listed and it is the one asked for.

@dataclass
class CancelResult:
    ready: bool            # the guarded flow reached the point where SUBMIT could be pressed
    submitted: bool        # SUBMIT was actually pressed (True also when the outcome is unknown)
    message: str
    ok: bool | None = None  # NuPay's reply read as success / failure / unclear (only after submitting)
    evidence: str = ""      # screenshot of NuPay's reply, when a folder was given


def check_cancellation_target(row_texts: list[str], contract_reference: str | None) -> str | None:
    """Why NOT to go ahead, or None when exactly one mandate is listed and it is the target."""
    target = " ".join(str(contract_reference or "").split()).lower()
    if not target:
        return "No contract reference given."
    if not row_texts:
        return ("NuPay lists no mandate for that contract reference - it may already be "
                "cancelled, or is not open to cancellation.")
    if len(row_texts) > 1:
        return (f"NuPay listed {len(row_texts)} mandates; refusing to select them all. "
                "Only a single mandate may be cancelled at a time.")
    if target not in " ".join(row_texts[0].split()).lower():
        return "The mandate NuPay listed is not the one asked for; refusing."
    return None


_REPLY_FAILURE_WORDS = ("error", "fail", "unable", "invalid", "reject", "not found", "could not")
_REPLY_SUCCESS_WORDS = ("success", "submitted", "accepted", "processed", "completed")


def read_maintenance_reply(texts: list[str]) -> bool | None:
    """Read NuPay's reply page conservatively: any failure wording wins, and anything
    unrecognised is None (unknown) rather than a guess at success."""
    joined = " ".join(texts).lower()
    if any(word in joined for word in _REPLY_FAILURE_WORDS):
        return False
    if any(word in joined for word in _REPLY_SUCCESS_WORDS):
        return True
    return None


def extract_maintenance_result(body_text: str) -> str:
    """The outcome of a Transaction Maintenance submission lives in the "Mandate
    Maintenance Result" table's Result column - the maintenance-ws page has no
    heading/alert/toast element carrying it. An earlier version of this driver
    scraped `h1, h2, h3, [role='alert'], [role='status'], .alert, .toast` for the
    reply and it kept matching an sr-only "Notifications" span for the header's
    bell icon (present on every page) instead, so a real batch of 72 cancellations
    on 2026-09-29 read back "NuPay says: Notifications" for 68 of them regardless
    of the actual result. Reading the whole page body sidesteps guessing at
    whichever container the result table happens to use."""
    match = re.search(r"Mandate Maintenance Result(.*?)(?:Showing \d|$)", body_text, re.I | re.S)
    return " ".join((match.group(1) if match else body_text).split())


def cancel_mandate(
    contract_reference: str | None, *, submit: bool = False, evidence_dir: str | None = None,
) -> CancelResult:
    """Cancel ONE mandate at NuPay. A dry run unless submit=True: it does every step
    except pressing SUBMIT, so what would be cancelled can be checked first."""
    reference = str(contract_reference or "").strip()
    if not reference:
        return CancelResult(False, False, "No contract reference given.")
    missing = _require_creds()
    if missing:
        return CancelResult(False, False, "Missing .env value(s): " + ", ".join(missing))

    access_id = _env("NUPAY_NEW_ACCESS_ID") or DEFAULT_ACCESS_ID
    submit_pressed = False

    def listed(page) -> list[str]:
        return page.locator("#maintenance .tabulator-row").all_inner_texts()

    def header_checkbox(page):
        return page.locator("#maintenance .tabulator-header input[type='checkbox']").first

    PLAYWRIGHT_LOCK.acquire()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_context(viewport={"width": 1500, "height": 1000}).new_page()
                _login(page)
                page.goto(f"{BASE}/debicheck/{access_id}/maintenance-search",
                          wait_until="networkidle", timeout=45_000)
                page.locator('select:has(option[value="MandateMaintenanceNoAuth"])').first \
                    .select_option("MandateMaintenanceNoAuth")
                page.wait_for_selector('select option[value="CC"]', state="attached", timeout=15_000)
                page.locator('select:has(option[value="CC"])').first.select_option("CC")
                page.locator('select:has(option[value="date_created"])').first.select_option("date_created")
                page.locator('select:has(option[value="contract_reference"])').first \
                    .select_option("contract_reference")
                page.locator("#searchText").fill(reference)
                _set_date(page, "#from_date", "2020-01-01")
                _set_date(page, "#to_date", date.today().isoformat())
                page.locator("#btnContinue").click(timeout=15_000)
                page.wait_for_url("**/maintenance-results**", timeout=45_000)
                page.wait_for_selector("#maintenance", timeout=45_000)
                page.wait_for_timeout(1500)

                reason = check_cancellation_target(listed(page), reference)
                if reason:
                    return CancelResult(False, False, reason)

                header_checkbox(page).click()
                page.get_by_role("button", name=re.compile("FILTER SELECTED", re.I)).click(timeout=15_000)
                page.wait_for_timeout(1500)
                reason = check_cancellation_target(listed(page), reference)     # again, after filtering
                if reason:
                    return CancelResult(False, False, reason)
                selected = page.locator("#maintenance .tabulator-row.tabulator-selected")
                if selected.count() == 0:
                    header_checkbox(page).click()
                    page.wait_for_timeout(500)
                if page.locator("#maintenance .tabulator-row.tabulator-selected").count() != 1:
                    return CancelResult(False, False,
                                        "Could not confirm exactly one selected mandate; nothing was submitted.")

                if not submit:
                    return CancelResult(True, False,
                                        "Dry run: exactly one mandate is listed and selected. Nothing was submitted.")

                submit_pressed = True
                page.locator("#btnMaintain").click(timeout=15_000)
                page.wait_for_url("**/maintenance-ws**", timeout=60_000)
                page.wait_for_timeout(1500)
                summary = extract_maintenance_result(page.locator("body").inner_text(timeout=5_000))
                evidence = ""
                if evidence_dir:
                    evidence = os.path.join(evidence_dir, f"cancel_{reference}.png")
                    page.screenshot(path=evidence, full_page=True)
                return CancelResult(True, True, "Submitted. NuPay says: " + summary[:300],
                                    ok=read_maintenance_reply([summary]), evidence=evidence)
            finally:
                browser.close()
    except Exception as exc:
        state = ("SUBMIT had been pressed, so whether it went through is unknown - check NuPay. "
                 if submit_pressed else "Nothing was submitted. ")
        return CancelResult(False, submit_pressed, f"{state}Cancellation flow failed: {exc}")
    finally:
        PLAYWRIGHT_LOCK.release()
