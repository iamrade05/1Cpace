"""Daily fitness onboarding for newly joined members."""
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from .auth import login_required, permission_required
from .database import get_db
from .encryption import decrypt_member

onboarding_bp = Blueprint("onboarding", __name__, url_prefix="/fitness/onboarding")
ONBOARDING_VISITS = 7
ONBOARDING_MAX_DAYS = 30
ELIGIBILITY_DAYS = 45

VISIT_ACTIONS = [("welcomed", "Welcomed"), ("gym_intro", "Gym/equipment introduction"),
                 ("assessment", "Assessment completed"), ("demo", "Exercises demonstrated"),
                 ("assistance", "Workout assistance"), ("independent", "Trained independently"),
                 ("pt_interest", "Personal Training interest"), ("follow_up", "Follow-up required")]
ACTION_LABELS = dict(VISIT_ACTIONS)
VISIT_OUTCOMES = [("assisted", "Assisted"), ("independent", "Training independently"),
                  ("needs_assistance", "Needs further assistance"), ("pt_interest", "Interested in Personal Training"),
                  ("follow_up", "Follow-up required"), ("not_assisted", "Not assisted")]
REVIEW_OUTCOMES = [("independent", "Comfortable training independently"),
                   ("needs_assistance", "Requires additional assistance"),
                   ("pt_interest", "Interested in Personal Training"),
                   ("classes_interest", "Interested in group classes"),
                   ("needs_programme", "Requires a training programme"),
                   ("follow_up", "Requires follow-up")]
ACTIVITY_FACTORS = {"sedentary": 1.20, "light": 1.375, "moderate": 1.55, "active": 1.725, "very_active": 1.90}
ACTIVITY_LEVELS = [("sedentary", "Sedentary"), ("light", "Light"), ("moderate", "Moderate"),
                   ("active", "Active"), ("very_active", "Very active")]


def calculate_bmi(weight_kg, height_cm):
    try:
        weight, height = float(weight_kg), float(height_cm)
    except (TypeError, ValueError):
        return None
    return round(weight / ((height / 100) ** 2), 1) if weight > 0 and height > 0 else None


def calculate_daily_calories(weight_kg, height_cm, age_years, gender, activity_level):
    try:
        weight, height, age = float(weight_kg), float(height_cm), int(age_years)
    except (TypeError, ValueError):
        return None
    if weight <= 0 or height <= 0 or not 10 <= age <= 100:
        return None
    bmr = 10 * weight + 6.25 * height - 5 * age + (5 if str(gender).lower() in ("m", "male") else -161)
    return int(round(bmr * ACTIVITY_FACTORS.get(activity_level, ACTIVITY_FACTORS["light"])))


def add_onboarding_columns():
    db = get_db()
    if db.__class__.__module__.startswith("onecpase.db_adapter"):
        existing = {
            row[0] for row in db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='assessments'"
            ).fetchall()
        }
    else:
        existing = {row[1] for row in db.execute("PRAGMA table_info(assessments)").fetchall()}
    for column, declaration in {"height_cm": "REAL", "activity_level": "TEXT", "calorie_target": "INTEGER",
                               "training_experience": "TEXT", "fitness_goal": "TEXT",
                               "is_onboarding": "INTEGER DEFAULT 0"}.items():
        if column not in existing:
            db.execute(f"ALTER TABLE assessments ADD COLUMN {column} {declaration}")
    db.commit()


def ensure_onboarding_rows(db):
    db.execute("""INSERT INTO member_onboarding (member_id, status, eligible_from)
        SELECT m.id, 'eligible', COALESCE(m.join_date, date('now')) FROM members m
        WHERE m.outcome='joined' AND NOT EXISTS
        (SELECT 1 FROM member_onboarding o WHERE o.member_id=m.id)""")
    db.commit()


def _close(db, row, reason):
    db.execute("""UPDATE member_onboarding SET status='complete', closed_at=datetime('now','localtime'),
                 close_reason=?, updated_at=datetime('now','localtime') WHERE id=?""", (reason, row["id"]))


