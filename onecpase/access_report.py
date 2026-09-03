from flask import Blueprint, render_template, request, session, redirect, url_for, g, abort, current_app
from datetime import datetime

access_report_bp = Blueprint('access_report', __name__, url_prefix='/access-report')

_CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=tcp:RADE,1433;"
    "DATABASE=ELEV8_;"
    "Trusted_Connection=yes;"
    "Encrypt=no;"
    "TrustServerCertificate=yes;"
)


def _elev8_conn():
    import pyodbc
    return pyodbc.connect(_CONN_STR, timeout=8)


# ── All visits (last_visited_report) ────────────────────────────────────────
_SQL_VISITS = """\
SELECT
    lv.name_surname,
    lv.cell,
    TRY_CONVERT(datetime2(0), lv.last_visited)              AS last_visited,
    DATEDIFF(day,
        TRY_CONVERT(datetime2(0), lv.last_visited),
        GETDATE())                                          AS days_ago,
    CASE
        WHEN DATEPART(hour, TRY_CONVERT(datetime2(0), lv.last_visited)) BETWEEN 5 AND 12
             THEN 'Morning'
        WHEN DATEPART(hour, TRY_CONVERT(datetime2(0), lv.last_visited)) BETWEEN 13 AND 20
             THEN 'Afternoon'
        ELSE 'Other'
    END                                                     AS shift
FROM dbo.last_visited_report lv
WHERE TRY_CONVERT(datetime2(0), lv.last_visited) >= DATEADD(day, -DAYS_VAL, GETDATE())
  AND NULLIF(LTRIM(RTRIM(lv.last_visited)), '') IS NOT NULL
  SEARCH_CLAUSE
ORDER BY TRY_CONVERT(datetime2(0), lv.last_visited) DESC
"""

