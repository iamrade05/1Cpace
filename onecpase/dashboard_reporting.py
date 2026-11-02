"""Shared dashboard periods and comparable, dated activity (never invented snapshots)."""
from dataclasses import dataclass
from datetime import date, timedelta
from flask import abort, request
from .reporting_cycle import today as business_today, cycle_key, cycle_bounds, shift_month

PERIOD_OPTIONS=(('day','Daily'),('week','Weekly'),('cycle','Cycle (15th–14th)'),
                ('3cycles','3 cycles'),('6cycles','6 cycles'),('12cycles','12 cycles'),('custom','Custom dates'))


@dataclass(frozen=True)
class DashboardPeriod:
    mode: str
    start: date
    end: date
    full_end: date
    previous_start: date
    previous_end: date
    previous_full_end: date
    anchor: date
    cycle: str
    today: date

    @property
    def date_from(self):return self.start.isoformat()
    @property
    def date_to(self):return self.end.isoformat()
    @property
    def title(self):return dict(PERIOD_OPTIONS)[self.mode]
    @property
    def partial(self):return self.end<self.full_end
    @property
    def days(self):return (self.end-self.start).days+1
    @property
    def previous_days(self):return (self.previous_end-self.previous_start).days+1
    @property
    def max_cycle(self):return cycle_key(self.today)
    @property
    def query_args(self):
        if self.mode in ('day','week'):return dict(period=self.mode,on=self.anchor.isoformat())
        if self.mode=='custom':return dict(period=self.mode,**{'from':self.date_from,'to':self.full_end.isoformat()})
        return dict(period=self.mode,cycle=self.cycle)


def resolve_period(args=None, *, today=None):
    args=request.args if args is None else args
    today=today or business_today()
    aliases={'24h':'day','7d':'week','30d':'cycle','3m':'3cycles','6m':'6cycles','1y':'12cycles'}
    mode=args.get('period') or aliases.get(args.get('range')) or ('custom' if args.get('from') or args.get('to') else 'cycle')
    try:
        if mode not in dict(PERIOD_OPTIONS):raise ValueError()
        anchor=date.fromisoformat(args.get('on') or today.isoformat())
        key=args.get('cycle') or cycle_key(anchor)
        first,last=cycle_bounds(key)
        if mode=='day':
            start=end=anchor;previous_start=previous_end=anchor-timedelta(days=1)
        elif mode=='week':
            start=anchor-timedelta(days=anchor.weekday());end=start+timedelta(days=6)
            previous_start=start-timedelta(days=7);previous_end=start-timedelta(days=1)
        elif mode=='custom':
            current_first,current_last=cycle_bounds(cycle_key(today))
            start=date.fromisoformat(args.get('from') or current_first.isoformat())
            end=date.fromisoformat(args.get('to') or current_last.isoformat())
            if end<start or (end-start).days>366:raise ValueError()
            previous_end=start-timedelta(days=1)
            previous_start=previous_end-timedelta(days=(end-start).days)
        else:
            if first>today:raise ValueError()
            count=1 if mode=='cycle' else int(mode.replace('cycles',''))
            start=shift_month(first,1-count).replace(day=15);end=last
            previous_start=shift_month(start,-count).replace(day=15)
            previous_end=start-timedelta(days=1)
        if start>today:raise ValueError()
        full_end=end;previous_full_end=previous_end
        end=min(end,today)
        if end<full_end:
            previous_end=min(previous_end,previous_start+timedelta(days=(end-start).days))
        return DashboardPeriod(mode,start,end,full_end,previous_start,previous_end,
                               previous_full_end,anchor,key,today)
    except (ValueError,TypeError,OverflowError):
        abort(400,description='Choose a valid reporting period that has started. Custom dates may span up to one year.')


def comparison_row(label,current,previous,kind='number'):
    if current is None or previous is None:
        return dict(label=label,current=current,previous=previous,kind=kind,change=None,percent=None)
    return dict(label=label,current=current,previous=previous,kind=kind,
                change=round(current-previous,2),
                percent=round((current-previous)/abs(previous)*100,1) if previous else (0 if current==0 else None))


def _scalar(db,sql,params):return db.execute(sql,params).fetchone()[0] or 0