def register_arrival(db, member_id, source="turnstile", at=None):
    at = at or datetime.now()
    day = at.date().isoformat()
    row = db.execute("SELECT * FROM member_onboarding WHERE member_id=?", (member_id,)).fetchone()
    if not row or row["status"] in ("complete", "lapsed"):
        return None
    existing = db.execute("SELECT id FROM onboarding_visits WHERE member_id=? AND visit_date=?", (member_id, day)).fetchone()
    if existing:
        return existing["id"]
    first = row["first_visit_date"] or day
    if (at.date() - date.fromisoformat(str(first)[:10])).days >= ONBOARDING_MAX_DAYS:
        _close(db, row, "window_expired"); db.commit(); return None
    visit_no = (row["visits_recorded"] or 0) + 1
    db.execute("""INSERT INTO onboarding_visits
        (onboarding_id, member_id, visit_no, visit_date, checked_in_at, arrival_source)
        VALUES (?,?,?,?,?,?)""", (row["id"], member_id, visit_no, day, at.strftime("%Y-%m-%d %H:%M:%S"), source))
    visit_id = db.execute("SELECT id FROM onboarding_visits WHERE member_id=? AND visit_date=?", (member_id, day)).fetchone()["id"]
    db.execute("""UPDATE member_onboarding SET status='active', first_visit_date=COALESCE(first_visit_date,?),
        last_visit_date=?, visits_recorded=?, updated_at=datetime('now','localtime') WHERE id=?""",
        (day, day, visit_no, row["id"]))
    if visit_no >= ONBOARDING_VISITS:
        _close(db, db.execute("SELECT * FROM member_onboarding WHERE id=?", (row["id"],)).fetchone(), "visits_complete")
    db.commit()
    return visit_id


