"""NuPay reconciliation: store the uploaded Transaction Report, compare every member, answer for one.

The uploaded rows live in ``nupay_transactions``; the comparison of all members lives in
``reconciliation_snapshot`` and is rebuilt on demand. Neither touches a member's collections
history - recording payments there is a separate, explicit step (``nupay_transactions.apply_plan``).

The "after" balance is worked out in memory by laying the would-be rows on top of the member's real
ones and running the same payment-matching the member profile uses, so it never needs a write.
"""
from __future__ import annotations

import io
import json
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from . import member_audit as audit
from . import nupay_transactions as nt
from .debicheck import itensity_client_ref_number, normalise_debicheck_status
from .nupay_sync import _member_for_report_row, _member_indexes

KEEP = {nt.SUCCESS, nt.FAILED, "disputed"}
MANDATE_RANK = {"approved": 0, "submitted": 1, "pending": 1, "reviewing": 1, "processing": 1, "unknown": 2, "failed": 3}
CHUNK = 400

GROUP_LABELS = {
    audit.PAYING_ARREARS_CLEAR: ("Paying on NuPay, arrears clear", "ok"),
    audit.PAYING_STILL_OWES: ("Paying on NuPay, still owes", "warn"),
    audit.FAILING_DEBITS: ("Failing debits", "bad"),
    audit.MANDATE_NO_RECENT_DEBIT: ("Mandate, no recent debit", "warn"),
    audit.NO_MANDATE: ("No NuPay mandate", "warn"),
    audit.EFT_CASH: ("EFT or cash", "mute"),
    audit.SUB_ACCOUNT: ("Sub-account", "mute"),
    audit.NOT_IN_ITENSITY: ("Not in Itensity", "mute"),
    audit.INACTIVE_UNVERIFIED: ("Inactive in Itensity", "mute"),
}
GROUP_ORDER = list(GROUP_LABELS)


# ── Reading an upload ────────────────────────────────────────────────────────

def read_upload(files: Iterable[tuple[str, bytes]]) -> list[dict[str, Any]]:
    """Rows from uploaded CSV files, or CSVs inside a zip. Anything else is ignored."""
    rows: list[dict[str, Any]] = []
    for name, data in files:
        lower = name.lower()
        if lower.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                for member in bundle.namelist():
                    if member.lower().endswith(".csv"):
                        rows.extend(nt.parse_csv(bundle.read(member).decode("utf-8-sig", errors="replace")))
        elif lower.endswith(".csv"):
            rows.extend(nt.parse_csv(data.decode("utf-8-sig", errors="replace")))
    return rows


