"""Promise-to-Pay decision calculation.

One pure function that turns a client's verified arrears and the conditions of
an offer into a recommendation. Discount tiers and manager thresholds come from
the shared arrears policy in collections_engine, so this panel, payment
arrangements and Queries cannot disagree. A promise never applies a discount or
settles an account; it only records what is on offer and what is still missing.

The upfront payment is whatever the client can actually offer on the call -
there is no required percentage. It is counted toward the total, and it only
counts once it has been received and verified.
"""
from __future__ import annotations

from datetime import datetime, timedelta

POLICY_VERSION = "2026-10-ranges-1"

DECISION_ELIGIBLE = "eligible"
DECISION_CONDITIONS = "conditions_outstanding"
DECISION_NO_DISCOUNT = "no_discount"
DECISION_MANAGER = "manager_review"
DECISION_NO_BALANCE = "no_balance"

DECISION_LABELS = {
    DECISION_ELIGIBLE: "Eligible offer",
    DECISION_CONDITIONS: "Conditions outstanding",
    DECISION_NO_DISCOUNT: "No discount available",
    DECISION_MANAGER: "Manager review required",
    DECISION_NO_BALANCE: "Nothing owing",
}

INTENT_SETTLE = "settle"
INTENT_INSTALMENTS = "instalments"
MAX_REMAIN_PLUS_UPFRONT = 2000
STALE_PAYMENT_MONTHS = 3


def stale_cutoff(now=None):
    from datetime import date, datetime, timedelta

    today = now.date() if isinstance(now, datetime) else (now or date.today())
    return today - timedelta(days=30 * STALE_PAYMENT_MONTHS)


def payment_is_stale(last_payment_date, now=None) -> bool:
    """True when the last payment is older than the cut-off. Unknown dates are not stale."""
    if not last_payment_date:
        return False
    return str(last_payment_date)[:10] < stale_cutoff(now).isoformat()

CONFIRMED_DEBICHECK = {"approved", "authorised", "authorized", "accepted", "active", "completed"}
FAILED_DEBICHECK = {"failed", "rejected", "declined", "cancelled", "canceled", "expired"}

# A DebiCheck approval request is only valid for one day. After that the client
# can no longer approve it and the mandate has to be sent again.
DEBICHECK_AUTH_VALID = timedelta(days=1)
AWAITING_DEBICHECK = {"submitted", "reviewing", "processing", "pending_authorisation", "pending authorisation"}


