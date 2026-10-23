"""Read-only staff attendance evidence from an access-card snapshot.

An entry is never treated as an exit, a lunch break, or proof of hours worked.
"""
from collections import defaultdict
from datetime import date,datetime,time,timedelta


def shift_window(day,department,assignment=None,public_holidays=()):
    if day.weekday()==6:
        return {'label':'Closed','start':None,'end':None}
    short_day=day.weekday()==5 or day.isoformat() in public_holidays
    if assignment in ('early','late'):
        if short_day:return {'label':'Saturday / public-holiday shift','start':'06:00','end':'14:00'}
        if assignment=='early':return {'label':'Morning shift','start':'05:00','end':'13:00'}
        return {'label':'Late shift','start':'13:00','end':'20:00' if day.weekday()==4 else '21:00'}
    if department=='Sales':
        if short_day:return {'label':'Sales hours need confirmation','start':None,'end':None}
        return {'label':'Sales','start':'09:00','end':'19:00'}
    if department in ('Reception','Fitness Instructor','Cleaner'):
        if short_day:return {'label':'Saturday / public-holiday shift','start':'06:00','end':'14:00'}
        return {'label':'Early / late assignment needed','start':None,'end':None}
    return {'label':'Presence only / no shift assigned','start':None,'end':None}


def build_week(snapshot,week_start):
    start=date.fromisoformat(str(week_start));start-=timedelta(days=start.weekday())
    days=[start+timedelta(days=i) for i in range(7)]
    available=snapshot.get('week_start')==start.isoformat()
    counts=snapshot.get('source_rows_by_date',{}) if available else {}
    assignments=snapshot.get('shift_assignments',{}) if available else {}
    events=defaultdict(list);duplicates=0;seen=set()
    for event in snapshot.get('events',[]) if available else []:
        stamp=datetime.fromisoformat(event['occurred_at'])
        if stamp.date() not in days:continue
        key=(event['user_id'],stamp.isoformat(),event['movement'],event['allowed'],event.get('zone',''))
        if key in seen:duplicates+=1;continue
        seen.add(key)
        events[(event['user_id'],stamp.date())].append((stamp,event))
    employees=[];totals={'present':0,'successful_scans':0,'denied_scans':0,'scan_groups':0,'days_observed':len(counts)}
    linked_ids=set(snapshot.get('matched_references',{}).values())
    for user in snapshot.get('roster',[]):
        record={**user,'days':[],'present_days':0,'successful_scans':0,'denied_scans':0,'scan_groups':0}
        for day in days:
            pairs=sorted(events.get((user['id'],day),[]),key=lambda pair:pair[0])
            granted=[stamp for stamp,event in pairs if event['allowed'] and event['movement'].lower()=='enter']
            denied=[event for stamp,event in pairs if not event['allowed']]
            groups=[]
            previous=None
            for stamp in granted:
                if previous is None or (stamp-previous).total_seconds()>60:groups.append(stamp)
                previous=stamp
            shift=shift_window(day,user['department'],assignments.get(user['username']),snapshot.get('public_holidays',[]))
            first=granted[0] if granted else None
            policy_start=None
            if first and shift['start'] and shift['end']:
                planned_start=datetime.combine(day,time.fromisoformat(shift['start']))
                planned_end=datetime.combine(day,time.fromisoformat(shift['end']))
                if first<planned_end:policy_start=max(first,planned_start).strftime('%H:%M:%S')
            if granted:status='Entry recorded'
            elif day.weekday()==6:status='Closed'
            elif not available:status='No snapshot for this week'
            elif user['id'] not in linked_ids:status='Card link not found'
            elif day.isoformat() not in counts:status='Awaiting source data'
            else:status='No successful scan recorded'
            row={'date':day.isoformat(),'day':day.strftime('%a %d %b'),'shift':shift,'status':status,
                 'first_entry':first.strftime('%H:%M:%S') if first else None,
                 'last_entry':granted[-1].strftime('%H:%M:%S') if granted else None,
                 'successful_scans':len(granted),'denied_scans':len(denied),'scan_groups':len(groups),
                 'scan_times':[stamp.strftime('%H:%M:%S') for stamp in granted],
                 'policy_start':policy_start,'pre_shift_entry':bool(first and shift['start'] and first.strftime('%H:%M:%S')<shift['start']),
                 'clock_out':None,'lunch_out':None,'lunch_in':None,'verified_hours':None}
            record['days'].append(row)
            record['present_days']+=bool(granted)
            for metric in ('successful_scans','denied_scans','scan_groups'):record[metric]+=row[metric]
        totals['present']+=bool(record['present_days'])
        for metric in ('successful_scans','denied_scans','scan_groups'):totals[metric]+=record[metric]
        employees.append(record)
    return {'week_start':start.isoformat(),'week_end':days[-1].isoformat(),'employees':employees,
            'totals':totals,'available':available,'duplicates_removed':duplicates,
            'source':snapshot.get('source'),'source_latest_date':snapshot.get('source_latest_date'),
            'captured_at':snapshot.get('captured_at'),'quality':snapshot.get('quality',{}),
            'prev_week':(start-timedelta(days=7)).isoformat(),'next_week':(start+timedelta(days=7)).isoformat()}
