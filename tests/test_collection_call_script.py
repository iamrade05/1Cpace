from datetime import date, datetime, timezone

import pytest

from onecpase.collection_call_script import build_call_script, conversation_notes
from onecpase.database import get_db


def script(visit=None, balance=1250.5):
    return build_call_script({'first_name':'Lerato', 'last_name':'Dube'},
                             {'full_name':'Thandi Mokoena', 'username':'reception'},
                             visit, {'total_arrears':balance}, today=date(2026,11,2))


@pytest.mark.parametrize('value', ['2026-09-01 08:30:00', datetime(2026,9,1,8,30), date(2026,9,1)])
def test_script_personalises_greeting_last_visit_and_ledger_amount(value):
    guide = script({'last_visited':value})
    assert 'am I speaking to Lerato Dube?' in guide['steps'][0]['say']
    assert 'Thandi Mokoena from Elevate Health and Fitness' in guide['steps'][0]['say']
    assert '01 Sep 2026' in guide['steps'][1]['say']
    assert 'keeping you away' in guide['steps'][1]['say']
    assert 'overdue balance of R1,250.50' in guide['steps'][2]['say']
    assert 'Does this match your understanding' in guide['steps'][2]['say']
    assert 'would you like to return' in guide['steps'][3]['say']
    assert 'R1,250.50' in guide['steps'][4]['say']
    assert 'afford to pay upfront' in guide['steps'][4]['say']
    assert 'achieving your fitness goals' in guide['paths']['return']['say']
    assert 'settle the current arrears' in guide['paths']['moved']['say']
    assert 'settle the current arrears' in guide['paths']['not_returning']['say']
    assert all(step['cue'] for step in guide['steps'])


@pytest.mark.parametrize('visit', [None, {'last_visited':None}, {'last_visited':'bad data'}, {'last_visited':'2027-01-01'}])
def test_script_does_not_invent_missing_or_future_attendance(visit):
    guide = script(visit)
    assert guide['visit_label'] == 'No confirmed visit record'
    assert 'When were you last able to visit' in guide['steps'][1]['say']
    assert 'keeping you away' not in guide['steps'][1]['say']


def test_recent_visit_gets_a_training_check_in_and_sast_date():
    guide = script({'last_visited':datetime(2026,11,1,23,30,tzinfo=timezone.utc)})
    assert guide['visit_label'] == '02 Nov 2026'
    assert 'How has your training been' in guide['steps'][1]['say']
    assert 'missed seeing you' not in guide['steps'][1]['say']


@pytest.mark.parametrize('balance', [0, -50, None])
def test_paid_accounts_do_not_get_an_overdue_or_instalment_claim(balance):
    guide = script(balance=balance)
    assert guide['overdue_label'] == 'R0.00'
    assert 'no overdue balance' in guide['steps'][2]['say']
    assert 'instalments' not in guide['steps'][3]['say']


def test_names_have_natural_fallbacks():
    guide = build_call_script({}, {'full_name':' ', 'username':'receptionist'}, None, {})
    assert 'may I speak to the account holder?' in guide['steps'][0]['say']
    assert 'speaking to receptionist' in guide['steps'][0]['say']


def test_profile_script_connects_membership_payment_and_call_commitment():
    guide = build_call_script(
        {'first_name':'Boitumelo', 'join_date':'2025-06-15', 'monthly_installment':300},
        {'full_name':'Reception'}, {'last_visited':'2026-10-01'}, {'total_arrears':900},
        today=date(2026,11,2), payment_stats={'last_payment_date':'2026-08-15', 'last_payment_amount':250})
    assert 'joined us on 15 Jun 2025' in guide['steps'][1]['say']
    assert 'R250.00 on 15 Aug 2026' in guide['steps'][2]['say']
    assert 'R900.00' in guide['steps'][2]['say']
    assert 'R300.00' in guide['steps'][4]['cue']
    assert 'separately from arrears' in guide['steps'][4]['cue']
    close = guide['steps'][-1]
    assert 'exact payment date' in close['say'] and 'payment method' in close['say']
    assert 'what time' in close['say']
    assert 'do not record a promise to pay' in close['cue']
    agreement = guide['steps'][-2]
    assert 'while we are on the call' in agreement['say']
    assert 'before saving the call' in agreement['cue']
    assert 'separate from approval' in agreement['cue']


@pytest.mark.parametrize('stamp', [None, '', 'bad data', '2027-01-01'])
def test_unconfirmed_membership_and_payment_dates_prompt_questions(stamp):
    guide = build_call_script({'join_date':stamp}, {}, None, {'total_arrears':200},
        today=date(2026,11,2), payment_stats={'last_payment_date':stamp,'last_payment_amount':500})
    assert guide['joined_label'] == 'Membership start not confirmed'
    assert 'joined us on' not in guide['steps'][1]['say']
    assert 'latest successful payment' not in guide['steps'][2]['say']
    assert 'When did you last make a payment?' in guide['steps'][2]['say']
    assert 'never paid' not in guide['steps'][2]['say']


