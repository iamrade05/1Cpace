"""Bring V8's DebiCheck mandates in line with NuPay's Mandate Report in one pass.

Checking a member's mandate on NuPay logs in and downloads the whole report each
time, so checking a gym's worth of members one by one means a thousand logins for
what is a single report. This reads the report once and settles every mandate from
those rows, using the same matching rules as the single-mandate check.

Two separate steps, each changing nothing unless asked:
- statuses: update the mandates V8 already has to what NuPay says;
- recording: create V8 records for live NuPay mandates V8 does not have, so the
  access rule can see those members' DebiCheck.

Recording is deliberately careful. A row is recorded only when the Itensity number in
its client reference names the member: the account holder's SA ID alone is not enough,
because a parent paying for a child has their own ID on the mandate. Recorded mandates
are marked as imported and are never sent to NuPay again.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any

from flask import current_app

from . import nupay_driver
from .database import member_activity
from .debicheck import ACCOUNT_TYPE_CODES, itensity_client_ref_number, sync_debicheck_to_application
from .encryption import decrypt_mandate, encrypt_mandate, hash_for_lookup

# NuPay's wording and V8's own statuses overlap, so "changed" means changed in what it
# means for the access rule, not in spelling.
_WAITING = {"pending", "submitted", "reviewing", "draft"}
_ACTIVE = {"active", "approved", "authorised", "authorized", "accepted", "completed"}

EXAMPLE_LIMIT = 25

# NuPay's Mandate Report describes tracking and frequency in its own new-portal
# wording, not V8's own field codes (the codes V8 sends when it pushes a mandate,
# via nupay_driver's old->new tables). Both are inverted here to convert back.
_TRACKING_DAY_TO_OLD_CODE = {new: old for old, new in nupay_driver._TRACKING_TO_NEW.items()}
_FREQUENCY_NEW_TO_OLD_CODE = {new: old for old, new in nupay_driver._FREQUENCY_TO_NEW.items()}


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", _text(value))


def _report_status(row: dict[str, Any]) -> str:
    return nupay_driver._normalize_mandate_status(
        row.get("status"), row.get("status_reason"), row.get("mandate_status")
    ) or "unknown"


def _same_meaning(old: str | None, new: str) -> bool:
    old = (old or "").lower()
    if old == new:
        return True
    if new == "pending" and old in _WAITING:
        return True
    if new == "active" and old in _ACTIVE:
        return True
    return False


def _member_indexes(db):
    by_ref: dict[str, Any] = {}
    by_id: dict[str, Any] = {}
    for member in db.execute(
        "SELECT id, first_name, last_name, itensity_ref, id_number_hash FROM members"
    ).fetchall():
        ref = itensity_client_ref_number(member["itensity_ref"])
        if ref:
            by_ref.setdefault(ref, member)
        if member["id_number_hash"]:
            by_id.setdefault(member["id_number_hash"], member)
    return by_ref, by_id


def _client_reference_digits(row: dict[str, Any]) -> str:
    return _digits(_text(row.get("client_reference")).split("-")[0])


def _member_by_reference(row: dict[str, Any], by_ref):
    """The member the Itensity number in the client reference names (V8's own identifier)."""
    digits = _client_reference_digits(row)
    if digits and len(digits) != 13:
        return by_ref.get(itensity_client_ref_number(digits))
    return None


def _member_by_id(row: dict[str, Any], by_id):
    """A member whose SA ID is on the row - the account holder, who may only be the payer."""
    for candidate in (_client_reference_digits(row), _digits(row.get("debtor_id"))):
        if len(candidate) == 13:
            member = by_id.get(hash_for_lookup(candidate))
            if member is not None:
                return member
    return None


def _member_for_report_row(row: dict[str, Any], by_ref, by_id):
    return _member_by_reference(row, by_ref) or _member_by_id(row, by_id)


# ── Turning a report row into a V8 mandate ───────────────────────────────────

def _money(value: Any) -> float:
    try:
        return round(float(re.sub(r"[^0-9.\-]", "", _text(value))), 2)
    except ValueError:
        return 0.0


def _day(*values: Any) -> str:
    for value in values:
        match = re.match(r"\d{4}-\d{2}-\d{2}", _text(value))
        if match:
            return match.group(0)
    return date.today().isoformat()


def _tracking_old_code(row: dict[str, Any]) -> str:
    """The report's `tracking` field is only a Y/N flag; the real day count is in
    `tracking_desc` ('5 Day Tracking'). Converted back to V8's own tracking code via
    the same table nupay_driver uses when submitting a mandate - reversed."""
    match = re.search(r"(\d+)\s*Day", _text(row.get("tracking_desc")), re.I)
    if match:
        old_code = _TRACKING_DAY_TO_OLD_CODE.get(f"{int(match.group(1)):02d}")
        if old_code:
            return old_code
    return "14"


def _frequency_old_code(row: dict[str, Any]) -> str:
    """The report's `frequency` field already uses new-portal wording (Monthly/Weekly/
    Ad-Hoc), not V8's own numeric code. Old codes 5 and 6 both mean Ad-Hoc on the new
    portal and cannot be told apart from the report alone; ad-hoc mandates import as 5
    (End of Month), the more common of the two."""
    return _FREQUENCY_NEW_TO_OLD_CODE.get(_text(row.get("frequency")).upper(), "3")


def _account_type_code(row: dict[str, Any]) -> str:
    code = _text(row.get("debtor_account_type_code"))
    if code in ACCOUNT_TYPE_CODES:
        return ACCOUNT_TYPE_CODES[code]
    description = " ".join(
        _text(row.get(key)).lower() for key in ("debtor_account_type_desc", "debtor_account_type")
    )
    if "saving" in description:
        return "2"
    if "transmission" in description:
        return "3"
    if "cheque" in description or "current" in description:
        return "1"
    return ""


def _mandate_from_report_row(row: dict[str, Any], member) -> dict[str, Any]:
    reference1, _, reference2 = _text(row.get("client_reference")).partition("-")
    debtor_id = _digits(row.get("debtor_id"))
    paid_by_member = (
        bool(member["id_number_hash"]) and len(debtor_id) == 13
        and hash_for_lookup(debtor_id) == member["id_number_hash"]
    )
    amount = _money(row.get("instalment_amount") or row.get("instalment"))
    installments = _digits(row.get("instalments"))
    accepted = _text(row.get("mandate_acceptance_date")) or _text(row.get("date_created"))
    contract = _text(row.get("contract_reference"))
    merchant_id = current_app.config.get("NUPAY_MERCHANT_ID") or nupay_driver.DEFAULT_ACCESS_ID
    return {
        "member_id": member["id"],
        "merchant_id": merchant_id,
        "client_ref1": reference1.strip()[:25],
        "client_ref2": reference2.strip()[:4],
        "account_name": (_text(row.get("debtor_name"))
                         or f"{member['first_name']} {member['last_name']}".strip())[:30],
        "account_type": _account_type_code(row),
        "account_number": _digits(row.get("debtor_account_number")),
        "branch_code": _digits(row.get("debtor_branch_number")),
        "frequency": _frequency_old_code(row),
        "no_installments": int(installments) if installments else 99,
        "tracking": _tracking_old_code(row),
        "submit_date": _day(row.get("first_collection_date"), accepted),
        "id_type": "0",
        "id_number": debtor_id if len(debtor_id) == 13 else "",
        "passport": "",
        "juristic_account": "",
        "instalment_rands": int(amount),
        "instalment_cents": round((amount - int(amount)) * 100),
        "instalment_amount": amount,
        "payer_type": "self" if paid_by_member else "third_party",
        "status": "active",
        "submitted_at": accepted[:19] or None,
        "nupay_response": f"[NuPay import {date.today().isoformat()}] Contract {contract or '?'}: Active",
        "contract_reference": contract,
        "imported_from_nupay": 1,
    }


def _record_mandate(db, member, row: dict[str, Any], actor_id) -> int:
    data = encrypt_mandate(_mandate_from_report_row(row, member))
    fields = tuple(data)
    mandate_id = db.execute(
        f"INSERT INTO debicheck_mandates ({', '.join(fields)}) "
        f"VALUES ({', '.join('?' for _ in fields)})",
        tuple(data[field] for field in fields),
    ).lastrowid
    member_activity(
        db, member["id"],
        f"DebiCheck mandate #{mandate_id} imported from NuPay "
        f"(contract {_text(row.get('contract_reference')) or '?'}, active).",
        user_id=actor_id,
    )
    return mandate_id


# ── Updating the mandates V8 already has ─────────────────────────────────────

def _apply_change(db, mandate, report_row: dict[str, Any], new_status: str, actor_id) -> None:
    if new_status == "unverified":
        note = (f"[NuPay sync {date.today().isoformat()}] Only a mandate accepted before this member "
                f"joined matches - likely someone else's earlier mandate. Not applied as active.")
    else:
        note = (f"[NuPay sync {date.today().isoformat()}] Contract "
                f"{report_row.get('contract_reference', '?')}: {report_row.get('status', '?')}")
    db.execute(
        "UPDATE debicheck_mandates SET status = ?, nupay_response = ? WHERE id = ?",
        (new_status, note[:2000], mandate["id"]),
    )
    gate = sync_debicheck_to_application(db, mandate["id"], actor_id=actor_id)
    member_activity(
        db, mandate["member_id"],
        f"DebiCheck mandate #{mandate['id']}: status {mandate['status']} -> {new_status} (synced from NuPay)"
        + (f" | application gate: {gate}" if gate else ""),
        user_id=actor_id,
    )


def sync_from_report(
    db, rows: list[dict[str, Any]], *, apply: bool, actor_id=None, import_missing: bool = False,
) -> dict[str, Any]:
    """Compare every local mandate with the report rows. *apply* updates the mandates V8
    has; *import_missing* records the live NuPay mandates it lacks. Neither by default."""
    mandates = db.execute(
        """SELECT d.*, m.join_date AS member_join_date, m.first_name, m.last_name
           FROM debicheck_mandates d JOIN members m ON m.id = d.member_id
           ORDER BY d.id"""
    ).fetchall()

    claimed: set[int] = set()          # report rows a local mandate already accounts for
    changes: list[dict[str, Any]] = []
    missing_from_active_list: list[dict[str, Any]] = []
    unchanged = not_found = no_reference = 0
    for mandate in mandates:
        data = decrypt_mandate(mandate)
        client_ref1 = str(data.get("client_ref1") or "").strip()
        account_number = str(data.get("account_number") or "").strip()
        contract = str(data.get("contract_reference") or "").strip()
        if len(client_ref1) < 6 and len(account_number) < 6 and not contract:
            no_reference += 1
            continue
        report_row, pre_join_only = nupay_driver.match_mandate_row(rows, data)
        if report_row is None:
            not_found += 1
            # The report lists only active mandates, so a mandate V8 shows as active that is
            # absent from it may have been cancelled at NuPay - or may just not match. A
            # person decides; nothing is changed.
            if (mandate["status"] or "").lower() in _ACTIVE:
                missing_from_active_list.append({
                    "mandate_id": mandate["id"], "member_id": mandate["member_id"],
                    "member": f"{mandate['first_name']} {mandate['last_name']}".strip(),
                    "status": mandate["status"],
                })
            continue
        claimed.add(id(report_row))
        new_status = "unverified" if pre_join_only else _report_status(report_row)
        if new_status == "unknown" or _same_meaning(mandate["status"], new_status):
            unchanged += 1
            continue
        changes.append({
            "mandate_id": mandate["id"], "member_id": mandate["member_id"],
            "member": f"{mandate['first_name']} {mandate['last_name']}".strip(),
            "old": mandate["status"], "new": new_status,
        })
        if apply:
            _apply_change(db, mandate, report_row, new_status, actor_id)

    # Live at NuPay but not accounted for by any local mandate: the gap the access rule
    # cannot see.
    by_ref, by_id = _member_indexes(db)
    covered = {mandate["member_id"] for mandate in mandates}
    live_unrecorded = [r for r in rows if id(r) not in claimed and _report_status(r) == "active"]
    matched_to_member = already_have_a_record = 0
    examples: list[dict[str, Any]] = []
    held_id_only = no_member = has_mandate = 0
    candidates: dict[int, list[tuple[Any, dict[str, Any]]]] = {}
    for report_row in live_unrecorded:
        by_reference = _member_by_reference(report_row, by_ref)
        member = by_reference or _member_by_id(report_row, by_id)
        if member is None:
            no_member += 1
            continue
        matched_to_member += 1
        if member["id"] in covered:
            already_have_a_record += 1
        if len(examples) < EXAMPLE_LIMIT:
            examples.append({
                "member_id": member["id"],
                "member": f"{member['first_name']} {member['last_name']}".strip(),
                "contract_reference": report_row.get("contract_reference"),
            })
        if by_reference is None:
            held_id_only += 1              # only the account holder's ID - could be a payer
        elif member["id"] in covered:
            has_mandate += 1
        else:
            candidates.setdefault(member["id"], []).append((member, report_row))

    to_record: list[tuple[Any, dict[str, Any]]] = []
    superseded = 0
    for pairs in candidates.values():
        pairs.sort(key=lambda pair: nupay_driver._acceptance_key(pair[1]))
        to_record.append(pairs[-1])         # the latest live mandate
        superseded += len(pairs) - 1

    recorded = 0
    if import_missing:
        for member, report_row in to_record:
            _record_mandate(db, member, report_row, actor_id)
            recorded += 1

    return {
        "applied": apply,
        "report_rows": len(rows),
        "report_by_status": dict(Counter(_report_status(r) for r in rows)),
        "mandates_checked": len(mandates),
        "updated": len(changes),
        "changes": changes,
        "unchanged": unchanged,
        "not_found": not_found,
        "no_reference": no_reference,
        "missing_from_active_list": {
            "count": len(missing_from_active_list),
            "examples": missing_from_active_list[:EXAMPLE_LIMIT],
        },
        "unrecorded": {
            "live_without_local_record": len(live_unrecorded),
            "matched_to_member": matched_to_member,
            "member_already_has_a_mandate": already_have_a_record,
            "no_member": no_member,
            "examples": examples,
        },
        "import": {
            "applied": import_missing,
            "to_record": len(to_record),
            "recorded": recorded,
            "superseded": superseded,
            "held_id_only": held_id_only,
            "no_member": no_member,
            "member_already_has_a_mandate": has_mandate,
            "examples": [
                {
                    "member_id": member["id"],
                    "member": f"{member['first_name']} {member['last_name']}".strip(),
                    "contract_reference": report_row.get("contract_reference"),
                }
                for member, report_row in to_record[:EXAMPLE_LIMIT]
            ],
        },
    }
