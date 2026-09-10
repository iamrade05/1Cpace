"""Import Itensity members into 1Cpase with deterministic statuses.

Takes either a roster pulled by scripts/pull_itensity_roster.py (.json — the
full ~2,049 people) or a legacy Data Export workbook (.xlsx — the confirmed
1,892 only). A roster is refused unless scripts/itensity_reconcile.py says it
matches Itensity bucket for bucket.

The import is a dry run unless --commit is supplied. Existing records are
matched by ID-number hash first and Itensity ref second.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onecpase.encryption import encrypt_member, hash_for_lookup  # noqa: E402

try:  # run as a script (scripts/ on sys.path) or imported as scripts.<module>
    from itensity_reconcile import RosterMismatch, reconcile
except ImportError:  # pragma: no cover - import path differs under the scheduler
    from scripts.itensity_reconcile import (  # type: ignore[no-redef]
        RosterMismatch,
        reconcile,
    )


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def iso_date(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return text(value)


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def truthy(value: Any) -> bool:
    return text(value).lower() in {"1", "true", "yes", "y", "blocked"}


def imported_status(row: dict[str, Any]) -> str:
    if text(row.get("Member Status")).lower() != "active":
        return "Inactive"
    return "Blocked" if truthy(row.get("Blocked Status")) else "Active"


def member_values(row: dict[str, Any]) -> dict[str, Any]:
    tariff = text(row.get("Tariff Name"))
    return {
        "first_name": text(row.get("Member Name")),
        "last_name": text(row.get("Member Surname")),
        "id_number": text(row.get("ID Number")),
        "contact": text(row.get("Cell")),
        "email": text(row.get("Email")),
        "date_of_birth": iso_date(row.get("DoB")),
        "gender": text(row.get("Gender")),
        "join_date": iso_date(row.get("Date Joined")),
        "member_status": imported_status(row),
        "package": tariff,
        "tariff": tariff,
        "monthly_installment": number(row.get("Monthly Membership")),
        "source": "Itensity Import",
        "entry_type": "import",
        "outcome": "joined",
        "street_name": text(row.get("Address")),
        "country": "South Africa",
        "payment_type": text(row.get("Payment Type")),
        "debit_order_date": text(row.get("Debit Date")),
        "bank": text(row.get("Bank Name")),
        "account_name": text(row.get("Acc Name")),
        "account_number": text(row.get("Acc No")),
        "branch_code": text(row.get("Branch Code")),
        "payer_name": text(row.get("Acc Name")),
        "itensity_ref": text(row.get("Itensity Acc Number")),
        "occupation": text(row.get("Occupation")),
        "emergency_contact_name": text(row.get("Emergency Contact Name")),
        "emergency_contact_number": text(row.get("Emergency Contact Number")),
        "emergency_contact_relation": text(row.get("Emergency Contact Relationship")),
    }


# ── Roster source (scripts/pull_itensity_roster.py) ──────────────────────────

SA_ID = re.compile(r"\d{13}")
ROSTER_STATUSES = {
    "active": "Active",
    "blocked": "Blocked",
    "inactive": "Inactive",
    "unverified": "Unverified",
}


def split_name(full_name: str) -> tuple[str, str]:
    """The roster carries one name field where the detail reports carry two.

    Itensity writes the surname last, so everything before it is given names.
    """
    parts = text(full_name).split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def phoneish(value: Any) -> bool:
    return len(re.sub(r"\D", "", text(value))) >= 9


def na_blank(value: Any) -> str:
    """Itensity writes the literal string 'N/A' where it means nothing."""
    cleaned = text(value)
    return "" if cleaned.upper() in {"N/A", "NA", "NONE"} else cleaned


def pick_id_number(record: dict[str, Any], ref: str) -> tuple[str, bool]:
    """Return (id_number, is_synthetic).

    Itensity stores the SA ID in its *email* column — 1,840 of 1,869 confirmed
    members are a bare 13-digit ID there and none contains an '@'. Where no ID
    was ever captured, a deterministic ``ITN-<ref>`` stands in: V8 requires a
    unique id_number, and deriving it from the Itensity ref keeps a re-import
    updating the same row instead of creating a second one. The prefix keeps
    these unmistakable against real IDs.
    """
    for source in (record.get("personal"), record.get("roster")):
        candidate = text((source or {}).get("email"))
        if SA_ID.fullmatch(candidate):
            return candidate, False
    return f"ITN-{ref}", True


def pick_email(record: dict[str, Any]) -> str:
    """The actual email address, which Itensity keeps outside its email column."""
    for candidate in (
        text((record.get("roster") or {}).get("id")),
        text((record.get("extract") or {}).get("add4")),
    ):
        if "@" in candidate:
            return candidate
    return ""


def roster_member_values(record: dict[str, Any]) -> dict[str, Any]:
    """One member's fields, drawn from whichever reports described them.

    Only the confirmed ~1,869 appear in the detail reports; the rest are known
    from the roster alone, so banking and personal fields stay blank rather
    than being guessed at. The roster's own account number is masked
    ("147****099") and is never used.
    """
    roster = record.get("roster") or {}
    personal = record.get("personal") or {}
    membership = record.get("membership") or {}
    banking = record.get("banking") or {}
    extract = record.get("extract") or {}
    ref = text(record.get("ref"))

    first_name = text(personal.get("name"))
    last_name = text(personal.get("surname"))
    if not (first_name and last_name):
        first_name, last_name = split_name(roster.get("name"))

    id_number, synthetic = pick_id_number(record, ref)
    tariff = text(roster.get("tariff")) or text(membership.get("tarname"))

    # Two custom fields hold the emergency contact, and staff have filled them
    # in both orders — so read them by shape, not by position.
    contact_a, contact_b = text(extract.get("add20")), text(extract.get("add21"))
    if phoneish(contact_a) and not phoneish(contact_b):
        emergency_name, emergency_number = contact_b, contact_a
    else:
        emergency_name, emergency_number = contact_a, contact_b

    return {
        "first_name": first_name,
        "last_name": last_name,
        "id_number": id_number,
        "contact": text(roster.get("cell")) or text(personal.get("cell")),
        "email": pick_email(record),
        "date_of_birth": iso_date(personal.get("dob")),
        "gender": text(personal.get("gender")),
        "join_date": iso_date(roster.get("start"))[:10],
        "member_status": ROSTER_STATUSES.get(text(roster.get("status")).lower(), "Unverified"),
        "package": tariff,
        "tariff": tariff,
        "monthly_installment": number(roster.get("pay")),
        "source": "Itensity Import",
        "entry_type": "import",
        "outcome": "joined",
        "street_name": text(personal.get("address")),
        "country": "South Africa",
        "payment_type": text(roster.get("Payment")),
        "debit_order_date": na_blank(banking.get("debitdate")),
        "bank": na_blank(banking.get("bank")),
        "account_name": na_blank(banking.get("accname")),
        "account_number": na_blank(banking.get("accno")),
        "branch_code": na_blank(banking.get("branch")),
        "payer_name": na_blank(banking.get("accname")),
        "itensity_ref": ref,
        "occupation": text(extract.get("add25")),
        "emergency_contact_name": emergency_name,
        "emergency_contact_number": emergency_number,
        "emergency_contact_relation": text(extract.get("add334")),
        "_synthetic_id": synthetic,
    }


def read_roster(path: Path) -> tuple[list[dict[str, Any]], Counter, list[dict[str, str]]]:
    """Read a pulled roster, but only if it reconciles with Itensity's own counts."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    ok, lines = reconcile(payload)
    print("\n".join(lines))
    if not ok:
        raise RosterMismatch(
            "This roster does not match Itensity bucket for bucket, so nothing may "
            "be imported from it. Pull it again with scripts/pull_itensity_roster.py.\n"
            + "\n".join(lines)
        )

    members: list[dict[str, Any]] = []
    skipped: Counter = Counter()
    synthetic: list[dict[str, str]] = []
    seen: set[str] = set()

    for record in payload.get("rows") or []:
        if text((record.get("roster") or {}).get("role")).lower() != "member":
            skipped["non_member_role"] += 1
            continue
        member = roster_member_values(record)
        if not member["first_name"] or not member["last_name"]:
            skipped["missing_name"] += 1
            continue
        if member["id_number"] in seen:
            skipped["duplicate_id_in_roster"] += 1
            continue
        seen.add(member["id_number"])
        if member.pop("_synthetic_id"):
            synthetic.append(
                {
                    "name": f"{member['first_name']} {member['last_name']}".strip(),
                    "itensity_ref": member["itensity_ref"],
                    "id_number": member["id_number"],
                    "status": member["member_status"],
                }
            )
        members.append(member)
    return members, skipped, synthetic