def store_upload(db, rows: list[dict[str, Any]], filenames: list[str], user_id: int | None) -> dict[str, int]:
    """Keep each Success, Failed or Disputed debit once. Re-uploading the same report adds nothing."""
    by_ref, by_id = _member_indexes(db)
    existing = {r["txn_key"] for r in db.execute("SELECT txn_key FROM nupay_transactions")}
    run_id = db.execute(
        "INSERT INTO nupay_import_runs (uploaded_by, filenames, rows_read) VALUES (?, ?, ?)",
        (user_id, ", ".join(filenames)[:500], len(rows)),
    ).lastrowid
    stats = Counter()
    for raw in rows:
        row = nt.normalise_row(raw)
        status = nt._status(row)
        if status not in KEEP:
            stats["ignored"] += 1
            continue
        key = nt.transaction_key(row) or ""
        action = nt._day(row.get("action_date"))
        if not key or not action:
            stats["unreadable"] += 1
            continue
        if key in existing:
            stats["already_stored"] += 1
            continue
        existing.add(key)
        member = _member_for_report_row(row, by_ref, by_id)
        db.execute(
            """INSERT INTO nupay_transactions (run_id, txn_key, member_id, mandate_id, client_reference,
                   contract_reference, instalment, total_instalments, amount, action_date, cycle_date, status, tracking)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, key, member["id"] if member else None, nt._text(row.get("mandate_id")),
             nt._text(row.get("client_reference")), nt._text(row.get("contract_reference")),
             _int(row.get("instalment")), _int(row.get("total_instalments")), nt._money(row.get("instalment_amount")),
             action, nt._day(row.get("cycle_date")), status, nt._text(row.get("tracking"))),
        )
        stats["new"] += 1
        if member is None:
            stats["unmatched"] += 1
    db.execute("UPDATE nupay_import_runs SET rows_new=? WHERE id=?", (stats["new"], run_id))
    return dict(stats)


def _int(value: Any) -> int | None:
    try:
        return int(float(nt._text(value)))
    except ValueError:
        return None


def stored_rows(db, member_id: int | None = None) -> list[dict[str, Any]]:
    """Stored debits shaped like the report rows that ``nupay_transactions.plan_import`` reads."""
    sql = "SELECT * FROM nupay_transactions"
    params: tuple = ()
    if member_id is not None:
        sql += " WHERE member_id = ?"
        params = (member_id,)
    return [
        {"mandate_id": r["mandate_id"], "client_reference": r["client_reference"],
         "contract_reference": r["contract_reference"], "instalment": r["instalment"],
         "total_instalments": r["total_instalments"], "instalment_amount": r["amount"],
         "action_date": r["action_date"], "cycle_date": r["cycle_date"], "status": r["status"]}
        for r in db.execute(sql, params)
    ]


# ── Itensity and mandates ────────────────────────────────────────────────────

def latest_roster(root: Path) -> dict[str, dict[str, Any]] | None:
    """The newest nightly Itensity roster, keyed by Itensity number. None when there is no file."""
    files = sorted((root / "data").glob("itensity_roster_*.json"))
    if not files:
        return None
    try:
        rows = json.loads(files[-1].read_text(encoding="utf-8"))["rows"]
    except (OSError, ValueError, KeyError):
        return None
    return {itensity_client_ref_number(str(r.get("ref"))): (r.get("roster") or {}) for r in rows}


def _best_mandates(db) -> dict[int, str]:
    best: dict[int, str] = {}
    for row in db.execute("SELECT member_id, status FROM debicheck_mandates"):
        status = normalise_debicheck_status(row["status"])
        old = best.get(row["member_id"])
        if old is None or MANDATE_RANK.get(status, 2) < MANDATE_RANK.get(old, 2):
            best[row["member_id"]] = status
    return best


# ── Arrears with and without the would-be rows ───────────────────────────────

def _profile_arrears(rows: list[Any]) -> tuple[float, int]:
    from .members import _build_payment_profile
    from .ptp import compute_arrears_from_profile

    info = compute_arrears_from_profile(_build_payment_profile(rows))
    return round(float(info["total_arrears"] or 0), 2), int(info["months_in_arrears"] or 0)


def _synthetic(plan: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """The rows ``apply_plan`` would write, per member, as plain dicts."""
    out: dict[int, list[dict[str, Any]]] = {}
    for n, action in enumerate(plan["actions"], start=10**9):
        if action["op"] == "charge":
            row = {"outstanding_balance": action["amount"], "amount_paid": 0, "collection_date": action["date"],
                   "status": action["status"], "notes": action["notes"]}
        elif action["op"] == "payment":
            row = {"outstanding_balance": 0, "amount_paid": action["amount"], "collection_date": action["date"],
                   "status": "paid", "notes": action["notes"]}
        else:
            continue
        row.update(id=n, member_id=action["member_id"], method="debicheck")
        out.setdefault(action["member_id"], []).append(row)
    return out


def _collections_for(db, ids: list[int]) -> dict[int, list[Any]]:
    grouped: dict[int, list[Any]] = {}
    for start in range(0, len(ids), CHUNK):
        part = ids[start:start + CHUNK]
        for row in db.execute(
            f"SELECT * FROM collections WHERE member_id IN ({','.join('?' for _ in part)}) "
            "ORDER BY collection_date DESC, id DESC", part,
        ):
            grouped.setdefault(row["member_id"], []).append(row)
    return grouped


# ── Snapshot of every member ─────────────────────────────────────────────────

def rebuild_snapshot(db, roster: dict[str, dict[str, Any]] | None, *, today: date | None = None, window_days: int = 45) -> dict[str, Any]:
    """Compare every member and replace the stored snapshot. The caller commits."""
    from .ptp import bulk_member_arrears

    today = today or date.today()
    rows = stored_rows(db)
    plan = nt.plan_import(db, rows)
    synthetic = _synthetic(plan)
    affected = sorted(synthetic)
    real = _collections_for(db, affected)

    members = db.execute(
        "SELECT id, member_status, gym_access_status, itensity_ref FROM members ORDER BY id"
    ).fetchall()
    ids = [m["id"] for m in members]
    bulk = bulk_member_arrears(db, ids)
    mandates = _best_mandates(db)
    last_success: dict[int, str] = {}
    last_failed: dict[int, str] = {}
    for r in db.execute(
        "SELECT member_id, status, MAX(action_date) AS d FROM nupay_transactions WHERE member_id IS NOT NULL GROUP BY member_id, status"
    ):
        target = last_success if r["status"] == nt.SUCCESS else last_failed if r["status"] == nt.FAILED else None
        if target is not None:
            target[r["member_id"]] = r["d"]

    db.execute("DELETE FROM reconciliation_snapshot")
    counts: Counter = Counter()
    for m in members:
        mid = m["id"]
        if mid in synthetic:
            now_amt, now_months = _profile_arrears(real.get(mid, []))
            after_amt, after_months = _profile_arrears(real.get(mid, []) + synthetic[mid])
        else:
            info = bulk.get(mid, {})
            now_amt = after_amt = round(float(info.get("total_arrears", 0) or 0), 2)
            now_months = after_months = int(info.get("months_in_arrears", 0) or 0)
        it = (roster or {}).get(itensity_client_ref_number(m["itensity_ref"])) if roster is not None else None
        if roster is None:
            # No Itensity file yet: judge only what NuPay itself can say.
            it = {"status": "Active", "Payment": "Debit Order", "main_mem": "Main Member"}
        group = audit.classify(
            itensity=it, mandate_status=mandates.get(mid), last_success=last_success.get(mid),
            last_failed=last_failed.get(mid), arrears_after=after_amt, today=today, window_days=window_days,
        )
        flags = audit.mismatch_flags(beta_status=m["member_status"], beta_access=m["gym_access_status"],
                                     itensity=it if roster is not None else None, group=group)
        db.execute(
            """INSERT INTO reconciliation_snapshot (member_id, grp, arrears_now, months_now, arrears_after, months_after,
                   last_success, last_failed, mandate_status, itensity_status, itensity_payment, flags)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, group, now_amt, now_months, after_amt, after_months, last_success.get(mid), last_failed.get(mid),
             mandates.get(mid), (it or {}).get("status") if roster is not None else None,
             (it or {}).get("Payment") if roster is not None else None, "; ".join(flags)),
        )
        counts[group] += 1
    return {"members": len(members), "groups": dict(counts), "roster": roster is not None,
            "payments_to_add": plan["summary"].get("payments_added", 0)}


