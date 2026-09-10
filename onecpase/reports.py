import json

from flask import Blueprint, render_template, request
from datetime import date, timedelta
from werkzeug.datastructures import MultiDict
from .auth import permission_required
from .database import get_db
from .encryption import decrypt
from .member_counts import get_member_counts

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

PERIODS = [
    ('this_month',     'This Month'),
    ('last_month',     'Last Month'),
    ('this_quarter',   'This Quarter'),
    ('this_year',      'This Year'),
    ('last_12_months', 'Last 12 Months'),
    ('all_time',       'All Time'),
]

VALID_TABS = ('overview', 'membership', 'finance', 'arrears', 'queries', 'custom')

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
    today = date.today()
    if period == 'this_month':
        return today.replace(day=1).isoformat(), today.isoformat()
    elif period == 'last_month':
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1).isoformat(), last.isoformat()
    elif period == 'this_quarter':
        q = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q, day=1).isoformat(), today.isoformat()
    elif period == 'this_year':
        return today.replace(month=1, day=1).isoformat(), today.isoformat()
    elif period == 'all_time':
        return '2000-01-01', today.isoformat()
    else:
        return (today - timedelta(days=365)).isoformat(), today.isoformat()


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
    month_start = date.today().replace(day=1).isoformat()
    s = {}
    member_counts = get_member_counts(db)
    s['active_members']      = member_counts['active']
    s['frozen_members']      = member_counts['by_status'].get('Frozen', 0)
    s['cancelled_members']   = member_counts['by_status'].get('Cancelled', 0)
    s['total_members']       = member_counts['total']
    s['new_this_month']      = db.execute("SELECT COUNT(*) FROM members WHERE join_date >= ?", (month_start,)).fetchone()[0]
    s['collected_this_month']= db.execute("SELECT COALESCE(SUM(amount_paid),0) FROM collections WHERE collection_date >= ?", (month_start,)).fetchone()[0]
    from .ptp import bulk_member_arrears
    s['total_outstanding']   = sum(v["total_arrears"] for v in bulk_member_arrears(db).values())
    s['open_queries']        = db.execute("SELECT COUNT(*) FROM queries WHERE status NOT IN ('resolved','closed')").fetchone()[0]
    s['active_ptps']         = db.execute("SELECT COUNT(*) FROM ptp_agreements WHERE ptp_status IN ('pending','partially_paid')").fetchone()[0]
    s['broken_ptps']         = db.execute("SELECT COUNT(*) FROM ptp_agreements WHERE ptp_status='broken'").fetchone()[0]

    monthly = db.execute("""
        SELECT strftime('%Y-%m', collection_date) AS month,
               SUM(amount_paid) AS collected, SUM(outstanding_balance) AS outstanding, COUNT(*) AS cnt
        FROM collections WHERE collection_date >= date('now','-6 months')
        GROUP BY month ORDER BY month DESC
    """).fetchall()
    joins_monthly = db.execute("""
        SELECT strftime('%Y-%m', join_date) AS month, COUNT(*) AS cnt
        FROM members WHERE join_date >= date('now','-6 months')
        GROUP BY month ORDER BY month DESC
    """).fetchall()
    top_packages = db.execute("""
        SELECT package, COUNT(*) AS cnt FROM members
        WHERE member_status='Active' AND package IS NOT NULL AND package!=''
        GROUP BY package ORDER BY cnt DESC LIMIT 5
    """).fetchall()
    query_cats = db.execute("""
        SELECT category, COUNT(*) AS cnt FROM queries
        WHERE DATE(created_at) >= ? GROUP BY category ORDER BY cnt DESC LIMIT 6
    """, (month_start,)).fetchall()

    return dict(stats=s, monthly=monthly, joins_monthly=joins_monthly,
                top_packages=top_packages, query_cats=query_cats)


