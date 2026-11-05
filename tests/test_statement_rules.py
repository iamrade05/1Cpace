"""The money-in rule and how a statement's rows are read."""
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from flask import session
from werkzeug.datastructures import FileStorage

from onecpase.database import get_db
from onecpase.statement_parser import _parse_tx_row, analyse_cashflow, analyse_pdf
from onecpase.statement_uploads import upload_statements


def _analyse(tmp_path, text, instalment=334.75):
    page = MagicMock()
    page.extract_text.return_value = text
    pdf = MagicMock()
    pdf.pages = [page]
    pdf.__enter__.return_value = pdf
    pdf.__exit__.return_value = False
    path = tmp_path / "statement.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    with patch("onecpase.statement_parser.pdfplumber.open", return_value=pdf):
        return analyse_pdf(str(path), monthly_installment=instalment)


CAPITEC = """MR TEST CLIENT Capitec Bank Limited
From Date: 01/08/2026 Opening Balance: R100.00
02/08/2026 Payment Received: Acme Other Income 4 000.00 4 100.00
03/08/2026 Purchase -4 000.00 100.00
02/09/2026 Payment Received: Acme Other Income 3 500.00 3 600.00
03/09/2026 Purchase -3 500.00 100.00
02/10/2026 Payment Received: Acme Other Income 3 500.00 3 600.00
"""


@pytest.mark.parametrize("line, expected", [
    ("2/09/2026 Transaction Fee Headoffice * 10.00 9.88-", (2026, 9, 2, -9.88)),
    ("3/10/2026 Purchase 2 000.00 2 000.00-", (2026, 10, 3, -2000.0)),
    ("4/10/2026 Purchase 5.00 9.88", (2026, 10, 4, 9.88)),
    ("05 Oct 26 Purchase 50.00 12.00-", (2026, 10, 5, -12.0)),
])
def test_balances_keep_their_sign(line, expected):
    assert _parse_tx_row(line, None) == expected


def test_overdrawn_balance_is_read_as_negative(tmp_path):
    result = _analyse(tmp_path, "3/08/2026 Bal Brought Forward 3.02\n2/09/2026 Fee 10.00 9.88-\n")

    assert result["closing_balance"] == -9.88
    assert result["monthly_balances"]["9"] == -9.88


def test_rows_with_only_small_amounts_are_read(tmp_path):
    text = "3/08/2026 Bal Brought Forward 3.02\n8/08/2026 Airtime 5.50 2.52\n12/08/2026 Airtime 5.00 0.12\n"

    result = _analyse(tmp_path, text)

    assert result["monthly_balances"]["8"] == 0.12


def test_one_absa_statement_is_one_month(tmp_path):
    text = "3/07/2026 Bal Brought Forward 7.58-\n14/07/2026 Trf 4 000.00 3 992.42\n2/08/2026 Purchase 45.00 3 947.42\n"

    cash = _analyse(tmp_path, text)["cashflow"]

    assert cash["months"] == 1 and cash["enough_months"] is False


def test_two_days_across_a_month_end_is_not_two_months():
    cash = analyse_cashflow([(2026, 8, 31, 100), (2026, 9, 1, 200)])

    assert cash["enough_months"] is False


def test_about_two_months_counts_as_two():
    assert analyse_cashflow([(2026, 7, 3, 100), (2026, 9, 2, 200)])["enough_months"] is True
    assert analyse_cashflow([(2026, 7, 3, 100), (2026, 8, 20, 200)])["enough_months"] is False


def test_the_first_transaction_counts_as_money_in(tmp_path):
    text = "3/07/2026 Bal Brought Forward 7.58-\n14/07/2026 Immediate Trf Cr 4 000.00 3 992.42\n"

    result = _analyse(tmp_path, text)

    assert result["monthly_money_in"] == {"2026-07": 4000.0}
    assert result["cashflow"]["avg_money_in"] == 4000.0


def test_r3000_in_two_months_passes_without_a_salary_or_a_balance(tmp_path):
    result = _analyse(tmp_path, CAPITEC)

    assert result["income_detected"] is False
    assert result["money_in_months"] == 3
    assert result["verification_qualified"] is True
    assert result["rating"] == "good"


def test_only_one_month_of_r3000_does_not_pass(tmp_path):
    text = """From Date: 01/08/2026 Opening Balance: R100.00
02/08/2026 Payment Received: Acme 4 000.00 4 100.00
02/09/2026 Payment Received: Acme 2 999.00 7 099.00
"""
    assert _analyse(tmp_path, text)["verification_qualified"] is False


def test_a_low_or_negative_balance_no_longer_decides(tmp_path):
    text = CAPITEC + "03/10/2026 Purchase -5 000.00 1 400.00-\n"

    result = _analyse(tmp_path, text)

    assert result["monthly_balances"]["10"] == -1400.0
    assert result["verification_qualified"] is True


def test_a_high_balance_alone_no_longer_passes(tmp_path):
    text = """From Date: 01/08/2026 Opening Balance: R90 000.00
02/08/2026 Purchase -10.00 89 990.00
02/09/2026 Purchase -10.00 89 980.00
"""
    assert _analyse(tmp_path, text, instalment=100)["verification_qualified"] is False