def read_members(workbook: Path) -> tuple[list[dict[str, Any]], Counter]:
    sheet = load_workbook(workbook, data_only=True).active
    headers = [text(cell.value) for cell in sheet[1]]
    rows: list[dict[str, Any]] = []
    skipped = Counter()
    seen_ids: set[str] = set()

    for values in sheet.iter_rows(min_row=2, values_only=True):
        row = dict(zip(headers, values))
        if not any(value is not None for value in values):
            continue
        if text(row.get("Role")).lower() != "member":
            skipped["non_member_role"] += 1
            continue
        member = member_values(row)
        if not member["id_number"]:
            skipped["missing_id_number"] += 1
            continue
        if not member["first_name"] or not member["last_name"]:
            skipped["missing_name"] += 1
            continue
        if member["id_number"] in seen_ids:
            skipped["duplicate_id_in_workbook"] += 1
            continue
        seen_ids.add(member["id_number"])
        rows.append(member)
    return rows, skipped


def import_members(db: sqlite3.Connection, members: list[dict[str, Any]]) -> Counter:
    result = Counter()
    for member in members:
        # PII is stored encrypted; the hash is the only way to match on an ID,
        # because the same ID encrypts to different ciphertext every time.
        values = encrypt_member(member)
        by_id = db.execute(
            "SELECT id FROM members WHERE id_number_hash = ?",
            (hash_for_lookup(member["id_number"]),),
        ).fetchone()
        by_ref = None
        if member["itensity_ref"]:
            by_ref = db.execute(
                "SELECT id FROM members WHERE itensity_ref = ?", (member["itensity_ref"],)
            ).fetchone()
        if by_id and by_ref and by_id[0] != by_ref[0]:
            result["conflicting_match"] += 1
            continue

        existing = by_id or by_ref
        columns = list(values)
        if existing:
            assignments = ", ".join(f"{column} = ?" for column in columns)
            db.execute(
                f"UPDATE members SET {assignments} WHERE id = ?",
                [values[column] for column in columns] + [existing[0]],
            )
            member_id = existing[0]
            result["updated"] += 1
        else:
            placeholders = ", ".join("?" for _ in columns)
            cursor = db.execute(
                f"INSERT INTO members ({', '.join(columns)}) VALUES ({placeholders})",
                [values[column] for column in columns],
            )
            member_id = cursor.lastrowid
            result["inserted"] += 1

        # These are established Itensity profiles, not new applications awaiting checks.
        application = db.execute(
            "SELECT id FROM membership_applications WHERE member_id = ?", (member_id,)
        ).fetchone()
        if application:
            db.execute(
                """UPDATE membership_applications
                   SET package = ?, application_status = 'active', push_status = 'pushed',
                       updated_at = datetime('now') WHERE id = ?""",
                (member["package"], application[0]),
            )
        else:
            db.execute(
                """INSERT INTO membership_applications
                   (member_id, package, application_status, push_status, pushed_at)
                   VALUES (?, ?, 'active', 'pushed', datetime('now'))""",
                (member_id, member["package"]),
            )
        result[member["member_status"]] += 1
    return result


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", type=Path,
        help="a roster .json from pull_itensity_roster.py, or a legacy Data Export .xlsx",
    )
    parser.add_argument("--database", type=Path, default=root / "1cpase.db")
    parser.add_argument("--commit", action="store_true", help="Write changes to the database")
    args = parser.parse_args()

    synthetic: list[dict[str, str]] = []
    if args.source.suffix.lower() == ".json":
        try:
            members, skipped, synthetic = read_roster(args.source)
        except RosterMismatch as exc:
            sys.exit(f"\nRefusing to import — {exc}")
    else:
        members, skipped = read_members(args.source)
        print(
            "NOTE: a Data Export workbook holds only Itensity's confirmed memberships "
            "(~1,892 of 2,049). Prefer a roster pulled by scripts/pull_itensity_roster.py."
        )

    print(f"\nEligible member rows: {len(members)}")
    print("Calculated statuses:", dict(Counter(m["member_status"] for m in members)))
    print("Skipped rows:", dict(skipped))
    if synthetic:
        print(
            f"\n{len(synthetic)} members have no SA ID in Itensity and were given a "
            "placeholder ID (ITN-<ref>) — correct these in Itensity, then re-import:"
        )
        for row in synthetic:
            print(f"  {row['id_number']:14} {row['status']:11} {row['name']}")

    db = sqlite3.connect(args.database)
    db.execute("PRAGMA foreign_keys = ON")
    before = db.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    backup = None
    if args.commit:
        backup_dir = root / "backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"1cpase_before_member_import_{stamp}.db"
        backup_db = sqlite3.connect(backup)
        try:
            db.backup(backup_db)
        finally:
            backup_db.close()
    try:
        result = import_members(db, members)
        after = db.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        print("Import result:", dict(result))
        print(f"Member count: {before} -> {after}")
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
