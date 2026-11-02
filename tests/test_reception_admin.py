from datetime import date
import pytest
from onecpase.database import get_db
from onecpase import reception_ops as ops

@pytest.fixture
def setup(app,monkeypatch):
    monkeypatch.setattr(ops,'today',lambda:date(2026,10,28))
    with app.app_context():
        db=get_db()
        for uid,role,dept in ((1,'admin','Management'),(2,'reception','Reception'),(3,'manager','Sales')):
            db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES(?,?,'x',?,?,?,1)",(uid,'rec'+str(uid),'Example '+str(uid),role,dept))
        db.execute("INSERT INTO members(id,first_name,last_name,id_number,member_ref) VALUES(1,'Example','Member','reception-fixture','E-001')")
        db.commit()
    return app

def form(db,**kwargs):
    result={'slot':'morning','opening_cash':'100','closing_cash':'100','closing_card':'0','closing_eft':'0','note':''}
    result.update({'count_'+str(i['id']):str(ops.stock(db,i['id'])) if i['baseline_set'] else '100' for i in ops.items(db)})
    result.update(kwargs);return result
def open_(db):
    with ops.transaction(db):return ops.open_shift(db,2,form(db))
def pay(**kwargs):return dict(category='arrears',method='cash',amount='50',member_id='1',reference='POS-001',token='unique-receipt-token-12345',**kwargs)

def test_initial_counts_are_unknown_then_physical_counts_establish_stock(setup):
    with setup.app_context():
        db=get_db();assert len(ops.items(db))==len(ops.DEFAULT_ITEMS);assert all(not r['baseline_set'] for r in ops.items(db));sid=open_(db)
        assert all(r['on_hand']==100 and r['baseline_set'] for r in ops.items(db))
        with pytest.raises(ValueError),ops.transaction(db):ops.open_shift(db,2,form(db))
        assert ops.current_shift(db)['id']==sid

def test_full_day_four_checks_cash_float_merchandise_and_manager_report(setup):
    with setup.app_context():
        db=get_db();sid=open_(db);water=next(i for i in ops.items(db) if i['code']=='water')
        with ops.transaction(db):
            ops.receipt(db,2,pay())
            ops.receipt(db,2,dict(category='merchandise',method='card',amount='40',item_id=water['id'],quantity='2',reference='POS-002',token='unique-merchandise-token'))
            ops.cash_move(db,2,dict(kind='cash_drop',amount='30',reference='Safe handover to manager'))
        assert ops.stock(db,water['id'])==98
        assert ops.totals(db,sid)['expected_cash']==12000
        with ops.transaction(db):ops.close_shift(db,2,form(db,closing_cash='120',closing_card='40'))
        report=ops.report(db,sid);assert report['totals']['total']==9000 and report['shift']['status']=='closed'
        assert report['outstanding_snapshot']['unallocated_payments']==0
        with ops.transaction(db):afternoon=ops.open_shift(db,2,form(db,slot='afternoon',opening_cash='120'))
        with ops.transaction(db):ops.close_shift(db,2,form(db,closing_cash='120'))
        assert db.execute('SELECT COUNT(*) FROM reception_counts').fetchone()[0]==len(ops.DEFAULT_ITEMS)*4
        assert ops.report(db,afternoon)['totals']['total']==0

def test_card_signup_signature_link_issue_return_and_damaged_card_not_reissued(setup):
    with setup.app_context():
        db=get_db();open_(db);data=dict(member_id='1',serial='CARD-001',reason='new_signup',reference='Signed new member')
        with pytest.raises(ValueError,match='sign'),ops.transaction(db):ops.issue_card(db,2,data)
        db.execute("INSERT INTO contract_signatures(member_id,signer_role,image_filename,signed_at) VALUES(1,'member','synthetic.png','2026-10-28T08:00:00+00:00')")
        db.commit()
        assert len(ops.outstanding(db,'2026-10-28')['signups'])==1
        with ops.transaction(db):ops.issue_card(db,2,data)
        assert not ops.outstanding(db,'2026-10-28')['signups']
        with pytest.raises(ValueError),ops.transaction(db):ops.issue_card(db,2,data)
        qid=db.execute("INSERT INTO queries(reference,member_id,category,description) VALUES('CAN-001',1,'cancellation','Synthetic cancellation')").lastrowid;db.commit()
        assert len(ops.outstanding(db,'2026-10-28')['returns'])==1
        with ops.transaction(db):ops.return_card(db,2,dict(member_id=1,serial='CARD-001',query_id=qid,reusable='no'))
        assert not ops.outstanding(db,'2026-10-28')['returns']
        cards=next(i for i in ops.items(db) if i['code']=='access_cards');assert cards['on_hand']==99
        with pytest.raises(ValueError),ops.transaction(db):ops.issue_card(db,2,data)
        assert len(ops.report(db,ops.current_shift(db)['id'])['returns'])==1

def test_stock_underflow_and_failed_cashup_roll_back_all_partial_changes(setup):
    with setup.app_context():
        db=get_db();open_(db);item=ops.items(db)[0]
        with pytest.raises(ValueError),ops.transaction(db):ops.stock_movement(db,2,dict(item_id=item['id'],kind='damaged',quantity='101',reference='Damaged count'))
        with pytest.raises(ValueError),ops.transaction(db):ops.close_shift(db,2,form(db,closing_cash='99'))
        assert ops.current_shift(db) and db.execute("SELECT COUNT(*) FROM reception_counts WHERE checkpoint='closing'").fetchone()[0]==0
        assert ops.stock(db,item['id'])==100
        with ops.transaction(db):ops.close_shift(db,2,form(db,closing_cash='99',note='R1 shortage under review'))
        assert ops.report(db,1)['shift']['expected_cash']==10000