# ── Owing visitors (Access_Owing_7Days equivalent) ─────────────────────────
_SQL_OWING = """\
;WITH
raw_member_access AS (
    SELECT
        NULLIF(LTRIM(RTRIM(lv.[name_surname])), '')            AS lv_name,
        RIGHT(NULLIF(LTRIM(RTRIM(lv.[cell])), ''), 9)          AS phone_last_9,
        TRY_CONVERT(datetime2(0), lv.[last_visited])           AS last_visited_at
    FROM dbo.last_visited_report AS lv
    WHERE TRY_CONVERT(datetime2(0), lv.[last_visited]) >= DATEADD(day, -DAYS_VAL, GETDATE())
      AND NULLIF(LTRIM(RTRIM(lv.[cell])), '') IS NOT NULL
),
member_access AS (
    SELECT
        lv_name, phone_last_9,
        MAX(last_visited_at)  AS last_visited_at,
        CASE
            WHEN DATEPART(hour, MAX(last_visited_at)) BETWEEN 5 AND 12  THEN 'Morning'
            WHEN DATEPART(hour, MAX(last_visited_at)) BETWEEN 13 AND 20 THEN 'Afternoon'
            ELSE 'Outside Shifts'
        END AS last_visit_shift
    FROM raw_member_access
    GROUP BY lv_name, phone_last_9
),
total_visits AS (
    SELECT
        UPPER(NULLIF(LTRIM(RTRIM(tv.[NAME_SURNAME])), ''))   AS tv_name,
        MAX(TRY_CAST(NULLIF(LTRIM(RTRIM(tv.[TOTAL_VISITS])),'') AS INT)) AS total_visits
    FROM dbo.total_visits_report AS tv
    WHERE NULLIF(LTRIM(RTRIM(tv.[NAME_SURNAME])), '') IS NOT NULL
    GROUP BY UPPER(NULLIF(LTRIM(RTRIM(tv.[NAME_SURNAME])), ''))
),
mandate_status AS (
    SELECT
        COALESCE(
            CASE
                WHEN LEN(LTRIM(RTRIM(mr.[client_reference]))) IN (6,7)
                     THEN LTRIM(RTRIM(mr.[client_reference]))
                WHEN CHARINDEX('-', mr.[client_reference]) = 8
                     THEN LEFT(mr.[client_reference], 7)
                WHEN CHARINDEX('-', mr.[client_reference]) = 7
                     THEN LEFT(mr.[client_reference], 6)
            END,
            m.[unique_ref]
        ) AS client_ref_clean,
        MAX(CASE WHEN NULLIF(LTRIM(RTRIM(mr.[mandate_status_code])),'') IS NULL
                   OR LTRIM(RTRIM(mr.[mandate_status_code])) = '500105'
                 THEN 1 ELSE 0 END) AS has_authorised,
        MAX(CASE WHEN NULLIF(LTRIM(RTRIM(mr.[mandate_status_code])),'') IS NOT NULL
                  AND LTRIM(RTRIM(mr.[mandate_status_code])) <> '500105'
                 THEN 1 ELSE 0 END) AS has_rejected
    FROM dbo.mandate_report AS mr
    LEFT JOIN dbo.members AS m
           ON LTRIM(RTRIM(mr.[debtor_id])) = LTRIM(RTRIM(m.[id_number]))
    GROUP BY COALESCE(
        CASE
            WHEN LEN(LTRIM(RTRIM(mr.[client_reference]))) IN (6,7)
                 THEN LTRIM(RTRIM(mr.[client_reference]))
            WHEN CHARINDEX('-', mr.[client_reference]) = 8
                 THEN LEFT(mr.[client_reference], 7)
            WHEN CHARINDEX('-', mr.[client_reference]) = 7
                 THEN LEFT(mr.[client_reference], 6)
        END,
        m.[unique_ref]
    )
),
call_activity AS (
    SELECT
        RIGHT(REPLACE(COALESCE(c.[call_to],''), '>', ''), 9)  AS phone_last_9,
        COUNT(*)                                               AS calls_count,
        SUM(CASE WHEN UPPER(COALESCE(c.[status],'')) = 'ANSWERED'
                 THEN 1 ELSE 0 END)                           AS answered_calls,
        MAX(TRY_CONVERT(datetime2(0), c.[call_leg_time]))      AS last_call_at
    FROM dbo.calls AS c
    WHERE TRY_CONVERT(datetime2(0), c.[call_leg_time]) >= DATEADD(day, -DAYS_VAL, GETDATE())
      AND NULLIF(c.[call_to], '') IS NOT NULL
    GROUP BY RIGHT(REPLACE(COALESCE(c.[call_to],''), '>', ''), 9)
),
ptp AS (
    SELECT
        LTRIM(RTRIM(p.[account_id]))                          AS account_id,
        MAX(TRY_CONVERT(DATE, p.[ptp_date]))                  AS latest_ptp_date,
        MAX(TRY_CONVERT(DECIMAL(18,2), p.[amount]))           AS ptp_amount
    FROM dbo.ptp_activity AS p
    WHERE NULLIF(LTRIM(RTRIM(p.[account_id])),'') IS NOT NULL
    GROUP BY LTRIM(RTRIM(p.[account_id]))
),
itensity_payments AS (
    SELECT
        LTRIM(RTRIM(t.[ref]))                                 AS account_id,
        MAX(TRY_CONVERT(DATE, t.[date]))                      AS latest_payment_date
    FROM dbo.itensity_transactions_report AS t
    WHERE LTRIM(RTRIM(t.[type])) IN (
              'Card Payment','Cash Payment',
              'Debit Order Payment','EFT Payment')
      AND NULLIF(LTRIM(RTRIM(t.[ref])),'') IS NOT NULL
    GROUP BY LTRIM(RTRIM(t.[ref]))
)
SELECT
    m.[unique_ref]                                                                AS Unique_Ref,
    rd.[member_name] + ' ' + rd.[member_surname]                                 AS Member_Name,
    m.[cellphone]                                                                 AS Cellphone,
    rd.[tariff_name]                                                              AS Package,
    rd.[member_status]                                                            AS Member_Status,
    TRY_CONVERT(DECIMAL(18,2),
        REPLACE(NULLIF(rd.[outstanding_balance],''),',',''))                     AS Outstanding_Balance,
    ma.[last_visited_at]                                                          AS Last_Visited,
    DATEDIFF(day, ma.[last_visited_at], GETDATE())                               AS Days_Since,
    ma.[last_visit_shift]                                                         AS Shift,
    COALESCE(tv.[total_visits], 0)                                               AS Total_Visits,
    CASE WHEN ms.[has_authorised]=1 THEN 'Authorised'
         WHEN ms.[has_rejected]=1   THEN 'Rejected'
         ELSE 'No Mandate' END                                                   AS Mandate_Status,
    COALESCE(ca.[calls_count], 0)                                                AS Calls,
    COALESCE(ca.[answered_calls], 0)                                             AS Answered,
    CASE
        WHEN ca.[calls_count] IS NULL OR ca.[calls_count] = 0
             THEN 'Not Called'
        WHEN ca.[answered_calls] = 0 THEN 'No Answer'
        ELSE 'Answered'
    END                                                                          AS Call_Status,
    CASE WHEN pt.[latest_ptp_date] IS NOT NULL THEN 'Yes' ELSE 'No' END         AS Has_PTP,
    CONVERT(VARCHAR(10), pt.[latest_ptp_date], 120)                             AS PTP_Date,
    pt.[ptp_amount]                                                              AS PTP_Amount,
    CASE
        WHEN pt.[latest_ptp_date] IS NULL THEN 'No PTP'
        WHEN ip.[latest_payment_date] >= pt.[latest_ptp_date] THEN 'Paid'
        ELSE 'Unpaid'
    END                                                                          AS PTP_Paid
FROM member_access AS ma
INNER JOIN dbo.members AS m   ON ma.[phone_last_9] = RIGHT(COALESCE(m.[cellphone],''), 9)
LEFT  JOIN dbo.rawdata AS rd  ON m.[unique_ref] = rd.[itensity_acc_number]
                              AND rd.[role] = 'Member'
LEFT  JOIN total_visits AS tv ON UPPER(LTRIM(RTRIM(ma.[lv_name]))) = tv.[tv_name]
LEFT  JOIN mandate_status AS ms ON m.[unique_ref] = ms.[client_ref_clean]
LEFT  JOIN call_activity AS ca ON ma.[phone_last_9] = ca.[phone_last_9]
LEFT  JOIN ptp AS pt           ON m.[unique_ref] = pt.[account_id]
LEFT  JOIN itensity_payments AS ip ON m.[unique_ref] = ip.[account_id]
WHERE TRY_CONVERT(DECIMAL(18,2),
        REPLACE(NULLIF(rd.[outstanding_balance],''),',','')) > 0
  AND m.[package] NOT IN ('staff','dependent')
  AND LOWER(LTRIM(RTRIM(rd.[tariff_name]))) NOT IN ('staff','dependent')
  SEARCH_CLAUSE
ORDER BY TRY_CONVERT(DECIMAL(18,2),
    REPLACE(NULLIF(rd.[outstanding_balance],''),',','')) DESC,
    ma.[last_visited_at] DESC
"""


