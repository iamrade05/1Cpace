import json

from flask import Blueprint, abort, render_template, request
from datetime import date, timedelta
from werkzeug.datastructures import MultiDict
from .auth import permission_required
from .dashboard_access import dashboard_only
from .database import get_db
from .encryption import decrypt
from .member_counts import get_member_counts
from .reporting_cycle import today as business_today, current_cycle, cycle_key, cycle_bounds, shift_month, history_start
from .financial_reporting import cash_amount, cash_received, monthly_totals, totals as financial_totals

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

PERIODS = [
    ('this_month',     'This Cycle (15th to 14th)'),
    ('last_month',     'Previous Cycle (15th to 14th)'),
    ('this_quarter',   'This Quarter'),
    ('this_year',      'This Year'),
    ('last_12_months', 'Last 12 Months'),
    ('all_time',       'All Time'),
]

VALID_TABS = ('overview', 'membership', 'finance', 'arrears', 'queries', 'custom')

# ── Membership report (one row per member) ────────────────────────────────────

REGISTER_STATUSES = ('Active', 'Inactive', 'Blocked', 'Unverified')
NOT_ATTENDING_DAYS = 30
_VISIT_CACHE = {"at": None, "data": None, "error": None}


def _mask_id(value):
    """Show the first 6 digits (date of birth) and last 3 only."""
    text = (value or "").strip()
    if len(text) < 10:
        return text
    return f"{text[:6]} \u2022\u2022\u2022\u2022 {text[-3:]}"


def _phone_key(value):
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else ""


def _itensity_last_visits():
    """Last gym visit per member from the Elev8 SQL Server, keyed by the last 9
    digits of the cellphone number. Returns (dict, error). Cached for 5 minutes.
    When the server cannot be reached the report still loads, with no visit data,
    rather than showing invented dates."""
    from .elev8_data import elev8_source_enabled
    if not elev8_source_enabled():
        return {}, 'External attendance is not configured for this gym.'
    from datetime import datetime
    from flask import current_app, g
    cache_key = (current_app.config['PLATFORM_DATABASE_PATH'], g.tenant_slug)
    now = datetime.now()
    if _VISIT_CACHE.get('tenant') == cache_key and _VISIT_CACHE["at"] and (now - _VISIT_CACHE["at"]).total_seconds() < 300:
        return _VISIT_CACHE["data"], _VISIT_CACHE["error"]
    data, error = {}, None
    try:
        from .access_report import _elev8_conn
        conn = _elev8_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT RIGHT(NULLIF(LTRIM(RTRIM(cell)), ''), 9) AS p, "
                "MAX(TRY_CONVERT(datetime2(0), last_visited)) AS lv "
                "FROM dbo.last_visited_report "
                "WHERE NULLIF(LTRIM(RTRIM(cell)), '') IS NOT NULL "
                "GROUP BY RIGHT(NULLIF(LTRIM(RTRIM(cell)), ''), 9)"
            )
            for p, lv in cur.fetchall():
                if p and lv:
                    data[p] = lv.date().isoformat() if hasattr(lv, "date") else str(lv)[:10]
        finally:
            conn.close()
    except Exception as exc:  # no driver, server off, not on the gym network
        data, error = {}, str(exc)[:160]
    _VISIT_CACHE.update(tenant=cache_key, at=now, data=data, error=error)
    return data, error


