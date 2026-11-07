"""Table exports are recorded before the browser downloads them."""
import json

from onecpase.database import get_db


def _login(client):
    with client.session_transaction() as s:
        s.update(user_id=1, role="staff", permissions=[], full_name="Exporter")
    with client.application.app_context():
        db = get_db()
        db.execute("INSERT OR IGNORE INTO users (id, username, password_hash, full_name, role, active) "
                   "VALUES (1, 'exporter', 'x', 'Exporter', 'staff', 1)")
        db.commit()


def test_export_is_recorded_with_who_what_and_how_many(client):
    _login(client)
    response = client.post("/table-export-log", json={
        "page": "/applications/?status=verification", "title": "Applications | 1Cpace",
        "format": "xlsx", "rows": 42, "columns": ["Applicant", "Status"],
    })
    assert response.status_code == 200 and response.get_json() == {"ok": True}
    with client.application.app_context():
        row = get_db().execute("SELECT user_id, new_value FROM audit_logs WHERE action='table_export'").fetchone()
    assert row["user_id"] == 1
    assert json.loads(row["new_value"]) == {
        "page": "/applications/?status=verification", "title": "Applications | 1Cpace",
        "format": "xlsx", "rows": 42, "columns": ["Applicant", "Status"],
    }


def test_unknown_format_or_signed_out_is_refused(client):
    assert client.post("/table-export-log", json={"format": "csv"}).status_code in (302, 401)
    _login(client)
    assert client.post("/table-export-log", json={"format": "pdf"}).status_code == 400


def test_every_page_loads_the_table_tools(client):
    _login(client)
    assert b"js/table-tools.js" in client.get("/notifications").data
