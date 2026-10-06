"""Collections > Reconciliation: NuPay, Itensity and this app compared for every member.

Read-only for member data. Uploading a NuPay Transaction Report only stores NuPay's own results, and
"Run check" only rebuilds the comparison.
"""
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from .auth import permission_required
from .database import get_db
from . import nupay_reconciliation as rec

reconciliation_bp = Blueprint("reconciliation", __name__, url_prefix="/collections/reconciliation")

MANAGERS = ("admin", "manager")


def _root() -> Path:
    return Path(current_app.root_path).parent


def _can_change() -> bool:
    return session.get("role") in MANAGERS


@reconciliation_bp.get("")
@permission_required("all_collections")
def overview():
    db = get_db()
    return render_template("collections/reconciliation.html", data=rec.overview(db), can_change=_can_change(),
                           has_roster=rec.latest_roster(_root()) is not None)


@reconciliation_bp.get("/members")
@permission_required("all_collections")
def members():
    db = get_db()
    group = request.args.get("group", "").strip()
    flag = request.args.get("flag", "").strip()
    query = request.args.get("q", "").strip()
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1
    rows, total = rec.member_list(db, group=group or None, flag=flag or None, query=query or None, page=page)
    return render_template(
        "collections/reconciliation_members.html",
        rows=rows, total=total, page=page, page_size=rec.PAGE_SIZE,
        group=group, flag=flag, query=query, labels=rec.GROUP_LABELS, order=rec.GROUP_ORDER,
    )


@reconciliation_bp.post("/upload")
@permission_required("all_collections")
def upload():
    if not _can_change():
        flash("Only a manager can upload a NuPay report.", "error")
        return redirect(url_for("reconciliation.overview"))
    files = [(f.filename or "", f.read()) for f in request.files.getlist("reports") if f and f.filename]
    if not files:
        flash("Choose one or more NuPay Transaction Report files (CSV or ZIP).", "warning")
        return redirect(url_for("reconciliation.overview"))
    try:
        rows = rec.read_upload(files)
    except Exception:
        current_app.logger.exception("NuPay upload could not be read")
        flash("That file could not be read. Upload the CSV files exactly as NuPay downloads them, or the ZIP.", "error")
        return redirect(url_for("reconciliation.overview"))
    if not rows:
        flash("No transactions were found in those files.", "warning")
        return redirect(url_for("reconciliation.overview"))
    db = get_db()
    stats = rec.store_upload(db, rows, [name for name, _ in files], session.get("user_id"))
    summary = rec.rebuild_snapshot(db, rec.latest_roster(_root()))
    db.commit()
    flash(
        f"Read {len(rows):,} rows: {stats.get('new', 0):,} new, {stats.get('already_stored', 0):,} already stored, "
        f"{stats.get('unmatched', 0):,} not matched to a member. Checked {summary['members']:,} members.",
        "success",
    )
    return redirect(url_for("reconciliation.overview"))


@reconciliation_bp.post("/rebuild")
@permission_required("all_collections")
def rebuild():
    if not _can_change():
        flash("Only a manager can run the check.", "error")
        return redirect(url_for("reconciliation.overview"))
    db = get_db()
    summary = rec.rebuild_snapshot(db, rec.latest_roster(_root()))
    db.commit()
    flash(f"Checked {summary['members']:,} members.", "success")
    return redirect(url_for("reconciliation.overview"))
