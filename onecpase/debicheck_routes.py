"""Standalone Finance > DebiCheck workflow."""
import sqlite3
import threading
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from .auth import permission_required
from .database import get_db, member_activity
from .debicheck import (
    check_mandate_status_on_nupay,
    insert_mandate,
    itensity_client_ref_number,
    mandate_from_member,
    push_mandate_to_nupay,
    push_mandate_to_nupay_manual_review,
    record_push_result,
    sync_debicheck_to_application,
    validate_mandate,
    validate_stored_mandate,
)
from .encryption import decrypt_mandate, decrypt_member, encrypt_mandate
from .members import BANKS


debicheck_bp = Blueprint("debicheck", __name__, url_prefix="/debicheck")


@debicheck_bp.get("")
@permission_required("debicheck_mandates")
def index():
    q = request.args.get("q", "").strip()
    sql = """SELECT d.*, m.first_name, m.last_name, m.id_number
             FROM debicheck_mandates d
             JOIN members m ON m.id = d.member_id"""
    params = []
    if q:
        like = f"%{q}%"
        sql += """ WHERE (
            m.first_name LIKE ? OR m.last_name LIKE ?
            OR (m.first_name || ' ' || m.last_name) LIKE ?
            OR d.client_ref1 LIKE ? OR d.client_ref2 LIKE ?
            OR d.nupay_response LIKE ?
        )"""
        # nupay_response is where the contract reference actually lives
        # (e.g. "DCPRD00015MQZ6") — it isn't its own column, so a search for
        # a contract ref would silently find nothing without this.
        params += [like, like, like, like, like, like]
    sql += " ORDER BY d.created_at DESC, d.id DESC"
    rows = get_db().execute(sql, params).fetchall()
    # Decrypt PII fields on each row for display
    decrypted = []
    for row in rows:
        rd = decrypt_mandate(row)
        from .encryption import decrypt
        rd["id_number"] = decrypt(rd.get("id_number"))
        decrypted.append(rd)
    counts = {row["status"]: row["count"] for row in get_db().execute(
        "SELECT status, COUNT(*) AS count FROM debicheck_mandates GROUP BY status"
    ).fetchall()}
    return render_template("debicheck/index.html", mandates=decrypted, counts=counts, search=q)


@debicheck_bp.get("/coverage")
@permission_required("debicheck_mandates")
def coverage():
    """Every Debit Order member split into has-a-mandate / no-mandate, so
    staff can see the gap directly instead of only ever seeing the members
    who already have one (the main index only ever lists mandate rows, so a
    member with none never appears there at all)."""
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()

    sql = """SELECT m.id, m.first_name, m.last_name, m.member_status, m.payment_type,
                     m.debit_order_date, d.id AS mandate_id, d.status AS mandate_status
              FROM members m
              LEFT JOIN debicheck_mandates d ON d.member_id = m.id
              WHERE m.payment_type = 'Debit Order'"""
    params: list = []
    if status_filter:
        sql += " AND m.member_status = ?"
        params.append(status_filter)
    if q:
        like = f"%{q}%"
        sql += " AND (m.first_name LIKE ? OR m.last_name LIKE ? OR (m.first_name || ' ' || m.last_name) LIKE ?)"
        params += [like, like, like]
    sql += " ORDER BY m.first_name, m.last_name"
    rows = get_db().execute(sql, params).fetchall()

    with_mandate = [dict(r) for r in rows if r["mandate_id"] is not None]
    without_mandate = [dict(r) for r in rows if r["mandate_id"] is None]
    statuses = [r["member_status"] for r in get_db().execute(
        "SELECT DISTINCT member_status FROM members WHERE payment_type='Debit Order' ORDER BY member_status"
    ).fetchall()]

    return render_template(
        "debicheck/coverage.html",
        with_mandate=with_mandate,
        without_mandate=without_mandate,
        search=q,
        status_filter=status_filter,
        statuses=statuses,
    )


def _submission_data(member, form):
    payer_type = form.get("payer_type", "self")
    return {
        "first_name": member["first_name"],
        "last_name": member["last_name"],
        "id_number": member["id_number"],
        "itensity_ref": member["itensity_ref"],
        "join_date": member["join_date"],
        "payment_type": "Third-Party Debit Order" if payer_type == "third_party" else "Debit Order",
        "payer_type": payer_type,
        "payer_name": form.get("payer_name", "").strip(),
        "payer_id_number": form.get("payer_id_number", "").strip(),
        "account_name": form.get("account_name", "").strip(),
        "bank": form.get("bank", "").strip(),
        "account_type": form.get("account_type", "").strip(),
        "account_number": form.get("account_number", "").strip(),
        "branch_code": form.get("branch_code", "").strip(),
        "monthly_installment": form.get("instalment_amount", "0").strip(),
        "debicheck_auth_type": form.get("auth_type", "cell").strip(),
        "debicheck_client_ref1": form.get("client_ref1", "").strip(),
        "debicheck_client_ref2": form.get("client_ref2", "").strip(),
        "debicheck_frequency": form.get("frequency", "3").strip(),
        "debicheck_installments": form.get("no_installments", "99").strip(),
        "debicheck_tracking": form.get("tracking", "14").strip(),
        "debicheck_submit_date": form.get("submit_date", "").strip(),
        "debicheck_id_type": form.get("id_type", "0").strip(),
        "debicheck_passport": form.get("passport", "").strip(),
        "debicheck_juristic": form.get("juristic_account", "").strip(),
    }