def _load_membership(db, period):
    from_date, to_date = _date_range(period)
    status_breakdown = db.execute("SELECT member_status, COUNT(*) AS cnt FROM members GROUP BY member_status ORDER BY cnt DESC").fetchall()
    new_by_month = db.execute("SELECT strftime('%Y-%m', join_date) AS month, COUNT(*) AS cnt FROM members WHERE join_date BETWEEN ? AND ? GROUP BY month ORDER BY month DESC", (from_date, to_date)).fetchall()
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
        WHERE m.join_date BETWEEN ? AND ? ORDER BY m.join_date DESC LIMIT 50
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
    monthly_collections = db.execute("""
        SELECT strftime('%Y-%m', collection_date) AS month, COUNT(*) AS cnt,
               SUM(amount_paid) AS collected, SUM(outstanding_balance) AS outstanding,
               SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END) AS paid_cnt,
               SUM(CASE WHEN status='partial' THEN 1 ELSE 0 END) AS partial_cnt,
               SUM(CASE WHEN status IN ('pending','failed') THEN 1 ELSE 0 END) AS failed_cnt
        FROM collections WHERE collection_date BETWEEN ? AND ?
        GROUP BY month ORDER BY month DESC
    """, (from_date, to_date)).fetchall()
    payment_methods = db.execute("SELECT method, COUNT(*) AS cnt, SUM(amount_paid) AS total FROM collections WHERE collection_date BETWEEN ? AND ? GROUP BY method ORDER BY total DESC", (from_date, to_date)).fetchall()
    status_breakdown = db.execute("SELECT status, COUNT(*) AS cnt, SUM(amount_paid) AS total FROM collections WHERE collection_date BETWEEN ? AND ? GROUP BY status ORDER BY cnt DESC", (from_date, to_date)).fetchall()

    from .ptp import bulk_member_arrears, ARREARS_RESTRICTION_MONTHS
    member_rows = {
        r["id"]: r for r in db.execute(
            """SELECT id, first_name, last_name, package, member_status, contact
               FROM members WHERE id IN (SELECT DISTINCT member_id FROM collections)"""
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
    totals = db.execute("SELECT COALESCE(SUM(amount_paid),0) AS total_collected, COALESCE(SUM(outstanding_balance),0) AS total_billed, COUNT(*) AS total_records FROM collections WHERE collection_date BETWEEN ? AND ?", (from_date, to_date)).fetchone()
    revenue_by_package = db.execute("""
        SELECT m.package, SUM(c.amount_paid) AS collected, COUNT(DISTINCT c.member_id) AS members
        FROM collections c JOIN members m ON c.member_id = m.id
        WHERE c.collection_date BETWEEN ? AND ? AND m.package IS NOT NULL AND m.package!=''
        GROUP BY m.package ORDER BY collected DESC
    """, (from_date, to_date)).fetchall()
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
    contact_log_summary = db.execute("SELECT call_code, COUNT(*) AS cnt FROM contact_logs WHERE created_at >= date('now','-30 days') GROUP BY call_code ORDER BY cnt DESC").fetchall()
    ptp_monthly = db.execute("""
        SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS created,
               SUM(CASE WHEN ptp_status='paid' THEN 1 ELSE 0 END) AS paid,
               SUM(CASE WHEN ptp_status='broken' THEN 1 ELSE 0 END) AS broken
        FROM ptp_agreements WHERE created_at >= date('now','-6 months')
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
    monthly_trend = db.execute("""
        SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS cnt,
               SUM(CASE WHEN status IN ('resolved','closed') THEN 1 ELSE 0 END) AS resolved
        FROM queries WHERE created_at >= date('now','-6 months')
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
def index():
    db = get_db()
    tab = request.args.get('tab', 'overview')
    if tab not in VALID_TABS:
        tab = 'overview'
    period = request.args.get('period', 'last_12_months')

    ctx = dict(tab=tab, period=period,
               period_label=_period_label(period), periods=PERIODS,
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


@reports_bp.route('/run-custom', methods=['POST'])
@permission_required("reports_module")
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