def sync_arrivals_from_turnstile(db):
    state = db.execute("SELECT last_event_id FROM onboarding_sync_state WHERE id=1").fetchone()
    last_id = (state["last_event_id"] if state else 0) or 0
    events = db.execute("""SELECT id, member_id, created_at FROM turnstile_events WHERE id>? AND member_id IS NOT NULL
        AND lower(coalesce(decision,'')) IN ('allowed','granted','ok')
        AND lower(coalesce(direction,'in')) IN ('in','entry','') ORDER BY id""", (last_id,)).fetchall()
    for event in events:
        try: stamp = datetime.strptime(str(event["created_at"])[:19], "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError): stamp = datetime.now()
        register_arrival(db, event["member_id"], "turnstile", stamp); last_id = max(last_id, event["id"])
    db.execute("UPDATE onboarding_sync_state SET last_event_id=?, last_run_at=datetime('now','localtime') WHERE id=1", (last_id,)); db.commit()


def _today(): return date.today().isoformat()


@onboarding_bp.route("/")
@login_required
@permission_required("fitness_module")
def today_list():
    db = get_db(); ensure_onboarding_rows(db); sync_arrivals_from_turnstile(db)
    day = request.args.get("date") or _today()
    visits = db.execute("""SELECT v.*,m.first_name,m.last_name,m.package,u.full_name instructor_name
        FROM onboarding_visits v JOIN members m ON m.id=v.member_id LEFT JOIN users u ON u.id=v.instructor_id
        WHERE v.visit_date=? ORDER BY v.recorded_at IS NOT NULL,v.checked_in_at""", (day,)).fetchall()
    visits = [decrypt_member(v) for v in visits]
    reviews = db.execute("""SELECT o.*,m.first_name,m.last_name,m.package FROM member_onboarding o JOIN members m ON m.id=o.member_id
        WHERE o.status='complete' AND o.review_done=0 ORDER BY o.closed_at""").fetchall()
    return render_template("fitness/onboarding_today.html", day=day, visits=visits, awaiting=[v for v in visits if not v["recorded_at"]],
        reviews_due=reviews, never_attended=[], members_for_manual=[], action_labels=ACTION_LABELS, window_visits=ONBOARDING_VISITS)


@onboarding_bp.post("/arrive")
@login_required
@permission_required("fitness_module")
def manual_arrival():
    db = get_db(); ensure_onboarding_rows(db); visit_id = register_arrival(db, int(request.form["member_id"]), "manual")
    if visit_id: return redirect(url_for("onboarding.record_visit", vid=visit_id))
    flash("That member is not in an active onboarding window.", "error"); return redirect(url_for("onboarding.today_list"))


@onboarding_bp.route("/visit/<int:vid>", methods=["GET", "POST"])
@login_required
@permission_required("fitness_module")
def record_visit(vid):
    db = get_db(); visit = db.execute("SELECT v.*,m.first_name,m.last_name,m.package,m.join_date FROM onboarding_visits v JOIN members m ON m.id=v.member_id WHERE v.id=?", (vid,)).fetchone()
    if not visit: flash("Visit not found.", "error"); return redirect(url_for("onboarding.today_list"))
    if request.method == "POST":
        instructor = request.form.get("instructor_id")
        if not instructor: flash("Select an instructor.", "error"); return redirect(url_for("onboarding.record_visit", vid=vid))
        db.execute("UPDATE onboarding_visits SET instructor_id=?,actions=?,outcome=?,notes=?,recorded_at=datetime('now','localtime') WHERE id=?",
                   (int(instructor), ",".join(request.form.getlist("actions")), request.form.get("outcome") or None, request.form.get("notes") or None, vid)); db.commit()
        flash("Interaction recorded.", "success"); return redirect(url_for("onboarding.today_list"))
    instructors = db.execute("SELECT id,full_name FROM users WHERE active=1 AND role IN ('trainer','admin','manager') ORDER BY full_name").fetchall()
    return render_template("fitness/onboarding_visit.html", visit=visit, instructors=instructors, is_day_one=visit["visit_no"] == 1,
        visit_actions=VISIT_ACTIONS, visit_outcomes=VISIT_OUTCOMES, activity_levels=ACTIVITY_LEVELS, age=None, prior=[], action_labels=ACTION_LABELS)


@onboarding_bp.post("/visit/<int:vid>/assessment")
@login_required
@permission_required("fitness_module")
def save_assessment(vid):
    db = get_db()
    visit = db.execute(
        "SELECT v.*, m.gender, m.date_of_birth FROM onboarding_visits v "
        "JOIN members m ON m.id=v.member_id WHERE v.id=?", (vid,)
    ).fetchone()
    if not visit:
        flash("Visit not found.", "error")
        return redirect(url_for("onboarding.today_list"))

    weight = request.form.get("weight_kg")
    height = request.form.get("height_cm")
    bmi = calculate_bmi(weight, height)
    age = None
    dob = visit["date_of_birth"]
    if dob:
        birth_date = date.fromisoformat(str(dob)[:10])
        today = date.today()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    calories = calculate_daily_calories(
        weight, height, age, visit["gender"], request.form.get("activity_level")
    ) if age is not None else None
    db.execute(
        """INSERT INTO assessments
           (member_id, assessed_by_id, date, weight_kg, height_cm, bmi,
            activity_level, calorie_target, training_experience, fitness_goal,
            notes, is_onboarding)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
        (visit["member_id"], session.get("user_id"), visit["visit_date"],
         float(weight) if weight else None, float(height) if height else None,
         bmi, request.form.get("activity_level") or None, calories,
         (request.form.get("training_experience") or "").strip() or None,
         (request.form.get("fitness_goal") or "").strip() or None,
         (request.form.get("assessment_notes") or "").strip() or None),
    )
    db.commit()
    flash("Assessment saved.", "success")
    return redirect(url_for("onboarding.record_visit", vid=vid))


@onboarding_bp.route("/<int:oid>/review", methods=["GET", "POST"])
@login_required
@permission_required("fitness_module")
def seven_day_review(oid):
    db = get_db(); row = db.execute("SELECT o.*,m.first_name,m.last_name,m.package FROM member_onboarding o JOIN members m ON m.id=o.member_id WHERE o.id=?", (oid,)).fetchone()
    if not row: return redirect(url_for("onboarding.today_list"))
    if request.method == "POST":
        db.execute("UPDATE member_onboarding SET review_done=1,review_by_id=?,review_date=date('now','localtime'),review_outcome=?,review_notes=? WHERE id=?", (session.get("user_id"), request.form.get("review_outcome"), request.form.get("review_notes"), oid)); db.commit(); return redirect(url_for("onboarding.today_list"))
    visits = db.execute("SELECT v.*,u.full_name instructor_name FROM onboarding_visits v LEFT JOIN users u ON u.id=v.instructor_id WHERE v.onboarding_id=? ORDER BY visit_no", (oid,)).fetchall()
    return render_template("fitness/onboarding_review.html", row=row, visits=visits, review_outcomes=REVIEW_OUTCOMES, action_labels=ACTION_LABELS)


@onboarding_bp.route("/report")
@login_required
@permission_required("fitness_module")
def report():
    db = get_db(); start = request.args.get("start") or (date.today() - timedelta(days=30)).isoformat(); end = request.args.get("end") or _today()
    def count(sql, args=(start, end)): return db.execute(sql, args).fetchone()[0] or 0
    new = count("SELECT count(*) FROM member_onboarding WHERE eligible_from BETWEEN ? AND ?")
    attended = count("SELECT count(*) FROM member_onboarding WHERE eligible_from BETWEEN ? AND ? AND first_visit_date IS NOT NULL")
    recorded = count("SELECT count(*) FROM onboarding_visits v JOIN member_onboarding o ON o.id=v.onboarding_id WHERE v.recorded_at IS NOT NULL AND o.eligible_from BETWEEN ? AND ?")
    stats = {"new_members": new, "attended": attended, "never_attended": new-attended, "assessments_done": 0, "assisted": recorded, "interactions": recorded, "pt_interest": 0, "independent": 0, "follow_up": 0, "unrecorded_visits": count("SELECT count(*) FROM onboarding_visits v JOIN member_onboarding o ON o.id=v.onboarding_id WHERE v.recorded_at IS NULL AND o.eligible_from BETWEEN ? AND ?")}
    return render_template("fitness/onboarding_report.html", stats=stats, by_instructor=[], start=start, end=end)


@onboarding_bp.get("/pending-count")
@login_required
def pending_count():
    return jsonify({"pending": get_db().execute("SELECT count(*) FROM onboarding_visits WHERE visit_date=date('now','localtime') AND recorded_at IS NULL").fetchone()[0]})
