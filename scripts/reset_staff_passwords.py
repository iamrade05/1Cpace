#!/usr/bin/env python3
"""Reset every staff account to the default password.

Sets each account's password to admin.DEFAULT_STAFF_PASSWORD and marks it
must_change_password, so the default only survives until that person's next
sign-in — onecpase/auth.py refuses to serve any other page until they have
chosen their own.

Never touched:
  * accounts in admin.PROTECTED_USERNAMES (the owner account), so a mistaken
    run cannot lock the owner out of their own system;
  * platform power-user identities, which are administered separately.

Dry run unless --commit is given. A backup is written before any change.

    python scripts/reset_staff_passwords.py
    python scripts/reset_staff_passwords.py --commit
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

from onecpase.admin import (  # noqa: E402
    DEFAULT_STAFF_PASSWORD,
    PROTECTED_USERNAMES,
    is_protected_user,
)


def _active_tenant_database(slug: str) -> Path:
    """The database path the app serves for *slug*, straight from platform.db."""
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
    parser.add_argument(
        "--database", type=Path, default=None,
        help="target database (default: the active tenant's, from platform.db)",
    )
    parser.add_argument("--tenant-slug", default="elev8")
    parser.add_argument("--commit", action="store_true", help="write the changes")
    args = parser.parse_args()

    # Resolve the tenant the application actually serves rather than guessing a
    # file. Staff accounts live in the tenant database; picking the wrong file
    # silently resets nothing and reports success.
    if args.database is None:
        args.database = _active_tenant_database(args.tenant_slug)

    if not args.database.is_file():
        raise SystemExit(f"Database not found: {args.database}")

    db = sqlite3.connect(args.database)
    db.row_factory = sqlite3.Row
    try:
        users = db.execute(
            "SELECT id, username, full_name, role, active, "
            "COALESCE(is_platform_user, 0) AS is_platform_user FROM users ORDER BY role, username"
        ).fetchall()

        targets, skipped = [], []
        for user in users:
            if user["is_platform_user"]:
                skipped.append((user, "platform power user"))
            elif is_protected_user(user["username"]):
                skipped.append((user, "protected account"))
            else:
                targets.append(user)

        print(f"Database: {args.database}")
        print(f"Default password: {DEFAULT_STAFF_PASSWORD}\n")
        print(f"{'':2}{'username':<26}{'role':<12}{'active':<8}action")
        for user in targets:
            print(f"{'':2}{user['username']:<26}{user['role'] or '':<12}"
                  f"{'yes' if user['active'] else 'no':<8}reset + must change")
        for user, why in skipped:
            print(f"{'':2}{user['username']:<26}{user['role'] or '':<12}"
                  f"{'yes' if user['active'] else 'no':<8}SKIPPED — {why}")

        print(f"\n{len(targets)} to reset, {len(skipped)} skipped "
              f"(protected: {', '.join(sorted(PROTECTED_USERNAMES))})")

        if not args.commit:
            print("\nDRY RUN — no passwords were changed. Re-run with --commit.")
            return 0

        backup_dir = args.database.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"{args.database.stem}_before_password_reset_{datetime.now():%Y%m%d_%H%M%S}.db"
        shutil.copy2(args.database, backup)
        print(f"\nBackup: {backup}")

        # One hash per account rather than one shared hash: werkzeug salts each
        # call, so identical passwords must not produce identical hashes.
        for user in targets:
            db.execute(
                "UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
                (generate_password_hash(DEFAULT_STAFF_PASSWORD), user["id"]),
            )
        db.commit()
        print(f"Reset {len(targets)} accounts. Each must choose a new password at next sign-in.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
