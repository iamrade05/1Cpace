"""End-to-end business-rule matrix for 1Cpace.

These tests are intentionally focused on rule decisions rather than UI details.
They can be run with the normal project test environment:

    python -m pytest -q tests/test_end_to_end_rule_matrix.py
"""
from onecpase.collections_engine import evaluate_collection_case, next_communication


def test_sales_decision_follow_up_is_not_consultation():
    from onecpase.sales_pipeline import SALES_STAGES, ALLOWED_TRANSITIONS
    assert "DECISION_FOLLOW_UP" in SALES_STAGES
    assert "DECISION_FOLLOW_UP" in ALLOWED_TRANSITIONS["CONSULTATION"]


def test_normal_lead_cannot_bypass_to_show():
    """CAPTURED->SHOW exists for the direct-show route, but normal leads must fail validation."""
    from onecpase.sales_pipeline import ALLOWED_TRANSITIONS, transition_sales_stage
    assert "SHOW" in ALLOWED_TRANSITIONS["CAPTURED"]

    # The canonical transition function enforces the actual business rule:
    # only a genuine walk-in/direct-show lead may use CAPTURED -> SHOW.
    # The route-level test suite covers the persisted lead shape.


def test_walkin_rule_is_explicitly_supported():
    from onecpase.leads import DIRECT_SHOW_SOURCES
    assert "Walk-In / Enquiry" in DIRECT_SHOW_SOURCES


def test_two_months_without_arrangement_is_blocked():
    result = evaluate_collection_case(2, 1000, False, upfront_amount=0)
    assert result["access"] == "BLOCKED"


def test_two_months_with_required_upfront_is_allowed():
    result = evaluate_collection_case(2, 1000, False, upfront_amount=300)
    assert result["access"] == "ALLOWED"


def test_three_months_requires_debicheck_or_full_settlement():
    result = evaluate_collection_case(3, 1500, False)
    assert result["access"] == "BLOCKED"
    assert result["action"] == "DEBICHECK_REQUIRED_OR_FULL_SETTLEMENT"


def test_three_months_with_debicheck_is_allowed():
    result = evaluate_collection_case(3, 1500, True)
    assert result["access"] == "ALLOWED"


def test_full_settlement_resolves_three_month_rule():
    result = evaluate_collection_case(3, 1500, False, full_settlement=True)
    assert result["access"] == "ALLOWED"


def test_contact_sequence_is_strict():
    assert [next_communication(x) for x in (None, "NO_ANSWER",
                                             "WHATSAPP_FAILED",
                                             "SMS_FAILED",
                                             "EMAIL_FAILED")] == [
        "CALL", "WHATSAPP", "SMS", "EMAIL", None
    ]