@access_report_bp.route('/')
def index():
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    # This report reads a hardcoded external SQL Server that mirrors only
    # one gym's turnstile/Itensity data. It was never tenant-scoped, so any
    # signed-in tenant could otherwise reach another gym's access data.
    # g.tenant_slug is None when the tenant system is inert (legacy
    # single-tenant Postgres mode) — allow that case through unchanged.
    allowed_slug = current_app.config.get('ACCESS_REPORT_TENANT_SLUG', 'elev8')
    if g.tenant_slug and g.tenant_slug != allowed_slug:
        abort(404)

    days_raw = request.args.get('days', '7')
    days = int(days_raw) if days_raw.isdigit() and 1 <= int(days_raw) <= 90 else 7
    search = request.args.get('q', '').strip()
    view = request.args.get('view', 'owing')
    call_filter = request.args.get('call', '')

    rows, error, stats = [], None, {}

    try:
        conn = _elev8_conn()
        cur = conn.cursor()

        sc, params = '', []

        if view == 'visits':
            if search:
                sc = "AND (lv.name_surname LIKE ? OR lv.cell LIKE ?)"
                params = [f'%{search}%', f'%{search}%']
            sql = _SQL_VISITS.replace('DAYS_VAL', str(days)).replace('SEARCH_CLAUSE', sc)
        else:
            if search:
                sc = "AND (rd.[member_name] + ' ' + rd.[member_surname] LIKE ? OR m.[cellphone] LIKE ?)"
                params = [f'%{search}%', f'%{search}%']
            sql = _SQL_OWING.replace('DAYS_VAL', str(days)).replace('SEARCH_CLAUSE', sc)

        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        conn.close()

        if view == 'owing' and call_filter:
            fmap = {'not_called': 'Not Called', 'no_answer': 'No Answer', 'answered': 'Answered'}
            if target := fmap.get(call_filter):
                rows = [r for r in rows if r.get('Call_Status') == target]

        if view == 'owing':
            stats = {
                'total':      len(rows),
                'not_called': sum(1 for r in rows if r.get('Call_Status') == 'Not Called'),
                'no_answer':  sum(1 for r in rows if r.get('Call_Status') == 'No Answer'),
                'answered':   sum(1 for r in rows if r.get('Call_Status') == 'Answered'),
                'balance':    sum((r.get('Outstanding_Balance') or 0) for r in rows),
                'has_ptp':    sum(1 for r in rows if r.get('Has_PTP') == 'Yes'),
            }
        else:
            stats = {
                'total':     len(rows),
                'today':     sum(1 for r in rows if (r.get('days_ago') or 0) == 0),
                'morning':   sum(1 for r in rows if r.get('shift') == 'Morning'),
                'afternoon': sum(1 for r in rows if r.get('shift') == 'Afternoon'),
            }

    except Exception as exc:
        error = str(exc)

    return render_template(
        'access_report/index.html',
        rows=rows, error=error, stats=stats,
        days=days, search=search, view=view, call_filter=call_filter,
        today=datetime.today().strftime('%Y-%m-%d'),
    )
