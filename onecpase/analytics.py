"""Read-only management reports built from tenant operational records."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .financial_reporting import cash_amount, money
from .reporting_cycle import current_cycle, today
from .staff_kpi import business_day


TITLES = {
    "master": "Master Member Report",
    "kpis": "Staff KPIs",
    "funnel": "Sales Funnel",
    "reception": "Reception, Collections and Debits",
    "compliance": "Member Compliance",
    "commission": "Sales Commission",
}


def reporting_window(args):
    as_of = today()
    start, end = current_cycle(as_of)
    end = min(end, as_of)
    try:
        requested_start = date.fromisoformat(args.get("from", start.isoformat()))
        requested_end = date.fromisoformat(args.get("to", end.isoformat()))
        if requested_start <= requested_end <= as_of:
            return requested_start, requested_end
    except ValueError:
        pass
    return start, end


def _in_window(value, start, end):
    day = business_day(value)
    return day is not None and start <= day <= end


def _members(db, member_ref=""):
    return db.execute("""
        SELECT m.id, m.member_ref, m.first_name, m.last_name, m.contact, m.email,
               m.date_of_birth, m.gender, m.join_date, m.member_status, m.package,
               m.tariff, m.contract_duration, m.monthly_installment, m.joining_fee,
               m.source, m.entry_type, m.outcome, m.street_number,m.street_name,m.suburb,m.city,m.postal_code,
               m.country, m.payment_type, m.debit_order_date, m.gym_access_status,
               m.access_blocked_until, m.itensity_ref, m.opt_in, m.fitness_goal,
               m.training_experience, m.promotion, m.access_level,m.occupation,m.employer,
               m.alternative_number,m.work_number,m.member_addons,
               CASE WHEN NULLIF(m.id_number,'') IS NULL THEN 'Missing' ELSE 'On file' END AS identity_status,
               m.sold_by, m.created_at, u.full_name AS loaded_by,
               a.id AS application_id, a.application_status, a.push_status,
               COALESCE(u.full_name, creator.full_name) AS owner,
               d.status AS mandate_status, o.status AS onboarding_status
        FROM members m
        LEFT JOIN users u ON u.id=m.uploaded_by_id
        LEFT JOIN membership_applications a ON a.id=(
            SELECT MAX(ma.id) FROM membership_applications ma WHERE ma.member_id=m.id)
        LEFT JOIN users creator ON creator.id=a.created_by
        LEFT JOIN debicheck_mandates d ON d.id=(
            SELECT MAX(dm.id) FROM debicheck_mandates dm WHERE dm.member_id=m.id)
        LEFT JOIN member_onboarding o ON o.member_id=m.id
        WHERE (?='' OR m.member_ref=?)
        ORDER BY m.last_name, m.first_name, m.id
    """, (member_ref, member_ref)).fetchall()


def _compliance(db, members):
    from .applications import compliance_completion_blockers
    from .debicheck import normalise_debicheck_status

    documents = defaultdict(set)
    for row in db.execute("SELECT member_id,document_type FROM member_documents"):
        documents[row["member_id"]].add(row["document_type"] or "Other")
    checklists = {r["application_id"]: r for r in db.execute(
        "SELECT application_id,id_copy_status,bank_statement_status,updated_at FROM compliance_checklists"
    )}
    results = {}
    for member in members:
        mid, aid = member["id"], member["application_id"]
        checks = checklists.get(aid)
        files = documents[mid]
        missing = [label for field, label in (
            ("first_name", "First name"), ("last_name", "Last name"),
            ("date_of_birth", "Date of birth"), ("join_date", "Join date"),
            ("payment_type", "Payment method"),
        ) if not str(member[field] or "").strip()]
        if not any((member[field] or "").strip() for field in ("contact", "email")):
            missing.append("Contact number or email")
        if not (member["package"] or member["tariff"]):
            missing.append("Membership package")
        debit = (member["payment_type"] or "").strip().lower() in ("debit order", "debicheck", "debit_order")
        if debit and not member["debit_order_date"]:
            missing.append("Debit order date")
        if member["identity_status"] == "Missing":
            missing.append("Identity number")
        if aid is None:
            blockers = ["No application checklist available"]
        elif debit:
            blockers = compliance_completion_blockers(db, aid)
        else:
            # The existing activation gate is specifically for debit-order applications.
            blockers = ["Payment-specific compliance rules require review"]
        for kind, field, accepted in (
            ("SA ID Copy", "id_copy_status", ("verified",)),
            ("Bank Statement", "bank_statement_status", ("analysed", "verified", "approved")),
        ):
            if kind in files and (kind != "Bank Statement" or debit):
                if not checks or checks[field] not in accepted:
                    blockers.append(f"{kind} uploaded, verification outstanding")
        state = "Review required" if not aid or not debit else (
            "Incomplete" if missing or blockers else "Complete"
        )
        updated = business_day(checks["updated_at"] if checks else member["created_at"])
        results[mid] = (state, ", ".join(missing), "; ".join(dict.fromkeys(blockers)),
                        ", ".join(sorted(files)), member["owner"],
                        max(0, (today() - updated).days) if updated and state != "Complete" else None,
                        normalise_debicheck_status(member["mandate_status"]) if member["mandate_status"] else "",
                        checks["id_copy_status"] if checks else None,
                        checks["bank_statement_status"] if checks else None)
    return results


MASTER_FIELDS = (
    ("member_ref", "Reference"), ("first_name", "First name"), ("last_name", "Last name"),
    ("contact", "Contact"), ("email", "Email"), ("alternative_number", "Alternative number"),
    ("work_number", "Work number"), ("date_of_birth", "Birth date"), ("identity_status", "Identity number status"),
    ("gender", "Gender"), ("occupation", "Occupation"), ("employer", "Employer"),
    ("street_number", "Street number"), ("street_name", "Street"), ("suburb", "Suburb"),
    ("city", "City"), ("postal_code", "Postal code"),
    ("country", "Country"), ("opt_in", "Opt-in"), ("created_at", "Profile created"),
    ("join_date", "Joined"), ("member_status", "Membership status"), ("package", "Package"),
    ("tariff", "Tariff"), ("contract_duration", "Contract duration"), ("promotion", "Promotion"),
    ("member_addons", "Membership add-ons"),
    ("monthly_installment", "Monthly fee (R)"), ("joining_fee", "Joining fee (R)"),
    ("source", "Source"), ("entry_type", "Entry type"), ("outcome", "Outcome"),
    ("sold_by", "Imported seller"), ("loaded_by", "Loaded by"),
    ("payment_type", "Payment method"), ("debit_order_date", "Debit day"),
    ("access_level", "Access level"), ("gym_access_status", "Access status"),
    ("access_blocked_until", "Access blocked until"), ("itensity_ref", "Itensity reference"),
    ("application_status", "Application status"), ("push_status", "Activation status"),
    ("fitness_goal", "Fitness goal"), ("training_experience", "Training experience"),
    ("onboarding_status", "Fitness onboarding"),
)


def _table(title, headers, rows):
    return dict(title=title, headers=headers, rows=list(rows))


def _identity(member):
    return (member["member_ref"], f'{member["first_name"]} {member["last_name"]}')


def _payments(db):
    return db.execute("""
        SELECT c.id,c.member_id,c.collection_date,c.method,c.payment_purpose,c.status,c.amount_paid,
               c.outstanding_balance,c.notes,c.created_by,u.full_name AS staff,
               m.member_ref,m.first_name,m.last_name
        FROM collections c JOIN members m ON m.id=c.member_id
        LEFT JOIN users u ON u.id=c.created_by
        ORDER BY c.collection_date,c.id
    """).fetchall()


def _first_payments(payments):
    """First currently cleared general payment on or after each supplied join."""
    by_member = defaultdict(list)
    for row in payments:
        if row["payment_purpose"] in (None, "", "general") and cash_amount(row) > 0:
            by_member[row["member_id"]].append(row)
    return by_member


def _first_payment(payments, mid, joined):
    for row in payments.get(mid, ()):
        day = business_day(row["collection_date"])
        if day and joined <= day <= today():
            return day, cash_amount(row)
    return None, None


def member_tables(db, report, start, end, member_ref, review_status=""):
    from .ptp import bulk_member_arrears

    members = _members(db, member_ref)
    compliance = _compliance(db, members)
    if report == "compliance":
        matching_ids = {m["id"] for m in members
                         if not review_status or compliance[m["id"]][0] == review_status}
        return [_table("Profile and attachment review",
                       ("Reference", "Member", "Membership", "Review status", "Missing profile fields",
                        "Evidence outstanding", "Attachments on file", "Application owner",
                        "Days since checklist update", "Mandate status", "ID copy check", "Bank statement check"),
                       ((*_identity(m), m["member_status"], *compliance[m["id"]]) for m in members
                        if m["id"] in matching_ids)),
                _table("Attachment register", ("Reference", "Type", "Uploaded", "Uploaded by"),
                       (tuple(r)[1:] for r in db.execute("""
                           SELECT m.id AS member_id,m.member_ref,d.document_type,d.uploaded_at,u.full_name
                           FROM member_documents d JOIN members m ON m.id=d.member_id
                           LEFT JOIN users u ON u.id=d.uploaded_by
                           WHERE (?='' OR m.member_ref=?)
                           ORDER BY m.member_ref,d.uploaded_at,d.id
                       """, (member_ref, member_ref)) if r["member_id"] in matching_ids))]
    ledger = defaultdict(lambda: [0, 0])
    for row in _payments(db):
        if _in_window(row["collection_date"], start, end):
            ledger[row["member_id"]][0] += cash_amount(row)
            ledger[row["member_id"]][1] += 1
    queries = Counter(r["member_id"] for r in db.execute("SELECT member_id FROM queries WHERE status <> 'closed'"))
    arrears = bulk_member_arrears(db, [m["id"] for m in members] if member_ref else None)
    from .consultant_book import load_consultant_book
    sellers = {m["id"]: m["consultant"] for m in load_consultant_book(
        db, include_all_members=True)["members"]}
    return [_table("One row per member", tuple(label for _, label in MASTER_FIELDS) + (
        "Credited sales consultant", "Cash in period (R)", "Ledger records in period", "Current arrears (R)",
        "Open queries", "Compliance", "Missing profile fields", "Evidence outstanding", "Attachments",
        "Application owner", "Days since checklist update", "Latest mandate status",
        "ID copy check", "Bank statement check",
    ), (tuple(m[field] for field, _ in MASTER_FIELDS) + (
        sellers.get(m["id"]), *ledger[m["id"]], arrears.get(m["id"], {}).get("total_arrears", 0),
        queries[m["id"]], *compliance[m["id"]],
    ) for m in members))]


def reception_tables(db, start, end, member_ref):
    from .members import _collection_note_parts, _payment_source
    from .ptp import bulk_member_arrears
    from .debicheck import normalise_debicheck_status

    lookup_start = start - timedelta(days=1) if start > date.min else start
    queries = db.execute("""
        SELECT q.reference,q.created_at,q.category,q.query_type,q.status,q.closed_at,
               q.closure_reason,q.resolution_notes,q.member_name,m.member_ref,logger.full_name AS logged_by,
               owner.full_name AS owner
        FROM queries q LEFT JOIN members m ON m.id=q.member_id
        LEFT JOIN users logger ON logger.id=q.logged_by
        LEFT JOIN users owner ON owner.id=q.assigned_to
        WHERE substr(CAST(q.created_at AS TEXT),1,10) BETWEEN ? AND ?
        ORDER BY q.created_at DESC,q.id DESC
    """, (lookup_start.isoformat(), end.isoformat()))
    query_rows = []
    for row in queries:
        if member_ref and row["member_ref"] != member_ref:
            continue
        opened = business_day(row["created_at"])
        if not opened or not start <= opened <= end:
            continue
        closed = business_day(row["closed_at"])
        age = max(0, ((closed or today()) - opened).days) if opened else None
        query_rows.append((row["reference"], row["member_ref"], row["member_name"], opened,
                           row["category"], row["query_type"], row["logged_by"], row["owner"],
                           row["status"], age, closed, row["closure_reason"], row["resolution_notes"]))
    payment_rows, debit_rows = [], []
    payment_summary = defaultdict(lambda: [0, 0])
    for row in _payments(db):
        if (member_ref and row["member_ref"] != member_ref) or not _in_window(row["collection_date"], start, end):
            continue
        values = (row["id"], *_identity(row), row["collection_date"], row["method"], row["payment_purpose"],
                  row["status"], money(row["outstanding_balance"]), cash_amount(row), row["staff"])
        payment_rows.append(values)
        payment_summary[row["member_id"]][0] += values[-2]
        method = (row["method"] or "").strip().lower()
        if (method in ("debit order", "debit_order", "nupay")
                or _payment_source(row, _collection_note_parts(row["notes"])) == "Automatic Debit Order"):
            debit_rows.append(values)
            payment_summary[row["member_id"]][1] += 1
    members = _members(db, member_ref)
    arrears = bulk_member_arrears(db, [m["id"] for m in members] if member_ref else None)
    payment_headers = ("Ledger ID", "Reference", "Member", "Date", "Method", "Purpose", "Ledger status",
                       "Gross charge (R)", "Cash received (R)", "Recorded by")
    return [
        _table("Member payment position", ("Reference", "Member", "Payment method", "Debit day",
                "Latest mandate status", "Cash received in period (R)", "Debit ledger rows in period", "Current arrears (R)"),
               ((*_identity(m), m["payment_type"], m["debit_order_date"],
                 normalise_debicheck_status(m["mandate_status"]) if m["mandate_status"] else None,
                 *payment_summary[m["id"]], arrears.get(m["id"], {}).get("total_arrears", 0)) for m in members)),
        _table("Queries logged", ("Query", "Member reference", "Member", "Logged", "Category", "Type",
                                  "Logged by", "Owner", "Status", "Age in days", "Closed", "Closure reason", "Resolution"),
               query_rows),
        _table("Collections ledger", payment_headers, payment_rows),
        _table("Debit order ledger", payment_headers, debit_rows),
    ]


FUNNEL_STAGES = ("Captured", "Contacted", "Booked", "Showed", "Joined", "First payment cleared")


def _funnel_data(db, start, end, consultant="", source="", *, include_details=True):
    from .sales_pipeline import sales_milestones

    as_of = today()
    leads = [row for row in db.execute("""
        SELECT l.id,l.full_name,l.created_at,l.lead_status,l.converted_member_id,l.first_contact_at,l.joined_at,
               COALESCE(NULLIF(l.original_source,''),l.source,'Unknown') AS source,
               COALESCE(u.full_name,'Unassigned') AS consultant
        FROM leads l LEFT JOIN users u ON u.id=l.assigned_to
        WHERE l.duplicate_of_lead_id IS NULL AND l.lead_status NOT IN ('duplicate','existing_member')
        ORDER BY l.created_at,l.id
    """) if _in_window(row["created_at"], start, end)
        and (not consultant or consultant.casefold() in row["consultant"].casefold())
        and (not source or source.casefold() in row["source"].casefold())]
    ids = {row["id"] for row in leads}
    activities, stages = defaultdict(set), defaultdict(set)
    for row in db.execute("SELECT lead_id,activity_type,outcome,occurred_at FROM lead_activities"):
        if row["lead_id"] in ids and _in_window(row["occurred_at"], start, as_of):
            activities[row["lead_id"]].add((row["activity_type"], row["outcome"]))
    for row in db.execute("SELECT lead_id,to_stage,changed_at FROM sales_stage_history"):
        if row["lead_id"] in ids and _in_window(row["changed_at"], start, as_of):
            stages[row["lead_id"]].add(row["to_stage"])
    members = {r["id"]: r for r in db.execute("SELECT id,join_date,member_status FROM members")}
    payments = _first_payments(_payments(db))
    counts, transitions = Counter(), Counter()
    groups = defaultdict(Counter)
    details = []
    for lead in leads:
        lid = lead["id"]
        evidence = sales_milestones(lead["lead_status"], activities[lid])
        member = members.get(lead["converted_member_id"])
        joined = (business_day(member["join_date"]) or business_day(lead["joined_at"])) if member else None
        joined = joined if joined and start <= joined <= as_of and member["member_status"] != "Unverified" else None
        paid = _first_payment(payments, member["id"], joined)[0] if joined else None
        flags = (True, evidence["contacted"] or "CONTACTED" in stages[lid]
                 or bool(_in_window(lead["first_contact_at"], start, as_of)),
                 evidence["appointment"] or "APPOINTMENT_BOOKED" in stages[lid],
                 evidence["show"] or "SHOW" in stages[lid], bool(joined), bool(paid))
        group = groups[lead["consultant"], lead["source"]]
        for index, reached in enumerate(flags):
            counts[index] += reached
            group[index] += reached
            if index:
                transitions[index] += reached and flags[index - 1]
        if include_details:
            details.append((lid, lead["full_name"], business_day(lead["created_at"]), lead["consultant"],
                            lead["source"], *("Yes" if flag else "—" for flag in flags[1:])))
    summary = []
    for index, label in enumerate(FUNNEL_STAGES):
        previous = counts[index - 1] if index else 0
        overlap = transitions[index]
        summary.append((label, counts[index], f"{overlap / previous:.1%}" if previous else None,
                        previous - overlap if index else None))
    return summary, groups, details


def funnel_tables(db, start, end, consultant="", source=""):
    summary, groups, details = _funnel_data(db, start, end, consultant, source)
    return [
        _table("Funnel stages", ("Stage", "Leads reaching stage", "Conversion from prior stage",
                                "Prior-stage leads without this milestone"), summary),
        _table("Consultant and source", ("Consultant", "Lead source", *FUNNEL_STAGES),
               ((*key, *(values[i] for i in range(len(FUNNEL_STAGES)))) for key, values in sorted(groups.items()))),
        _table("Lead details", ("Lead ID", "Lead", "Captured", "Consultant", "Lead source",
                               *FUNNEL_STAGES[1:]), details),
    ]


def commission_rate(raw):
    try:
        rate = Decimal(raw)
        if rate.is_finite() and 0 <= rate <= 100:
            return rate
    except (InvalidOperation, TypeError):
        pass
    return None


def commission_tables(db, start, end, rate):
    from .consultant_book import load_consultant_book

    payments = _first_payments(_payments(db))
    rows = []
    for member in load_consultant_book(db, include_all_members=True)["members"]:
        paid_on, basis = _first_payment(payments, member["id"], member["joined"])
        if not (start <= member["joined"] <= end or (paid_on and start <= paid_on <= end)):
            continue
        state = "Review loss" if member["lost"] else "Payment recorded" if paid_on else "Awaiting payment"
        estimate = ((basis * rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if paid_on and rate is not None and not member["lost"] else None)
        rows.append((*_identity(member), member["consultant"], member["joined"], paid_on,
                     basis, rate, estimate, state))
    consultant_totals = defaultdict(lambda: [0, 0, Decimal(0)])
    for row in rows:
        counts = consultant_totals[row[2]]
        counts[0] += 1
        if row[7] is not None:
            counts[1] += 1
            counts[2] += row[7]
    return [
        _table("Commission review", ("Reference", "Member", "Consultant", "Joined", "First cleared payment",
               "First payment basis (R)", "Scenario rate (%)", "Estimated commission (R)", "Review status"), rows),
        _table("Consultant totals", ("Consultant", "Sales reviewed", "Sales with estimate", "Estimated total (R)"),
               ((name, counts[0], counts[1], counts[2] if counts[1] else None)
                for name, counts in sorted(consultant_totals.items()))),
    ]


def kpi_tables(db, start, end, kpi_day):
    from .staff_kpi import build_daily_scorecard
    from .sales_kpi import credited_sales
    from .hr_workforce import department_key
    from .database import _db_type

    tables = []
    staff = {r["id"]: r for r in db.execute(
        "SELECT id,full_name,department,active FROM users WHERE COALESCE(is_platform_user,0)=0 ORDER BY full_name")}
    cards = {scope: build_daily_scorecard(db, scope, kpi_day) for scope in ("sales", "reception")}
    for scope, card in cards.items():
        headers = ("Staff member", *(label for _, label, _ in card["targets"]))
        tables.append(_table(f"{scope.title()} daily targets · {card['day']}", headers,
                             ((row["name"], *(f"{c['actual']} / {c['target']}" if c["target"] else c["actual"]
                                              for c in row["cells"])) for row in card["rows"])))
    sales = Counter(row["credited_to"] for row in credited_sales(
        db, lambda stamp: _in_window(stamp, start, end), lambda uid: uid in staff
    ))
    sales_ids = {row["id"] for row in cards["sales"]["rows"]} | sales.keys()
    tables.append(_table("Sales credited in period", ("Staff member", "Compliance-complete sales"),
                         ((row["full_name"], sales[uid]) for uid, row in staff.items() if uid in sales_ids)))
    lead_totals = defaultdict(lambda: [0, 0])
    _, cohort_groups, _ = _funnel_data(db, start, end, include_details=False)
    for (consultant, _), counts in cohort_groups.items():
        lead_totals[consultant][0] += counts[0]
        lead_totals[consultant][1] += counts[4]
    tables.append(_table("Sales lead conversion", ("Consultant", "Leads captured in period",
                         "Those leads joined to date", "Lead-to-join conversion"),
                         ((name, captured, joined, f"{joined / captured:.1%}")
                          for name, (captured, joined) in sorted(lead_totals.items()))))
    reception_ids = {row["id"] for row in cards["reception"]["rows"]}
    logged, resolved, collected = Counter(), Counter(), Counter()
    for row in db.execute("SELECT logged_by,created_at,closed_by,closed_at,status FROM queries"):
        if _in_window(row["created_at"], start, end):
            logged[row["logged_by"]] += 1
        if row["status"] == "closed" and _in_window(row["closed_at"], start, end):
            resolved[row["closed_by"]] += 1
    for row in _payments(db):
        if row["created_by"] in reception_ids and _in_window(row["collection_date"], start, end):
            collected[row["created_by"]] += cash_amount(row)
    tables.append(_table("Reception in period", ("Staff member", "Queries logged", "Queries closed", "Cash recorded (R)"),
                         ((r["full_name"], logged[uid], resolved[uid], collected[uid])
                          for uid, r in staff.items() if uid in reception_ids)))
    sessions, reviews, followups = Counter(), Counter(), Counter()
    for row in db.execute("SELECT instructor_id,date FROM instructor_dailies WHERE NULLIF(time_out,'') IS NOT NULL"):
        if _in_window(row["date"], start, end):
            sessions[row["instructor_id"]] += 1
    for row in db.execute("SELECT review_by_id,review_date FROM member_onboarding WHERE review_done=1"):
        if _in_window(row["review_date"], start, end):
            reviews[row["review_by_id"]] += 1
    if _db_type() == "postgresql":
        from .db_adapter import pg_table_columns
        allocation_available = bool(pg_table_columns(db._conn, "fitness_allocation_requests"))
    else:
        allocation_available = bool(db.execute("PRAGMA table_info(fitness_allocation_requests)").fetchone())
    if allocation_available:
        for row in db.execute("""SELECT instructor_id,ends_at FROM fitness_allocation_requests
                               WHERE status IN ('reserved','attendance_due')"""):
            due = business_day(row["ends_at"])
            if due and due < today():
                followups[row["instructor_id"]] += 1
    tables.append(_table("Fitness instructors", ("Staff member", "Completed session records in period",
                         "Onboarding reviews completed in period", "Attendance follow-ups overdue today"),
                         ((r["full_name"], sessions[uid], reviews[uid],
                           followups[uid] if allocation_available else "Unavailable")
                          for uid, r in staff.items() if (r["active"] and department_key(r["department"]) == "fitness")
                          or sessions[uid] or reviews[uid] or followups[uid])))
    cleaner_counts = defaultdict(lambda: [0, 0])
    for row in db.execute("""
        SELECT h.user_id,h.department,h.work_date,
               EXISTS(SELECT 1 FROM attendance a JOIN hr_profiles p ON p.staff_id=a.staff_id
                      WHERE p.user_id=h.user_id AND a.date=h.work_date
                        AND NULLIF(a.clock_in,'') IS NOT NULL AND NULLIF(a.clock_out,'') IS NOT NULL
                        AND LOWER(a.status)='present') AS attended
        FROM hr_shifts h WHERE h.status='published' AND h.work_date BETWEEN ? AND ?
    """, (start.isoformat(), end.isoformat())):
        if department_key(row["department"]) == "cleaner":
            cleaner_counts[row["user_id"]][0] += 1
            cleaner_counts[row["user_id"]][1] += bool(row["attended"])
    tables.append(_table("Cleaners", ("Staff member", "Published shifts in period", "Shifts with complete attendance",
                         "Cleaning checks", "Inspections", "Issues resolved"),
                         ((r["full_name"], *cleaner_counts[uid], "Unavailable", "Unavailable", "Unavailable")
                          for uid, r in staff.items() if (r["active"] and department_key(r["department"]) == "cleaner")
                          or uid in cleaner_counts)))
    cleaning_issues = db.execute("""
        SELECT reference,created_at,status,closed_at FROM queries
        WHERE LOWER(TRIM(COALESCE(query_type,'')))='cleaning complaint'
        ORDER BY created_at DESC,id DESC
    """)
    tables.append(_table("Cleaning complaints", ("Query reference", "Logged", "Current status", "Closed"),
                         (tuple(row) for row in cleaning_issues
                          if _in_window(row["created_at"], start, end)
                          or _in_window(row["closed_at"], start, end))))
    return tables
