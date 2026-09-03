"""1Cpase DebiCheck mandate persistence and NuPay hand-off."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from flask import current_app

from . import nupay_new_driver


ACCOUNT_TYPE_CODES = {
    "Cheque/Current": "1",
    "Savings": "2",
    "Transmission": "3",
    "1": "1",
    "2": "2",
    "3": "3",
}


def itensity_client_ref_number(itensity_ref: str | None) -> str:
    """Strip Itensity's letter prefix and leading zeros from a member's
    Itensity ref, e.g. 'EHF002160994' -> '2160994'. Plain numeric refs
    ('2103268') pass through unchanged — that's the format client_ref1 has
    always used, matched against NuPay's own client_reference field."""
    return re.sub(r"^\D*0*", "", str(itensity_ref or "").strip())


def mandate_from_member(member_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """Build the NuPay-shaped mandate from the Payment-tab submission."""
    amount = round(float(data.get("monthly_installment") or 0), 2)
    rands = int(amount)
    cents = round((amount - rands) * 100)
    payer_type = data.get("payer_type", "self")
    payer_name = data.get("payer_name") if payer_type == "third_party" else ""
    account_name = data.get("account_name") or payer_name or (
        f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
    )
    identity = data.get("payer_id_number") if payer_type == "third_party" else data.get("id_number")
    identity_type = data.get("debicheck_id_type") or "0"
    client_ref1 = (
        data.get("debicheck_client_ref1")
        or itensity_client_ref_number(data.get("itensity_ref"))
        or f"FIT-{member_id}"
    )

    return {
        "member_id": member_id,
        "merchant_id": current_app.config["NUPAY_MERCHANT_ID"],
        "auth_type": data.get("debicheck_auth_type") or "cell",
        "client_ref1": client_ref1[:25],
        "client_ref2": str(data.get("debicheck_client_ref2") or "")[:4],
        "account_name": account_name[:30],
        "account_type": ACCOUNT_TYPE_CODES.get(data.get("account_type"), ""),
        "account_number": data.get("account_number", ""),
        "branch_code": data.get("branch_code", ""),
        "frequency": data.get("debicheck_frequency") or "3",
        "no_installments": int(data.get("debicheck_installments") or 99),
        "tracking": data.get("debicheck_tracking") or "14",
        "submit_date": data.get("debicheck_submit_date", ""),
        "id_type": identity_type,
        "id_number": identity if identity_type == "0" else "",
        "passport": data.get("debicheck_passport") or (identity if identity_type == "1" else ""),
        "juristic_account": data.get("debicheck_juristic", ""),
        "instalment_rands": rands,
        "instalment_cents": cents,
        "instalment_amount": amount,
        "payer_type": payer_type,
    }


def validate_mandate(data: dict[str, Any]) -> list[str]:
    """Validate payer and mandate details before saving or contacting NuPay."""
    if data.get("payment_type") == "Cash":
        return []
    if data.get("payment_type") not in ("Debit Order", "Third-Party Debit Order"):
        return ["Select Cash, Debit Order, or Third-Party Debit Order."]

    errors = []
    staff_number = str(data.get("debicheck_client_ref2") or "").strip()
    if len(staff_number) != 4 or not staff_number.isdigit():
        errors.append("Client Reference 2 must be the 4-digit staff number")
    for field, label in (
        ("account_name", "Account holder name"),
        ("bank", "Bank"),
        ("account_type", "Account type"),
        ("account_number", "Account number"),
        ("branch_code", "Branch code"),
        ("debicheck_submit_date", "DebiCheck submit date"),
    ):
        if not str(data.get(field) or "").strip():
            errors.append(f"{label} is required")
    join_date = str(data.get("join_date") or "").strip()
    submit_date = str(data.get("debicheck_submit_date") or "").strip()
    if join_date and submit_date and submit_date < join_date:
        errors.append(
            f"DebiCheck submit date ({submit_date}) cannot be before the "
            f"member's join date ({join_date})"
        )
    if float(data.get("monthly_installment") or 0) <= 0:
        errors.append("Monthly instalment must be greater than zero")
    account_number = str(data.get("account_number") or "").strip()
    if account_number and not account_number.isdigit():
        errors.append("Account number must contain digits only")
    branch_code = str(data.get("branch_code") or "").strip()
    if branch_code and (len(branch_code) != 6 or not branch_code.isdigit()):
        errors.append("Branch code must contain exactly 6 digits")
    if data.get("payer_type") == "third_party":
        if not str(data.get("payer_name") or "").strip():
            errors.append("Third-party payer name is required")
        payer_id = str(data.get("payer_id_number") or "").strip()
        if len(payer_id) != 13 or not payer_id.isdigit():
            errors.append("Third-party payer must have a valid 13-digit SA ID number")
    id_type = data.get("debicheck_id_type") or "0"
    if id_type == "1" and not str(data.get("debicheck_passport") or "").strip():
        errors.append("Passport number is required for passport mandates")
    if id_type == "4" and not str(data.get("debicheck_juristic") or "").strip():
        errors.append("Juristic account is required for juristic mandates")
    return errors


def validate_stored_mandate(mandate: dict[str, Any]) -> list[str]:
    """Validate a persisted mandate before a standalone retry."""
    errors = []
    required = {
        "merchant_id": "Merchant",
        "client_ref1": "Client Reference 1",
        "account_name": "Account holder name",
        "account_type": "Account type",
        "account_number": "Account number",
        "branch_code": "Branch code",
        "submit_date": "Submit date",
    }
    for field, label in required.items():
        if not str(mandate.get(field) or "").strip():
            errors.append(f"{label} is required")
    staff_number = str(mandate.get("client_ref2") or "").strip()
    if len(staff_number) != 4 or not staff_number.isdigit():
        errors.append("Client Reference 2 must be the 4-digit staff number")
    if float(mandate.get("instalment_amount") or 0) <= 0:
        errors.append("Instalment amount must be greater than zero")
    return errors


def insert_mandate(db, mandate: dict[str, Any], user_id: int | None) -> int:
    fields = tuple(mandate.keys())
    cursor = db.execute(
        f"""INSERT INTO debicheck_mandates
            ({', '.join(fields)}, status, submitted_by)
            VALUES ({', '.join('?' for _ in fields)}, 'pending', ?)""",
        tuple(mandate[field] for field in fields) + (user_id,),
    )
    return cursor.lastrowid


def _undecrypted_fields_error(mandate: dict[str, Any]) -> str | None:
    undecrypted = [
        field for field in ("account_number", "branch_code", "id_number")
        if str(mandate.get(field) or "").startswith("enc:")
    ]
    if not undecrypted:
        return None
    return (
        "Mandate has un-decrypted field(s): " + ", ".join(undecrypted) +
        ". This means the value's ciphertext no longer matches ENCRYPTION_KEY "
        "(check logs for the decrypt() warning) — not a real NuPay validation error. "
        "Re-save the field on the Edit screen to re-encrypt it with the current key."
    )


def push_mandate_to_nupay(mandate: dict[str, Any]) -> tuple[bool, str]:
    error = _undecrypted_fields_error(mandate)
    if error:
        return False, error
    result = nupay_new_driver.push_mandate(mandate)
    return bool(result.success), str(result.message)


def push_mandate_to_nupay_manual_review(mandate: dict[str, Any]) -> tuple[bool, str]:
    """Fill the mandate on NuPay in a visible browser and let a person handle
    Submit/CONFIRM/DONE themselves — see push_mandate_manual_review()'s
    docstring for why. Blocks until they finish (or close the window)."""
    error = _undecrypted_fields_error(mandate)
    if error:
        return False, error
    result = nupay_new_driver.push_mandate_manual_review(mandate)
    return bool(result.success), str(result.message)


def check_mandate_status_on_nupay(mandate: dict[str, Any]) -> tuple[bool, str, str]:
    """Look up a mandate's live status on NuPay without pushing anything.

    Returns (found, status, message)."""
    result = nupay_new_driver.refresh_mandate_status(mandate)
    return bool(result.found), str(result.status), str(result.message)


def record_push_result(db, mandate_id: int, success: bool, message: str) -> None:
    """Persist the NuPay submission result.

    A successful push means the mandate was submitted to NuPay; it does not
    mean the member has approved the mandate.  The compliance checklist is
    therefore left pending until a later live-status check returns an
    approval/authorisation state.
    """
    db.execute(
        """UPDATE debicheck_mandates
           SET status = ?, submitted_at = ?, nupay_response = ?, updated_at = datetime('now')
           WHERE id = ?""",
        ("submitted" if success else "failed",
         datetime.now().isoformat(timespec="seconds"), message, mandate_id),
    )


# NuPay installations can return different spellings for the same business
# outcome. Keep the canonical internal vocabulary small and explicit.
DEBICHECK_APPROVED_STATUSES = {
    "approved", "authorised", "authorized", "accepted", "active", "completed",
}
DEBICHECK_FAILED_STATUSES = {
    "failed", "rejected", "declined", "cancelled", "canceled", "expired",
}


def normalise_debicheck_status(status: str | None) -> str:
    """Convert a NuPay status into 1Cpace's canonical mandate vocabulary."""
    value = re.sub(r"[^a-z0-9]+", "_", str(status or "").strip().lower()).strip("_")
    if value in DEBICHECK_APPROVED_STATUSES:
        return "approved"
    if value in DEBICHECK_FAILED_STATUSES:
        return "failed"
    if value in {"pending", "submitted", "reviewing", "processing", "unknown"}:
        return value
    return "unknown"


def sync_debicheck_to_application(db, mandate_id: int, actor_id: int | None = None) -> str | None:
    """Synchronise an approved/failed mandate into the member application.

    Submission is deliberately *not* treated as approval.  Only a confirmed
    NuPay approval/authorisation can satisfy the application's DebiCheck gate.
    """
    row = db.execute(
        """SELECT d.member_id, d.status, ma.id AS application_id
           FROM debicheck_mandates d
           LEFT JOIN membership_applications ma ON ma.member_id = d.member_id
           WHERE d.id = ?
           ORDER BY ma.id DESC LIMIT 1""",
        (mandate_id,),
    ).fetchone()
    if not row or not row["application_id"]:
        return None

    status = normalise_debicheck_status(row["status"])
    checklist_status = {
        "approved": "approved",
        "failed": "failed",
    }.get(status)
    if checklist_status is None:
        return status

    aid = row["application_id"]
    previous = db.execute(
        "SELECT debit_check_status FROM compliance_checklists WHERE application_id = ?",
        (aid,),
    ).fetchone()

    db.execute(
        """INSERT OR IGNORE INTO compliance_checklists (application_id)
           VALUES (?)""",
        (aid,),
    )
    db.execute(
        """UPDATE compliance_checklists
           SET debit_check_status = ?, updated_at = datetime('now')
           WHERE application_id = ?""",
        (checklist_status, aid),
    )

    if previous is None or previous["debit_check_status"] != checklist_status:
        try:
            from .applications import sync_application_status
            sync_application_status(db, aid, actor_id=actor_id)
        except Exception:
            # The mandate status must remain persisted even if an older
            # application record cannot be reconciled.
            pass

    return checklist_status
