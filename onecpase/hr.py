from datetime import date, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
from .auth import login_required, permission_required
from .database import get_db
from .permissions import MODULE_PERMISSIONS, ALL_PERMISSIONS

hr_bp = Blueprint("hr", __name__, url_prefix="/hr")

ROLES = ["admin", "manager", "staff", "trainer", "receptionist"]
DEPARTMENTS = ["Management", "Training", "Reception", "Finance", "Maintenance", "General"]
LEAVE_TYPES = ["annual", "sick", "family", "unpaid", "study"]
EMPLOYEE_ROLES = ["Personal Trainer", "Head Trainer", "Receptionist", "Nutritionist", "Maintenance", "Manager", "Other"]
STAFF_LEAVE_TYPES = [
    "Annual Leave", "Sick Leave", "Family Responsibility", "Study Leave", "Maternity Leave", "Unpaid Leave"
]


# ── Small helpers ─────────────────────────────────────────────────────────────

def as_float(value, default: float = 0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_date_value(value, fallback: date | None = None) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return fallback or date.today()


def week_bounds(anchor_day: date) -> tuple[date, date]:
    start = anchor_day - timedelta(days=anchor_day.weekday())
    end = start + timedelta(days=6)
    return start, end


def calculate_hours_worked(time_in, lunch_out, lunch_in, time_out) -> float:
    def minutes(value):
        if not value:
            return None
        try:
            hour, minute = map(int, str(value).split(":"))
        except ValueError:
            return None
        return hour * 60 + minute

    start = minutes(time_in)
    end = minutes(time_out)
    if start is None or end is None or end <= start:
        return 0.0

    total = end - start
    lunch_start = minutes(lunch_out)
    lunch_end = minutes(lunch_in)
    if lunch_start is not None and lunch_end is not None and lunch_end > lunch_start:
        total -= lunch_end - lunch_start
    return round(max(total, 0) / 60, 2)


@hr_bp.route("/")
@permission_required("hr_staff")
def staff_index():
    db = get_db()
    search = request.args.get("q", "").strip()
    query = "SELECT * FROM users WHERE 1=1"
    params = []
    if search:
        query += " AND (full_name LIKE ? OR username LIKE ? OR department LIKE ?)"
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    query += " ORDER BY full_name"
    staff = db.execute(query, params).fetchall()
    return render_template("hr/index.html", staff=staff, search=search)


@hr_bp.route("/add", methods=["GET", "POST"])
@permission_required("user_management")
def staff_add():
    db = get_db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "staff")
        department = request.form.get("department", "General")
        contact = request.form.get("contact", "").strip()
        email = request.form.get("email", "").strip()

        if not username or not full_name or not password:
            flash("Username, full name and password are required.", "error")
        else:
            try:
                cursor = db.execute(
                    """INSERT INTO users (username, password_hash, full_name, role, department, contact, email)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (username, generate_password_hash(password), full_name, role, department, contact, email),
                )
                new_id = cursor.lastrowid

                # Save selected permissions
                selected = {k[5:] for k in request.form if k.startswith("perm_") and k[5:] in ALL_PERMISSIONS}
                for perm in selected:
                    db.execute(
                        "INSERT OR IGNORE INTO user_permissions (user_id, permission) VALUES (?, ?)",
                        (new_id, perm),
                    )
                db.commit()
                flash(f"Staff member {full_name} added.", "success")
                return redirect(url_for("hr.staff_index"))
            except Exception as e:
                if "UNIQUE" in str(e):
                    flash("Username already exists.", "error")
                else:
                    flash(f"Error: {e}", "error")

    return render_template(
        "hr/add.html",
        roles=ROLES,
        departments=DEPARTMENTS,
        module_permissions=MODULE_PERMISSIONS,
        user_perms=set(),
    )


@hr_bp.route("/<int:uid>/edit", methods=["GET", "POST"])
@permission_required("hr_staff")
def staff_edit(uid: int):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user is None:
        flash("Staff member not found.", "warning")
        return redirect(url_for("hr.staff_index"))

    # Current permissions for this user
    current_perms = {
        r["permission"]
        for r in db.execute("SELECT permission FROM user_permissions WHERE user_id = ?", (uid,)).fetchall()
    }

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        role = request.form.get("role", "staff")
        department = request.form.get("department", "General")
        contact = request.form.get("contact", "").strip()
        email = request.form.get("email", "").strip()
        active = 1 if request.form.get("active") else 0
        must_change = 1 if request.form.get("must_change_password") else 0
        new_password = request.form.get("password", "").strip()

        try:
            if new_password:
                db.execute(
                    """UPDATE users SET full_name=?, role=?, department=?, contact=?, email=?,
                       active=?, must_change_password=?, password_hash=? WHERE id=?""",
                    (full_name, role, department, contact, email, active, must_change,
                     generate_password_hash(new_password), uid),
                )
            else:
                db.execute(
                    """UPDATE users SET full_name=?, role=?, department=?, contact=?, email=?,
                       active=?, must_change_password=? WHERE id=?""",
                    (full_name, role, department, contact, email, active, must_change, uid),
                )

            # Replace permissions
            selected = {k[5:] for k in request.form if k.startswith("perm_") and k[5:] in ALL_PERMISSIONS}
            db.execute("DELETE FROM user_permissions WHERE user_id = ?", (uid,))
            for perm in selected:
                db.execute(
                    "INSERT INTO user_permissions (user_id, permission) VALUES (?, ?)", (uid, perm)
                )
            db.commit()
            flash("Staff record updated.", "success")
            return redirect(url_for("hr.staff_index"))
        except Exception as e:
            flash(f"Error: {e}", "error")

    return render_template(
        "hr/edit.html",
        user=user,
        roles=ROLES,
        departments=DEPARTMENTS,
        module_permissions=MODULE_PERMISSIONS,
        user_perms=current_perms,
    )


# ── Leave requests ───────────────────────────────────────────────────────────

@hr_bp.route("/leave")
@login_required
def leave_index():
    db = get_db()
    status_filter = request.args.get("status", "").strip()
    query = """
        SELECT l.*, u.full_name, u.department,
               a.full_name AS approver_name
        FROM leave_requests l
        JOIN users u ON l.user_id = u.id
        LEFT JOIN users a ON l.approved_by = a.id
        WHERE 1=1
    """
    params = []
    if status_filter:
        query += " AND l.status = ?"
        params.append(status_filter)
    query += " ORDER BY l.created_at DESC"
    leaves = db.execute(query, params).fetchall()
    return render_template("hr/leave.html", leaves=leaves, status_filter=status_filter)


@hr_bp.route("/leave/request", methods=["GET", "POST"])
@login_required
def leave_request():
    db = get_db()
    if request.method == "POST":
        leave_type = request.form.get("leave_type", "annual")
        start_date = request.form.get("start_date", "")
        end_date = request.form.get("end_date", "")
        days = request.form.get("days", "1")
        reason = request.form.get("reason", "").strip()
        try:
            db.execute(
                """INSERT INTO leave_requests (user_id, leave_type, start_date, end_date, days, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session["user_id"], leave_type, start_date, end_date, int(days or 1), reason),
            )
            db.commit()
            flash("Leave request submitted.", "success")
            return redirect(url_for("hr.leave_index"))
        except Exception as e:
            flash(f"Error: {e}", "error")
    return render_template("hr/leave_request.html", leave_types=LEAVE_TYPES)