def overview(db) -> dict[str, Any]:
    """Headline figures and the per-group table, read from the stored snapshot."""
    head = db.execute(
        "SELECT COUNT(*) n, MAX(computed_at) at FROM reconciliation_snapshot"
    ).fetchone()
    if not head["n"]:
        return {"ready": False, "runs": _runs(db)}
    groups = {
        r["grp"]: r for r in db.execute(
            """SELECT grp, COUNT(*) n, SUM(arrears_now) now, SUM(arrears_after) after
               FROM reconciliation_snapshot GROUP BY grp"""
        )
    }
    table = []
    for key in GROUP_ORDER:
        g = groups.get(key)
        if g:
            table.append({"key": key, "label": GROUP_LABELS[key][0], "tone": GROUP_LABELS[key][1], "members": g["n"],
                          "now": round(g["now"] or 0, 2), "after": round(g["after"] or 0, 2), "action": audit.ACTION[key]})
    showing = db.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(arrears_now),0) amt FROM reconciliation_snapshot WHERE arrears_now > 0"
    ).fetchone()
    wrong = db.execute(
        """SELECT COUNT(*) n, COALESCE(SUM(arrears_now),0) amt FROM reconciliation_snapshot
           WHERE grp = ? AND arrears_now > 0""", (audit.PAYING_ARREARS_CLEAR,)
    ).fetchone()
    flags = {
        label: db.execute("SELECT COUNT(*) FROM reconciliation_snapshot WHERE flags LIKE ?", (f"%{label}%",)).fetchone()[0]
        for label in ("Paying on NuPay but gym access is blocked", "Paying on NuPay but marked inactive here",
                      "Itensity Active but inactive here")
    }
    return {
        "ready": True, "members": head["n"], "computed_at": head["at"], "table": table,
        "showing_arrears": showing["n"], "showing_amount": round(showing["amt"], 2),
        "wrong_count": wrong["n"], "wrong_amount": round(wrong["amt"], 2),
        "blocked_paying": flags["Paying on NuPay but gym access is blocked"],
        "inactive_paying": flags["Paying on NuPay but marked inactive here"],
        "status_drift": flags["Itensity Active but inactive here"],
        "runs": _runs(db),
    }


def _runs(db) -> list[Any]:
    return db.execute(
        """SELECT r.*, u.full_name AS uploader FROM nupay_import_runs r LEFT JOIN users u ON u.id = r.uploaded_by
           ORDER BY r.id DESC LIMIT 5"""
    ).fetchall()


