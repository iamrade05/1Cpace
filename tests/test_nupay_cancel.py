"""Cancelling one mandate at NuPay, the way the gym does it by hand.

The portal's Transaction Maintenance screen lists mandates and a header checkbox
selects EVERY listed row, so cancelling is only safe when exactly one mandate is
listed and it is the one asked for. These tests pin the guard and the defaults; the
browser steps themselves can only be exercised against the real portal.
"""
import inspect
from unittest.mock import patch

import pytest

from onecpase import nupay_driver
from onecpase.nupay_driver import CancelResult, cancel_mandate, check_cancellation_target

REF = "DCPRD0001DMBLG"


def test_a_single_matching_mandate_is_allowed():
    assert check_cancellation_target([f"1 000025500014054 {REF} 2025-03-03 ..."], REF) is None


def test_the_match_ignores_case_and_spacing():
    assert check_cancellation_target([f"x   {REF.lower()}   y"], f" {REF} ") is None


def test_no_listed_mandate_means_do_nothing():
    reason = check_cancellation_target([], REF)
    assert reason and "no mandate" in reason.lower()


def test_more_than_one_listed_mandate_is_refused_because_select_all_would_cancel_them_all():
    reason = check_cancellation_target([f"{REF} ...", "DCPRD00016TXZH ..."], REF)
    assert reason and "refusing" in reason.lower() and "2" in reason


def test_a_single_row_for_a_different_contract_is_refused():
    reason = check_cancellation_target(["1 000025500014054 DCPRD00016TXZH ..."], REF)
    assert reason and "not the one" in reason.lower()


@pytest.mark.parametrize("reference", ["", "   ", None])
def test_a_missing_contract_reference_is_refused(reference):
    assert check_cancellation_target(["anything"], reference)


def test_cancelling_is_a_dry_run_unless_submitting_is_asked_for_explicitly():
    assert inspect.signature(cancel_mandate).parameters["submit"].default is False


def test_a_blank_reference_never_opens_a_browser():
    with patch.object(nupay_driver, "sync_playwright") as browser:
        result = cancel_mandate("  ")

    browser.assert_not_called()
    assert isinstance(result, CancelResult) and not result.ready and not result.submitted


def test_missing_credentials_never_open_a_browser():
    with patch.object(nupay_driver, "_require_creds", return_value=["NUPAY_NEW_EMAIL"]), \
         patch.object(nupay_driver, "sync_playwright") as browser:
        result = cancel_mandate(REF, submit=True)

    browser.assert_not_called()
    assert not result.submitted and "NUPAY_NEW_EMAIL" in result.message


@pytest.mark.parametrize(
    "words, expected",
    [
        (["Maintenance request submitted successfully"], True),
        (["Error: mandate could not be found"], False),
        (["Request failed"], False),
        (["Something happened"], None),
        ([], None),
    ],
)
def test_nupays_reply_is_read_conservatively(words, expected):
    assert nupay_driver.read_maintenance_reply(words) is expected


# The real maintenance-ws page carries no heading/alert/toast for the outcome - it
# is a table cell, and the only element the old selector actually matched was an
# sr-only "Notifications" span for the header bell (confirmed against a saved
# screenshot from the 2026-09-29 batch: 68 of 72 cancellations read back exactly
# this, regardless of the real per-row result).
_REAL_MAINTENANCE_WS_BODY = (
    "Dashboard DebiCheck MPS POS NuCard AVS\nNotifications\nMvuleni Radebe\n"
    "000025500014054\nKwa-Thema Gym\nMandate Maintenance Result\n"
    "Mandate... Contract Refere... Mandate Reque... Debtor ID Debtor Name Result\n"
    "1 63553621 DCPRD0001XG266 19652026-01-15000058845 0102130679081 ZV KABENI 500000 Success\n"
    "Showing 1-1 of 1 rows\nBACK\nCopyright © NuPay 2026"
)


def test_extract_maintenance_result_skips_the_notifications_bell_and_finds_the_result_cell():
    summary = nupay_driver.extract_maintenance_result(_REAL_MAINTENANCE_WS_BODY)
    assert "Notifications" not in summary
    assert "Success" in summary and "DCPRD0001XG266" in summary
    assert nupay_driver.read_maintenance_reply([summary]) is True


def test_extract_maintenance_result_falls_back_to_the_whole_body_when_the_heading_is_missing():
    assert nupay_driver.extract_maintenance_result("Something went wrong: Error 500") == (
        "Something went wrong: Error 500"
    )
