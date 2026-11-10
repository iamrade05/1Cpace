"""Read-only department summaries. Period activity is separate from live balances."""
from datetime import timedelta
from .dashboard_access import team_ids
from .member_counts import get_member_counts


def period():
    from .dashboard_reporting import resolve_period
    selected=resolve_period()
    return selected.date_from,selected.date_to,selected.today


def scalar(db,sql,params=()):return db.execute(sql,params).fetchone()[0] or 0


def actor_filter(ids,column):
    return column+' IN ('+','.join('?' for _ in ids)+')' if ids else '1=0'


def card(label,value,note='',kind='number'):
    return dict(label=label,value=value,note=note,kind=kind)


def reception(db,start,end,today):
    ids=team_ids(db,'reception')
    counts=get_member_counts(db)
    from collections import defaultdict
    from .ptp import compute_arrears_from_profile
    from .members import _build_payment_profile,_is_discount
    ledger=db.execute('SELECT * FROM active_collections ORDER BY member_id,collection_date DESC,id DESC').fetchall()
    by_member=defaultdict(list)
    for row in ledger:by_member[row['member_id']].append(row)
    profiles={mid:_build_payment_profile(rows) for mid,rows in by_member.items()}
    balances={mid:compute_arrears_from_profile(profile) for mid,profile in profiles.items()}
    period_charges=[row for profile in profiles.values() for row in profile if start<=str(row['date'] or '')[:10]<=end]
    due=sum(row['due'] for row in period_charges)
    matched=sum(row['paid'] for row in period_charges)
    total=sum(row['total_arrears'] for row in balances.values())
    period_filter='substr(collection_date,1,10)>=? AND substr(collection_date,1,10)<=?'
    payments=db.execute('SELECT status,COUNT(*) AS n FROM active_collections WHERE '+period_filter+' GROUP BY status',(start,end)).fetchall()
    collected=sum(float(row['amount_paid'] or 0) for row in ledger if row['status'] in ('paid','partial') and
                  start<=str(row['collection_date'] or '')[:10]<=end and not _is_discount(row))
    calls=scalar(db,"SELECT COUNT(*) FROM pbx_call_logs WHERE direction='outbound' AND "+actor_filter(ids,'user_id')+
                 ' AND substr(start_time,1,10)>=? AND substr(start_time,1,10)<=?',(*ids,start,end))
    reached=scalar(db,"SELECT COUNT(*) FROM pbx_call_logs WHERE direction='outbound' AND talk_duration>0 AND "+actor_filter(ids,'user_id')+
                   ' AND substr(start_time,1,10)>=? AND substr(start_time,1,10)<=?',(*ids,start,end))
    logged=scalar(db,'SELECT COUNT(*) FROM queries WHERE '+actor_filter(ids,'logged_by')+' AND substr(created_at,1,10)>=? AND substr(created_at,1,10)<=?',(*ids,start,end))
    ptps=scalar(db,'SELECT COUNT(*) FROM ptp_agreements WHERE '+actor_filter(ids,'created_by')+' AND substr(created_at,1,10)>=? AND substr(created_at,1,10)<=?',(*ids,start,end))
    open_queries=scalar(db,"SELECT COUNT(*) FROM queries WHERE status NOT IN ('closed','resolved') AND ("+actor_filter(ids,'assigned_to')+" OR department='Reception')",ids)
    new_members=scalar(db,"SELECT COUNT(*) FROM members WHERE outcome='joined' AND substr(join_date,1,10)>=? AND substr(join_date,1,10)<=?",(start,end))
    sources=db.execute("SELECT COALESCE(NULLIF(source,''),'Unrecorded') AS label,COUNT(*) AS n FROM members WHERE outcome='joined' AND substr(join_date,1,10)>=? AND substr(join_date,1,10)<=? GROUP BY 1 ORDER BY n DESC",(start,end)).fetchall()
    return dict(cards=[card('Total gym members',counts['total'],'Current local member register'),
        card('Active members',counts['active'],'Current membership state'),card('New gym members',new_members,'Joined in selected period'),
        card('Outstanding amount',total,'Current reconciled arrears; charges matched against payments','money'),
        card('Members owing',sum(row['total_arrears']>0 for row in balances.values()),'Current reconciled accounts'),
        card('Collections received',collected,'Paid and partial payment amounts in selected period','money'),
        card('Collection coverage rate',round(matched/due*100,1) if due else None,'Reconciled payments/adjustments ÷ charges due in selected period','percent'),
        card('Queries logged',logged,'Logged by the reception team in selected period'),card('Open reception queries',open_queries,'Current assigned reception workload'),
        card('PBX calls made',calls,'Reception outbound calls in selected period'),
        card('Call connection rate',round(reached/calls*100,1) if calls else None,'Reception outbound calls with recorded talk time','percent'),
        card('PTPs made',ptps,'Reception agreements created in selected period')],
        breakdowns=[dict(title='Current membership states',rows=[dict(label=k,n=v) for k,v in counts['by_status'].items()]),
                    dict(title='Payment-record states in selected period',rows=[dict(label=r['status'],n=r['n']) for r in payments]),
                    dict(title='New member acquisition sources',rows=[dict(r) for r in sources])],
        links=[('Members','members.members_index'),('Queries','queries.index'),('Collections work','collections.dashboard'),('PTPs','ptp.ptp_dashboard')])


