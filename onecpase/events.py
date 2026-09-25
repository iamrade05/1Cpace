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
import secrets
from datetime import date, datetime
from pathlib import Path

from flask import (Blueprint, Response, abort, current_app, flash, redirect,
                   render_template, request, send_file, session, url_for)
from werkzeug.utils import secure_filename

from .admin import _admin_required
from .comms import send_whatsapp_message
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

BANNER_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


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

    # Additive: each registrant's numbered place at the event, a private
    # token for their personal ticket link (never the guessable sequential
    # number - that would let anyone check someone else in), and whether
    # they actually showed up. A friend brought along gets their own row via
    # invited_by_registration_id rather than being folded into the primary
    # registrant's row, so both get their own number, ticket, and check-in.
    columns = {
        "registration_number": "INTEGER",
        "ticket_token": "TEXT",
        "checked_in": "INTEGER DEFAULT 0",
        "checked_in_at": timestamp,
        "invited_by_registration_id": "INTEGER",
        "friend_phone": "TEXT",
        # Tracks that a ticket message was handed off for delivery (queued
        # or sent), not that it was confirmed delivered - WhatsApp Web
        # sending is asynchronous. Lets the catch-up "Resend Tickets" admin
        # action skip people already notified instead of messaging everyone
        # again on every click.
        "ticket_sent": "INTEGER DEFAULT 0",
        "ticket_sent_at": timestamp,
    }
    for name, definition in columns.items():
        if backend == "postgresql":
            from .db_adapter import pg_table_columns
            existing = set(pg_table_columns(db._conn, "event_registrations"))
        else:
            existing = {row[1] for row in db.execute("PRAGMA table_info(event_registrations)").fetchall()}
        if name not in existing:
            db.execute(f"ALTER TABLE event_registrations ADD COLUMN {name} {definition}")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_event_registrations_ticket_token "
        "ON event_registrations(ticket_token)"
    )
    db.commit()
    _backfill_registration_numbers_and_tokens(db)


def _backfill_registration_numbers_and_tokens(db) -> None:
    """One-time catch-up for registrations that existed before
    registration_number/ticket_token did. Renumbers every registration in
    each event by true signup order (registered_at, id) rather than only
    filling the gaps, so a handful of people who happened to register in
    the narrow window between this column being added and this backfill
    running don't end up with numbers ahead of people who actually signed
    up first. Safe to do: nothing had a working WhatsApp send configured
    yet, so no one has actually seen a registration number to contradict."""
    missing = db.execute(
        "SELECT COUNT(*) FROM event_registrations WHERE registration_number IS NULL"
    ).fetchone()[0]
    if not missing:
        return
    for event_row in db.execute("SELECT DISTINCT event_id FROM event_registrations").fetchall():
        rows = db.execute(
            "SELECT id FROM event_registrations WHERE event_id=? ORDER BY registered_at, id",
            (event_row[0],),
        ).fetchall()
        for number, row in enumerate(rows, start=1):
            db.execute(
                "UPDATE event_registrations SET registration_number=? WHERE id=?",
                (number, row["id"]),
            )
    for row in db.execute(
        "SELECT id FROM event_registrations WHERE ticket_token IS NULL"
    ).fetchall():
        db.execute(
            "UPDATE event_registrations SET ticket_token=? WHERE id=?",
            (secrets.token_urlsafe(24), row["id"]),
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


def _next_registration_number(db, event_id: int) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(registration_number), 0) FROM event_registrations WHERE event_id=?",
        (event_id,),
    ).fetchone()
    return row[0] + 1


def _new_ticket_token() -> str:
    return secrets.token_urlsafe(24)


