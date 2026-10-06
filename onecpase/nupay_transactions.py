"""NuPay Transaction Report -> member collection history.

NuPay is the authority on whether a DebiCheck debit was paid. Until now the app only
read NuPay's *Mandate* Report, so a member could be paying every month on NuPay while
their record here showed unpaid charges and sent them to collections.

Each Success or Failed row becomes entries in ``collections``:

- Success: a payment (and, when that month has no charge yet, the charge it settled).
- Failed:  the month's charge is marked failed (created first when missing).

A payment must always travel with its charge. ``members._build_payment_profile`` applies
payments to the *oldest* unpaid charge first, so a payment without its own month's charge
would silently clear an older month instead - the exact error this module exists to stop.

Nothing is written unless ``apply_plan`` is called. ``plan_import`` only reads.
Every row it writes carries ``nupay_txn=<key>``, so running it twice adds nothing.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .nupay_sync import _member_indexes, _member_for_report_row

SOURCE = "NuPay transaction report"
TXN_TAG = "nupay_txn="
SUCCESS = "success"
FAILED = "failed"
# Reversed means a payment that was paid and then taken back: it needs a person to decide
# how the account is corrected, so it is counted and reported, never auto-posted.
REVIEW = {"reversed", "disputed"}


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _text(name).lower()).strip("_")


def normalise_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Column names arrive as 'Client Reference', 'client_reference' or 'ClientReference'."""
    return {_key(name): value for name, value in raw.items()}