@hr_bp.post("/leave/<int:lid>/approve")
@permission_required("hr_module")
def leave_approve(lid: int):
    db = get_db()
    db.execute(
        "UPDATE leave_requests SET status='approved', approved_by=?, approved_at=datetime('now') WHERE id=?",
        (session["user_id"], lid),
    )
    db.commit()
    flash("Leave request approved.", "success")
    return redirect(url_for("hr.leave_index"))


@hr_bp.post("/leave/<int:lid>/reject")
@permission_required("hr_module")
def leave_reject(lid: int):
    db = get_db()
    db.execute(
        "UPDATE leave_requests SET status='rejected', approved_by=?, approved_at=datetime('now') WHERE id=?",
        (session["user_id"], lid),
    )
    db.commit()
    flash("Leave request rejected.", "info")
    return redirect(url_for("hr.leave_index"))


# ── Employee register ────────────────────────────────────────────────────────

@hr_bp.route("/employees")
@permission_required("hr_staff")
def employee_index():
    db = get_db()
    search = request.args.get("q", "").strip()
    query = "SELECT * FROM staff WHERE 1=1"
    params = []
    if search:
        query += " AND (first_name LIKE ? OR last_name LIKE ? OR role LIKE ?)"
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    query += " ORDER BY first_name, last_name"
    employees = db.execute(query, params).fetchall()
    return render_template("hr/employees.html", employees=employees, search=search)


