"""Authoritative Elev8 member synchronisation.

Source of truth: SQL Server RADE/ELEV8_/dbo.Members.
Target: the active 1Cpace tenant SQLite database.

The sync is deliberately one-way. It creates/updates local operational member
records from the source, never deletes source records, and never deletes local
records that are absent from the source. ``unique_ref`` is the authoritative
external key and is stored in ``members.itensity_ref``.

Member ID numbers are PII and are stored the way the member screens store
them: ``id_number`` encrypted, with the deterministic ``id_number_hash``
alongside it for exact-match lookups. That shapes three things here — rows are
written through :func:`encrypt_member`, source records are matched against the
hash rather than the stored ciphertext, and change detection compares decrypted
plaintext, because Fernet ciphertext differs on every encrypt and comparing it
would report every member as changed on every run.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .database import next_member_ref
from .encryption import decrypt, encrypt_member, encryption_enabled, hash_for_lookup

SOURCE_SQL = """
SELECT id_number, unique_ref, name_surname, date_joined, package, cellphone,
       monthly_installment, status, soldby
FROM dbo.Members
ORDER BY unique_ref
"""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).lower()


def _parse_amount(value: Any) -> float:
    raw = _clean(value).replace("R", "").replace(",", "").strip()
    if not raw:
        return 0.0
    try:
        return float(Decimal(raw))
    except (InvalidOperation, ValueError):
        return 0.0


def _parse_join_date(value: Any) -> str | None:
    raw = _clean(value)
    if not raw:
        return None
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _split_name(value: Any) -> tuple[str, str]:
    parts = _clean(value).split()
    if not parts:
        return "Unknown", "Member"
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def fetch_source_members(connection) -> list[dict[str, Any]]:
    """Read and validate the complete authoritative roster from SQL Server."""
    rows = connection.cursor().execute(SOURCE_SQL).fetchall()
    members: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for row in rows:
        ref = _clean(row.unique_ref)
        if not ref:
            raise ValueError("dbo.Members contains a blank unique_ref")
        key = _norm(ref)
        if key in seen_refs:
            raise ValueError(f"dbo.Members contains duplicate unique_ref: {ref}")
        seen_refs.add(key)
        first_name, last_name = _split_name(row.name_surname)
        members.append({
            "unique_ref": ref,
            "id_number": _clean(row.id_number),
            "first_name": first_name,
            "last_name": last_name,
            "join_date": _parse_join_date(row.date_joined),
            "package": _clean(row.package) or None,
            "contact": _clean(row.cellphone) or None,
            "monthly_installment": _parse_amount(row.monthly_installment),
            "member_status": _clean(row.status) or "Inactive",
            "soldby": _clean(row.soldby) or None,
        })
    return members


def _prepare_pii(values: dict[str, Any]) -> dict[str, Any]:
    """Encrypt id_number and add its lookup hash, as the member screens do.

    Without a configured key the row is written as legacy plaintext rather
    than failing the whole sync — the same tolerance the read path has.
    """
    if not encryption_enabled():
        return dict(values)
    return encrypt_member(values)


def _source_id_keys(id_number: str) -> tuple[str | None, str]:
    """Return (hash, plaintext-key) used to find an existing local member."""
    plain_key = _norm(id_number)
    if not plain_key:
        return None, ""
    if not encryption_enabled():
        return None, plain_key
    return hash_for_lookup(id_number), plain_key


def _local_maps(db):
    """Index local members by source ref, by ID hash, and by legacy plaintext ID."""
    rows = db.execute(
        "SELECT id, member_ref, itensity_ref, id_number, id_number_hash FROM members"
    ).fetchall()
    by_ref: dict[str, Any] = {}
    by_hash: dict[str, Any] = {}
    by_plain: dict[str, Any] = {}
    for row in rows:
        ref_key = _norm(row["itensity_ref"])
        if ref_key:
            by_ref[ref_key] = row
        hashed = _clean(row["id_number_hash"])
        if hashed:
            by_hash[hashed] = row
        else:
            # Rows written before encryption was switched on still hold a
            # plaintext id_number and have no hash to match against.
            plain = _norm(row["id_number"])
            if plain:
                by_plain[plain] = row
    return rows, by_ref, by_hash, by_plain


def _match_local(src, by_ref, by_hash, by_plain):
    """Find the local member for *src*: by source ref first, then by ID number."""
    local = by_ref.get(_norm(src["unique_ref"]))
    if local is not None:
        return local, False
    id_hash, plain_key = _source_id_keys(src["id_number"])
    if id_hash and id_hash in by_hash:
        return by_hash[id_hash], True
    if plain_key and plain_key in by_plain:
        return by_plain[plain_key], True
    return None, False


def reconcile_source(db, source_members: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a no-write reconciliation summary."""
    rows, by_ref, by_hash, by_plain = _local_maps(db)
    matched = 0
    id_matches = 0
    missing = []
    conflicts = []
    source_refs = set()
    for src in source_members:
        ref_key = _norm(src["unique_ref"])
        source_refs.add(ref_key)
        local, matched_by_id = _match_local(src, by_ref, by_hash, by_plain)
        if matched_by_id:
            id_matches += 1
            if _norm(local["itensity_ref"]) and _norm(local["itensity_ref"]) != ref_key:
                conflicts.append({
                    "source_ref": src["unique_ref"],
                    "local_id": local["id"],
                    "local_ref": local["itensity_ref"],
                    "reason": "id_number_match_with_different_source_ref",
                })
        if local is None:
            missing.append(src["unique_ref"])
        else:
            matched += 1
    local_not_source = [
        r["id"] for r in rows
        if _norm(r["itensity_ref"]) and _norm(r["itensity_ref"]) not in source_refs
    ]
    return {
        "source_total": len(source_members),
        "local_total": len(rows),
        "matched": matched,
        "matched_by_id": id_matches,
        "missing_in_local": len(missing),
        "missing_refs": missing,
        "local_not_in_source": len(local_not_source),
        "local_not_source_ids": local_not_source,
        "conflicts": conflicts,
        "clean": not missing and not conflicts,
    }