def test_existing_collection_allocates_once_without_creating_duplicate_money(setup):
    with setup.app_context():
        db=get_db();open_(db)
        cid=db.execute("INSERT INTO collections(member_id,amount_paid,collection_date,method,status,created_by) VALUES(1,50,'2026-10-28','cash','paid',2)").lastrowid;db.commit()
        assert len(ops.outstanding(db,'2026-10-28')['unallocated'])==1
        with ops.transaction(db):ops.receipt(db,2,pay(collection_id=cid))
        with ops.transaction(db):ops.receipt(db,2,pay(collection_id=cid))
        assert db.execute('SELECT COUNT(*) FROM collections').fetchone()[0]==1
        assert ops.totals(db,1)['total']==5000
        new=pay(collection_id=cid);new['token']='another-unique-payment-token'
        with pytest.raises(ValueError),ops.transaction(db):ops.receipt(db,2,new)
        assert not ops.outstanding(db,'2026-10-28')['unallocated']

def test_void_retains_audit_reverses_merchandise_and_flags_changed_source(setup):
    with setup.app_context():
        db=get_db();open_(db)
        with ops.transaction(db):ops.receipt(db,2,pay())
        cid=db.execute('SELECT collection_id FROM reception_receipts').fetchone()[0]
        db.execute('UPDATE collections SET amount_paid=60 WHERE id=?',(cid,));db.commit()
        assert ops.report(db,1)['source_changes']==[1]
        with pytest.raises(ValueError),ops.transaction(db):ops.void_receipt(db,1,1,'Mistaken amount')
        db.execute('UPDATE collections SET amount_paid=50 WHERE id=?',(cid,));db.commit()
        with ops.transaction(db):ops.void_receipt(db,1,1,'Duplicate counter receipt')
        assert ops.totals(db,1)['total']==0
        assert db.execute('SELECT status FROM collections WHERE id=?',(cid,)).fetchone()[0]=='cancelled'
        assert db.execute('SELECT void_reason FROM reception_receipts').fetchone()[0]=='Duplicate counter receipt'

def test_routes_permissions_stale_shift_forms_and_review_lock(setup,client):
    def login(uid,role):
        with client.session_transaction() as s:s.update(user_id=uid,role=role,full_name='Example',permissions=[])
    login(3,'manager');assert client.get('/reception-admin/').status_code==403
    login(2,'reception');assert client.get('/reception-admin/').status_code==200
    with setup.app_context():db=get_db();sid=open_(db)
    response=client.post('/reception-admin/action',data=dict(action='receipt',shift_id='999',**pay()),follow_redirects=True)
    assert b'The shift changed' in response.data
    with setup.app_context():item=ops.items(get_db())[1]
    movement=dict(action='movement',shift_id=str(sid),token='stock-delivery-deduplication',item_id=str(item['id']),kind='received',quantity='10',reference='DELIVERY-001')
    assert client.post('/reception-admin/action',data=movement).status_code==302
    assert client.post('/reception-admin/action',data=movement).status_code==302
    with setup.app_context():assert ops.stock(get_db(),item['id'])==110
    with setup.app_context():
        db=get_db();assert db.execute('SELECT COUNT(*) FROM reception_receipts').fetchone()[0]==0
        with ops.transaction(db):ops.close_shift(db,2,form(db))
    assert client.post(f'/reception-admin/reports/{sid}/review').status_code==403
    login(1,'admin');assert client.post(f'/reception-admin/reports/{sid}/review').status_code==302
    report=client.get(f'/reception-admin/reports/{sid}');assert report.status_code==200 and b'Reviewed by' in report.data
    with setup.app_context():
        db=get_db()
        with pytest.raises(ValueError),ops.transaction(db):ops.receipt(db,2,pay())

def test_phase_gate_places_reception_with_collections():
    from onecpase.phases import is_enabled
    assert not is_enabled('reception_admin',1)
    assert is_enabled('reception_admin',2) and is_enabled('reception_admin','all')

def test_voided_import_can_be_correctly_reallocated_without_new_money(setup):
    with setup.app_context():
        db=get_db();open_(db)
        cid=db.execute("INSERT INTO collections(member_id,amount_paid,collection_date,method,status,created_by) VALUES(1,50,'2026-10-28','cash','paid',2)").lastrowid;db.commit()
        with ops.transaction(db):ops.receipt(db,2,pay(collection_id=cid))
        with ops.transaction(db):ops.void_receipt(db,1,1,'Wrong receipt category')
        assert len(ops.outstanding(db,'2026-10-28')['unallocated'])==1
        corrected=pay(collection_id=cid);corrected['token']='corrected-allocation-token';corrected['category']='card_fee'
        with ops.transaction(db):ops.receipt(db,2,corrected)
        assert ops.totals(db,1)['total']==5000
        assert db.execute('SELECT COUNT(*) FROM collections').fetchone()[0]==1

@pytest.mark.parametrize('value',['NaN','Infinity','-1','bad'])
def test_money_rejects_non_finite_negative_or_invalid(value):
    with pytest.raises(ValueError):ops.cents(value)