def _load_register(db, status='', search='', segment=''):
    today = date.today()
    rows = db.execute("""
        SELECT m.id, m.member_ref, m.first_name, m.last_name, m.id_number, m.contact,
               m.member_status, m.package, m.monthly_installment, m.join_date,
               m.payment_type, m.sold_by, u.full_name AS signed_by_user,
               (SELECT MAX(c.collection_date) FROM active_collections c
                 WHERE c.member_id = m.id AND COALESCE(c.amount_paid,0) > 0) AS last_paid,
               (SELECT MAX(t.created_at) FROM turnstile_events t
                 WHERE t.member_id = m.id AND t.decision = 'granted') AS local_last_visit,
               (SELECT d.status FROM debicheck_mandates d
                 WHERE d.member_id = m.id ORDER BY d.id DESC LIMIT 1) AS mandate_status
        FROM members m LEFT JOIN users u ON m.uploaded_by_id = u.id
        ORDER BY m.last_name, m.first_name
    """).fetchall()

    from .ptp import bulk_member_arrears
    arrears = bulk_member_arrears(db)
    visits, visit_error = _itensity_last_visits()
    cash_dates = {}
    for payment in db.execute('SELECT member_id,collection_date,amount_paid,status,notes FROM active_collections WHERE COALESCE(amount_paid,0)>0'):
        if cash_amount(payment) > 0:
            mid = payment['member_id']
            cash_dates[mid] = max(cash_dates.get(mid,''), str(payment['collection_date'] or '')[:10])

    out = []
    for r in rows:
        d = dict(r)
        d["id_masked"] = _mask_id(decrypt(d["id_number"]))
        d["name"] = f"{d['first_name'] or ''} {d['last_name'] or ''}".strip()
        d["signed_by"] = d["sold_by"] or d["signed_by_user"]
        last_visit = visits.get(_phone_key(d["contact"])) or (d["local_last_visit"] or "")[:10] or None
        d["last_visit"] = last_visit
        d["days_since_visit"] = (today - date.fromisoformat(last_visit)).days if last_visit else None
        d["last_paid"] = cash_dates.get(d['id']) or None
        d["owing"] = round(arrears.get(d["id"], {}).get("total_arrears", 0) or 0, 2)
        ms = (d["mandate_status"] or "").strip()
        if ms:
            from .debicheck import normalise_debicheck_status
            d["mandate"], d["mandate_ok"] = ms.replace("_", " ").title(), normalise_debicheck_status(ms) == 'approved'
        elif (d["payment_type"] or "") == "Debit Order":
            d["mandate"], d["mandate_ok"] = "No mandate on file", False
        else:
            d["mandate"], d["mandate_ok"] = "Not a debit order", None
        active = d["member_status"] == "Active"
        d["not_attending"] = bool(active and visits_known(visits, d) and (
            d["days_since_visit"] is None or d["days_since_visit"] > NOT_ATTENDING_DAYS))
        out.append(d)

    summary = {
        "total": len(out),
        "by_status": {s: sum(1 for d in out if d["member_status"] == s) for s in REGISTER_STATUSES},
        "owing": sum(1 for d in out if d["owing"] > 0),
        "owing_total": round(sum(d["owing"] for d in out), 2),
        "no_mandate": sum(1 for d in out if d["mandate_ok"] is False and d["member_status"] == "Active"),
        "not_attending": sum(1 for d in out if d["not_attending"]),
        "reactivate": sum(1 for d in out if d["member_status"] in ("Inactive", "Blocked")),
        "premium_active": round(sum(d["monthly_installment"] or 0 for d in out if d["member_status"] == "Active"), 2),
        "has_visit_data": bool(visits) or any(d["local_last_visit"] for d in out),
    }

    joins = db.execute(f"""
        SELECT strftime('%Y-%m', date(join_date,'-14 days','start of month','+1 month')) AS month, COUNT(*) AS joined,
               COALESCE(SUM(monthly_installment),0) AS premium,
               SUM(CASE WHEN member_status='Active' THEN 1 ELSE 0 END) AS still_active
        FROM members WHERE join_date IS NOT NULL AND substr(join_date,1,10) BETWEEN '{history_start(12)}' AND '{current_cycle()[1].isoformat()}'
        GROUP BY month ORDER BY month DESC
    """).fetchall()
    collected = {r['month']: r['collected'] for r in monthly_totals(db.execute(
        'SELECT * FROM active_collections WHERE substr(collection_date,1,10) BETWEEN ? AND ?',
        (history_start(12),current_cycle()[1].isoformat())).fetchall())}
    joins_by_month = [{**dict(j), "collected": collected.get(j["month"], 0)} for j in joins]

    if status:
        out = [d for d in out if d["member_status"] == status]
    if segment == "not_attending":
        out = [d for d in out if d["not_attending"]]
    elif segment == "owing":
        out = [d for d in out if d["owing"] > 0]
    elif segment == "no_mandate":
        out = [d for d in out if d["mandate_ok"] is False and d["member_status"] == "Active"]
    elif segment == "reactivate":
        out = [d for d in out if d["member_status"] in ("Inactive", "Blocked")]
    if search:
        q = search.lower()
        out = [d for d in out if q in d["name"].lower() or q in (d["contact"] or "").lower()
               or q in (d["member_ref"] or "").lower()]
    return dict(register=out, summary=summary, joins_by_month=joins_by_month,
                visit_error=visit_error, status=status, search=search, segment=segment,
                statuses=REGISTER_STATUSES, not_attending_days=NOT_ATTENDING_DAYS)


def visits_known(visits, d):
    """A member only counts as 'not attending' when we actually have visit data."""
    return bool(visits) or bool(d.get("local_last_visit"))

# ── Custom Report Builder ─────────────────────────────────────────────────────

REPORT_TABLES = {
    'members': {
        'label': 'Members',
        'member_join': None,
        'cols': [
            ('first_name', 'First Name'), ('last_name', 'Last Name'),
            ('member_status', 'Status'), ('package', 'Package'),
            ('join_date', 'Join Date'), ('gender', 'Gender'),
            ('contact', 'Contact'), ('email', 'Email'),
            ('payment_type', 'Payment Type'), ('gym_access_status', 'Gym Access'),
            ('contract_duration', 'Contract Duration'),
            ('monthly_installment', 'Monthly Fee'), ('tariff', 'Tariff'),
            ('source', 'Lead Source'), ('id_number', 'ID Number'),
        ],
    },
    'collections': {
        'label': 'Collections / Payments',
        'member_join': 'member_id',
        'cols': [
            ('collection_date', 'Date'), ('amount_paid', 'Amount Paid'),
            ('outstanding_balance', 'Outstanding'), ('method', 'Payment Method'),
            ('status', 'Status'), ('debit_date', 'Debit Date'), ('bank_name', 'Bank'),
        ],
    },
    'ptp_agreements': {
        'label': 'PTP / Arrears',
        'member_join': 'member_id',
        'cols': [
            ('arrears_amount', 'Arrears Amount'), ('promise_amount', 'Promised Amount'),
            ('promise_date', 'Promise Date'), ('ptp_status', 'Status'),
            ('arrangement_type', 'Arrangement Type'), ('payment_method', 'Payment Method'),
            ('follow_up_date', 'Follow-up Date'), ('created_at', 'Date Created'),
            ('gym_access_status', 'Gym Access'),
        ],
    },
    'queries': {
        'label': 'Queries & Tickets',
        'member_join': 'member_id',
        'cols': [
            ('reference', 'Reference'), ('category', 'Category'),
            ('query_type', 'Query Type'), ('status', 'Status'),
            ('priority', 'Priority'), ('name', 'Name'),
            ('follow_up_date', 'Follow-up Date'), ('created_at', 'Date Created'),
            ('department', 'Department'),
        ],
    },
    'contact_logs': {
        'label': 'Call / Contact Logs',
        'member_join': 'member_id',
        'cols': [
            ('call_code', 'Call Code'), ('notes', 'Notes'), ('created_at', 'Date / Time'),
        ],
    },
}

