from onecpase.statement_parser import analyse_cashflow
from onecpase.collections_decision import payment_is_stale


def test_constant_income_suggests_day_after_pay_day():
    rows = [(2025,1,1,500),(2025,1,25,5500),(2025,1,28,1000),(2025,2,1,900),(2025,2,26,5900),(2025,2,28,800)]
    r = analyse_cashflow(rows)
    assert r["income_pattern"] == "constant" and r["suggested_debit_day"] == 27 and r["enough_months"]


def test_one_month_is_not_enough():
    r = analyse_cashflow([(2025,1,1,500),(2025,1,25,5500),(2025,1,28,1000)])
    assert not r["enough_months"]


def test_stale_last_payment():
    assert payment_is_stale("2020-01-01")
    assert not payment_is_stale(None)
