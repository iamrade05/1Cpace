import pytest

from onecpase.database import get_db
from onecpase.sales_board import build_board
from tests.test_department_dashboards import login


def consultant(db, name='Board consultant', department='Sales'):
    return db.execute("""INSERT INTO users(username,password_hash,full_name,role,department,active)
        VALUES(?,'x',?,'staff',?,1)""", (name, name, department)).lastrowid


def test_board_uses_credit_owner_sast_cycle_and_package(app, monkeypatch):
    with app.app_context():
        db = get_db()
        uid = consultant(db)
        other = consultant(db, 'Reception only', 'Reception')
        db.execute("INSERT INTO sales_board_targets(cycle,user_id,target) VALUES('2026-11',?,25)", (uid,))
        def credits(db, in_window, in_scope, apps):
            return [s for s in [
                {'credited_at':'2026-10-14T22:01:00Z', 'credited_to':uid, 'package':'12 Month'},
                {'credited_at':'2026-11-02T10:00:00Z', 'credited_to':uid, 'package':'Student'},
                {'credited_at':'2026-11-02T22:00:00Z', 'credited_to':uid, 'package':'Future'},
                {'credited_at':'2026-11-01T10:00:00Z', 'credited_to':other, 'package':'Private'},
            ] if in_window(s['credited_at']) and in_scope(s['credited_to'])]
        monkeypatch.setattr('onecpase.sales_kpi.credited_sales', credits)
        board = build_board(db, '2026-11-02')
        row = board['rows'][0]
        assert len(board['rows']) == 1
        assert board['start'] == '2026-10-15' and board['end'] == '2026-11-14'
        assert (board['days'], board['elapsed'], board['remaining']) == (27, 16, 11)
        assert (row['actual'], row['balance'], row['today'], row['percent']) == (2, 23, 1, 8)
        assert row['daily'] == 2.1 and row['projection'] == 3.4
        assert row['packages'] == [('12 Month', 1), ('Student', 1)]
        assert build_board(db, '2026-11-02', other)['rows'] == []


@pytest.mark.parametrize('day', ['2026-11-14', '2026-11-15'])
def test_empty_targets_and_cycle_edges_do_not_divide_by_zero(app, day):
    with app.app_context():
        db = get_db()
        uid = consultant(db)
        row = build_board(db, day)['rows'][0]
        assert row['balance'] is None and row['daily'] is None and row['percent'] is None
        db.execute('INSERT INTO sales_board_targets(cycle,user_id,target) VALUES(?,?,0)',
                   (build_board(db, day)['cycle'], uid))
        board = build_board(db, day)
        assert board['rows'][0]['percent'] is None
        if day == '2026-11-15':
            assert board['elapsed'] == 0 and board['rows'][0]['projection'] is None


def test_manager_saves_cycle_target_and_dated_commitment(client, app):
    login(app, client, 'manager', 'Sales')
    with app.app_context():
        uid = consultant(get_db())
        get_db().commit()
    response = client.post('/sales-dashboard/board?period=week&on=2026-11-02', data={
        'user_id':uid, 'day':'2026-11-02', 'target':'25', 'commitment':'2'})
    assert response.status_code == 302
    assert 'on=2026-11-02' in response.location
    with app.app_context():
        board = build_board(get_db(), '2026-11-02')
        assert (board['rows'][0]['target'], board['rows'][0]['commitment']) == (25, 2)
        assert build_board(get_db(), '2026-11-03')['rows'][0]['commitment'] is None
    page = client.get('/sales-dashboard?kpi_date=2026-11-02')
    assert page.status_code == 200
    assert b'Consultant sales board' in page.data
    assert b'Set consultant targets' in page.data
    assert client.post('/sales-dashboard/board', data={
        'user_id':uid, 'day':'2026-11-02', 'target':'-1', 'commitment':'2'}).status_code == 400
    client.post('/sales-dashboard/board', data={
        'user_id':uid, 'day':'2026-11-02', 'target':'', 'commitment':''})
    with app.app_context():
        row = build_board(get_db(), '2026-11-02')['rows'][0]
        assert row['target'] is None and row['commitment'] is None


def test_staff_and_other_departments_cannot_change_targets(client, app):
    uid = login(app, client, 'staff', 'Sales')
    data = {'user_id':uid, 'day':'2026-11-02', 'target':'25', 'commitment':'2'}
    assert client.post('/sales-dashboard/board', data=data).status_code == 403
    assert b'Set consultant targets' not in client.get('/sales-dashboard').data
    login(app, client, 'manager', 'Sales')
    with app.app_context():
        data['user_id'] = consultant(get_db(), 'Outside', 'Reception')
        get_db().commit()
    assert client.post('/sales-dashboard/board', data=data).status_code == 403


def test_board_counts_first_completion_once_and_excludes_blockers(app, monkeypatch):
    with app.app_context():
        db = get_db()
        uid = consultant(db)
        complete = db.execute("INSERT INTO membership_applications(package,created_by) VALUES('12 Month',?)", (uid,)).lastrowid
        blocked = db.execute("INSERT INTO membership_applications(package,created_by) VALUES('Student',?)", (uid,)).lastrowid
        for aid in (complete, complete, blocked):
            db.execute("""INSERT INTO stage_events(entity_type,entity_id,application_id,field,to_value,owner_id,occurred_at)
                VALUES('compliance',?,?,'overall_status','complete',?,'2026-11-02T10:00:00Z')""", (aid, aid, uid))
        monkeypatch.setattr('onecpase.applications.compliance_completion_blockers',
                            lambda db, aid: ['Unsigned'] if aid == blocked else [])
        row = build_board(db, '2026-11-02')['rows'][0]
        assert row['actual'] == row['today'] == 1
        assert row['packages'] == [('12 Month', 1)]


def test_achievement_bands_use_exact_ratios(app, monkeypatch):
    with app.app_context():
        db = get_db()
        uid = consultant(db)
        db.execute("INSERT INTO sales_board_targets(cycle,user_id,target) VALUES('2026-11',?,1000)", (uid,))
        sales = []
        monkeypatch.setattr('onecpase.sales_kpi.credited_sales', lambda *args: sales)
        for count, band, met in ((999, '', False), (1000, 'Target 3', True),
                                 (1100, 'Target 2', True), (1250, 'Target 1', True)):
            sales[:] = [{'credited_to':uid, 'credited_at':'2026-11-02T10:00:00Z', 'package':'12 Month'}] * count
            row = build_board(db, '2026-11-02')['rows'][0]
            assert (row['band'], row['met']) == (band, met)
