"""
Database connection factory for 1Cpase.

Supports two backends, selected automatically from the DATABASE_URL env var:
  - SQLite  (default)  — no DATABASE_URL or DATABASE_URL=""
  - PostgreSQL          — DATABASE_URL="postgresql://user:pass@host/db"

The DbAdapter wrapper in db_adapter.py translates SQLite-specific SQL syntax
to PostgreSQL at runtime, so route files remain backend-agnostic.
"""
import json
import re
import sqlite3
from pathlib import Path
from flask import current_app, g

from .schema import get_schema


# ── Connection factory ────────────────────────────────────────────────────────

def _db_type() -> str:
    url = current_app.config.get("DATABASE_URL", "")
    return "postgresql" if url.startswith(("postgresql://", "postgres://")) else "sqlite"


def get_db():
    if "db" not in g:
        if _db_type() == "postgresql":
            g.db = _connect_pg()
        else:
            tenant = g.get("tenant")
            g.db = _connect_sqlite(tenant["db_path"] if tenant else None)
    return g.db


def _connect_sqlite(path: str | None = None):
    conn = sqlite3.connect(
        path or current_app.config["DATABASE_PATH"],
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _connect_pg():
    import psycopg2
    from .db_adapter import DbAdapter
    raw = psycopg2.connect(current_app.config["DATABASE_URL"])
    raw.autocommit = False
    return DbAdapter(raw)


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ── Schema initialisation ─────────────────────────────────────────────────────

def init_db():
    """Initialise the platform registry, then create/migrate every tenant's
    database (each is a fully independent SQLite file). Postgres deployments
    (DATABASE_URL set) stay single-tenant/legacy and skip the tenant loop."""
    from .platform_db import init_platform_db, get_platform_db

    init_platform_db()

    backend = _db_type()
    if backend == "postgresql":
        _init_db_on_connection(get_db(), backend, "1Cpase")
        return

    for tenant in get_platform_db().execute("SELECT * FROM tenants WHERE active = 1").fetchall():
        tenant_path = Path(tenant["db_path"])
        tenant_path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect_sqlite(str(tenant_path))
        try:
            _init_db_on_connection(conn, "sqlite", tenant["name"])
        finally:
            conn.close()


def _init_db_on_connection(db, backend: str, tenant_name: str = "1Cpase") -> None:
    """Create tables and apply additive migrations on *db* — any open
    connection, tenant-scoped or not."""
    schema = get_schema(backend)
    # PostgreSQL DDL can be executed in one transaction; split on ; for SQLite compat
    if backend == "postgresql":
        db.execute(schema)
    else:
        for statement in schema.split(";"):
            if statement.strip():
                db.execute(statement)
    db.commit()

    from .sales_kpi import ensure_sales_kpi_schema
    ensure_sales_kpi_schema(db, backend)

    # ── Additive column migrations ────────────────────────────────────────────
    _ensure_column(db, backend, "users", "must_change_password", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "users", "staff_id", "INTEGER")
    _ensure_column(db, backend, "users", "is_platform_user", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "users", "platform_admin_id", "INTEGER")
    _ensure_column(db, backend, "users", "is_sales_consultant", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "users", "sales_available", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "users", "sales_rotation_order", "INTEGER")
    _ensure_column(db, backend, "users", "sales_disqualification_category", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "users", "sales_skip_remaining", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "users", "is_collections_caller", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "users", "pbx_extension", "TEXT")
    _ensure_column(
        db, backend, "users", "last_lead_assigned_at",
        "TIMESTAMPTZ" if backend == "postgresql" else "TEXT",
    )
    _ensure_column(db, backend, "debicheck_mandates", "updated_at",
                    "TIMESTAMPTZ" if backend == "postgresql" else "TEXT")

    # Elev8 Yeastar extension map. Only fills empty values so an administrator
    # can later override an assignment from the staff profile. Sales rotation
    # positions map to the four consultant extensions supplied for this site.
    pbx_sales_extensions = {1: "1001", 2: "1003", 3: "1005", 4: "1006"}
    try:
        db.execute(
            "UPDATE users SET pbx_extension='1000' "
            "WHERE COALESCE(pbx_extension, '')='' "
            "AND (LOWER(COALESCE(department,''))='reception' "
            "OR LOWER(COALESCE(full_name,''))='reception')"
        )
        db.execute(
            "UPDATE users SET pbx_extension='1002' "
            "WHERE COALESCE(pbx_extension, '')='' "
            "AND LOWER(COALESCE(full_name,'')) LIKE 'mvuleni%'"
        )
        db.execute(
            "UPDATE users SET pbx_extension='1004' "
            "WHERE COALESCE(pbx_extension, '')='' "
            "AND LOWER(COALESCE(role,''))='manager' "
            "AND LOWER(COALESCE(department,''))='sales'"
        )
        for position, extension in pbx_sales_extensions.items():
            db.execute(
                "UPDATE users SET pbx_extension=? "
                "WHERE COALESCE(pbx_extension, '')='' "
                "AND COALESCE(is_sales_consultant,0)=1 "
                "AND sales_rotation_order=?",
                (extension, position),
            )
    except Exception:
        # PBX assignment is additive and must never prevent database startup.
        db.rollback()

    # PBX integration tables. These are deliberately additive so existing
    # installations can enable calling without rebuilding tenant databases.
    if backend == "postgresql":
        db.execute("""CREATE TABLE IF NOT EXISTS pbx_call_logs (
            id BIGSERIAL PRIMARY KEY, external_call_id TEXT UNIQUE,
            lead_id BIGINT, member_id BIGINT, user_id BIGINT, collection_task_id BIGINT,
            direction TEXT NOT NULL, caller TEXT, callee TEXT,
            start_time TIMESTAMPTZ, duration INTEGER DEFAULT 0, talk_duration INTEGER DEFAULT 0,
            status TEXT, call_type TEXT, recording TEXT, raw_json TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS pbx_webhook_events (
            id BIGSERIAL PRIMARY KEY, event_key TEXT UNIQUE, event_type TEXT,
            received_at TIMESTAMPTZ DEFAULT NOW(), payload TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS pbx_account_codes (
            id BIGSERIAL PRIMARY KEY, code TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
            user_id BIGINT, active INTEGER DEFAULT 1, created_at TIMESTAMPTZ DEFAULT NOW(),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""")
    else:
        db.execute("""CREATE TABLE IF NOT EXISTS pbx_call_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, external_call_id TEXT UNIQUE,
            lead_id INTEGER, member_id INTEGER, user_id INTEGER, collection_task_id INTEGER,
            direction TEXT NOT NULL, caller TEXT, callee TEXT,
            start_time TEXT, duration INTEGER DEFAULT 0, talk_duration INTEGER DEFAULT 0,
            status TEXT, call_type TEXT, recording TEXT, raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS pbx_webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE, event_type TEXT,
            received_at TEXT DEFAULT (datetime('now')), payload TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS pbx_account_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
            user_id INTEGER, active INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""")
    db.commit()
    _ensure_column(db, backend, "pbx_call_logs", "collection_task_id", "INTEGER")
    _ensure_column(db, backend, "pbx_call_logs", "outcome", "TEXT")
    _ensure_column(db, backend, "pbx_call_logs", "outcome_notes", "TEXT")
    _ensure_column(db, backend, "pbx_call_logs", "next_action", "TEXT")
    _ensure_column(db, backend, "pbx_call_logs", "follow_up_at", "TEXT")
    _ensure_column(db, backend, "pbx_call_logs", "outcome_recorded_at", "TEXT")
    _ensure_column(db, backend, "pbx_call_logs", "outcome_recorded_by", "INTEGER")
    _ensure_column(db, backend, "pbx_call_logs", "account_code", "TEXT")

    # Elev8 reception/sales share physical phones and identify themselves by
    # PIN ("account code" in Yeastar's terms) rather than by extension. Only
    # inserted if the code doesn't already exist, so an admin can rename a
    # label or relink a user afterward without it being overwritten here.
    # "Sales 1/2/3" were supplied as generic pool codes, not tied to one
    # named person - left unlinked (user_id NULL) rather than guessed.
    try:
        for code, label, full_name_prefix in (
            ("3074", "Entle", "mfundentle"),
            ("5296", "Busi", "colin"),
            ("1852", "Mvuleni", "mvuleni"),
            ("1347", "Gugu", "gugulami"),
            ("4496", "Thokozane", "thokozani"),
        ):
            user = db.execute(
                "SELECT id FROM users WHERE LOWER(username) LIKE ?", (f"{full_name_prefix}%",)
            ).fetchone()
            db.execute(
                "INSERT INTO pbx_account_codes (code, label, user_id) "
                "SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM pbx_account_codes WHERE code=?)",
                (code, label, user["id"] if user else None, code),
            )
        for code, label in (("7536", "Sales 1"), ("8624", "Sales 2"), ("9841", "Sales 3")):
            db.execute(
                "INSERT INTO pbx_account_codes (code, label, user_id) "
                "SELECT ?, ?, NULL WHERE NOT EXISTS (SELECT 1 FROM pbx_account_codes WHERE code=?)",
                (code, label, code),
            )
    except Exception:
        db.rollback()
    _ensure_column(db, backend, "collections", "method", "TEXT DEFAULT 'cash'")
    _ensure_column(db, backend, "collections", "notes", "TEXT")
    _ensure_column(db, backend, "collections", "created_by", "INTEGER")
    _ensure_column(db, backend, "member_documents", "analysis_json", "TEXT")
    _ensure_column(db, backend, "statement_analyses", "income_months", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "statement_analyses", "verification_qualified", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "leave_requests", "staff_id", "INTEGER")
    _ensure_column(db, backend, "ptp_agreements", "discount_pct", "REAL DEFAULT 0")
    _ensure_column(db, backend, "ptp_agreements", "discount_amount", "REAL DEFAULT 0")
    _ensure_column(db, backend, "ptp_agreements", "discount_basis", "TEXT")
    _ensure_column(db, backend, "ptp_agreements", "discounted_balance", "REAL DEFAULT 0")
    _ensure_column(db, backend, "ptp_agreements", "upfront_percent", "REAL DEFAULT 30")
    _ensure_column(db, backend, "ptp_agreements", "upfront_amount", "REAL DEFAULT 0")
    _ensure_column(db, backend, "ptp_agreements", "remaining_balance", "REAL DEFAULT 0")
    _ensure_column(db, backend, "ptp_agreements", "recovery_installment", "REAL DEFAULT 0")
    _ensure_column(db, backend, "ptp_agreements", "recovery_months", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "collection_call_tasks", "whatsapp_sent", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "collection_call_tasks", "sms_sent", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "collection_call_tasks", "email_sent", "INTEGER DEFAULT 0")
    _ensure_column(db, backend, "collection_call_tasks", "next_channel", "TEXT DEFAULT 'CALL'")
    _ensure_column(db, backend, "collection_call_tasks", "next_action_at", "TEXT")

    from .collections_engine import ensure_collections_engine_schema
    ensure_collections_engine_schema(db, backend)

    # Sales CRM: the lead row is the permanent prospect profile. Calls,
    # appointments, visits, decisions and follow-ups live in lead_activities.
    for col, defn in [
        ("phone_normalized",       "TEXT"),
        ("original_source",       "TEXT"),
        ("referrer",              "TEXT"),
        ("campaign",              "TEXT"),
        ("decision_start_date",   "TEXT"),
        ("decision_date",         "TEXT"),
        ("next_action",           "TEXT"),
        ("next_action_at",        "TEXT"),
        ("marketing_consent",     "INTEGER DEFAULT 1"),
        ("do_not_contact",        "INTEGER DEFAULT 0"),
        ("closure_reason",        "TEXT"),
        ("recycle_due_at",        "TEXT"),
        ("objection_reason",      "TEXT"),
        ("duplicate_of_lead_id", "INTEGER"),
        ("converted_member_id",   "INTEGER"),
        ("last_activity_at",      "TEXT"),
        ("lead_channel",          "TEXT DEFAULT 'in_reach'"),
        ("entry_path",            "TEXT DEFAULT 'standard'"),
        ("outreach_needs_appointment", "INTEGER"),
        ("recycle_reason",        "TEXT"),
    ]:
        _ensure_column(db, backend, "leads", col, defn)
    from .sales_pipeline import ensure_sales_pipeline_schema
    ensure_sales_pipeline_schema(db, backend)

    from .events import ensure_events_schema
    ensure_events_schema(db, backend)
    db.execute("CREATE INDEX IF NOT EXISTS idx_leads_phone_normalized ON leads(phone_normalized)")
    # These indexes keep the idempotent application backfill fast at startup.
    db.execute("CREATE INDEX IF NOT EXISTS idx_membership_applications_member ON membership_applications(member_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_membership_applications_lead ON membership_applications(lead_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_compliance_checklists_application ON compliance_checklists(application_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_member_documents_member_type ON member_documents(member_id, document_type)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_collections_member_status ON collections(member_id, status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_legal_referrals_status ON legal_referrals(status, referred_at)")

    # Members extended profile
    for col, defn in [
        ("member_ref",                "TEXT"),
        ("date_of_birth",             "TEXT"),
        ("gender",                    "TEXT"),
        ("tariff",                    "TEXT"),
        ("contract_duration",         "TEXT"),
        ("monthly_installment",       "REAL DEFAULT 0"),
        ("source",                    "TEXT DEFAULT 'Walk-in'"),
        ("street_number",             "TEXT"),
        ("street_name",               "TEXT"),
        ("suburb",                    "TEXT"),
        ("city",                      "TEXT"),
        ("postal_code",               "TEXT"),
        ("country",                   "TEXT DEFAULT 'South Africa'"),
        ("payment_type",              "TEXT DEFAULT 'Debit Order'"),
        ("debit_order_date",          "TEXT"),
        ("bank",                      "TEXT"),
        ("account_type",              "TEXT"),
        ("account_name",              "TEXT"),
        ("account_number",            "TEXT"),
        ("branch_code",               "TEXT"),
        ("payer_name",                "TEXT"),
        ("payer_id_number",           "TEXT"),
        ("opt_in",                    "INTEGER DEFAULT 1"),
        ("fitness_goal",              "TEXT"),
        ("training_experience",       "INTEGER DEFAULT 0"),
        ("uploaded_by_id",            "INTEGER"),
        ("itensity_ref",              "TEXT"),
        ("access_credential",         "TEXT"),
        ("promotion",                 "TEXT"),
        ("access_level",              "TEXT DEFAULT 'Local'"),
        ("occupation",                "TEXT"),
        ("emergency_contact_name",    "TEXT"),
        ("emergency_contact_number",  "TEXT"),
        ("emergency_contact_relation","TEXT"),
        ("parq_q1",  "INTEGER DEFAULT 0"),
        ("parq_q2",  "INTEGER DEFAULT 0"),
        ("parq_q3",  "INTEGER DEFAULT 0"),
        ("parq_q4",  "INTEGER DEFAULT 0"),
        ("parq_q5",  "INTEGER DEFAULT 0"),
        ("parq_q6",  "INTEGER DEFAULT 0"),
        ("parq_q7",  "INTEGER DEFAULT 0"),
        ("parq_q8",  "INTEGER DEFAULT 0"),
        ("parq_q9",  "INTEGER DEFAULT 0"),
        ("gym_access_status",  "TEXT DEFAULT 'allowed'"),
        ("access_blocked_until", "TEXT"),
        ("access_block_reason", "TEXT"),
        # Encryption column for searchable id_number lookup
        ("id_number_hash", "TEXT"),
        # Optional contract fields (off by default on the printed contract)
        ("employer", "TEXT"),
        ("alternative_number", "TEXT"),
        ("work_number", "TEXT"),
        ("joining_fee", "REAL DEFAULT 0"),
        ("member_addons", "TEXT"),
        ("witness_name", "TEXT"),
        # Why a member is on a custom package (see CUSTOM_TARIFF)
        ("custom_package_note", "TEXT"),
    ]:
        _ensure_column(db, backend, "members", col, defn)
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_members_member_ref ON members(member_ref)")

    # One-time code a member must enter before approving a contract from the shared link
    for col, defn in [
        ("otp_hash",       "TEXT"),
        ("otp_expires_at", "TEXT"),
        ("otp_attempts",   "INTEGER DEFAULT 0"),
        ("otp_sends",      "INTEGER DEFAULT 0"),
        ("otp_sent_at",    "TEXT"),
        ("otp_sent_to",    "TEXT"),
        ("verified_at",    "TEXT"),
    ]:
        _ensure_column(db, backend, "contract_share_tokens", col, defn)

    # DebiCheck mandates recorded from NuPay's own report rather than created here: they
    # are already live at NuPay, so they carry NuPay's contract reference and are never
    # sent to NuPay again.
    for col, defn in [
        ("contract_reference",  "TEXT"),
        ("imported_from_nupay", "INTEGER DEFAULT 0"),
    ]:
        _ensure_column(db, backend, "debicheck_mandates", col, defn)

    for col, defn in [
        ("reg_no", "TEXT"),
        ("address", "TEXT"),
        ("accent_color", "TEXT DEFAULT '#185FA5'"),
        ("sections_config", "TEXT"),
        ("terms_parts_config", "TEXT"),
        ("debit_mandate_text", "TEXT"),
    ]:
        _ensure_column(db, backend, "contract_templates", col, defn)

    for col, defn in [
        ("training_experience", "TEXT"),
        ("training_preference", "TEXT"),
        ("joining_preference", "TEXT"),
    ]:
        _ensure_column(db, backend, "leads", col, defn)

    # Queries: financial decision snapshot (frozen at capture time)
    for col, defn in [
        ("outstanding_snapshot",        "REAL DEFAULT 0"),
        ("arrears_months_snapshot",     "INTEGER DEFAULT 0"),
        ("discount_percent_snapshot",   "REAL DEFAULT 0"),
        ("discount_amount_snapshot",    "REAL DEFAULT 0"),
        ("settlement_amount_snapshot",  "REAL DEFAULT 0"),
        ("recommended_action_snapshot", "TEXT"),
        ("access_decision_snapshot",    "TEXT"),
        ("manager_approval_required",   "INTEGER DEFAULT 0"),
        ("manager_approved_by",         "INTEGER"),
        ("manager_approved_at",         "TEXT"),
    ]:
        _ensure_column(db, backend, "queries", col, defn)

    # Queries: cancellation fee/settlement math (section 11.3 penalty schedule)
    real = "DOUBLE PRECISION" if backend == "postgresql" else "REAL"
    for col, defn in [
        ("cancellation_contract_months",  "INTEGER"),
        ("cancellation_months_completed", "INTEGER"),
        ("cancellation_monthly_fee",      f"{real} DEFAULT 0"),
        ("cancellation_fee",              f"{real} DEFAULT 0"),
        ("cancellation_penalty_months",   "INTEGER DEFAULT 0"),
        ("cancellation_payment_method",   "TEXT"),
        ("cancellation_outstanding",      f"{real} DEFAULT 0"),
        ("cancellation_settlement_amount", f"{real} DEFAULT 0"),
        ("cancellation_settlement_rule",  "TEXT"),
        ("cancellation_discount_pct",     f"{real} DEFAULT 0"),
        ("member_cancelled",              "INTEGER DEFAULT 0"),
    ]:
        _ensure_column(db, backend, "queries", col, defn)

    _relax_leave_requests_user_id(db, backend)

    _backfill_member_refs(db, tenant_name)
    db.commit()
    _migrate_sales_crm(db)
    _migrate_lead_lifecycle_v2(db)
    _backfill_member_applications(db)
    _seed_tariffs(db)
    _seed_contract_template(db)
    _backfill_contract_config(db)


def member_ref_prefix(tenant_name: str) -> str:
    """Return the first three letters of the tenant name for Member Ref."""
    letters = "".join(ch for ch in (tenant_name or "") if ch.isalpha()).upper()
    return (letters + "APP")[:3]


def _backfill_member_refs(db, tenant_name: str) -> None:
    prefix = member_ref_prefix(tenant_name)
    rows = db.execute("SELECT id FROM members WHERE member_ref IS NULL OR member_ref='' ORDER BY id").fetchall()
    used = {
        row[0] for row in db.execute("SELECT member_ref FROM members WHERE member_ref IS NOT NULL").fetchall()
    }
    next_number = 1
    for row in rows:
        while f"{prefix}-{next_number:010d}" in used:
            next_number += 1
        value = f"{prefix}-{next_number:010d}"
        db.execute("UPDATE members SET member_ref=? WHERE id=?", (value, row["id"]))
        used.add(value)
        next_number += 1


def next_member_ref(db, tenant_name: str) -> str:
    """Return the next tenant-local Member Ref without changing Itensity Ref."""
    prefix = member_ref_prefix(tenant_name)
    highest = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d{{10}})$")
    for row in db.execute("SELECT member_ref FROM members WHERE member_ref LIKE ?", (f"{prefix}-%",)).fetchall():
        match = pattern.match(str(row["member_ref"] or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1:010d}"


def current_tenant_name() -> str:
    """Return the active tenant name, including legacy single-tenant requests."""
    tenant = g.get("tenant")
    if tenant:
        return tenant["name"]
    if _db_type() == "postgresql":
        return "1Cpase"
    try:
        from .platform_db import get_platform_db
        configured_path = str(Path(current_app.config["DATABASE_PATH"]).resolve())
        for row in get_platform_db().execute(
            "SELECT name, db_path FROM tenants WHERE active=1 ORDER BY id"
        ).fetchall():
            if str(Path(row["db_path"]).resolve()) == configured_path:
                return row["name"]
    except Exception:
        current_app.logger.exception("Could not resolve tenant name for Member Ref")
    return "1Cpase"


# ── Column migration helpers ──────────────────────────────────────────────────

def _ensure_column(db, backend: str, table: str, column: str, definition: str) -> None:
    """Add *column* to *table* if it does not already exist."""
    if backend == "postgresql":
        from .db_adapter import pg_table_columns
        existing = pg_table_columns(db._conn, table)
    else:
        existing = [row[1] for row in
                    db.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        db.commit()


def _normalise_phone(value: str | None) -> str:
    """Return a comparison-safe South African phone number representation."""
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if digits.startswith("0027"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        return "27" + digits[1:]
    return digits


def _migrate_sales_crm(db) -> None:
    """Backfill the additive CRM fields without replacing existing prospects."""
    db.execute(
        "UPDATE leads SET original_source = COALESCE(NULLIF(original_source, ''), source, 'Walk-in')"
    )
    db.execute(
        """UPDATE leads SET
               decision_start_date = COALESCE(decision_start_date, trial_start_date),
               decision_date = COALESCE(decision_date, trial_end_date),
               lead_status = CASE
                   WHEN lead_status = 'trial_active' THEN 'decision'
                   WHEN lead_status = 'trial_offered' THEN 'consultation'
                   WHEN lead_status = 'expired' THEN 'inactive'
                   ELSE lead_status END"""
    )
    for row in db.execute("SELECT id, phone, phone_normalized FROM leads").fetchall():
        normalised = _normalise_phone(row["phone"])
        if normalised != (row["phone_normalized"] or ""):
            db.execute(
                "UPDATE leads SET phone_normalized = ? WHERE id = ?",
                (normalised, row["id"]),
            )
    db.commit()


def _migrate_lead_lifecycle_v2(db) -> None:
    """One-time (idempotent) Phase-1 lead lifecycle rename. Safe on every boot —
    the ELSE branches make re-runs a no-op, same pattern as _migrate_sales_crm.
    Must run after _migrate_sales_crm so any lingering trial_active/trial_offered/
    expired rows are already renamed to decision/consultation/inactive first."""
    db.execute(
        """UPDATE leads SET
               recycle_reason = CASE
                   WHEN lead_status = 'declined' THEN 'legacy_not_interested'
                   WHEN lead_status = 'unreachable' THEN 'legacy_unreachable'
                   WHEN lead_status = 'inactive' THEN 'seven_day_no_conversion'
                   ELSE recycle_reason END,
               lead_status = CASE
                   WHEN lead_status = 'new' THEN 'captured'
                   WHEN lead_status = 'appointment' THEN 'appointment_booked'
                   WHEN lead_status = 'visited' THEN 'show'
                   WHEN lead_status = 'consultation' THEN 'show'
                   WHEN lead_status = 'decision' THEN 'hot_prospect_7day'
                   WHEN lead_status IN ('declined', 'unreachable', 'inactive') THEN 'recycle'
                   ELSE lead_status END"""
    )
    # Legacy rows sometimes carried the lifecycle label without the matching
    # consent flags. Keep the legal contact block internally consistent.
    db.execute(
        """UPDATE leads SET do_not_contact=1, marketing_consent=0,
               next_action=NULL, next_action_at=NULL, recycle_due_at=NULL
           WHERE lead_status='do_not_contact'"""
    )
    db.commit()


def _relax_leave_requests_user_id(db, backend: str) -> None:
    """Allow leave_requests.user_id to be NULL, so staff-only (non-login) leave
    rows can be inserted without a user account. SQLite can't ALTER COLUMN, so
    the table is rebuilt in place when the old NOT NULL constraint is found."""
    if backend == "postgresql":
        db.execute("ALTER TABLE leave_requests ALTER COLUMN user_id DROP NOT NULL")
        db.commit()
        return

    columns = db.execute("PRAGMA table_info(leave_requests)").fetchall()
    user_id_col = next((c for c in columns if c[1] == "user_id"), None)
    if user_id_col is None or user_id_col[3] == 0:
        return  # column missing or already nullable

    keep_cols = [c[1] for c in columns]
    col_list = ", ".join(keep_cols)
    db.execute("ALTER TABLE leave_requests RENAME TO leave_requests_old")
    db.execute(
        """
        CREATE TABLE leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            staff_id INTEGER,
            leave_type TEXT NOT NULL DEFAULT 'annual',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            days INTEGER DEFAULT 1,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            approved_by INTEGER,
            approved_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (staff_id) REFERENCES staff(id),
            FOREIGN KEY (approved_by) REFERENCES users(id)
        )
        """
    )
    db.execute(f"INSERT INTO leave_requests ({col_list}) SELECT {col_list} FROM leave_requests_old")
    db.execute("DROP TABLE leave_requests_old")
    db.commit()


# ── Default tariff seeding ────────────────────────────────────────────────────

_DEFAULT_TARIFFS = [
    ("Premium 36 2026",    36, 302.25, 1),
    ("Premium 24 2026",    24, 334.75, 2),
    ("Premium 12 2026",    12, 345.58, 3),
    ("Pensioner",          36, 199.00, 4),
    ("Couples Membership", 36, 259.00, 5),
    ("Student",            36, 249.00, 6),
    ("Premium 24",         24, 334.75, 7),
    ("Premium 36 2025",    36, 302.25, 8),
    ("Staff",              12,   0.00, 9),
    ("Gym Swop",           12,   0.00, 10),
    ("Dependent",          12,   0.00, 11),
    ("Rewards program",    12,   0.00, 12),
    ("Black Friday 2025",  12,   0.00, 13),
]


# The tariff a manager sets for a member on a different agreement or a family
# package. The package name, amount and duration live in the ordinary member
# columns, so reports, applications and collections read them unchanged.
CUSTOM_TARIFF = "Custom"


def _seed_tariffs(db) -> None:
    for name, months, amount, order in _DEFAULT_TARIFFS:
        db.execute(
            """INSERT OR IGNORE INTO tariffs (name, duration_months, monthly_amount, sort_order)
               VALUES (?, ?, ?, ?)""",
            (name, months, amount, order),
        )
    db.commit()


_DEFAULT_CONTRACT_TERMS = """This membership agreement is binding and constitutes a legal contract between the member and {gym_name}.
Monthly fees are payable in advance and are non-refundable.
The member authorises {gym_name} to debit their account on the agreed debit order date each month.
A 60 days written notice is required to cancel this membership. Early termination fees apply for fixed-term contracts.
The gym reserves the right to suspend access for non-payment. A R50 re-activation fee applies.
The member accepts full responsibility for their own health and safety whilst using the facilities and equipment.
{gym_name} is not liable for theft, loss, or damage to personal property.
The member agrees to abide by gym rules and etiquette. Disruptive behaviour may result in membership termination without refund.
Membership cards and access tags are non-transferable. Sharing access is prohibited.
This agreement is governed by the laws of the Republic of South Africa and the Consumer Protection Act 68 of 2008."""

# Part 2, Section D: Conduct, Club Rules and Personal Training. {gym_name} is
# substituted at render time — never hardcode a specific gym's name here, this
# same default seeds every tenant's own database.
_DEFAULT_SECTION_D_CLAUSES = [
    "No member may provide personal training, coaching or instruction to anyone on the premises — paid, unpaid, or barter — unless employed/contracted by {gym_name} or holding prior written authorisation from management.",
    "No using the premises, equipment, members or platforms to solicit clients or promote a competing service.",
    "{gym_name} may suspend or terminate immediately, no refund, and recover lost income.",
    "Members may not pursue, solicit or engage in romantic or sexual relationships with any {gym_name} employee, trainer or contractor.",
    "No advances toward, repeated approaches to, or persistent contact with staff — in person, by phone, message or social media. Staff contact details obtained through the Club are for Club business only.",
    "Unwelcome advances, harassment or intimidation of staff or members = serious breach, immediate termination without refund, reportable to SAPS.",
    "Club rules and gym etiquette; disruptive or unsafe behaviour may end the membership without refund.",
]

# Supplied Elev8 contract wording. Kept as a separate part so existing
# configurable terms remain intact when this is merged into a live database.
_ATTACHED_CONTRACT_CLAUSES = [
    "The Member warrants that all information provided in the Membership Application, whether submitted in writing, electronically, or through any other medium, is true, accurate, and complete. The Member acknowledges that {gym_name} will rely on such information for membership administration and service delivery.",
    "The Member confirms that, to the best of their knowledge, they are physically capable of participating in exercise and fitness activities and are not aware of any medical condition that would make participation unsafe without professional medical advice.",
    "The Member acknowledges that it is their responsibility to seek medical advice from a qualified healthcare practitioner before commencing any exercise program if they have any concerns regarding their health, fitness level, medical condition, injury, or physical capability.",
    "The Member agrees to immediately notify {gym_name} of any change in their medical condition, physical health, or circumstances that may affect their ability to safely participate in exercise activities.",
    "The Member agrees to familiarize themselves with and comply with all Club Rules, Policies, Procedures, Safety Requirements, and Operating Hours, as amended from time to time. These rules are available at the Club and may be updated at the sole discretion of {gym_name}.",
    "The Member acknowledges that participation in exercise, fitness training, group classes, and the use of gym equipment involves inherent risks, including the risk of injury, illness, disability, or, in exceptional circumstances, death.",
    "The Member voluntarily accepts and assumes all risks associated with participation in any activity conducted at or through {gym_name}, except where such loss, injury, or damage results directly from the gross negligence or willful misconduct of {gym_name}.",
    "The Member acknowledges that certain provisions of this Agreement limit the liability of {gym_name}, allocate risk, and set out the Member's responsibilities. The Member confirms that they have read, understood, and accepted these provisions before signing this Agreement.",
    "By completing the Membership Application and authorizing payment details, the Member accepts this Agreement. The membership starts immediately or when the Club opens if the Member joins pre-opening and continues until terminated under these terms.",
    "The first Monthly Fee is payable via the chosen payment method on the date specified in the Membership Application. The debit order will be processed on the selected date and remains unchanged unless mutually agreed upon.",
    "{gym_name} may increase fees by 5% on the anniversary of commencement and will notify the Member at least 10 business days in advance. An annual levy of one month's membership fee will be charged every November with at least 30 days' notice.",
    "The Member may cancel by giving {gym_name} no less than twenty (20) business days' written notice. The Club will review the account and confirm cancellation or outstanding amounts within seven (7) days.",
    "Before cancellation is approved, all outstanding membership fees, annual levies, administration fees, and other amounts owing must be paid in full.",
    "If the Member cancels before expiry of the Initial Term, {gym_name} may charge a reasonable cancellation fee in accordance with the Consumer Protection Act, 2008, taking into account the remaining term, benefits received, discounts, and administrative costs.",
    "The Member is entitled to a five (5) business day cooling-off period from signing, subject to payment for services already used. After the Initial Term, the membership continues month-to-month unless cancelled with twenty (20) business days' notice and the account is paid in full.",
    "{gym_name} may terminate on twenty (20) business days' notice for non-payment, breach of this Agreement, failure to comply with Club Rules or Policies, or conduct threatening the safety, wellbeing, or enjoyment of others. The Member remains liable for amounts owing before termination.",
    "{gym_name} may allow suspension for a valid reason such as injury, illness, or extended travel for up to three months. No membership fees are due during suspension, but the suspension does not count toward the Initial Term.",
    "The Member must follow health and safety protocols. Additional charges may apply to personal training and group classes and must be paid before use.",
    "Members must act respectfully and responsibly. Abusive, threatening, offensive, harassing, discriminatory, unsafe, disruptive, or criminal conduct may result in suspension or immediate termination without refund, and may be reported to SAPS.",
    "Alcohol, illegal drugs, smoking, vaping in prohibited areas, vandalism, theft, and deliberate damage to equipment or property are prohibited. Members must comply with Club Rules, Policies, Procedures, and staff instructions.",
    "{gym_name} respects privacy and processes personal information in accordance with POPIA. Information may be collected to administer membership, process payments, assess exercise risks, contact the Member in an emergency, and communicate about membership.",
    "Banking details may be shared with the debit-order processor solely to collect the monthly instalment. Personal information is not sold. ID and banking details are stored securely and access is restricted to staff who need it.",
    "Personal information is retained for the period required for membership administration and legal and financial record-keeping, after which it is securely deleted or anonymised where permitted.",
    "The Member may request access to, correction or deletion of personal information, or object to processing subject to legal retention obligations. Complaints may be lodged with the Information Regulator at enquiries@inforegulator.org.za.",
    "{gym_name} is not liable for loss or damage to personal belongings. The Member should use personal locks on lockers.",
    "{gym_name} may use photographs or videos taken in the gym for promotional purposes. Members who do not consent must notify the Club in writing.",
    "{gym_name} may amend these terms with at least 30 days' notice. Force majeure events beyond the Club's control may excuse performance, including natural disasters, government regulations, or pandemics.",
]


def default_sections_config() -> list:
    """Section/field toggle config for the printed contract. Fields already on
    the current contract default to enabled; fields that aren't captured
    anywhere yet (per README_INTEGRATION.md) default to disabled — tick one
    in the contract template editor and it prints."""
    return [
        {"key": "member", "heading": "Member Details", "enabled": True, "fields": [
            {"key": "full_name", "label": "Full Name", "enabled": True},
            {"key": "id_number", "label": "SA ID Number", "enabled": True},
            {"key": "date_of_birth", "label": "Date of Birth", "enabled": True},
            {"key": "gender", "label": "Gender", "enabled": True},
            {"key": "contact", "label": "Cell Number", "enabled": True},
            {"key": "alternative_number", "label": "Alternative Number", "enabled": False},
            {"key": "email", "label": "Email Address", "enabled": True},
            {"key": "occupation", "label": "Occupation", "enabled": True},
            {"key": "employer", "label": "Employer", "enabled": False},
            {"key": "work_number", "label": "Work Number", "enabled": False},
            {"key": "opt_in", "label": "Marketing Opt-In", "enabled": True},
            {"key": "join_date", "label": "Join Date", "enabled": True},
            {"key": "member_ref", "label": "Member No.", "enabled": False},
        ]},
        {"key": "address", "heading": "Residential Address", "enabled": True, "fields": [
            {"key": "street", "label": "Street", "enabled": True},
            {"key": "suburb", "label": "Suburb", "enabled": True},
            {"key": "city", "label": "City / Town", "enabled": True},
            {"key": "postal_code", "label": "Postal Code", "enabled": True},
            {"key": "country", "label": "Country", "enabled": True},
            {"key": "source", "label": "Source", "enabled": True},
        ]},
        {"key": "membership", "heading": "Membership Details", "enabled": True, "fields": [
            {"key": "tariff", "label": "Tariff", "enabled": True},
            {"key": "contract_duration", "label": "Contract Duration", "enabled": True},
            {"key": "access_level", "label": "Access Level", "enabled": True},
            {"key": "member_status", "label": "Member Status", "enabled": True},
            {"key": "promotion", "label": "Promotion / Code", "enabled": True},
            {"key": "consultant_name", "label": "Consultant", "enabled": True},
            {"key": "joining_fee", "label": "Joining Fee", "enabled": False},
            {"key": "member_addons", "label": "Add-Ons", "enabled": False},
        ]},
        {"key": "payment", "heading": "Payment & Banking", "enabled": True, "fields": [
            {"key": "payment_type", "label": "Payment Method", "enabled": True},
            {"key": "debit_order_date", "label": "Debit Order Date", "enabled": True},
            {"key": "bank", "label": "Bank", "enabled": True},
            {"key": "account_type", "label": "Account Type", "enabled": True},
            {"key": "account_number", "label": "Account Number", "enabled": True},
            {"key": "branch_code", "label": "Branch Code", "enabled": True},
        ]},
        {"key": "emergency", "heading": "Emergency Contact", "enabled": True, "fields": [
            {"key": "emergency_contact_name", "label": "Contact Name", "enabled": True},
            {"key": "emergency_contact_number", "label": "Contact Number", "enabled": True},
            {"key": "emergency_contact_relation", "label": "Relation", "enabled": True},
        ]},
        {"key": "parq", "heading": "PAR-Q Health Screening", "enabled": False, "fields": []},
        {"key": "signatures", "heading": "Signatures", "enabled": True, "fields": [
            {"key": "witness_name", "label": "Witness", "enabled": False},
        ]},
    ]


_POPIA_CLAUSES = [
    "Information we collect: your name and contact details, South African ID "
    "number (or passport number), date of birth, banking details for debit "
    "order collection, emergency contact details, and the health-screening "
    "(PAR-Q) answers you provide before starting an exercise programme.",
    "Why we collect it: to create and administer your membership, verify your "
    "identity, process your monthly instalment by debit order, assess "
    "exercise risk factors for your safety, contact you in an emergency, and "
    "communicate with you about your membership.",
    "Legal basis: processing is necessary to perform this membership "
    "agreement and to comply with our legal and financial record-keeping "
    "obligations. Health-screening information is processed with your "
    "explicit consent.",
    "Who we share it with: your banking details are shared with our debit "
    "order processor solely to collect your monthly instalment, and your "
    "membership details are shared with our membership management system to "
    "administer your access and billing. We do not sell your personal "
    "information.",
    "Security: your ID number and banking details are stored encrypted, and "
    "access to your personal information is restricted to staff who need it "
    "to perform their duties.",
    "Retention: we keep your personal information for as long as you remain "
    "a member, and afterward for as long as our legal and financial "
    "record-keeping obligations require, after which it is securely deleted "
    "or anonymised.",
    "Your rights: you may ask us to confirm what personal information we "
    "hold about you, request a copy of it, ask us to correct or delete it, "
    "and object to how it is processed, subject to our legal retention "
    "obligations. To exercise these rights, contact our Information Officer "
    "at [INFORMATION OFFICER NAME / EMAIL — insert before publishing].",
    "Complaints: if you believe we have not handled your personal "
    "information properly, you may lodge a complaint with the Information "
    "Regulator (South Africa) at enquiries@inforegulator.org.za.",
]


def default_terms_parts_config() -> list:
    """The 3 Parts of terms. Part 1 is this app's real, currently-live
    membership terms (just restructured); Part 2 adds the new Section D
    conduct clauses onto the existing club-rules clause; Part 3 is a
    first-draft POPIA privacy statement grounded in what this app actually
    collects (members, debicheck_mandates) — it still needs sign-off from
    the gym or a compliance advisor before publishing, and the Information
    Officer contact detail is a placeholder no draft can supply."""
    part1_clauses = [
        line.strip().replace("Elev8 Health & Fitness", "{gym_name}")
        for line in _DEFAULT_CONTRACT_TERMS.splitlines() if line.strip()
    ]
    return [
        {
            "title": "Part 1 — General Terms",
            "intro": "",
            "clauses": part1_clauses,
            "page_break": False,
            "initials_required": True,
        },
        {
            "title": "Part 2 — Conduct, Club Rules and Personal Training",
            "intro": "Section D: Conduct, Club Rules and Personal Training",
            "clauses": list(_DEFAULT_SECTION_D_CLAUSES),
            "page_break": True,
            "initials_required": True,
        },
        {
            "title": "Part 3 — Privacy Statement",
            "intro": (
                "{gym_name} (\"the Club\") processes members' personal "
                "information in accordance with the Protection of Personal "
                "Information Act 4 of 2013 (POPIA). This statement explains "
                "what we collect, why, and your rights."
            ),
            "clauses": list(_POPIA_CLAUSES),
            "page_break": True,
            "initials_required": False,
        },
        {
            "title": "Attached Contract Terms — Parts 1–3",
            "intro": "The following terms are preserved from the signed Elev8 Health and Fitness membership contract supplied for this system merge.",
            "clauses": list(_ATTACHED_CONTRACT_CLAUSES),
            "page_break": True,
            "initials_required": True,
        },
    ]


def default_debit_mandate_text() -> str:
    return (
        "The Member authorises {gym_name} to issue and deliver payment instructions "
        "to the nominated bank account, provided each instruction does not exceed "
        "the Member's obligation under this Agreement. Payment is due on the "
        "{debit_day} day of each month, commencing {debit_start}. If the payment day "
        "falls on a Saturday, Sunday, or recognised South African public holiday, "
        "the debit order will be processed on the preceding business day. If funds "
        "are insufficient, {gym_name} may track the account and re-present the debit "
        "instruction when sufficient funds become available until the outstanding "
        "obligation is settled. The Member remains responsible for ensuring funds "
        "are available. This authority may be cancelled by written notice of no less "
        "than 20 ordinary working days, but cancellation does not cancel the Agreement."
    )


def _seed_contract_template(db) -> None:
    db.execute(
        """INSERT OR IGNORE INTO contract_templates
           (name, gym_name, agreement_title, terms_text, footer_text,
            sections_config, terms_parts_config, debit_mandate_text, active, is_default)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1)""",
        (
            "Standard Membership Agreement",
            "Elev8 Health & Fitness",
            "Membership Agreement",
            _DEFAULT_CONTRACT_TERMS,
            "Please retain a copy of this agreement for your records.",
            json.dumps(default_sections_config()),
            json.dumps(default_terms_parts_config()),
            default_debit_mandate_text(),
        ),
    )
    db.commit()


def _backfill_contract_config(db) -> None:
    """Populate sections_config / terms_parts_config / debit_mandate_text on
    contract_templates rows created before those columns existed."""
    rows = db.execute(
        """SELECT id FROM contract_templates
           WHERE sections_config IS NULL OR sections_config = ''
              OR terms_parts_config IS NULL OR terms_parts_config = ''
              OR debit_mandate_text IS NULL OR debit_mandate_text = ''"""
    ).fetchall()
    sections_json = json.dumps(default_sections_config())
    terms_json = json.dumps(default_terms_parts_config())
    mandate_text = default_debit_mandate_text()
    for row in rows:
        db.execute(
            """UPDATE contract_templates SET
                 sections_config = COALESCE(NULLIF(sections_config, ''), ?),
                 terms_parts_config = COALESCE(NULLIF(terms_parts_config, ''), ?),
                 debit_mandate_text = COALESCE(NULLIF(debit_mandate_text, ''), ?)
               WHERE id = ?""",
            (sections_json, terms_json, mandate_text, row["id"]),
        )
    db.commit()

    # Existing templates may already have structured terms. Add the supplied
    # contract as a new part without replacing any administrator-authored text.
    templates = db.execute(
        "SELECT id, terms_parts_config FROM contract_templates"
    ).fetchall()
    attached_title = "Attached Contract Terms — Parts 1–3"
    for template in templates:
        try:
            parts = json.loads(template["terms_parts_config"] or "[]")
        except (TypeError, ValueError):
            parts = []
        if any(part.get("title") == attached_title for part in parts):
            continue
        parts.append({
            "title": attached_title,
            "intro": "The following terms are preserved from the signed Elev8 Health and Fitness membership contract supplied for this system merge.",
            "clauses": list(_ATTACHED_CONTRACT_CLAUSES),
            "page_break": True,
            "initials_required": True,
        })
        db.execute(
            "UPDATE contract_templates SET terms_parts_config=?, updated_at=datetime('now') WHERE id=?",
            (json.dumps(parts), template["id"]),
        )
    db.commit()


# ── Application backfill ──────────────────────────────────────────────────────

def _backfill_member_applications(db) -> None:
    """Create missing application workflows for members added outside lead conversion."""
    db.execute(
        """INSERT INTO membership_applications
           (member_id, package, application_status, created_by)
           SELECT m.id, COALESCE(NULLIF(m.package, ''), m.tariff),
                  'awaiting_docs', m.uploaded_by_id
           FROM members m
           WHERE NOT EXISTS (
               SELECT 1 FROM membership_applications ma WHERE ma.member_id = m.id
           )"""
    )
    db.execute(
        """INSERT OR IGNORE INTO compliance_checklists (application_id)
           SELECT id FROM membership_applications"""
    )
    db.execute(
        """UPDATE compliance_checklists
           SET bank_statement_status = CASE
                   WHEN EXISTS (
                       SELECT 1 FROM membership_applications ma
                       JOIN member_documents d ON d.member_id = ma.member_id
                       WHERE ma.id = compliance_checklists.application_id
                         AND d.document_type = 'Bank Statement'
                         AND d.analysis_json IS NOT NULL
                   ) THEN 'analysed'
                   WHEN EXISTS (
                       SELECT 1 FROM membership_applications ma
                       JOIN member_documents d ON d.member_id = ma.member_id
                       WHERE ma.id = compliance_checklists.application_id
                         AND d.document_type = 'Bank Statement'
                   ) THEN 'uploaded'
                   ELSE bank_statement_status END,
               id_copy_status = CASE WHEN EXISTS (
                   SELECT 1 FROM membership_applications ma
                   JOIN member_documents d ON d.member_id = ma.member_id
                   WHERE ma.id = compliance_checklists.application_id
                     AND d.document_type = 'SA ID Copy'
               ) THEN 'uploaded' ELSE id_copy_status END,
               contract_status = CASE WHEN EXISTS (
                   SELECT 1 FROM membership_applications ma
                   JOIN member_documents d ON d.member_id = ma.member_id
                   WHERE ma.id = compliance_checklists.application_id
                     AND d.document_type = 'Signed Contract'
               ) THEN 'signed' ELSE contract_status END"""
    )
    db.execute(
        """UPDATE compliance_checklists
           SET pos_status = CASE WHEN EXISTS (
               SELECT 1 FROM membership_applications ma
               JOIN collections c ON c.member_id = ma.member_id
               WHERE ma.id = compliance_checklists.application_id
                 AND c.status = 'paid' AND c.amount_paid > 0
           ) THEN 'paid' ELSE pos_status END"""
    )
    db.execute(
        """UPDATE membership_applications
           SET application_status = CASE
               WHEN application_status IN ('pushed', 'active') THEN application_status
               WHEN (SELECT manager_approval_status FROM compliance_checklists
                     WHERE application_id = membership_applications.id) = 'required'
                   THEN 'manager_approval'
               WHEN EXISTS (
                   SELECT 1 FROM compliance_checklists cc
                   WHERE cc.application_id = membership_applications.id
                     AND (cc.contract_status != 'pending'
                          OR cc.debit_check_status != 'pending'
                          OR cc.bank_statement_status != 'not_uploaded'
                          OR cc.id_copy_status != 'not_uploaded'
                          OR cc.pos_status != 'pending')
                   ) THEN 'verification'
               ELSE 'awaiting_docs' END,
               updated_at = datetime('now')"""
    )
    db.commit()


# ── Audit & activity helpers ──────────────────────────────────────────────────

def audit_log(db, action: str, lead_id=None, application_id=None,
              old_value=None, new_value=None, user_id=None) -> None:
    if user_id is None:
        from flask import has_request_context, session
        if has_request_context():
            user_id = session.get("user_id")
    db.execute(
        """INSERT INTO audit_logs (application_id, lead_id, user_id, action, old_value, new_value)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (application_id, lead_id, user_id, action, old_value, new_value),
    )
    if application_id is not None:
        application = db.execute(
            "SELECT member_id FROM membership_applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        if application and application["member_id"]:
            description = action.replace("_", " ").capitalize()
            if new_value not in (None, ""):
                description += f": {new_value}"
            member_activity(db, application["member_id"], description, user_id=user_id)


def member_activity(db, member_id: int, note: str, user_id=None) -> None:
    """Append a timestamped activity entry to a member's profile."""
    if user_id is None:
        from flask import has_request_context, session
        if has_request_context():
            user_id = session.get("user_id")
    db.execute(
        """INSERT INTO member_notes (member_id, note, created_by, created_at)
           VALUES (?, ?, ?, datetime('now', 'localtime'))""",
        (member_id, note, user_id),
    )


def query_scalar(query: str, params: tuple = ()):
    db = get_db()
    cursor = db.execute(query, params)
    value = cursor.fetchone()
    return value[0] if value is not None else None