def fitness(db,start,end,today):
    from .database import _db_type
    if _db_type()=='postgresql':
        tables={r[0] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
    else:tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    new=scalar(db,"SELECT COUNT(*) FROM members WHERE outcome='joined' AND substr(join_date,1,10)>=? AND substr(join_date,1,10)<=?",(start,end))
    assisted=scalar(db,"SELECT COUNT(*) FROM onboarding_visits WHERE recorded_at IS NOT NULL AND outcome IS NOT NULL AND outcome!='not_assisted' AND visit_date>=? AND visit_date<=?",(start,end))
    cards=[card('New members joining',new,'Joined in selected period'),card('Assisted sessions',assisted,'Recorded assistance in selected period; gym entry alone does not count')]
    if 'fitness_intake' in tables:
        fields={r[1] for r in db.execute('PRAGMA table_info(fitness_intake)').fetchall()} if _db_type()!='postgresql' else {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='fitness_intake'").fetchall()}
        cards.append(card('Welcome follow-ups',scalar(db,"SELECT COUNT(*) FROM fitness_intake WHERE welcome_status IN ('failed','invalid_number')"),'Current delivery failures'))
        if 'allocation_state' in fields:
            cards.extend([card('Awaiting allocation',scalar(db,"SELECT COUNT(*) FROM fitness_intake WHERE allocation_state='awaiting_allocation' AND interest='interested'"),'Current allocation queue'),
                          card('Goals to clarify',scalar(db,"SELECT COUNT(*) FROM fitness_intake WHERE allocation_state='awaiting_goal_clarification' AND interest='interested'"),'Ask member to confirm their primary goal')])
        if 'program_start_on' in fields:
            cutoff=(today-timedelta(days=9)).isoformat()
            cards.append(card('Completion reviews due',scalar(db,"SELECT COUNT(*) FROM fitness_intake f JOIN member_onboarding o ON o.member_id=f.member_id WHERE f.program_start_on<? AND COALESCE(o.review_done,0)=0",(cutoff,)),'Programme window elapsed; review still required'))
    for table,label,condition in [('fitness_allocation_offers','Pending instructor offers',"status='pending'"),('fitness_pt_reviews','PT-rights reviews',"status='pending'")]:
        cards.append(card(label,scalar(db,'SELECT COUNT(*) FROM '+table+' WHERE '+condition) if table in tables else 0,'Current fitness workload'))
    if 'fitness_allocation_requests' in tables:
        cards.append(card('Attendance to record',scalar(db,"SELECT COUNT(*) FROM fitness_allocation_requests WHERE status='attendance_due'"),'Confirm actual attendance and assistance'))
    return dict(cards=cards,breakdowns=[],links=[('New member onboarding','onboarding.today_list'),('Allocations and offers','fitness_journey.allocations'),('Classes','fitness.fitness_index'),('Instructor dailies','fitness.dailies_index')])


def work(db,start,end,today,user_id):
    return dict(cards=[card('My open queries',scalar(db,"SELECT COUNT(*) FROM queries WHERE assigned_to=? AND status NOT IN ('closed','resolved')",(user_id,)),'Currently assigned to you'),
                       card('Queries I logged',scalar(db,'SELECT COUNT(*) FROM queries WHERE logged_by=? AND substr(created_at,1,10)>=? AND substr(created_at,1,10)<=?',(user_id,start,end)),'Selected period')],breakdowns=[],links=[])
