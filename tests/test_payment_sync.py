from datetime import date
from collections import Counter
import sqlite3

import pytest

from onecpase.database import get_db
from onecpase import payment_sync as ps


def member(db, ref='1234567'):
    return db.execute("""INSERT INTO members(first_name,last_name,id_number,contact,itensity_ref)
        VALUES('Test','Member',?,'0820000000',?)""",('ID'+ref,ref)).lastrowid


def itensity(key='tx1', kind='Cash Payment', day='2026-10-20', amount='300'):
    return dict(id=key,uid='1234567',date=day,type=kind,amount=amount,desc=kind,stamp='2026-10-20')


def nupay(status='Success'):
    return dict(mandate_id='12345678',client_reference='1234567',instalment='1',
                instalment_amount='300',action_date='2026-10-19',cycle_date='2026-10-20',status=status)


def test_imports_preserve_separate_equal_payments_and_are_idempotent(app):
    with app.app_context():
        db=get_db();member(db);ps.ensure_schema(db)
        rows=[itensity('a'),itensity('b')]
        assert ps.import_itensity(db,rows)['inserted']==2
        assert ps.import_itensity(db,rows)['already_recorded']==2
        assert db.execute('SELECT sum(amount_paid) FROM collections').fetchone()[0]==600
        changed=itensity('a',amount='400')
        assert ps.import_itensity(db,[changed])['needs_review']==1
        assert db.execute('SELECT sum(amount_paid) FROM collections').fetchone()[0]==600


@pytest.mark.parametrize('first',['nupay','itensity'])
def test_same_payment_from_both_sources_counts_once(app,first):
    with app.app_context():
        db=get_db();member(db);ps.ensure_schema(db)
        its=[itensity('fee','Recurring Fee','2026-10-01'),itensity(kind='Debit Order Payment')]
        if first=='nupay':
            ps.import_nupay(db,[nupay()]);ps.import_itensity(db,its,include_debit_orders=True)
        else:
            ps.import_itensity(db,its,include_debit_orders=True);ps.import_nupay(db,[nupay()])
        ps.import_nupay(db,[nupay()]);ps.import_itensity(db,its,include_debit_orders=True)
        assert db.execute('SELECT count(*) FROM collections').fetchone()[0]==2
        assert db.execute('SELECT sum(amount_paid) FROM collections').fetchone()[0]==300
        assert db.execute('SELECT sum(outstanding_balance) FROM collections').fetchone()[0]==300


def test_legacy_ledger_is_reconciled_instead_of_duplicated(app):
    with app.app_context():
        db=get_db();mid=member(db);ps.ensure_schema(db)
        db.execute("""INSERT INTO collections(member_id,collection_date,amount_paid,outstanding_balance,
            method,status,notes) VALUES(?,'2026-10-20',300,0,'debicheck','paid',
            'WEBAPPL ledger import | type=Debit Order Payment | entry_id=42')""",(mid,))
        assert ps.import_itensity(db,[itensity(kind='Debit Order Payment')],include_debit_orders=True)['reconciled_existing']==1
        assert ps.import_nupay(db,[nupay()])['reconciled_existing']==1
        assert db.execute('SELECT count(*) FROM collections').fetchone()[0]==1


def test_ambiguous_payments_are_held_for_review(app):
    with app.app_context():
        db=get_db();member(db);ps.ensure_schema(db)
        ps.import_itensity(db,[itensity('a','Debit Order Payment'),itensity('b','Debit Order Payment')],include_debit_orders=True)
        assert ps.import_nupay(db,[nupay()])['needs_review']==1
        assert db.execute('SELECT sum(amount_paid) FROM collections').fetchone()[0]==600


def test_missing_members_and_unknown_transactions_stay_visible(app):
    with app.app_context():
        db=get_db();ps.ensure_schema(db)
        assert ps.import_itensity(db,[itensity()])['unmatched']==1
        member(db)
        assert ps.import_itensity(db,[itensity(kind='Unexpected')])['needs_review']==1
        assert ps.import_itensity(db,[itensity(amount='NaN')])['unreadable']==1
        assert db.execute('SELECT count(*) FROM collections').fetchone()[0]==0


def test_automated_itensity_defers_mirrored_debits_to_nupay(app):
    with app.app_context():
        db=get_db();member(db);ps.ensure_schema(db)
        counts=ps.import_itensity(db,[itensity(kind='Debit Order Payment'),itensity('failure','Debit Order Unpaid')])
        assert counts['deferred_to_nupay']==2
        assert db.execute('SELECT count(*) FROM collections').fetchone()[0]==0
        ps.import_nupay(db,[nupay()])
        assert db.execute('SELECT sum(amount_paid) FROM collections').fetchone()[0]==300


