from datetime import date
from math import isfinite

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from .auth import permission_required
from .database import get_db

operations_bp = Blueprint("operations", __name__, url_prefix="/operations")

EQUIPMENT_TYPES = ["cardio", "weights", "machines", "stretching", "functional", "group", "general"]
CONDITIONS = ["excellent", "good", "fair", "needs repair", "out of service"]


def _equipment_fields(form):
    name = form.get("name", "").strip()
    equipment_type = form.get("equipment_type", "general")
    purchase_date = form.get("purchase_date", "").strip()
    condition = form.get("condition", "good")
    if not name:
        raise ValueError("Equipment name is required.")
    if equipment_type not in EQUIPMENT_TYPES:
        raise ValueError("Select a valid equipment type.")
    if condition not in CONDITIONS:
        raise ValueError("Select a valid equipment condition.")
    if purchase_date:
        purchase_date = _date_field(purchase_date, "purchase date").isoformat()
    return (name, equipment_type, form.get("serial_number", "").strip(), purchase_date,
            condition, form.get("location", "").strip(), form.get("notes", "").strip())


def _date_field(value, label):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Enter a valid {label}.") from None


@operations_bp.route("/")
@permission_required("equipment")
def equipment_index():
    db = get_db()
    search = request.args.get("q", "").strip()
    condition_filter = request.args.get("condition", "").strip()
    show_inactive = request.args.get("inactive", "")

    query = "SELECT * FROM equipment WHERE 1=1"
    params = []
    if not show_inactive:
        query += " AND active = 1"
    if search:
        query += " AND (name LIKE ? OR location LIKE ? OR serial_number LIKE ?)"
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if condition_filter:
        query += " AND condition = ?"
        params.append(condition_filter)
    query += " ORDER BY name"

    items = db.execute(query, params).fetchall()

    summary = db.execute(
        "SELECT condition, COUNT(*) AS cnt FROM equipment WHERE active=1 GROUP BY condition ORDER BY cnt DESC"
    ).fetchall()

    return render_template(
        "operations/index.html",
        items=items,
        search=search,
        condition_filter=condition_filter,
        show_inactive=show_inactive,
        conditions=CONDITIONS,
        summary=summary,
    )


@operations_bp.route("/add", methods=["GET", "POST"])
@permission_required("equipment")
def equipment_add():
    if request.method == "POST":
        try:
            fields = _equipment_fields(request.form)
        except ValueError as error:
            flash(str(error), "error")
        else:
            db = get_db()
            db.execute(
                """INSERT INTO equipment (name, equipment_type, serial_number, purchase_date, condition, location, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                fields,
            )
            db.commit()
            flash(f"'{fields[0]}' added to equipment.", "success")
            return redirect(url_for("operations.equipment_index"))

    return render_template("operations/add.html", types=EQUIPMENT_TYPES, conditions=CONDITIONS)


@operations_bp.route("/<int:eid>/edit", methods=["GET", "POST"])
@permission_required("equipment")
def equipment_edit(eid: int):
    db = get_db()
    item = db.execute("SELECT * FROM equipment WHERE id = ?", (eid,)).fetchone()
    if item is None:
        flash("Equipment not found.", "warning")
        return redirect(url_for("operations.equipment_index"))

    if request.method == "POST":
        try:
            fields = _equipment_fields(request.form)
        except ValueError as error:
            flash(str(error), "error")
        else:
            active = 1 if request.form.get("active") else 0
            db.execute(
                """UPDATE equipment
                   SET name=?, equipment_type=?, serial_number=?, purchase_date=?,
                       condition=?, location=?, notes=?, active=?
                   WHERE id=?""",
                (*fields, active, eid),
            )
            db.commit()
            flash("Equipment updated.", "success")
            return redirect(url_for("operations.equipment_index"))

    return render_template("operations/edit.html", item=item, types=EQUIPMENT_TYPES, conditions=CONDITIONS)


@operations_bp.route("/<int:eid>/maintenance")
@permission_required("maintenance")
def maintenance_log(eid: int):
    db = get_db()
    item = db.execute("SELECT * FROM equipment WHERE id = ?", (eid,)).fetchone()
    if item is None:
        flash("Equipment not found.", "warning")
        return redirect(url_for("operations.equipment_index"))

    logs = db.execute(
        """SELECT ml.*, u.full_name AS logged_by_name
           FROM maintenance_log ml
           LEFT JOIN users u ON ml.created_by = u.id
           WHERE ml.equipment_id = ?
           ORDER BY ml.maintenance_date DESC""",
        (eid,),
    ).fetchall()

    total_cost = sum(l["cost"] or 0 for l in logs)
    return render_template("operations/maintenance.html", item=item, logs=logs, total_cost=total_cost)


@operations_bp.post("/<int:eid>/maintenance/add")
@permission_required("maintenance")
def maintenance_add(eid: int):
    db = get_db()
    if db.execute("SELECT 1 FROM equipment WHERE id = ?", (eid,)).fetchone() is None:
        flash("Equipment not found.", "warning")
        return redirect(url_for("operations.equipment_index"))
    description = request.form.get("description", "").strip()
    maintenance_date = request.form.get("maintenance_date", "").strip()
    performed_by = request.form.get("performed_by", "").strip()
    cost_value = request.form.get("cost", "0").strip()
    next_due_date = request.form.get("next_due_date", "").strip()
    notes = request.form.get("notes", "").strip()

    try:
        if not description:
            raise ValueError("Description is required.")
        maintenance_date = _date_field(maintenance_date, "maintenance date").isoformat()
        if next_due_date:
            next_due_date = _date_field(next_due_date, "next due date").isoformat()
            if next_due_date < maintenance_date:
                raise ValueError("The next due date must be on or after the maintenance date.")
        try:
            cost = float(cost_value or 0)
        except ValueError:
            cost = None
        if cost is None or not isfinite(cost) or cost < 0:
            raise ValueError("Enter a valid, non-negative maintenance cost.")
    except ValueError as error:
        flash(str(error), "error")
    else:
        db.execute(
            """INSERT INTO maintenance_log
               (equipment_id, maintenance_date, description, performed_by, cost, next_due_date, notes, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, maintenance_date, description, performed_by, cost, next_due_date, notes, session.get("user_id")),
        )
        db.commit()
        flash("Maintenance record logged.", "success")

    return redirect(url_for("operations.maintenance_log", eid=eid))