@debicheck_bp.route("/add", methods=["GET", "POST"])
@permission_required("new_debicheck")
def add():
    db = get_db()
    raw_members = db.execute(
        """SELECT id, first_name, last_name, id_number, contact, email,
                  monthly_installment, account_name, bank, account_type,
                  account_number, branch_code, payer_name, payer_id_number,
                  itensity_ref
           FROM members ORDER BY first_name, last_name"""
    ).fetchall()
    members = [decrypt_member(m) for m in raw_members]
    for m in members:
        m["client_ref_suggestion"] = itensity_client_ref_number(m.get("itensity_ref"))
    selected_member_id = request.form.get("member_id") or request.args.get("member_id", "")

    if request.method == "POST":
        raw_member = db.execute("SELECT * FROM members WHERE id = ?", (selected_member_id,)).fetchone()
        if raw_member is None:
            flash("Select a member before creating the mandate.", "error")
        else:
            member = decrypt_member(raw_member)
            data = _submission_data(member, request.form)
            errors = validate_mandate(data)
            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                mandate = mandate_from_member(member["id"], data)
                encrypted_mandate = encrypt_mandate(mandate)
                try:
                    mandate_id = insert_mandate(db, encrypted_mandate, session.get("user_id"))
                    member_activity(db, member["id"], f"DebiCheck mandate #{mandate_id} created")
                    db.commit()
                    if request.form.get("submit_action") == "push_nupay":
                        return _push(db, mandate_id, mandate, member["id"])
                    flash("DebiCheck mandate saved and ready to push.", "success")
                    return redirect(url_for("debicheck.detail", mandate_id=mandate_id))
                except Exception:
                    current_app.logger.exception("Failed to insert DebiCheck mandate for member id=%s", selected_member_id)
                    flash("Failed to save mandate. Please try again.", "error")

    return render_template(
        "debicheck/add.html", members=members, banks=BANKS,
        selected_member_id=str(selected_member_id), today=date.today().isoformat(),
        form=request.form,
    )


@debicheck_bp.get("/<int:mandate_id>")
@permission_required("debicheck_mandates")
def detail(mandate_id):
    row = get_db().execute(
        """SELECT d.*, m.first_name, m.last_name, m.contact, m.email
           FROM debicheck_mandates d JOIN members m ON m.id=d.member_id
           WHERE d.id=?""", (mandate_id,)
    ).fetchone()
    if row is None:
        flash("DebiCheck mandate not found.", "warning")
        return redirect(url_for("debicheck.index"))
    mandate = decrypt_mandate(row)
    return render_template("debicheck/detail.html", mandate=mandate)


