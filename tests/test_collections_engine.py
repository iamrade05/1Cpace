from datetime import date
from decimal import Decimal

from onecpase.collections_engine import (
    calculate_arrears_installment,
    calculate_daily_call_kpi,
    calculate_debicheck_options,
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
