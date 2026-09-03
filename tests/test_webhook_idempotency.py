from pathlib import Path

def test_webhook_idempotency_schema_and_claim_helpers():
    schema = Path("onecpase/schema.py").read_text()
    comms = Path("onecpase/comms.py").read_text()
    pbx = Path("onecpase/pbx.py").read_text()
    assert "CREATE TABLE IF NOT EXISTS webhook_events" in schema
    assert "UNIQUE(provider, event_key)" in schema
    assert "_claim_webhook_event" in comms
    assert "unread_count = unread_count + 1" in comms
    assert '_claim_webhook_event(db, "whatsapp"' in comms
    assert '_claim_webhook_event(db, "facebook"' in comms
    assert "INSERT OR IGNORE INTO pbx_webhook_events" in pbx
    assert "if not inserted:" in pbx
    assert "_save_call(msg, commit=False)" in pbx