@debicheck_bp.route("/<int:mandate_id>/edit", methods=["GET", "POST"])
@permission_required("new_debicheck")
def edit(mandate_id):
    db = get_db()
    row = db.execute(
        """SELECT d.*, m.first_name, m.last_name
           FROM debicheck_mandates d JOIN members m ON m.id=d.member_id
           WHERE d.id=?""", (mandate_id,)
    ).fetchone()
    if row is None:
        flash("DebiCheck mandate not found.", "warning")
        return redirect(url_for("debicheck.index"))

    if request.method == "POST":
        form = request.form
        amount = float(form.get("instalment_amount", "0").strip() or 0)
        updates = {
            "account_name": form.get("account_name", "").strip(),
            "account_type": form.get("account_type", "").strip(),
            "account_number": form.get("account_number", "").strip(),
            "branch_code": form.get("branch_code", "").strip(),
            "client_ref1": form.get("client_ref1", "").strip(),
            "client_ref2": form.get("client_ref2", "").strip(),
            "frequency": form.get("frequency", "3").strip(),
            "no_installments": form.get("no_installments", "99").strip(),
            "tracking": form.get("tracking", "14").strip(),
            "submit_date": form.get("submit_date", "").strip(),
            "instalment_amount": amount,
            "instalment_rands": int(amount),
            "instalment_cents": round((amount - int(amount)) * 100),
            "id_type": form.get("id_type", "0").strip(),
            "id_number": form.get("id_number", "").strip(),
            "passport": form.get("passport", "").strip(),
            "juristic_account": form.get("juristic_account", "").strip(),
            "payer_type": form.get("payer_type", "self").strip(),
        }

        errors = validate_stored_mandate({**updates, "merchant_id": row["merchant_id"]})
        if errors:
            for error in errors:
                flash(error, "error")
            preview = dict(row)
            preview.update(updates)
            return render_template("debicheck/edit.html", mandate=preview, banks=BANKS, today=date.today().isoformat())

        encrypted = encrypt_mandate(updates)
        db.execute(
            """UPDATE debicheck_mandates SET
                 account_name=?, account_type=?, account_number=?, branch_code=?,
                 client_ref1=?, client_ref2=?, frequency=?, no_installments=?,
                 tracking=?, submit_date=?, instalment_amount=?, instalment_rands=?,
                 instalment_cents=?, id_type=?, id_number=?, passport=?, juristic_account=?,
                 payer_type=?
               WHERE id=?""",
            (encrypted["account_name"], encrypted["account_type"], encrypted["account_number"],
             encrypted["branch_code"], encrypted["client_ref1"], encrypted["client_ref2"],
             encrypted["frequency"], int(updates["no_installments"] or 99), encrypted["tracking"],
             encrypted["submit_date"], amount, updates["instalment_rands"], updates["instalment_cents"],
             encrypted["id_type"], encrypted["id_number"], encrypted["passport"], encrypted["juristic_account"],
             encrypted["payer_type"], mandate_id),
        )
        member_activity(db, row["member_id"], f"DebiCheck mandate #{mandate_id} details edited")
        db.commit()
        flash("Mandate details updated.", "success")
        return redirect(url_for("debicheck.detail", mandate_id=mandate_id))

    mandate = decrypt_mandate(row)
    return render_template("debicheck/edit.html", mandate=mandate, banks=BANKS, today=date.today().isoformat())


def _member_itensity_ref(db, member_id: int) -> str:
    row = db.execute("SELECT itensity_ref FROM members WHERE id=?", (member_id,)).fetchone()
    return str(row["itensity_ref"] or "").strip() if row else ""


def _push(db, mandate_id, mandate: dict, member_id: int):
    """Push a plaintext mandate dict to NuPay.

    Requires a real Itensity client reference first: NuPay's client_ref1 is
    derived from it (see mandate_from_member), and pushing with the
    placeholder FIT-<member_id> fallback would submit a mandate NuPay and
    Itensity can never reconcile against each other.
    """
    if not _member_itensity_ref(db, member_id):
        flash(
            "This member hasn't been pushed to Itensity yet. Push to Itensity "
            "first so NuPay has a real client reference to use.",
            "error",
        )
        return redirect(url_for("debicheck.detail", mandate_id=mandate_id))
    try:
        success, message = push_mandate_to_nupay(mandate)
    except Exception as exc:
        success, message = False, f"NuPay integration error: {exc}"
    record_push_result(db, mandate_id, success, message)
    member_activity(
        db, member_id,
        f"DebiCheck mandate #{mandate_id} "
        f"{'submitted to NuPay' if success else 'push failed'}: {message}",
    )
    db.commit()
    flash(
        ("Mandate sent to NuPay for client approval: " if success else "NuPay push failed: ") + message,
        "success" if success else "error",
    )
    if success:
        return redirect(url_for("members.member_detail", mid=member_id) + "#docs")
    return redirect(url_for("debicheck.detail", mandate_id=mandate_id))


@debicheck_bp.post("/<int:mandate_id>/push")
@permission_required("new_debicheck")
def push(mandate_id):
    db = get_db()
    row = db.execute("SELECT * FROM debicheck_mandates WHERE id=?", (mandate_id,)).fetchone()
    if row is None:
        flash("DebiCheck mandate not found.", "warning")
        return redirect(url_for("debicheck.index"))
    if not _member_itensity_ref(db, row["member_id"]):
        flash(
            "This member hasn't been pushed to Itensity yet. Push to Itensity "
            "first so NuPay has a real client reference to use.",
            "error",
        )
        return redirect(url_for("debicheck.detail", mandate_id=mandate_id))
    mandate = decrypt_mandate(row)
    errors = validate_stored_mandate(mandate)
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("debicheck.detail", mandate_id=mandate_id))
    return _push(db, mandate_id, mandate, row["member_id"])


