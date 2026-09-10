"""Import Itensity transactions into 1Cpase member collection history.

The import is a dry run unless --commit is supplied. Transactions are matched
to members by Itensity account reference and written to the collections table.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


CHARGE_TYPES = {"Recurring Fee", "Once-Off Fee"}
PAYMENT_TYPES = {
    "Debit Order Payment": "debicheck",
    "Card Payment": "card",
    "Cash Payment": "cash",
    "EFT Payment": "eft",
}


def text(value: Any) -> str:
    return str(value or "").strip()


def amount(value: Any) -> float:
    value_text = (
        text(value)
        .replace("R", "")
        .replace(",", "")
        .replace(" ", "")
    )
    if not value_text:
        return 0.0
    negative = value_text.startswith("(") and value_text.endswith(")")
    value_text = value_text.strip("()")
    try:
        parsed = round(float(value_text), 2)
        return -parsed if negative else parsed
    except ValueError:
        return 0.0


def iso_date(value: Any) -> str:
    value_text = text(value)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value_text, fmt).date().isoformat()
        except ValueError:
            pass
    return value_text


def note_for(row: dict[str, str]) -> str:
    parts = [
        "Itensity import",
        f"ref={text(row.get('Ref'))}",
        f"type={text(row.get('Type'))}",
        f"description={text(row.get('Description'))}",
        f"debit={text(row.get('Debit'))}",
        f"credit={text(row.get('Credit'))}",
        f"balance={text(row.get('Balance'))}",
        f"loaded_by={text(row.get('Loaded By'))}",
        f"sales_rep={text(row.get('Sales Rep'))}",
        f"loaded_on={text(row.get('Loaded On'))}",
    ]
    return " | ".join(parts)


def collection_values(row: dict[str, str], member_id: int) -> dict[str, Any]:
    transaction_type = text(row.get("Type"))
    debit = abs(amount(row.get("Debit")))
    credit = abs(amount(row.get("Credit")))
    transaction_amount = abs(amount(row.get("Amount")))

    if transaction_type in CHARGE_TYPES:
        outstanding_balance = debit or transaction_amount
        amount_paid = 0.0
        method = "other"
        status = "pending"
    elif transaction_type in PAYMENT_TYPES:
        outstanding_balance = 0.0
        amount_paid = credit or transaction_amount
        method = PAYMENT_TYPES[transaction_type]
        status = "paid"
    elif transaction_type == "Debit Order Unpaid":
        outstanding_balance = debit or transaction_amount
        amount_paid = 0.0
        method = "debicheck"
        status = "failed"
    elif transaction_type == "Discount":
        outstanding_balance = 0.0
        amount_paid = credit or transaction_amount
        method = "other"
        status = "paid"
    else:
        outstanding_balance = 0.0
        amount_paid = transaction_amount
        method = "other"
        status = "pending"

    return {
        "member_id": member_id,
        "outstanding_balance": outstanding_balance,
        "discount_pct": 0.0,
        "amount_paid": amount_paid,
        "collection_date": iso_date(row.get("Date")),
        "method": method,
        "notes": note_for(row),
        "status": status,
    }


def read_transactions(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as csvfile:
        return list(csv.DictReader(csvfile))


def import_transactions(
    db: sqlite3.Connection, rows: list[dict[str, str]]
) -> tuple[Counter, Counter]:
    result = Counter()
    unmatched_refs = Counter()
    member_refs = {
        str(row["itensity_ref"]): row["id"]
        for row in db.execute(
            "SELECT id, itensity_ref FROM members WHERE COALESCE(itensity_ref, '') != ''"
        )
    }
    existing_keys = {
        (
            row["member_id"],
            row["collection_date"],
            round(float(row["outstanding_balance"] or 0), 2),
            round(float(row["amount_paid"] or 0), 2),
            row["method"],
            row["status"],
            row["notes"],
        )
        for row in db.execute(
            """SELECT member_id, collection_date, outstanding_balance, amount_paid,
                      method, status, notes
               FROM collections"""
        )
    }

    for row in rows:
        ref = text(row.get("Ref"))
        member_id = member_refs.get(ref)
        if not member_id:
            unmatched_refs[f"{ref} - {text(row.get('Name and Surname'))}"] += 1
            result["unmatched"] += 1
            continue

        values = collection_values(row, int(member_id))
        key = (
            values["member_id"],
            values["collection_date"],
            values["outstanding_balance"],
            values["amount_paid"],
            values["method"],
            values["status"],
            values["notes"],
        )
        if key in existing_keys:
            result["duplicates"] += 1
            continue

        db.execute(
            """INSERT INTO collections
               (member_id, outstanding_balance, discount_pct, amount_paid,
                collection_date, method, status, notes, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                values["member_id"],
                values["outstanding_balance"],
                values["discount_pct"],
                values["amount_paid"],
                values["collection_date"],
                values["method"],
                values["status"],
                values["notes"],
            ),
        )
        existing_keys.add(key)
        result["inserted"] += 1
        result[text(row.get("Type"))] += 1

    return result, unmatched_refs


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--database", type=Path, default=root / "1cpase.db")
    parser.add_argument("--commit", action="store_true", help="Write changes to the database")
    parser.add_argument(
        "--replace-itensity",
        action="store_true",
        help="Delete previously imported Itensity transaction rows before importing",
    )
    args = parser.parse_args()

    rows = read_transactions(args.csv_file)
    db = sqlite3.connect(args.database)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    before = db.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
    backup = None
    if args.commit:
        backup_dir = root / "backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"1cpase_before_transaction_import_{stamp}.db"
        backup_db = sqlite3.connect(backup)
        try:
            db.backup(backup_db)
        finally:
            backup_db.close()

    try:
        if args.replace_itensity:
            deleted = db.execute(
                "DELETE FROM collections WHERE notes LIKE 'Itensity import |%'"
            ).rowcount
            print(f"Deleted existing Itensity imports: {deleted}")
        result, unmatched_refs = import_transactions(db, rows)
        after = db.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
        print(f"Transaction rows: {len(rows)}")
        print("Import result:", dict(result))
        print(f"Collection count: {before} -> {after}")
        if unmatched_refs:
            print("Top unmatched refs:")
            for ref, count in unmatched_refs.most_common(20):
                print(f"  {ref}: {count}")
        if args.commit:
            db.commit()
            print(f"Committed. Backup: {backup}")
        else:
            db.rollback()
            print("Dry run only; no database changes were made.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
