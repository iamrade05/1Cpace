"""Classify every member by what Itensity, NuPay and this app each say about them.

Read-only. The point is to turn "check all 2,000 members" into a short list of groups
that each mean a different action, so staff fix the right people instead of ringing
everybody who shows arrears.

Groups, in the order they are decided (a member lands in exactly one):

  paying_arrears_clear    paid on NuPay recently; recording that history clears the arrears
  paying_still_owes       paid on NuPay recently; still owes once that history is recorded
  failing_debits          NuPay's latest attempts failed and nothing has succeeded since
  mandate_no_recent_debit has a NuPay mandate but no debit in the window
  no_mandate              Itensity says Debit Order, but NuPay has no mandate for them
  eft_cash                Itensity says EFT/Cash: NuPay cannot confirm payment either way
  sub_account             billed through the main member
  not_in_itensity         in this app, not in Itensity
  inactive_unverified     Itensity shows them Inactive/Unverified: nothing to collect
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

PAYING_ARREARS_CLEAR = "paying_arrears_clear"
PAYING_STILL_OWES = "paying_still_owes"
FAILING_DEBITS = "failing_debits"
MANDATE_NO_RECENT_DEBIT = "mandate_no_recent_debit"
NO_MANDATE = "no_mandate"
EFT_CASH = "eft_cash"
SUB_ACCOUNT = "sub_account"
NOT_IN_ITENSITY = "not_in_itensity"
INACTIVE_UNVERIFIED = "inactive_unverified"

ACTION = {
    PAYING_ARREARS_CLEAR: "Record NuPay payments; arrears clear. Do not call.",
    PAYING_STILL_OWES: "Record NuPay payments, then check what is genuinely still owed.",
    FAILING_DEBITS: "Real collection candidate: debits are failing.",
    MANDATE_NO_RECENT_DEBIT: "Check the mandate (suspended, cancelled or not yet started).",
    NO_MANDATE: "Debit-order member with no NuPay mandate: set one up or confirm how they pay.",
    EFT_CASH: "Verify payments in the Itensity ledger; NuPay cannot confirm.",
    SUB_ACCOUNT: "Billed through the main member: check the main member's record.",
    NOT_IN_ITENSITY: "Confirm whether this person should exist; Itensity does not list them.",
    INACTIVE_UNVERIFIED: "Itensity has them inactive/unverified: confirm before collecting.",
}


def classify(
    *,
    itensity: dict[str, Any] | None,
    mandate_status: str | None,
    last_success: str | None,
    last_failed: str | None,
    arrears_after: float,
    today: date | None = None,
    window_days: int = 45,
) -> str:
    """Decide one member's group. Pure."""
    cutoff = ((today or date.today()) - timedelta(days=window_days)).isoformat()
    paying_recently = bool(last_success and last_success >= cutoff)

    # The latest attempt decides: a failure after the last success means the debit is failing
    # now, however recently they paid before it.
    if last_failed and last_failed >= cutoff and (not last_success or last_failed > last_success):
        return FAILING_DEBITS
    if paying_recently:
        return PAYING_ARREARS_CLEAR if arrears_after <= 0.005 else PAYING_STILL_OWES
    if itensity is None:
        return NOT_IN_ITENSITY
    if str(itensity.get("main_mem") or "").lower().startswith("sub") or itensity.get("Payment") == "Sub Account":
        return SUB_ACCOUNT
    if itensity.get("status") in ("Inactive", "Unverified"):
        return INACTIVE_UNVERIFIED
    pay = str(itensity.get("Payment") or "")
    if pay == "EFT/Cash":
        return EFT_CASH
    if mandate_status:
        return MANDATE_NO_RECENT_DEBIT
    return NO_MANDATE


def mismatch_flags(*, beta_status: str | None, beta_access: str | None, itensity: dict[str, Any] | None, group: str) -> list[str]:
    """Things staff should look at whatever group the member is in."""
    flags = []
    status = str((itensity or {}).get("status") or "")
    beta_status = str(beta_status or "")
    access = str(beta_access or "").lower()
    if itensity and status == "Active" and beta_status in ("Inactive",):
        flags.append("Itensity Active but inactive here")
    if group in (PAYING_ARREARS_CLEAR, PAYING_STILL_OWES) and access in ("blocked", "restricted"):
        flags.append("Paying on NuPay but gym access is blocked")
    if group in (PAYING_ARREARS_CLEAR, PAYING_STILL_OWES) and beta_status == "Inactive":
        flags.append("Paying on NuPay but marked inactive here")
    return flags
