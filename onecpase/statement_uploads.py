"""Common bank statement upload and cashflow rules across the app."""
import json
from pathlib import Path
from uuid import uuid4
from flask import current_app, session, request
from werkzeug.utils import secure_filename
from .statement_parser import analyse_pdf, analyse_cashflow


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
    analysis = {**analysis, "error": "; ".join(errors) if errors else None,
                "cashflow": analyse_cashflow(all_rows)}
    for stored in stored_names:
        row = db.execute("SELECT analysis_json FROM member_documents WHERE member_id=? AND stored_name=?", (mid, stored)).fetchone()
        result = json.loads(row["analysis_json"])
        result["cashflow"] = analysis["cashflow"]
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
