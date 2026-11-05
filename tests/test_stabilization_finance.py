from onecpase.database import get_db
from onecpase.financial_reporting import totals, monthly_totals
from onecpase.reports import _load_finance, _date_range
from onecpase.collections_workspace import financial_report,performance_report
from onecpase.reporting_cycle import cycle_key
from datetime import date


def row(charge=0,paid=0,notes='',member=1,status='paid',day='2026-10-20'):
    return dict(member_id=member,outstanding_balance=charge,amount_paid=paid,notes=notes,status=status,collection_date=day)


def test_cash_discount_and_settlement_are_distinct():
    entries=[row(charge=150),row(paid=100),row(paid=50,notes='type=Discount')]
    assert totals(entries)==dict(total_collected=100,total_billed=150,total_credits=50,total_records=3,total_outstanding=0)
    assert monthly_totals(entries)[0]['outstanding']==0


def test_same_row_settlement_has_no_remaining_debt():
    assert monthly_totals([row(charge=100,paid=100)])[0]['outstanding']==0


def test_dashboard_converts_offset_timestamps_before_period_comparison():
    from onecpase.dashboard import _event_datetime
    from datetime import datetime
    assert _event_datetime('2026-11-01T23:30:00Z')==datetime(2026,11,2,1,30)


def test_another_members_overpayment_cannot_hide_debt():
    assert totals([row(charge=100,member=1),row(paid=200,member=2)])['total_outstanding']==100


def test_unconfirmed_payments_and_nonfinite_values_are_not_cash():
    assert totals([row(paid=100,status='pending'),row(paid='NaN'),row(paid='Infinity')])['total_collected']==0
    assert totals([row(paid='.10'),row(paid='.20')])['total_collected']==.30


def test_reports_share_cash_classification_and_net_period_balance(app):
    with app.app_context():
        db=get_db()
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number,package) VALUES ('Cash','Test','CASH-TEST','Gold')").lastrowid
        start,end=_date_range('this_month')
        for charge,paid,status,notes in [(150,0,'pending','type=Recurring Fee'),(0,100,'paid','type=Cash Payment'),(0,50,'paid','type=Discount')]:
            db.execute('INSERT INTO collections(member_id,outstanding_balance,amount_paid,collection_date,status,notes) VALUES (?,?,?,?,?,?)',(mid,charge,paid,start,status,notes))
        db.commit()
        finance=_load_finance(db,'this_month')
        assert finance['totals']['total_collected']==100
        assert finance['totals']['total_outstanding']==0
        assert finance['monthly_collections'][0]['outstanding']==0
        assert sum(r['total'] for r in finance['payment_methods'])==100
        assert sum(r['collected'] for r in finance['revenue_by_package'])==100
        cycle=cycle_key(date.fromisoformat(start))
        assert financial_report(db,cycle)['collected']==100
        assert performance_report(db,cycle)['totals']['amount_collected']==100


def test_discount_credit_does_not_satisfy_a_paid_registration_fee(app):
    from onecpase.applications import registration_fee_paid
    with app.app_context():
        db=get_db()
        mid=db.execute("INSERT INTO members(first_name,last_name,id_number,joining_fee) VALUES ('Fee','Test','FEE-TEST',100)").lastrowid
        db.execute("INSERT INTO collections(member_id,amount_paid,payment_purpose,status,notes) VALUES (?,100,'registration_fee','paid','type=Discount')",(mid,))
        assert not registration_fee_paid(db,mid)
        db.execute("INSERT INTO collections(member_id,amount_paid,payment_purpose,status,notes) VALUES (?,100,'registration_fee','paid','type=Cash Payment')",(mid,))
        assert registration_fee_paid(db,mid)
