from onecpase.collection_call_script import build_return_guidance
from onecpase.database import get_db
from tests.test_collections_decision import _call_task_for_arrangement, _login


def test_repayment_and_reactivation_use_distinct_requirements():
    guide = build_return_guidance({'member_status':'Inactive'}, {'total_arrears':1250},
                                  {'access':'BLOCKED','status':'INACTIVE'})
    assert guide['same_premium_minimum'] == 625
    assert 'If reactivation is needed' in guide['say']
    assert 'R625.00 (50%' in guide['say']
    assert 'received and verified' in guide['say']
    assert 'does not itself reactivate' in guide['say']
    assert any('manager review' in line for line in guide['conditions'])
    assert 'lower upfront proposal may still be discussed' in guide['cue']
    assert 'authorised campaigns' in guide['cue']


def test_allowed_collections_check_does_not_override_membership_or_access_block():
    guide = build_return_guidance({'member_status':'Blocked','gym_access_status':'blocked'},
                                  {'total_arrears':500}, {'access':'ALLOWED'})
    assert any('Membership status is Blocked' in line for line in guide['conditions'])
    assert any('gym access record is blocked' in line for line in guide['conditions'])
    assert any('must also permit entry' in line for line in guide['conditions'])


def test_access_upfront_is_not_confused_with_same_premium_reactivation_amount():
    guide = build_return_guidance({'member_status':'Active'}, {'total_arrears':1000},
        {'access':'BLOCKED','upfront_required':True,'required_upfront_amount':300})
    assert 'R500.00' in guide['say']
    assert any('R300.00' in line and 'separate' in line for line in guide['conditions'])


def test_paid_account_has_no_reactivation_payment_demand():
    guide = build_return_guidance({'member_status':'Active'}, {'total_arrears':0}, {'access':'ALLOWED'})
    assert guide['same_premium_minimum'] == 0
    assert '50%' not in guide['say'] and 'upfront requirement' not in guide['say']


def test_return_offer_explains_rules_without_rejecting_repayment_or_granting_access(app,client):
    task = _call_task_for_arrangement(app,'none')
    _login(client,task)
    with app.app_context():
        db=get_db()
        db.execute("UPDATE members SET member_status='Blocked',gym_access_status='blocked' WHERE id=?",(task['member_id'],))
        db.commit()
    url=f"/collections/call-queue/{task['id']}/offer-preview"
    quote=client.get(url,query_string={'conversation_intention':'return','upfront_offered':'10',
        'monthly_affordable':'100','intention':'instalments'}).get_json()
    assert quote['return_guidance']['same_premium_minimum'] == 125
    assert quote['upfront_offered'] == 10
    assert quote['arrangement']
    assert 'R125.00' in quote['return_guidance']['say']
    with app.app_context():
        db=get_db()
        assert tuple(db.execute('SELECT member_status,gym_access_status FROM members WHERE id=?',(task['member_id'],)).fetchone()) == ('Blocked','blocked')
        assert db.execute('SELECT count(*) FROM ptp_agreements').fetchone()[0] == 0
        assert db.execute('SELECT sum(amount_paid) FROM collections WHERE member_id=?',(task['member_id'],)).fetchone()[0] == 0