PAGE_SIZE = 100


def member_list(db, *, group: str | None = None, flag: str | None = None, query: str | None = None,
                page: int = 1) -> tuple[list[Any], int]:
    """One page of members, biggest balance first, and how many match in all."""
    where = " WHERE 1=1"
    params: list[Any] = []
    if group in GROUP_LABELS:
        where += " AND s.grp = ?"
        params.append(group)
    if flag:
        where += " AND s.flags LIKE ?"
        params.append(f"%{flag}%")
    if query:
        where += " AND (m.member_ref LIKE ? OR m.first_name LIKE ? OR m.last_name LIKE ?)"
        params += [f"%{query}%"] * 3
    source = " FROM reconciliation_snapshot s JOIN members m ON m.id = s.member_id" + where
    total = db.execute("SELECT COUNT(*)" + source, params).fetchone()[0]
    rows = db.execute(
        "SELECT s.*, m.member_ref, m.first_name, m.last_name, m.member_status, m.gym_access_status" + source
        + " ORDER BY s.arrears_now DESC, m.id LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (max(page, 1) - 1) * PAGE_SIZE],
    ).fetchall()
    return rows, total


# ── One member (for the profile) ─────────────────────────────────────────────

def member_view(db, member_id: int, roster: dict[str, dict[str, Any]] | None = None, *, today: date | None = None) -> dict[str, Any] | None:
    """What NuPay says about one member, and what recording it would change. None when NuPay has nothing."""
    txns = db.execute(
        "SELECT * FROM nupay_transactions WHERE member_id = ? ORDER BY action_date DESC, id DESC", (member_id,)
    ).fetchall()
    if not txns:
        return None
    rows = stored_rows(db, member_id)
    plan = nt.plan_import(db, rows)
    synthetic = _synthetic(plan).get(member_id, [])
    real = db.execute(
        "SELECT * FROM collections WHERE member_id = ? ORDER BY collection_date DESC, id DESC", (member_id,)
    ).fetchall()
    now_amt, now_months = _profile_arrears(real)
    after_amt, after_months = _profile_arrears(list(real) + synthetic)
    paid = [t for t in txns if t["status"] == nt.SUCCESS]
    failed = [t for t in txns if t["status"] == nt.FAILED]
    summary = plan["summary"]
    changes = []
    if summary.get("payments_added"):
        amount = sum(a["amount"] for a in plan["actions"] if a["op"] == "payment" and a["member_id"] == member_id)
        changes.append(("ok", f"Record {summary['payments_added']} NuPay payment(s), R{amount:,.2f}, with their charges"))
    if summary.get("charges_marked_failed"):
        changes.append(("warn", f"Mark {summary['charges_marked_failed']} charge(s) as failed"))
    if after_amt > 0 and paid:
        changes.append(("warn", f"R{after_amt:,.2f} stays open after recording. Confirm it in the Itensity ledger before collecting."))
    if not changes:
        changes.append(("mute", "Everything NuPay reports is already on this member's record."))
    mandate = db.execute(
        "SELECT status FROM debicheck_mandates WHERE member_id = ? ORDER BY id DESC LIMIT 1", (member_id,)
    ).fetchone()
    snap = db.execute("SELECT * FROM reconciliation_snapshot WHERE member_id = ?", (member_id,)).fetchone()
    return {
        "txns": txns[:24], "paid_count": len(paid), "failed_count": len(failed),
        "paid_amount": round(sum(t["amount"] for t in paid), 2),
        "last_success": paid[0]["action_date"] if paid else None,
        "last_failed": failed[0]["action_date"] if failed else None,
        "total_instalments": next((t["total_instalments"] for t in txns if t["total_instalments"]), None),
        "arrears_now": now_amt, "months_now": now_months, "arrears_after": after_amt, "months_after": after_months,
        "changes": changes, "mandate": normalise_debicheck_status(mandate["status"]) if mandate else None,
        "group": snap["grp"] if snap else None,
        "group_label": GROUP_LABELS[snap["grp"]][0] if snap and snap["grp"] in GROUP_LABELS else None,
        "group_tone": GROUP_LABELS[snap["grp"]][1] if snap and snap["grp"] in GROUP_LABELS else "mute",
        "action": audit.ACTION.get(snap["grp"]) if snap else None,
        "flags": [f for f in (snap["flags"] or "").split("; ") if f] if snap else [],
    }
