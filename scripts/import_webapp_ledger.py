"""Import the real transaction ledger from WEBAPPL\\1Cpace into V8's collections table.

V8's Collections and PTP screens have been empty since day one because no billing
history has ever been loaded — arrears comes only from onecpase.ptp.bulk_member_arrears,
which reads the `collections` table. That history already exists, correctly imported,
in the other codebase's live database: WEBAPPL\\1Cpace\\cpace.db, table
payment_statement_entries (43,314 rows). This script moves the real 41,653 posted/paid
rows across; it leaves out 1,658 payment_profiles rows (future-scheduled projections,
not actual transactions) and a handful of zero-value adjustment rows.

The source database is opened read-only (file:...?mode=ro) — this script can never
write to the live production database.

Dry run unless --commit is supplied. A backup is written to V8's tenant database
before any write, and the target defaults to the active tenant from platform.db,
not a guessed filename.

    python scripts/import_webapp_ledger.py
    python scripts/import_webapp_ledger.py --commit
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_WEBAPP_DB = (
    Path.home() / "OneDrive - Elev8 Health and Fitness Gym (1)"
    / "RADE" / "WEBAPPL" / "1Cpace" / "cpace.db"
)

# Same vocabulary as scripts/import_itensity_transactions.py's collection_values(),
# because the source description text is Itensity's own "Type: detail" wording
# with the type folded into the free-text description rather than its own column.
CHARGE_TYPES = {"Recurring Fee", "Once-Off Fee"}
PAYMENT_TYPES = {
    "Debit Order Payment": "debicheck",
    "Card Payment": "card",
    "Cash Payment": "cash",
    "EFT Payment": "eft",
}


def _active_tenant_database(slug: str) -> Path:
    """The database path the app serves for *slug*, straight from platform.db.

    Mirrors scripts/reset_staff_passwords.py — a wrong guess at a filename here
    silently writes nothing to the database the app actually reads.
    """
    from onecpase.config import Config

    platform = sqlite3.connect(Config.PLATFORM_DATABASE_PATH)
    platform.row_factory = sqlite3.Row
    try:
        row = platform.execute(
            "SELECT db_path FROM tenants WHERE slug=? AND active=1", (slug,)
        ).fetchone()
    finally:
        platform.close()
    if not row:
        raise SystemExit(f"No active tenant with slug {slug!r} in platform.db")
    path = Path(row["db_path"])
    return path if path.is_absolute() else (ROOT / path)


def _type_from_description(description: str) -> str:
    """Recover Itensity's transaction "Type" from "Type: detail" description text."""
    head, sep, _ = (description or "").partition(":")
    return head.strip() if sep else ""


_LOADED_BY_RE = re.compile(r"\(Loaded by (.*?)\)", re.IGNORECASE)


def _loaded_by(description: str) -> str:
    m = _LOADED_BY_RE.search(description or "")
    return m.group(1).strip() if m else ""


def _notes_for(entry: sqlite3.Row, kind: str, description: str) -> str:
    """Build the ``key=value | key=value`` notes string the account page and
    the arrears engine both parse via onecpase.members._collection_note_parts().

    Only three keys are ever read back out anywhere in onecpase/*.py — type,
    description, loaded_by — but type is the one that matters:
    _build_payment_profile() only treats a row as a chargeable event when
    note_parts["type"] is exactly "Recurring Fee" or "Once-Off Fee". A notes
    string without that key makes every imported row invisible to arrears
    while still displaying fine in the raw ledger — the shape of bug that
    passes a casual look and only fails a real spot-check against known
    balances (caught here: five sampled members all showed R0 arrears
    despite 40-60 transaction rows each and a genuine net balance).
    """
    return (
        f"WEBAPPL ledger import | ref={entry['member_id']} | type={kind} | "
        f"description={description} | debit={entry['debit_amount']} | "
        f"credit={entry['credit_amount']} | loaded_by={_loaded_by(description)} | "
        f"entry_id={entry['id']} | period={entry['period']}"
    )