def test_existing_issues_and_arrangements_are_reviewed_before_a_new_offer():
    guide = build_call_script({'member_status':'Blocked'}, {}, None, {'total_arrears':700},
        queries=[{'status':'open'}], arrangements=[{'ptp_status':'partially_paid'}],
        previous_calls=[{'outcome':'asked_callback'}])
    words = ' '.join(p['say'] + p['cue'] for p in guide['context'])
    assert 'membership as blocked' in words and 'does not restore access' in words
    assert 'unresolved query' in words and 'reconciliation' in words
    assert 'already a payment arrangement' in words and 'approval status' in words
    assert 'previously asked us to call back' in words
    assert 'missed without verifying' in words


def test_resolved_history_and_settled_plans_do_not_trigger_live_issue_prompts():
    guide = build_call_script({'member_status':'Active'}, {}, None, {'total_arrears':0},
        queries=[{'status':'closed'}, {'status':'resolved'}],
        arrangements=[{'ptp_status':'paid'}, {'ptp_status':'cancelled'}],
        previous_calls=[{'outcome':'reminder_delivered'}, {'outcome':'asked_callback'}])
    assert guide['context'] == []
    assert not any(step.get('negotiate') for step in guide['steps'])


def test_call_profile_uses_logged_in_caller_and_current_member_facts(app, client, monkeypatch):
    from tests.test_collections_call_queue import _seed_callers_and_members
    from onecpase.collections import _ensure_daily_call_tasks
    monkeypatch.setattr('onecpase.members._get_last_gym_visit', lambda db, member: {'last_visited':'2026-09-01', 'days_ago':62, 'source':'Test', 'shift':'Morning'})
    with app.app_context():
        _seed_callers_and_members(2)
        db = get_db()
        _ensure_daily_call_tasks(db, date(2026,8,12))
        task = dict(db.execute('SELECT * FROM collection_call_tasks ORDER BY id LIMIT 1').fetchone())
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(20,'review-admin','x','Actual Receptionist','admin',1)")
        db.execute("UPDATE members SET first_name=?,last_name='Dube' WHERE id=?", ('Lerato <client>', task['member_id']))
        db.commit()
    with client.session_transaction() as state:
        state.update(user_id=20, role='admin', permissions=['collections_call_queue'])
    path = f"/collections/call-queue/{task['id']}/profile"
    response = client.get(path)
    assert response.status_code == 200
    assert b'Actual Receptionist from Elevate Health and Fitness' in response.data
    assert b'Lerato &lt;client&gt; Dube' in response.data
    assert b'overdue balance of R250.00' in response.data
    assert b'01 Sep 2026' in response.data
    assert b'href="#notes-input"' in response.data
    with app.app_context():
        db = get_db()
        db.execute("UPDATE collections SET amount_paid=outstanding_balance,status='paid' WHERE member_id=?", (task['member_id'],))
        db.commit()
    assert b'no overdue balance' in client.get(path).data
    # An unrelated caller cannot load this member's script through the task URL.
    with client.session_transaction() as state:
        state.update(user_id=12, role='reception', permissions=['collections_call_queue'])
    denied = client.get(path)
    assert denied.status_code == 302 and b'overdue balance of' not in denied.data


def test_call_profile_uses_cash_receipt_not_discount_or_failed_debit(app, client):
    from tests.test_collections_call_queue import _seed_callers_and_members
    from onecpase.collections import _ensure_daily_call_tasks
    with app.app_context():
        _seed_callers_and_members(1)
        db = get_db()
        _ensure_daily_call_tasks(db, date(2026,8,12))
        task = dict(db.execute('SELECT * FROM collection_call_tasks LIMIT 1').fetchone())
        db.execute("UPDATE members SET join_date='2025-06-15',member_status='Blocked' WHERE id=?", (task['member_id'],))
        for stamp, amount, status, note in [
            ('2026-06-15',100,'paid','type=Recurring Fee'),
            ('2026-07-01',50,'partial','type=Recurring Fee'),
            ('2026-07-15',200,'paid','type=Discount'),
            ('2026-08-01',300,'failed','type=Recurring Fee')]:
            db.execute('''INSERT INTO collections(member_id,collection_date,outstanding_balance,amount_paid,status,notes)
                VALUES(?,?,300,?,?,?)''', (task['member_id'],stamp,amount,status,note))
        db.commit()
        before = [tuple(r) for r in db.execute('SELECT id,amount_paid,status FROM collections ORDER BY id')]
    with client.session_transaction() as state:
        state.update(user_id=task['assigned_to'],role='reception',permissions=['collections_call_queue'])
    response = client.get(f"/collections/call-queue/{task['id']}/profile")
    assert response.status_code == 200
    assert b'joined us on 15 Jun 2025' in response.data
    assert b'latest successful payment recorded is R50.00 on 01 Jul 2026' in response.data
    assert b'latest successful payment recorded is R200.00' not in response.data
    assert b'membership as blocked' in response.data
    assert b'Agree the next step before ending the call' in response.data
    with app.app_context():
        db = get_db()
        assert before == [tuple(r) for r in db.execute('SELECT id,amount_paid,status FROM collections ORDER BY id')]
        assert db.execute('SELECT COUNT(*) FROM ptp_agreements').fetchone()[0] == 0


