#!/usr/bin/env python3
"""Shorten sign-in usernames from firstname.lastname to firstname.

Usernames are what people type to sign in, and the column is UNIQUE, so this
refuses to change anything at all if two accounts would end up with the same
name. A partial rename is worse than none: half the staff would be unable to
sign in and the other half would not know why.

Full names, roles, permissions and passwords are untouched — only the
``users.username`` column changes.

Dry run unless --commit is given. A backup is written before any change.

    python scripts/shorten_usernames.py
    python scripts/shorten_usernames.py --commit
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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


def shorten(username: str) -> str:
    """firstname.lastname -> firstname. Anything without a dot is left alone."""
    return str(username or "").strip().lower().split(".")[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--tenant-slug", default="elev8")
    parser.add_argument("--commit", action="store_true", help="write the changes")
    args = parser.parse_args()

    if args.database is None:
        args.database = _active_tenant_database(args.tenant_slug)
    if not args.database.is_file():
        raise SystemExit(f"Database not found: {args.database}")

    db = sqlite3.connect(args.database)
    db.row_factory = sqlite3.Row
    try:
        users = db.execute(
            "SELECT id, username, full_name, role FROM users ORDER BY id"
        ).fetchall()

        planned = [(u, shorten(u["username"])) for u in users]
        counts = Counter(new for _u, new in planned)
        clashes = {name: n for name, n in counts.items() if n > 1}

        print(f"Database: {args.database}\n")
        print(f"  {'current':<24} {'new':<14} {'full name':<28} role")
        for user, new in planned:
            note = ""
            if new in clashes:
                note = "   <-- CLASH"
            elif new == user["username"]:
                note = "   (unchanged)"
            print(f"  {user['username']:<24} {new:<14} "
                  f"{(user['full_name'] or ''):<28} {user['role'] or ''}{note}")

        if clashes:
            print(f"\nREFUSED — these names would collide: "
                  f"{', '.join(sorted(clashes))}. Nothing was changed.")
            return 1

        changing = [(u, new) for u, new in planned if new != u["username"]]
        print(f"\n{len(changing)} to rename, {len(planned) - len(changing)} already short")

        if not args.commit:
            print("\nDRY RUN — no usernames were changed. Re-run with --commit.")
            return 0

        backup_dir = args.database.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"{args.database.stem}_before_username_shorten_{datetime.now():%Y%m%d_%H%M%S}.db"
        shutil.copy2(args.database, backup)
        print(f"\nBackup: {backup}")

        for user, new in changing:
            db.execute("UPDATE users SET username=? WHERE id=?", (new, user["id"]))
        db.commit()
        print(f"Renamed {len(changing)} accounts.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
