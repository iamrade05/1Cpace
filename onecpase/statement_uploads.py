"""Common bank statement upload and cashflow rules across the app."""
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4
from flask import current_app, session, request
from werkzeug.utils import secure_filename
from .statement_parser import analyse_pdf, analyse_cashflow


def _batch_analysis(results, all_rows, monthly_installment):
    """Keep qualification consistent with the evidence across all uploaded PDFs."""
    combined = dict(results[-1])
    if len(results) == 1:
        return combined
    payments = [amount for result in results for amount in result.get("salary_payments", [])]
    # Only combine income counts when the files cover separate months. Repeated
    # or overlapping statements must not turn one month's income into two.
    seen = set()
    separate = True
    for result in results:
        months = {(row[0], row[1]) for row in result.get("tx_rows", [])}
        if not months or seen.intersection(months):
            separate = False
        seen.update(months)
    income_count = sum(result.get("income_months", 0) for result in results)
    income_qualified = any(
        result.get("income_detected") and result.get("income_months", 0) >= 2
        for result in results
    ) or (separate and income_count >= 2 and all(
        result.get("income_detected") for result in results
    ))
    balances = [row[3] for row in all_rows]
    balance_qualified = monthly_installment > 0 and bool(balances) and all(
        monthly_installment <= balance * 0.30 for balance in balances
    )
    combined.update(
        income_detected=any(result.get("income_detected") for result in results),
        income_months=income_count if separate else max(result.get("income_months", 0) for result in results),
        salary_payments=sorted(payments, reverse=True),
        returned_debits=sum(result.get("returned_debits", 0) for result in results),
        verification_qualified=bool(income_qualified or balance_qualified),
        tx_rows=all_rows,
    )
    incomes = [result.get("estimated_income", 0) for result in results if result.get("income_detected")]
    combined["estimated_income"] = (
        float(Counter(round(amount / 500) * 500 for amount in payments).most_common(1)[0][0])
        if payments else round(sum(incomes) / len(incomes), 2) if incomes else 0
    )
    if balances:
        combined["max_balance"] = max(balances)
        upper_half = sorted(balances)[len(balances)//2:]
        combined["avg_balance"] = round(sum(upper_half) / len(upper_half), 2)
        combined["closing_balance"] = balances[-1]
    combined["monthly_balances"] = {}
    for _year, month, _day, balance in all_rows:
        key = str(month)
        combined["monthly_balances"][key] = min(balance, combined["monthly_balances"].get(key, balance))
    combined["months_seen"] = len(seen)
    income = combined["estimated_income"]
    combined["rating"], combined["can_afford"] = "unknown", None
    if income > 0:
        ratio = monthly_installment / income
        affordable = ratio <= 0.15 or (ratio <= 0.30 and combined["returned_debits"] <= 2)
        combined["can_afford"] = affordable
        combined["rating"] = "good" if monthly_installment > 0 and ratio <= 0.15 else "moderate" if affordable else "risk"
    combined["summary"] = "<br>".join(result.get("summary", "") for result in results)
    return combined


def upload_statements(db, mid, files):
    from .applications import ensure_member_application, sync_application_status
    from .members import member_activity
    files = [f for f in files if f and f.filename]
    if not files or not all(f.filename.lower().endswith(".pdf") for f in files):
        raise ValueError("Upload the bank statements as PDF files")
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "members" / str(mid)
    upload_dir.mkdir(parents=True, exist_ok=True)
    member = db.execute("SELECT monthly_installment FROM members WHERE id=?", (mid,)).fetchone()
    aid = ensure_member_application(db, mid)
    all_rows, errors, analysis = [], [], {}
    results = []
    stored_names = []
    for file in files:
        stored_name = f"{uuid4().hex}_{secure_filename(file.filename)}"
        dest = upload_dir / stored_name
        file.save(str(dest))
        stored_names.append(stored_name)
        analysis = analyse_pdf(
            str(dest),
            monthly_installment=float(member["monthly_installment"] or 0) if member else 0.0,
            pdf_password=request.form.get("pdf_password") or None,
        )
        if analysis.get("error"):
            errors.append(f"{file.filename}: {analysis['error']}")
        results.append(analysis)
        all_rows += [tuple(r) for r in analysis.get("tx_rows", [])]
        db.execute(
            """INSERT INTO member_documents
               (member_id, document_type, original_name, stored_name, file_size, uploaded_by, analysis_json)
               VALUES (?, 'Bank Statement', ?, ?, ?, ?, ?)""",
            (mid, file.filename, stored_name, dest.stat().st_size, session.get("user_id"), json.dumps(analysis)),
        )
        member_activity(db, mid, f"Uploaded Bank Statement: {file.filename}")
    db.execute(
        "UPDATE compliance_checklists SET bank_statement_status=?, updated_at=datetime('now') WHERE application_id=?",
        ("uploaded" if errors else "analysed", aid),
    )
    # Statements from several files are read together, oldest first.
    all_rows.sort(key=lambda r: (r[0], r[1], r[2]))
    analysis = _batch_analysis(results, all_rows, float(member["monthly_installment"] or 0) if member else 0.0)
    analysis = {**analysis, "error": "; ".join(errors) if errors else None,
                "cashflow": analyse_cashflow(all_rows)}
    if errors:
        analysis["verification_qualified"] = False
    for stored in stored_names:
        row = db.execute("SELECT analysis_json FROM member_documents WHERE member_id=? AND stored_name=?", (mid, stored)).fetchone()
        result = json.loads(row["analysis_json"])
        result["file_analysis"] = dict(result)
        result.update(analysis)
        result["batch_error"] = analysis.get("error")
        db.execute("UPDATE member_documents SET analysis_json=? WHERE member_id=? AND stored_name=?", (json.dumps(result), mid, stored))
    analysis_result = analysis
    if not errors:
        db.execute(
            """INSERT INTO statement_analyses
               (application_id, income_detected, estimated_monthly_income,
                max_balance_m1, max_balance_m2, max_balance_m3, average_balance,
                returned_debits_count, negative_balance_days, income_frequency,
                income_months, verification_qualified, risk_rating, analysis_summary, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                aid,
                "yes" if analysis_result.get("income_detected") else "no",
                analysis_result.get("estimated_income", 0),
                analysis_result.get("monthly_balances", {}).get("1", 0),
                analysis_result.get("monthly_balances", {}).get("2", 0),
                analysis_result.get("monthly_balances", {}).get("3", 0),
                analysis_result.get("avg_balance", 0),
                analysis_result.get("returned_debits", 0),
                0,
                "monthly" if analysis_result.get("income_months", 0) else "irregular",
                analysis_result.get("income_months", 0),
                int(bool(analysis_result.get("verification_qualified"))),
                analysis_result.get("rating", "unknown"),
                analysis_result.get("summary", ""),
                session.get("user_id"),
            ),
        )
    sync_application_status(db, aid)
    return analysis
