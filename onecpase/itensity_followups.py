"""After a member exists in Itensity: send their documents and paid reg fee.

Each part records its own outcome and can be retried alone, so a failed upload
never undoes the member. A document re-checks the profile before uploading, so
repeating one is harmless. A payment that may have gone through is never resent
until someone has looked at the Itensity account: that's real money on a real
statement.
"""
import base64
import mimetypes
import re
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from uuid import uuid4

from flask import current_app

from .database import audit_log, member_activity
from .encryption import decrypt_member
from .financial_reporting import cash_amount
from .integrations import (
    attach_member_documents_to_itensity,
    bind_itensity_tenant,
    html_to_pdf,
    record_reg_fee_in_itensity,
)

MAX_UPLOAD_BYTES = 7 * 1024 * 1024
# Itensity offers two kinds of attachment on a profile.
DOCUMENT_KINDS = {"Signed Contract": "contract", "SA ID Copy": "compliance", "Bank Statement": "compliance"}
DONE_DOCUMENT_STATUSES = ("sent", "already_attached")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
# Only methods with an unmistakable match on Itensity's Add Payment list.
PAYMENT_TYPES = {"cash": "Cash", "card": "Card", "eft": "EFT", "debicheck": "DebiCheck"}


def _member_folder(member_id: int) -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"]) / "members" / str(member_id)


def _data_url(path: Path, default_type: str = "image/png") -> str:
    mime = mimetypes.guess_type(path.name)[0] or default_type
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _inline_static_images(html: str) -> str:
    """A PDF is rendered without the web server, so /static images must travel with it."""
    def replace(match):
        path = Path(current_app.static_folder) / match.group(1)
        return f'src="{_data_url(path)}"' if path.is_file() else match.group(0)
    return re.sub(r'src="/static/([^"]+)"', replace, html)


def _latest_signature_data(conn, member_id: int, role: str) -> str | None:
    row = conn.execute(
        """SELECT image_filename FROM contract_signatures
           WHERE member_id=? AND signer_role=? ORDER BY signed_at DESC, id DESC LIMIT 1""",
        (member_id, role),
    ).fetchone()
    if not row:
        return None
    path = _member_folder(member_id) / "signatures" / row["image_filename"]
    return _data_url(path) if path.is_file() else None


def ensure_signed_contract_document(conn, member_id: int) -> str | None:
    """A contract signed on screen stores only the signature. Make the PDF from
    it, using today's member details. Returns an error message, or None."""
    if conn.execute(
        "SELECT 1 FROM member_documents WHERE member_id=? AND document_type='Signed Contract' LIMIT 1",
        (member_id,),
    ).fetchone():
        return None
    signature = _latest_signature_data(conn, member_id, "member")
    if not signature:
        return "No signed contract or member signature is on file"

    from .contracts import _active_template, render_contract
    member = conn.execute(
        "SELECT m.*,u.full_name consultant_name FROM members m LEFT JOIN users u ON u.id=m.uploaded_by_id WHERE m.id=?",
        (member_id,),
    ).fetchone()
    html = render_contract(
        _active_template(conn), decrypt_member(member), share={"stage": "signed"},
        signature_data=signature, debit_signature_data=_latest_signature_data(conn, member_id, "debit_authoriser"),
    )
    # Other signers' images are links back to the app, which a PDF can't follow.
    def inline_signature(match):
        data = _latest_signature_data(conn, member_id, match.group(1))
        return f'src="{data}"' if data else match.group(0)
    html = re.sub(r'src="[^"]*/signature/(\w+)"', inline_signature, html)
    folder = _member_folder(member_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}_signed_contract.pdf"
    error = html_to_pdf(_inline_static_images(html), str(folder / stored_name))
    if error:
        return error
    conn.execute(
        """INSERT INTO member_documents (member_id, document_type, original_name, stored_name, file_size)
           VALUES (?, 'Signed Contract', ?, ?, ?)""",
        (member_id, f"Signed contract {date.today().isoformat()}.pdf", stored_name,
         (folder / stored_name).stat().st_size),
    )
    conn.commit()
    return None


def documents_to_send(conn, member_id: int) -> list:
    """Every ID copy and bank statement, plus only the newest signed contract."""
    rows = conn.execute(
        """SELECT * FROM member_documents WHERE member_id=?
           AND document_type IN ('Signed Contract','SA ID Copy','Bank Statement') ORDER BY id DESC""",
        (member_id,),
    ).fetchall()
    newest_contract = next((row["id"] for row in rows if row["document_type"] == "Signed Contract"), None)
    return [row for row in rows if row["document_type"] != "Signed Contract" or row["id"] == newest_contract]