FILTER_OPS = {
    'eq':           '=',
    'neq':          '!=',
    'gt':           '>',
    'lt':           '<',
    'gte':          '>=',
    'lte':          '<=',
    'like':         'LIKE',
    'not_like':     'NOT LIKE',
    'is_null':      'IS NULL',
    'is_not_null':  'IS NOT NULL',
}

AGG_FUNCS = ['COUNT', 'SUM', 'AVG', 'MIN', 'MAX']


def _build_custom_sql(form):
    table = form.get('table', 'members')
    if table not in REPORT_TABLES:
        return None, None, None, "Invalid table"

    tdef = REPORT_TABLES[table]
    col_map = dict(tdef['cols'])
    allowed = set(col_map.keys())

    join_members = form.get('join_members') == '1' and tdef['member_join'] is not None

    # Group-by / aggregation mode?
    group_by = form.get('group_by', '').strip()
    agg_func = form.get('agg_func', 'COUNT')
    agg_col  = form.get('agg_col', '*').strip()
    do_agg   = bool(group_by and group_by in allowed and agg_func in AGG_FUNCS)

    select_parts = []
    col_labels   = []

    if join_members:
        select_parts.append("members.first_name || ' ' || members.last_name AS member_name")
        col_labels.append('Member Name')
        select_parts.append("members.contact AS member_contact")
        col_labels.append('Contact')

    if do_agg:
        select_parts.append(f"t.{group_by}")
        col_labels.append(col_map[group_by])
        agg_expr = f"t.{agg_col}" if agg_col in allowed else '*'
        select_parts.append(f"{agg_func}({agg_expr}) AS result")
        col_labels.append(agg_func)
        order_sql = "ORDER BY result DESC"
        group_sql  = f"GROUP BY t.{group_by}"
    else:
        raw_cols = form.getlist('cols')
        sel_cols = [c for c in raw_cols if c in allowed] or [c[0] for c in tdef['cols'][:6]]
        for c in sel_cols:
            select_parts.append(f"t.{c}")
            col_labels.append(col_map.get(c, c))
        group_sql = ""
        sort_col = form.get('sort_by', '').strip()
        sort_dir = 'DESC' if form.get('sort_dir') == 'desc' else 'ASC'
        order_sql = f"ORDER BY t.{sort_col} {sort_dir}" if sort_col and sort_col in allowed else ""

    from_sql = f"FROM {table} t"
    if join_members:
        from_sql += f" LEFT JOIN members ON t.{tdef['member_join']} = members.id"

    params, where_parts = [], []
    for i in range(1, 5):
        field = form.get(f'f{i}_field', '').strip()
        op    = form.get(f'f{i}_op',    '').strip()
        val   = form.get(f'f{i}_val',   '').strip()
        if not field or field not in allowed or op not in FILTER_OPS:
            continue
        if op in ('is_null', 'is_not_null'):
            where_parts.append(f"t.{field} {FILTER_OPS[op]}")
        elif val:
            sql_val = f'%{val}%' if op in ('like', 'not_like') else val
            where_parts.append(f"t.{field} {FILTER_OPS[op]} ?")
            params.append(sql_val)

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    try:
        limit = min(int(form.get('limit', '200')), 1000)
    except ValueError:
        limit = 200

    sql = f"SELECT {', '.join(select_parts)} {from_sql} {where_sql} {group_sql} {order_sql} LIMIT {limit}"
    return sql, params, col_labels, None


