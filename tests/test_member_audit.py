from datetime import date

from onecpase import member_audit as a

TODAY = date(2026, 10, 6)
ACTIVE_DO = {"status": "Active", "Payment": "Debit Order", "main_mem": "Main Member"}


def group(**kw):
    base = dict(itensity=ACTIVE_DO, mandate_status="active", last_success=None, last_failed=None, arrears_after=0.0, today=TODAY)
    base.update(kw)
    return a.classify(**base)


def test_paying_recently_is_split_by_what_is_still_owed():
    assert group(last_success="2026-09-18") == a.PAYING_ARREARS_CLEAR
    assert group(last_success="2026-09-18", arrears_after=691.16) == a.PAYING_STILL_OWES


def test_the_45_day_window_is_inclusive_of_the_edge_only():
    assert group(last_success="2026-08-22") == a.PAYING_ARREARS_CLEAR   # exactly 45 days back
    assert group(last_success="2026-08-21") == a.MANDATE_NO_RECENT_DEBIT


def test_failing_means_a_recent_failure_with_no_later_success():
    assert group(last_failed="2026-10-01") == a.FAILING_DEBITS
    assert group(last_failed="2026-09-20", last_success="2026-10-01") == a.PAYING_ARREARS_CLEAR
    assert group(last_failed="2026-10-02", last_success="2026-09-20") == a.FAILING_DEBITS


def test_members_who_cannot_be_checked_on_nupay_are_grouped_by_why():
    assert group(itensity=None) == a.NOT_IN_ITENSITY
    assert group(itensity={**ACTIVE_DO, "Payment": "EFT/Cash"}, mandate_status=None) == a.EFT_CASH
    assert group(itensity={**ACTIVE_DO, "Payment": "Sub Account", "main_mem": "Sub Member"}) == a.SUB_ACCOUNT
    assert group(itensity={**ACTIVE_DO, "status": "Inactive"}) == a.INACTIVE_UNVERIFIED
    assert group(mandate_status=None) == a.NO_MANDATE


def test_paying_members_who_are_blocked_or_inactive_here_are_flagged():
    flags = a.mismatch_flags(beta_status="Inactive", beta_access="blocked", itensity=ACTIVE_DO, group=a.PAYING_ARREARS_CLEAR)
    assert "Paying on NuPay but gym access is blocked" in flags and "Paying on NuPay but marked inactive here" in flags
    assert "Itensity Active but inactive here" in flags
    assert a.mismatch_flags(beta_status="Active", beta_access="allowed", itensity=ACTIVE_DO, group=a.PAYING_ARREARS_CLEAR) == []