def test_training_context_does_not_invent_return_commitment_or_financial_terms():
    moved = conversation_notes({'conversation_intention':'moved', 'training_barrier':'Relocated to Durban', 'client_goal':'Stale hidden goal'})
    assert 'Has moved away' in moved and 'Relocated to Durban' in moved
    assert 'Stale hidden goal' not in moved and 'promise' not in moved.lower()
    returning = conversation_notes({'conversation_intention':'return', 'client_goal':'Improve fitness', 'training_barrier':'Shift work'})
    assert 'Improve fitness' in returning and 'Shift work' in returning
    with pytest.raises(ValueError):
        conversation_notes({'conversation_intention':'invalid'})
    assert conversation_notes({}) == ''


def test_client_intention_is_saved_in_existing_call_notes(app, client):
    from tests.test_collections_call_queue import _seed_callers_and_members
    from onecpase.collections import _ensure_daily_call_tasks
    with app.app_context():
        _seed_callers_and_members(2)
        db = get_db()
        _ensure_daily_call_tasks(db, date(2026,8,12))
        task = dict(db.execute('SELECT * FROM collection_call_tasks ORDER BY id LIMIT 1').fetchone())
    with client.session_transaction() as state:
        state.update(user_id=task['assigned_to'], role='reception', permissions=['collections_call_queue'])
    result = client.post(f"/collections/call-queue/{task['id']}/result", data={
        'outcome':'reminder_delivered', 'conversation_intention':'moved',
        'training_barrier':'Moved to Durban', 'notes':'Client is considering how much they can afford.'})
    assert result.status_code == 302
    with app.app_context():
        db = get_db()
        row = db.execute('SELECT notes,outcome FROM collection_call_tasks WHERE id=?', (task['id'],)).fetchone()
        assert 'Has moved away' in row['notes'] and 'Moved to Durban' in row['notes']
        assert row['outcome'] == 'reminder_delivered'
        assert db.execute('SELECT COUNT(*) FROM ptp_agreements').fetchone()[0] == 0
        assert db.execute('SELECT amount_paid FROM collections WHERE member_id=?', (task['member_id'],)).fetchone()[0] == 0


def test_proposed_upfront_quotes_discount_without_receiving_payment_or_return_commitment(app, client):
    from tests.test_collections_decision import _caller_task, _login
    task = _caller_task(app)
    _login(client, task)
    with app.app_context():
        db = get_db()
        mid = db.execute('SELECT member_id FROM collection_call_tasks WHERE id=?', (task['id'],)).fetchone()[0]
        for stamp in ('2026-08-15', '2026-09-15'):
            db.execute("""INSERT INTO collections(member_id,outstanding_balance,amount_paid,collection_date,status,notes)
                VALUES(?,375,0,?,'failed','type=Recurring Fee')""", (mid,stamp))
        db.commit()
    url = f"/collections/call-queue/{task['id']}/offer-preview"
    quote = client.get(url, query_string={'conversation_intention':'moved', 'returning_member':'1',
        'upfront_offered':'500', 'monthly_affordable':'200', 'intention':'instalments', 'arrears':'1'}).get_json()
    assert quote['original_arrears'] == 1000 and quote['discount_percent'] == 50
    assert quote['upfront_offered'] == 500 and quote['upfront_received'] == 0
    assert any(c['key']=='upfront' and not c['met'] for c in quote['checklist'])
    assert not any(c['key']=='returning_debicheck' for c in quote['checklist'])
    returning = client.get(url, query_string={'conversation_intention':'return', 'returning_member':'',
        'upfront_offered':'500', 'intention':'instalments'}).get_json()
    assert any(c['key']=='returning_debicheck' for c in returning['checklist'])
    with app.app_context():
        db = get_db()
        assert db.execute('SELECT COUNT(*) FROM ptp_agreements').fetchone()[0] == 0
        assert db.execute('SELECT SUM(amount_paid) FROM collections WHERE member_id=?', (mid,)).fetchone()[0] == 0