def _date_range(period):
    today = business_today()
    first, last = current_cycle(today)
    closing = date.fromisoformat(cycle_key(today) + '-01')
    if period == 'this_month':
        start, end = first, last
    elif period == 'last_month':
        start, end = cycle_bounds(shift_month(closing, -1).strftime('%Y-%m'))
    elif period == 'this_quarter':
        q = ((closing.month - 1) // 3) * 3 + 1
        start = cycle_bounds(closing.replace(month=q).strftime('%Y-%m'))[0]
        end = cycle_bounds(closing.replace(month=q+2).strftime('%Y-%m'))[1]
    elif period == 'this_year':
        start = cycle_bounds(closing.replace(month=1).strftime('%Y-%m'))[0]
        end = cycle_bounds(closing.replace(month=12).strftime('%Y-%m'))[1]
    elif period == 'all_time':
        start, end = date(2000, 1, 1), last
    else:
        start, end = cycle_bounds(shift_month(closing, -11).strftime('%Y-%m'))[0], last
    return start.isoformat(), end.isoformat()


def _period_label(period):
    return dict(PERIODS).get(period, 'Last 12 Months')


def _saved_report_form_data(report):
    try:
        config = json.loads(report['config_json'] or '{}')
    except (TypeError, ValueError):
        config = {}
    form_data = MultiDict()
    for key, value in config.items():
        if isinstance(value, list):
            for item in value:
                form_data.add(key, str(item))
        elif value not in (None, ''):
            form_data.add(key, str(value))
    return form_data


def _load_overview(db):
    today_str = date.today().isoformat()
    first, last = current_cycle()
    month_start, month_end = first.isoformat(), last.isoformat()
    s = {}
    member_counts = get_member_counts(db)
    s['active_members']      = member_counts['active']
    s['frozen_members']      = member_counts['by_status'].get('Frozen', 0)
    s['cancelled_members']   = member_counts['by_status'].get('Cancelled', 0)
    s['total_members']       = member_counts['total']
    s['new_this_month']      = db.execute("SELECT COUNT(*) FROM members WHERE substr(join_date,1,10) BETWEEN ? AND ?", (month_start, month_end,)).fetchone()[0]
    s['collected_this_month']= cash_received(db, month_start, month_end)
    from .ptp import bulk_member_arrears
    s['total_outstanding']   = sum(v["total_arrears"] for v in bulk_member_arrears(db).values())
    s['open_queries']        = db.execute("SELECT COUNT(*) FROM queries WHERE status NOT IN ('resolved','closed')").fetchone()[0]
    s['active_ptps']         = db.execute("SELECT COUNT(*) FROM ptp_agreements WHERE ptp_status IN ('pending','partially_paid')").fetchone()[0]
    s['broken_ptps']         = db.execute("SELECT COUNT(*) FROM ptp_agreements WHERE ptp_status='broken'").fetchone()[0]

    monthly = monthly_totals(db.execute(f"""
        SELECT *
        FROM active_collections WHERE substr(collection_date,1,10) BETWEEN '{history_start(6)}' AND '{current_cycle()[1].isoformat()}'
    """).fetchall())
    joins_monthly = db.execute(f"""
        SELECT strftime('%Y-%m', date(join_date,'-14 days','start of month','+1 month')) AS month, COUNT(*) AS cnt
        FROM members WHERE substr(join_date,1,10) BETWEEN '{history_start(6)}' AND '{current_cycle()[1].isoformat()}'
        GROUP BY month ORDER BY month DESC
    """).fetchall()
    top_packages = db.execute("""
        SELECT package, COUNT(*) AS cnt FROM members
        WHERE member_status='Active' AND package IS NOT NULL AND package!=''
        GROUP BY package ORDER BY cnt DESC LIMIT 5
    """).fetchall()
    query_cats = db.execute("""
        SELECT category, COUNT(*) AS cnt FROM queries
        WHERE DATE(created_at) BETWEEN ? AND ? GROUP BY category ORDER BY cnt DESC LIMIT 6
    """, (month_start, month_end)).fetchall()

    return dict(stats=s, monthly=monthly, joins_monthly=joins_monthly,
                top_packages=top_packages, query_cats=query_cats)


def _load_membership(db, period):
    from_date, to_date = _date_range(period)
    status_breakdown = db.execute("SELECT member_status, COUNT(*) AS cnt FROM members GROUP BY member_status ORDER BY cnt DESC").fetchall()
    new_by_month = db.execute("SELECT strftime('%Y-%m', date(join_date,'-14 days','start of month','+1 month')) AS month, COUNT(*) AS cnt FROM members WHERE DATE(join_date) BETWEEN ? AND ? GROUP BY month ORDER BY month DESC", (from_date, to_date)).fetchall()
    package_breakdown = db.execute("SELECT package, member_status, COUNT(*) AS cnt FROM members WHERE package IS NOT NULL AND package!='' GROUP BY package, member_status ORDER BY cnt DESC").fetchall()
    payment_type_breakdown = db.execute("SELECT payment_type, COUNT(*) AS cnt FROM members WHERE payment_type IS NOT NULL AND payment_type!='' GROUP BY payment_type ORDER BY cnt DESC").fetchall()
    gender_breakdown = db.execute("SELECT gender, COUNT(*) AS cnt FROM members WHERE gender IS NOT NULL AND gender!='' GROUP BY gender ORDER BY cnt DESC").fetchall()
    consultant_breakdown = db.execute("""
        SELECT u.full_name AS consultant, COUNT(*) AS cnt,
               SUM(CASE WHEN m.member_status='Active' THEN 1 ELSE 0 END) AS active_cnt
        FROM members m LEFT JOIN users u ON m.uploaded_by_id = u.id
        GROUP BY m.uploaded_by_id ORDER BY cnt DESC
    """).fetchall()
    contract_duration = db.execute("SELECT contract_duration, COUNT(*) AS cnt FROM members WHERE contract_duration IS NOT NULL GROUP BY contract_duration ORDER BY cnt DESC").fetchall()
    source_breakdown = db.execute("SELECT source, COUNT(*) AS cnt FROM members WHERE source IS NOT NULL AND source!='' GROUP BY source ORDER BY cnt DESC LIMIT 10").fetchall()
    recently_joined = db.execute("""
        SELECT m.id, m.first_name, m.last_name, m.member_status, m.package,
               m.join_date, m.contact, u.full_name AS consultant
        FROM members m LEFT JOIN users u ON m.uploaded_by_id = u.id
        WHERE DATE(m.join_date) BETWEEN ? AND ? ORDER BY m.join_date DESC LIMIT 50
    """, (from_date, to_date)).fetchall()
    total_members = get_member_counts(db)['total']
    return dict(from_date=from_date, to_date=to_date,
                status_breakdown=status_breakdown, new_by_month=new_by_month,
                package_breakdown=package_breakdown, payment_type_breakdown=payment_type_breakdown,
                gender_breakdown=gender_breakdown, consultant_breakdown=consultant_breakdown,
                contract_duration=contract_duration, source_breakdown=source_breakdown,
                recently_joined=recently_joined, total_members=total_members)


def _load_finance(db, period):
    from_date, to_date = _date_range(period)
    from collections import defaultdict
    from decimal import Decimal
    rows = db.execute('''SELECT c.*,m.package FROM active_collections c LEFT JOIN members m ON m.id=c.member_id
        WHERE substr(c.collection_date,1,10) BETWEEN ? AND ?''',(from_date,to_date)).fetchall()
    monthly_collections = monthly_totals(rows)
    methods, statuses, packages = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        statuses[row['status']].append(row)
        if cash_amount(row):
            methods[row['method']].append(row)
            if row['package']:
                packages[row['package']].append(row)
    cash_sum = lambda entries: float(sum((cash_amount(r) for r in entries), Decimal(0)).quantize(Decimal('0.01')))
    payment_methods = sorted((dict(method=key,cnt=len(entries),total=cash_sum(entries)) for key,entries in methods.items()),key=lambda r:r['total'],reverse=True)
    status_breakdown = sorted((dict(status=key,cnt=len(entries),total=cash_sum(entries)) for key,entries in statuses.items()),key=lambda r:r['cnt'],reverse=True)

    from .ptp import bulk_member_arrears, ARREARS_RESTRICTION_MONTHS
    member_rows = {
        r["id"]: r for r in db.execute(
            """SELECT id, first_name, last_name, package, member_status, contact
               FROM members WHERE id IN (SELECT DISTINCT member_id FROM active_collections)"""
        ).fetchall()
    }
    top_outstanding = []
    for mid, arrears in bulk_member_arrears(db, member_rows.keys()).items():
        if arrears.get("months_in_arrears", 0) <= ARREARS_RESTRICTION_MONTHS:
            continue
        m = member_rows[mid]
        top_outstanding.append({
            "id": mid, "first_name": m["first_name"], "last_name": m["last_name"],
            "package": m["package"], "member_status": m["member_status"],
            "contact": m["contact"], "net_owed": arrears["total_arrears"],
        })
    top_outstanding.sort(key=lambda r: r["net_owed"], reverse=True)
    top_outstanding = top_outstanding[:25]
    totals = financial_totals(rows)
    revenue_by_package = sorted((dict(package=key,collected=cash_sum(entries),members=len({r['member_id'] for r in entries})) for key,entries in packages.items()),key=lambda r:r['collected'],reverse=True)
    return dict(from_date=from_date, to_date=to_date,
                monthly_collections=monthly_collections, payment_methods=payment_methods,
                fin_status_breakdown=status_breakdown, top_outstanding=top_outstanding,
                totals=totals, revenue_by_package=revenue_by_package)


def _load_arrears(db):
    today_str = date.today().isoformat()
    ptp_status_breakdown = db.execute("SELECT ptp_status, COUNT(*) AS cnt, SUM(promise_amount) AS total_promised, SUM(arrears_amount) AS total_arrears FROM ptp_agreements GROUP BY ptp_status ORDER BY cnt DESC").fetchall()
    ptp_by_arrangement = db.execute("SELECT arrangement_type, COUNT(*) AS cnt, SUM(CASE WHEN ptp_status='paid' THEN 1 ELSE 0 END) AS paid_cnt FROM ptp_agreements GROUP BY arrangement_type ORDER BY cnt DESC").fetchall()
    ptp_by_payment_method = db.execute("SELECT payment_method, COUNT(*) AS cnt, SUM(promise_amount) AS total FROM ptp_agreements GROUP BY payment_method ORDER BY total DESC").fetchall()
    broken_ptps = db.execute("""
        SELECT p.*, m.first_name, m.last_name, m.contact, m.package, uc.full_name AS created_by_name
        FROM ptp_agreements p JOIN members m ON p.member_id=m.id LEFT JOIN users uc ON p.created_by=uc.id
        WHERE p.ptp_status='broken' ORDER BY p.promise_date DESC LIMIT 30
    """).fetchall()
    overdue_ptps = db.execute("""
        SELECT p.*, m.first_name, m.last_name, m.contact, m.package
        FROM ptp_agreements p JOIN members m ON p.member_id=m.id
        WHERE p.ptp_status IN ('pending','partially_paid') AND p.promise_date < ?
        ORDER BY p.promise_date ASC LIMIT 30
    """, (today_str,)).fetchall()
    top_arrears = db.execute("""
        SELECT p.member_id, m.first_name, m.last_name, m.contact, m.package, m.member_status, m.gym_access_status,
               MAX(p.arrears_amount) AS arrears, COUNT(p.id) AS ptp_count,
               SUM(CASE WHEN p.ptp_status='broken' THEN 1 ELSE 0 END) AS broken_count
        FROM ptp_agreements p JOIN members m ON p.member_id=m.id
        GROUP BY p.member_id ORDER BY arrears DESC LIMIT 25
    """).fetchall()
    access_blocked = db.execute("SELECT m.id, m.first_name, m.last_name, m.contact, m.package, m.gym_access_status, m.access_blocked_until FROM members m WHERE m.gym_access_status IN ('blocked','temp_unblocked') ORDER BY m.last_name").fetchall()
    ptp_totals = db.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN ptp_status='paid' THEN 1 ELSE 0 END) AS paid,
               SUM(CASE WHEN ptp_status='broken' THEN 1 ELSE 0 END) AS broken,
               SUM(CASE WHEN ptp_status IN ('pending','partially_paid') THEN 1 ELSE 0 END) AS active,
               COALESCE(SUM(promise_amount),0) AS total_promised,
               COALESCE(SUM(arrears_amount),0) AS total_arrears
        FROM ptp_agreements
    """).fetchone()
    contact_log_summary = db.execute(f"SELECT call_code, COUNT(*) AS cnt FROM contact_logs WHERE substr(created_at,1,10) BETWEEN '{current_cycle()[0].isoformat()}' AND '{current_cycle()[1].isoformat()}' GROUP BY call_code ORDER BY cnt DESC").fetchall()
    ptp_monthly = db.execute(f"""
        SELECT strftime('%Y-%m', date(created_at,'-14 days','start of month','+1 month')) AS month, COUNT(*) AS created,
               SUM(CASE WHEN ptp_status='paid' THEN 1 ELSE 0 END) AS paid,
               SUM(CASE WHEN ptp_status='broken' THEN 1 ELSE 0 END) AS broken
        FROM ptp_agreements WHERE substr(created_at,1,10) BETWEEN '{history_start(6)}' AND '{current_cycle()[1].isoformat()}'
        GROUP BY month ORDER BY month DESC
    """).fetchall()
    return dict(ptp_status_breakdown=ptp_status_breakdown, ptp_by_arrangement=ptp_by_arrangement,
                ptp_by_payment_method=ptp_by_payment_method, broken_ptps=broken_ptps,
                overdue_ptps=overdue_ptps, top_arrears=top_arrears, access_blocked=access_blocked,
                ptp_totals=ptp_totals, contact_log_summary=contact_log_summary, ptp_monthly=ptp_monthly)


def _load_queries(db, period):
    from_date, to_date = _date_range(period)
    today_str = date.today().isoformat()
    status_breakdown = db.execute("SELECT status, COUNT(*) AS cnt FROM queries WHERE DATE(created_at) BETWEEN ? AND ? GROUP BY status ORDER BY cnt DESC", (from_date, to_date)).fetchall()
    category_breakdown = db.execute("""
        SELECT category, COUNT(*) AS cnt,
               SUM(CASE WHEN status NOT IN ('resolved','closed') THEN 1 ELSE 0 END) AS open_cnt,
               SUM(CASE WHEN priority IN ('high','urgent') THEN 1 ELSE 0 END) AS urgent_cnt
        FROM queries WHERE DATE(created_at) BETWEEN ? AND ?
        GROUP BY category ORDER BY cnt DESC
    """, (from_date, to_date)).fetchall()
    priority_breakdown = db.execute("SELECT priority, COUNT(*) AS cnt FROM queries WHERE DATE(created_at) BETWEEN ? AND ? GROUP BY priority ORDER BY cnt DESC", (from_date, to_date)).fetchall()
    assigned_breakdown = db.execute("""
        SELECT u.full_name AS staff, COUNT(*) AS cnt,
               SUM(CASE WHEN q.status IN ('resolved','closed') THEN 1 ELSE 0 END) AS resolved_cnt
        FROM queries q LEFT JOIN users u ON q.assigned_to=u.id
        WHERE DATE(q.created_at) BETWEEN ? AND ?
        GROUP BY q.assigned_to ORDER BY cnt DESC
    """, (from_date, to_date)).fetchall()
    overdue = db.execute("""
        SELECT q.*, m.first_name||' '||m.last_name AS member_name, u.full_name AS assigned_to_name
        FROM queries q LEFT JOIN members m ON q.member_id=m.id LEFT JOIN users u ON q.assigned_to=u.id
        WHERE q.follow_up_date < ? AND q.status NOT IN ('resolved','closed')
        ORDER BY q.follow_up_date ASC LIMIT 20
    """, (today_str,)).fetchall()
    recent = db.execute("""
        SELECT q.*, m.first_name||' '||m.last_name AS member_name, u.full_name AS assigned_to_name
        FROM queries q LEFT JOIN members m ON q.member_id=m.id LEFT JOIN users u ON q.assigned_to=u.id
        WHERE DATE(q.created_at) BETWEEN ? AND ?
        ORDER BY q.created_at DESC LIMIT 50
    """, (from_date, to_date)).fetchall()
    monthly_trend = db.execute(f"""
        SELECT strftime('%Y-%m', date(created_at,'-14 days','start of month','+1 month')) AS month, COUNT(*) AS cnt,
               SUM(CASE WHEN status IN ('resolved','closed') THEN 1 ELSE 0 END) AS resolved
        FROM queries WHERE substr(created_at,1,10) BETWEEN '{history_start(6)}' AND '{current_cycle()[1].isoformat()}'
        GROUP BY month ORDER BY month DESC
    """).fetchall()
    totals = db.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_cnt,
               SUM(CASE WHEN status='escalated' THEN 1 ELSE 0 END) AS escalated_cnt,
               SUM(CASE WHEN priority='urgent' THEN 1 ELSE 0 END) AS urgent_cnt,
               SUM(CASE WHEN status IN ('resolved','closed') THEN 1 ELSE 0 END) AS resolved_cnt
        FROM queries WHERE DATE(created_at) BETWEEN ? AND ?
    """, (from_date, to_date)).fetchone()
    return dict(from_date=from_date, to_date=to_date,
                q_status_breakdown=status_breakdown, category_breakdown=category_breakdown,
                priority_breakdown=priority_breakdown, assigned_breakdown=assigned_breakdown,
                q_overdue=overdue, q_recent=recent, monthly_trend=monthly_trend, q_totals=totals)


