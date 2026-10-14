"""Recheck the 48 candidates against complete live reports and served identities."""
import json
import re
import sqlite3
import sys
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
load_dotenv(ROOT/'.env')
from onecpase.encryption import decrypt
from onecpase.nupay_sync import _member_indexes, _member_for_report_row

read=lambda name:json.loads((ROOT/'artifacts'/name).read_text(encoding='utf-8'))
review=read('completed-mandate-cancellation-review-20261014.json')
live=read('current-cancellation-evidence-20261014.json')
target=read('cancellation-target-snapshot-20261014.json')
mandates=read('lapsed-mandate-review-20261014.json')['rows']
assert live['through_date']=='2026-10-14'
assert live['visits'][0]==['Name & Surname','Cell','Last Visited']
assert live['transactions'] and len(live['windows'])==5
assert all('2026-01-01'<=str(row['date'])[:10]<='2026-10-14' for row in live['transactions'])
db=sqlite3.connect('file:'+str(ROOT/'data/tenants/elev8.db')+'?mode=ro',uri=True)
db.row_factory=sqlite3.Row
refs,ids=_member_indexes(db)
phone=lambda s:re.sub(r'\D','',str(s or ''))[-9:]
name=lambda s:' '.join(str(s or '').casefold().split())
payment_types={'Cash Payment','Card Payment','Debit Order Payment','EFT Payment'}
members=[]
excluded=[]
candidates={}
for row in review['rows']:
    if row['blockers']==['Current payment and attendance coverage through 14 October not verified'] and row.get('member_status')!='Cancelled':
        candidates.setdefault(str(row['itensity_ref']),[]).append(row)
assert len(candidates)==48
for ref,previous in candidates.items():
    matches=[m for m in target['members'] if str(m['itensity_ref'])==ref]
    reasons=[]
    if len(matches)!=1:
        excluded.append({'itensity_ref':ref,'reasons':['Served member match is ambiguous']});continue
    m=matches[0]
    local=db.execute('SELECT * FROM members WHERE id=?',(previous[0]['member_id'],)).fetchone()
    if not local or decrypt(local['id_number'])!=m['id_number'] or name(decrypt(local['first_name'])+' '+decrypt(local['last_name']))!=name(m['first_name']+' '+m['last_name']):
        reasons.append('Local and served member identities differ')
    cutoff=max(r['last_debit_date'] for r in previous)
    visits=[r for r in live['visits'][1:] if phone(m['contact']) and phone(r[1])==phone(m['contact'])]
    if len(visits)!=1 or name(visits[0][0])!=name(m['first_name']+' '+m['last_name']):
        reasons.append('Attendance identity not uniquely verified')
    last_visit=max((r[2] for r in visits),default=None)
    if last_visit and last_visit[:10]>=cutoff:reasons.append('Gym visit on or after final debit date')
    later=[r for r in live['transactions'] if str(r['uid'])==ref and str(r['type']).strip() in payment_types and str(r['date'])[:10]>cutoff]
    if later:reasons.append('Later successful payment found in live Itensity report')
    later_local=[p for p in m['payments'] if str(p['collection_date'])[:10]>cutoff and 'discount' not in str(p['notes']).lower() and str(p['status']).lower() not in {'failed','reversed'}]
    if later_local:reasons.append('Later successful payment recorded in served app')
    replacement=[]
    for mandate in mandates:
        matched=_member_for_report_row(mandate,refs,ids)
        if matched is None or str(matched['itensity_ref'])!=ref:continue
        if str(mandate.get('perpetual')).lower()=='yes' or (str(mandate.get('instalment')).isdigit() and str(mandate.get('instalments')).isdigit() and int(mandate['instalment'])<int(mandate['instalments'])):
            replacement.append(mandate['mandate_id'])
    if replacement:reasons.append('Replacement or perpetual mandate found')
    if m['member_status']=='Cancelled':reasons.append('Already Cancelled')
    if reasons:
        excluded.append({'itensity_ref':ref,'id':m['id'],'reasons':reasons,'last_visit':last_visit,'later_payments':len(later)})
        continue
    m['evidence']={'mandates':[{'mandate_id':r['mandate_id'],'instalment':r['current_instalment'],'total_instalments':r['total_instalments']} for r in previous],
       'last_debit_date':cutoff,'last_visit':last_visit,'later_successful_payments':0,'replacement_mandates':0,
       'payment_report_from':'2026-01-01','payment_report_through':'2026-10-14','attendance_report':'Live Itensity Last Visited',
       'owner_instruction':'Cancel completed fixed-term DebiCheck clients with no payment or visit since the last debit.'}
    members.append(m)
payload={'verified':True,'database':target['database'],'through_date':live['through_date'],
         'retrieved_at':live['retrieved_at'],'members':members,'excluded':excluded}
(ROOT/'artifacts/verified-cancellation-payload-20261014.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
print('Verified cancellations:',len(members),'Excluded:',len(excluded))
print('Exclusion reasons:',dict(Counter(r for m in excluded for r in m['reasons'])))
print('Previous statuses:',dict(Counter(m['member_status'] for m in members)))
