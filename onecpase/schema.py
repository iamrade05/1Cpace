"""
Database schema for 1Cpase.

Two variants are provided:
  SCHEMA_SQL      — SQLite (development default)
  SCHEMA_SQL_PG   — PostgreSQL (production)

Use get_schema() to obtain the right one based on DB_TYPE.
"""

# ── SQLite schema ─────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'staff',
    department TEXT DEFAULT 'General',
    contact TEXT,
    email TEXT,
    active INTEGER DEFAULT 1,
    must_change_password INTEGER DEFAULT 0,
    is_platform_user INTEGER DEFAULT 0,
    platform_admin_id INTEGER,
    is_sales_consultant INTEGER DEFAULT 0,
    sales_available INTEGER DEFAULT 0,
    sales_rotation_order INTEGER,
    sales_disqualification_category INTEGER DEFAULT 0,
    sales_skip_remaining INTEGER DEFAULT 0,
    is_collections_caller INTEGER DEFAULT 0,
    last_lead_assigned_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS user_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    permission TEXT NOT NULL,
    UNIQUE(user_id, permission),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_ref TEXT UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    id_number TEXT NOT NULL UNIQUE,
    id_number_hash TEXT UNIQUE,
    contact TEXT,
    email TEXT,
    date_of_birth TEXT,
    gender TEXT,
    join_date TEXT,
    member_status TEXT DEFAULT 'Active',
    package TEXT,
    tariff TEXT,
    contract_duration TEXT,
    monthly_installment REAL DEFAULT 0,
    source TEXT DEFAULT 'Walk-in',
    entry_type TEXT DEFAULT 'manual',
    outcome TEXT DEFAULT 'joined',
    street_number TEXT,
    street_name TEXT,
    suburb TEXT,
    city TEXT,
    postal_code TEXT,
    country TEXT DEFAULT 'South Africa',
    payment_type TEXT DEFAULT 'Debit Order',
    debit_order_date TEXT,
    bank TEXT,
    account_type TEXT,
    account_name TEXT,
    account_number TEXT,
    branch_code TEXT,
    payer_name TEXT,
    payer_id_number TEXT,
    opt_in INTEGER DEFAULT 1,
    fitness_goal TEXT,
    training_experience INTEGER DEFAULT 0,
    uploaded_by_id INTEGER,
    itensity_ref TEXT,
    access_credential TEXT,
    promotion TEXT,
    access_level TEXT DEFAULT 'Local',
    occupation TEXT,
    employer TEXT,
    alternative_number TEXT,
    work_number TEXT,
    joining_fee REAL DEFAULT 0,
    member_addons TEXT,
    witness_name TEXT,
    emergency_contact_name TEXT,
    emergency_contact_number TEXT,
    emergency_contact_relation TEXT,
    parq_q1 INTEGER DEFAULT 0,
    parq_q2 INTEGER DEFAULT 0,
    parq_q3 INTEGER DEFAULT 0,
    parq_q4 INTEGER DEFAULT 0,
    parq_q5 INTEGER DEFAULT 0,
    parq_q6 INTEGER DEFAULT 0,
    parq_q7 INTEGER DEFAULT 0,
    parq_q8 INTEGER DEFAULT 0,
    parq_q9 INTEGER DEFAULT 0,
    gym_access_status TEXT DEFAULT 'allowed',
    access_blocked_until TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (uploaded_by_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    outstanding_balance REAL DEFAULT 0,
    discount_pct REAL DEFAULT 0,
    amount_paid REAL DEFAULT 0,
    collection_date TEXT DEFAULT (date('now')),
    method TEXT DEFAULT 'cash',
    notes TEXT,
    status TEXT DEFAULT 'pending',
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS collection_call_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_date TEXT NOT NULL,
    member_id INTEGER NOT NULL,
    collection_id INTEGER,
    queue_type TEXT NOT NULL,
    cycle_date TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    assigned_to INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    outcome TEXT,
    successful_contact INTEGER DEFAULT 0,
    next_channel TEXT DEFAULT 'CALL',
    next_action_at TEXT,
    notes TEXT,
    called_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(task_date, member_id),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (collection_id) REFERENCES collections(id),
    FOREIGN KEY (assigned_to) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_collection_call_tasks_daily
ON collection_call_tasks(task_date, assigned_to, status);

CREATE INDEX IF NOT EXISTS idx_collection_call_tasks_cycle
ON collection_call_tasks(cycle_date, member_id, successful_contact);

CREATE TABLE IF NOT EXISTS leave_requests (
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
);

CREATE TABLE IF NOT EXISTS fitness_classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    instructor_id INTEGER,
    schedule_day TEXT,
    schedule_time TEXT,
    capacity INTEGER DEFAULT 20,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (instructor_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS class_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    booking_date TEXT DEFAULT (date('now')),
    status TEXT DEFAULT 'booked',
    FOREIGN KEY (class_id) REFERENCES fitness_classes(id),
    FOREIGN KEY (member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    equipment_type TEXT DEFAULT 'general',
    serial_number TEXT,
    purchase_date TEXT,
    condition TEXT DEFAULT 'good',
    location TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS maintenance_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL,
    maintenance_date TEXT DEFAULT (date('now')),
    description TEXT NOT NULL,
    performed_by TEXT,
    cost REAL DEFAULT 0,
    next_due_date TEXT,
    notes TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (equipment_id) REFERENCES equipment(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    phone TEXT,
    phone_normalized TEXT,
    email TEXT,
    source TEXT DEFAULT 'Walk-In / Enquiry',
    original_source TEXT,
    referrer TEXT,
    campaign TEXT,
    assigned_to INTEGER,
    lead_status TEXT DEFAULT 'captured',
    interested_package TEXT,
    appointment_date TEXT,
    trial_start_date TEXT,
    trial_end_date TEXT,
    decision_start_date TEXT,
    decision_date TEXT,
    next_action TEXT,
    next_action_at TEXT,
    marketing_consent INTEGER DEFAULT 1,
    do_not_contact INTEGER DEFAULT 0,
    closure_reason TEXT,
    recycle_due_at TEXT,
    objection_reason TEXT,
    duplicate_of_lead_id INTEGER,
    converted_member_id INTEGER,
    last_activity_at TEXT,
    lead_channel TEXT DEFAULT 'in_reach',
    entry_path TEXT DEFAULT 'standard',
    outreach_needs_appointment INTEGER,
    training_experience TEXT,
    training_preference TEXT,
    joining_preference TEXT,
    notes TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (assigned_to) REFERENCES users(id),
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (duplicate_of_lead_id) REFERENCES leads(id),
    FOREIGN KEY (converted_member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS lead_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    activity_type TEXT NOT NULL,
    outcome TEXT,
    status TEXT DEFAULT 'completed',
    notes TEXT,
    occurred_at TEXT DEFAULT (datetime('now')),
    scheduled_for TEXT,
    completed_at TEXT,
    next_action TEXT,
    follow_up_at TEXT,
    reason TEXT,
    offer_amount REAL,
    discount_percent REAL DEFAULT 0,
    manager_approval_status TEXT DEFAULT 'not_required',
    manager_approved_by INTEGER,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE,
    FOREIGN KEY (manager_approved_by) REFERENCES users(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_leads_status_assigned ON leads(lead_status, assigned_to);
CREATE INDEX IF NOT EXISTS idx_lead_activities_lead_date ON lead_activities(lead_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_lead_activities_followup ON lead_activities(status, scheduled_for);

CREATE TABLE IF NOT EXISTS membership_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER,
    member_id INTEGER,
    package TEXT,
    application_status TEXT DEFAULT 'draft',
    date_joined TEXT DEFAULT (date('now')),
    push_status TEXT DEFAULT 'not_pushed',
    pushed_at TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (lead_id) REFERENCES leads(id),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS debicheck_mandates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    merchant_id TEXT NOT NULL,
    auth_type TEXT DEFAULT 'cell',
    client_ref1 TEXT NOT NULL,
    client_ref2 TEXT NOT NULL,
    account_name TEXT NOT NULL,
    account_type TEXT NOT NULL,
    account_number TEXT NOT NULL,
    branch_code TEXT NOT NULL,
    frequency TEXT DEFAULT '3',
    no_installments INTEGER DEFAULT 1,
    tracking TEXT DEFAULT '14',
    submit_date TEXT NOT NULL,
    id_type TEXT DEFAULT '0',
    id_number TEXT,
    passport TEXT,
    juristic_account TEXT,
    instalment_rands INTEGER DEFAULT 0,
    instalment_cents INTEGER DEFAULT 0,
    instalment_amount REAL DEFAULT 0,
    payer_type TEXT DEFAULT 'self',
    status TEXT DEFAULT 'pending',
    submitted_at TEXT,
    submitted_by INTEGER,
    nupay_response TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (submitted_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS compliance_checklists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL UNIQUE,
    contract_status TEXT DEFAULT 'pending',
    debit_check_status TEXT DEFAULT 'pending',
    bank_statement_status TEXT DEFAULT 'not_uploaded',
    id_copy_status TEXT DEFAULT 'not_uploaded',
    pos_status TEXT DEFAULT 'pending',
    age_category TEXT DEFAULT 'above_23',
    guardian_required TEXT DEFAULT 'not_required',
    guardian_status TEXT DEFAULT 'not_required',
    manager_approval_status TEXT DEFAULT 'not_required',
    overall_status TEXT DEFAULT 'incomplete',
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (application_id) REFERENCES membership_applications(id)
);

CREATE TABLE IF NOT EXISTS statement_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    income_detected TEXT DEFAULT 'unclear',
    estimated_monthly_income REAL DEFAULT 0,
    max_balance_m1 REAL DEFAULT 0,
    max_balance_m2 REAL DEFAULT 0,
    max_balance_m3 REAL DEFAULT 0,
    average_balance REAL DEFAULT 0,
    returned_debits_count INTEGER DEFAULT 0,
    negative_balance_days INTEGER DEFAULT 0,
    income_frequency TEXT DEFAULT 'irregular',
    income_months INTEGER DEFAULT 0,
    verification_qualified INTEGER DEFAULT 0,
    risk_rating TEXT DEFAULT 'medium',
    analysis_summary TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (application_id) REFERENCES membership_applications(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    approval_type TEXT NOT NULL DEFAULT 'manager',
    requested_by INTEGER,
    approved_by INTEGER,
    status TEXT DEFAULT 'pending',
    reason TEXT,
    manager_comment TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    approved_at TEXT,
    FOREIGN KEY (application_id) REFERENCES membership_applications(id),
    FOREIGN KEY (requested_by) REFERENCES users(id),
    FOREIGN KEY (approved_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS tariffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    duration_months INTEGER NOT NULL DEFAULT 12,
    monthly_amount REAL NOT NULL DEFAULT 0,
    active INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 99,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contract_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    gym_name TEXT NOT NULL DEFAULT 'Elev8 Health & Fitness',
    agreement_title TEXT NOT NULL DEFAULT 'Membership Agreement',
    terms_text TEXT NOT NULL,
    footer_text TEXT,
    reg_no TEXT,
    address TEXT,
    accent_color TEXT DEFAULT '#185FA5',
    sections_config TEXT,
    terms_parts_config TEXT,
    debit_mandate_text TEXT,
    active INTEGER DEFAULT 1,
    is_default INTEGER DEFAULT 0,
    created_by INTEGER,
    updated_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (updated_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS contract_share_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    sent_via TEXT,
    viewed_at TEXT,
    responded_at TEXT,
    response_note TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_contract_share_token_hash ON contract_share_tokens(token_hash);

CREATE TABLE IF NOT EXISTS report_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    table_name TEXT NOT NULL DEFAULT 'members',
    config_json TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    created_by INTEGER,
    updated_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (updated_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS member_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    document_type TEXT DEFAULT 'Other',
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    file_size INTEGER DEFAULT 0,
    analysis_json TEXT,
    uploaded_by INTEGER,
    uploaded_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS itensity_live_snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active_count INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    unverified_count INTEGER NOT NULL DEFAULT 0,
    inactive_count INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    source TEXT NOT NULL DEFAULT 'Itensity dashboard'
);

CREATE TABLE IF NOT EXISTS member_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER,
    lead_id INTEGER,
    user_id INTEGER,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    timestamp TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (application_id) REFERENCES membership_applications(id),
    FOREIGN KEY (lead_id) REFERENCES leads(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS turnstile_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_serial TEXT,
    raw_report_hex TEXT NOT NULL,
    credential TEXT,
    member_id INTEGER,
    decision TEXT DEFAULT 'unmatched',
    reason TEXT,
    direction TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id)
);

CREATE INDEX IF NOT EXISTS idx_turnstile_events_created ON turnstile_events(created_at);
CREATE INDEX IF NOT EXISTS idx_turnstile_events_credential ON turnstile_events(credential);

CREATE TABLE IF NOT EXISTS ptp_agreements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    arrears_amount REAL DEFAULT 0,
    promise_amount REAL DEFAULT 0,
    promise_date TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    arrangement_type TEXT NOT NULL DEFAULT 'full',
    ptp_status TEXT DEFAULT 'pending',
    access_unblock TEXT DEFAULT 'no',
    unblock_until TEXT,
    auto_block_if_failed INTEGER DEFAULT 1,
    notes TEXT,
    manager_approval_status TEXT DEFAULT 'not_required',
    approved_by INTEGER,
    approved_date TEXT,
    approval_notes TEXT,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (approved_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS payment_plan_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ptp_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    instalment_number INTEGER NOT NULL,
    amount REAL NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    paid_at TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (ptp_id) REFERENCES ptp_agreements(id) ON DELETE CASCADE,
    FOREIGN KEY (member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS contact_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    consultant_id INTEGER,
    contact_method TEXT DEFAULT 'call',
    call_code INTEGER,
    notes TEXT,
    next_followup_date TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (consultant_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS ptp_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ptp_id INTEGER,
    member_id INTEGER NOT NULL,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    amount_paid REAL NOT NULL,
    payment_date TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    reference TEXT,
    uploaded_by INTEGER,
    uploaded_at TEXT DEFAULT (datetime('now', 'localtime')),
    verified_by INTEGER,
    verified_at TEXT,
    verification_status TEXT DEFAULT 'pending',
    FOREIGN KEY (ptp_id) REFERENCES ptp_agreements(id),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (uploaded_by) REFERENCES users(id),
    FOREIGN KEY (verified_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS legal_referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL UNIQUE,
    referred_by INTEGER,
    referred_at TEXT DEFAULT (datetime('now', 'localtime')),
    status TEXT DEFAULT 'open',
    arrears_months INTEGER DEFAULT 0,
    arrears_amount REAL DEFAULT 0,
    reason TEXT,
    notes TEXT,
    resolved_by INTEGER,
    resolved_at TEXT,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (referred_by) REFERENCES users(id),
    FOREIGN KEY (resolved_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT UNIQUE,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    logged_by INTEGER,
    member_id INTEGER,
    member_name TEXT,
    contact TEXT,
    category TEXT NOT NULL,
    query_type TEXT,
    description TEXT NOT NULL,
    priority TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'open',
    assigned_to INTEGER,
    department TEXT,
    due_date TEXT,
    action_taken TEXT,
    follow_up_date TEXT,
    resolution_notes TEXT,
    closed_by INTEGER,
    closed_at TEXT,
    ptp_id INTEGER,
    outstanding_snapshot REAL DEFAULT 0,
    arrears_months_snapshot INTEGER DEFAULT 0,
    discount_percent_snapshot REAL DEFAULT 0,
    discount_amount_snapshot REAL DEFAULT 0,
    settlement_amount_snapshot REAL DEFAULT 0,
    recommended_action_snapshot TEXT,
    access_decision_snapshot TEXT,
    manager_approval_required INTEGER DEFAULT 0,
    manager_approved_by INTEGER,
    manager_approved_at TEXT,
    cancellation_contract_months INTEGER,
    cancellation_months_completed INTEGER,
    cancellation_monthly_fee REAL DEFAULT 0,
    cancellation_fee REAL DEFAULT 0,
    cancellation_penalty_months INTEGER DEFAULT 0,
    cancellation_payment_method TEXT,
    cancellation_outstanding REAL DEFAULT 0,
    cancellation_settlement_amount REAL DEFAULT 0,
    cancellation_settlement_rule TEXT,
    cancellation_discount_pct REAL DEFAULT 0,
    member_cancelled INTEGER DEFAULT 0,
    FOREIGN KEY (logged_by) REFERENCES users(id),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE SET NULL,
    FOREIGN KEY (assigned_to) REFERENCES users(id),
    FOREIGN KEY (closed_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS query_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    created_by INTEGER,
    note TEXT NOT NULL,
    action_type TEXT DEFAULT 'note',
    FOREIGN KEY (query_id) REFERENCES queries(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

-- ── Fitness: assessments, workout plans, dailies, incidents, body matrix, nutrition, supplements ──

CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    assessed_by_id INTEGER,
    date TEXT DEFAULT (date('now')),
    weight_kg REAL,
    body_fat_pct REAL,
    muscle_mass_kg REAL,
    bmi REAL,
    fitness_level TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (assessed_by_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS member_onboarding (
    id INTEGER PRIMARY KEY AUTOINCREMENT, member_id INTEGER NOT NULL UNIQUE,
    status TEXT DEFAULT 'eligible', eligible_from TEXT, first_visit_date TEXT,
    last_visit_date TEXT, visits_recorded INTEGER DEFAULT 0, closed_at TEXT,
    close_reason TEXT, review_done INTEGER DEFAULT 0, review_by_id INTEGER,
    review_date TEXT, review_outcome TEXT, review_notes TEXT,
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (review_by_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_member_onboarding_status ON member_onboarding(status);
CREATE TABLE IF NOT EXISTS onboarding_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT, onboarding_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL, visit_no INTEGER NOT NULL, visit_date TEXT NOT NULL,
    checked_in_at TEXT, arrival_source TEXT DEFAULT 'turnstile', instructor_id INTEGER,
    actions TEXT, outcome TEXT, notes TEXT, recorded_at TEXT,
    created_at TEXT DEFAULT (datetime('now')), UNIQUE (member_id, visit_date),
    FOREIGN KEY (onboarding_id) REFERENCES member_onboarding(id) ON DELETE CASCADE,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (instructor_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_onboarding_visits_date ON onboarding_visits(visit_date);
CREATE TABLE IF NOT EXISTS onboarding_sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), last_event_id INTEGER DEFAULT 0, last_run_at TEXT
);
INSERT OR IGNORE INTO onboarding_sync_state (id, last_event_id) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS workout_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    trainer_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (trainer_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS instructor_dailies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instructor_id INTEGER,
    member_id INTEGER,
    walk_in_name TEXT,
    client_type TEXT DEFAULT 'existing',
    service_type TEXT DEFAULT 'exercise_assistance',
    session_type TEXT DEFAULT 'full',
    date TEXT DEFAULT (date('now')),
    time_in TEXT,
    time_out TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (instructor_id) REFERENCES users(id),
    FOREIGN KEY (member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER,
    walk_in_name TEXT,
    walk_in_contact TEXT,
    incident_type TEXT NOT NULL DEFAULT 'other',
    description TEXT,
    severity TEXT DEFAULT 'medium',
    logged_by_id INTEGER,
    date TEXT DEFAULT (date('now')),
    time TEXT,
    status TEXT DEFAULT 'open',
    whatsapp_sent INTEGER DEFAULT 0,
    resolution_notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (logged_by_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS body_matrix (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    recorded_by_id INTEGER,
    date TEXT DEFAULT (date('now')),
    week_number INTEGER,
    month TEXT,
    weight_kg REAL,
    body_fat_pct REAL,
    muscle_mass_kg REAL,
    bmi REAL,
    chest_cm REAL,
    waist_cm REAL,
    hips_cm REAL,
    arms_cm REAL,
    thighs_cm REAL,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (recorded_by_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS nutrition_foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'macro',
    macro_type TEXT,
    micro_type TEXT,
    serving_size TEXT,
    calories REAL DEFAULT 0,
    protein_g REAL DEFAULT 0,
    carbs_g REAL DEFAULT 0,
    fiber_g REAL DEFAULT 0,
    fat_g REAL DEFAULT 0,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS supplements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    brand TEXT,
    supp_type TEXT NOT NULL,
    goal TEXT NOT NULL,
    risk_level INTEGER DEFAULT 1,
    description TEXT,
    dosage TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS member_supplements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    supplement_id INTEGER NOT NULL,
    assigned_by TEXT,
    start_date TEXT DEFAULT (date('now')),
    end_date TEXT,
    dosage TEXT,
    goal TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (supplement_id) REFERENCES supplements(id)
);

-- ── HR: staff register, attendance, payroll, timesheets ──────────────────────

CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    id_number TEXT,
    role TEXT,
    department TEXT DEFAULT 'General',
    contact TEXT,
    email TEXT,
    start_date TEXT,
    contract_end TEXT,
    salary REAL DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL,
    date TEXT DEFAULT (date('now')),
    clock_in TEXT,
    clock_out TEXT,
    status TEXT DEFAULT 'present',
    notes TEXT,
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payroll (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    basic REAL DEFAULT 0,
    bonus REAL DEFAULT 0,
    deductions REAL DEFAULT 0,
    net REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    paid_on TEXT,
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS timesheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    time_in TEXT,
    lunch_out TEXT,
    lunch_in TEXT,
    time_out TEXT,
    hours_worked REAL DEFAULT 0,
    notes TEXT,
    captured_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS call_list_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    uploaded_by INTEGER,
    row_count INTEGER NOT NULL DEFAULT 0,
    imported_count INTEGER NOT NULL DEFAULT 0,
    parser_message TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    imported_at TEXT,
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS call_list_upload_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL,
    row_number INTEGER NOT NULL,
    client_name TEXT,
    contact TEXT,
    raw_response TEXT,
    mapped_outcome TEXT NOT NULL DEFAULT 'other',
    appointment_date TEXT,
    appointment_time TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    imported_lead_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (upload_id) REFERENCES call_list_uploads(id) ON DELETE CASCADE,
    FOREIGN KEY (imported_lead_id) REFERENCES leads(id)
);

CREATE INDEX IF NOT EXISTS idx_call_list_upload_hash ON call_list_uploads(file_sha256);
CREATE INDEX IF NOT EXISTS idx_call_list_rows_upload ON call_list_upload_rows(upload_id, row_number);

CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    event_key TEXT NOT NULL,
    event_type TEXT,
    payload_hash TEXT,
    received_at TEXT DEFAULT (datetime('now')),
    processed_at TEXT,
    status TEXT NOT NULL DEFAULT 'processing',
    error TEXT,
    UNIQUE(provider, event_key)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON webhook_events(provider, status);

CREATE TABLE IF NOT EXISTS wa_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_number TEXT NOT NULL,
    contact_phone TEXT NOT NULL,
    contact_name TEXT,
    last_message_at TEXT,
    unread_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'open',
    linked_lead_id INTEGER,
    linked_member_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(wa_number, contact_phone),
    FOREIGN KEY (linked_lead_id) REFERENCES leads(id),
    FOREIGN KEY (linked_member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS wa_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    wa_message_id TEXT UNIQUE,
    direction TEXT NOT NULL,
    message_type TEXT DEFAULT 'text',
    content TEXT,
    media_url TEXT,
    status TEXT DEFAULT 'received',
    sent_by_user_id INTEGER,
    timestamp TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (conversation_id) REFERENCES wa_conversations(id),
    FOREIGN KEY (sent_by_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS fb_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id TEXT,
    sender_psid TEXT NOT NULL UNIQUE,
    sender_name TEXT,
    last_message_at TEXT,
    unread_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'open',
    linked_lead_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (linked_lead_id) REFERENCES leads(id)
);

CREATE TABLE IF NOT EXISTS fb_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    fb_message_id TEXT UNIQUE,
    direction TEXT NOT NULL,
    content TEXT,
    attachment_type TEXT,
    attachment_url TEXT,
    sent_by_user_id INTEGER,
    timestamp TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (conversation_id) REFERENCES fb_conversations(id),
    FOREIGN KEY (sent_by_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS fb_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fb_comment_id TEXT UNIQUE,
    post_id TEXT,
    post_message TEXT,
    commenter_name TEXT,
    commenter_id TEXT,
    content TEXT,
    is_interested INTEGER DEFAULT 0,
    is_replied INTEGER DEFAULT 0,
    linked_lead_id INTEGER,
    timestamp TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (linked_lead_id) REFERENCES leads(id)
);
"""


# ── PostgreSQL schema ─────────────────────────────────────────────────────────

SCHEMA_SQL_PG = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'staff',
    department TEXT DEFAULT 'General',
    contact TEXT,
    email TEXT,
    active INTEGER DEFAULT 1,
    must_change_password INTEGER DEFAULT 0,
    is_platform_user INTEGER DEFAULT 0,
    platform_admin_id INTEGER,
    is_sales_consultant INTEGER DEFAULT 0,
    sales_available INTEGER DEFAULT 0,
    sales_rotation_order INTEGER,
    sales_disqualification_category INTEGER DEFAULT 0,
    sales_skip_remaining INTEGER DEFAULT 0,
    is_collections_caller INTEGER DEFAULT 0,
    last_lead_assigned_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS user_permissions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    permission TEXT NOT NULL,
    UNIQUE(user_id, permission),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS members (
    id SERIAL PRIMARY KEY,
    member_ref TEXT UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    id_number TEXT NOT NULL,
    id_number_hash TEXT UNIQUE,
    contact TEXT,
    email TEXT,
    date_of_birth TEXT,
    gender TEXT,
    join_date TEXT,
    member_status TEXT DEFAULT 'Active',
    package TEXT,
    tariff TEXT,
    contract_duration TEXT,
    monthly_installment DOUBLE PRECISION DEFAULT 0,
    source TEXT DEFAULT 'Walk-in',
    entry_type TEXT DEFAULT 'manual',
    outcome TEXT DEFAULT 'joined',
    street_number TEXT,
    street_name TEXT,
    suburb TEXT,
    city TEXT,
    postal_code TEXT,
    country TEXT DEFAULT 'South Africa',
    payment_type TEXT DEFAULT 'Debit Order',
    debit_order_date TEXT,
    bank TEXT,
    account_type TEXT,
    account_name TEXT,
    account_number TEXT,
    branch_code TEXT,
    payer_name TEXT,
    payer_id_number TEXT,
    opt_in INTEGER DEFAULT 1,
    fitness_goal TEXT,
    training_experience INTEGER DEFAULT 0,
    uploaded_by_id INTEGER,
    itensity_ref TEXT,
    access_credential TEXT,
    promotion TEXT,
    access_level TEXT DEFAULT 'Local',
    occupation TEXT,
    employer TEXT,
    alternative_number TEXT,
    work_number TEXT,
    joining_fee REAL DEFAULT 0,
    member_addons TEXT,
    witness_name TEXT,
    emergency_contact_name TEXT,
    emergency_contact_number TEXT,
    emergency_contact_relation TEXT,
    parq_q1 INTEGER DEFAULT 0,
    parq_q2 INTEGER DEFAULT 0,
    parq_q3 INTEGER DEFAULT 0,
    parq_q4 INTEGER DEFAULT 0,
    parq_q5 INTEGER DEFAULT 0,
    parq_q6 INTEGER DEFAULT 0,
    parq_q7 INTEGER DEFAULT 0,
    parq_q8 INTEGER DEFAULT 0,
    parq_q9 INTEGER DEFAULT 0,
    gym_access_status TEXT DEFAULT 'allowed',
    access_blocked_until TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (uploaded_by_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS collections (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    outstanding_balance DOUBLE PRECISION DEFAULT 0,
    discount_pct DOUBLE PRECISION DEFAULT 0,
    amount_paid DOUBLE PRECISION DEFAULT 0,
    collection_date TEXT DEFAULT (CURRENT_DATE::TEXT),
    method TEXT DEFAULT 'cash',
    notes TEXT,
    status TEXT DEFAULT 'pending',
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS collection_call_tasks (
    id SERIAL PRIMARY KEY,
    task_date DATE NOT NULL,
    member_id INTEGER NOT NULL,
    collection_id INTEGER,
    queue_type TEXT NOT NULL,
    cycle_date DATE,
    priority INTEGER NOT NULL DEFAULT 3,
    assigned_to INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    outcome TEXT,
    successful_contact INTEGER DEFAULT 0,
    next_channel TEXT DEFAULT 'CALL',
    next_action_at TEXT,
    notes TEXT,
    called_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(task_date, member_id),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (collection_id) REFERENCES collections(id),
    FOREIGN KEY (assigned_to) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_collection_call_tasks_daily
ON collection_call_tasks(task_date, assigned_to, status);

CREATE INDEX IF NOT EXISTS idx_collection_call_tasks_cycle
ON collection_call_tasks(cycle_date, member_id, successful_contact);

CREATE TABLE IF NOT EXISTS leave_requests (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    staff_id INTEGER,
    leave_type TEXT NOT NULL DEFAULT 'annual',
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    days INTEGER DEFAULT 1,
    reason TEXT,
    status TEXT DEFAULT 'pending',
    approved_by INTEGER,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (staff_id) REFERENCES staff(id),
    FOREIGN KEY (approved_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS fitness_classes (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    instructor_id INTEGER,
    schedule_day TEXT,
    schedule_time TEXT,
    capacity INTEGER DEFAULT 20,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (instructor_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS class_bookings (
    id SERIAL PRIMARY KEY,
    class_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    booking_date TEXT DEFAULT (CURRENT_DATE::TEXT),
    status TEXT DEFAULT 'booked',
    FOREIGN KEY (class_id) REFERENCES fitness_classes(id),
    FOREIGN KEY (member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS equipment (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    equipment_type TEXT DEFAULT 'general',
    serial_number TEXT,
    purchase_date TEXT,
    condition TEXT DEFAULT 'good',
    location TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS maintenance_log (
    id SERIAL PRIMARY KEY,
    equipment_id INTEGER NOT NULL,
    maintenance_date TEXT DEFAULT (CURRENT_DATE::TEXT),
    description TEXT NOT NULL,
    performed_by TEXT,
    cost DOUBLE PRECISION DEFAULT 0,
    next_due_date TEXT,
    notes TEXT,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (equipment_id) REFERENCES equipment(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    full_name TEXT NOT NULL,
    phone TEXT,
    phone_normalized TEXT,
    email TEXT,
    source TEXT DEFAULT 'Walk-In / Enquiry',
    original_source TEXT,
    referrer TEXT,
    campaign TEXT,
    assigned_to INTEGER,
    lead_status TEXT DEFAULT 'captured',
    interested_package TEXT,
    appointment_date TEXT,
    trial_start_date TEXT,
    trial_end_date TEXT,
    decision_start_date TEXT,
    decision_date TEXT,
    next_action TEXT,
    next_action_at TIMESTAMPTZ,
    marketing_consent INTEGER DEFAULT 1,
    do_not_contact INTEGER DEFAULT 0,
    closure_reason TEXT,
    recycle_due_at TIMESTAMPTZ,
    objection_reason TEXT,
    duplicate_of_lead_id INTEGER,
    converted_member_id INTEGER,
    last_activity_at TIMESTAMPTZ,
    lead_channel TEXT DEFAULT 'in_reach',
    entry_path TEXT DEFAULT 'standard',
    outreach_needs_appointment INTEGER,
    training_experience TEXT,
    training_preference TEXT,
    joining_preference TEXT,
    notes TEXT,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (assigned_to) REFERENCES users(id),
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (duplicate_of_lead_id) REFERENCES leads(id),
    FOREIGN KEY (converted_member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS lead_activities (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL,
    activity_type TEXT NOT NULL,
    outcome TEXT,
    status TEXT DEFAULT 'completed',
    notes TEXT,
    occurred_at TIMESTAMPTZ DEFAULT NOW(),
    scheduled_for TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    next_action TEXT,
    follow_up_at TIMESTAMPTZ,
    reason TEXT,
    offer_amount DOUBLE PRECISION,
    discount_percent DOUBLE PRECISION DEFAULT 0,
    manager_approval_status TEXT DEFAULT 'not_required',
    manager_approved_by INTEGER,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE,
    FOREIGN KEY (manager_approved_by) REFERENCES users(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_leads_status_assigned ON leads(lead_status, assigned_to);
CREATE INDEX IF NOT EXISTS idx_lead_activities_lead_date ON lead_activities(lead_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_lead_activities_followup ON lead_activities(status, scheduled_for);

CREATE TABLE IF NOT EXISTS membership_applications (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER,
    member_id INTEGER,
    package TEXT,
    application_status TEXT DEFAULT 'draft',
    date_joined TEXT DEFAULT (CURRENT_DATE::TEXT),
    push_status TEXT DEFAULT 'not_pushed',
    pushed_at TIMESTAMPTZ,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (lead_id) REFERENCES leads(id),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS debicheck_mandates (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    merchant_id TEXT NOT NULL,
    auth_type TEXT DEFAULT 'cell',
    client_ref1 TEXT NOT NULL,
    client_ref2 TEXT NOT NULL,
    account_name TEXT NOT NULL,
    account_type TEXT NOT NULL,
    account_number TEXT NOT NULL,
    branch_code TEXT NOT NULL,
    frequency TEXT DEFAULT '3',
    no_installments INTEGER DEFAULT 1,
    tracking TEXT DEFAULT '14',
    submit_date TEXT NOT NULL,
    id_type TEXT DEFAULT '0',
    id_number TEXT,
    passport TEXT,
    juristic_account TEXT,
    instalment_rands INTEGER DEFAULT 0,
    instalment_cents INTEGER DEFAULT 0,
    instalment_amount DOUBLE PRECISION DEFAULT 0,
    payer_type TEXT DEFAULT 'self',
    status TEXT DEFAULT 'pending',
    submitted_at TIMESTAMPTZ,
    submitted_by INTEGER,
    nupay_response TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (submitted_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS compliance_checklists (
    id SERIAL PRIMARY KEY,
    application_id INTEGER NOT NULL UNIQUE,
    contract_status TEXT DEFAULT 'pending',
    debit_check_status TEXT DEFAULT 'pending',
    bank_statement_status TEXT DEFAULT 'not_uploaded',
    id_copy_status TEXT DEFAULT 'not_uploaded',
    pos_status TEXT DEFAULT 'pending',
    age_category TEXT DEFAULT 'above_23',
    guardian_required TEXT DEFAULT 'not_required',
    guardian_status TEXT DEFAULT 'not_required',
    manager_approval_status TEXT DEFAULT 'not_required',
    overall_status TEXT DEFAULT 'incomplete',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (application_id) REFERENCES membership_applications(id)
);

CREATE TABLE IF NOT EXISTS statement_analyses (
    id SERIAL PRIMARY KEY,
    application_id INTEGER NOT NULL,
    income_detected TEXT DEFAULT 'unclear',
    estimated_monthly_income DOUBLE PRECISION DEFAULT 0,
    max_balance_m1 DOUBLE PRECISION DEFAULT 0,
    max_balance_m2 DOUBLE PRECISION DEFAULT 0,
    max_balance_m3 DOUBLE PRECISION DEFAULT 0,
    average_balance DOUBLE PRECISION DEFAULT 0,
    returned_debits_count INTEGER DEFAULT 0,
    negative_balance_days INTEGER DEFAULT 0,
    income_frequency TEXT DEFAULT 'irregular',
    risk_rating TEXT DEFAULT 'medium',
    analysis_summary TEXT,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (application_id) REFERENCES membership_applications(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id SERIAL PRIMARY KEY,
    application_id INTEGER NOT NULL,
    approval_type TEXT NOT NULL DEFAULT 'manager',
    requested_by INTEGER,
    approved_by INTEGER,
    status TEXT DEFAULT 'pending',
    reason TEXT,
    manager_comment TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    FOREIGN KEY (application_id) REFERENCES membership_applications(id),
    FOREIGN KEY (requested_by) REFERENCES users(id),
    FOREIGN KEY (approved_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS tariffs (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    duration_months INTEGER NOT NULL DEFAULT 12,
    monthly_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
    active INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 99,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS contract_templates (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    gym_name TEXT NOT NULL DEFAULT 'Elev8 Health & Fitness',
    agreement_title TEXT NOT NULL DEFAULT 'Membership Agreement',
    terms_text TEXT NOT NULL,
    footer_text TEXT,
    reg_no TEXT,
    address TEXT,
    accent_color TEXT DEFAULT '#185FA5',
    sections_config TEXT,
    terms_parts_config TEXT,
    debit_mandate_text TEXT,
    active INTEGER DEFAULT 1,
    is_default INTEGER DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    updated_by INTEGER REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS contract_share_tokens (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    sent_via TEXT, viewed_at TIMESTAMPTZ, responded_at TIMESTAMPTZ,
    response_note TEXT, created_by INTEGER, created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_contract_share_token_hash ON contract_share_tokens(token_hash);

CREATE TABLE IF NOT EXISTS report_definitions (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    table_name TEXT NOT NULL DEFAULT 'members',
    config_json TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    updated_by INTEGER REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS member_documents (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    document_type TEXT DEFAULT 'Other',
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    file_size INTEGER DEFAULT 0,
    analysis_json TEXT,
    uploaded_by INTEGER,
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS member_notes (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    application_id INTEGER,
    lead_id INTEGER,
    user_id INTEGER,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (application_id) REFERENCES membership_applications(id),
    FOREIGN KEY (lead_id) REFERENCES leads(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS turnstile_events (
    id SERIAL PRIMARY KEY,
    device_serial TEXT,
    raw_report_hex TEXT NOT NULL,
    credential TEXT,
    member_id INTEGER,
    decision TEXT DEFAULT 'unmatched',
    reason TEXT,
    direction TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id)
);

CREATE INDEX IF NOT EXISTS idx_turnstile_events_created ON turnstile_events(created_at);
CREATE INDEX IF NOT EXISTS idx_turnstile_events_credential ON turnstile_events(credential);

CREATE TABLE IF NOT EXISTS ptp_agreements (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    arrears_amount DOUBLE PRECISION DEFAULT 0,
    promise_amount DOUBLE PRECISION DEFAULT 0,
    promise_date TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    arrangement_type TEXT NOT NULL DEFAULT 'full',
    ptp_status TEXT DEFAULT 'pending',
    access_unblock TEXT DEFAULT 'no',
    unblock_until TEXT,
    auto_block_if_failed INTEGER DEFAULT 1,
    notes TEXT,
    manager_approval_status TEXT DEFAULT 'not_required',
    approved_by INTEGER,
    approved_date TEXT,
    approval_notes TEXT,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (approved_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS payment_plan_items (
    id SERIAL PRIMARY KEY,
    ptp_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    instalment_number INTEGER NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (ptp_id) REFERENCES ptp_agreements(id) ON DELETE CASCADE,
    FOREIGN KEY (member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS contact_logs (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    consultant_id INTEGER,
    contact_method TEXT DEFAULT 'call',
    call_code INTEGER,
    notes TEXT,
    next_followup_date TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (consultant_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS ptp_receipts (
    id SERIAL PRIMARY KEY,
    ptp_id INTEGER,
    member_id INTEGER NOT NULL,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    amount_paid DOUBLE PRECISION NOT NULL,
    payment_date TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    reference TEXT,
    uploaded_by INTEGER,
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    verified_by INTEGER,
    verified_at TIMESTAMPTZ,
    verification_status TEXT DEFAULT 'pending',
    FOREIGN KEY (ptp_id) REFERENCES ptp_agreements(id),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (uploaded_by) REFERENCES users(id),
    FOREIGN KEY (verified_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS legal_referrals (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL UNIQUE,
    referred_by INTEGER,
    referred_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'open',
    arrears_months INTEGER DEFAULT 0,
    arrears_amount DOUBLE PRECISION DEFAULT 0,
    reason TEXT,
    notes TEXT,
    resolved_by INTEGER,
    resolved_at TIMESTAMPTZ,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (referred_by) REFERENCES users(id),
    FOREIGN KEY (resolved_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS queries (
    id SERIAL PRIMARY KEY,
    reference TEXT UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    logged_by INTEGER,
    member_id INTEGER,
    member_name TEXT,
    contact TEXT,
    category TEXT NOT NULL,
    query_type TEXT,
    description TEXT NOT NULL,
    priority TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'open',
    assigned_to INTEGER,
    department TEXT,
    due_date TEXT,
    action_taken TEXT,
    follow_up_date TEXT,
    resolution_notes TEXT,
    closed_by INTEGER,
    closed_at TIMESTAMPTZ,
    ptp_id INTEGER,
    outstanding_snapshot DOUBLE PRECISION DEFAULT 0,
    arrears_months_snapshot INTEGER DEFAULT 0,
    discount_percent_snapshot DOUBLE PRECISION DEFAULT 0,
    discount_amount_snapshot DOUBLE PRECISION DEFAULT 0,
    settlement_amount_snapshot DOUBLE PRECISION DEFAULT 0,
    recommended_action_snapshot TEXT,
    access_decision_snapshot TEXT,
    manager_approval_required INTEGER DEFAULT 0,
    manager_approved_by INTEGER,
    manager_approved_at TEXT,
    cancellation_contract_months INTEGER,
    cancellation_months_completed INTEGER,
    cancellation_monthly_fee DOUBLE PRECISION DEFAULT 0,
    cancellation_fee DOUBLE PRECISION DEFAULT 0,
    cancellation_penalty_months INTEGER DEFAULT 0,
    cancellation_payment_method TEXT,
    cancellation_outstanding DOUBLE PRECISION DEFAULT 0,
    cancellation_settlement_amount DOUBLE PRECISION DEFAULT 0,
    cancellation_settlement_rule TEXT,
    cancellation_discount_pct DOUBLE PRECISION DEFAULT 0,
    member_cancelled INTEGER DEFAULT 0,
    FOREIGN KEY (logged_by) REFERENCES users(id),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE SET NULL,
    FOREIGN KEY (assigned_to) REFERENCES users(id),
    FOREIGN KEY (closed_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS query_notes (
    id SERIAL PRIMARY KEY,
    query_id INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by INTEGER,
    note TEXT NOT NULL,
    action_type TEXT DEFAULT 'note',
    FOREIGN KEY (query_id) REFERENCES queries(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

-- ── Fitness: assessments, workout plans, dailies, incidents, body matrix, nutrition, supplements ──

CREATE TABLE IF NOT EXISTS assessments (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    assessed_by_id INTEGER,
    date TEXT DEFAULT (CURRENT_DATE::TEXT),
    weight_kg DOUBLE PRECISION,
    body_fat_pct DOUBLE PRECISION,
    muscle_mass_kg DOUBLE PRECISION,
    bmi DOUBLE PRECISION,
    fitness_level TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (assessed_by_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS member_onboarding (
    id SERIAL PRIMARY KEY, member_id INTEGER NOT NULL UNIQUE,
    status TEXT DEFAULT 'eligible', eligible_from TEXT, first_visit_date TEXT,
    last_visit_date TEXT, visits_recorded INTEGER DEFAULT 0, closed_at TIMESTAMPTZ,
    close_reason TEXT, review_done INTEGER DEFAULT 0, review_by_id INTEGER,
    review_date TEXT, review_outcome TEXT, review_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (review_by_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_member_onboarding_status ON member_onboarding(status);
CREATE TABLE IF NOT EXISTS onboarding_visits (
    id SERIAL PRIMARY KEY, onboarding_id INTEGER NOT NULL, member_id INTEGER NOT NULL,
    visit_no INTEGER NOT NULL, visit_date TEXT NOT NULL, checked_in_at TIMESTAMPTZ,
    arrival_source TEXT DEFAULT 'turnstile', instructor_id INTEGER, actions TEXT,
    outcome TEXT, notes TEXT, recorded_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (member_id, visit_date),
    FOREIGN KEY (onboarding_id) REFERENCES member_onboarding(id) ON DELETE CASCADE,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (instructor_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_onboarding_visits_date ON onboarding_visits(visit_date);
CREATE TABLE IF NOT EXISTS onboarding_sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), last_event_id INTEGER DEFAULT 0, last_run_at TIMESTAMPTZ
);
INSERT INTO onboarding_sync_state (id, last_event_id) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS workout_plans (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    trainer_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (trainer_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS instructor_dailies (
    id SERIAL PRIMARY KEY,
    instructor_id INTEGER,
    member_id INTEGER,
    walk_in_name TEXT,
    client_type TEXT DEFAULT 'existing',
    service_type TEXT DEFAULT 'exercise_assistance',
    session_type TEXT DEFAULT 'full',
    date TEXT DEFAULT (CURRENT_DATE::TEXT),
    time_in TEXT,
    time_out TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (instructor_id) REFERENCES users(id),
    FOREIGN KEY (member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS incidents (
    id SERIAL PRIMARY KEY,
    member_id INTEGER,
    walk_in_name TEXT,
    walk_in_contact TEXT,
    incident_type TEXT NOT NULL DEFAULT 'other',
    description TEXT,
    severity TEXT DEFAULT 'medium',
    logged_by_id INTEGER,
    date TEXT DEFAULT (CURRENT_DATE::TEXT),
    time TEXT,
    status TEXT DEFAULT 'open',
    whatsapp_sent INTEGER DEFAULT 0,
    resolution_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (logged_by_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS body_matrix (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    recorded_by_id INTEGER,
    date TEXT DEFAULT (CURRENT_DATE::TEXT),
    week_number INTEGER,
    month TEXT,
    weight_kg DOUBLE PRECISION,
    body_fat_pct DOUBLE PRECISION,
    muscle_mass_kg DOUBLE PRECISION,
    bmi DOUBLE PRECISION,
    chest_cm DOUBLE PRECISION,
    waist_cm DOUBLE PRECISION,
    hips_cm DOUBLE PRECISION,
    arms_cm DOUBLE PRECISION,
    thighs_cm DOUBLE PRECISION,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (recorded_by_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS nutrition_foods (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'macro',
    macro_type TEXT,
    micro_type TEXT,
    serving_size TEXT,
    calories DOUBLE PRECISION DEFAULT 0,
    protein_g DOUBLE PRECISION DEFAULT 0,
    carbs_g DOUBLE PRECISION DEFAULT 0,
    fiber_g DOUBLE PRECISION DEFAULT 0,
    fat_g DOUBLE PRECISION DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS supplements (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    brand TEXT,
    supp_type TEXT NOT NULL,
    goal TEXT NOT NULL,
    risk_level INTEGER DEFAULT 1,
    description TEXT,
    dosage TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS member_supplements (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL,
    supplement_id INTEGER NOT NULL,
    assigned_by TEXT,
    start_date TEXT DEFAULT (CURRENT_DATE::TEXT),
    end_date TEXT,
    dosage TEXT,
    goal TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (supplement_id) REFERENCES supplements(id)
);

-- ── HR: staff register, attendance, payroll, timesheets ──────────────────────

CREATE TABLE IF NOT EXISTS staff (
    id SERIAL PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    id_number TEXT,
    role TEXT,
    department TEXT DEFAULT 'General',
    contact TEXT,
    email TEXT,
    start_date TEXT,
    contract_end TEXT,
    salary DOUBLE PRECISION DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS attendance (
    id SERIAL PRIMARY KEY,
    staff_id INTEGER NOT NULL,
    date TEXT DEFAULT (CURRENT_DATE::TEXT),
    clock_in TEXT,
    clock_out TEXT,
    status TEXT DEFAULT 'present',
    notes TEXT,
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payroll (
    id SERIAL PRIMARY KEY,
    staff_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    basic DOUBLE PRECISION DEFAULT 0,
    bonus DOUBLE PRECISION DEFAULT 0,
    deductions DOUBLE PRECISION DEFAULT 0,
    net DOUBLE PRECISION DEFAULT 0,
    status TEXT DEFAULT 'pending',
    paid_on TEXT,
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS timesheets (
    id SERIAL PRIMARY KEY,
    staff_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    time_in TEXT,
    lunch_out TEXT,
    lunch_in TEXT,
    time_out TEXT,
    hours_worked DOUBLE PRECISION DEFAULT 0,
    notes TEXT,
    captured_by TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS call_list_uploads (
    id SERIAL PRIMARY KEY,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    uploaded_by INTEGER,
    row_count INTEGER NOT NULL DEFAULT 0,
    imported_count INTEGER NOT NULL DEFAULT 0,
    parser_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    imported_at TEXT,
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS call_list_upload_rows (
    id SERIAL PRIMARY KEY,
    upload_id INTEGER NOT NULL,
    row_number INTEGER NOT NULL,
    client_name TEXT,
    contact TEXT,
    raw_response TEXT,
    mapped_outcome TEXT NOT NULL DEFAULT 'other',
    appointment_date TEXT,
    appointment_time TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    imported_lead_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (upload_id) REFERENCES call_list_uploads(id) ON DELETE CASCADE,
    FOREIGN KEY (imported_lead_id) REFERENCES leads(id)
);

CREATE INDEX IF NOT EXISTS idx_call_list_upload_hash ON call_list_uploads(file_sha256);
CREATE INDEX IF NOT EXISTS idx_call_list_rows_upload ON call_list_upload_rows(upload_id, row_number);

CREATE TABLE IF NOT EXISTS webhook_events (
    id SERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    event_key TEXT NOT NULL,
    event_type TEXT,
    payload_hash TEXT,
    received_at TEXT DEFAULT (NOW()),
    processed_at TEXT,
    status TEXT NOT NULL DEFAULT 'processing',
    error TEXT,
    UNIQUE(provider, event_key)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON webhook_events(provider, status);

CREATE TABLE IF NOT EXISTS wa_conversations (
    id SERIAL PRIMARY KEY,
    wa_number TEXT NOT NULL,
    contact_phone TEXT NOT NULL,
    contact_name TEXT,
    last_message_at TEXT,
    unread_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'open',
    linked_lead_id INTEGER,
    linked_member_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(wa_number, contact_phone),
    FOREIGN KEY (linked_lead_id) REFERENCES leads(id),
    FOREIGN KEY (linked_member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS wa_messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    wa_message_id TEXT UNIQUE,
    direction TEXT NOT NULL,
    message_type TEXT DEFAULT 'text',
    content TEXT,
    media_url TEXT,
    status TEXT DEFAULT 'received',
    sent_by_user_id INTEGER,
    timestamp TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (conversation_id) REFERENCES wa_conversations(id),
    FOREIGN KEY (sent_by_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS fb_conversations (
    id SERIAL PRIMARY KEY,
    page_id TEXT,
    sender_psid TEXT NOT NULL UNIQUE,
    sender_name TEXT,
    last_message_at TEXT,
    unread_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'open',
    linked_lead_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (linked_lead_id) REFERENCES leads(id)
);

CREATE TABLE IF NOT EXISTS fb_messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    fb_message_id TEXT UNIQUE,
    direction TEXT NOT NULL,
    content TEXT,
    attachment_type TEXT,
    attachment_url TEXT,
    sent_by_user_id INTEGER,
    timestamp TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (conversation_id) REFERENCES fb_conversations(id),
    FOREIGN KEY (sent_by_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS fb_comments (
    id SERIAL PRIMARY KEY,
    fb_comment_id TEXT UNIQUE,
    post_id TEXT,
    post_message TEXT,
    commenter_name TEXT,
    commenter_id TEXT,
    content TEXT,
    is_interested INTEGER DEFAULT 0,
    is_replied INTEGER DEFAULT 0,
    linked_lead_id INTEGER,
    timestamp TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (linked_lead_id) REFERENCES leads(id)
);
"""


def get_schema(db_type: str = "sqlite") -> str:
    """Return the appropriate schema SQL for the given database backend."""
    return SCHEMA_SQL_PG if db_type == "postgresql" else SCHEMA_SQL


# ── Platform registry schema (SQLite only — the tenant/platform-admin list) ────
# Lives in a separate platform.db, never in a tenant's own database.

PLATFORM_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    logo_filename TEXT,
    db_path TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS platform_admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS platform_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_admin_id INTEGER NOT NULL,
    tenant_id INTEGER,
    action TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (platform_admin_id) REFERENCES platform_admins(id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);
"""
