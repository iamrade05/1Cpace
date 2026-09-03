"""All module permission definitions for 1Cpase."""

MODULE_PERMISSIONS = {
    "CORE": [
        ("dashboard", "Dashboard"),
    ],
    "LEADS": [
        ("view_leads", "View Leads"),
        ("pbx_call", "PBX Calling"),
        ("capture_leads", "Capture Leads"),
        ("approve_sales_offers", "Approve Sales Offers"),
    ],
    "MEMBERS": [
        ("all_members", "All Members"),
        ("add_member", "Add Member"),
        ("daily_queries", "Daily Queries"),
        ("verification", "Verification"),
        ("application_approvals", "Application Manager Approvals"),
        ("member_lifecycle_change", "Member Lifecycle Changes"),
    ],
    "FITNESS": [
        ("fitness_module", "Fitness Module"),
        ("classes", "Classes"),
        ("dailies", "Dailies"),
        ("incident_log", "Incident Log"),
        ("body_matrix", "Body Matrix"),
        ("assessments", "Assessments"),
        ("workout_plans", "Workout Plans"),
        ("nutrition_guide", "Nutrition Guide"),
        ("supplements", "Supplements"),
    ],
    "COLLECTIONS": [
        ("all_collections", "All Collections"),
        ("collections_call_queue", "Collections Call Queue"),
        ("new_collection", "New Collection"),
        ("debicheck_mandates", "DebiCheck Mandates"),
        ("new_debicheck", "New DebiCheck"),
    ],
    "REPORTS": [
        ("reports_module", "Reports Module"),
        ("daily_reports", "Daily Reports"),
        ("weekly_reports", "Weekly Reports"),
        ("monthly_reports", "Monthly Reports"),
    ],
    "OPERATIONS": [
        ("operations_module", "Operations Module"),
        ("equipment", "Equipment"),
        ("maintenance", "Maintenance"),
        ("inventory", "Inventory"),
        ("suppliers", "Suppliers"),
    ],
    "HR": [
        ("hr_module", "HR Module"),
        ("hr_staff", "Staff"),
        ("hr_leave", "Leave"),
        ("hr_payroll", "Payroll"),
        ("hr_timesheets", "Timesheets"),
    ],
    "ADMIN": [
        ("user_management", "User Management"),
        ("contract_setup", "Contract Setup"),
    ],
    "COMMUNICATIONS": [
        ("communications_hub", "Communications Hub"),
        ("reception_whatsapp", "Reception WhatsApp"),
        ("sales_whatsapp", "Sales WhatsApp"),
        ("facebook_messages", "Facebook Messages"),
        ("facebook_comments", "Facebook Comments"),
    ],
}

# Flat set of all valid permission keys
ALL_PERMISSIONS = {perm for perms in MODULE_PERMISSIONS.values() for perm, _ in perms}

# Permissions granted automatically by department/role, on top of whatever a
# user is individually assigned via the Add/Edit User form. Keeps a Sales
# Manager fully functional without an admin having to remember to tick every
# box by hand.
DEPARTMENT_DEFAULT_PERMISSIONS = {
    "Sales": {
        "dashboard", "view_leads", "capture_leads", "approve_sales_offers",
        "all_members", "add_member", "daily_queries", "verification",
        "communications_hub", "pbx_call",
    },
}

ROLE_DEFAULT_PERMISSIONS = {
    "manager": {
        "reports_module", "daily_reports", "weekly_reports", "monthly_reports",
        "application_approvals",
    },
}


def default_permissions_for(role: str, department: str) -> set:
    """Permissions a user gets for free from their role/department, merged
    with whatever's individually granted in user_permissions."""
    return (DEPARTMENT_DEFAULT_PERMISSIONS.get(department, set())
            | ROLE_DEFAULT_PERMISSIONS.get(role, set()))
