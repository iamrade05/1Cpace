import json

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
from .auth import login_required, session_tenant_ok
from .database import default_sections_config, default_terms_parts_config, get_db
from .permissions import MODULE_PERMISSIONS, ALL_PERMISSIONS
from .reports import AGG_FUNCS, FILTER_OPS, REPORT_TABLES

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

_VALID_ROLES = {"admin", "manager", "staff", "trainer", "reception"}

# The password a staff account is reset to. Deliberately not a secret: every
# path that sets it also sets must_change_password, so it survives only until
# that person's next sign-in and is never what they actually log in with twice.
DEFAULT_STAFF_PASSWORD = "Staff@123"

# Accounts that a default-password reset must never touch. The owner account
# has to stay reachable even if a bulk reset is run by mistake.
PROTECTED_USERNAMES = frozenset({"mvuleni.radebe"})


def is_protected_user(username) -> bool:
    return str(username or "").strip().lower() in PROTECTED_USERNAMES
_SALES_SKIP_COUNTS = {0: 0, 1: 3, 2: 5}


def _sales_disqualification(form, current_category=0, current_remaining=0):
    """Validate the category and preserve a partially consumed penalty."""
    try:
        category = int(form.get("sales_disqualification_category", "0") or 0)
    except ValueError:
        category = -1
    if category not in _SALES_SKIP_COUNTS:
        return -1, 0
    reset = form.get("reset_sales_disqualification") == "1"
    if category != current_category or reset:
        return category, _SALES_SKIP_COUNTS[category]
    return category, current_remaining