def activity_rows(db,period,scope,user_id,*,allowed_ids=None):
    """Date filters are independent of the current-state work queues below them."""
    from .dashboard_access import team_ids
    from .department_metrics import actor_filter
    from .members import _is_discount
    bounds=((period.date_from,period.date_to),
            (period.previous_start.isoformat(),period.previous_end.isoformat()))
    rows=[]
    def count(label,table,column,where='1=1',params=(),kind='number',expression='COUNT(*)',utc=False):
        day=f'substr({column},1,10)'
        if utc:
            from .database import _db_type
            day=f"CAST(CAST({column} AS TIMESTAMPTZ) AT TIME ZONE 'Africa/Johannesburg' AS DATE)" if _db_type()=='postgresql' else f"date({column},'+2 hours')"
        values=[_scalar(db,f'SELECT {expression} FROM {table} WHERE {where} AND {day}>=? AND {day}<=?',(*params,start,end)) for start,end in bounds]
        rows.append(comparison_row(label,*values,kind))
    if scope in ('admin','reception','fitness'):
        count('New gym members','members','join_date',"outcome='joined'")
    if scope in ('admin','reception','collections','ptp'):
        values=[]
        for start,end in bounds:
            payments=db.execute("SELECT amount_paid,notes FROM collections WHERE status IN ('paid','partial') AND substr(collection_date,1,10)>=? AND substr(collection_date,1,10)<=?",(start,end)).fetchall()
            values.append(round(sum(float(r['amount_paid'] or 0) for r in payments if not _is_discount(r)),2))
        rows.append(comparison_row('Collections received',*values,'money'))
        valid="COALESCE(ptp_status,'pending') NOT IN ('cancelled','canceled','rejected','declined','superseded') AND COALESCE(manager_approval_status,'not_required') NOT IN ('rejected','declined')"
        count('PTPs created','ptp_agreements','created_at',valid)
        count('Amount promised','ptp_agreements','created_at',valid,kind='money',expression='SUM(promise_amount)')
    if scope in ('admin','reception','work','sales'):
        ids=allowed_ids if scope=='sales' else team_ids(db,'reception') if scope=='reception' else [user_id] if scope=='work' else None
        query_filter=actor_filter(ids,'logged_by') if ids is not None else '1=1'
        call_filter=actor_filter(ids,'user_id') if ids is not None else '1=1'
        params=tuple(ids or [])
        if scope!='sales':count('Queries logged','queries','created_at',query_filter,params)
        from datetime import timezone
        from zoneinfo import ZoneInfo
        from .staff_kpi import business_day
        from .collections_engine import get_collection_rule_int
        minimum=max(1,get_collection_rule_int(db,'contact_talk_seconds',60))
        low=(period.previous_start-timedelta(days=1)).isoformat()
        high=(period.end+timedelta(days=2)).isoformat()
        calls=db.execute("SELECT start_time,created_at,talk_duration FROM pbx_call_logs WHERE LOWER(direction)='outbound' AND "+call_filter+
            " AND COALESCE(NULLIF(CAST(start_time AS TEXT),''),CAST(created_at AS TEXT))>=? AND COALESCE(NULLIF(CAST(start_time AS TEXT),''),CAST(created_at AS TEXT))<?",(*params,low,high)).fetchall()
        calls=[(business_day(r['start_time'] or r['created_at'],naive_zone=ZoneInfo('Africa/Johannesburg') if r['start_time'] else timezone.utc),float(r['talk_duration'] or 0)) for r in calls]
        call_counts=[]
        for label,connected in (('Outbound calls',False),(f'Calls meeting connection target (≥{minimum}s)',True)):
            values=[sum(1 for day,talk in calls if day is not None and start<=day.isoformat()<=end and (not connected or talk>=minimum)) for start,end in bounds]
            call_counts.append(values)
            rows.append(comparison_row(label,*values))
        if scope=='reception':
            rates=[round(connected/total*100,1) if total else None for total,connected in zip(*call_counts)]
            rows.append(comparison_row('Call connection rate',*rates,'percent'))
    if scope=='sales':
        owner_filter=actor_filter(allowed_ids,'assigned_to') if allowed_ids is not None else '1=1'
        params=tuple(allowed_ids or [])
        count('Leads assigned','leads',"COALESCE(NULLIF(CAST(assigned_at AS TEXT),''),CAST(created_at AS TEXT))",owner_filter,params,utc=True)
        count('First contacts recorded','leads','first_contact_at',owner_filter,params,utc=True)
        booking_filter=actor_filter(allowed_ids,'h.changed_by') if allowed_ids is not None else '1=1'
        count('First appointments booked',
              "sales_stage_history h JOIN (SELECT lead_id,MIN(id) AS first_id FROM sales_stage_history WHERE to_stage='APPOINTMENT_BOOKED' GROUP BY lead_id) first_booking ON h.id=first_booking.first_id JOIN leads l ON l.id=h.lead_id",
              'h.changed_at',booking_filter+' AND l.duplicate_of_lead_id IS NULL',params,utc=True)
        from .sales_kpi import credited_sales
        from .staff_kpi import business_day
        eligible=set(allowed_ids) if allowed_ids is not None else None
        sales=credited_sales(db,lambda stamp:True,lambda uid:eligible is None or uid in eligible)
        values=[sum(1 for sale in sales if (day:=business_day(sale['credited_at'])) is not None and start<=day.isoformat()<=end) for start,end in bounds]
        rows.insert(0,comparison_row('Credited sales',*values))
    if scope in ('collections','ptp'):
        count('Cases resolved','collections_cases','closed_at',"status='closed'")
        count('Recorded case communications','collection_communications','created_at')
    if scope=='fitness':
        count('Assisted sessions','onboarding_visits','visit_date',"recorded_at IS NOT NULL AND outcome IS NOT NULL AND outcome!='not_assisted'")
    if scope=='hr':
        count('Leave requests submitted','hr_requests','created_at','user_id=?',(user_id,),utc=True)
        count('Published shifts','hr_shifts','work_date',"user_id=? AND status='published'",(user_id,))
        count('Timesheet submissions','hr_signatures','created_at',"actor_id=? AND action='submitted'",(user_id,),utc=True)
    return rows


def dashboard_context(db,scope,user_id,*,period=None,rows=None):
    period=period or resolve_period()
    return dict(dashboard_period=period,period_options=PERIOD_OPTIONS,
                period_rows=activity_rows(db,period,scope,user_id) if rows is None else rows)
