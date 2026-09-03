from datetime import date, timedelta
from urllib.parse import quote

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from .auth import login_required, permission_required
from .database import get_db
from .encryption import decrypt_member

fitness_bp = Blueprint("fitness", __name__, url_prefix="/fitness")

SCHEDULE_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

RISK_LABELS = {1: "Low", 2: "Low+", 3: "Moderate", 4: "High", 5: "Extreme"}
RISK_COLORS = {1: "#10b981", 2: "#22c55e", 3: "#f59e0b", 4: "#ef4444", 5: "#991b1b"}
RISK_BG = {1: "#ecfdf5", 2: "#f0fdf4", 3: "#fffbeb", 4: "#fef2f2", 5: "#fee2e2"}


# ── Small helpers ─────────────────────────────────────────────────────────────

def as_int(value, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


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


def get_active_members():
    rows = get_db().execute(
        "SELECT * FROM members WHERE outcome = 'joined' ORDER BY first_name, last_name"
    ).fetchall()
    return [decrypt_member(row) for row in rows]


def get_fitness_staff():
    return get_db().execute(
        """SELECT id, full_name FROM users
           WHERE active = 1 AND role IN ('trainer', 'admin', 'manager')
           ORDER BY full_name"""
    ).fetchall()


def member_full_name(member) -> str:
    first_name = str((member.get("first_name") if isinstance(member, dict) else member["first_name"]) or "").strip()
    last_name = str((member.get("last_name") if isinstance(member, dict) else member["last_name"]) or "").strip()
    return " ".join(part for part in (first_name, last_name) if part) or "Unnamed Member"


def _normalize_phone(phone) -> str:
    raw = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not raw:
        return ""
    if raw.startswith("0") and len(raw) >= 10:
        return "27" + raw[1:]
    return raw


def whatsapp_url(phone, message: str):
    normalized = _normalize_phone(phone)
    if not normalized:
        return None
    return f"https://wa.me/{normalized}?text={quote(message)}"


def incident_whatsapp_message(incident) -> str:
    recipient_name = incident["member_name"] or incident["walk_in_name"] or "member"
    incident_type = str(incident["incident_type"] or "incident").replace("_", " ")
    severity = str(incident["severity"] or "medium").title()
    return (
        f"Hi {recipient_name}, this is Elev8 Health & Fitness. "
        f"We logged a {severity} {incident_type} incident during your recent visit. "
        "Please speak to reception or floor staff if you need clarity."
    )


@fitness_bp.route("/")
@permission_required("classes")
def fitness_index():
    db = get_db()
    search = request.args.get("q", "").strip()
    show_inactive = request.args.get("inactive", "")

    query = """
        SELECT fc.*, u.full_name AS instructor_name,
               COUNT(cb.id) AS booking_count
        FROM fitness_classes fc
        LEFT JOIN users u ON fc.instructor_id = u.id
        LEFT JOIN class_bookings cb ON fc.id = cb.class_id AND cb.status = 'booked'
        WHERE 1=1
    """
    params = []
    if not show_inactive:
        query += " AND fc.active = 1"
    if search:
        query += " AND (fc.name LIKE ? OR u.full_name LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    query += " GROUP BY fc.id ORDER BY fc.schedule_day, fc.schedule_time"

    classes = db.execute(query, params).fetchall()
    return render_template("fitness/index.html", classes=classes, search=search, show_inactive=show_inactive)


@fitness_bp.route("/add", methods=["GET", "POST"])
@permission_required("fitness_module")
def fitness_add():
    db = get_db()
    instructors = db.execute(
        "SELECT id, full_name FROM users WHERE active = 1 AND role IN ('trainer','admin','manager') ORDER BY full_name"
    ).fetchall()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        instructor_id = request.form.get("instructor_id", "").strip()
        schedule_day = request.form.get("schedule_day", "")
        schedule_time = request.form.get("schedule_time", "")
        capacity = request.form.get("capacity", "20").strip()

        if not name:
            flash("Class name is required.", "error")
        else:
            try:
                db.execute(
                    """INSERT INTO fitness_classes (name, instructor_id, schedule_day, schedule_time, capacity)
                       VALUES (?, ?, ?, ?, ?)""",
                    (name, int(instructor_id) if instructor_id else None, schedule_day, schedule_time, int(capacity or 20)),
                )
                db.commit()
                flash(f"Class '{name}' created.", "success")
                return redirect(url_for("fitness.fitness_index"))
            except Exception as e:
                flash(f"Error: {e}", "error")

    return render_template("fitness/add.html", instructors=instructors, days=SCHEDULE_DAYS)


@fitness_bp.route("/<int:fid>/edit", methods=["GET", "POST"])
@permission_required("fitness_module")
def fitness_edit(fid: int):
    db = get_db()
    fc = db.execute("SELECT * FROM fitness_classes WHERE id = ?", (fid,)).fetchone()
    if fc is None:
        flash("Class not found.", "warning")
        return redirect(url_for("fitness.fitness_index"))

    instructors = db.execute(
        "SELECT id, full_name FROM users WHERE active = 1 AND role IN ('trainer','admin','manager') ORDER BY full_name"
    ).fetchall()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        instructor_id = request.form.get("instructor_id", "").strip()
        schedule_day = request.form.get("schedule_day", "")
        schedule_time = request.form.get("schedule_time", "")
        capacity = request.form.get("capacity", "20").strip()
        active = 1 if request.form.get("active") else 0

        try:
            db.execute(
                """UPDATE fitness_classes
                   SET name=?, instructor_id=?, schedule_day=?, schedule_time=?, capacity=?, active=?
                   WHERE id=?""",
                (name, int(instructor_id) if instructor_id else None, schedule_day, schedule_time, int(capacity or 20), active, fid),
            )
            db.commit()
            flash("Class updated.", "success")
            return redirect(url_for("fitness.fitness_index"))
        except Exception as e:
            flash(f"Error: {e}", "error")

    return render_template("fitness/edit.html", fc=fc, instructors=instructors, days=SCHEDULE_DAYS)


@fitness_bp.route("/<int:fid>/bookings")
@permission_required("classes")
def fitness_bookings(fid: int):
    db = get_db()
    fc = db.execute(
        """SELECT fc.*, u.full_name AS instructor_name
           FROM fitness_classes fc
           LEFT JOIN users u ON fc.instructor_id = u.id
           WHERE fc.id = ?""",
        (fid,),
    ).fetchone()
    if fc is None:
        flash("Class not found.", "warning")
        return redirect(url_for("fitness.fitness_index"))

    bookings = db.execute(
        """SELECT cb.id, cb.booking_date, cb.status,
                  m.id AS member_id, m.first_name, m.last_name, m.package
           FROM class_bookings cb
           JOIN members m ON cb.member_id = m.id
           WHERE cb.class_id = ?
           ORDER BY cb.booking_date DESC""",
        (fid,),
    ).fetchall()

    booked_ids = {b["member_id"] for b in bookings if b["status"] == "booked"}
    members = db.execute(
        "SELECT id, first_name, last_name, package FROM members WHERE member_status = 'Active' ORDER BY first_name"
    ).fetchall()
    available = [m for m in members if m["id"] not in booked_ids]

    return render_template("fitness/bookings.html", fc=fc, bookings=bookings, available=available)


@fitness_bp.post("/<int:fid>/book")
@permission_required("classes")
def fitness_book(fid: int):
    db = get_db()
    member_id = request.form.get("member_id", "").strip()
    if not member_id:
        flash("Please select a member.", "error")
        return redirect(url_for("fitness.fitness_bookings", fid=fid))

    fc = db.execute("SELECT capacity FROM fitness_classes WHERE id = ?", (fid,)).fetchone()
    current = db.execute(
        "SELECT COUNT(*) FROM class_bookings WHERE class_id = ? AND status = 'booked'", (fid,)
    ).fetchone()[0]

    if fc and current >= fc["capacity"]:
        flash("Class is at full capacity.", "warning")
        return redirect(url_for("fitness.fitness_bookings", fid=fid))

    try:
        db.execute(
            "INSERT INTO class_bookings (class_id, member_id, status) VALUES (?, ?, 'booked')",
            (fid, int(member_id)),
        )
        db.commit()
        flash("Member booked successfully.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")

    return redirect(url_for("fitness.fitness_bookings", fid=fid))


@fitness_bp.post("/bookings/<int:bid>/cancel")
@permission_required("classes")
def booking_cancel(bid: int):
    db = get_db()
    booking = db.execute("SELECT class_id FROM class_bookings WHERE id = ?", (bid,)).fetchone()
    if booking is None:
        flash("Booking not found.", "warning")
        return redirect(url_for("fitness.fitness_index"))

    db.execute("UPDATE class_bookings SET status = 'cancelled' WHERE id = ?", (bid,))
    db.commit()
    flash("Booking cancelled.", "info")
    return redirect(url_for("fitness.fitness_bookings", fid=booking["class_id"]))


# ── Assessments ────────────────────────────────────────────────────────────

@fitness_bp.get("/assessments")
@permission_required("assessments")
def assessments_index():
    assessments = get_db().execute(
        """
        SELECT a.*, TRIM(m.first_name || ' ' || m.last_name) AS member_name,
               u.full_name AS trainer_name
        FROM assessments a
        JOIN members m ON m.id = a.member_id
        LEFT JOIN users u ON u.id = a.assessed_by_id
        ORDER BY COALESCE(a.date, a.created_at) DESC, a.id DESC
        LIMIT 50
        """
    ).fetchall()
    return render_template("fitness/assessments.html", assessments=assessments)


@fitness_bp.route("/assessments/add", methods=["GET", "POST"])
@permission_required("assessments")
def assessment_add():
    members = get_active_members()
    trainers = get_fitness_staff()
    if request.method == "POST":
        member_id = request.form.get("member_id")
        if not member_id:
            flash("Please select a member.", "error")
        else:
            get_db().execute(
                """
                INSERT INTO assessments (
                    member_id, assessed_by_id, date, weight_kg, body_fat_pct,
                    muscle_mass_kg, bmi, fitness_level, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    member_id,
                    request.form.get("assessed_by_id") or None,
                    parse_date_value(request.form.get("date")).isoformat(),
                    as_float(request.form.get("weight_kg"), None),
                    as_float(request.form.get("body_fat_pct"), None),
                    as_float(request.form.get("muscle_mass_kg"), None),
                    as_float(request.form.get("bmi"), None),
                    str(request.form.get("fitness_level", "")).strip() or None,
                    str(request.form.get("notes", "")).strip() or None,
                ),
            )
            get_db().commit()
            flash("Assessment saved.", "success")
            return redirect(url_for("fitness.assessments_index"))
    return render_template("fitness/assessment_form.html", members=members, trainers=trainers)


# ── Workout plans ────────────────────────────────────────────────────────────

@fitness_bp.get("/workout-plans")
@permission_required("workout_plans")
def workout_plans_index():
    plans = get_db().execute(
        """
        SELECT wp.*, TRIM(m.first_name || ' ' || m.last_name) AS member_name,
               u.full_name AS trainer_name
        FROM workout_plans wp
        JOIN members m ON m.id = wp.member_id
        LEFT JOIN users u ON u.id = wp.trainer_id
        ORDER BY COALESCE(wp.start_date, wp.created_at) DESC, wp.id DESC
        LIMIT 50
        """
    ).fetchall()
    return render_template("fitness/plans.html", plans=plans)


@fitness_bp.route("/workout-plans/add", methods=["GET", "POST"])
@permission_required("workout_plans")
def workout_plan_add():
    members = get_active_members()
    trainers = get_fitness_staff()
    if request.method == "POST":
        member_id = request.form.get("member_id")
        title = str(request.form.get("title", "")).strip()
        if not member_id or not title:
            flash("Member and plan title are required.", "error")
        else:
            get_db().execute(
                """
                INSERT INTO workout_plans (member_id, trainer_id, title, description, start_date, end_date, active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    member_id,
                    request.form.get("trainer_id") or None,
                    title,
                    str(request.form.get("description", "")).strip() or None,
                    str(request.form.get("start_date", "")).strip() or None,
                    str(request.form.get("end_date", "")).strip() or None,
                ),
            )
            get_db().commit()
            flash("Workout plan created.", "success")
            return redirect(url_for("fitness.workout_plans_index"))
    return render_template("fitness/plan_form.html", members=members, trainers=trainers)


# ── Instructor dailies ────────────────────────────────────────────────────────

@fitness_bp.get("/dailies")
@permission_required("dailies")
def dailies_index():
    db = get_db()
    selected_day = parse_date_value(request.args.get("date"))
    selected_view = request.args.get("view", "day")
    selected_view = selected_view if selected_view in {"day", "week"} else "day"
    selected_staff_id = request.args.get("staff_id", "all")

    if selected_view == "week":
        start_day = selected_day - timedelta(days=selected_day.weekday())
        end_day = start_day + timedelta(days=6)
    else:
        start_day = end_day = selected_day

    clauses = ["d.date >= ?", "d.date <= ?"]
    params = [start_day.isoformat(), end_day.isoformat()]
    if selected_staff_id != "all":
        clauses.append("d.instructor_id = ?")
        params.append(selected_staff_id)

    entries = db.execute(
        f"""
        SELECT d.*, TRIM(COALESCE(m.first_name, '') || ' ' || COALESCE(m.last_name, '')) AS member_name,
               u.full_name AS instructor_name
        FROM instructor_dailies d
        LEFT JOIN members m ON m.id = d.member_id
        LEFT JOIN users u ON u.id = d.instructor_id
        WHERE {' AND '.join(clauses)}
        ORDER BY d.date DESC, d.time_in DESC, d.id DESC
        """,
        tuple(params),
    ).fetchall()

    total = len(entries)
    pt_count = sum(1 for e in entries if e["service_type"] == "personal_training")
    ob_count = sum(1 for e in entries if e["service_type"] == "onboarding")
    ea_count = sum(1 for e in entries if e["service_type"] == "exercise_assistance")
    full_count = sum(1 for e in entries if e["session_type"] == "full")
    new_count = sum(1 for e in entries if e["client_type"] == "new")

    return render_template(
        "fitness/dailies.html",
        day=selected_day.isoformat(),
        view=selected_view,
        staff_id=str(selected_staff_id),
        entries=entries,
        total=total,
        pt_count=pt_count,
        ob_count=ob_count,
        ea_count=ea_count,
        full_count=full_count,
        new_count=new_count,
        instructors=get_fitness_staff(),
        members=get_active_members(),
    )


@fitness_bp.post("/dailies/add")
@permission_required("dailies")
def daily_add():
    client_type = str(request.form.get("client_type", "existing")).strip() or "existing"
    member_id = request.form.get("member_id") or None
    walk_in_name = str(request.form.get("walk_in_name", "")).strip() or None
    fallback_date = request.form.get("date") or date.today().isoformat()

    if client_type == "existing" and not member_id:
        flash("Please select an existing member.", "error")
        return redirect(url_for("fitness.dailies_index", date=fallback_date))
    if client_type == "new" and not walk_in_name:
        flash("Please capture the new client name.", "error")
        return redirect(url_for("fitness.dailies_index", date=fallback_date))

    get_db().execute(
        """
        INSERT INTO instructor_dailies (
            instructor_id, member_id, walk_in_name, client_type, service_type,
            session_type, date, time_in, time_out, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.form.get("instructor_id") or None,
            member_id,
            walk_in_name,
            client_type,
            str(request.form.get("service_type", "exercise_assistance")).strip() or "exercise_assistance",
            str(request.form.get("session_type", "full")).strip() or "full",
            parse_date_value(request.form.get("date")).isoformat(),
            str(request.form.get("time_in", "")).strip() or None,
            str(request.form.get("time_out", "")).strip() or None,
            str(request.form.get("notes", "")).strip() or None,
        ),
    )
    get_db().commit()
    flash("Daily session logged.", "success")
    return redirect(url_for("fitness.dailies_index", date=fallback_date))


# ── Incident log ──────────────────────────────────────────────────────────────

@fitness_bp.get("/incidents")
@permission_required("incident_log")
def incidents_index():
    db = get_db()
    status = request.args.get("status", "all")
    severity = request.args.get("severity", "all")

    clauses = ["1=1"]
    params = []
    if status != "all":
        clauses.append("i.status = ?")
        params.append(status)
    if severity != "all":
        clauses.append("i.severity = ?")
        params.append(severity)

    incidents = db.execute(
        f"""
        SELECT i.*, TRIM(COALESCE(m.first_name, '') || ' ' || COALESCE(m.last_name, '')) AS member_name,
               m.contact AS member_contact, u.full_name AS logged_by_name
        FROM incidents i
        LEFT JOIN members m ON m.id = i.member_id
        LEFT JOIN users u ON u.id = i.logged_by_id
        WHERE {' AND '.join(clauses)}
        ORDER BY COALESCE(i.date, i.created_at) DESC, COALESCE(i.time, '00:00') DESC, i.id DESC
        """,
        tuple(params),
    ).fetchall()

    repeats = db.execute(
        """
        SELECT i.member_id, TRIM(COALESCE(m.first_name, '') || ' ' || COALESCE(m.last_name, '')) AS member_name,
               COUNT(*) AS c
        FROM incidents i
        JOIN members m ON m.id = i.member_id
        WHERE i.member_id IS NOT NULL
        GROUP BY i.member_id
        HAVING COUNT(*) > 1
        ORDER BY c DESC, member_name
        """
    ).fetchall()

    wa_messages = {}
    for incident in incidents:
        phone = incident["member_contact"] or incident["walk_in_contact"]
        url = whatsapp_url(phone, incident_whatsapp_message(incident))
        if url:
            wa_messages[incident["id"]] = url

    open_count = db.execute("SELECT COUNT(*) FROM incidents WHERE status = 'open'").fetchone()[0]
    high_count = db.execute("SELECT COUNT(*) FROM incidents WHERE severity = 'high'").fetchone()[0]

    return render_template(
        "fitness/incidents.html",
        incidents=incidents,
        open_count=open_count,
        high_count=high_count,
        repeats=repeats,
        members=get_active_members(),
        instructors=get_fitness_staff(),
        wa_messages=wa_messages,
        status=status,
        sev=severity,
    )


@fitness_bp.post("/incidents/add")
@permission_required("incident_log")
def incident_add():
    member_id = request.form.get("member_id") or None
    walk_in_name = str(request.form.get("walk_in_name", "")).strip() or None
    walk_in_contact = str(request.form.get("walk_in_contact", "")).strip() or None
    description = str(request.form.get("description", "")).strip()
    if not member_id and not walk_in_name:
        flash("Select a member or capture the walk-in name.", "error")
        return redirect(url_for("fitness.incidents_index"))
    if not description:
        flash("Incident description is required.", "error")
        return redirect(url_for("fitness.incidents_index"))

    get_db().execute(
        """
        INSERT INTO incidents (
            member_id, walk_in_name, walk_in_contact, incident_type, description,
            severity, logged_by_id, date, time, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (
            member_id,
            walk_in_name,
            walk_in_contact,
            str(request.form.get("incident_type", "other")).strip() or "other",
            description,
            str(request.form.get("severity", "medium")).strip() or "medium",
            request.form.get("logged_by_id") or None,
            parse_date_value(request.form.get("date")).isoformat(),
            str(request.form.get("time", "")).strip() or None,
        ),
    )
    get_db().commit()
    flash("Incident logged.", "success")
    return redirect(url_for("fitness.incidents_index"))


@fitness_bp.post("/incidents/<int:iid>/update")
@permission_required("incident_log")
def incident_update(iid: int):
    db = get_db()
    incident = db.execute("SELECT * FROM incidents WHERE id = ?", (iid,)).fetchone()
    if incident is None:
        flash("Incident not found.", "warning")
        return redirect(url_for("fitness.incidents_index"))

    new_status = request.form.get("status") or incident["status"]
    resolution_notes = str(request.form.get("resolution_notes", "")).strip() or incident["resolution_notes"]
    whatsapp_sent = 1 if request.form.get("whatsapp_sent") == "1" or incident["whatsapp_sent"] else 0

    db.execute(
        "UPDATE incidents SET status = ?, resolution_notes = ?, whatsapp_sent = ? WHERE id = ?",
        (new_status, resolution_notes, whatsapp_sent, iid),
    )
    db.commit()
    flash("Incident updated.", "success")
    return redirect(request.referrer or url_for("fitness.incidents_index"))


# ── Body matrix ───────────────────────────────────────────────────────────────

@fitness_bp.get("/body-matrix")
@permission_required("body_matrix")
def body_matrix_index():
    q = request.args.get("q", "").strip()
    sql = """
        SELECT m.*, COUNT(b.id) AS entry_count, MAX(b.date) AS last_entry
        FROM members m
        LEFT JOIN body_matrix b ON b.member_id = m.id
        WHERE m.outcome = 'joined'
    """
    params = []
    if q:
        like = f"%{q}%"
        sql += """ AND (
            m.first_name LIKE ? OR m.last_name LIKE ?
            OR (m.first_name || ' ' || m.last_name) LIKE ?
            OR m.contact LIKE ? OR m.email LIKE ?
        )"""
        params += [like, like, like, like, like]
    sql += " GROUP BY m.id ORDER BY m.first_name, m.last_name"
    rows = get_db().execute(sql, params).fetchall()
    members = [decrypt_member(row) | {"entry_count": row["entry_count"], "last_entry": row["last_entry"]} for row in rows]
    return render_template("fitness/body_matrix_index.html", members=members, search=q)


@fitness_bp.get("/body-matrix/<int:mid>")
@permission_required("body_matrix")
def body_matrix_member(mid: int):
    db = get_db()
    member_row = db.execute("SELECT * FROM members WHERE id = ?", (mid,)).fetchone()
    if member_row is None:
        flash("Member not found.", "warning")
        return redirect(url_for("fitness.body_matrix_index"))
    member = decrypt_member(member_row)

    raw_entries = db.execute(
        "SELECT * FROM body_matrix WHERE member_id = ? ORDER BY date ASC, id ASC", (mid,)
    ).fetchall()
    view = request.args.get("view", "weekly")
    view = view if view in {"weekly", "monthly"} else "weekly"

    latest = raw_entries[-1] if raw_entries else None
    if view == "monthly":
        entries = db.execute(
            """
            SELECT substr(COALESCE(month, date), 1, 7) || '-01' AS period,
                   AVG(weight_kg) AS weight_kg, AVG(body_fat_pct) AS body_fat_pct,
                   AVG(muscle_mass_kg) AS muscle_mass_kg, AVG(bmi) AS bmi, AVG(waist_cm) AS waist_cm
            FROM body_matrix
            WHERE member_id = ?
            GROUP BY substr(COALESCE(month, date), 1, 7)
            ORDER BY period
            """,
            (mid,),
        ).fetchall()
        labels = [str(e["period"] or "")[:7] for e in entries]
    else:
        entries = [dict(e) | {"period": e["date"]} for e in raw_entries]
        labels = [str(e["date"] or "")[5:10] for e in raw_entries]

    weights = [as_float(e["weight_kg"]) for e in entries]
    body_fats = [as_float(e["body_fat_pct"]) for e in entries]
    muscles = [as_float(e["muscle_mass_kg"]) for e in entries]
    waists = [as_float(e["waist_cm"]) for e in entries]

    return render_template(
        "fitness/body_matrix_member.html",
        member=member,
        full_name=member_full_name(member),
        latest=latest,
        entries=entries,
        labels=labels,
        weights=weights,
        body_fats=body_fats,
        muscles=muscles,
        waists=waists,
        view=view,
        staff=get_fitness_staff(),
    )


@fitness_bp.post("/body-matrix/<int:mid>/add")
@permission_required("body_matrix")
def body_matrix_add(mid: int):
    recorded_on = parse_date_value(request.form.get("date"))
    get_db().execute(
        """
        INSERT INTO body_matrix (
            member_id, recorded_by_id, date, week_number, month, weight_kg, body_fat_pct,
            muscle_mass_kg, bmi, chest_cm, waist_cm, hips_cm, arms_cm, thighs_cm, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mid,
            request.form.get("recorded_by_id") or session.get("user_id"),
            recorded_on.isoformat(),
            recorded_on.isocalendar()[1],
            recorded_on.strftime("%Y-%m"),
            as_float(request.form.get("weight_kg"), None),
            as_float(request.form.get("body_fat_pct"), None),
            as_float(request.form.get("muscle_mass_kg"), None),
            as_float(request.form.get("bmi"), None),
            as_float(request.form.get("chest_cm"), None),
            as_float(request.form.get("waist_cm"), None),
            as_float(request.form.get("hips_cm"), None),
            as_float(request.form.get("arms_cm"), None),
            as_float(request.form.get("thighs_cm"), None),
            str(request.form.get("notes", "")).strip() or None,
        ),
    )
    get_db().commit()
    flash("Body matrix entry saved.", "success")
    return redirect(url_for("fitness.body_matrix_member", mid=mid))


# ── Nutrition guide ───────────────────────────────────────────────────────────

@fitness_bp.get("/nutrition")
@permission_required("nutrition_guide")
def nutrition_index():
    category = request.args.get("category", "all")
    category = category if category in {"all", "macro", "micro"} else "all"
    view = request.args.get("view", "all")

    clauses = ["1=1"]
    params = []
    if category in {"macro", "micro"}:
        clauses.append("category = ?")
        params.append(category)

    macro_types = {"protein", "carb", "fiber", "fat"}
    micro_types = {"vitamin", "mineral", "electrolyte", "antioxidant"}
    if view in macro_types:
        clauses.append("macro_type = ?")
        params.append(view)
    elif view in micro_types:
        clauses.append("micro_type = ?")
        params.append(view)

    foods = get_db().execute(
        f"SELECT * FROM nutrition_foods WHERE {' AND '.join(clauses)} ORDER BY category, name",
        tuple(params),
    ).fetchall()
    return render_template("fitness/nutrition.html", foods=foods, category=category, view=view)


@fitness_bp.route("/nutrition/add", methods=["GET", "POST"])
@permission_required("nutrition_guide")
def nutrition_add():
    if request.method == "POST":
        category = str(request.form.get("category", "macro")).strip() or "macro"
        name = str(request.form.get("name", "")).strip()
        if not name:
            flash("Food name is required.", "error")
        else:
            get_db().execute(
                """
                INSERT INTO nutrition_foods (
                    name, category, macro_type, micro_type, serving_size,
                    calories, protein_g, carbs_g, fiber_g, fat_g, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    category,
                    request.form.get("macro_type") if category == "macro" else None,
                    request.form.get("micro_type") if category == "micro" else None,
                    str(request.form.get("serving_size", "")).strip() or None,
                    as_float(request.form.get("calories")),
                    as_float(request.form.get("protein_g")),
                    as_float(request.form.get("carbs_g")),
                    as_float(request.form.get("fiber_g")),
                    as_float(request.form.get("fat_g")),
                    str(request.form.get("notes", "")).strip() or None,
                ),
            )
            get_db().commit()
            flash("Nutrition item added.", "success")
            return redirect(url_for("fitness.nutrition_index"))
    return render_template("fitness/nutrition_add.html")


# ── Supplements ───────────────────────────────────────────────────────────────

@fitness_bp.get("/supplements")
@permission_required("supplements")
def supplements_index():
    supp_type = request.args.get("type", "all")
    goal = request.args.get("goal", "all")
    risk = request.args.get("risk", "all")

    clauses = ["1=1"]
    params = []
    if supp_type != "all":
        clauses.append("supp_type = ?")
        params.append(supp_type)
    if goal != "all":
        clauses.append("goal = ?")
        params.append(goal)
    if risk != "all":
        clauses.append("risk_level = ?")
        params.append(as_int(risk))

    supps = get_db().execute(
        f"SELECT * FROM supplements WHERE {' AND '.join(clauses)} ORDER BY risk_level DESC, name",
        tuple(params),
    ).fetchall()
    return render_template(
        "fitness/supplements.html",
        supps=supps, supp_type=supp_type, goal=goal, risk=risk,
        risk_labels=RISK_LABELS, risk_colors=RISK_COLORS, risk_bg=RISK_BG,
    )


@fitness_bp.route("/supplements/add", methods=["GET", "POST"])
@permission_required("supplements")
def supplement_add():
    if request.method == "POST":
        name = str(request.form.get("name", "")).strip()
        supp_type = str(request.form.get("supp_type", "")).strip()
        goal = str(request.form.get("goal", "")).strip()
        if not all([name, supp_type, goal]):
            flash("Name, type, and goal are required.", "error")
        else:
            get_db().execute(
                """
                INSERT INTO supplements (name, brand, supp_type, goal, risk_level, description, dosage, notes, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    name,
                    str(request.form.get("brand", "")).strip() or None,
                    supp_type,
                    goal,
                    as_int(request.form.get("risk_level"), 1),
                    str(request.form.get("description", "")).strip() or None,
                    str(request.form.get("dosage", "")).strip() or None,
                    str(request.form.get("notes", "")).strip() or None,
                ),
            )
            get_db().commit()
            flash("Supplement added.", "success")
            return redirect(url_for("fitness.supplements_index"))
    return render_template(
        "fitness/supplement_add.html",
        risk_labels=RISK_LABELS, risk_colors=RISK_COLORS, risk_bg=RISK_BG,
    )


@fitness_bp.get("/supplements/<int:sid>")
@permission_required("supplements")
def supplement_detail(sid: int):
    db = get_db()
    supplement = db.execute("SELECT * FROM supplements WHERE id = ?", (sid,)).fetchone()
    if supplement is None:
        flash("Supplement not found.", "warning")
        return redirect(url_for("fitness.supplements_index"))

    assignments = db.execute(
        """
        SELECT ms.*, TRIM(COALESCE(m.first_name, '') || ' ' || COALESCE(m.last_name, '')) AS member_name
        FROM member_supplements ms
        JOIN members m ON m.id = ms.member_id
        WHERE ms.supplement_id = ?
        ORDER BY COALESCE(ms.start_date, ms.created_at) DESC, ms.id DESC
        """,
        (sid,),
    ).fetchall()
    return render_template(
        "fitness/supplement_detail.html",
        s=supplement, assignments=assignments, members=get_active_members(),
        risk_labels=RISK_LABELS, risk_colors=RISK_COLORS, risk_bg=RISK_BG,
    )


@fitness_bp.post("/supplements/<int:sid>/assign")
@permission_required("supplements")
def supplement_assign(sid: int):
    member_id = request.form.get("member_id")
    if not member_id:
        flash("Please select a member.", "error")
        return redirect(url_for("fitness.supplement_detail", sid=sid))

    end_date = str(request.form.get("end_date", "")).strip() or None
    active = 0 if end_date and parse_date_value(end_date) < date.today() else 1
    get_db().execute(
        """
        INSERT INTO member_supplements (
            member_id, supplement_id, assigned_by, start_date, end_date, dosage, goal, notes, active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            member_id,
            sid,
            str(request.form.get("assigned_by", "")).strip() or session.get("full_name"),
            parse_date_value(request.form.get("start_date")).isoformat(),
            end_date,
            str(request.form.get("dosage", "")).strip() or None,
            str(request.form.get("goal", "")).strip() or None,
            str(request.form.get("notes", "")).strip() or None,
            active,
        ),
    )
    get_db().commit()
    flash("Supplement assigned.", "success")
    return redirect(url_for("fitness.supplement_detail", sid=sid))


@fitness_bp.get("/members/<int:mid>/supplements")
@permission_required("supplements")
def member_supplements_view(mid: int):
    db = get_db()
    member_row = db.execute("SELECT * FROM members WHERE id = ?", (mid,)).fetchone()
    if member_row is None:
        flash("Member not found.", "warning")
        return redirect(url_for("members.members_index"))
    member = decrypt_member(member_row)

    assignments = db.execute(
        """
        SELECT ms.*, s.id AS supplement_id, s.name AS supp_name, s.supp_type,
               s.goal AS supp_goal, s.risk_level
        FROM member_supplements ms
        JOIN supplements s ON s.id = ms.supplement_id
        WHERE ms.member_id = ?
        ORDER BY COALESCE(ms.start_date, ms.created_at) DESC, ms.id DESC
        """,
        (mid,),
    ).fetchall()
    return render_template(
        "fitness/member_supplements.html",
        m=member, full_name=member_full_name(member), assignments=assignments,
        risk_labels=RISK_LABELS, risk_colors=RISK_COLORS, risk_bg=RISK_BG,
    )
