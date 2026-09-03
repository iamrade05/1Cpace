from datetime import date

from onecpase.collections_engine import evaluate_collection_case, evaluate_member_access
from onecpase.database import get_db


def _member(db, mid, name):
    db.execute(
        """INSERT INTO members (member_ref, first_name, last_name, id_number, contact,
               member_status, gym_access_status)
           VALUES (?, ?, ?, ?, ?, 'Active', 'allowed')""",
        (f"V6-{mid}", name, "Test", f"ID-V6-{mid}", f"07100000{mid:02d}"),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _arrears(db, member_id, months=2):
    for month in ("2026-07", "2026-08")[:months]:
        db.execute(
            """INSERT INTO collections (member_id, outstanding_balance, amount_paid,
                   collection_date, status, notes)
               VALUES (?, 500, 0, ?, 'failed', 'type=Recurring Fee')""",
            (member_id, f"{month}-01"),
        )


def test_two_months_without_arrangement_blocks_access(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 1, "NoPlan")
        _arrears(db, mid, 2)
        db.commit()
        decision = evaluate_member_access(db, mid, today=date(2026, 9, 2))
        assert decision["status"] == "TWO_MONTHS_RESTRICTED"
        assert decision["access"] == "BLOCKED"


def test_submitted_debicheck_does_not_unlock_two_month_arrears(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 2, "Submitted")
        _arrears(db, mid, 2)
        db.execute(
            "INSERT INTO debicheck_mandates (member_id, merchant_id, client_ref1, client_ref2, account_name, account_type, account_number, branch_code, submit_date, status) VALUES (?, 'test-merchant', 'test-ref1', 'test-ref2', 'Test Member', '1', '123456789', '250655', '2026-09-01', 'submitted')", (mid,)
        )
        db.execute(
            """INSERT INTO ptp_agreements
               (member_id, created_by, promise_amount, promise_date, payment_method, ptp_status)
               VALUES (?, NULL, 100, '2026-09-10', 'debit_order', 'pending')""", (mid,)
        )
        db.commit()
        decision = evaluate_member_access(db, mid, today=date(2026, 9, 2))
        assert decision["access"] == "BLOCKED"


def test_approved_debicheck_plus_ptp_unlocks_two_month_arrears(app):
    with app.app_context():
        db = get_db()
        mid = _member(db, 3, "Approved")
        _arrears(db, mid, 2)
        db.execute(
            "INSERT INTO debicheck_mandates (member_id, merchant_id, client_ref1, client_ref2, account_name, account_type, account_number, branch_code, submit_date, status) VALUES (?, 'test-merchant', 'test-ref1', 'test-ref2', 'Test Member', '1', '123456789', '250655', '2026-09-01', 'approved')", (mid,)
        )
        db.execute(
            """INSERT INTO ptp_agreements
               (member_id, created_by, promise_amount, promise_date, payment_method, ptp_status)
               VALUES (?, NULL, 100, '2026-09-10', 'debit_order', 'pending')""", (mid,)
        )
        db.commit()
        decision = evaluate_member_access(db, mid, today=date(2026, 9, 2))
        assert decision["debicheck_active"] is True
        assert decision["access"] == "ALLOWED"


def test_three_plus_without_debicheck_requires_settlement_or_debicheck(app):
    decision = evaluate_collection_case(3, 1500, False)
    assert decision["access"] == "BLOCKED"
    assert decision["action"] == "DEBICHECK_REQUIRED_OR_FULL_SETTLEMENT"


def test_fixed_contact_sequence_is_enforced(app):
    from onecpase.collections_engine import next_communication
    assert next_communication() == "CALL"
    assert next_communication("NO_ANSWER") == "WHATSAPP"
    assert next_communication("WHATSAPP_FAILED") == "SMS"
    assert next_communication("SMS_FAILED") == "EMAIL"
    assert next_communication("EMAIL_FAILED") is None
