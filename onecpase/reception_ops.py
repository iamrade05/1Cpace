"""Reception stock, card custody and shift cash reconciliation (tenant-local)."""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('Africa/Johannesburg')
CATEGORIES={'card_fee':'Card fees','arrears':'Arrears payments','card_purchase':'Card purchases','merchandise':'Merchandise','registration_fee':'Registration fees','other':'Other reception fees'}
METHODS={'cash':'Cash','card':'Card / POS','eft':'EFT'}
DEFAULT_ITEMS=(('access_cards','Access cards','cards'),('paper','Paper (sheets)','supplies'),('pens','Pens','supplies'),('bags','Bags','merchandise'),('bottles','Bottles','merchandise'),('tshirts','T-shirts','merchandise'),('caps','Caps','merchandise'),('water','Water','merchandise'),('energy','Energy drinks','merchandise'),('electrolytes','Electrolytes / Energate','merchandise'))
def today():return datetime.now(ZONE).date()
def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def cents(value):
    try:
        amount=Decimal(str(value))
        if not amount.is_finite() or amount<0 or amount>Decimal('10000000'):raise ValueError()
        return int((amount*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    except (InvalidOperation,ValueError,TypeError):raise ValueError('Enter a valid non-negative amount.')
def integer(value):
    try:
        number=int(str(value))
        if number<0 or number>1000000:raise ValueError()
        return number
    except (ValueError,TypeError):raise ValueError('Enter a whole stock quantity from 0 to 1,000,000.')
def text(value,required=False):
    result=str(value or '').strip()[:1000]
    if required and not result:raise ValueError('A reference or explanation is required.')
    return result
def migrate(db,backend='sqlite'):
    pk='SERIAL PRIMARY KEY' if backend=='postgresql' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    statements=[
        f'''CREATE TABLE IF NOT EXISTS reception_items(id {pk},code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,kind TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,baseline_set INTEGER NOT NULL DEFAULT 0,reorder_level INTEGER NOT NULL DEFAULT 0)''',
        f'''CREATE TABLE IF NOT EXISTS reception_shifts(id {pk},work_date TEXT NOT NULL,slot TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',opened_by INTEGER NOT NULL REFERENCES users(id),opened_at TEXT NOT NULL,opening_cash INTEGER NOT NULL,opening_note TEXT NOT NULL DEFAULT '',closed_by INTEGER REFERENCES users(id),closed_at TEXT,closing_cash INTEGER,closing_card INTEGER,closing_eft INTEGER,expected_cash INTEGER,expected_card INTEGER,expected_eft INTEGER,close_note TEXT,outstanding_snapshot TEXT,reviewed_by INTEGER REFERENCES users(id),reviewed_at TEXT,UNIQUE(work_date,slot))''',
        f'''CREATE TABLE IF NOT EXISTS reception_counts(id {pk},shift_id INTEGER NOT NULL REFERENCES reception_shifts(id),checkpoint TEXT NOT NULL,item_id INTEGER NOT NULL REFERENCES reception_items(id),expected INTEGER NOT NULL,actual INTEGER NOT NULL,UNIQUE(shift_id,checkpoint,item_id))''',
        f'''CREATE TABLE IF NOT EXISTS reception_movements(id {pk},shift_id INTEGER NOT NULL REFERENCES reception_shifts(id),item_id INTEGER NOT NULL REFERENCES reception_items(id),quantity INTEGER NOT NULL,kind TEXT NOT NULL,member_id INTEGER REFERENCES members(id),query_id INTEGER REFERENCES queries(id),reference TEXT NOT NULL,actor_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS reception_receipts(id {pk},shift_id INTEGER NOT NULL REFERENCES reception_shifts(id),category TEXT NOT NULL,method TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),member_id INTEGER REFERENCES members(id),item_id INTEGER REFERENCES reception_items(id),quantity INTEGER NOT NULL DEFAULT 0,collection_id INTEGER REFERENCES collections(id),collection_created INTEGER NOT NULL DEFAULT 0,reference TEXT NOT NULL,token TEXT NOT NULL UNIQUE,actor_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,voided_by INTEGER REFERENCES users(id),voided_at TEXT,void_reason TEXT)''',
        f'''CREATE TABLE IF NOT EXISTS reception_cards(id {pk},serial TEXT NOT NULL UNIQUE,member_id INTEGER NOT NULL REFERENCES members(id),state TEXT NOT NULL,issued_at TEXT,application_id INTEGER REFERENCES membership_applications(id))''',
        f'''CREATE TABLE IF NOT EXISTS reception_card_returns(id {pk},member_id INTEGER NOT NULL REFERENCES members(id),query_id INTEGER UNIQUE REFERENCES queries(id),serial TEXT NOT NULL,reusable INTEGER NOT NULL,actor_id INTEGER NOT NULL REFERENCES users(id),shift_id INTEGER NOT NULL REFERENCES reception_shifts(id),created_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS reception_cash_moves(id {pk},shift_id INTEGER NOT NULL REFERENCES reception_shifts(id),kind TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),reference TEXT NOT NULL,actor_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS reception_audit(id {pk},actor_id INTEGER NOT NULL REFERENCES users(id),action TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,request_token TEXT UNIQUE)''',
        'CREATE INDEX IF NOT EXISTS idx_reception_movements_item ON reception_movements(item_id,shift_id)',
        'CREATE INDEX IF NOT EXISTS idx_reception_receipts_shift ON reception_receipts(shift_id)',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_reception_receipts_active_collection ON reception_receipts(collection_id) WHERE collection_id IS NOT NULL AND voided_at IS NULL',
    ]
    for statement in statements:db.execute(statement)
    for code,name,kind in DEFAULT_ITEMS:db.execute('INSERT OR IGNORE INTO reception_items(code,name,kind) VALUES(?,?,?)',(code,name,kind))

@contextmanager
def transaction(db):
    try:
        if hasattr(db,'_conn'):
            db.execute('BEGIN');db.execute('LOCK TABLE reception_shifts,reception_items,reception_cards,reception_receipts IN EXCLUSIVE MODE')
        else:db.execute('BEGIN IMMEDIATE')
        yield;db.commit()
    except BaseException:db.rollback();raise
def audit(db,uid,action,payload):db.execute('INSERT INTO reception_audit(actor_id,action,payload,created_at) VALUES(?,?,?,?)',(uid,action,json.dumps(payload,sort_keys=True),stamp()))
def current_shift(db):return db.execute("SELECT * FROM reception_shifts WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
def live_shift(db):
    shift=current_shift(db)
    if not shift or shift['work_date']!=today().isoformat():raise ValueError('Open the current shift first. Complete any earlier open shift before starting today.')
    return shift
def stock(db,item_id):return db.execute('SELECT COALESCE(SUM(quantity),0) FROM reception_movements WHERE item_id=?',(item_id,)).fetchone()[0]
def items(db):return [dict(r,on_hand=stock(db,r['id'])) for r in db.execute('SELECT * FROM reception_items WHERE active=1 ORDER BY id')]
def member(db,uid):
    row=db.execute('SELECT id,first_name,last_name,member_ref,member_status FROM members WHERE id=?',(uid,)).fetchone()
    if not row:raise ValueError('Select an existing member.')
    return row
def move(db,shift,item_id,quantity,kind,uid,reference,member_id=None,query_id=None):
    item=db.execute('SELECT * FROM reception_items WHERE id=? AND active=1',(item_id,)).fetchone()
    if not item or not item['baseline_set']:raise ValueError('Count this item at shift opening before recording movements.')
    if stock(db,item_id)+quantity<0:raise ValueError('This movement exceeds recorded stock. Reconcile the stock first.')
    db.execute('INSERT INTO reception_movements(shift_id,item_id,quantity,kind,member_id,query_id,reference,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(shift['id'],item_id,quantity,kind,member_id,query_id,text(reference,True),uid,stamp()))
def record_counts(db,shift,checkpoint,form,uid):
    variances=[]
    for item in items(db):
        actual=integer(form.get('count_'+str(item['id'])));expected=item['on_hand']
        if not item['baseline_set']:
            if checkpoint!='opening':raise ValueError('An opening count is required for every item.')
            db.execute('UPDATE reception_items SET baseline_set=1 WHERE id=?',(item['id'],))
            if actual:move(db,shift,item['id'],actual,'initial_count',uid,'First physical stock count')
            expected=actual
        db.execute('INSERT INTO reception_counts(shift_id,checkpoint,item_id,expected,actual) VALUES(?,?,?,?,?)',(shift['id'],checkpoint,item['id'],expected,actual))
        if actual!=expected:variances.append(dict(item=item['name'],expected=expected,actual=actual))
    return variances
def open_shift(db,uid,form):
    if current_shift(db):raise ValueError('Close the current shift before opening another.')
    day=today().isoformat();slot=form.get('slot')
    if slot not in ('morning','afternoon'):raise ValueError('Choose morning or afternoon.')
    if db.execute('SELECT 1 FROM reception_shifts WHERE work_date=? AND slot=?',(day,slot)).fetchone():raise ValueError('This shift has already been recorded.')
    if slot=='afternoon' and not db.execute("SELECT 1 FROM reception_shifts WHERE work_date=? AND slot='morning' AND status='closed'",(day,)).fetchone():raise ValueError('Complete the morning closing check before afternoon opening.')
    cash=cents(form.get('opening_cash'));note=text(form.get('note'))
    previous=db.execute("SELECT closing_cash FROM reception_shifts WHERE status='closed' ORDER BY id DESC LIMIT 1").fetchone()
    sid=db.execute('INSERT INTO reception_shifts(work_date,slot,opened_by,opened_at,opening_cash,opening_note) VALUES(?,?,?,?,?,?)',(day,slot,uid,stamp(),cash,note)).lastrowid
    shift=db.execute('SELECT * FROM reception_shifts WHERE id=?',(sid,)).fetchone()
    variance=record_counts(db,shift,'opening',form,uid)
    if (variance or (previous and cash!=previous['closing_cash'])) and not note:raise ValueError('Explain the opening stock or cash handover difference.')
    audit(db,uid,'shift_opened',dict(shift_id=sid,stock_variances=variance));return sid
def totals(db,sid):
    result={key:0 for key in METHODS}
    for row in db.execute('SELECT method,SUM(amount) AS amount FROM reception_receipts WHERE shift_id=? AND voided_at IS NULL GROUP BY method',(sid,)):result[row['method']]=row['amount']
    shift=db.execute('SELECT * FROM reception_shifts WHERE id=?',(sid,)).fetchone()
    cash_change=sum((r['amount'] if r['kind']=='float_in' else -r['amount']) for r in db.execute('SELECT kind,amount FROM reception_cash_moves WHERE shift_id=?',(sid,)))
    return dict(receipts=result,expected_cash=shift['opening_cash']+result['cash']+cash_change,expected_card=result['card'],expected_eft=result['eft'],cash_change=cash_change,total=sum(result.values()))
def close_shift(db,uid,form):
    shift=current_shift(db)
    if not shift:raise ValueError('There is no open shift to close.')
    # Earlier shifts may be completed today; entries are frozen at close.
    expected=totals(db,shift['id']);actual={key:cents(form.get('closing_'+key)) for key in METHODS}
    variance=record_counts(db,shift,'closing',form,uid);note=text(form.get('note'))
    if (variance or any(actual[k]!=expected['expected_'+k] for k in METHODS)) and not note:raise ValueError('Explain the closing stock or cash-up variance.')
    pending=outstanding(db,shift['work_date'])
    snapshot=dict(card_returns=len(pending['returns']),signed_members_awaiting_cards=len(pending['signups']),unallocated_payments=len(pending['unallocated']))
    if snapshot['unallocated_payments'] and not note:raise ValueError('Allocate outstanding reception payments or explain why they remain unallocated.')
    db.execute("UPDATE reception_shifts SET status='closed',closed_by=?,closed_at=?,closing_cash=?,closing_card=?,closing_eft=?,expected_cash=?,expected_card=?,expected_eft=?,close_note=?,outstanding_snapshot=? WHERE id=? AND status='open'",(uid,stamp(),actual['cash'],actual['card'],actual['eft'],expected['expected_cash'],expected['expected_card'],expected['expected_eft'],note,json.dumps(snapshot),shift['id']))
    audit(db,uid,'shift_closed',dict(shift_id=shift['id'],stock_variances=variance,actual=actual,expected=expected));return shift['id']
def stock_movement(db,uid,form,manager=False):
    shift=live_shift(db);item_id=integer(form.get('item_id'));quantity=integer(form.get('quantity'));kind=form.get('kind');note=text(form.get('reference'),True)
    item=db.execute('SELECT * FROM reception_items WHERE id=? AND active=1',(item_id,)).fetchone()
    if not quantity or not item:raise ValueError('Choose an item and a positive quantity.')
    if kind not in ('received','used','damaged','adjust_in','adjust_out'):raise ValueError('Choose a stock movement.')
    if kind.startswith('adjust_') and not manager:raise PermissionError('Only a manager may reconcile stock adjustments.')
    if item['kind']=='cards' and kind=='used':raise ValueError('Issue access cards through the member card form.')
    change=quantity if kind in ('received','adjust_in') else -quantity
    move(db,shift,item_id,change,kind,uid,note);audit(db,uid,'stock_movement',dict(shift_id=shift['id'],item_id=item_id,quantity=change,kind=kind))
def signed(db,member_id):return db.execute("SELECT 1 FROM contract_signatures WHERE member_id=? AND signer_role='member' LIMIT 1",(member_id,)).fetchone() is not None
def issue_card(db,uid,form):
    shift=live_shift(db);mid=integer(form.get('member_id'));person=member(db,mid);serial=text(form.get('serial'),True);reason=form.get('reason')
    if str(person['member_status'] or '').lower() in ('cancelled','canceled'):raise ValueError('A cancelled member cannot receive an active card.')
    if reason not in ('new_signup','replacement'):raise ValueError('Choose new sign-up or replacement.')
    if reason=='new_signup' and not signed(db,mid):raise ValueError('The member must sign their contract before a new sign-up card is issued.')
    note=text(form.get('reference'),True)
    if db.execute("SELECT 1 FROM reception_cards WHERE member_id=? AND state='issued'",(mid,)).fetchone():raise ValueError('This member already has an issued card. Record its return or loss first.')
    card=db.execute('SELECT * FROM reception_cards WHERE serial=?',(serial,)).fetchone()
    if card and card['state']!='returned':raise ValueError('This card is already issued or is not reusable.')
    application=db.execute('SELECT id FROM membership_applications WHERE member_id=? ORDER BY id DESC LIMIT 1',(mid,)).fetchone()
    aid=application['id'] if application else None
    if card:db.execute("UPDATE reception_cards SET member_id=?,state='issued',issued_at=?,application_id=? WHERE id=?",(mid,stamp(),aid,card['id']))
    else:db.execute("INSERT INTO reception_cards(serial,member_id,state,issued_at,application_id) VALUES(?,?,'issued',?,?)",(serial,mid,stamp(),aid))
    item=db.execute("SELECT id FROM reception_items WHERE code='access_cards'").fetchone()
    move(db,shift,item['id'],-1,'card_issue_'+reason,uid,serial+' · '+note,mid)
    audit(db,uid,'card_issued',dict(member_id=mid,serial=serial,reason=reason,application_id=aid,shift_id=shift['id']))
def return_card(db,uid,form):
    shift=live_shift(db);mid=integer(form.get('member_id'));member(db,mid);serial=text(form.get('serial'),True)
    qid=integer(form.get('query_id')) if form.get('query_id') else None
    if not qid:
        pending=db.execute("SELECT q.id FROM queries q WHERE q.member_id=? AND q.category='cancellation' AND q.status NOT IN ('rejected','declined','withdrawn','cancelled','canceled') AND NOT EXISTS(SELECT 1 FROM reception_card_returns r WHERE r.query_id=q.id) ORDER BY q.id DESC LIMIT 1",(mid,)).fetchone()
        qid=pending['id'] if pending else None
    if qid:
        qry=db.execute("SELECT * FROM queries WHERE id=? AND member_id=? AND category='cancellation'",(qid,mid)).fetchone()
        if not qry:raise ValueError('Choose this member’s cancellation case.')
        if db.execute('SELECT 1 FROM reception_card_returns WHERE query_id=?',(qid,)).fetchone():raise ValueError('This cancellation card return is already recorded.')
    card=db.execute('SELECT * FROM reception_cards WHERE serial=?',(serial,)).fetchone()
    if card and (card['member_id']!=mid or card['state'] not in ('issued','lost')):raise ValueError('This card is not currently held by the selected member.')
    if not card and db.execute('SELECT 1 FROM reception_card_returns WHERE serial=?',(serial,)).fetchone():raise ValueError('This card return was already recorded.')
    reusable=form.get('reusable')=='yes'
    db.execute('INSERT INTO reception_card_returns(member_id,query_id,serial,reusable,actor_id,shift_id,created_at) VALUES(?,?,?,?,?,?,?)',(mid,qid,serial,int(reusable),uid,shift['id'],stamp()))
    state='returned' if reusable else 'damaged'
    if card:db.execute('UPDATE reception_cards SET state=? WHERE id=?',(state,card['id']))
    else:db.execute('INSERT INTO reception_cards(serial,member_id,state) VALUES(?,?,?)',(serial,mid,state))
    item=db.execute("SELECT id FROM reception_items WHERE code='access_cards'").fetchone()
    if reusable:move(db,shift,item['id'],1,'card_return',uid,serial,mid,qid)
    audit(db,uid,'card_returned',dict(member_id=mid,query_id=qid,serial=serial,reusable=reusable,shift_id=shift['id']))

def receipt(db,uid,form):
    shift=live_shift(db);token=text(form.get('token'),True)
    if len(token)<16:raise ValueError('Reload the receipt form before submitting.')
    if db.execute('SELECT 1 FROM reception_receipts WHERE token=?',(token,)).fetchone():return
    category=form.get('category');method=form.get('method');reference=text(form.get('reference'),True)
    if category not in CATEGORIES or method not in METHODS:raise ValueError('Choose a valid receipt category and payment method.')
    amount=cents(form.get('amount'))
    if not amount:raise ValueError('The receipt amount must be positive.')
    mid=integer(form.get('member_id')) if form.get('member_id') else None
    if mid:member(db,mid)
    if not mid and category not in ('merchandise','other'):raise ValueError('Link this payment to a member.')
    item_id=None;quantity=0;collection_id=None;created=0
    if form.get('collection_id'):
        collection_id=integer(form.get('collection_id'))
        row=db.execute("SELECT c.*,u.department,u.role FROM collections c LEFT JOIN users u ON u.id=c.created_by WHERE c.id=?",(collection_id,)).fetchone()
        from .members import _is_discount
        if not row or row['status'] not in ('paid','partial') or _is_discount(row) or str(row['collection_date'])[:10]!=shift['work_date'] or row['member_id']!=mid or cents(row['amount_paid'])!=amount:raise ValueError('The linked collection must be a matching paid receipt for this member and day.')
        source_method={'pos':'card','credit_card':'card','debit_card':'card','bank_transfer':'eft'}.get(str(row['method']).lower(),str(row['method']).lower())
        if source_method!=method:raise ValueError('Payment method must match the existing collection.')
        if str(row['department'] or '').strip().lower() not in ('reception','collections') and row['role']!='admin':raise ValueError('Only reception or manager-recorded collections may be linked.')
        if row['payment_purpose']=='registration_fee' and category!='registration_fee':raise ValueError('A registration fee must retain its payment category.')
        if db.execute('SELECT 1 FROM reception_receipts WHERE collection_id=? AND voided_at IS NULL',(collection_id,)).fetchone():raise ValueError('This collection is already allocated to a shift.')
        if category=='merchandise':raise ValueError('Record merchandise through the counter sale form, not an arrears collection.')
    else:
        if category=='merchandise':
            item_id=integer(form.get('item_id'));quantity=integer(form.get('quantity'))
            item=db.execute("SELECT * FROM reception_items WHERE id=? AND kind='merchandise' AND active=1",(item_id,)).fetchone()
            if not item or not quantity:raise ValueError('Select merchandise and a positive quantity.')
            move(db,shift,item_id,-quantity,'sale',uid,reference,mid)
        if mid:
            purpose={'arrears':'general','other':'reception_other'}.get(category,category)
            collection_id=db.execute("INSERT INTO collections(member_id,payment_purpose,amount_paid,collection_date,method,notes,status,created_by) VALUES(?,?,?,?,?,?,'paid',?)",(mid,purpose,amount/100,shift['work_date'],method,'Reception receipt: '+reference,uid)).lastrowid;created=1
    rid=db.execute('INSERT INTO reception_receipts(shift_id,category,method,amount,member_id,item_id,quantity,collection_id,collection_created,reference,token,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(shift['id'],category,method,amount,mid,item_id,quantity,collection_id,created,reference,token,uid,stamp())).lastrowid
    audit(db,uid,'receipt_recorded',dict(receipt_id=rid,shift_id=shift['id'],collection_id=collection_id,amount=amount))
def cash_move(db,uid,form):
    shift=live_shift(db);kind=form.get('kind');amount=cents(form.get('amount'));reference=text(form.get('reference'),True)
    if kind not in ('float_in','cash_drop') or not amount:raise ValueError('Choose a float addition or cash handover, with a positive amount.')
    if kind=='cash_drop' and amount>totals(db,shift['id'])['expected_cash']:raise ValueError('The cash handover exceeds expected cash on hand.')
    db.execute('INSERT INTO reception_cash_moves(shift_id,kind,amount,reference,actor_id,created_at) VALUES(?,?,?,?,?,?)',(shift['id'],kind,amount,reference,uid,stamp()));audit(db,uid,kind,dict(shift_id=shift['id'],amount=amount,reference=reference))
def void_receipt(db,uid,rid,note):
    shift=live_shift(db);row=db.execute('SELECT * FROM reception_receipts WHERE id=? AND shift_id=? AND voided_at IS NULL',(rid,shift['id'])).fetchone()
    if not row:raise ValueError('Only an unvoided receipt in the open shift can be corrected.')
    note=text(note,True)
    if row['collection_created']:
        collection=db.execute('SELECT * FROM collections WHERE id=?',(row['collection_id'],)).fetchone()
        if collection['status']!='paid' or cents(collection['amount_paid'])!=row['amount']:raise ValueError('The linked payment changed. Reconcile it before voiding.')
        db.execute("UPDATE collections SET status='cancelled' WHERE id=?",(row['collection_id'],))
    if row['item_id']:move(db,shift,row['item_id'],row['quantity'],'sale_void',uid,note,row['member_id'])
    db.execute('UPDATE reception_receipts SET voided_by=?,voided_at=?,void_reason=? WHERE id=?',(uid,stamp(),note,rid));audit(db,uid,'receipt_voided',dict(receipt_id=rid,note=note))

def lost_card(db,uid,form):
    shift=live_shift(db);serial=text(form.get('serial'),True);note=text(form.get('reference'),True)
    row=db.execute("SELECT * FROM reception_cards WHERE serial=? AND state='issued'",(serial,)).fetchone()
    if not row:raise ValueError('Choose a currently issued card.')
    db.execute("UPDATE reception_cards SET state='lost' WHERE id=?",(row['id'],));audit(db,uid,'card_reported_lost',dict(serial=serial,member_id=row['member_id'],reference=note,shift_id=shift['id']))
def outstanding(db,day):
    cancellations=[dict(r) for r in db.execute("""SELECT q.id AS query_id,q.reference,m.id AS member_id,m.member_ref,m.first_name,m.last_name,m.access_credential
        FROM queries q JOIN members m ON m.id=q.member_id
        WHERE q.category='cancellation' AND q.status NOT IN ('rejected','declined','withdrawn','cancelled','canceled')
        AND q.id=(SELECT MAX(q2.id) FROM queries q2 WHERE q2.member_id=q.member_id AND q2.category='cancellation' AND q2.status NOT IN ('rejected','declined','withdrawn','cancelled','canceled'))
        AND NOT EXISTS(SELECT 1 FROM reception_card_returns r WHERE r.query_id=q.id)
        ORDER BY q.id DESC""")]
    cancelled=[dict(r,query_id=None,reference='Cancelled membership') for r in db.execute("""SELECT m.id AS member_id,m.member_ref,m.first_name,m.last_name,m.access_credential FROM members m
        WHERE LOWER(m.member_status) IN ('cancelled','canceled')
        AND NOT EXISTS(SELECT 1 FROM queries q WHERE q.member_id=m.id AND q.category='cancellation' AND q.status NOT IN ('rejected','declined','withdrawn','cancelled','canceled'))
        AND NOT EXISTS(SELECT 1 FROM reception_card_returns r WHERE r.member_id=m.id) ORDER BY m.id DESC""")]
    start=db.execute('SELECT MIN(work_date) FROM reception_shifts').fetchone()[0]
    signed_today=set();pending=[]
    if start:
        from .staff_kpi import business_day
        signatures=db.execute("SELECT member_id,signed_at FROM contract_signatures WHERE signer_role='member' AND substr(CAST(signed_at AS TEXT),1,10)>=?",(str(start)[:7]+'-01',)).fetchall()
        signups=set()
        for row in signatures:
            signed_day=business_day(row['signed_at'])
            if signed_day and signed_day.isoformat()==day:signed_today.add(row['member_id'])
            if signed_day and start<=signed_day.isoformat()<=day:signups.add(row['member_id'])
        for mid in sorted(signups):
            if db.execute("SELECT 1 FROM reception_movements WHERE member_id=? AND kind='card_issue_new_signup'",(mid,)).fetchone():continue
            row=db.execute("SELECT id AS member_id,first_name,last_name,member_ref FROM members WHERE id=? AND LOWER(member_status) NOT IN ('cancelled','canceled')",(mid,)).fetchone()
            if row:pending.append(dict(row))
    unallocated=[]
    from .members import _is_discount
    for row in db.execute("""SELECT c.*,m.first_name,m.last_name FROM collections c JOIN members m ON m.id=c.member_id JOIN users u ON u.id=c.created_by
        WHERE substr(CAST(c.collection_date AS TEXT),1,10)=? AND c.status IN ('paid','partial') AND c.amount_paid>0
        AND (LOWER(u.department) IN ('reception','collections') OR u.role='admin')
        AND LOWER(c.method) IN ('cash','card','pos','credit_card','debit_card','eft','bank_transfer')
        AND NOT EXISTS(SELECT 1 FROM reception_receipts r WHERE r.collection_id=c.id AND r.voided_at IS NULL) ORDER BY c.id DESC""",(day,)):
        if not _is_discount(row):unallocated.append(dict(row))
    return dict(returns=cancellations+cancelled,signups=pending,signed_today=len(signed_today),unallocated=unallocated)

def report(db,sid):
    shift=db.execute('''SELECT s.*,u.full_name AS opener,c.full_name AS closer,r.full_name AS reviewer FROM reception_shifts s
        JOIN users u ON u.id=s.opened_by LEFT JOIN users c ON c.id=s.closed_by LEFT JOIN users r ON r.id=s.reviewed_by WHERE s.id=?''',(sid,)).fetchone()
    if not shift:raise ValueError('Shift report not found.')
    counts=[dict(r,variance=r['actual']-r['expected']) for r in db.execute('SELECT c.*,i.name FROM reception_counts c JOIN reception_items i ON i.id=c.item_id WHERE shift_id=? ORDER BY checkpoint DESC,i.id',(sid,))]
    receipts=[dict(r) for r in db.execute('SELECT r.*,u.full_name AS cashier FROM reception_receipts r JOIN users u ON u.id=r.actor_id WHERE shift_id=? ORDER BY r.id DESC',(sid,))]
    movements=[dict(r) for r in db.execute('SELECT m.*,i.name,u.full_name AS staff_name FROM reception_movements m JOIN reception_items i ON i.id=m.item_id JOIN users u ON u.id=m.actor_id WHERE shift_id=? ORDER BY m.id DESC',(sid,))]
    category_totals={key:sum(r['amount'] for r in receipts if r['category']==key and not r['voided_at']) for key in CATEGORIES}
    cash_moves=[dict(r) for r in db.execute('SELECT * FROM reception_cash_moves WHERE shift_id=? ORDER BY id',(sid,))]
    changes=[]
    for receipt_row in receipts:
        if receipt_row['collection_id'] and not receipt_row['voided_at']:
            source=db.execute('SELECT amount_paid,status FROM collections WHERE id=?',(receipt_row['collection_id'],)).fetchone()
            if not source or source['status'] not in ('paid','partial') or cents(source['amount_paid'])!=receipt_row['amount']:changes.append(receipt_row['id'])
    returns=[dict(r) for r in db.execute('SELECT r.*,m.first_name,m.last_name FROM reception_card_returns r JOIN members m ON m.id=r.member_id WHERE shift_id=?',(sid,))]
    return dict(shift=dict(shift),counts=counts,receipts=receipts,movements=movements,returns=returns,category_totals=category_totals,cash_moves=cash_moves,source_changes=changes,totals=totals(db,sid),outstanding_snapshot=json.loads(shift['outstanding_snapshot']) if shift['outstanding_snapshot'] else None)
