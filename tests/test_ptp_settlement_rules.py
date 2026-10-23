from decimal import Decimal

from onecpase.collections import below_installment_not_callable
from onecpase.collections_engine import ptp_settlement_offer


def test_owing_less_than_instalment_is_not_callable_without_failed_debit():
    assert below_installment_not_callable(80, 100, False)
    assert not below_installment_not_callable(100, 100, False)
    assert not below_installment_not_callable(80, 100, True)
    assert not below_installment_not_callable(80, 0, False)


def test_half_upfront_settles_the_account():
    offer = ptp_settlement_offer(3500, 1750)
    assert offer["settled_by_upfront"]
    assert offer["debit_order_amount"] == Decimal("0.00")
    assert offer["discount_amount"] == Decimal("1750.00")


def test_upfront_then_half_of_remainder_by_debit_order():
    offer = ptp_settlement_offer(2500, 1000)
    assert not offer["settled_by_upfront"]
    assert offer["debit_order_amount"] == Decimal("750.00")
    assert offer["needs_debit_order"]