def parse_csv(content: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    return [normalise_row(row) for row in reader]


def _money(value: Any) -> float:
    try:
        return round(float(re.sub(r"[^0-9.\-]", "", _text(value))), 2)
    except ValueError:
        return 0.0


def _day(value: Any) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", _text(value))
    return match.group(0) if match else ""


def _status(row: dict[str, Any]) -> str:
    return _text(row.get("status")).lower()


def transaction_key(row: dict[str, Any]) -> str:
    """Stable identity of one debit, so a re-run recognises it."""
    unique = _text(row.get("id") or row.get("transaction_id"))
    if unique:
        return f"id{unique}"
    return ":".join(
        part for part in (
            _text(row.get("mandate_id")), _text(row.get("client_reference")), _text(row.get("instalment")),
            _day(row.get("action_date")), _status(row),
        )
    )


def _notes(kind: str, row: dict[str, Any], key: str) -> str:
    detail = f"Instalment {_text(row.get('instalment'))} of {_text(row.get('total_instalments')) or '?'}".strip()
    return (
        f"{SOURCE} | type={kind} | description={detail} (contract {_text(row.get('contract_reference'))}) "
        f"| mandate={_text(row.get('mandate_id'))} | {TXN_TAG}{key}"
    )


def _month_of(row: dict[str, Any]) -> str:
    return (_day(row.get("cycle_date")) or _day(row.get("action_date")))[:7]


def plan_import(db, rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Work out what would be recorded. Reads the database; changes nothing."""
    by_ref, by_id = _member_indexes(db)
    already = set()
    for entry in db.execute(f"SELECT notes FROM collections WHERE notes LIKE '%{TXN_TAG}%'"):
        found = re.search(re.escape(TXN_TAG) + r"(\S+)", entry["notes"] or "")
        if found:
            already.add(found.group(1))

    months: dict[tuple[int, str], int] = {}
    for entry in db.execute(
        "SELECT id, member_id, collection_date, notes FROM collections WHERE notes LIKE '%type=Recurring Fee%'"
    ):
        months.setdefault((entry["member_id"], (entry["collection_date"] or "")[:7]), entry["id"])

    plan = {"actions": [], "summary": Counter(), "unmatched": Counter(), "review": [], "members": set()}
    pending_charges: set[tuple[int, str]] = set()

    for raw in rows:
        row = normalise_row(raw)
        status = _status(row)
        if status in REVIEW:
            plan["summary"]["needs_review"] += 1
            plan["review"].append({k: row.get(k) for k in ("mandate_id", "client_reference", "action_date", "status", "instalment_amount")})
            continue
        if status not in (SUCCESS, FAILED):
            plan["summary"]["ignored"] += 1   # tracking, future, cancelled
            continue

        key = transaction_key(row)
        amount = _money(row.get("instalment_amount"))
        action = _day(row.get("action_date"))
        month = _month_of(row)
        if not key or not amount or not action or not month:
            plan["summary"]["unreadable"] += 1
            continue

        member = _member_for_report_row(row, by_ref, by_id)
        if member is None:
            plan["summary"]["unmatched"] += 1
            plan["unmatched"][_text(row.get("client_reference")) or "no reference"] += 1
            continue
        mid = member["id"]

        if key in already or f"{key}:charge" in already:
            plan["summary"]["already_recorded"] += 1
            continue
        already.add(key)

        charge_id = months.get((mid, month))
        needs_charge = charge_id is None and (mid, month) not in pending_charges
        if needs_charge:
            pending_charges.add((mid, month))
            plan["actions"].append({
                "op": "charge", "member_id": mid, "date": _day(row.get("cycle_date")) or action,
                "amount": amount, "status": "failed" if status == FAILED else "pending",
                "notes": _notes("Recurring Fee", row, key + ":charge"),
            })
            plan["summary"]["charges_added"] += 1
        elif status == FAILED and charge_id is not None:
            plan["actions"].append({"op": "mark_failed", "collection_id": charge_id, "member_id": mid, "note_key": key})
            plan["summary"]["charges_marked_failed"] += 1

        if status == SUCCESS:
            plan["actions"].append({
                "op": "payment", "member_id": mid, "date": action, "amount": amount,
                "notes": _notes("Debit Order Payment", row, key),
            })
            plan["summary"]["payments_added"] += 1
        plan["members"].add(mid)

    plan["summary"]["members_touched"] = len(plan["members"])
    return plan


def apply_plan(db, plan: dict[str, Any]) -> Counter:
    """Write a plan. The caller commits."""
    done = Counter()
    for action in plan["actions"]:
        if action["op"] == "charge":
            db.execute(
                """INSERT INTO collections (member_id, outstanding_balance, amount_paid, collection_date,
                       method, status, notes, created_by)
                   VALUES (?, ?, 0, ?, 'debicheck', ?, ?, NULL)""",
                (action["member_id"], action["amount"], action["date"], action["status"], action["notes"]),
            )
        elif action["op"] == "payment":
            db.execute(
                """INSERT INTO collections (member_id, outstanding_balance, amount_paid, collection_date,
                       method, status, notes, created_by)
                   VALUES (?, 0, ?, ?, 'debicheck', 'paid', ?, NULL)""",
                (action["member_id"], action["amount"], action["date"], action["notes"]),
            )
        elif action["op"] == "mark_failed":
            db.execute(
                "UPDATE collections SET status='failed', notes=COALESCE(notes,'') || ? WHERE id=?",
                (f" | {TXN_TAG}{action['note_key']}", action["collection_id"]),
            )
        done[action["op"]] += 1
    return done


def paying_but_in_arrears(db, rows: Iterable[dict[str, Any]], *, days: int = 45, today: date | None = None) -> list[dict[str, Any]]:
    """Members with a successful NuPay debit in the last ``days`` days who still show arrears.

    These are the people collections would ring while they are paying. Run it on the
    state *before* applying a plan: a member listed here has an unrecorded or
    unreconciled payment history, not necessarily a debt.
    """
    from .ptp import bulk_member_arrears

    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
    by_ref, by_id = _member_indexes(db)
    latest: dict[int, dict[str, Any]] = {}
    for raw in rows:
        row = normalise_row(raw)
        if _status(row) != SUCCESS or _day(row.get("action_date")) < cutoff:
            continue
        member = _member_for_report_row(row, by_ref, by_id)
        if member is None:
            continue
        seen = latest.get(member["id"])
        if seen is None or _day(row.get("action_date")) > seen["last_success"]:
            latest[member["id"]] = {"member_id": member["id"], "last_success": _day(row.get("action_date")),
                                    "amount": _money(row.get("instalment_amount"))}
    arrears = bulk_member_arrears(db, list(latest))
    flagged = []
    for mid, info in latest.items():
        owing = arrears.get(mid, {})
        if owing.get("total_arrears", 0) > 0:
            flagged.append({**info, "total_arrears": owing["total_arrears"], "months": owing.get("months_in_arrears", 0)})
    for item in flagged:
        who = db.execute("SELECT member_ref, first_name, last_name FROM members WHERE id=?", (item["member_id"],)).fetchone()
        item["member_ref"] = who["member_ref"] if who else ""
        item["name"] = f"{who['first_name']} {who['last_name']}".strip() if who else ""
    return sorted(flagged, key=lambda item: -item["total_arrears"])
