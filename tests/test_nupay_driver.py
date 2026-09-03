from unittest.mock import patch

from onecpase import debicheck, nupay_new_driver
from onecpase.nupay_new_driver import MandateStatusResult, PushResult


def _valid_mandate(**overrides):
    mandate = {
        "merchant_id": "000025500014054",
        "account_name": "Test Member",
        "account_type": "1",
        "account_number": "123456789",
        "branch_code": "051001",
        "no_installments": "12",
        "tracking": "14",
        "submit_date": "2099-01-01",
        "client_ref1": "FIT-1",
        "client_ref2": "0001",
        "id_type": "0",
        "id_number": "8001015009087",
        "instalment_amount": 500,
    }
    mandate.update(overrides)
    return mandate


# ── Pure validation logic — no network, no Playwright ────────────────────────

def test_validate_mandate_accepts_a_complete_mandate():
    assert nupay_new_driver._validate_mandate(_valid_mandate()) == []


def test_validate_mandate_flags_missing_required_fields():
    errors = nupay_new_driver._validate_mandate({})
    assert any("Account name" in e for e in errors)
    assert any("Merchant" in e for e in errors)
    assert any("Client reference 1" in e for e in errors)


def test_validate_mandate_rejects_unsupported_branch_code():
    errors = nupay_new_driver._validate_mandate(_valid_mandate(branch_code="000000"))
    assert any("Unsupported branch code" in e for e in errors)


def test_validate_mandate_rejects_past_submit_date():
    errors = nupay_new_driver._validate_mandate(_valid_mandate(submit_date="2020-01-01"))
    assert any("cannot be in the past" in e for e in errors)


def test_validate_mandate_rejects_zero_amount():
    errors = nupay_new_driver._validate_mandate(_valid_mandate(instalment_amount=0))
    assert any("greater than zero" in e for e in errors)


def test_validate_mandate_requires_passport_when_id_type_is_passport():
    errors = nupay_new_driver._validate_mandate(_valid_mandate(id_type="1", id_number="", passport=""))
    assert any("Passport number is required" in e for e in errors)


# ── Missing credentials short-circuit before any browser/network activity ────

def test_push_mandate_reports_missing_credentials_without_touching_playwright(monkeypatch):
    monkeypatch.delenv("NUPAY_NEW_EMAIL", raising=False)
    monkeypatch.delenv("NUPAY_NEW_PASSWORD", raising=False)
    monkeypatch.delenv("NUPAY_TOTP_SECRET", raising=False)

    with patch("onecpase.nupay_new_driver.sync_playwright") as sync_playwright:
        result = nupay_new_driver.push_mandate(_valid_mandate())

    assert result.success is False
    assert "NUPAY_NEW_EMAIL" in result.message
    sync_playwright.assert_not_called()


def test_refresh_status_reports_missing_credentials_without_touching_playwright(monkeypatch):
    monkeypatch.delenv("NUPAY_NEW_EMAIL", raising=False)

    with patch("onecpase.nupay_new_driver.sync_playwright") as sync_playwright:
        result = nupay_new_driver.refresh_mandate_status(_valid_mandate())

    assert result.found is False
    sync_playwright.assert_not_called()


# ── debicheck.py delegates to the native driver (mocked — never touches
#    the real NuPay portal) ──────────────────────────────────────────────────

def test_push_mandate_to_nupay_delegates_to_driver_and_unpacks_result():
    with patch(
        "onecpase.debicheck.nupay_new_driver.push_mandate",
        return_value=PushResult(True, "Accepted", "raw"),
    ) as push:
        success, message = debicheck.push_mandate_to_nupay(_valid_mandate())

    assert (success, message) == (True, "Accepted")
    push.assert_called_once()


def test_check_mandate_status_on_nupay_delegates_to_driver_and_unpacks_result():
    with patch(
        "onecpase.debicheck.nupay_new_driver.refresh_mandate_status",
        return_value=MandateStatusResult(True, "active", "Contract X: active"),
    ) as refresh:
        found, status, message = debicheck.check_mandate_status_on_nupay(_valid_mandate())

    assert (found, status, message) == (True, "active", "Contract X: active")
    refresh.assert_called_once()


# ── mandate_from_member() still builds the portal-agnostic shape the driver
#    expects (unchanged by the port, but never had direct coverage before) ───

def test_mandate_from_member_builds_expected_shape(app):
    with app.app_context():
        mandate = debicheck.mandate_from_member(
            42,
            {
                "monthly_installment": "499.50",
                "payer_type": "self",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "id_number": "8001015009087",
                "account_type": "Savings",
                "account_number": "123456789",
                "branch_code": "051001",
                "debicheck_submit_date": "2099-01-01",
            },
        )

    assert mandate["member_id"] == 42
    assert mandate["instalment_rands"] == 499
    assert mandate["instalment_cents"] == 50
    assert mandate["account_type"] == "2"  # Savings -> NuPay code
    assert mandate["account_name"] == "Ada Lovelace"
    assert mandate["id_number"] == "8001015009087"
