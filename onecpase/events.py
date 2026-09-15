"""One-off event registration: a public link anyone can register through,
and a staff-facing view of who signed up.

Separate from leads.py on purpose. A fun-run registrant is not a membership
enquiry working through the sales pipeline — it is participation in a single
dated event, and asking "how many people are coming to Heritage Day" should
never have to be answered by filtering the lead funnel for it.

Events are tenant data (like leads and members), so this table lives in the
tenant's own database, not platform.db - ensure_events_schema() is called
from database.py's init_db() the same way onecpase/sales_pipeline.py's
schema is.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from flask import (Blueprint, Response, abort, flash, redirect,
                   render_template, request, session, url_for)

from .admin import _admin_required
from .database import get_db
from .extensions import limiter

events_bp = Blueprint("events", __name__, url_prefix="/events")
admin_events_bp = Blueprint("admin_events", __name__, url_prefix="/admin/events")

GENDERS = ("Male", "Female", "Non-binary", "Prefer not to say")
PACES = ("Walk", "Jog", "Run")
REFERRAL_SOURCES = (
    "Social media", "Friend or family", "Flyer / Poster",
    "Community group", "ELEV8 Gym member", "Other",
)
RELATIONS = ("Spouse / Partner", "Parent", "Sibling", "Friend", "Other")


def ensure_events_schema(db, backend: str) -> None:
    integer_id = "SERIAL PRIMARY KEY" if backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    timestamp = "TIMESTAMPTZ" if backend == "postgresql" else "TEXT"

    db.execute(f"""CREATE TABLE IF NOT EXISTS events (
        id {integer_id},
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        tagline TEXT,
        description TEXT,
        event_date TEXT,
        start_time TEXT,
        end_time TEXT,
        location TEXT,
        distance TEXT,
        banner_filename TEXT,
        waiver_text TEXT,
        active INTEGER DEFAULT 1,
        created_by INTEGER,
        created_at {timestamp} DEFAULT {"NOW()" if backend == "postgresql" else "(datetime('now'))"}
    )""")
    db.execute(f"""CREATE TABLE IF NOT EXISTS event_registrations (
        id {integer_id},
        event_id INTEGER NOT NULL,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        phone TEXT NOT NULL,
        email TEXT,
        date_of_birth TEXT,
        gender TEXT,
        pace TEXT,
        referral_source TEXT,
        bringing_friend INTEGER DEFAULT 0,
        friend_name TEXT,
        emergency_contact_name TEXT NOT NULL,
        emergency_contact_phone TEXT NOT NULL,
        emergency_contact_relation TEXT,
        waiver_accepted INTEGER NOT NULL DEFAULT 0,
        registered_at {timestamp} DEFAULT {"NOW()" if backend == "postgresql" else "(datetime('now'))"},
        FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
    )""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_event_registrations_event "
        "ON event_registrations(event_id)"
    )
    # A person double-tapping Submit on a slow connection must not create two
    # rows; a genuine second registration under the same number for the same
    # event is not a real scenario worth allowing either.
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_event_registrations_unique "
        "ON event_registrations(event_id, phone)"
    )
    db.commit()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "event"