def _background_manual_review_push(
    app, db_path: str, mandate_id: int, mandate: dict, member_id: int
) -> None:
    """Runs the visible-browser, human-paced NuPay push off the request
    thread — it blocks until the person finishes in the window (or closes
    it), which can take minutes. Opens its own sqlite connection since the
    request's is gone by the time this returns."""
    conn = sqlite3.connect(db_path)
    try:
        with app.app_context():
            try:
                success, message = push_mandate_to_nupay_manual_review(mandate)
            except Exception as exc:
                success, message = False, f"NuPay integration error: {exc}"
            record_push_result(conn, mandate_id, success, message)
            member_activity(
                conn, member_id,
                f"DebiCheck mandate #{mandate_id} "
                f"{'submitted to NuPay (reviewed in browser)' if success else 'manual-review push ended'}: {message}",
            )
            conn.commit()
    except Exception:
        app.logger.exception(
            "Background manual-review NuPay push crashed for mandate %s", mandate_id
        )
        try:
            conn.execute(
                "UPDATE debicheck_mandates SET status='failed', "
                "nupay_response='Manual-review push crashed — see server logs.' WHERE id=?",
                (mandate_id,),
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


@debicheck_bp.post("/<int:mandate_id>/review-on-nupay")
@permission_required("new_debicheck")
def review_on_nupay(mandate_id):
    db = get_db()
    row = db.execute("SELECT * FROM debicheck_mandates WHERE id=?", (mandate_id,)).fetchone()
    if row is None:
        flash("DebiCheck mandate not found.", "warning")
        return redirect(url_for("debicheck.index"))
    if row["status"] == "reviewing":
        flash("This mandate already has a NuPay review window open. Finish or close it first.", "warning")
        return redirect(url_for("debicheck.detail", mandate_id=mandate_id))
    if not _member_itensity_ref(db, row["member_id"]):
        flash(
            "This member hasn't been pushed to Itensity yet. Push to Itensity "
            "first so NuPay has a real client reference to use.",
            "error",
        )
        return redirect(url_for("debicheck.detail", mandate_id=mandate_id))

    mandate = decrypt_mandate(row)
    errors = validate_stored_mandate(mandate)
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("debicheck.detail", mandate_id=mandate_id))

    db.execute("UPDATE debicheck_mandates SET status='reviewing' WHERE id=?", (mandate_id,))
    member_activity(
        db, row["member_id"],
        f"DebiCheck mandate #{mandate_id}: opened on NuPay for manual review — "
        "a browser window is open on the server for Submit/CONFIRM/DONE.",
    )
    db.commit()

    db_path = db.execute("PRAGMA database_list").fetchone()["file"]
    app_obj = current_app._get_current_object()
    threading.Thread(
        target=_background_manual_review_push,
        args=(app_obj, db_path, mandate_id, mandate, row["member_id"]),
        daemon=True,
    ).start()

    flash(
        "A NuPay browser window is opening on the server — review the filled-in details there "
        "and click Submit → CONFIRM → DONE yourself. This page will show the real result once "
        "you're done (refresh, or use Check NuPay Status).",
        "info",
    )
    return redirect(url_for("debicheck.detail", mandate_id=mandate_id))


@debicheck_bp.post("/<int:mandate_id>/check-status")
@permission_required("debicheck_mandates")
def check_status(mandate_id):
    db = get_db()
    row = db.execute("SELECT * FROM debicheck_mandates WHERE id=?", (mandate_id,)).fetchone()
    if row is None:
        flash("DebiCheck mandate not found.", "warning")
        return redirect(url_for("debicheck.index"))

    mandate = decrypt_mandate(row)
    member_row = db.execute(
        "SELECT join_date FROM members WHERE id = ?", (row["member_id"],)
    ).fetchone()
    mandate["member_join_date"] = member_row["join_date"] if member_row else None
    try:
        found, status, message = check_mandate_status_on_nupay(mandate)
    except Exception as exc:
        found, status, message = False, "unknown", f"Status check failed: {exc}"

    response_note = f"[Status check {date.today().isoformat()}] {message}"
    if "Only a pre-join match found" in message:
        db.execute(
            "UPDATE debicheck_mandates SET status='unverified', nupay_response=? WHERE id=?",
            (response_note, mandate_id),
        )
    elif found and status and status != "unknown":
        db.execute(
            "UPDATE debicheck_mandates SET status=?, nupay_response=? WHERE id=?",
            (status, response_note, mandate_id),
        )
    else:
        db.execute(
            "UPDATE debicheck_mandates SET nupay_response=? WHERE id=?",
            (response_note, mandate_id),
        )
    # Only a confirmed NuPay approval/authorisation satisfies the
    # application's DebiCheck compliance gate.
    application_debit_status = sync_debicheck_to_application(
        db, mandate_id, actor_id=session.get("user_id")
    )
    member_activity(
        db, row["member_id"],
        f"DebiCheck mandate #{mandate_id} status check: {message}"
        + (f" | application gate: {application_debit_status}" if application_debit_status else ""),
    )
    db.commit()

    flash(message, "success" if found else "warning")
    return redirect(url_for("debicheck.detail", mandate_id=mandate_id))
