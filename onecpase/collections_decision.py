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
) -> dict:
    """Recommend an offer. Pure: no database, no side effects."""
    from .collections_engine import arrears_discount_policy

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

    # ── Discount: the same tiers and manager threshold the live engine uses ──
    tier = arrears_discount_policy(months)
    percent = tier["discount_percent"]
    manager_required = tier["manager_approval_required"]
    result.update(
        discount_min_percent=tier["min_percent"],
        discount_max_percent=tier["max_percent"],
        discount_range_label=tier["range_label"],
    )
    if tier["manager_discretion"]:
        result["reasons"].append(
            f"{months} months owing: the discount is at manager discretion — full account review first."
        )
    minimum = policy.get("recovery_minimum_amount", DEFAULT_RECOVERY_MINIMUM)
    from_months = int(policy.get("recovery_minimum_from_months", DEFAULT_RECOVERY_FROM_MONTHS))
    recovery_account = months >= from_months

    if months <= 1:
        result["reasons"].append("One month or less owing: no discount is defined; collect the full balance.")

    discount_amount = _money(arrears * percent / 100)
    total = _money(arrears - discount_amount)

    # Recovery floor: a discount may never take the total below the minimum,
    # and we never ask for more than is actually owed.
    below_minimum = False
    if recovery_account:
        floor = min(minimum, arrears)
        if arrears < minimum:
            below_minimum = True
            result["reasons"].append(
                f"Balance R{arrears:,.2f} is below the R{minimum:,.2f} recovery minimum: review before offering."
            )
            result["conditions"].append("Balance below the recovery minimum")
        if total < floor:
            total = _money(floor)
            discount_amount = _money(arrears - total)
            percent = round(100 * discount_amount / arrears, 2) if arrears else 0
            result["reasons"].append(f"Discount limited so the total collected stays at R{floor:,.2f} or more.")

    result.update(discount_percent=percent, discount_amount=discount_amount, total_to_collect=total)
    result["remaining_to_collect"] = max(round(total - upfront_received, 2), 0.0)

    # ── Checklist ──────────────────────────────────────────────────────────
    # Every line says what is needed, who has to act, and what to do next.
    if intention == INTENT_SETTLE:
        check("settlement", "Full settlement payment not received",
              upfront_received + 0.005 >= total, "client", "Record the proof of payment")
    else:
        if upfront_offered > 0:
            # Upfront is whatever the client can offer; it just has to be verified.
            check("upfront", "Upfront payment not verified",
                  upfront_received + 0.005 >= upfront_offered, "receptionist", "Record the proof of payment")
        if result["remaining_to_collect"] > 0:
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
        check("bank_on_file", "No bank statement on file", False, "receptionist",
              "Ask the client for a bank statement", blocking=False)

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