def _send_ticket_message(event, registration, *, invited_by_name: str | None = None) -> None:
    """Best-effort WhatsApp send of the personal ticket link. Never blocks
    registration on delivery - a WhatsApp/network hiccup must not make the
    person think they failed to register when they didn't."""
    link = url_for(
        "events.ticket", slug=event["slug"], token=registration["ticket_token"], _external=True
    )
    if invited_by_name:
        text = (
            f"Hi {registration['first_name']}, {invited_by_name} has registered you for "
            f"{event['name']}! You're registration #{registration['registration_number']}. "
            f"View your ticket and check in on the day here: {link}"
        )
    else:
        text = (
            f"Hi {registration['first_name']}, you're registered for {event['name']} — "
            f"you're registration #{registration['registration_number']}. "
            f"Here's your ticket, and a button to let us know you've arrived on the day: {link}"
        )
    # Prefer the official Cloud API when it's actually configured - only
    # fall back to driving a real WhatsApp Web session (real ban risk to
    # whichever number is linked) when it isn't.
    sent = False
    if current_app.config.get("WA_TOKEN") and current_app.config.get("WA_RECEPTION_PHONE_ID"):
        try:
            send_whatsapp_message(
                current_app.config.get("WA_RECEPTION_PHONE_ID", ""), registration["phone"], text,
            )
            sent = True
        except Exception:
            current_app.logger.exception(
                "Cloud API WhatsApp send failed for registration %s", registration["id"]
            )
    if not sent and current_app.config.get("WHATSAPP_WEB_ENABLED"):
        from . import whatsapp_web
        whatsapp_web.queue_message(registration["phone"], text)
        sent = True
    if sent:
        get_db().execute(
            "UPDATE event_registrations SET ticket_sent=1, ticket_sent_at=datetime('now') WHERE id=?",
            (registration["id"],),
        )
        get_db().commit()


def _banner_dir() -> Path:
    # Deliberately UPLOAD_FOLDER, not onecpase/static/ - deploy_beta.py
    # replaces the onecpase package wholesale on every deploy, which would
    # silently wipe out any banner saved under static/. UPLOAD_FOLDER lives
    # outside the deployed code tree and is never touched by a deploy.
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "events"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_banner(file, slug: str, *, previous_filename: str | None = None) -> str | None:
    """Save an uploaded banner image, replacing any previous one for this
    event. Returns the stored filename, or None if no file was submitted."""
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in BANNER_EXTENSIONS:
        raise ValueError("Banner must be a JPG, PNG, or WEBP image.")

    banner_dir = _banner_dir()
    if previous_filename and previous_filename != f"{slug}.{ext}":
        (banner_dir / secure_filename(previous_filename)).unlink(missing_ok=True)

    filename = f"{slug}.{ext}"
    file.save(str(banner_dir / filename))
    return filename


@events_bp.get("/<slug>/banner")
def event_banner(slug: str):
    db = get_db()
    event = db.execute("SELECT banner_filename FROM events WHERE slug=?", (slug,)).fetchone()
    if not event or not event["banner_filename"]:
        abort(404)
    path = _banner_dir() / secure_filename(event["banner_filename"])
    if not path.exists():
        abort(404)
    return send_file(str(path))


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
        "friend_phone": request.form.get("friend_phone", "").strip(),
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
    friend_phone_digits = ""
    if values["bringing_friend"]:
        if not values["friend_name"]:
            errors.append("Please enter your friend's name, or untick bringing a friend.")
        friend_phone_digits = normalise_phone(values["friend_phone"])
        if len(friend_phone_digits) < 10:
            errors.append("Please enter your friend's cellphone number so we can send them their own ticket.")
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
        "SELECT * FROM event_registrations WHERE event_id=? AND phone=?",
        (event["id"], phone_digits),
    ).fetchone()
    if existing:
        return render_template(
            "public/event_register_success.html", event=event, already=True,
            registration=existing,
        )

    registration_number = _next_registration_number(db, event["id"])
    ticket_token = _new_ticket_token()
    cursor = db.execute(
        """INSERT INTO event_registrations
           (event_id, first_name, last_name, phone, email, date_of_birth, gender,
            pace, referral_source, bringing_friend, friend_name, friend_phone,
            emergency_contact_name, emergency_contact_phone, emergency_contact_relation,
            waiver_accepted, registration_number, ticket_token)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event["id"], values["first_name"], values["last_name"], phone_digits,
            values["email"] or None, values["date_of_birth"], values["gender"] or None,
            values["pace"], values["referral_source"] or None,
            1 if values["bringing_friend"] else 0, values["friend_name"] or None,
            friend_phone_digits or None,
            values["emergency_contact_name"], normalise_phone(values["emergency_contact_phone"]),
            values["emergency_contact_relation"] or None, 1,
            registration_number, ticket_token,
        ),
    )
    registration_id = cursor.lastrowid

    friend_registration = None
    if values["bringing_friend"] and friend_phone_digits:
        friend_exists = db.execute(
            "SELECT id FROM event_registrations WHERE event_id=? AND phone=?",
            (event["id"], friend_phone_digits),
        ).fetchone()
        if not friend_exists:
            friend_first, _, friend_last = values["friend_name"].partition(" ")
            friend_number = _next_registration_number(db, event["id"])
            friend_token = _new_ticket_token()
            db.execute(
                """INSERT INTO event_registrations
                   (event_id, first_name, last_name, phone, pace,
                    emergency_contact_name, emergency_contact_phone, emergency_contact_relation,
                    waiver_accepted, registration_number, ticket_token, invited_by_registration_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["id"], friend_first or values["friend_name"], friend_last, friend_phone_digits,
                    values["pace"],
                    f"{values['first_name']} {values['last_name']}", phone_digits, "Friend",
                    1, friend_number, friend_token, registration_id,
                ),
            )
            friend_registration = db.execute(
                "SELECT * FROM event_registrations WHERE ticket_token=?", (friend_token,)
            ).fetchone()
    db.commit()

    registration = db.execute(
        "SELECT * FROM event_registrations WHERE id=?", (registration_id,)
    ).fetchone()
    _send_ticket_message(event, registration)
    if friend_registration:
        _send_ticket_message(
            event, friend_registration,
            invited_by_name=f"{values['first_name']} {values['last_name']}",
        )

    return render_template(
        "public/event_register_success.html", event=event, name=values["first_name"],
        registration=registration,
    )


