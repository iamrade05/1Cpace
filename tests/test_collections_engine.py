from datetime import date
from decimal import Decimal

from onecpase.collections_engine import (
    arrears_discount_policy,
    calculate_arrears_installment,
    calculate_daily_call_kpi,
    calculate_debicheck_options,
    calculate_realistic_recovery_plan,
    complete_call,
    determine_access,
    evaluate_collection_case,
    evaluate_ptp,
    next_communication,
    required_upfront_amount,
)
from onecpase.database import get_db


def test_two_month_rule_requires_upfront_payment_without_debicheck():
    decision = evaluate_collection_case(2, 600, False)
    assert decision["access"] == "BLOCKED"
    assert decision["required_upfront_amount"] == Decimal("180.00")

    paid = evaluate_collection_case(2, 600, False, upfront_amount=180)
    assert paid["access"] == "ALLOWED"
    assert paid["action"] == "CREATE_PAYMENT_PLAN"


def test_three_month_rule_requires_debicheck_or_full_settlement():
    blocked = evaluate_collection_case(3, 900, False)
    assert blocked["debicheck_required"] is True
    assert blocked["access"] == "BLOCKED"

    plan = evaluate_collection_case(3, 900, True)
    assert plan["access"] == "ALLOWED"
    assert plan["action"] == "CREATE_FORMAL_PTP"

    settled = evaluate_collection_case(3, 900, False, full_settlement=True)
    assert settled["status"] == "FULL_SETTLEMENT"
    assert settled["access"] == "ALLOWED"
    assert determine_access(3, 900, False) == "BLOCKED"


def test_debicheck_options_apply_minimum_arrears_component():
    assert calculate_arrears_installment(54, 3) == Decimal("30.00")
    options = calculate_debicheck_options(900, 300, 24)
    assert options[-1]["term"] == 24
    assert options[-1]["arrears_installment"] == Decimal("37.50")
    assert options[-1]["new_monthly_amount"] == Decimal("337.50")


def test_communication_sequence_and_call_validation():
    assert next_communication() == "CALL"
    assert next_communication("no_answer") == "WHATSAPP"
    assert next_communication("WHATSAPP_FAILED") == "SMS"
    assert next_communication("SMS_FAILED") == "EMAIL"
    assert next_communication("CONTACTED") is None
    assert complete_call("no_answer")["successful_contact"] is False

    try:
        complete_call("")
    except ValueError as exc:
        assert "valid call outcome" in str(exc)
    else:
        raise AssertionError("invalid call outcomes must be rejected")


def test_kpi_and_ptp_helpers_are_safe_for_empty_workloads():
    kpi = calculate_daily_call_kpi(32, target=40, contacts=18, calls_attempted=32)
    assert kpi["remaining"] == 8
    assert kpi["completion_percentage"] == 80.0
    assert kpi["contact_rate"] == 56.25
    assert calculate_daily_call_kpi(0, target=0)["contact_rate"] == 0
    assert evaluate_ptp(date(2026, 9, 1), False, date(2026, 9, 2)) == "BROKEN"
    assert evaluate_ptp(date(2026, 9, 1), True, date(2026, 8, 31)) == "PAID"


def test_engine_schema_seeds_configurable_rules(app):
    with app.app_context():
        db = get_db()
        keys = {
            row["rule_key"]
            for row in db.execute("SELECT rule_key FROM collection_rules").fetchall()
        }
        assert "daily_call_target" in keys
        assert "upfront_min_percent" in keys
        assert db.execute("SELECT COUNT(*) FROM collections_cases").fetchone()[0] == 0


def test_realistic_recovery_plan_discounts_before_upfront():
    plan = calculate_realistic_recovery_plan(1725, 345, 25, 30)
    assert plan["discount_amount"] == Decimal("431.25")
    assert plan["discounted_balance"] == Decimal("1293.75")
    assert plan["upfront_amount"] == Decimal("388.13")
    assert plan["remaining_balance"] == Decimal("905.62")
    assert plan["recovery_installment"] == Decimal("345.00")
    assert plan["recovery_months"] == 3
    assert plan["final_recovery_installment"] == Decimal("215.62")
    assert plan["monthly_amount_during_recovery"] == Decimal("690.00")


def test_arrears_discount_policy_requires_manager_at_four_months():
    assert arrears_discount_policy(2) == {
        "discount_percent": 25,
        "manager_approval_required": False,
    }
    assert arrears_discount_policy(3) == {
        "discount_percent": 25,
        "manager_approval_required": False,
    }
    assert arrears_discount_policy(4) == {
        "discount_percent": 50,
        "manager_approval_required": True,
    }
    assert arrears_discount_policy(5) == {
        "discount_percent": 50,
        "manager_approval_required": True,
    }
    assert arrears_discount_policy(6) == {
        "discount_percent": 75,
        "manager_approval_required": True,
    }


def test_queries_account_decision_follows_the_shared_arrears_policy():
    """Queries and PTP must not disagree about a member's settlement terms.

    The Queries screen used to allow a 50% write-off without a manager while
    the collections policy required one. Both now read the same policy.
    """
    from onecpase.queries import _payment_arrears_decision

    for months in (0, 1, 2, 3, 4, 5, 6, 7):
        policy = arrears_discount_policy(months)
        decision = _payment_arrears_decision(1000, months)
        assert decision["discount_percent"] == policy["discount_percent"], months
        assert (
            decision["manager_approval_required"]
            == policy["manager_approval_required"]
        ), months
