"""Prove a pulled roster is complete before anything is imported.

Itensity's dashboard is the authority on how many people the club has. A pull
that is short — the Data Export's confirmed-only 1,892 against a real 2,049 —
must never reach the database, because a missing member reads downstream as a
cancelled one.

The check is per bucket, not on the total: two buckets wrong in opposite
directions still add up. There is no override flag; a short pull is a failed
pull.

Bucket rule, verified against a live pull on 2026-09-10:

    frozen people are counted on their own — the dashboard's Active chip
    excludes them — and everyone else falls into the chip named by their own
    ``status`` column (Active / Blocked / Inactive / Unverified).

Usage:
    python scripts/itensity_reconcile.py data/itensity_roster_<stamp>.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

CHIP_BUCKETS = ("active", "blocked", "inactive", "unverified")
FROZEN = "frozen"


class RosterMismatch(RuntimeError):
    """A pulled roster does not match Itensity's own counts, so it cannot be imported.

    A plain exception rather than SystemExit: the scheduler runs the same gate,
    and must be able to log the mismatch and skip the job without taking the
    process down.
    """


def bucket_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Count people the way the dashboard chips do."""
    counts: Counter[str] = Counter()
    for record in rows:
        roster = record.get("roster") or {}
        if str(roster.get("frozen") or "").strip().lower() == "frozen":
            counts[FROZEN] += 1
            continue
        counts[str(roster.get("status") or "").strip().lower() or "(blank)"] += 1
    return dict(counts)


def reconcile(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    rows = payload.get("rows") or []
    chips = (payload.get("snapshot") or {}).get("chips") or {}
    counts = bucket_counts(rows)
    lines: list[str] = []
    ok = True

    if not chips:
        return False, ["Itensity's own counts were not captured — nothing to reconcile against."]

    unexpected = set(counts) - set(CHIP_BUCKETS) - {FROZEN}
    if unexpected:
        ok = False
        lines.append(f"Unrecognised status values in the roster: {sorted(unexpected)}")

    lines.append(f"{'bucket':12} {'pulled':>8} {'Itensity':>9} {'diff':>6}")
    for bucket in CHIP_BUCKETS:
        pulled = counts.get(bucket, 0)
        expected = int(chips.get(bucket, 0))
        diff = pulled - expected
        if diff:
            ok = False
        lines.append(f"{bucket:12} {pulled:>8} {expected:>9} {diff:>+6}")

    frozen = counts.get(FROZEN, 0)
    lines.append(f"{'frozen':12} {frozen:>8} {'(separate)':>9} {'':>6}")

    # The chips total the four buckets; frozen people sit outside them.
    pulled_total = len(rows)
    expected_total = int(chips.get("total", 0)) + frozen
    if pulled_total != expected_total:
        ok = False
    lines.append(
        f"{'TOTAL':12} {pulled_total:>8} {expected_total:>9} {pulled_total - expected_total:>+6}"
    )

    roles = Counter(str((r.get("roster") or {}).get("role") or "") for r in rows)
    thin = sum(1 for r in rows if not r.get("sources"))
    lines.append("")
    lines.append(f"people {pulled_total} = members {roles.get('Member', 0)} + staff "
                 f"{pulled_total - roles.get('Member', 0)}; {thin} have no detail row "
                 f"(roster fields only)")
    return ok, lines


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roster", type=Path, help="data/itensity_roster_<stamp>.json")
    args = parser.parse_args()

    payload = load(args.roster)
    ok, lines = reconcile(payload)
    print(f"Roster pulled {payload.get('pulled_at', '?')}")
    print("\n".join(lines))
    if ok:
        print("\nPASS — the pull matches Itensity bucket for bucket. Safe to import.")
        return
    print("\nFAIL — the pull does not match Itensity. Nothing may be imported from it.")
    sys.exit(1)


if __name__ == "__main__":
    main()