def _as_datetime(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def authorisation_lapsed(status, submitted_at, now=None) -> bool:
    """True when a mandate has been waiting on the client for more than a day."""
    if str(status or "").strip().lower() not in AWAITING_DEBICHECK:
        return False
    sent = _as_datetime(submitted_at)
    return sent is not None and (_as_datetime(now) or datetime.now()) - sent > DEBICHECK_AUTH_VALID


# Recovery accounts (long-term arrears) are never discounted below this total.
DEFAULT_RECOVERY_MINIMUM = 1500.0
DEFAULT_RECOVERY_FROM_MONTHS = 7   # "more than six months"; set to 6 to include six


def _number(value):
    try:
        text = str(value).strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def load_policy(db) -> dict:
    """Recovery-minimum settings from collection_rules, with defaults."""
    from .collections_engine import get_collection_rule

    minimum = _number(get_collection_rule(db, "recovery_minimum_amount", ""))
    from_months = _number(get_collection_rule(db, "recovery_minimum_from_months", ""))
    return {
        "recovery_minimum_amount": DEFAULT_RECOVERY_MINIMUM if minimum is None else minimum,
        "recovery_minimum_from_months": int(from_months or DEFAULT_RECOVERY_FROM_MONTHS),
    }


def _money(value) -> float:
    return round(float(value or 0), 2)


def decide_offer(
    *,
    arrears,
    months_owing,
    debicheck_status=None,
    debicheck_submitted_at=None,
    now=None,
    upfront_offered=0,
    upfront_received=0,
    intention=INTENT_INSTALMENTS,
    returning_member=False,
    bank_statement_status=None,
    flags=None,
    policy=None,
    monthly_affordable=0,
    normal_instalment=0,
    mandate_state=None,
    last_payment_date=None,
) -> dict:
    """Recommend an offer. Pure: no database, no side effects."""
    import math

    from .collections_engine import MIN_MONTHS_FOR_DISCOUNT, category_offer

    policy = policy or {}
    flags = flags or {}
    arrears = _money(arrears)
    months = max(int(months_owing or 0), 0)
    upfront_offered = max(_money(upfront_offered), 0.0)
    upfront_received = max(_money(upfront_received), 0.0)
    status = str(debicheck_status or "").strip().lower()
    debicheck_confirmed = status in CONFIRMED_DEBICHECK

    result = {
        "policy_version": POLICY_VERSION,
        "months_owing": months,
        "original_arrears": arrears,
        "discount_percent": 0,
        "discount_amount": 0.0,
        "total_to_collect": arrears,
        "upfront_offered": upfront_offered,
        "upfront_received": upfront_received,
        "remaining_to_collect": max(round(arrears - upfront_received, 2), 0.0),
        "decision": DECISION_NO_DISCOUNT,
        "conditions": [],
        "checklist": [],
        "reasons": [],
        "debicheck_confirmed": debicheck_confirmed,
    }

    def check(key, label, met, owner, action=None, blocking=True):
        """One line of the checklist. Open blocking items also feed `conditions`."""
        result["checklist"].append(
            {"key": key, "label": label, "met": bool(met), "owner": owner, "action": action, "blocking": blocking}
        )
        if not met and blocking and label not in result["conditions"]:
            result["conditions"].append(label)

    def finish(decision):
        result["decision"] = decision
        result["decision_label"] = DECISION_LABELS[decision]
        return result

    if arrears <= 0:
        result["reasons"].append("No verified balance is owing.")
        return finish(DECISION_NO_BALANCE)

    # ── Discount: the arrears category, the upfront paid and months owing ──
    # The discount applies to what is left after the upfront. It is only a
    # proposal until the DebiCheck mandate is approved.
    # A full settlement is paid in one go, so it is priced at the no-upfront rate.
    counted_upfront = 0 if intention == INTENT_SETTLE else max(upfront_offered, upfront_received)
    offer = category_offer(arrears, counted_upfront, months)
    percent = float(offer["discount_percent"])
    discount_amount = float(offer["discount_amount"])
    ptp_balance = float(offer["ptp_balance"])
    upfront_counted = float(offer["upfront"])
    total = _money(upfront_counted + ptp_balance)
    result.update(
        category=offer["category"], rule_version=offer["rule_version"],
        ptp_balance=ptp_balance, half_upfront=offer["half_upfront"],
    )
    if months < MIN_MONTHS_FOR_DISCOUNT:
        result["reasons"].append(
            f"{months} month(s) owing: a discount needs {MIN_MONTHS_FOR_DISCOUNT} or more months; collect the full balance."
        )
    else:
        result["reasons"].append(
            f"Category {offer['category']}: {percent:g}% off what is left after the upfront"
            + (" (50% or more paid upfront)." if offer["half_upfront"] else " (under 50% upfront).")
        )
    recovery_account = False
    below_minimum = False
    manager_required = False
    arrangement_conditions = []
    result["intention"] = intention

    # ── The arrangement: what the client can afford each month ──
    # The affordable figure is everything debited monthly, so the normal
    # instalment comes off it and the rest pays down the PTP balance.
    normal_instalment = max(_money(normal_instalment), 0.0)
    affordable = max(_money(monthly_affordable), 0.0)
    result["normal_instalment"] = normal_instalment
    result["mandate_state"] = mandate_state or "unknown"
    # An active mandate means only the arrangement mandate is new; without one
    # the normal instalment needs a mandate as well.
    mandates_to_load = []
    if mandate_state != "active":
        mandates_to_load.append({"purpose": "Normal instalment", "amount": normal_instalment})
    if ptp_balance > 0:
        mandates_to_load.append({"purpose": "Arrangement (PTP)", "amount": ptp_balance})
    arrangement = None
    if intention != INTENT_SETTLE and ptp_balance > 0:
        monthly_arrears = round(affordable - normal_instalment, 2)
        if affordable <= 0:
            arrangement_conditions.append(("affordable", "Enter what the client can pay above the normal instalment",
                                           "receptionist", "Ask the client what they can afford"))
        elif monthly_arrears <= 0:
            arrangement_conditions.append(("affordable", "The monthly amount does not cover the normal instalment plus an arrears payment",
                                           "client", "Agree a higher monthly amount"))
        else:
            payments = int(math.ceil(ptp_balance / monthly_arrears - 1e-9))
            regular = _money(math.ceil(ptp_balance / payments * 100 - 1e-9) / 100)
            arrangement = {
                "monthly_arrears": regular, "payments": payments, "normal_instalment": normal_instalment,
                "total_monthly": _money(regular + normal_instalment),
                "final_payment": _money(ptp_balance - regular * (payments - 1)),
            }
            mandates_to_load[-1:] = [
                {**m, "amount": regular} if m["purpose"] == "Arrangement (PTP)" else m
                for m in mandates_to_load[-1:]
            ]
    result["arrangement"] = arrangement
    result["mandates_to_load"] = mandates_to_load

    result.update(discount_percent=percent, discount_amount=discount_amount, total_to_collect=total)
    result["remaining_to_collect"] = max(round(total - upfront_received, 2), 0.0)

    # ── Checklist ──────────────────────────────────────────────────────────
    # Every line says what is needed, who has to act, and what to do next.
    if 'statement_months_ready' in flags and (intention != INTENT_SETTLE or percent > 0):
        check('statement_months', 'Two months of bank statements analysed',
              flags['statement_months_ready'], 'client', 'Send two months of bank statements')
        if intention == INTENT_SETTLE and percent > 0:
            check('discount_debicheck', 'Discount requires approved DebiCheck',
                  debicheck_confirmed, 'client', 'Authorise the required DebiCheck mandate')
    if intention == INTENT_SETTLE:
        check("settlement", "Full settlement payment not received",
              upfront_received + 0.005 >= total, "client", "Record the proof of payment")
    else:
        if upfront_offered > 0:
            # Upfront is whatever the client can offer; it just has to be verified.
            paid = upfront_received + 0.005 >= upfront_offered
            check("upfront", "Upfront payment verified" if paid else "Upfront payment not verified",
                  paid, "receptionist", "Record the proof of payment")
        for key, label, owner, action in arrangement_conditions:
            check(key, label, False, owner, action)
        if mandate_state == "unknown":
            check("nupay_check", "Check NuPay for an existing mandate", False, "receptionist",
                  "Run the NuPay check")
        if payment_is_stale(last_payment_date, now):
            # A debit order set up after the cut-off counts as the new one.
            renewed = bool(debicheck_submitted_at) and str(debicheck_submitted_at)[:10] > stale_cutoff(now).isoformat()
            check("stale_payment", f"Last payment was over {STALE_PAYMENT_MONTHS} months ago - run a new debit order",
                  renewed, "receptionist", "Set up a new DebiCheck")
        # No DebiCheck, no PTP: it is needed even when nothing is left to
        # collect, because the normal membership mandate must be approved too.
        if debicheck_confirmed:
            check("debicheck", "DebiCheck confirmed", True, "client")
        elif status in FAILED_DEBICHECK:
            check("debicheck", "DebiCheck failed - a new mandate is needed", False, "receptionist",
                  "Set up a new DebiCheck")
        elif authorisation_lapsed(status, debicheck_submitted_at, now):
            check("debicheck", "DebiCheck approval expired after 1 day - send it again", False,
                  "receptionist", "Set up a new DebiCheck")
        elif not status:
            check("debicheck", "DebiCheck not set up", False, "receptionist", "Set up DebiCheck")
        elif status == "pending" and not debicheck_submitted_at:
            check("debicheck", "DebiCheck saved but not yet sent to the bank", False, "receptionist",
                  "Send the mandate to the bank")
        else:
            check("debicheck", "DebiCheck awaiting authorisation", False, "client",
                  "Ask the client to approve it in their banking app within 1 day")

    if returning_member:
        check("returning_debicheck", "Returning member needs a confirmed DebiCheck", debicheck_confirmed,
              "receptionist", "Set up DebiCheck")
        check("bank_statement", "Bank statement review outstanding",
              str(bank_statement_status or "").lower() in ("reviewed", "approved"),
              "manager", "Upload the bank statement for review")

    # What the account already shows, surfaced before the client agrees to anything.
    if flags.get("open_dispute"):
        check("dispute", "Dispute under review - finalise the offer once it is resolved", False, "manager",
              "Wait for the dispute outcome")
    if flags.get("unverified_payment_claim"):
        check("paid_claim", "Client's payment claim not yet verified", False, "receptionist",
              "Verify the payment before working out what is owed")
    manager_flags = []
    if flags.get("broken_ptp"):
        manager_flags.append("a previous promise to pay was broken")
        check("broken_ptp", "Previous promise to pay was broken", False, "manager", None, blocking=False)
    if flags.get("legal_referral"):
        manager_flags.append("the account is in the legal pathway")
        check("legal", "Account is in the legal pathway", False, "manager", None, blocking=False)
    if intention != INTENT_SETTLE and flags.get("bank_statement_on_file") is False:
        check("bank_on_file", "Bank statement required: none on file", False, "client",
              "Ask the client to send a bank statement", blocking=False)
    if intention != INTENT_SETTLE and ptp_balance > 0 and ptp_balance + max(upfront_offered, upfront_received) > MAX_REMAIN_PLUS_UPFRONT + 0.005:
        check("cap", f"Remaining balance plus upfront is above R{MAX_REMAIN_PLUS_UPFRONT:,.0f}", False,
              "manager", "Get manager approval")
    if arrangement and arrangement["payments"] > 12:
        manager_flags.append("the term is over 12 months")
        check("long_term", "Term over 12 months needs a manager", False, "manager", None, blocking=False)

    # ── Decision ───────────────────────────────────────────────────────────
    for note in manager_flags:
        result["reasons"].append(f"Manager review: {note}.")
    if percent == 0 and not recovery_account:
        return finish(DECISION_NO_DISCOUNT)
    if manager_required or below_minimum or manager_flags:
        if manager_required:
            result["reasons"].append(f"{months} months owing: a manager must approve this discount.")
        return finish(DECISION_MANAGER)
    return finish(DECISION_CONDITIONS if result["conditions"] else DECISION_ELIGIBLE)