def collection_row(entry: sqlite3.Row, member_id: int) -> dict[str, Any] | None:
    """Map one payment_statement_entries row to a onecpase collections row.

    Returns None for rows with no financial effect (both amounts zero) — a small
    number of housekeeping entries (e.g. a zero-value "Dependent" fee note) that
    would otherwise land as a meaningless pending-R0.00 row.
    """
    debit = round(float(entry["debit_amount"] or 0), 2)
    credit = round(float(entry["credit_amount"] or 0), 2)
    if debit == 0 and credit == 0:
        return None

    description = entry["description"] or ""
    kind = _type_from_description(description)
    entry_type = entry["entry_type"]
    source_type = entry["source_type"]

    if entry_type == "charge" and source_type == "transactions_report":
        if kind == "Debit Order Unpaid":
            outstanding_balance, amount_paid = debit, 0.0
            method, status = "debicheck", "failed"
        elif kind in CHARGE_TYPES:
            outstanding_balance, amount_paid = debit, 0.0
            method, status = "other", "pending"
        else:
            # No recognised "Type: " prefix on a charge row (7 rows total).
            # Treated as a Once-Off Fee so it still counts toward arrears
            # rather than silently vanishing from the calculation.
            outstanding_balance, amount_paid = debit, 0.0
            method, status = "other", "pending"
            kind = kind or "Once-Off Fee"
    elif entry_type == "payment" and source_type == "transactions_report":
        if kind in PAYMENT_TYPES:
            outstanding_balance, amount_paid = 0.0, credit
            method, status = PAYMENT_TYPES[kind], "paid"
        elif kind == "Discount":
            outstanding_balance, amount_paid = 0.0, credit
            method, status = "other", "paid"
        else:
            outstanding_balance, amount_paid = 0.0, credit
            method, status = "other", "paid"
            kind = kind or "Cash Payment"
    elif source_type == "itensity_live_balance":
        # A point-in-time balance reconciliation, not a monthly charge — the
        # arrears engine only recognises Recurring Fee / Once-Off Fee rows, so
        # this deliberately does NOT feed the arrears total (labelling it as
        # one of those to force it in would misreport what actually happened
        # on the account). It still appears in the raw ledger on the member's
        # account page.
        kind = "Live Balance Adjustment"
        if debit:
            outstanding_balance, amount_paid = debit, 0.0
            method, status = "other", "pending"
        else:
            outstanding_balance, amount_paid = 0.0, credit
            method, status = "other", "paid"
    else:
        # The 23 zero-value "adjustment" rows under transactions_report, and
        # anything else unforeseen, fall back to a plain record of the amount
        # rather than being silently dropped.
        kind = kind or "Adjustment"
        outstanding_balance, amount_paid = debit, credit

    return {
        "member_id": member_id,
        "outstanding_balance": outstanding_balance,
        "discount_pct": 0.0,
        "amount_paid": amount_paid,
        "collection_date": entry["entry_date"],
        "method": method,
        "status": status,
        "notes": _notes_for(entry, kind, description),
    }


def load_source_entries(webapp_db: Path) -> tuple[list[sqlite3.Row], dict[str, int]]:
    uri = f"file:{webapp_db.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        entries = conn.execute(
            """SELECT id, member_id, period, entry_date, due_date, entry_type,
                      description, debit_amount, credit_amount, status, source_type
               FROM payment_statement_entries
               WHERE status IN ('posted', 'paid')
               ORDER BY member_id, entry_date, id"""
        ).fetchall()
        refs = {
            str(row["mid"]): row["ref"]
            for row in conn.execute(
                """SELECT id AS mid,
                          COALESCE(NULLIF(TRIM(unique_ref), ''), NULLIF(TRIM(itensity_ref), '')) AS ref
                   FROM members"""
            ).fetchall()
            if row["ref"]
        }
        return entries, {mid: ref for mid, ref in refs.items()}
    finally:
        conn.close()


