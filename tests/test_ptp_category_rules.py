from decimal import Decimal as D

import pytest

from onecpase.collections_engine import (
    arrears_category, category_offer, repayment_schedule,
)


@pytest.mark.parametrize("arrears,category", [
    (1500, 1), (1500.01, 2), (2499.99, 2), (2500, 3),
])
def test_category_boundaries(arrears, category):
    assert arrears_category(arrears) == category


def test_category_1_half_upfront():
    offer = category_offer(700, 350, months_owing=3)
    assert offer["discount_amount"] == D("175.00")
    assert offer["ptp_balance"] == D("175.00")


def test_category_2_half_upfront():
    offer = category_offer(2000, 1000, months_owing=3)
    assert offer["discount_amount"] == D("650.00")
    assert offer["ptp_balance"] == D("350.00")


def test_category_3_half_upfront_clears_balance():
    offer = category_offer(3500, 1750, months_owing=4)
    assert offer["ptp_balance"] == D("0.00")


def test_category_3_any_upfront_gets_half_off_the_whole_amount():
    offer = category_offer(3239.20, 1000, months_owing=10)
    assert offer["discount_percent"] == D("50")
    assert offer["discount_amount"] == D("1619.60")
    assert offer["ptp_balance"] == D("619.60")
    assert offer["upfront"] + offer["ptp_balance"] == D("1619.60")


def test_category_3_without_upfront_has_no_automatic_half():
    assert category_offer(3239.20, 0, months_owing=10)["discount_percent"] == D("30")


@pytest.mark.parametrize("arrears,pct", [(1000, 25), (2000, 35), (4000, 30)])
def test_no_upfront_uses_the_lower_rate(arrears, pct):
    offer = category_offer(arrears, 0, months_owing=3)
    assert offer["discount_percent"] == D(pct)
    assert offer["discount_amount"] + offer["ptp_balance"] == D(arrears)


def test_no_discount_under_three_months():
    offer = category_offer(2000, 1000, months_owing=2)
    assert offer["discount_amount"] == D("0.00")
    assert offer["ptp_balance"] == D("1000.00")


def test_discount_is_only_proposed():
    assert category_offer(2000, 1000, 3)["discount_status"] == "proposed"


def test_schedule_reconciles_with_smaller_final_payment():
    rows = repayment_schedule(350, 4, "2026-11-25")
    assert [a for _, a in rows] == [D("87.50")] * 4
    rows = repayment_schedule(100, 3, "2026-01-31")
    assert sum(a for _, a in rows) == D("100.00")
    assert rows[-1][1] < rows[0][1]
    assert rows[1][0] == "2026-02-28"

from onecpase.communication import collection_offer_message


def test_offer_message_channels():
    wa = collection_offer_message("Sam", "Gym", 3239.20, 1619.60, upfront=1000, monthly_debit=443.92, instalments=2)
    sms = collection_offer_message("Sam", "Gym", 3239.20, 1619.60, upfront=1000, monthly_debit=443.92, instalments=2, short=True)
    assert "R1 619.60" in wa and "R1 000.00" in wa and "R443.92" in wa
    assert len(sms) < len(wa) and "R1 619.60" in sms
    assert "upfront" not in collection_offer_message("Sam", "Gym", 3239.20, 1619.60)

from onecpase.collections_engine import reactivation_upfront_required


def test_reactivation_upfront_rules():
    assert reactivation_upfront_required(3000) == 1500
    assert reactivation_upfront_required(3000, same_premium=False) == 0.01
    assert reactivation_upfront_required(3000, campaign=True) == 0.01
    assert reactivation_upfront_required(0) == 0


def test_visit_call_approach_bands():
    from onecpase.collections_engine import visit_call_approach
    bands = [visit_call_approach(d)['band'] for d in (None, 10, 20, 60, 120)]
    assert bands == ['unknown', 'active', 'recent', 'slipping', 'inactive']

