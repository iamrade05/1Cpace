from collections import defaultdict
from datetime import date

from .financial_reporting import money
from .reporting_cycle import today


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _name_key(name):
    return " ".join((name or "").split()).casefold()


def load_consultant_book(db, consultant_id=None, segment="signed", *, include_all_members=False):
    as_of = today()
    staff = db.execute("""
        SELECT u.id, u.full_name, u.is_sales_consultant, u.department, u.role,
               COALESCE(NULLIF(h.start_date,''), NULLIF(s.start_date,'')) AS start_date
        FROM users u
        LEFT JOIN hr_profiles h ON h.user_id=u.id
        LEFT JOIN staff s ON s.id=h.staff_id
        WHERE COALESCE(u.is_platform_user,0)=0
        ORDER BY u.full_name, u.id
    """).fetchall()
    books = {
        str(row["id"]): dict(id=str(row["id"]), name=row["full_name"],
                             start=_date(row["start_date"]), signed=0, lost=0, active=0,
                             signed_value=0, lost_value=0, lost_priced=0, active_value=0)
        for row in staff
        if row["is_sales_consultant"] or (row["department"] or "").strip().lower() == "sales"
        or row["role"] in ("sales", "sales_manager")
    }
    staff_by_name = defaultdict(list)
    for row in staff:
        staff_by_name[_name_key(row["full_name"])].append(str(row["id"]))

    rows = db.execute("""
        WITH sale_events AS (
            SELECT lead_id, owner_id, occurred_at,
                   ROW_NUMBER() OVER (PARTITION BY lead_id ORDER BY id) AS rn
            FROM stage_events
            WHERE entity_type='lead' AND field='sales_stage' AND to_value='JOINED'
              AND owner_id IS NOT NULL
        ), exits AS (
            SELECT q.member_id, q.query_type, q.cancellation_monthly_fee,
                   COALESCE(MAX(e.created_at), NULLIF(CAST(q.closed_at AS TEXT),'')) AS exit_date,
                   ROW_NUMBER() OVER (PARTITION BY q.member_id
                       ORDER BY COALESCE(MAX(e.created_at), NULLIF(CAST(q.closed_at AS TEXT),''),
                                         CAST(q.updated_at AS TEXT)) DESC,
                                q.id DESC) AS rn
            FROM queries q LEFT JOIN query_workflow_events e
              ON e.query_id=q.id AND e.action='finalise_cancellation'
            WHERE q.category='cancellation' AND q.member_cancelled=1
            GROUP BY q.id, q.member_id, q.query_type, q.cancellation_monthly_fee,
                     q.closed_at, q.updated_at
        )
        SELECT m.id, m.member_ref, m.first_name, m.last_name, m.member_status, m.sold_by,
               m.monthly_installment, m.join_date, a.pushed_at,
               se.owner_id, se.occurred_at AS sale_recorded_at, m.uploaded_by_id, a.created_by,
               x.member_id AS cancelled_member, x.exit_date, x.query_type,
               x.cancellation_monthly_fee
        FROM members m
        LEFT JOIN membership_applications a ON a.id=(
            SELECT MIN(ma.id) FROM membership_applications ma WHERE ma.member_id=m.id
        )
        LEFT JOIN sale_events se ON se.lead_id=a.lead_id AND se.rn=1
        LEFT JOIN exits x ON x.member_id=m.id AND x.rn=1
        ORDER BY m.last_name, m.first_name, m.id
    """)

    members = []
    unattributed = 0
    missing_date = 0
    for row in rows:
        status = (row["member_status"] or "").strip().lower()
        if status == "unverified":
            continue
        joined = _date(row["join_date"]) or _date(row["pushed_at"]) or _date(row["sale_recorded_at"])
        if joined is None:
            missing_date += 1
            continue
        if joined > as_of:
            continue

        named_sellers = staff_by_name.get(_name_key(row["sold_by"]), [])
        named_seller = named_sellers[0] if len(named_sellers) == 1 else None
        seller = next((key for key in (
            str(row["owner_id"]), named_seller,
            str(row["uploaded_by_id"]), str(row["created_by"])
        ) if key in books), None)
        if seller is None:
            unattributed += 1
            continue
        book = books[seller]
        if book["start"] and joined < book["start"]:
            continue

        exit_date = _date(row["exit_date"])
        recorded_exit = row["cancelled_member"] and (exit_date is None or joined <= exit_date <= as_of)
        lost = bool(recorded_exit or status in ("cancelled", "canceled", "expired", "terminated"))
        active = status == "active"
        value = money(row["monthly_installment"])
        exit_value = money(row["cancellation_monthly_fee"])
        has_exit_value = bool(recorded_exit and exit_value > 0)
        book["signed"] += 1
        book["signed_value"] += value
        if lost:
            book["lost"] += 1
            if has_exit_value:
                book["lost_priced"] += 1
                book["lost_value"] += exit_value
        if active:
            book["active"] += 1
            book["active_value"] += value
        if include_all_members or (seller == consultant_id and (
            segment == "signed" or (segment == "lost" and lost) or (segment == "active" and active)
        )):
            exit_reason = (row["query_type"] or "Cancellation") if recorded_exit else (
                row["member_status"] if lost else ""
            )
            members.append(dict(row, joined=joined, consultant=book["name"], lost=lost,
                                exit_date=exit_date if recorded_exit else None,
                                exit_reason=exit_reason,
                                exit_value=exit_value if has_exit_value else None))

    return dict(books=list(books.values()), selected=books.get(consultant_id), members=members,
                segment=segment, as_of=as_of, unattributed=unattributed, missing_date=missing_date)