@hr_bp.route("/employees/add", methods=["GET", "POST"])
@permission_required("hr_staff")
def employee_add():
    if request.method == "POST":
        first_name = str(request.form.get("first_name", "")).strip()
        last_name = str(request.form.get("last_name", "")).strip()
        if not first_name or not last_name:
            flash("First and last name are required.", "error")
        else:
            get_db().execute(
                """
                INSERT INTO staff (
                    first_name, last_name, id_number, role, department, contact, email,
                    start_date, contract_end, salary, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    first_name,
                    last_name,
                    str(request.form.get("id_number", "")).strip() or None,
                    str(request.form.get("role", "")).strip() or None,
                    str(request.form.get("department", "General")).strip() or "General",
                    str(request.form.get("contact", "")).strip() or None,
                    str(request.form.get("email", "")).strip() or None,
                    str(request.form.get("start_date", "")).strip() or date.today().isoformat(),
                    str(request.form.get("contract_end", "")).strip() or None,
                    as_float(request.form.get("salary")),
                ),
            )
            get_db().commit()
            flash("Staff member added to the register.", "success")
            return redirect(url_for("hr.employee_index"))
    return render_template("hr/employee_form.html", roles=EMPLOYEE_ROLES, departments=DEPARTMENTS)


@hr_bp.route("/employees/<int:sid>/edit", methods=["GET", "POST"])
@permission_required("hr_staff")
def employee_edit(sid: int):
    db = get_db()
    employee = db.execute("SELECT * FROM staff WHERE id = ?", (sid,)).fetchone()
    if employee is None:
        flash("Staff member not found.", "warning")
        return redirect(url_for("hr.employee_index"))

    if request.method == "POST":
        first_name = str(request.form.get("first_name", "")).strip()
        last_name = str(request.form.get("last_name", "")).strip()
        if not first_name or not last_name:
            flash("First and last name are required.", "error")
        else:
            db.execute(
                """
                UPDATE staff SET first_name=?, last_name=?, id_number=?, role=?, department=?,
                       contact=?, email=?, start_date=?, contract_end=?, salary=?, status=?
                WHERE id=?
                """,
                (
                    first_name,
                    last_name,
                    str(request.form.get("id_number", "")).strip() or None,
                    str(request.form.get("role", "")).strip() or None,
                    str(request.form.get("department", "General")).strip() or "General",
                    str(request.form.get("contact", "")).strip() or None,
                    str(request.form.get("email", "")).strip() or None,
                    str(request.form.get("start_date", "")).strip() or None,
                    str(request.form.get("contract_end", "")).strip() or None,
                    as_float(request.form.get("salary")),
                    str(request.form.get("status", "active")).strip() or "active",
                    sid,
                ),
            )
            db.commit()
            flash("Staff record updated.", "success")
            return redirect(url_for("hr.employee_index"))

    return render_template(
        "hr/employee_form.html", employee=employee, roles=EMPLOYEE_ROLES, departments=DEPARTMENTS
    )


@hr_bp.get("/employees/<int:sid>")
@permission_required("hr_staff")
def employee_detail(sid: int):
    db = get_db()
    employee = db.execute("SELECT * FROM staff WHERE id = ?", (sid,)).fetchone()
    if employee is None:
        flash("Staff member not found.", "warning")
        return redirect(url_for("hr.employee_index"))

    attendance = db.execute(
        "SELECT * FROM attendance WHERE staff_id = ? ORDER BY date DESC, id DESC LIMIT 10", (sid,)
    ).fetchall()
    leave = db.execute(
        "SELECT * FROM leave_requests WHERE staff_id = ? ORDER BY start_date DESC, id DESC LIMIT 10", (sid,)
    ).fetchall()
    payroll = db.execute(
        "SELECT * FROM payroll WHERE staff_id = ? ORDER BY month DESC, id DESC LIMIT 12", (sid,)
    ).fetchall()

    return render_template(
        "hr/employee_detail.html", s=employee, attendance=attendance, leave=leave, payroll=payroll
    )


@hr_bp.post("/attendance/log")
@permission_required("hr_staff")
def attendance_log():
    db = get_db()
    staff_id = request.form.get("staff_id")
    if not staff_id:
        flash("Please select a staff member.", "error")
        return redirect(url_for("hr.employee_index"))

    today_iso = date.today().isoformat()
    existing = db.execute(
        "SELECT id FROM attendance WHERE staff_id = ? AND date = ? ORDER BY id DESC LIMIT 1",
        (staff_id, today_iso),
    ).fetchone()
    values = (
        str(request.form.get("clock_in", "")).strip() or None,
        str(request.form.get("clock_out", "")).strip() or None,
        str(request.form.get("status", "present")).strip() or "present",
    )
    if existing:
        db.execute(
            "UPDATE attendance SET clock_in = ?, clock_out = ?, status = ? WHERE id = ?",
            (*values, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO attendance (clock_in, clock_out, status, date, staff_id) VALUES (?, ?, ?, ?, ?)",
            (*values, today_iso, staff_id),
        )
    db.commit()
    flash("Attendance logged.", "success")
    return redirect(url_for("hr.employee_detail", sid=staff_id))


@hr_bp.route("/employees/<int:sid>/leave/add", methods=["GET", "POST"])
@permission_required("hr_leave")
def staff_leave_add(sid: int):
    db = get_db()
    employee = db.execute("SELECT * FROM staff WHERE id = ?", (sid,)).fetchone()
    if employee is None:
        flash("Staff member not found.", "warning")
        return redirect(url_for("hr.employee_index"))

    if request.method == "POST":
        db.execute(
            """
            INSERT INTO leave_requests (staff_id, leave_type, start_date, end_date, reason, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (
                sid,
                str(request.form.get("leave_type", "Annual Leave")).strip() or "Annual Leave",
                str(request.form.get("start_date", "")).strip() or None,
                str(request.form.get("end_date", "")).strip() or None,
                str(request.form.get("reason", "")).strip() or None,
            ),
        )
        db.commit()
        flash("Leave request logged.", "success")
        return redirect(url_for("hr.employee_detail", sid=sid))

    return render_template("hr/employee_leave_form.html", s=employee, leave_types=STAFF_LEAVE_TYPES)


# ── Payroll ───────────────────────────────────────────────────────────────────

@hr_bp.get("/payroll")
@permission_required("hr_payroll")
def payroll_index():
    month = request.args.get("month") or date.today().strftime("%Y-%m")
    rows = get_db().execute(
        """
        SELECT p.*, s.role, TRIM(s.first_name || ' ' || s.last_name) AS staff_name
        FROM payroll p
        JOIN staff s ON s.id = p.staff_id
        WHERE p.month = ?
        ORDER BY staff_name
        """,
        (month,),
    ).fetchall()
    total_net = sum(as_float(row["net"]) for row in rows)
    total_pending = sum(1 for row in rows if row["status"] == "pending")
    return render_template(
        "hr/payroll.html", month=month, rows=rows, total_net=total_net, total_pending=total_pending
    )


@hr_bp.post("/payroll/<int:pid>/pay")
@permission_required("hr_payroll")
def payroll_pay(pid: int):
    month = request.form.get("month") or date.today().strftime("%Y-%m")
    get_db().execute(
        "UPDATE payroll SET status = 'paid', paid_on = ? WHERE id = ?",
        (date.today().isoformat(), pid),
    )
    get_db().commit()
    flash("Payroll record marked as paid.", "success")
    return redirect(url_for("hr.payroll_index", month=month))


# ── Timesheets ────────────────────────────────────────────────────────────────

@hr_bp.get("/timesheets")
@permission_required("hr_timesheets")
def timesheets_index():
    requested_week = parse_date_value(request.args.get("week"))
    week_start, week_end = week_bounds(requested_week)
    week = [week_start + timedelta(days=offset) for offset in range(7)]
    rows = get_db().execute(
        "SELECT * FROM timesheets WHERE work_date >= ? AND work_date <= ?",
        (week_start.isoformat(), week_end.isoformat()),
    ).fetchall()
    ts_map = {(row["staff_id"], row["work_date"]): row for row in rows}
    totals = {}
    for row in rows:
        totals[row["staff_id"]] = round(totals.get(row["staff_id"], 0.0) + as_float(row["hours_worked"]), 2)

    return render_template(
        "hr/timesheets.html",
        staff=get_db().execute("SELECT * FROM staff ORDER BY first_name, last_name").fetchall(),
        week=week,
        week_start=week_start.isoformat(),
        week_end=week_end.isoformat(),
        prev_week=(week_start - timedelta(days=7)).isoformat(),
        next_week=(week_start + timedelta(days=7)).isoformat(),
        ts_map=ts_map,
        totals=totals,
    )


@hr_bp.post("/timesheets/save")
@permission_required("hr_timesheets")
def timesheets_save():
    db = get_db()
    staff_id = request.form.get("staff_id")
    if not staff_id:
        flash("Please select a staff member.", "error")
        return redirect(url_for("hr.timesheets_index"))

    week_start = parse_date_value(request.form.get("week_start"))
    captured_by = str(request.form.get("captured_by", "")).strip() or session.get("full_name")

    for offset in range(7):
        current_day = week_start + timedelta(days=offset)
        ds = current_day.isoformat()
        time_in = str(request.form.get(f"ti_{ds}", "")).strip() or None
        lunch_out = str(request.form.get(f"lo_{ds}", "")).strip() or None
        lunch_in = str(request.form.get(f"li_{ds}", "")).strip() or None
        time_out = str(request.form.get(f"to_{ds}", "")).strip() or None
        notes = str(request.form.get(f"note_{ds}", "")).strip() or None
        existing = db.execute(
            "SELECT id FROM timesheets WHERE staff_id = ? AND work_date = ? ORDER BY id DESC LIMIT 1",
            (staff_id, ds),
        ).fetchone()

        if not any([time_in, lunch_out, lunch_in, time_out, notes]):
            if existing:
                db.execute("DELETE FROM timesheets WHERE id = ?", (existing["id"],))
            continue

        hours_worked = calculate_hours_worked(time_in, lunch_out, lunch_in, time_out)
        if existing:
            db.execute(
                """
                UPDATE timesheets
                SET time_in = ?, lunch_out = ?, lunch_in = ?, time_out = ?,
                    hours_worked = ?, notes = ?, captured_by = ?
                WHERE id = ?
                """,
                (time_in, lunch_out, lunch_in, time_out, hours_worked, notes, captured_by, existing["id"]),
            )
        else:
            db.execute(
                """
                INSERT INTO timesheets (
                    staff_id, work_date, time_in, lunch_out, lunch_in, time_out,
                    hours_worked, notes, captured_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (staff_id, ds, time_in, lunch_out, lunch_in, time_out, hours_worked, notes, captured_by),
            )

    db.commit()
    flash("Timesheet saved.", "success")
    return redirect(url_for("hr.timesheets_index", week=week_start.isoformat()))


@hr_bp.get("/timesheets/<int:sid>")
@permission_required("hr_timesheets")
def timesheet_staff_week(sid: int):
    db = get_db()
    employee = db.execute("SELECT * FROM staff WHERE id = ?", (sid,)).fetchone()
    if employee is None:
        flash("Staff member not found.", "warning")
        return redirect(url_for("hr.timesheets_index"))

    requested_week = parse_date_value(request.args.get("week"))
    week_start, _week_end = week_bounds(requested_week)
    week = [week_start + timedelta(days=offset) for offset in range(7)]
    rows = db.execute(
        "SELECT * FROM timesheets WHERE staff_id = ? AND work_date >= ? AND work_date <= ? ORDER BY work_date",
        (sid, week_start.isoformat(), (week_start + timedelta(days=6)).isoformat()),
    ).fetchall()
    ts_map = {row["work_date"]: row for row in rows}
    total_hrs = round(sum(as_float(row["hours_worked"]) for row in rows), 1)
    overtime = round(max(total_hrs - 45, 0), 1)

    return render_template(
        "hr/timesheet_staff.html",
        s=employee,
        week=week,
        week_start=week_start.isoformat(),
        prev_week=(week_start - timedelta(days=7)).isoformat(),
        next_week=(week_start + timedelta(days=7)).isoformat(),
        ts_map=ts_map,
        total_hrs=total_hrs,
        overtime=overtime,
    )