def normalise_phone(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _event_by_slug(db, slug: str):
    return db.execute(
        "SELECT * FROM events WHERE slug=? AND active=1", (slug,)
    ).fetchone()


# ── Public registration ──────────────────────────────────────────────────────

@events_bp.route("/<slug>", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def register(slug: str):
    db = get_db()
    event = _event_by_slug(db, slug)
    if not event:
        abort(404)

    if request.method == "GET":
        return render_template(
            "public/event_register.html", event=event, values={}, errors=[],
            genders=GENDERS, referral_sources=REFERRAL_SOURCES, relations=RELATIONS,
        )

    # Honeypot: invisible to a person, irresistible to a bot filling every field.
    if request.form.get("company_website", "").strip():
        return render_template("public/event_register_success.html", event=event)

    values = {
        "first_name": request.form.get("first_name", "").strip(),
        "last_name": request.form.get("last_name", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip().lower(),
        "date_of_birth": request.form.get("date_of_birth", "").strip(),
        "gender": request.form.get("gender", "").strip(),
        "pace": request.form.get("pace", "").strip(),
        "referral_source": request.form.get("referral_source", "").strip(),
        "bringing_friend": request.form.get("bringing_friend") == "1",
        "friend_name": request.form.get("friend_name", "").strip(),
        "emergency_contact_name": request.form.get("emergency_contact_name", "").strip(),
        "emergency_contact_phone": request.form.get("emergency_contact_phone", "").strip(),
        "emergency_contact_relation": request.form.get("emergency_contact_relation", "").strip(),
        "waiver_accepted": request.form.get("waiver_accepted") == "1",
    }

    errors = []
    if not values["first_name"]:
        errors.append("Please enter your first name.")
    if not values["last_name"]:
        errors.append("Please enter your last name.")
    phone_digits = normalise_phone(values["phone"])
    if len(phone_digits) < 10:
        errors.append("Please enter a valid cellphone number.")
    if values["email"] and not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", values["email"]):
        errors.append("Please enter a valid email address.")
    if not values["date_of_birth"]:
        errors.append("Please enter your date of birth.")
    else:
        try:
            date.fromisoformat(values["date_of_birth"])
        except ValueError:
            errors.append("Please enter a valid date of birth.")
    if values["pace"] not in PACES:
        errors.append("Please choose a pace.")
    if values["bringing_friend"] and not values["friend_name"]:
        errors.append("Please enter your friend's name, or untick bringing a friend.")
    if not values["emergency_contact_name"]:
        errors.append("Please enter an emergency contact name.")
    if len(normalise_phone(values["emergency_contact_phone"])) < 10:
        errors.append("Please enter a valid emergency contact number.")
    if not values["waiver_accepted"]:
        errors.append("You must accept the participation waiver to register.")

    if errors:
        return render_template(
            "public/event_register.html", event=event, values=values, errors=errors,
            genders=GENDERS, referral_sources=REFERRAL_SOURCES, relations=RELATIONS,
        )

    existing = db.execute(
        "SELECT id FROM event_registrations WHERE event_id=? AND phone=?",
        (event["id"], phone_digits),
    ).fetchone()
    if existing:
        return render_template("public/event_register_success.html", event=event, already=True)

    db.execute(
        """INSERT INTO event_registrations
           (event_id, first_name, last_name, phone, email, date_of_birth, gender,
            pace, referral_source, bringing_friend, friend_name,
            emergency_contact_name, emergency_contact_phone, emergency_contact_relation,
            waiver_accepted)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event["id"], values["first_name"], values["last_name"], phone_digits,
            values["email"] or None, values["date_of_birth"], values["gender"] or None,
            values["pace"], values["referral_source"] or None,
            1 if values["bringing_friend"] else 0, values["friend_name"] or None,
            values["emergency_contact_name"], normalise_phone(values["emergency_contact_phone"]),
            values["emergency_contact_relation"] or None, 1,
        ),
    )
    db.commit()
    return render_template(
        "public/event_register_success.html", event=event, name=values["first_name"]
    )


# ── Staff administration ─────────────────────────────────────────────────────

@admin_events_bp.route("/")
@_admin_required
def events_index():
    db = get_db()
    events = db.execute(
        """SELECT e.*, COUNT(r.id) AS registration_count
           FROM events e LEFT JOIN event_registrations r ON r.event_id = e.id
           GROUP BY e.id ORDER BY e.event_date DESC, e.id DESC"""
    ).fetchall()
    return render_template("admin/events.html", events=events)


@admin_events_bp.route("/add", methods=["GET", "POST"])
@_admin_required
def event_add():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        errors = []
        if not name:
            errors.append("The event needs a name.")
        slug = _slugify(request.form.get("slug", "").strip() or name)
        if request.form.get("event_date"):
            try:
                date.fromisoformat(request.form["event_date"])
            except ValueError:
                errors.append("Enter a valid event date.")
        if not errors and get_db().execute(
            "SELECT 1 FROM events WHERE slug=?", (slug,)
        ).fetchone():
            errors.append(f"The link \"{slug}\" is already used by another event.")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("admin/event_form.html", event=None, form=request.form)

        db = get_db()
        db.execute(
            """INSERT INTO events
               (slug, name, tagline, description, event_date, start_time, end_time,
                location, distance, banner_filename, waiver_text, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                slug, name,
                request.form.get("tagline", "").strip() or None,
                request.form.get("description", "").strip() or None,
                request.form.get("event_date") or None,
                request.form.get("start_time", "").strip() or None,
                request.form.get("end_time", "").strip() or None,
                request.form.get("location", "").strip() or None,
                request.form.get("distance", "").strip() or None,
                # Set by convention so "drop the file in" is the only step to
                # add a banner later - see event_register.html, which treats
                # a missing file as a graceful CSS fallback, not a broken image.
                f"{slug}.jpg",
                request.form.get("waiver_text", "").strip() or None,
                session.get("user_id"),
            ),
        )
        db.commit()
        flash(
            f"Event created. Registration link: {url_for('events.register', slug=slug, _external=True)}",
            "success",
        )
        return redirect(url_for("admin_events.events_index"))

    return render_template("admin/event_form.html", event=None, form={})


@admin_events_bp.post("/<int:event_id>/toggle")
@_admin_required
def event_toggle(event_id: int):
    db = get_db()
    event = db.execute("SELECT active FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)
    db.execute("UPDATE events SET active=? WHERE id=?", (0 if event["active"] else 1, event_id))
    db.commit()
    return redirect(url_for("admin_events.events_index"))


@admin_events_bp.get("/<int:event_id>/registrations")
@_admin_required
def event_registrations(event_id: int):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)
    registrations = db.execute(
        "SELECT * FROM event_registrations WHERE event_id=? ORDER BY registered_at DESC",
        (event_id,),
    ).fetchall()
    return render_template(
        "admin/event_registrations.html", event=event, registrations=registrations
    )


@admin_events_bp.get("/<int:event_id>/registrations/export.csv")
@_admin_required
def event_registrations_export(event_id: int):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)
    rows = db.execute(
        "SELECT * FROM event_registrations WHERE event_id=? ORDER BY registered_at",
        (event_id,),
    ).fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "First name", "Last name", "Phone", "Email", "Date of birth", "Gender", "Pace",
        "Referral source", "Bringing a friend", "Friend name",
        "Emergency contact", "Emergency phone", "Emergency relation", "Registered at",
    ])
    for r in rows:
        writer.writerow([
            r["first_name"], r["last_name"], r["phone"], r["email"] or "",
            r["date_of_birth"] or "", r["gender"] or "", r["pace"] or "",
            r["referral_source"] or "", "Yes" if r["bringing_friend"] else "No",
            r["friend_name"] or "", r["emergency_contact_name"],
            r["emergency_contact_phone"], r["emergency_contact_relation"] or "",
            r["registered_at"],
        ])
    filename = f"{event['slug']}-registrations-{datetime.now():%Y%m%d}.csv"
    return Response(
        buffer.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