@reports_bp.route('/')
@reports_bp.route('/overview')
@permission_required("reports_module")
@dashboard_only('admin')
def index():
    db = get_db()
    tab = request.args.get('tab', 'overview')
    if tab not in VALID_TABS:
        tab = 'overview'
    period = request.args.get('period', 'this_month')

    ctx = dict(tab=tab, period=period,
               period_label=_period_label(period), periods=PERIODS,
               reporting_from=_date_range(period)[0], reporting_to=_date_range(period)[1],
               today=date.today().isoformat())

    if tab == 'overview':
        ctx.update(_load_overview(db))
    elif tab == 'membership':
        ctx.update(_load_membership(db, period))
    elif tab == 'finance':
        ctx.update(_load_finance(db, period))
    elif tab == 'arrears':
        ctx.update(_load_arrears(db))
    elif tab == 'queries':
        ctx.update(_load_queries(db, period))
    elif tab == 'custom':
        saved_reports = db.execute(
            "SELECT id, name, description FROM report_definitions WHERE active=1 ORDER BY name"
        ).fetchall()
        saved_report = None
        form_data = None
        try:
            saved_report_id = int(request.args.get('saved_report', '0'))
        except ValueError:
            saved_report_id = 0
        if saved_report_id:
            saved_report = db.execute(
                "SELECT * FROM report_definitions WHERE id=? AND active=1", (saved_report_id,)
            ).fetchone()
            if saved_report:
                form_data = _saved_report_form_data(saved_report)
        ctx.update(report_tables=REPORT_TABLES, filter_ops=FILTER_OPS, agg_funcs=AGG_FUNCS,
                   report_tables_json={k: {'cols': list(v['cols'])} for k, v in REPORT_TABLES.items()},
                   custom_results=None, custom_cols=None, custom_sql=None, custom_error=None,
                   form_data=form_data, saved_reports=saved_reports, saved_report=saved_report)

    return render_template('reports/index.html', **ctx)