# ── Personal ticket / on-the-day check-in ────────────────────────────────────

@events_bp.get("/<slug>/ticket/<token>")
def ticket(slug: str, token: str):
    db = get_db()
    registration = db.execute(
        """SELECT r.* FROM event_registrations r JOIN events e ON e.id = r.event_id
           WHERE e.slug=? AND r.ticket_token=?""",
        (slug, token),
    ).fetchone()
    if not registration:
        abort(404)
    event = db.execute("SELECT * FROM events WHERE id=?", (registration["event_id"],)).fetchone()
    return render_template("public/event_ticket.html", event=event, registration=registration)


@events_bp.post("/<slug>/ticket/<token>/checkin")
@limiter.limit("10 per minute")
def ticket_checkin(slug: str, token: str):
    db = get_db()
    registration = db.execute(
        """SELECT r.* FROM event_registrations r JOIN events e ON e.id = r.event_id
           WHERE e.slug=? AND r.ticket_token=?""",
        (slug, token),
    ).fetchone()
    if not registration:
        abort(404)
    if not registration["checked_in"]:
        db.execute(
            "UPDATE event_registrations SET checked_in=1, checked_in_at=datetime('now') WHERE id=?",
            (registration["id"],),
        )
        db.commit()
    return redirect(url_for("events.ticket", slug=slug, token=token))


# ── Staff administration ─────────────────────────────────────────────────────