def import_ledger(
    db: sqlite3.Connection,
    entries: list[sqlite3.Row],
    source_member_refs: dict[str, str],
) -> tuple[Counter, Counter, float, float]:
    result = Counter()
    unmatched_refs = Counter()
    source_total_debit = source_total_credit = 0.0

    v8_member_ids = {
        str(row["itensity_ref"]): row["id"]
        for row in db.execute(
            "SELECT id, itensity_ref FROM members WHERE COALESCE(itensity_ref, '') != ''"
        )
    }
    existing_keys = {
        (
            row["member_id"], row["collection_date"],
            round(float(row["outstanding_balance"] or 0), 2),
            round(float(row["amount_paid"] or 0), 2),
            row["method"], row["status"], row["notes"],
        )
        for row in db.execute(
            """SELECT member_id, collection_date, outstanding_balance, amount_paid,
                      method, status, notes FROM collections
               WHERE notes LIKE 'WEBAPPL ledger import |%'"""
        )
    }

    for entry in entries:
        source_total_debit += float(entry["debit_amount"] or 0)
        source_total_credit += float(entry["credit_amount"] or 0)

        source_ref = source_member_refs.get(str(entry["member_id"]))
        v8_id = v8_member_ids.get(source_ref) if source_ref else None
        if not v8_id:
            unmatched_refs[f"webapp_member_id={entry['member_id']} ref={source_ref or '(none)'}"] += 1
            result["unmatched"] += 1
            continue

        row = collection_row(entry, int(v8_id))
        if row is None:
            result["skipped_zero_value"] += 1
            continue

        key = (
            row["member_id"], row["collection_date"],
            round(row["outstanding_balance"], 2), round(row["amount_paid"], 2),
            row["method"], row["status"], row["notes"],
        )
        if key in existing_keys:
            result["duplicates"] += 1
            continue

        db.execute(
            """INSERT INTO collections
               (member_id, outstanding_balance, discount_pct, amount_paid,
                collection_date, method, status, notes, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (row["member_id"], row["outstanding_balance"], row["discount_pct"],
             row["amount_paid"], row["collection_date"], row["method"],
             row["status"], row["notes"]),
        )
        existing_keys.add(key)
        result["inserted"] += 1
        result[f"{entry['entry_type']}/{entry['source_type']}"] += 1

    return result, unmatched_refs, source_total_debit, source_total_credit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webapp-database", type=Path, default=DEFAULT_WEBAPP_DB)
    parser.add_argument("--database", type=Path, default=None,
                        help="target database (default: the active tenant's, from platform.db)")
    parser.add_argument("--tenant-slug", default="elev8")
    parser.add_argument("--commit", action="store_true", help="write the changes")
    args = parser.parse_args()

    if not args.webapp_database.is_file():
        raise SystemExit(f"WEBAPPL database not found: {args.webapp_database}")
    if args.database is None:
        args.database = _active_tenant_database(args.tenant_slug)
    if not args.database.is_file():
        raise SystemExit(f"Target database not found: {args.database}")

    print(f"Source (read-only): {args.webapp_database}")
    print(f"Target            : {args.database}\n")

    entries, source_member_refs = load_source_entries(args.webapp_database)
    print(f"Source rows (status posted/paid): {len(entries)}")

    db = sqlite3.connect(args.database)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    before = db.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
    backup = None
    if args.commit:
        backup_dir = args.database.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"{args.database.stem}_before_webapp_ledger_import_{stamp}.db"
        backup_conn = sqlite3.connect(backup)
        try:
            db.backup(backup_conn)
        finally:
            backup_conn.close()

    try:
        result, unmatched_refs, src_debit, src_credit = import_ledger(
            db, entries, source_member_refs
        )
        after = db.execute("SELECT COUNT(*) FROM collections").fetchone()[0]

        print(f"\nSource totals   : debit=R{src_debit:,.2f}  credit=R{src_credit:,.2f}")
        print("Import result   :", dict(result))
        print(f"Collection count: {before} -> {after}")
        if unmatched_refs:
            print(f"\nUnmatched member refs: {sum(unmatched_refs.values())} rows, "
                  f"{len(unmatched_refs)} distinct")
            for ref, count in unmatched_refs.most_common(15):
                print(f"  {ref}: {count}")

        if args.commit:
            db.commit()
            print(f"\nCommitted. Backup: {backup}")
        else:
            db.rollback()
            print("\nDRY RUN — no database changes were made. Re-run with --commit.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