@pytest.mark.parametrize("description", [
    "PayShap Payment Received: Betway178398886",
    "Payment Received: Cap Top Up",
    "PayShap Payment Received: T Client",
    "Payment Received: Mqnick",
    "Cash Deposit: Bedfordview",
])
def test_money_in_counts_wherever_it_came_from(tmp_path, description):
    text = f"""MR TEST CLIENT Capitec Bank Limited
From Date: 01/08/2026 Opening Balance: R100.00
02/08/2026 {description} 4 000.00 4 100.00
"""
    assert _analyse(tmp_path, text)["monthly_money_in"] == {"2026-08": 4000.0}


def test_instalment_is_capped_at_30_percent_of_the_lower_month(tmp_path):
    assert _analyse(tmp_path, CAPITEC, instalment=1050)["verification_qualified"] is True
    assert _analyse(tmp_path, CAPITEC, instalment=1051)["verification_qualified"] is False


@pytest.mark.parametrize("flag, field", [
    ("01/09/2026 DebiCheck Insufficient Funds (R610.20): Vpy (104282239)", "failed_debits"),
    ("03/09/2026 Payment Received: Dispute Vodacom 184.01 3 784.01", "disputed_debits"),
])
def test_a_failed_or_disputed_debit_order_is_shown_but_does_not_stop_a_pass(tmp_path, flag, field):
    result = _analyse(tmp_path, CAPITEC + flag + "\n")

    assert result[field] == 1
    assert result["verification_qualified"] is True


def test_the_fee_line_is_not_a_second_failed_debit_order(tmp_path):
    text = CAPITEC + "01/09/2026 DebiCheck Insufficient Funds (R610.20): Vpy\n01/09/2026 DebiCheck Insufficient Funds Fee Fees -6.00 94.00\n"

    assert _analyse(tmp_path, text)["failed_debits"] == 1


def test_debit_day_follows_when_money_arrives_not_one_large_deposit():
    rows = [
        (2026, 8, 1, 100), (2026, 8, 2, 2100), (2026, 8, 3, 100),
        (2026, 8, 31, 1600), (2026, 9, 1, 3100), (2026, 9, 3, 100),
        (2026, 9, 19, 7100), (2026, 9, 25, 100),
        (2026, 10, 1, 2600), (2026, 10, 2, 4600), (2026, 10, 5, 100),
    ]
    cash = analyse_cashflow(rows)

    assert cash["income_pattern"] == "lump_sum"
    assert cash["suggested_debit_day"] == 2


def _seed(db):
    member_id = db.execute("INSERT INTO members(first_name,last_name,id_number,monthly_installment) VALUES('Rule','Test','RULE-1',334.75)").lastrowid
    db.commit()
    return member_id


def test_the_same_file_twice_counts_once(app, monkeypatch):
    calls = []

    def analyse(path, **kwargs):
        calls.append(path)
        return {"error": None, "returned_debits": 3, "tx_rows": [(2026, 1, 1, 500), (2026, 1, 25, 5500)],
                "money_in_rows": [(2026, 1, 25, 5500, 5000.0)]}
    monkeypatch.setattr("onecpase.statement_uploads.analyse_pdf", analyse)
    with app.test_request_context("/"):
        session["user_id"] = 1
        db = get_db()
        member_id = _seed(db)
        result = upload_statements(db, member_id, [
            FileStorage(io.BytesIO(b"same"), filename="one.pdf"), FileStorage(io.BytesIO(b"same"), filename="two.pdf"),
        ])

        assert len(calls) == 1
        assert result["duplicates"] == ["two.pdf"]
        assert "two.pdf" in result["cashflow"]["note"]
        assert result["returned_debits"] == 3
        assert db.execute("SELECT COUNT(*) FROM member_documents WHERE member_id=?", (member_id,)).fetchone()[0] == 1


def test_statements_together_pass_on_money_in(app, monkeypatch):
    def analyse(path, **kwargs):
        month = 7 if path.endswith("jul.pdf") else 8
        return {"error": None, "disputed_debits": 0, "failed_debits": 0,
                "tx_rows": [(2026, month, 1, 100), (2026, month, 20, 4100)],
                "money_in_rows": [(2026, month, 20, 4100, 4000.0)]}
    monkeypatch.setattr("onecpase.statement_uploads.analyse_pdf", analyse)
    with app.test_request_context("/"):
        session["user_id"] = 1
        db = get_db()
        member_id = _seed(db)
        result = upload_statements(db, member_id, [
            FileStorage(io.BytesIO(b"july"), filename="jul.pdf"), FileStorage(io.BytesIO(b"august"), filename="aug.pdf"),
        ])

        assert result["verification_qualified"] is True
        assert result["rating"] == "good"
        stored = db.execute("SELECT analysis_json FROM member_documents ORDER BY id DESC LIMIT 1").fetchone()
        assert json.loads(stored["analysis_json"])["monthly_money_in"] == {"2026-07": 4000.0, "2026-08": 4000.0}


def test_overlapping_statements_count_a_credit_once(app, monkeypatch):
    def analyse(path, **kwargs):
        return {"error": None, "tx_rows": [(2026, 8, 1, 100), (2026, 8, 20, 4100)],
                "money_in_rows": [(2026, 8, 20, 4100, 4000.0)]}
    monkeypatch.setattr("onecpase.statement_uploads.analyse_pdf", analyse)
    with app.test_request_context("/"):
        session["user_id"] = 1
        db = get_db()
        member_id = _seed(db)
        result = upload_statements(db, member_id, [
            FileStorage(io.BytesIO(b"first"), filename="a.pdf"), FileStorage(io.BytesIO(b"second"), filename="b.pdf"),
        ])

        assert result["monthly_money_in"] == {"2026-08": 4000.0}
        assert result["verification_qualified"] is False
