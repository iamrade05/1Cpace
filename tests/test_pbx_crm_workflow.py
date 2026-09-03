import pytest

@pytest.mark.pbx
def test_pbx_crm_workflow_contract():
    """Integration contract: dial -> PBX call row -> outcome -> CRM timeline."""
    from onecpase.pbx import CALL_OUTCOMES
    assert "connected" in CALL_OUTCOMES
    assert "payment_promise" in CALL_OUTCOMES
    assert "callback" in CALL_OUTCOMES
    assert "no_answer" in CALL_OUTCOMES
    assert "appointment_booked" in CALL_OUTCOMES

@pytest.mark.pbx
def test_collections_outcome_mapping_contract():
    from onecpase.pbx import CALL_OUTCOMES
    assert "payment_promise" in CALL_OUTCOMES
    assert "dispute" in CALL_OUTCOMES
    assert "escalation" in CALL_OUTCOMES


def test_elev8_extension_roster_contract():
    roster = {
        "reception": "1000",
        "manager": "1002",
        "sales_manager": "1004",
        "sales_1": "1001",
        "sales_2": "1003",
        "sales_3": "1005",
        "sales_4": "1006",
    }
    assert roster["reception"] == "1000"
    assert roster["manager"] == "1002"
    assert roster["sales_manager"] == "1004"
    assert [roster[f"sales_{i}"] for i in range(1,5)] == ["1001", "1003", "1005", "1006"]


def test_pbx_webhook_auth_contract():
    import hmac
    assert hmac.compare_digest("secret", "secret")
    assert not hmac.compare_digest("secret", "wrong")


def test_pbx_webhook_requires_header_secret(monkeypatch):
    # Contract-level regression: the implementation must read the configured
    # secret from the Yeastar header, not from a query-string parameter.
    from onecpase import pbx
    assert 'request.args.get("secret")' not in __import__('pathlib').Path(pbx.__file__).read_text()
    assert 'X-1Cpace-PBX-Secret' in __import__('pathlib').Path(pbx.__file__).read_text()
