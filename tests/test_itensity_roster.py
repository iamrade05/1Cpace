"""The Itensity roster gate, mapping, and import.

No network: every test builds a roster payload by hand, in the shape
scripts/pull_itensity_roster.py writes.
"""

import json

import pytest

from onecpase.database import get_db
from onecpase.encryption import hash_for_lookup
from scripts.import_itensity_members import (
    import_members,
    read_roster,
    roster_member_values,
)
from scripts.itensity_reconcile import RosterMismatch, bucket_counts, reconcile

TEST_KEY = "kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8="


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_KEY)


def record(
    ref,
    *,
    role="Member",
    status="Active",
    frozen="Not Frozen",
    name="Ada Lovelace",
    email="8402075628081",
    detail=False,
):
    """One merged person, as the puller writes them.

    ``email`` is Itensity's ID-number column — the address, when there is one,
    lives in ``id``. ``detail`` adds the reports that only cover confirmed
    memberships.
    """
    entry = {
        "ref": str(ref),
        "roster": {
            "userid": str(ref),
            "name": name,
            "email": email,
            "id": "ada@example.com",
            "cell": "0720000000",
            "role": role,
            "status": status,
            "frozen": frozen,
            "tariff": "Premium 12 2026",
            "pay": "345.58",
            "start": "2025-03-03 00:00:00",
            "Payment": "Debit Order",
            "accno": "147****099",
        },
        "sources": [],
    }
    if detail:
        given, _, surname = name.rpartition(" ")
        entry["personal"] = {
            "id": str(ref), "name": given, "surname": surname, "email": email,
            "gender": "Female", "dob": "1984-02-07", "cell": "0720000000",
            "address": "27132 Mxhasa Street",
        }
        entry["banking"] = {
            "id": str(ref), "bank": "Capitec", "accname": "A Lovelace",
            "accno": "1479690099", "branch": "470010", "debitdate": "1",
        }
        entry["extract"] = {
            "id": str(ref), "add4": "ada@example.com", "add25": "Analyst",
            "add20": "0742027189", "add21": "Tumelo Montsi", "add334": "Mother",
        }
        entry["sources"] = ["personal", "banking", "extract"]
    return entry


def payload(rows, chips):
    return {"pulled_at": "2026-09-10 09:00:00", "snapshot": {"chips": chips}, "rows": rows}


def chips(active=0, blocked=0, inactive=0, unverified=0):
    counts = {"active": active, "blocked": blocked, "inactive": inactive,
              "unverified": unverified}
    counts["total"] = sum(counts.values())
    return counts


# ── The gate ────────────────────────────────────────────────────────────────

def test_gate_passes_when_every_bucket_matches():
    rows = [record(1), record(2, status="Blocked"), record(3, status="Unverified")]
    ok, lines = reconcile(payload(rows, chips(active=1, blocked=1, unverified=1)))
    assert ok, "\n".join(lines)


def test_gate_fails_when_two_buckets_are_wrong_in_opposite_directions():
    # The total still adds up — which is exactly why the check is per bucket.
    rows = [record(1), record(2), record(3, status="Inactive")]
    ok, lines = reconcile(payload(rows, chips(active=1, inactive=2)))
    assert not ok
    assert any("active" in line for line in lines)


def test_gate_fails_on_a_short_pull():
    ok, _ = reconcile(payload([record(1)], chips(active=2)))
    assert not ok


def test_frozen_people_are_counted_outside_the_active_chip():
    # Itensity's Active chip excludes the frozen member; the pull still has them.
    rows = [record(1), record(2, frozen="Frozen")]
    counts = bucket_counts(rows)
    assert counts == {"active": 1, "frozen": 1}
    ok, lines = reconcile(payload(rows, chips(active=1)))
    assert ok, "\n".join(lines)


def test_unknown_status_value_fails_the_gate():
    ok, lines = reconcile(payload([record(1, status="Pending")], chips(active=1)))
    assert not ok
    assert any("Unrecognised status" in line for line in lines)


def test_read_roster_refuses_a_roster_that_does_not_match(tmp_path):
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(payload([record(1)], chips(active=2))), encoding="utf-8")
    with pytest.raises(RosterMismatch):
        read_roster(path)


# ── Field mapping ───────────────────────────────────────────────────────────

def test_id_number_is_read_from_itensitys_email_column():
    values = roster_member_values(record(1, detail=True))
    assert values["id_number"] == "8402075628081"
    assert values["email"] == "ada@example.com"


def test_member_without_an_id_gets_a_stable_marked_placeholder():
    thin = record(7, email="not-an-id")
    first = roster_member_values(thin)
    second = roster_member_values(thin)
    assert first["id_number"] == "ITN-7"
    assert first["id_number"] == second["id_number"], "a re-import must match, not duplicate"
    assert first["_synthetic_id"] is True


def test_emergency_contact_is_read_by_shape_not_by_column():
    # Staff have filled the two custom fields in both orders.
    values = roster_member_values(record(1, detail=True))
    assert values["emergency_contact_number"] == "0742027189"
    assert values["emergency_contact_name"] == "Tumelo Montsi"


def test_thin_member_keeps_banking_blank_rather_than_guessing():
    values = roster_member_values(record(2))
    assert values["account_number"] == ""  # the roster's own accno is masked
    assert values["bank"] == ""
    assert values["member_status"] == "Active"


def test_staff_are_not_imported(tmp_path):
    rows = [record(1), record(2, role="Sales", name="Sales Person")]
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(payload(rows, chips(active=2))), encoding="utf-8")
    members, skipped, _ = read_roster(path)
    assert len(members) == 1
    assert skipped["non_member_role"] == 1


# ── Import ──────────────────────────────────────────────────────────────────

def test_import_encrypts_the_id_and_stores_a_lookup_hash(app):
    with app.app_context():
        db = get_db()
        members = [roster_member_values(record(1, detail=True))]
        members[0].pop("_synthetic_id")
        import_members(db, members)
        db.commit()

        row = db.execute(
            "SELECT id_number, id_number_hash FROM members WHERE itensity_ref = '1'"
        ).fetchone()
        assert row["id_number"].startswith("enc:"), "PII must not be stored in the clear"
        assert row["id_number_hash"] == hash_for_lookup("8402075628081")


def test_reimport_updates_the_same_member(app):
    with app.app_context():
        db = get_db()
        for _ in range(2):
            values = roster_member_values(record(1, detail=True))
            values.pop("_synthetic_id")
            import_members(db, [values])
        db.commit()
        assert db.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 1


def test_reimport_of_a_placeholder_id_member_does_not_duplicate(app):
    with app.app_context():
        db = get_db()
        for _ in range(2):
            values = roster_member_values(record(9, email="no-id-captured"))
            values.pop("_synthetic_id")
            import_members(db, [values])
        db.commit()
        rows = db.execute("SELECT itensity_ref FROM members").fetchall()
        assert [row["itensity_ref"] for row in rows] == ["9"]
