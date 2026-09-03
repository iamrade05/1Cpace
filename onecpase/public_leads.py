from datetime import datetime

from flask import Blueprint, abort, g, render_template, request

from .database import get_db
from .extensions import limiter
from .leads import (
    _add_activity,
    _allocate_sales_consultant,
    _automatic_next_action,
    _find_duplicate,
    normalise_phone,
)
from .platform_db import get_platform_db


join_bp = Blueprint("join", __name__, url_prefix="/join")

CHANNELS = {
    "facebook": ("Social Media", "Facebook"),
    "instagram": ("Social Media", "Instagram"),
    "tiktok": ("Social Media", "TikTok"),
    "website": ("Website", "Website"),
}
TRAINING_EXPERIENCE = {"none", "some", "experienced"}
TRAINING_PREFERENCE = {"personal_trainer", "train_alone", "not_sure"}
JOINING_PREFERENCE = {"alone", "with_someone"}


def _select_public_tenant(tenant_slug: str):
    tenant = get_platform_db().execute(
        "SELECT * FROM tenants WHERE slug=? AND active=1", (tenant_slug,)
    ).fetchone()
    if not tenant:
        abort(404)
    g.tenant = dict(tenant)
    g.tenant_slug = tenant["slug"]
    return tenant


@join_bp.route("/<tenant_slug>/<channel>", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def public_join(tenant_slug: str, channel: str):
    channel = channel.lower()
    if channel not in CHANNELS:
        abort(404)
    tenant = _select_public_tenant(tenant_slug)
    source, campaign = CHANNELS[channel]

    if request.method == "GET":
        return render_template(
            "public/join.html", tenant=tenant, campaign=campaign, values={}, errors=[]
        )

    values = {
        "full_name": request.form.get("full_name", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip().lower(),
        "training_experience": request.form.get("training_experience", ""),
        "training_preference": request.form.get("training_preference", ""),
        "joining_preference": request.form.get("joining_preference", ""),
    }
    errors = []
    if request.form.get("company_website", "").strip():
        return render_template("public/join_success.html", tenant=tenant)
    if not values["full_name"]:
        errors.append("Please enter your full name.")
    if len(normalise_phone(values["phone"])) < 10:
        errors.append("Please enter a valid cellphone number.")
    if values["training_experience"] not in TRAINING_EXPERIENCE:
        errors.append("Please select your training experience.")
    if values["training_preference"] not in TRAINING_PREFERENCE:
        errors.append("Please select how you would like to train.")
    if values["joining_preference"] not in JOINING_PREFERENCE:
        errors.append("Please select whether you are joining alone.")
    if request.form.get("contact_consent") != "1":
        errors.append("Please allow the gym to contact you about this enquiry.")
    if errors:
        return render_template(
            "public/join.html", tenant=tenant, campaign=campaign, values=values, errors=errors
        ), 400

    db = get_db()
    duplicate = _find_duplicate(db, values["phone"])
    if duplicate:
        _add_activity(
            db,
            duplicate["id"],
            "note",
            outcome="recorded",
            notes=f"Repeat online enquiry received through {campaign}; original lead source retained.",
        )
        db.commit()
        return render_template("public/join_success.html", tenant=tenant)

    consultant = _allocate_sales_consultant(db, None, source)
    assigned_to = consultant["id"] if consultant else None
    lead_status = "assigned" if consultant else "captured"
    next_action, next_action_at = _automatic_next_action(lead_status)
    cursor = db.execute(
        """INSERT INTO leads
           (full_name, phone, phone_normalized, email, source, original_source,
            campaign, assigned_to, lead_status, next_action, next_action_at,
            marketing_consent, do_not_contact, created_by, last_activity_at,
            lead_channel, entry_path, outreach_needs_appointment,
            training_experience, training_preference, joining_preference)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, datetime('now'),
                   'outreach', 'standard', NULL, ?, ?, ?)""",
        (
            values["full_name"],
            values["phone"],
            normalise_phone(values["phone"]),
            values["email"],
            source,
            source,
            campaign,
            assigned_to,
            lead_status,
            next_action,
            next_action_at,
            values["training_experience"],
            values["training_preference"],
            values["joining_preference"],
        ),
    )
    lead_id = cursor.lastrowid
    if consultant:
        db.execute(
            "UPDATE users SET last_lead_assigned_at=? WHERE id=?",
            (datetime.now().isoformat(timespec="microseconds", sep=" "), assigned_to),
        )
    _add_activity(
        db,
        lead_id,
        "prospect_created",
        outcome=lead_status,
        notes=f"Online enquiry captured from {campaign}.",
        next_action=next_action,
        follow_up_at=next_action_at,
    )
    if next_action_at:
        _add_activity(
            db,
            lead_id,
            "follow_up",
            outcome="scheduled",
            status="scheduled",
            notes=next_action,
            scheduled_for=next_action_at,
        )
    db.commit()
    return render_template("public/join_success.html", tenant=tenant)
