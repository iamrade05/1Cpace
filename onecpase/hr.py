from datetime import date, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for, current_app, g, abort
from werkzeug.security import generate_password_hash
from .auth import login_required, permission_required, valid_email_address
from .database import get_db
from .permissions import MODULE_PERMISSIONS, ALL_PERMISSIONS
from .hr_access import can_access_payroll

hr_bp = Blueprint("hr", __name__, url_prefix="/hr")

ROLES = ["admin", "manager", "staff", "trainer", "receptionist"]
DEPARTMENTS = ["Management", "Sales", "Fitness", "Training", "Reception", "Cleaner", "Group Instructors", "Finance", "Maintenance", "General"]
LEAVE_TYPES = ["annual", "sick", "family", "unpaid", "study"]
EMPLOYEE_ROLES = ["Personal Trainer", "Head Trainer", "Receptionist", "Nutritionist", "Maintenance", "Manager", "Other"]
STAFF_LEAVE_TYPES = [
    "Annual Leave", "Sick Leave", "Family Responsibility", "Study Leave", "Maternity Leave", "Unpaid Leave"
]


@hr_bp.before_request
def protect_legacy_hr():
    """Legacy permission labels grant navigation, never broad HR mutation rights."""
    from .hr_workforce import actor, is_admin
    from .auth import session_tenant_ok
    if not session.get('user_id') or not session_tenant_ok():
        return None
    db = get_db()
    user = actor(db)
    if not user or user['is_platform_user']:
        abort(403)
    sensitive = {'hr.employee_add','hr.employee_edit','hr.employee_detail',
                 'hr.attendance_log','hr.employee_index','hr.access_attendance'}
    if request.endpoint in sensitive and not is_admin(db):
        abort(403)


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
        elif not valid_email_address(email):
            flash("Enter a valid email address. Staff accounts need one for OTP sign-in.", "error")
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
@permission_required("user_management")
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

        if not valid_email_address(email):
            flash("Enter a valid email address. Staff accounts need one for OTP sign-in.", "error")
        else:
            try:
                if new_password:
                    db.execute(
                        """UPDATE users SET full_name=?, role=?, department=?, contact=?, email=?,
                           active=?, must_change_password=?, password_hash=?, session_version=session_version+1 WHERE id=?""",
                        (full_name, role, department, contact, email, active, must_change,
                         generate_password_hash(new_password), uid),
                    )
                else:
                    db.execute(
                        """UPDATE users SET full_name=?, role=?, department=?, contact=?, email=?,
                           active=?, must_change_password=? WHERE id=?""",
                        (full_name, role, department, contact, email, active, must_change, uid),
                    )
                    if active != user['active'] or email != user['email']:
                        db.execute('UPDATE users SET session_version=session_version+1 WHERE id=?', (uid,))

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
    from .hr_workforce_routes import leave_list
    return leave_list()


@hr_bp.route("/leave/request", methods=["GET", "POST"])
@login_required
def leave_request():
    from .hr_workforce_routes import request_leave
    return request_leave()


@hr_bp.post("/leave/<int:lid>/approve")
@permission_required("hr_module")
def leave_approve(lid: int):
    from .hr_workforce_routes import leave_decision
    return leave_decision(lid=lid, action='approve')


@hr_bp.post("/leave/<int:lid>/reject")
@permission_required("hr_module")
def leave_reject(lid: int):
    from .hr_workforce_routes import leave_decision
    return leave_decision(lid=lid, action='reject')


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
                    as_float(request.form.get("salary")) if can_access_payroll() else 0,
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
                    as_float(request.form.get("salary")) if can_access_payroll() else employee['salary'],
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
    can_view_payroll = can_access_payroll()
    payroll = db.execute(
        "SELECT * FROM payroll WHERE staff_id = ? ORDER BY month DESC, id DESC LIMIT 12", (sid,)
    ).fetchall() if can_view_payroll else []

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
    from .hr_workforce import is_admin
    if not is_admin(get_db()):
        abort(403)
    flash('Employees must sign and submit their own leave request from My HR.', 'info')
    return redirect(url_for('workforce.dashboard'))


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

@hr_bp.get("/access-attendance")
@permission_required("hr_timesheets")
def access_attendance():
    import json
    from datetime import datetime, timezone
    from pathlib import Path
    from .staff_attendance import build_week

    allowed_slug=current_app.config.get('ACCESS_REPORT_TENANT_SLUG','elev8')
    tenant_slug=g.get('tenant_slug') or dict(g.get('tenant') or {}).get('slug')
    if tenant_slug and tenant_slug!=allowed_slug:
        abort(404)
    today=datetime.now(timezone(timedelta(hours=2))).date()
    week=week_bounds(parse_date_value(request.args.get('week'),today))[0]
    path=Path(current_app.config.get('STAFF_ACCESS_SNAPSHOT') or
              Path(current_app.config['UPLOAD_FOLDER']).parent/'staff-access'/'current.json')
    archived=path.parent/(week.isoformat()+'.json')
    if not current_app.config.get('STAFF_ACCESS_SNAPSHOT') and archived.is_file():
        path=archived
    report=None
    error=None
    try:
        snapshot=json.loads(path.read_text(encoding='utf-8'))
        if snapshot.get('tenant_slug')!=allowed_slug:
            abort(404)
        report=build_week(snapshot,week)
    except (OSError,ValueError,KeyError,TypeError):
        error='No usable staff access import is available. Refresh the gym access snapshot to view attendance.'
    return render_template('hr/access_attendance.html',report=report,error=error)


@hr_bp.get("/timesheets")
@permission_required("hr_timesheets")
def timesheets_index():
    return redirect(url_for('workforce.weeks', week=request.args.get('week')))


@hr_bp.post("/timesheets/save")
@permission_required("hr_timesheets")
def timesheets_save():
    abort(409, description='Use the versioned weekly timesheet to save and sign your hours.')


@hr_bp.get("/timesheets/<int:sid>")
@permission_required("hr_timesheets")
def timesheet_staff_week(sid: int):
    from .hr_workforce import is_admin, visible_ids
    p = get_db().execute('SELECT user_id FROM hr_profiles WHERE staff_id=?', (sid,)).fetchone()
    if not p or p['user_id'] not in visible_ids(get_db()):
        abort(403)
    return redirect(url_for('workforce.weeks', user_id=p['user_id'], week=request.args.get('week')))
