"""V6.1 enforcement regression tests.

These tests document the security boundary: staff may record evidence/actions,
but may not manufacture system-owned payment, mandate, verification or access states.
"""
from datetime import date

from onecpase.collections_engine import evaluate_collection_case, next_communication


def test_debicheck_requires_confirmed_state_for_three_plus_months():
    assert evaluate_collection_case(3, 1500, False)["access"] == "BLOCKED"
    assert evaluate_collection_case(3, 1500, True)["access"] == "ALLOWED"


def test_two_month_rule_uses_configured_upfront_minimum():
    blocked = evaluate_collection_case(2, 1000, False, upfront_amount=299)
    allowed = evaluate_collection_case(2, 1000, False, upfront_amount=300)
    assert blocked["access"] == "BLOCKED"
    assert allowed["access"] == "ALLOWED"


def test_communication_order_cannot_skip_a_channel():
    assert next_communication() == "CALL"
    assert next_communication("NO_ANSWER") == "WHATSAPP"
    assert next_communication("WHATSAPP_FAILED") == "SMS"
    assert next_communication("SMS_FAILED") == "EMAIL"
    assert next_communication("EMAIL_FAILED") is None