def test_nupay_repeated_failures_and_references_with_spaces_are_idempotent(app):
    with app.app_context():
        db=get_db();member(db);ps.ensure_schema(db)
        success=dict(nupay(),client_reference='1234567 - Test Member')
        failures=[dict(nupay('Failed'),action_date='2026-10-17'),
                  dict(nupay('Failed'),action_date='2026-10-18')]
        ps.import_nupay(db,[success,*failures])
        assert ps.import_nupay(db,[success,*failures])['payments_added']==0
        assert ps.import_nupay(db,[success,*failures])['charges_marked_failed']==0
        assert db.execute('SELECT sum(amount_paid) FROM collections').fetchone()[0]==300


def test_failed_fetch_records_failure_without_clearing_last_success(app,tmp_path,monkeypatch):
    path=tmp_path/'tenant.db'
    db=sqlite3.connect(path);ps.ensure_schema(db)
    db.execute("INSERT INTO payment_sync_state(source,status,last_success_at) VALUES('itensity','success','2026-10-01T05:30:00+02:00')")
    db.commit();db.close()
    from scripts import pull_itensity_transactions as pull
    def fail(*args):raise RuntimeError('private portal detail')
    monkeypatch.setattr(pull,'pull_transactions',fail)
    ps.run_source(app,dict(slug='test',db_path=str(path)),'itensity')
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row
    row=db.execute('SELECT * FROM payment_sync_state').fetchone()
    assert row['status']=='failed' and row['last_success_at']=='2026-10-01T05:30:00+02:00'
    assert 'private' not in row['error']
    db.close()


@pytest.mark.parametrize('fail_import',[False,True])
def test_source_import_and_status_are_atomic(app,tmp_path,monkeypatch,fail_import):
    from scripts import pull_itensity_transactions as pull
    from onecpase import scheduler
    with app.app_context():
        db=get_db();member(db);db.commit()
        path=db.execute('PRAGMA database_list').fetchone()[2]
    monkeypatch.setattr(app,'root_path',str(tmp_path/'onecpase'))
    monkeypatch.setattr(pull,'pull_transactions',lambda *a:[itensity()])
    monkeypatch.setattr(scheduler,'_backup_before_write',lambda *a:tmp_path/'backup.db')
    if fail_import:
        original=ps.import_itensity
        def broken(db,rows):
            original(db,rows)
            raise RuntimeError('failure after a payment insert')
        monkeypatch.setattr(ps,'import_itensity',broken)
    ps.run_source(app,dict(slug='test',db_path=path),'itensity')
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        state=db.execute('SELECT * FROM payment_sync_state').fetchone()
        assert state['status']==('failed' if fail_import else 'success')
        assert bool(state['last_success_at']) is not fail_import
        assert db.execute('SELECT count(*) FROM collections').fetchone()[0]==(0 if fail_import else 1)


def test_csv_fallback_does_not_duplicate_automatic_imports(app):
    from scripts.import_itensity_transactions import import_transactions
    with app.app_context():
        db=get_db();member(db);ps.ensure_schema(db)
        ps.import_itensity(db,[itensity()]);db.commit()
        result,_=import_transactions(db,[dict(Ref='1234567',Date='2026-10-20',
            Type='Cash Payment',Amount='300',Description='Payment')])
        assert result['duplicates']==1
        assert db.execute('SELECT sum(amount_paid) FROM collections').fetchone()[0]==300


def test_itensity_pagination_rejects_truncation_and_repeated_ids(monkeypatch):
    from scripts import itensity_portal as portal
    from scripts.pull_itensity_transactions import read_window
    responses=iter([dict(totalCount=2,items=[itensity()]),dict(totalCount=2,items=[])])
    monkeypatch.setattr(portal,'fetch_json',lambda *a:next(responses))
    with pytest.raises(RuntimeError,match='incomplete'):
        read_window(None,date(2026,10,1),date(2026,10,25),1)
    monkeypatch.setattr(portal,'fetch_json',lambda *a:dict(totalCount=2,items=[itensity()]))
    with pytest.raises(RuntimeError,match='repeated'):
        read_window(None,date(2026,10,1),date(2026,10,25),1)


def test_dashboard_reports_payment_sync_separately_from_refresh(app,client):
    with app.app_context():
        db=get_db()
        db.execute("INSERT OR REPLACE INTO users (id,username,password_hash,full_name,role,active) VALUES (1,'owner','x','Owner','admin',1)")
        db.commit()
    with client.session_transaction() as session:
        session['user_id']=1;session['tenant_slug']=None
    response=client.get('/dashboard')
    assert response.status_code==200
    assert b'View refreshed' in response.data and b'Last successful sync: Never' in response.data
