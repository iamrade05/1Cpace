"""Separate monthly PDFs must qualify as a batch before submitting to NuPay."""
import io
import json

import pytest

from onecpase.database import get_db
from onecpase.applications import nupay_submission_blockers


@pytest.mark.parametrize("second_month,error,ready", [(2, None, True), (1, None, False), (2, "Unreadable PDF", False)])
def test_batch_upload_updates_nupay_requirements(client, monkeypatch, second_month, error, ready):
    from test_staff_passwords import _make_admin
    _make_admin(client)
    with client.session_transaction() as session:
        session.update(user_id=1, username="testuser", role="admin")
    with client.application.app_context():
        db = get_db()
        mid = db.execute("INSERT INTO members(first_name,last_name,id_number,monthly_installment) VALUES('Batch','Test','9001015009087',500)").lastrowid
        db.commit()
    months = iter([1, second_month])

    def analyse(path, **kwargs):
        month = next(months)
        return {
            "error": error if month == 2 else None,
            "income_detected": True, "income_months": 1,
            "salary_payments": [7500], "estimated_income": 7500,
            "rating": "good", "verification_qualified": False,
            "tx_rows": [(2026, month, 1, 100), (2026, month, 25, 7600), (2026, month, 28, 100)],
        }

    monkeypatch.setattr("onecpase.statement_uploads.analyse_pdf", analyse)
    response = client.post(f"/debicheck/members/{mid}/documents", data={
        "document_type": "Bank Statement",
        "document": [(io.BytesIO(b"first"), "first.pdf"), (io.BytesIO(b"second"), "second.pdf")],
    })
    assert response.status_code == 200
    requirements = client.get(f"/debicheck/requirements/{mid}")
    assert requirements.json["ready"] is ready
    with client.application.app_context():
        db = get_db()
        assert (nupay_submission_blockers(db, mid) == []) is ready
        documents = db.execute("SELECT analysis_json FROM member_documents WHERE member_id=?", (mid,)).fetchall()
        assert len(documents) == 2
        for document in documents:
            result = json.loads(document["analysis_json"])
            assert result["verification_qualified"] is ready
            assert result["file_analysis"]["income_months"] == 1
