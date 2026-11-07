import json

from onecpase.collection_arrangement import arrangement_confirmation
from onecpase.database import get_db
from onecpase.ptp_grant import evaluate_grant, refresh_member_grants


def seed(db, status='approved'):
    mid = db.execute("INSERT INTO members(first_name,last_name,id_number,monthly_installment) VALUES('Lerato','Dube','ARR-CLOSE',300)").lastrowid
    pid = db.execute("""INSERT INTO ptp_agreements(member_id,promise_date,payment_method,arrears_amount,
        discount_pct,discount_amount,remaining_balance,payment_count,first_collection_date,recovery_installment,
        grant_status,kpi_contact_channel,manager_approval_status)
        VALUES(?,'2027-01-31','debicheck',1250,20,250,1000,3,'2027-01-31',333.34,'awaiting_debicheck','call','not_required')""", (mid,)).lastrowid
    did = db.execute("""INSERT INTO debicheck_mandates(member_id,merchant_id,client_ref1,client_ref2,
        account_name,account_type,account_number,branch_code,submit_date,status)
        VALUES(?,'test','test','test','Test Member','1','0000000000','000000','2027-01-31',?)""", (mid,status)).lastrowid
    db.execute("INSERT INTO ptp_mandate_links(ptp_id,purpose,amount,mandate_id) VALUES(?,'Arrangement (PTP)',333.34,?)", (pid,did))
    return mid,pid,did


def statements(db, mid, months):
    analysis = {'cashflow':{'months':months,'enough_months':months >= 2}}
    db.execute("INSERT INTO member_documents(member_id,document_type,original_name,stored_name,analysis_json) VALUES(?,'Bank Statement','s.pdf','s.pdf',?)", (mid,json.dumps(analysis)))


def test_discount_cannot_be_granted_without_two_months_even_with_manager_clearance(app):
    with app.app_context():
        db = get_db()
        mid,pid,_ = seed(db)
        assert evaluate_grant(db,pid) == 'awaiting_prerequisites'
        assert not arrangement_confirmation(db,mid)['say']
        db.execute('UPDATE ptp_agreements SET discount_pct=0,discount_amount=0 WHERE id=?', (pid,))
        assert evaluate_grant(db,pid) == 'awaiting_prerequisites'  # Call arrangements also need the statements without a discount.
        statements(db,mid,1)
        db.execute("UPDATE ptp_agreements SET manager_approval_status='approved' WHERE id=?", (pid,))
        assert evaluate_grant(db,pid) == 'awaiting_prerequisites'
        statements(db,mid,2)
        refresh_member_grants(db,mid)
        assert db.execute('SELECT grant_status FROM ptp_agreements WHERE id=?', (pid,)).fetchone()[0] == 'granted'


def test_statement_evidence_alone_does_not_grant_submitted_mandate(app):
    with app.app_context():
        db = get_db()
        mid,pid,did = seed(db,'submitted')
        statements(db,mid,2)
        assert evaluate_grant(db,pid) == 'awaiting_debicheck'
        assert not arrangement_confirmation(db,mid)['say']
        db.execute("UPDATE debicheck_mandates SET status='approved' WHERE id=?", (did,))
        assert evaluate_grant(db,pid) == 'granted'
        assert arrangement_confirmation(db,mid)['approved']


def test_pending_terms_can_be_read_back_on_call_without_granting_approval(app):
    with app.app_context():
        db = get_db()
        mid,pid,_ = seed(db,'submitted')
        db.execute("UPDATE ptp_agreements SET upfront_amount=100,remaining_balance=900,manager_approval_status='pending' WHERE id=?", (pid,))
        before = tuple(db.execute('SELECT grant_status,ptp_status FROM ptp_agreements WHERE id=?',(pid,)).fetchone())
        confirmation = arrangement_confirmation(db,mid)
        assert not confirmation['approved'] and confirmation['say'] is None
        proposal = confirmation['proposal_say']
        for text in ('R1,250.00','R250.00','R100.00','31 Jan 2027','31 Mar 2027',
                     'not yet approved','Manager approval','received and verified',
                     'does not itself restore gym access','Do you agree'):
            assert text in proposal
        assert len(confirmation['outstanding']) == 4
        assert before == tuple(db.execute('SELECT grant_status,ptp_status FROM ptp_agreements WHERE id=?',(pid,)).fetchone())
        statements(db,mid,2)
        assert 'bank statements must' not in arrangement_confirmation(db,mid)['proposal_say']


def test_broken_arrangement_is_not_presented_as_a_new_proposal(app):
    with app.app_context():
        db = get_db()
        mid,pid,_ = seed(db)
        db.execute("UPDATE ptp_agreements SET ptp_status='broken' WHERE id=?", (pid,))
        confirmation = arrangement_confirmation(db,mid)
        assert not confirmation['say'] and not confirmation['proposal_say']


def test_confirmation_uses_saved_schedule_rounding_and_month_end_dates(app):
    with app.app_context():
        db = get_db()
        mid,pid,_ = seed(db)
        statements(db,mid,2)
        evaluate_grant(db,pid)
        confirmation = arrangement_confirmation(db,mid)
        assert confirmation['schedule'] == [
            {'date':'2027-01-31','amount':333.34},
            {'date':'2027-02-28','amount':333.34},
            {'date':'2027-03-31','amount':333.32}]
        for text in ('R1,250.00', 'R250.00', 'R1,000.00', '3 monthly payment(s)',
                     'R333.34', 'R333.32', '31 Mar 2027', 'If every agreed payment is received',
                     'ongoing membership fees are separate'):
            assert text in confirmation['say']


def test_confirmation_does_not_invent_a_schedule_or_date(app):
    with app.app_context():
        db = get_db()
        mid,pid,_ = seed(db)
        statements(db,mid,2)
        evaluate_grant(db,pid)
        db.execute('UPDATE ptp_agreements SET first_collection_date=NULL,payment_count=0 WHERE id=?',(pid,))
        confirmation = arrangement_confirmation(db,mid)
        assert confirmation['approved'] and not confirmation['say'] and not confirmation['schedule']
        assert not confirmation['proposal_say']


def test_saved_payment_rows_take_precedence_over_a_recalculated_plan(app):
    with app.app_context():
        db = get_db()
        mid,pid,_ = seed(db)
        statements(db,mid,2)
        evaluate_grant(db,pid)
        db.execute("INSERT INTO payment_plan_items(ptp_id,member_id,instalment_number,amount,due_date) VALUES(?,?,1,1000,'2027-02-10')", (pid,mid))
        confirmation = arrangement_confirmation(db,mid)
        assert confirmation['schedule'] == [{'date':'2027-02-10','amount':1000}]
        assert '10 Feb 2027' in confirmation['say'] and '1 monthly payment(s)' in confirmation['say']


def test_discounted_full_settlement_preview_also_needs_debicheck_approval():
    from onecpase.collections_decision import decide_offer
    inputs = dict(arrears=1000, months_owing=3, intention='settle', upfront_received=750,
                  flags={'statement_months_ready':True})
    pending = decide_offer(**inputs)
    assert pending['decision'] == 'conditions_outstanding'
    assert any(item['key']=='discount_debicheck' and not item['met'] for item in pending['checklist'])
    assert decide_offer(**inputs, debicheck_status='approved')['decision'] == 'eligible'