def _upload_path(row, member_id: int, workdir: str) -> tuple[str | None, str | None]:
    """The PDF to upload for a stored document: (path, error)."""
    source = _member_folder(member_id) / row["stored_name"]
    if not source.is_file():
        return None, "The stored file is missing"
    suffix = source.suffix.lower()
    output = Path(workdir) / f"{row['id']}.pdf"
    if suffix == ".pdf":
        output = source
    elif suffix in (".html", ".htm"):
        error = html_to_pdf(_inline_static_images(source.read_text(encoding="utf-8")), str(output))
        if error:
            return None, error
    elif suffix in IMAGE_SUFFIXES:
        page = f'<html><body style="margin:0"><img src="{_data_url(source)}" style="width:100%"></body></html>'
        error = html_to_pdf(page, str(output))
        if error:
            return None, error
    else:
        return None, f"Itensity takes PDFs; {suffix or 'this file type'} can't be converted"
    if output.stat().st_size > MAX_UPLOAD_BYTES:
        return None, "The file is over Itensity's 7 MB limit"
    return str(output), None


def _record_document(conn, member_id: int, document_id: int, title: str, status: str, error: str | None) -> None:
    conn.execute(
        """INSERT INTO itensity_document_pushes (member_id, document_id, itensity_title, status, error)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(document_id) DO UPDATE SET
             status=excluded.status, error=excluded.error, attempted_at=datetime('now')""",
        (member_id, document_id, title, status, error),
    )
    if status in DONE_DOCUMENT_STATUSES:
        conn.execute("UPDATE itensity_document_pushes SET confirmed_at=datetime('now') WHERE document_id=?",
                     (document_id,))


def send_documents(conn, member_id: int, itensity_ref: str, surname: str) -> dict:
    """Upload whatever hasn't reached the profile yet. Returns counts and any problems."""
    problems = []
    contract_error = ensure_signed_contract_document(conn, member_id)
    if contract_error:
        problems.append(f"Signed Contract: {contract_error}")

    done = {row["document_id"] for row in conn.execute(
        "SELECT document_id FROM itensity_document_pushes WHERE member_id=? AND status IN ('sent','already_attached')",
        (member_id,),
    )}
    outstanding = [row for row in documents_to_send(conn, member_id) if row["id"] not in done]

    uploads = []
    with tempfile.TemporaryDirectory() as workdir:
        for row in outstanding:
            title = f"{row['document_type']} 1C-{row['id']}"
            path, error = _upload_path(row, member_id, workdir)
            if error:
                _record_document(conn, member_id, row["id"], title, "failed", error)
                problems.append(f"{row['document_type']}: {error}")
                continue
            # Claimed before the browser starts: a crash leaves a visible 'sending'.
            _record_document(conn, member_id, row["id"], title, "sending", None)
            uploads.append({
                "document_id": row["id"], "path": path, "title": title,
                "kind": DOCUMENT_KINDS[row["document_type"]], "document_type": row["document_type"],
            })
        conn.commit()

        results = attach_member_documents_to_itensity(itensity_ref, surname, uploads) if uploads else []
        by_id = {upload["document_id"]: upload for upload in uploads}
        for result in results:
            upload = by_id[result["document_id"]]
            _record_document(conn, member_id, upload["document_id"], upload["title"],
                             result["status"], result.get("error"))
            if result["status"] not in DONE_DOCUMENT_STATUSES:
                problems.append(f"{upload['document_type']}: {result.get('error') or result['status']}")
        conn.commit()

    sent = sum(1 for result in results if result["status"] in DONE_DOCUMENT_STATUSES)
    return {"sent": sent, "already_done": len(done), "problems": problems}