@reports_bp.route('/membership-report')
@permission_required("reports_module")
def membership_report():
    import csv, io
    from flask import Response
    db = get_db()
    ctx = _load_register(db, request.args.get('status', ''), request.args.get('q', '').strip(),
                         request.args.get('segment', ''))
    if request.args.get('format') == 'csv':
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['Member', 'Member ref', 'ID number', 'Premium', 'Signed by', 'Last visit',
                    'Package', 'Status', 'Last paid', 'Owing', 'Mandate'])
        for d in ctx['register']:
            w.writerow([d['name'], d['member_ref'], d['id_masked'], d['monthly_installment'] or 0,
                        d['signed_by'] or '', d['last_visit'] or '', d['package'] or '',
                        d['member_status'], d['last_paid'] or '', d['owing'], d['mandate']])
        return Response(buf.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition': 'attachment; filename=membership-report.csv'})
    return render_template('reports/membership_register.html', tab='register', **ctx)


@reports_bp.route('/consultant-book')
@permission_required("reports_module")
@dashboard_only('admin')
def consultant_book():
    from .consultant_book import load_consultant_book

    segment = request.args.get('segment', 'signed')
    if segment not in ('signed', 'lost', 'active'):
        segment = 'signed'
    ctx = load_consultant_book(get_db(), request.args.get('consultant'), segment)
    return render_template('reports/consultant_book.html', **ctx)


@reports_bp.route('/analytics/<report>')
@permission_required("reports_module")
@dashboard_only('admin')
def analytics_report(report):
    import csv
    import io
    from flask import Response, current_app, g
    from . import analytics
    from .staff_kpi import selected_day

    if report not in analytics.TITLES:
        abort(404)
    db = get_db()
    start, end = analytics.reporting_window(request.args)
    member_ref = request.args.get("member", "").strip()
    review_status = request.args.get("review_status", "")
    if review_status not in ("Complete", "Incomplete", "Review required"):
        review_status = ""
    consultant = request.args.get("consultant", "").strip()
    source = request.args.get("source", "").strip()
    kpi_day = min(selected_day(request.args.get("day")), business_today()).isoformat()
    rate = analytics.commission_rate(request.args.get("rate"))
    if report in ("master", "compliance"):
        tables = analytics.member_tables(db, report, start, end, member_ref, review_status)
    elif report == "reception":
        tables = analytics.reception_tables(db, start, end, member_ref)
    elif report == "funnel":
        tables = analytics.funnel_tables(db, start, end, consultant, source)
    elif report == "commission":
        tables = analytics.commission_tables(db, start, end, rate)
    else:
        tables = analytics.kpi_tables(db, start, end, kpi_day)
    if request.args.get("format") == "csv":
        index = request.args.get("table", 0, type=int)
        if not 0 <= index < len(tables):
            abort(404)
        table = tables[index]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(table["headers"])
        for row in table["rows"]:
            writer.writerow([
                "'" + cell if isinstance(cell, str) and (
                    cell.startswith(("\t", "\r", "\n")) or cell.lstrip().startswith(("=", "+", "-", "@"))
                ) else cell for cell in row
            ])
        return Response(output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={report}-{index + 1}.csv",
                                 "Cache-Control": "private, no-store"})
    filters = {"from": start.isoformat(), "to": end.isoformat()}
    if member_ref:
        filters["member"] = member_ref
    if report == "compliance" and review_status:
        filters["review_status"] = review_status
    if report == "funnel":
        filters.update(consultant=consultant, source=source)
    if report == "kpis":
        filters["day"] = kpi_day
    if rate is not None and report == "commission":
        filters["rate"] = str(rate)
    page_size = 100
    selected_table = request.args.get("table", type=int)
    for index, table in enumerate(tables):
        count = len(table["rows"])
        page_count = max(1, (count + page_size - 1) // page_size)
        page = request.args.get("page", 1, type=int) if index == selected_table else 1
        page = min(max(page, 1), page_count)
        offset = (page - 1) * page_size
        table.update(count=count, page=page, page_count=page_count,
                     rows=table["rows"][offset:offset + page_size])
    show_activation_audit = bool(
        report == "compliance" and current_app.config.get("ITENSITY_AUTO_PUSH_FROM")
        and g.get("tenant_slug") == current_app.config.get("ITENSITY_TENANT_SLUG")
    )
    activation_audit = None
    if show_activation_audit:
        from scripts.audit_sales_activation import latest_exceptions
        activation_audit = latest_exceptions(current_app, g.tenant_slug)
    return render_template("reports/analytics.html", report=report, title=analytics.TITLES[report],
                           tables=tables, filters=filters, start=start, end=end,
                           member_ref=member_ref, kpi_day=kpi_day, rate=rate, today=business_today(),
                           show_activation_audit=show_activation_audit, activation_audit=activation_audit,
                           invalid_rate=bool(request.args.get("rate")) and rate is None)


@reports_bp.route('/run-custom', methods=['POST'])
@permission_required("reports_module")
@dashboard_only('admin')
def run_custom():
    db  = get_db()
    frm = request.form
    sql, params, col_labels, err = _build_custom_sql(frm)

    ctx = dict(tab='custom', period='last_12_months',
               period_label='Last 12 Months', periods=PERIODS,
               today=date.today().isoformat(),
               report_tables=REPORT_TABLES, filter_ops=FILTER_OPS, agg_funcs=AGG_FUNCS,
               report_tables_json={k: {'cols': list(v['cols'])} for k, v in REPORT_TABLES.items()},
               form_data=frm,
               saved_reports=db.execute(
                   "SELECT id, name, description FROM report_definitions WHERE active=1 ORDER BY name"
               ).fetchall(),
               saved_report=None)

    if err:
        ctx.update(custom_results=None, custom_cols=None, custom_sql=None, custom_error=err)
        return render_template('reports/index.html', **ctx)

    try:
        raw_rows = db.execute(sql, params).fetchall()
        sel_cols = set(frm.getlist('cols'))
        needs_id_decrypt = 'id_number' in sel_cols
        rows = []
        for r in raw_rows:
            rd = dict(r)
            if needs_id_decrypt and 'id_number' in rd:
                rd['id_number'] = decrypt(rd['id_number'])
            rows.append(rd)
        ctx.update(custom_results=rows, custom_cols=col_labels,
                   custom_sql=sql, custom_error=None)
    except Exception as e:
        ctx.update(custom_results=None, custom_cols=None,
                   custom_sql=sql, custom_error=str(e))

    return render_template('reports/index.html', **ctx)
