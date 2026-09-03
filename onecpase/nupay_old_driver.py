"""
NuPay debit order mandate push — old portal (www.nupayments.co.za).

Ported verbatim from the reference 1Cpace app's nupay_push.py (Phase 2 of the
1Cpase rewrite) so this app owns the driver natively instead of dynamically
importing it from a sibling repo at runtime. The scraping logic itself
(form_build_id extraction, the /access_to cross-domain handoff, the exact
naedoRegistration rsargs[] order) is left untouched — it was reverse-engineered
against NuPay's real pages and any "cleanup" risks silently breaking it.

Login: https://www.nupayments.co.za/user/login
DebiCheck: https://debicheck.nupayments.co.za/

Credentials via .env:
    NUPAY_USERNAME
    NUPAY_PASSWORD
    NUPAY_MANDATE_URL  - the DebiCheck page opened after the normal
                         NuPay access flow. This can be
                         /debicheck-tranupload.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote, urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as exc:
    raise ImportError(
        f"Missing dependency: {exc.name}. Run: pip install requests beautifulsoup4"
    ) from exc

LOGIN_BASE = "https://www.nupayments.co.za"
LOGIN_PATH = "/user/login?from_welcome=1&skip_select=1"
DEBICHECK_BASE = "https://debicheck.nupayments.co.za"
MANDATE_URL = os.getenv("NUPAY_MANDATE_URL", DEBICHECK_BASE)

logger = logging.getLogger("nupay_push")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT_SECONDS = int(os.getenv("NUPAY_REQUEST_TIMEOUT_SECONDS", "15") or 15)
SUBMIT_TIMEOUT_SECONDS = int(os.getenv("NUPAY_SUBMIT_TIMEOUT_SECONDS", "25") or 25)


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


class NuPayConfigError(ValueError):
    """Raised when the configured NuPay capture page is not usable."""


def _extract_form_build_id(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("input", {"name": "form_build_id", "type": "hidden"})
    if tag and tag.get("value"):
        return tag["value"].strip()
    match = re.search(
        r'name=["\']form_build_id["\'][^>]+value=["\']([^"\']+)["\']',
        html,
    )
    if match:
        return match.group(1)
    raise ValueError("form_build_id not found - NuPay page structure may have changed")


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Referer": f"{LOGIN_BASE}{LOGIN_PATH}",
        }
    )
    for name, value in (("perf_dv6Tr4n", "1"), ("lhc_per", "{}")):
        session.cookies.set(name, value, domain=".nupayments.co.za", path="/")
    return session


def _resolve_mandate_url(raw_url: str) -> str:
    candidate = str(raw_url or "").strip()
    if not candidate:
        raise NuPayConfigError(
            "NUPAY_MANDATE_URL is empty. Use the NuPay DebiCheck capture page URL."
        )

    if candidate.startswith("/"):
        candidate = urljoin(f"{DEBICHECK_BASE}/", candidate.lstrip("/"))

    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        raise NuPayConfigError(
            "NUPAY_MANDATE_URL must be a full https URL or a /path on debicheck.nupayments.co.za."
        )

    return candidate


def _build_access_url(target_url: str) -> str:
    encoded = base64.b64encode(target_url.encode("utf-8")).decode("ascii")
    return f"{LOGIN_BASE}/access_to?url={quote(encoded)}"


def _find_debicheck_nav_link(session: requests.Session) -> str | None:
    """
    After login, scrape the NuPay home/dashboard page for a link that leads to
    debicheck.nupayments.co.za.  NuPay uses a redirect from their main site to
    establish the cross-domain session — we follow that instead of hard-coding
    the /access_to mechanism (which returns 403 for some accounts).
    """
    for path in ("/home", "/"):
        try:
            page = session.get(f"{LOGIN_BASE}{path}", timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
            if page.status_code != 200:
                continue
            soup = BeautifulSoup(page.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = str(a["href"])
                if "debicheck" in href.lower():
                    full = href if href.startswith("http") else urljoin(LOGIN_BASE + "/", href.lstrip("/"))
                    logger.debug("Found DebiCheck nav link: %s", full)
                    return full
        except requests.RequestException:
            pass
    return None


def _enter_debicheck(
    session: requests.Session, target_url: str = DEBICHECK_BASE
) -> requests.Response:
    """
    Establish a session on debicheck.nupayments.co.za.

    Strategy (tried in order):
    1. Scrape the NuPay home page for a real DebiCheck navigation link and follow it.
    2. Fall back to the /access_to?url=<base64> mechanism.
    3. Last resort: hit the target URL directly (works if cookies are shared).
    """
    # 1. Navigate via the dashboard link
    link = _find_debicheck_nav_link(session)
    if link:
        try:
            resp = session.get(link, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
            if resp.status_code == 200:
                logger.debug("DebiCheck entry via nav link: %s -> %s", link, resp.url)
                return resp
        except requests.RequestException as exc:
            logger.debug("Nav link failed: %s", exc)

    # 2. /access_to handoff
    try:
        access_url = _build_access_url(target_url)
        session.headers["Referer"] = f"{LOGIN_BASE}/home"
        resp = session.get(access_url, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
        resp.raise_for_status()
        logger.debug("DebiCheck handoff via /access_to: %s -> %s", access_url, resp.url)
        return resp
    except requests.RequestException as exc:
        logger.warning("DebiCheck /access_to handoff failed: %s", exc)

    # 3. Direct
    resp = session.get(target_url, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
    resp.raise_for_status()
    return resp


def _fetch_mandate_page(
    session: requests.Session, mandate_url: str
) -> requests.Response:
    try:
        _enter_debicheck(session, mandate_url)
    except requests.RequestException as exc:
        logger.warning("DebiCheck entry failed before mandate fetch: %s", exc)

    session.headers["Referer"] = f"{DEBICHECK_BASE}/"
    response = session.get(mandate_url, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
    if response.status_code == 403:
        raise NuPayConfigError(
            "NuPay returned 403 Forbidden while opening the configured mandate page. "
            "Check that the DebiCheck access handoff succeeded and that NUPAY_MANDATE_URL "
            "matches the page opened in your browser after DebiCheck > Continue."
        )
    response.raise_for_status()
    return response


def _candidate_mandates_urls(session: requests.Session) -> list[str]:
    configured = os.getenv("NUPAY_MANDATES_URL", "").strip()
    urls: list[str] = []
    if configured:
        urls.append(_resolve_mandate_url(configured))

    try:
        entry = _enter_debicheck(session, DEBICHECK_BASE)
        soup = BeautifulSoup(entry.text, "html.parser")
        for link in soup.find_all("a", href=True):
            label = f"{link.get_text(' ', strip=True)} {link['href']}".lower()
            if "mandate" in label or "naedo" in label or "debi" in label:
                urls.append(urljoin(entry.url, str(link["href"])))
    except requests.RequestException as exc:
        logger.warning("Could not discover NuPay mandate list links: %s", exc)

    urls.extend(
        [
            f"{DEBICHECK_BASE}/debicheck-mandates",
            f"{DEBICHECK_BASE}/mandates",
            f"{DEBICHECK_BASE}/mandate",
            DEBICHECK_BASE,
        ]
    )

    seen: set[str] = set()
    unique_urls: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            unique_urls.append(url)
    return unique_urls


def _normalize_mandate_status(text: str) -> str | None:
    value = re.sub(r"\s+", " ", text or "").strip().lower()
    if not value:
        return None

    success_terms = (
        "successful",
        "success",
        "verified",
        "active",
        "accepted",
        "approved",
        "registered",
        "authenticated",
        "completed",
    )
    rejected_terms = (
        "rejected",
        "declined",
        "failed",
        "unsuccessful",
        "cancelled",
        "canceled",
        "expired",
        "abandoned",
        "error",
    )
    pending_terms = (
        "pending",
        "awaiting",
        "waiting for authentication",
        "processing",
        "initiated",
        "sent",
        "loaded",
    )

    if any(term in value for term in rejected_terms):
        return "rejected"
    if any(term in value for term in success_terms):
        return "active"
    if any(term in value for term in pending_terms):
        return "pending"
    return None


def _find_matching_mandate_in_html(html: str, mandate: dict[str, Any]) -> MandateStatusResult:
    soup = BeautifulSoup(html, "html.parser")
    ref1 = str(mandate.get("client_ref1") or "").strip()
    ref2 = str(mandate.get("client_ref2") or "").strip()
    account_number = str(mandate.get("account_number") or "").strip()
    needles = [value.lower() for value in (ref1, ref2, account_number) if value]

    if not needles:
        return MandateStatusResult(False, "unknown", "No client reference available for NuPay lookup.")

    candidates: list[str] = []
    for row in soup.find_all("tr"):
        text = row.get_text(" ", strip=True)
        lower = text.lower()
        if any(needle in lower for needle in needles):
            candidates.append(text)

    if not candidates:
        page_text = soup.get_text(" ", strip=True)
        lower = page_text.lower()
        if any(needle in lower for needle in needles):
            candidates.append(page_text[:1200])

    if not candidates:
        return MandateStatusResult(False, "unknown", "Mandate not found on NuPay mandates page.")

    best = candidates[0]
    if ref1 and ref2:
        both_refs = [c for c in candidates if ref1.lower() in c.lower() and ref2.lower() in c.lower()]
        if both_refs:
            best = both_refs[0]

    status = _normalize_mandate_status(best) or "unknown"
    return MandateStatusResult(
        True,
        status,
        f"NuPay mandate status: {status}",
        best[:800],
        html[:3000],
    )


def refresh_mandate_status(mandate: dict[str, Any]) -> MandateStatusResult:
    username = os.getenv("NUPAY_USERNAME", "").strip()
    password = os.getenv("NUPAY_PASSWORD", "").strip()
    if not username or not password:
        return MandateStatusResult(False, "unknown", "NUPAY_USERNAME and NUPAY_PASSWORD must be set in .env")

    session = _build_session()
    try:
        if not _login(session, username, password):
            return MandateStatusResult(False, "unknown", "NuPay login failed - check NUPAY_USERNAME / NUPAY_PASSWORD")

        last_message = "NuPay mandate status could not be found."
        for url in _candidate_mandates_urls(session):
            try:
                session.headers["Referer"] = f"{DEBICHECK_BASE}/"
                response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
                response.raise_for_status()
            except requests.RequestException as exc:
                last_message = f"Could not open NuPay mandates page {url}: {exc}"
                logger.debug(last_message)
                continue

            result = _find_matching_mandate_in_html(response.text, mandate)
            if result.found:
                result.message = f"{result.message} ({url})"
                return result
            last_message = result.message

        return MandateStatusResult(False, "unknown", last_message)
    except NuPayConfigError as exc:
        logger.warning("NuPay status config error: %s", exc)
        return MandateStatusResult(False, "unknown", str(exc))
    except requests.RequestException as exc:
        logger.exception("Network error refreshing NuPay mandate status")
        return MandateStatusResult(False, "unknown", f"Network error: {exc}")
    finally:
        session.close()


def wait_for_mandate_status(
    mandate: dict[str, Any],
    interval_seconds: int | None = None,
    timeout_seconds: int | None = None,
) -> MandateStatusResult:
    interval = interval_seconds or int(os.getenv("NUPAY_STATUS_INTERVAL_SECONDS", "15") or 15)
    timeout = timeout_seconds or int(os.getenv("NUPAY_STATUS_TIMEOUT_SECONDS", "120") or 120)
    deadline = time.monotonic() + max(timeout, interval)
    last_result = MandateStatusResult(False, "unknown", "NuPay mandate status has not been checked yet.")

    while True:
        last_result = refresh_mandate_status(mandate)
        if last_result.status in {"active", "rejected"}:
            return last_result
        if time.monotonic() + interval > deadline:
            if last_result.status == "pending":
                return MandateStatusResult(
                    last_result.found,
                    "pending",
                    "NuPay mandate is still pending after the refresh window.",
                    last_result.details,
                    last_result.raw_response,
                )
            return last_result
        time.sleep(interval)


def _login(session: requests.Session, username: str, password: str) -> bool:
    login_url = f"{LOGIN_BASE}{LOGIN_PATH}"

    response = session.get(login_url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()

    form_build_id = _extract_form_build_id(response.text)

    payload = {
        "name": username,
        "pass": password,
        "form_build_id": form_build_id,
        "form_id": "user_login",
        "terms": "1",       # required T&C checkbox
        "home_page": "1",   # optional but present in the form
        "op": "Log in",
    }
    session.headers["Referer"] = login_url
    response = session.post(
        login_url,
        data=payload,
        allow_redirects=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": LOGIN_BASE,
        },
    )

    current_url = response.url.lower()

    # Drupal login: success = server redirected us away from the login page.
    # A 200 that stays on /user/login means the form was rejected (bad credentials).
    redirected = bool(response.history)
    still_on_login = "/user/login" in current_url

    if still_on_login and not redirected:
        # Log the first 600 chars so we can see the actual error message
        snippet = response.text[:600].replace("\n", " ")
        logger.error("NuPay login failed (stayed on login page). Page snippet: %s", snippet)
        return False

    # Sanity-check: Drupal sets a SESS* cookie on successful login
    drupal_session = next(
        (c for c in session.cookies if c.name.upper().startswith("SESS")), None
    )
    if drupal_session:
        logger.info("NuPay login OK — session cookie: %s", drupal_session.name)
    else:
        logger.warning(
            "NuPay login: redirected to %s but no SESS* cookie found — "
            "session may not be established",
            response.url,
        )

    logger.info("NuPay login OK")

    try:
        handoff = _enter_debicheck(session, MANDATE_URL)
        logger.debug("DebiCheck entry status after login: %s -> %s", handoff.status_code, handoff.url)
    except requests.RequestException as exc:
        logger.warning("DebiCheck entry after login failed (may still work): %s", exc)

    return True


def _dump_form(html: str) -> str:
    """Return a readable summary of all inputs in the page, for debugging."""
    soup = BeautifulSoup(html, "html.parser")
    lines: list[str] = []
    for form in soup.find_all("form"):
        action = form.get("action", "(no action)")
        method = form.get("method", "get").upper()
        lines.append(f"FORM action={action} method={method}")
        for inp in form.find_all(["input", "select", "textarea"]):
            tag = inp.name
            name = inp.get("name", "(no name)")
            typ = inp.get("type", tag)
            value = inp.get("value", "")
            lines.append(f"  [{typ}] name={name!r} value={value!r}")
    return "\n".join(lines) if lines else "(no forms found)"


def inspect_mandate_form() -> str:
    """
    Log in and return a dump of every input on the NuPay mandate capture page.
    Call this once to discover the real field names, then update push_mandate().
    """
    username = os.getenv("NUPAY_USERNAME", "").strip()
    password = os.getenv("NUPAY_PASSWORD", "").strip()

    try:
        url = _resolve_mandate_url(os.getenv("NUPAY_MANDATE_URL", MANDATE_URL))
    except NuPayConfigError as exc:
        return f"CONFIG ERROR: {exc}"

    session = _build_session()
    try:
        if not _login(session, username, password):
            return "LOGIN FAILED"
        response = _fetch_mandate_page(session, url)
        return _dump_form(response.text)
    except Exception as exc:
        return f"ERROR: {exc}"
    finally:
        session.close()


# Branch code → NuPay bank_id (from the DebiCheck page JS ddlBankNames handler)
_BRANCH_TO_BANK_ID: dict[str, str] = {
    "051001": "1",   # Standard Bank
    "198765": "2",   # Nedbank
    "250655": "3",   # FNB
    "410506": "6",   # Athens
    "430000": "7",   # Ubank / African Bank
    "450105": "9",   # Mercantile
    "470010": "10",  # Capitec
    "632005": "16",  # ABSA
    "462005": "44",  # Bidvest
    "589000": "55",  # Finbond
    "678910": "61",  # Tyme Bank
    "679000": "63",  # Discovery Bank
    "352000": "67",  # Old Mutual
}


def _required(value: Any) -> str:
    return str(value or "").strip()


def _validate_mandate(mandate: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors before NuPay is contacted."""
    errors: list[str] = []
    required_fields = {
        "merchant_id": "Merchant",
        "account_name": "Account name",
        "account_type": "Account type",
        "account_number": "Account number",
        "branch_code": "Branch code",
        "frequency": "Frequency",
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
        amount_cents = (
            int(mandate.get("instalment_rands", 0) or 0) * 100
            + int(mandate.get("instalment_cents", 0) or 0)
        )
        if amount_cents <= 0:
            errors.append("Instalment amount must be greater than zero")
    except (TypeError, ValueError):
        errors.append("Instalment amount is invalid")

    return errors


def _get_collection_day(frequency: str, action_date: str) -> str:
    """
    Mirror the JS getCollectionDay() logic.
    Monthly (3): day-of-month from the action date.
    All others: empty string (NuPay ignores it).
    """
    if frequency == "3" and action_date:
        try:
            dt = datetime.strptime(action_date, "%Y-%m-%d")
            return str(dt.day)
        except ValueError:
            pass
    return ""


def push_mandate(mandate: dict[str, Any]) -> PushResult:
    """
    Push a single DebiCheck mandate to NuPay via the jqSajax AJAX endpoint
    (naedoRegistration for cell/TT1-delayed auth type).
    """
    username = os.getenv("NUPAY_USERNAME", "").strip()
    password = os.getenv("NUPAY_PASSWORD", "").strip()

    try:
        url = _resolve_mandate_url(os.getenv("NUPAY_MANDATE_URL", MANDATE_URL))
    except NuPayConfigError as exc:
        return PushResult(False, str(exc))

    if not username or not password:
        return PushResult(False, "NUPAY_USERNAME and NUPAY_PASSWORD must be set in .env")

    validation_errors = _validate_mandate(mandate)
    if validation_errors:
        return PushResult(False, "; ".join(validation_errors))

    session = _build_session()
    try:
        if not _login(session, username, password):
            return PushResult(False, "NuPay login failed - check NUPAY_USERNAME / NUPAY_PASSWORD")

        # Establish session on debicheck subdomain
        _fetch_mandate_page(session, url)

        # ── Build naedoRegistration args ────────────────────────────────────
        auth_type   = str(mandate.get("auth_type", "cell"))
        merchant_id = str(mandate.get("merchant_id", "000025500014054"))
        branch_code = str(mandate.get("branch_code", ""))
        bank_id     = _BRANCH_TO_BANK_ID.get(branch_code, "")
        frequency   = str(mandate.get("frequency", "3"))
        action_date = str(mandate.get("submit_date", ""))
        freq_set    = _get_collection_day(frequency, action_date)

        # Amount: rands and cents concatenated as a string (NuPay format)
        rands = str(int(mandate.get("instalment_rands", 0))).lstrip("0") or "0"
        cents = str(int(mandate.get("instalment_cents", 0))).zfill(2)
        value = rands + cents

        # ID details
        id_type = str(mandate.get("id_type", "0"))
        if id_type == "0":
            client_id_no = str(mandate.get("id_number", "") or "")
        elif id_type == "1":
            client_id_no = str(mandate.get("passport", "") or "")
        else:
            client_id_no = str(mandate.get("juristic_account", "") or "")

        # Args match $.x_naedoRegistration call order in registerNaedoContract():
        # cardAcceptor, value, accType, frequency, accName, accNum, branchcode,
        # bank_id, "", noInstal, actionDate, dateAdjustRule, tracking,
        # cref1, cref2, clientIdNo, id_type, authType, FrequencySet
        rsargs = [
            merchant_id,
            value,
            str(mandate.get("account_type", "1")),
            frequency,
            str(mandate.get("account_name", "")),
            str(mandate.get("account_number", "")),
            branch_code,
            bank_id,
            "",                                          # 9th param (empty)
            str(mandate.get("no_installments", "1")),
            action_date,
            "",                                          # dateAdjustRule
            str(mandate.get("tracking", "14")),
            str(mandate.get("client_ref1", "")),
            str(mandate.get("client_ref2", "")),
            client_id_no,
            id_type,
            auth_type,
            freq_set,
        ]

        post_data = [
            ("rs",     "naedoRegistration"),
            ("rst",    ""),
            ("rsrnd",  str(int(time.time() * 1000))),
            ("rsobj",  ""),
        ] + [("rsargs[]", a) for a in rsargs]

        logger.info(
            "Submitting naedoRegistration — merchant=%s authType=%s amount=%s",
            merchant_id, auth_type, value,
        )
        logger.debug("rsargs: %s", rsargs)

        post_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": DEBICHECK_BASE,
            "Referer": url,
        }

        # The debicheck server drops idle connections; retry once after re-fetching
        # the page to re-establish the SSL session.
        submit_response = None
        for attempt in (1, 2):
            try:
                submit_response = session.post(
                    url,
                    data=post_data,
                    allow_redirects=True,
                    timeout=SUBMIT_TIMEOUT_SECONDS,
                    headers=post_headers,
                )
                break
            except requests.ConnectionError as exc:
                if attempt == 2:
                    raise
                logger.warning("Connection reset on attempt %d, re-establishing: %s", attempt, exc)
                _fetch_mandate_page(session, url)

        raw = submit_response.text.strip()
        submit_response.raise_for_status()
        logger.debug("naedoRegistration raw response (%d chars): %s", len(raw), raw[:500])

        # jqSajax wraps the PHP return value as a JS eval-able string literal,
        # e.g.: "\"<h3>Success.<\/h3>...\""  — strip the outer JS string quotes.
        clean = raw
        if clean.startswith('"') and clean.endswith('"'):
            try:
                import json
                clean = json.loads(clean)   # un-escape the inner string
            except Exception:
                clean = clean[1:-1]

        # Now clean is the raw HTML string from PHP naedoRegistration.
        soup = BeautifulSoup(clean, "html.parser")
        msg = soup.get_text(" ", strip=True)

        clean_lower = clean.lower()
        if "success" in clean_lower and "failed" not in clean_lower:
            logger.info("NuPay registration success: %s", msg[:200])
            return PushResult(True, msg[:400], raw[:1000])

        if "waiting for authentication" in clean_lower or "contract reference:" in clean_lower:
            logger.info("NuPay registration submitted and awaiting authentication: %s", msg[:200])
            return PushResult(True, msg[:400], raw[:1000])

        if "failed" in clean_lower or "error" in clean_lower:
            logger.warning("NuPay registration failed: %s", msg[:200])
            return PushResult(False, msg[:400], raw[:3000])

        logger.warning("NuPay response ambiguous (%d chars): %s", len(raw), msg[:300])
        return PushResult(False, f"NuPay response unclear: {msg[:400]}", raw[:3000])

    except NuPayConfigError as exc:
        logger.warning("NuPay config error: %s", exc)
        return PushResult(False, str(exc))
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        message = f"NuPay returned HTTP {status}: {exc}"
        logger.exception("HTTP error pushing to NuPay")
        return PushResult(False, message)
    except requests.RequestException as exc:
        logger.exception("Network error pushing to NuPay")
        return PushResult(False, f"Network error or timeout contacting NuPay: {exc}")
    finally:
        session.close()


def inspect_page() -> str:
    """Backward-compatible name used by the Flask debug route."""
    return inspect_mandate_form()