def reg_fee_to_send(conn, member_id: int) -> tuple[dict | None, str | None]:
    """The one paid reg fee to record: (payment details, reason it can't be sent)."""
    rows = conn.execute(
        """SELECT id,amount_paid,status,notes,method,collection_date FROM collections
           WHERE member_id=? AND payment_purpose='registration_fee' AND status='paid' AND amount_paid > 0""",
        (member_id,),
    ).fetchall()
    rows = [row for row in rows if cash_amount(row) > 0]
    if not rows:
        return None, "No paid registration fee is recorded in 1Cpace"
    if len(rows) > 1:
        return None, "More than one registration fee payment is recorded; check them in 1Cpace first"
    row = rows[0]

    from .applications import registration_fee_due
    member = conn.execute("SELECT joining_fee FROM members WHERE id=?", (member_id,)).fetchone()
    amount = float(cash_amount(row))
    due = registration_fee_due(member["joining_fee"])
    if due and abs(amount - due) >= 0.005:
        return None, f"The payment of R{amount:.2f} doesn't match the R{due:.2f} registration fee"
    payment_type = PAYMENT_TYPES.get(str(row["method"] or "").strip().lower())
    if not payment_type:
        return None, f"Payment method '{row['method']}' has no clear match in Itensity"
    paid_on = str(row["collection_date"] or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", paid_on):
        return None, "The registration fee payment has no valid date"
    return {
        "collection_id": row["id"], "amount": amount, "receipt": f"1C-{row['id']}",
        "paid_on": paid_on, "payment_type": payment_type,
    }, None


def send_reg_fee(conn, member_id: int, itensity_ref: str, surname: str) -> dict:
    """Record the paid fee in Itensity, at most once. Returns status and message."""
    collection, reason = reg_fee_to_send(conn, member_id)
    if not collection:
        return {"status": "not_sent", "message": reason}

    # Only a confirmed non-delivery may try again. 'sending' and 'unknown' may
    # already be on the account, and this statement decides that atomically.
    claim = conn.execute(
        """INSERT INTO itensity_reg_fee_pushes (member_id, collection_id, amount, receipt, status)
           VALUES (?, ?, ?, ?, 'sending')
           ON CONFLICT(collection_id) DO UPDATE SET
             status='sending', error=NULL, attempted_at=datetime('now')
           WHERE itensity_reg_fee_pushes.status='failed'""",
        (member_id, collection["collection_id"], collection["amount"], collection["receipt"]),
    )
    conn.commit()
    if claim.rowcount != 1:
        existing = conn.execute("SELECT status FROM itensity_reg_fee_pushes WHERE collection_id=?",
                                (collection["collection_id"],)).fetchone()
        return {"status": existing["status"], "message": "This payment has already been sent, or needs checking in Itensity"}

    result = record_reg_fee_in_itensity(itensity_ref, surname, collection)
    conn.execute(
        """UPDATE itensity_reg_fee_pushes
           SET status=?, fee_detected=?, error=?,
               confirmed_at=CASE WHEN ? IN ('sent','already_in_itensity') THEN datetime('now') END
           WHERE collection_id=?""",
        (result["status"], int(bool(result.get("fee_detected"))), result.get("error"),
         result["status"], collection["collection_id"]),
    )
    conn.commit()
    return {"status": result["status"], "message": result.get("error") or ""}


def run_followups(app, db_path: str, aid: int, member_id: int, user_id, parts=("documents", "reg_fee")) -> dict:
    """Send the requested parts. Never raises, because the member already exists
    in Itensity and nothing here may disturb that."""
    outcome = {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Rendering a contract uses url_for, which needs a request.
        with app.test_request_context("/"):
            bind_itensity_tenant(db_path)
            member = conn.execute("SELECT itensity_ref,last_name FROM members WHERE id=?", (member_id,)).fetchone()
            if not member or not member["itensity_ref"]:
                return {"error": "This member has no Itensity reference yet"}
            ref, surname = str(member["itensity_ref"]), member["last_name"] or ""
            if "documents" in parts:
                outcome["documents"] = documents = send_documents(conn, member_id, ref, surname)
                summary = f"{documents['sent']} sent" + (
                    f"; problems: {'; '.join(documents['problems'])}" if documents["problems"] else "")
                audit_log(conn, "itensity_documents_sent", application_id=aid, new_value=summary, user_id=user_id)
                member_activity(conn, member_id, f"Documents to Itensity: {summary}", user_id=user_id)
            if "reg_fee" in parts:
                outcome["reg_fee"] = fee = send_reg_fee(conn, member_id, ref, surname)
                summary = fee["status"] + (f": {fee['message']}" if fee["message"] else "")
                audit_log(conn, "itensity_reg_fee_sent", application_id=aid, new_value=summary, user_id=user_id)
                member_activity(conn, member_id, f"Reg fee to Itensity: {summary}", user_id=user_id)
            conn.commit()
    except Exception as exc:
        app.logger.exception("Itensity follow-up crashed for application %s", aid)
        conn.rollback()
        outcome["error"] = f"Stopped: {type(exc).__name__}"
    finally:
        conn.close()
    return outcome


def followup_status(db, member_id) -> dict:
    """What reached Itensity for this member, for the application page."""
    if not member_id:
        return {"documents": [], "reg_fee": None}
    documents = db.execute(
        """SELECT d.id, d.document_type, d.original_name, p.status, p.error, p.confirmed_at
           FROM member_documents d
           LEFT JOIN itensity_document_pushes p ON p.document_id = d.id
           WHERE d.member_id=? AND d.document_type IN ('Signed Contract','SA ID Copy','Bank Statement')
           ORDER BY d.id""",
        (member_id,),
    ).fetchall()
    reg_fee = db.execute(
        "SELECT * FROM itensity_reg_fee_pushes WHERE member_id=? ORDER BY id DESC LIMIT 1", (member_id,)
    ).fetchone()
    return {"documents": documents, "reg_fee": reg_fee}
