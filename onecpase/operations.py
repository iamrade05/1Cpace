from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from .auth import login_required, permission_required
from .database import get_db

operations_bp = Blueprint("operations", __name__, url_prefix="/operations")

EQUIPMENT_TYPES = ["cardio", "weights", "machines", "stretching", "functional", "group", "general"]
CONDITIONS = ["excellent", "good", "fair", "needs repair", "out of service"]


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
        name = request.form.get("name", "").strip()
        equipment_type = request.form.get("equipment_type", "general")
        serial_number = request.form.get("serial_number", "").strip()
        purchase_date = request.form.get("purchase_date", "")
        condition = request.form.get("condition", "good")
        location = request.form.get("location", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name:
            flash("Equipment name is required.", "error")
        else:
            db = get_db()
            try:
                db.execute(
                    """INSERT INTO equipment (name, equipment_type, serial_number, purchase_date, condition, location, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (name, equipment_type, serial_number, purchase_date, condition, location, notes),
                )
                db.commit()
                flash(f"'{name}' added to equipment.", "success")
                return redirect(url_for("operations.equipment_index"))
            except Exception as e:
                flash(f"Error: {e}", "error")

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
        name = request.form.get("name", "").strip()
        equipment_type = request.form.get("equipment_type", "general")
        serial_number = request.form.get("serial_number", "").strip()
        purchase_date = request.form.get("purchase_date", "")
        condition = request.form.get("condition", "good")
        location = request.form.get("location", "").strip()
        notes = request.form.get("notes", "").strip()
        active = 1 if request.form.get("active") else 0

        try:
            db.execute(
                """UPDATE equipment
                   SET name=?, equipment_type=?, serial_number=?, purchase_date=?,
                       condition=?, location=?, notes=?, active=?
                   WHERE id=?""",
                (name, equipment_type, serial_number, purchase_date, condition, location, notes, active, eid),
            )
            db.commit()
            flash("Equipment updated.", "success")
            return redirect(url_for("operations.equipment_index"))
        except Exception as e:
            flash(f"Error: {e}", "error")

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
    description = request.form.get("description", "").strip()
    maintenance_date = request.form.get("maintenance_date", "")
    performed_by = request.form.get("performed_by", "").strip()
    cost = request.form.get("cost", "0").strip()
    next_due_date = request.form.get("next_due_date", "")
    notes = request.form.get("notes", "").strip()

    if not description:
        flash("Description is required.", "error")
        return redirect(url_for("operations.maintenance_log", eid=eid))

    try:
        db.execute(
            """INSERT INTO maintenance_log
               (equipment_id, maintenance_date, description, performed_by, cost, next_due_date, notes, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, maintenance_date, description, performed_by, float(cost or 0), next_due_date, notes, session.get("user_id")),
        )
        db.commit()
        flash("Maintenance record logged.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")

    return redirect(url_for("operations.maintenance_log", eid=eid))