@admin_events_bp.route("/")
@_admin_required
def events_index():
    db = get_db()
    events = db.execute(
        """SELECT e.*, COUNT(r.id) AS registration_count,
                  COALESCE(SUM(r.checked_in), 0) AS checked_in_count
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

        banner_filename = None
        if not errors:
            try:
                banner_filename = _save_banner(request.files.get("banner"), slug)
            except ValueError as exc:
                errors.append(str(exc))

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
                banner_filename,
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


@admin_events_bp.route("/<int:event_id>/edit", methods=["GET", "POST"])
@_admin_required
def event_edit(event_id: int):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)

    if request.method == "POST":
        errors = []
        if request.form.get("event_date"):
            try:
                date.fromisoformat(request.form["event_date"])
            except ValueError:
                errors.append("Enter a valid event date.")

        banner_filename = event["banner_filename"]
        if not errors:
            try:
                uploaded = _save_banner(
                    request.files.get("banner"), event["slug"],
                    previous_filename=event["banner_filename"],
                )
                if uploaded:
                    banner_filename = uploaded
            except ValueError as exc:
                errors.append(str(exc))

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("admin/event_form.html", event=event, form=request.form)

        name = request.form.get("name", "").strip() or event["name"]
        db.execute(
            """UPDATE events SET name=?, tagline=?, description=?, event_date=?, start_time=?,
               end_time=?, location=?, distance=?, banner_filename=?, waiver_text=?
               WHERE id=?""",
            (
                name,
                request.form.get("tagline", "").strip() or None,
                request.form.get("description", "").strip() or None,
                request.form.get("event_date") or None,
                request.form.get("start_time", "").strip() or None,
                request.form.get("end_time", "").strip() or None,
                request.form.get("location", "").strip() or None,
                request.form.get("distance", "").strip() or None,
                banner_filename,
                request.form.get("waiver_text", "").strip() or None,
                event_id,
            ),
        )
        db.commit()
        flash("Event updated.", "success")
        return redirect(url_for("admin_events.events_index"))

    return render_template("admin/event_form.html", event=event, form=dict(event))


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
        """SELECT r.*, inviter.first_name AS invited_by_first_name, inviter.last_name AS invited_by_last_name
           FROM event_registrations r
           LEFT JOIN event_registrations inviter ON inviter.id = r.invited_by_registration_id
           WHERE r.event_id=? ORDER BY r.registration_number""",
        (event_id,),
    ).fetchall()
    checked_in_count = sum(1 for r in registrations if r["checked_in"])
    not_yet_sent_count = sum(1 for r in registrations if not r["ticket_sent"])
    return render_template(
        "admin/event_registrations.html", event=event, registrations=registrations,
        checked_in_count=checked_in_count, not_yet_sent_count=not_yet_sent_count,
    )


@admin_events_bp.post("/<int:event_id>/registrations/resend-tickets")
@_admin_required
def event_registrations_resend_tickets(event_id: int):
    """Catch-up send for people who registered before ticket sending was
    wired up (or whose original send failed) - only messages registrations
    that were never marked ticket_sent, so repeat clicks don't re-message
    everyone every time."""
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)
    pending = db.execute(
        "SELECT * FROM event_registrations WHERE event_id=? AND COALESCE(ticket_sent, 0) = 0",
        (event_id,),
    ).fetchall()
    for registration in pending:
        invited_by_name = None
        if registration["invited_by_registration_id"]:
            inviter = db.execute(
                "SELECT first_name, last_name FROM event_registrations WHERE id=?",
                (registration["invited_by_registration_id"],),
            ).fetchone()
            if inviter:
                invited_by_name = f"{inviter['first_name']} {inviter['last_name']}"
        _send_ticket_message(event, registration, invited_by_name=invited_by_name)
    flash(
        f"Queued tickets for {len(pending)} registration(s) that hadn't been sent one yet.",
        "success",
    )
    return redirect(url_for("admin_events.event_registrations", event_id=event_id))


@admin_events_bp.get("/<int:event_id>/registrations/export.csv")
@_admin_required
def event_registrations_export(event_id: int):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)
    rows = db.execute(
        "SELECT * FROM event_registrations WHERE event_id=? ORDER BY registration_number",
        (event_id,),
    ).fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "#", "First name", "Last name", "Phone", "Email", "Date of birth", "Gender", "Pace",
        "Referral source", "Bringing a friend", "Friend name", "Friend phone",
        "Emergency contact", "Emergency phone", "Emergency relation",
        "Checked in", "Checked in at", "Registered at",
    ])
    for r in rows:
        writer.writerow([
            r["registration_number"], r["first_name"], r["last_name"], r["phone"], r["email"] or "",
            r["date_of_birth"] or "", r["gender"] or "", r["pace"] or "",
            r["referral_source"] or "", "Yes" if r["bringing_friend"] else "No",
            r["friend_name"] or "", r["friend_phone"] or "", r["emergency_contact_name"],
            r["emergency_contact_phone"], r["emergency_contact_relation"] or "",
            "Yes" if r["checked_in"] else "No", r["checked_in_at"] or "",
            r["registered_at"],
        ])
    filename = f"{event['slug']}-registrations-{datetime.now():%Y%m%d}.csv"
    return Response(
        buffer.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