def _admin_required(view):
    from functools import wraps
    @wraps(view)
    def wrapped(**kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login"))
        if not session_tenant_ok():
            session.clear()
            flash("Your session expired. Please sign in again.", "warning")
            return redirect(url_for("auth.login"))
        if session.get("role") != "admin":
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard.dashboard"))
        return view(**kwargs)
    return wrapped


# ── Dashboard ─────────────────────────────────────────────────────────────────

@admin_bp.route("/")
@_admin_required
def admin_index():
    db = get_db()
    stats = {
        "users":   db.execute(
            "SELECT COUNT(*) FROM users WHERE COALESCE(is_platform_user, 0) = 0"
        ).fetchone()[0],
        "tariffs": db.execute("SELECT COUNT(*) FROM tariffs WHERE active=1").fetchone()[0],
        "members": db.execute("SELECT COUNT(*) FROM members").fetchone()[0],
        "contracts": db.execute(
            "SELECT COUNT(*) FROM contract_templates WHERE active=1"
        ).fetchone()[0],
        "reports": db.execute(
            "SELECT COUNT(*) FROM report_definitions WHERE active=1"
        ).fetchone()[0],
    }
    return render_template("admin/index.html", stats=stats)


# ── Tariff Manager ────────────────────────────────────────────────────────────

@admin_bp.route("/tariffs")
@_admin_required
def tariffs_index():
    db = get_db()
    tariffs = db.execute("SELECT * FROM tariffs ORDER BY sort_order, name").fetchall()
    return render_template("admin/tariffs.html", tariffs=tariffs)


@admin_bp.route("/tariffs/add", methods=["GET", "POST"])
@_admin_required
def tariff_add():
    if request.method == "POST":
        name   = request.form.get("name", "").strip()
        months = request.form.get("duration_months", "12")
        amount = request.form.get("monthly_amount", "0")
        order  = request.form.get("sort_order", "99")
        if not name:
            flash("Tariff name is required.", "error")
        else:
            try:
                get_db().execute(
                    "INSERT INTO tariffs (name, duration_months, monthly_amount, sort_order) VALUES (?,?,?,?)",
                    (name, int(months), float(amount), int(order))
                )
                get_db().commit()
                flash(f"Tariff '{name}' added.", "success")
                return redirect(url_for("admin.tariffs_index"))
            except Exception:
                current_app.logger.exception("Failed to add tariff '%s'", name)
                flash("Failed to add tariff. Please try again.", "error")
    return render_template("admin/tariff_form.html", tariff=None)


@admin_bp.route("/tariffs/<int:tid>/edit", methods=["GET", "POST"])
@_admin_required
def tariff_edit(tid: int):
    db = get_db()
    tariff = db.execute("SELECT * FROM tariffs WHERE id=?", (tid,)).fetchone()
    if not tariff:
        flash("Tariff not found.", "warning")
        return redirect(url_for("admin.tariffs_index"))

    if request.method == "POST":
        name   = request.form.get("name", "").strip()
        months = request.form.get("duration_months", "12")
        amount = request.form.get("monthly_amount", "0")
        order  = request.form.get("sort_order", "99")
        active = 1 if request.form.get("active") else 0
        try:
            db.execute(
                """UPDATE tariffs SET name=?, duration_months=?, monthly_amount=?,
                   sort_order=?, active=? WHERE id=?""",
                (name, int(months), float(amount), int(order), active, tid)
            )
            db.commit()
            flash(f"Tariff '{name}' updated.", "success")
            return redirect(url_for("admin.tariffs_index"))
        except Exception:
            current_app.logger.exception("Failed to update tariff id=%s", tid)
            flash("Failed to update tariff. Please try again.", "error")

    return render_template("admin/tariff_form.html", tariff=tariff)


@admin_bp.post("/tariffs/<int:tid>/toggle")
@_admin_required
def tariff_toggle(tid: int):
    db = get_db()
    db.execute("UPDATE tariffs SET active = 1 - active WHERE id=?", (tid,))
    db.commit()
    flash("Tariff status updated.", "success")
    return redirect(url_for("admin.tariffs_index"))


# ── Contract Template Manager ────────────────────────────────────────────────

@admin_bp.route("/contract-templates")
@_admin_required
def contract_templates_index():
    templates = get_db().execute(
        "SELECT * FROM contract_templates ORDER BY is_default DESC, active DESC, name"
    ).fetchall()
    return render_template("admin/contract_templates.html", templates=templates)


def _existing_sections_config(db, template_id):
    if template_id is not None:
        row = db.execute(
            "SELECT sections_config FROM contract_templates WHERE id=?", (template_id,)
        ).fetchone()
        if row and row["sections_config"]:
            try:
                return json.loads(row["sections_config"])
            except (TypeError, ValueError):
                pass
    return default_sections_config()


def _parse_sections_config(db, template_id):
    """Rebuild sections_config from the submitted checkboxes. Keys, labels and
    ordering come from the existing config (or the shipped defaults) — the
    form only ever toggles booleans, it doesn't invent new fields."""
    base = _existing_sections_config(db, template_id)
    for section in base:
        section["enabled"] = request.form.get(f"section_{section['key']}_enabled") == "1"
        for field in section["fields"]:
            field["enabled"] = request.form.get(f"field_{section['key']}__{field['key']}_enabled") == "1"
    return base


def _parse_terms_parts_config():
    count = int(request.form.get("parts_count", "0") or "0")
    parts = []
    for i in range(count):
        if request.form.get(f"part_remove_{i}") == "1":
            continue
        title = request.form.get(f"part_title_{i}", "").strip()
        intro = request.form.get(f"part_intro_{i}", "").strip()
        clauses = [c.strip() for c in request.form.get(f"part_clauses_{i}", "").splitlines() if c.strip()]
        if not title and not intro and not clauses:
            continue
        parts.append({
            "title": title or f"Part {len(parts) + 1}",
            "intro": intro,
            "clauses": clauses,
            "page_break": request.form.get(f"part_page_break_{i}") == "1",
            "initials_required": request.form.get(f"part_initials_{i}") == "1",
        })
    return parts


def _save_contract_template(template_id=None):
    db = get_db()
    name = request.form.get("name", "").strip()
    gym_name = request.form.get("gym_name", "").strip()
    agreement_title = request.form.get("agreement_title", "").strip()
    terms = [line.strip() for line in request.form.get("terms_text", "").splitlines() if line.strip()]
    footer_text = request.form.get("footer_text", "").strip()
    reg_no = request.form.get("reg_no", "").strip()
    address = request.form.get("address", "").strip()
    accent_color = request.form.get("accent_color", "").strip() or "#185FA5"
    debit_mandate_text = request.form.get("debit_mandate_text", "").strip()
    active = 1 if request.form.get("active") == "1" else 0
    make_default = request.form.get("is_default") == "1"
    if template_id is not None:
        current = db.execute(
            "SELECT is_default FROM contract_templates WHERE id=?", (template_id,)
        ).fetchone()
        if current and current["is_default"]:
            make_default = True

    if not name or not gym_name or not agreement_title:
        flash("Template name, gym name, and agreement title are required.", "error")
        return False
    if not terms:
        flash("Add at least one contract term, with one term per line.", "error")
        return False
    if make_default:
        active = 1

    sections_config = json.dumps(_parse_sections_config(db, template_id))
    terms_parts_config = json.dumps(_parse_terms_parts_config())

    try:
        if make_default:
            db.execute("UPDATE contract_templates SET is_default=0")
        if template_id is None:
            db.execute(
                """INSERT INTO contract_templates
                   (name, gym_name, agreement_title, terms_text, footer_text,
                    reg_no, address, accent_color, sections_config, terms_parts_config,
                    debit_mandate_text, active, is_default, created_by, updated_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, gym_name, agreement_title, "\n".join(terms), footer_text or None,
                 reg_no or None, address or None, accent_color, sections_config, terms_parts_config,
                 debit_mandate_text or None,
                 active, 1 if make_default else 0, session.get("user_id"), session.get("user_id")),
            )
        else:
            db.execute(
                """UPDATE contract_templates SET name=?, gym_name=?, agreement_title=?,
                   terms_text=?, footer_text=?, reg_no=?, address=?, accent_color=?,
                   sections_config=?, terms_parts_config=?, debit_mandate_text=?,
                   active=?, is_default=?, updated_by=?,
                   updated_at=datetime('now') WHERE id=?""",
                (name, gym_name, agreement_title, "\n".join(terms), footer_text or None,
                 reg_no or None, address or None, accent_color, sections_config, terms_parts_config,
                 debit_mandate_text or None,
                 active, 1 if make_default else 0, session.get("user_id"), template_id),
            )
        db.commit()
        return True
    except Exception:
        db.rollback()
        current_app.logger.exception("Failed to save contract template")
        flash("Could not save the contract template. The name may already exist.", "error")
        return False


@admin_bp.route("/contract-templates/add", methods=["GET", "POST"])
@_admin_required
def contract_template_add():
    if request.method == "POST" and _save_contract_template():
        flash("Contract template created.", "success")
        return redirect(url_for("admin.contract_templates_index"))
    starter = get_db().execute(
        "SELECT terms_text, gym_name FROM contract_templates ORDER BY is_default DESC, id LIMIT 1"
    ).fetchone()
    return render_template(
        "admin/contract_template_form.html", template=None, starter=starter,
        sections=default_sections_config(), terms_parts=default_terms_parts_config(),
    )


@admin_bp.route("/contract-templates/<int:template_id>/edit", methods=["GET", "POST"])
@_admin_required
def contract_template_edit(template_id: int):
    db = get_db()
    template = db.execute("SELECT * FROM contract_templates WHERE id=?", (template_id,)).fetchone()
    if not template:
        flash("Contract template not found.", "warning")
        return redirect(url_for("admin.contract_templates_index"))
    if request.method == "POST" and _save_contract_template(template_id):
        flash("Contract template updated.", "success")
        return redirect(url_for("admin.contract_templates_index"))
    sections = _existing_sections_config(db, template_id)
    try:
        terms_parts = json.loads(template["terms_parts_config"]) if template["terms_parts_config"] else default_terms_parts_config()
    except (TypeError, ValueError):
        terms_parts = default_terms_parts_config()
    return render_template(
        "admin/contract_template_form.html", template=template, starter=None,
        sections=sections, terms_parts=terms_parts,
    )


@admin_bp.route("/contract-templates/<int:template_id>/preview")
@_admin_required
def contract_template_preview(template_id: int):
    db = get_db()
    template = db.execute("SELECT * FROM contract_templates WHERE id=?", (template_id,)).fetchone()
    if not template:
        flash("Contract template not found.", "warning")
        return redirect(url_for("admin.contract_templates_index"))
    from .contracts import SAMPLE_MEMBER, render_contract
    return render_contract(template, dict(SAMPLE_MEMBER))


@admin_bp.post("/contract-templates/<int:template_id>/default")
@_admin_required
def contract_template_default(template_id: int):
    db = get_db()
    template = db.execute("SELECT id FROM contract_templates WHERE id=?", (template_id,)).fetchone()
    if not template:
        flash("Contract template not found.", "warning")
    else:
        db.execute("UPDATE contract_templates SET is_default=0")
        db.execute(
            "UPDATE contract_templates SET is_default=1, active=1, updated_at=datetime('now') WHERE id=?",
            (template_id,),
        )
        db.commit()
        flash("Default contract template updated.", "success")
    return redirect(url_for("admin.contract_templates_index"))


@admin_bp.post("/contract-templates/<int:template_id>/toggle")
@_admin_required
def contract_template_toggle(template_id: int):
    db = get_db()
    template = db.execute(
        "SELECT active, is_default FROM contract_templates WHERE id=?", (template_id,)
    ).fetchone()
    if not template:
        flash("Contract template not found.", "warning")
    elif template["is_default"] and template["active"]:
        flash("Choose another default contract before deactivating this one.", "warning")
    else:
        db.execute(
            "UPDATE contract_templates SET active=1-active, updated_at=datetime('now') WHERE id=?",
            (template_id,),
        )
        db.commit()
        flash("Contract template status updated.", "success")
    return redirect(url_for("admin.contract_templates_index"))


# ── Saved Report Manager ─────────────────────────────────────────────────────

def _report_config_from_form(form):
    table = form.get("table", "members")
    if table not in REPORT_TABLES:
        return None, "Choose a valid report data source."
    allowed_columns = {column for column, _ in REPORT_TABLES[table]["cols"]}
    columns = []
    for column in form.getlist("cols"):
        if column in allowed_columns and column not in columns:
            columns.append(column)
    if not columns:
        return None, "Select at least one report column."

    group_by = form.get("group_by", "")
    sort_by = form.get("sort_by", "")
    agg_col = form.get("agg_col", "*")
    try:
        limit = min(max(int(form.get("limit", "200")), 1), 1000)
    except ValueError:
        limit = 200
    config = {
        "table": table,
        "cols": columns,
        "join_members": "1" if form.get("join_members") == "1" else "",
        "group_by": group_by if group_by in allowed_columns else "",
        "agg_func": form.get("agg_func") if form.get("agg_func") in AGG_FUNCS else "COUNT",
        "agg_col": agg_col if agg_col in allowed_columns else "*",
        "sort_by": sort_by if sort_by in allowed_columns else "",
        "sort_dir": "desc" if form.get("sort_dir") == "desc" else "asc",
        "limit": str(limit),
    }
    for index in range(1, 5):
        field = form.get(f"f{index}_field", "")
        operator = form.get(f"f{index}_op", "")
        config[f"f{index}_field"] = field if field in allowed_columns else ""
        config[f"f{index}_op"] = operator if operator in FILTER_OPS else ""
        config[f"f{index}_val"] = form.get(f"f{index}_val", "").strip()
    return config, None


def _load_report_config(report):
    try:
        config = json.loads(report["config_json"] or "{}")
    except (TypeError, ValueError):
        config = {}
    return config


@admin_bp.route("/report-definitions")
@_admin_required
def report_definitions_index():
    reports = get_db().execute(
        "SELECT * FROM report_definitions ORDER BY active DESC, name"
    ).fetchall()
    return render_template(
        "admin/report_definitions.html", reports=reports, report_tables=REPORT_TABLES
    )


def _save_report_definition(report_id=None):
    db = get_db()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    active = 1 if request.form.get("active") == "1" else 0
    config, error = _report_config_from_form(request.form)
    if not name:
        flash("Report name is required.", "error")
        return False
    if error:
        flash(error, "error")
        return False
    try:
        if report_id is None:
            db.execute(
                """INSERT INTO report_definitions
                   (name, description, table_name, config_json, active, created_by, updated_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, description or None, config["table"], json.dumps(config), active,
                 session.get("user_id"), session.get("user_id")),
            )
        else:
            db.execute(
                """UPDATE report_definitions SET name=?, description=?, table_name=?,
                   config_json=?, active=?, updated_by=?, updated_at=datetime('now') WHERE id=?""",
                (name, description or None, config["table"], json.dumps(config), active,
                 session.get("user_id"), report_id),
            )
        db.commit()
        return True
    except Exception:
        db.rollback()
        current_app.logger.exception("Failed to save report definition")
        flash("Could not save the report. The name may already exist.", "error")
        return False


@admin_bp.route("/report-definitions/add", methods=["GET", "POST"])
@_admin_required
def report_definition_add():
    if request.method == "POST" and _save_report_definition():
        flash("Saved report created.", "success")
        return redirect(url_for("admin.report_definitions_index"))
    return render_template(
        "admin/report_definition_form.html", report=None, config={},
        report_tables=REPORT_TABLES, filter_ops=FILTER_OPS, agg_funcs=AGG_FUNCS,
        report_tables_json={key: {"cols": list(value["cols"])} for key, value in REPORT_TABLES.items()},
    )


@admin_bp.route("/report-definitions/<int:report_id>/edit", methods=["GET", "POST"])
@_admin_required
def report_definition_edit(report_id: int):
    db = get_db()
    report = db.execute("SELECT * FROM report_definitions WHERE id=?", (report_id,)).fetchone()
    if not report:
        flash("Saved report not found.", "warning")
        return redirect(url_for("admin.report_definitions_index"))
    if request.method == "POST" and _save_report_definition(report_id):
        flash("Saved report updated.", "success")
        return redirect(url_for("admin.report_definitions_index"))
    return render_template(
        "admin/report_definition_form.html", report=report,
        config=_load_report_config(report), report_tables=REPORT_TABLES,
        filter_ops=FILTER_OPS, agg_funcs=AGG_FUNCS,
        report_tables_json={key: {"cols": list(value["cols"])} for key, value in REPORT_TABLES.items()},
    )


@admin_bp.post("/report-definitions/<int:report_id>/toggle")
@_admin_required
def report_definition_toggle(report_id: int):
    db = get_db()
    db.execute(
        "UPDATE report_definitions SET active=1-active, updated_at=datetime('now') WHERE id=?",
        (report_id,),
    )
    db.commit()
    flash("Saved report status updated.", "success")
    return redirect(url_for("admin.report_definitions_index"))


# ── User Manager ──────────────────────────────────────────────────────────────

@admin_bp.route("/users")
@_admin_required
def users_index():
    db = get_db()
    users = db.execute(
        """SELECT * FROM users
           WHERE COALESCE(is_platform_user, 0) = 0
           ORDER BY role, full_name"""
    ).fetchall()
    return render_template(
        "admin/users.html",
        users=users,
        protected_usernames=PROTECTED_USERNAMES,
        default_staff_password=DEFAULT_STAFF_PASSWORD,
    )


@admin_bp.route("/users/add", methods=["GET", "POST"])
@_admin_required
def user_add():
    if request.method == "POST":
        username  = request.form.get("username", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        email     = request.form.get("email", "").strip()
        contact   = request.form.get("contact", "").strip()
        role      = request.form.get("role", "staff")
        dept      = request.form.get("department", "General").strip()
        password  = request.form.get("password", "").strip()
        is_sales_consultant = 1 if request.form.get("is_sales_consultant") else 0
        is_collections_caller = 1 if request.form.get("is_collections_caller") else 0
        pbx_extension = request.form.get("pbx_extension", "").strip()
        sales_available = 1 if is_sales_consultant and request.form.get("sales_available") else 0
        rotation_raw = request.form.get("sales_rotation_order", "").strip()
        try:
            sales_rotation_order = int(rotation_raw) if rotation_raw else None
        except ValueError:
            sales_rotation_order = -1
        disqualification_category, skip_remaining = _sales_disqualification(request.form)

        # Validate role against known values
        if role not in _VALID_ROLES:
            flash("Invalid role selected.", "error")
        elif not (username and full_name and password):
            flash("Username, full name, and password are required.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif is_sales_consultant and (sales_rotation_order is None or sales_rotation_order < 1):
            flash("Sales consultants require a rotation position of 1 or higher.", "error")
        elif disqualification_category not in _SALES_SKIP_COUNTS:
            flash("Invalid lead disqualification category.", "error")
        elif is_sales_consultant and get_db().execute(
            "SELECT id FROM users WHERE is_sales_consultant=1 AND sales_rotation_order=?",
            (sales_rotation_order,),
        ).fetchone():
            flash("That sales rotation position is already assigned.", "error")
        else:
            try:
                db = get_db()
                db.execute(
                    """INSERT INTO users
                       (username, full_name, email, contact, role, department,
                        password_hash, must_change_password, is_sales_consultant,
                        sales_available, sales_rotation_order,
                        sales_disqualification_category, sales_skip_remaining,
                        is_collections_caller, pbx_extension)
                       VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)""",
                    (username, full_name, email, contact, role, dept,
                     generate_password_hash(password), is_sales_consultant,
                     sales_available, sales_rotation_order,
                     disqualification_category if is_sales_consultant else 0,
                     skip_remaining if is_sales_consultant else 0,
                     is_collections_caller, pbx_extension or None)
                )
                db.commit()
                flash(f"User '{username}' created. They must change password on first login.", "success")
                return redirect(url_for("admin.users_index"))
            except Exception:
                current_app.logger.exception("Failed to create user '%s'", username)
                flash("Failed to create user. Username may already exist.", "error")
    return render_template("admin/user_form.html", user=None,
                           roles=sorted(_VALID_ROLES))


@admin_bp.route("/users/<int:uid>/edit", methods=["GET", "POST"])
@_admin_required
def user_edit(uid: int):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        flash("User not found.", "warning")
        return redirect(url_for("admin.users_index"))
    if user["is_platform_user"]:
        flash("Platform power-user identities are managed by 1Cpase.", "error")
        return redirect(url_for("admin.users_index"))

    all_perms  = sorted({p for perms in MODULE_PERMISSIONS.values() for p, _ in perms})
    user_perms = {r["permission"] for r in
                  db.execute("SELECT permission FROM user_permissions WHERE user_id=?", (uid,)).fetchall()}

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email     = request.form.get("email", "").strip()
        contact   = request.form.get("contact", "").strip()
        role      = request.form.get("role", "staff")
        dept      = request.form.get("department", "General").strip()
        active    = 1 if request.form.get("active") else 0
        new_pass  = request.form.get("new_password", "").strip()
        is_sales_consultant = 1 if request.form.get("is_sales_consultant") else 0
        is_collections_caller = 1 if request.form.get("is_collections_caller") else 0
        pbx_extension = request.form.get("pbx_extension", "").strip()
        sales_available = 1 if active and is_sales_consultant and request.form.get("sales_available") else 0
        rotation_raw = request.form.get("sales_rotation_order", "").strip()
        try:
            sales_rotation_order = int(rotation_raw) if rotation_raw else None
        except ValueError:
            sales_rotation_order = -1
        disqualification_category, skip_remaining = _sales_disqualification(
            request.form,
            user["sales_disqualification_category"] or 0,
            user["sales_skip_remaining"] or 0,
        )

        # Validate and sanitise permissions — only accept known permission strings
        submitted_perms = set(request.form.getlist("permissions"))
        selected_perms  = submitted_perms & ALL_PERMISSIONS  # discard unknowns

        if role not in _VALID_ROLES:
            flash("Invalid role selected.", "error")
        elif new_pass and len(new_pass) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif is_sales_consultant and (sales_rotation_order is None or sales_rotation_order < 1):
            flash("Sales consultants require a rotation position of 1 or higher.", "error")
        elif disqualification_category not in _SALES_SKIP_COUNTS:
            flash("Invalid lead disqualification category.", "error")
        elif is_sales_consultant and db.execute(
            """SELECT id FROM users WHERE is_sales_consultant=1
               AND sales_rotation_order=? AND id != ?""",
            (sales_rotation_order, uid),
        ).fetchone():
            flash("That sales rotation position is already assigned.", "error")
        else:
            try:
                if new_pass:
                    db.execute(
                        """UPDATE users SET full_name=?, email=?, contact=?, role=?,
                           department=?, active=?, is_sales_consultant=?, sales_available=?,
                           sales_rotation_order=?, sales_disqualification_category=?,
                           sales_skip_remaining=?, is_collections_caller=?, pbx_extension=?,
                           password_hash=?, must_change_password=1
                           WHERE id=?""",
                        (full_name, email, contact, role, dept, active,
                         is_sales_consultant, sales_available, sales_rotation_order,
                         disqualification_category if is_sales_consultant else 0,
                         skip_remaining if is_sales_consultant else 0,
                         is_collections_caller, pbx_extension or None,
                         generate_password_hash(new_pass), uid)
                    )
                else:
                    db.execute(
                        """UPDATE users SET full_name=?, email=?, contact=?, role=?,
                           department=?, active=?, is_sales_consultant=?, sales_available=?,
                           sales_rotation_order=?, sales_disqualification_category=?,
                           sales_skip_remaining=?, is_collections_caller=?, pbx_extension=? WHERE id=?""",
                        (full_name, email, contact, role, dept, active,
                         is_sales_consultant, sales_available, sales_rotation_order,
                         disqualification_category if is_sales_consultant else 0,
                         skip_remaining if is_sales_consultant else 0,
                         is_collections_caller, pbx_extension or None, uid)
                    )
                db.execute("DELETE FROM user_permissions WHERE user_id=?", (uid,))
                for perm in selected_perms:
                    db.execute(
                        "INSERT OR IGNORE INTO user_permissions (user_id, permission) VALUES (?,?)",
                        (uid, perm)
                    )
                db.commit()
                flash(f"{full_name} updated.", "success")
                return redirect(url_for("admin.users_index"))
            except Exception:
                current_app.logger.exception("Failed to update user id=%s", uid)
                flash("Failed to update user. Please try again.", "error")

    return render_template("admin/user_form.html", user=user,
                           roles=sorted(_VALID_ROLES),
                           all_perms=all_perms, user_perms=user_perms,
                           module_permissions=MODULE_PERMISSIONS)


@admin_bp.post("/users/<int:uid>/reset-password")
@_admin_required
def user_reset_password(uid: int):
    """Reset one account to the default staff password.

    The account is forced to choose a new one at next sign-in, so the default
    is a handover mechanism rather than a password anybody keeps using.
    """
    if uid == session.get("user_id"):
        flash("Use Change Password to set your own password.", "error")
        return redirect(url_for("admin.users_index"))
    db = get_db()
    user = db.execute(
        "SELECT username, full_name, is_platform_user FROM users WHERE id=?", (uid,)
    ).fetchone()
    if not user:
        flash("User not found.", "warning")
        return redirect(url_for("admin.users_index"))
    if user["is_platform_user"]:
        flash("Platform power-user identities cannot be reset here.", "error")
        return redirect(url_for("admin.users_index"))
    if is_protected_user(user["username"]):
        flash(f"'{user['username']}' is protected and cannot be reset to the "
              "default password.", "error")
        return redirect(url_for("admin.users_index"))

    db.execute(
        """UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?""",
        (generate_password_hash(DEFAULT_STAFF_PASSWORD), uid),
    )
    db.commit()
    flash(
        f"{user['full_name'] or user['username']} was reset to the default "
        f"password ({DEFAULT_STAFF_PASSWORD}). They must change it at next sign-in.",
        "success",
    )
    return redirect(url_for("admin.users_index"))


@admin_bp.post("/users/<int:uid>/toggle")
@_admin_required
def user_toggle(uid: int):
    if uid == session.get("user_id"):
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("admin.users_index"))
    db = get_db()
    user = db.execute(
        "SELECT is_platform_user FROM users WHERE id=?", (uid,)
    ).fetchone()
    if not user:
        flash("User not found.", "warning")
        return redirect(url_for("admin.users_index"))
    if user["is_platform_user"]:
        flash("Platform power-user identities cannot be deactivated here.", "error")
        return redirect(url_for("admin.users_index"))
    db.execute("UPDATE users SET active = 1 - active WHERE id=?", (uid,))
    db.commit()
    flash("User status updated.", "success")
    return redirect(url_for("admin.users_index"))