def _comparable_local(db, local_id: int) -> tuple:
    """The stored values for *local_id*, with id_number decrypted for compare."""
    row = db.execute(
        """SELECT first_name, last_name, id_number, contact, join_date,
                  member_status, package, tariff, monthly_installment,
                  itensity_ref, source
           FROM members WHERE id=?""", (local_id,)
    ).fetchone()
    current = dict(row)
    current["id_number"] = decrypt(current["id_number"]) or ""
    return tuple(current.values())


def sync_source_members(db, source_members: list[dict[str, Any]], tenant_name: str) -> dict[str, Any]:
    """Apply a source roster to local members. Caller owns the transaction."""
    rows, by_ref, by_hash, by_plain = _local_maps(db)
    inserted = 0
    updated = 0
    unchanged = 0
    conflicts = []

    for src in source_members:
        ref_key = _norm(src["unique_ref"])
        local, matched_by_id = _match_local(src, by_ref, by_hash, by_plain)
        if matched_by_id and _norm(local["itensity_ref"]) not in ("", ref_key):
            conflicts.append({
                "source_ref": src["unique_ref"],
                "local_id": local["id"],
                "local_ref": local["itensity_ref"],
                "reason": "id_number_already_belongs_to_different_source_ref",
            })
            continue

        payload = _prepare_pii({
            "id_number": src["id_number"],
            "first_name": src["first_name"],
            "last_name": src["last_name"],
            "contact": src["contact"],
            "join_date": src["join_date"],
            "member_status": src["member_status"],
            "package": src["package"],
            "monthly_installment": src["monthly_installment"],
        })
        id_hash = payload.get("id_number_hash")

        if local is None:
            member_ref = next_member_ref(db, tenant_name)
            db.execute(
                """INSERT INTO members
                   (member_ref, first_name, last_name, id_number, id_number_hash,
                    contact, join_date, member_status, package, tariff,
                    monthly_installment, source, entry_type, outcome, itensity_ref)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'import', 'joined', ?)""",
                (member_ref, payload["first_name"], payload["last_name"],
                 payload["id_number"], id_hash, payload["contact"],
                 payload["join_date"], payload["member_status"], payload["package"],
                 payload["package"], payload["monthly_installment"],
                 "Elev8 dbo.Members", src["unique_ref"]),
            )
            new_row = db.execute(
                """SELECT id, member_ref, itensity_ref, id_number, id_number_hash
                   FROM members WHERE member_ref=?""",
                (member_ref,),
            ).fetchone()
            by_ref[ref_key] = new_row
            if id_hash:
                by_hash[id_hash] = new_row
            elif _norm(src["id_number"]):
                by_plain[_norm(src["id_number"])] = new_row
            inserted += 1
            continue

        local_id = local["id"]
        before_values = _comparable_local(db, local_id)
        after_values = (
            src["first_name"], src["last_name"], src["id_number"], src["contact"],
            src["join_date"], src["member_status"], src["package"], src["package"],
            src["monthly_installment"], src["unique_ref"], "Elev8 dbo.Members",
        )
        if before_values == after_values:
            unchanged += 1
            continue
        db.execute(
            """UPDATE members SET first_name=?, last_name=?, id_number=?,
                   id_number_hash=?, contact=?, join_date=?, member_status=?,
                   package=?, tariff=?, monthly_installment=?, itensity_ref=?,
                   source=? WHERE id=?""",
            (payload["first_name"], payload["last_name"], payload["id_number"],
             id_hash, payload["contact"], payload["join_date"],
             payload["member_status"], payload["package"], payload["package"],
             payload["monthly_installment"], src["unique_ref"],
             "Elev8 dbo.Members", local_id),
        )
        updated += 1

    return {
        "source_total": len(source_members),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "conflicts": conflicts,
    }


def open_source_connection(connection_string: str, timeout: int = 10):
    import pyodbc
    return pyodbc.connect(connection_string, timeout=timeout)
