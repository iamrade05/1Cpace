#!/usr/bin/env python3
"""Synchronise Elev8 dbo.Members into the active 1Cpace tenant.

Default mode is dry-run. Use --apply to write. A backup is created immediately
before an apply operation.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from onecpase.config import Config
from onecpase.elev8_data import _connection_string
from onecpase.elev8_sync import fetch_source_members, open_source_connection, reconcile_source, sync_source_members


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the synchronisation")
    parser.add_argument("--db", default="", help="target 1Cpace SQLite database (defaults to active Elev8 tenant)")
    parser.add_argument("--tenant", default="Elev8 Health & Fitness")
    parser.add_argument("--tenant-slug", default="elev8")
    args = parser.parse_args()

    target_db = args.db
    if not target_db:
        platform = sqlite3.connect(Config.PLATFORM_DATABASE_PATH)
        platform.row_factory = sqlite3.Row
        try:
            tenant = platform.execute(
                "SELECT name, db_path FROM tenants WHERE slug=? AND active=1",
                (args.tenant_slug,),
            ).fetchone()
        finally:
            platform.close()
        if not tenant:
            raise SystemExit(f"Active tenant not found: {args.tenant_slug}")
        target_db = tenant["db_path"]
        # Tenant DB paths in platform.db may be absolute or relative to the V8 root.
        target_path = Path(target_db)
        if not target_path.is_absolute():
            target_path = ROOT / target_path
        target_db = str(target_path.resolve())
        args.tenant = tenant["name"]

    source = open_source_connection(_connection_string())
    try:
        source_members = fetch_source_members(source)
    finally:
        source.close()

    db = sqlite3.connect(target_db)
    db.row_factory = sqlite3.Row
    try:
        reconciliation = reconcile_source(db, source_members)
        print("=== ELEV8 MEMBER SYNCHRONISATION ===")
        print(f"Source members:       {reconciliation['source_total']}")
        print(f"Local members:        {reconciliation['local_total']}")
        print(f"Already matched:      {reconciliation['matched']}")
        print(f"Missing locally:      {reconciliation['missing_in_local']}")
        print(f"Local not in source:  {reconciliation['local_not_in_source']}")
        print(f"Conflicts:             {len(reconciliation['conflicts'])}")

        if not args.apply:
            print("\nDRY RUN — no local records changed.")
            return 0

        backup_dir = Path(target_db).resolve().parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"elev8_members_before_sync_{datetime.now():%Y%m%d_%H%M%S}.db"
        shutil.copy2(target_db, backup)
        print(f"Backup: {backup}")

        result = sync_source_members(db, source_members, args.tenant)
        db.commit()
        print(f"Inserted:             {result['inserted']}")
        print(f"Updated:              {result['updated']}")
        print(f"Unchanged:            {result['unchanged']}")
        print(f"Write conflicts:      {len(result['conflicts'])}")
        print("SYNC COMPLETE")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
