#!/usr/bin/env python3
"""Correct staff departments and create the staff accounts that were missing.

The users table separates two things that are easy to conflate: ``role`` is the
permission level the application enforces (admin / manager / staff / trainer /
reception) and ``department`` is the job, which is free text — "Kiddie's Corner"
was already in use. Job titles like Cleaner or Group Instructor are departments,
not roles; giving someone role='trainer' is what grants them trainer screens.

New accounts get the default staff password and must change it at first
sign-in, exactly as scripts/reset_staff_passwords.py does.

Dry run unless --commit is given. A backup is written before any change.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash  # noqa: E402

from onecpase.admin import DEFAULT_STAFF_PASSWORD  # noqa: E402
from onecpase.auth import valid_email_address  # noqa: E402

# username -> department. Roles already match and are left alone.
DEPARTMENT_FIXES = {
    "morongoe": "Cleaner",
    "tanki": "Cleaner",
    "louisa": "Group Instructor",
    "mduduzi": "Group Instructor",
    "lindelani": "Fitness Instructor",
}

# Staff present in Itensity but with no sign-in account.
# (username, full_name, role, department, contact, staff_id, sales_consultant)
NEW_STAFF = [
    ("melizwe",  "Melizwe Dlamini",         "trainer", "Group Instructor", "0617545530", "2159875", False),
    ("sibusiso", "Sibusiso Mabaso",         "trainer", "Group Instructor", "0738771601", "2156964", False),
    ("tholoane", "Tholoane Mashiane",       "staff",   "Sales",            "0829643615", "1906107", True),
    ("kamohelo", "Kamohelo Thabo Tsotetsi", "staff",   "Sales",            "0678209095", "2143602", True),
    ("crosby",   "Crosby Machate",          "trainer", "Group Instructor", "0835332457", "2137452", False),
]


def _active_tenant_database(slug: str) -> Path:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--tenant-slug", default="elev8")
    parser.add_argument(
        "--email", action="append", default=[], metavar="USERNAME=ADDRESS",
        help="email address for a new account; repeat once per account being created",
    )
    parser.add_argument("--commit", action="store_true", help="write the changes")
    args = parser.parse_args()

    emails = {}
    for item in args.email:
        username, separator, address = item.partition("=")
        username = username.strip().lower()
        address = address.strip()
        if not separator or not username or not valid_email_address(address):
            raise SystemExit("Each --email must be USERNAME=valid-address.")
        if username not in {staff[0] for staff in NEW_STAFF}:
            raise SystemExit(f"--email username {username!r} is not in the new staff list.")
        emails[username] = address

    if args.database is None:
        args.database = _active_tenant_database(args.tenant_slug)
    if not args.database.is_file():
        raise SystemExit(f"Database not found: {args.database}")

    db = sqlite3.connect(args.database)
    db.row_factory = sqlite3.Row
    try:
        print(f"Database: {args.database}\n")

        print("Department corrections")
        updates = []
        for username, department in DEPARTMENT_FIXES.items():
            row = db.execute(
                "SELECT id, department, role FROM users WHERE username=?", (username,)
            ).fetchone()
            if row is None:
                print(f"  {username:<12} NOT FOUND — skipped")
                continue
            if (row["department"] or "") == department:
                print(f"  {username:<12} {department:<20} already set")
                continue
            print(f"  {username:<12} {row['department'] or '—':<20} -> {department}")
            updates.append((department, row["id"]))

        print("\nNew staff accounts")
        creates = []
        missing_email = []
        for username, full_name, role, dept, contact, staff_id, consultant in NEW_STAFF:
            if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                print(f"  {username:<12} already exists — skipped")
                continue
            tag = " (sales rotation)" if consultant else ""
            print(f"  {username:<12} {full_name:<26} {role:<9} {dept}{tag}")
            email = emails.get(username, "")
            if not valid_email_address(email):
                print(f"  {username:<12} EMAIL REQUIRED — supply --email {username}=address")
                missing_email.append(username)
                continue
            creates.append((username, full_name, role, dept, contact, email, staff_id, consultant))

        # Rotation positions continue after the existing consultants.
        next_position = (db.execute(
            "SELECT COALESCE(MAX(sales_rotation_order), 0) FROM users WHERE is_sales_consultant=1"
        ).fetchone()[0] or 0) + 1

        print(f"\n{len(updates)} departments to change, {len(creates)} accounts to create")
        if missing_email:
            print(f"{len(missing_email)} new account(s) still need email addresses.")
        if not args.commit:
            print("\nDRY RUN — nothing was changed. Re-run with --commit.")
            return 0
        if missing_email:
            raise SystemExit("No changes made. Supply real email addresses for every new account before --commit.")

        backup_dir = args.database.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"{args.database.stem}_before_staff_sync_{datetime.now():%Y%m%d_%H%M%S}.db"
        shutil.copy2(args.database, backup)
        print(f"\nBackup: {backup}")

        for department, uid in updates:
            db.execute("UPDATE users SET department=? WHERE id=?", (department, uid))

        for username, full_name, role, dept, contact, email, staff_id, consultant in creates:
            position = None
            if consultant:
                position = next_position
                next_position += 1
            db.execute(
                """INSERT INTO users
                   (username, password_hash, full_name, role, department, contact, email,
                    active, must_change_password, staff_id, is_sales_consultant,
                    sales_available, sales_rotation_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, 0, ?)""",
                (username, generate_password_hash(DEFAULT_STAFF_PASSWORD), full_name,
                 role, dept, contact, email, staff_id, 1 if consultant else 0, position),
            )
        db.commit()
        print(f"Updated {len(updates)} departments, created {len(creates)} accounts.")
        print(f"New accounts sign in with {DEFAULT_STAFF_PASSWORD} and must change it.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
