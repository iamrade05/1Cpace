from datetime import date, datetime
import sqlite3
import pytest

from onecpase.reporting_cycle import current_cycle, cycle_key
from onecpase.dashboard import _build_trend_ranges


@pytest.mark.parametrize('day,key,first,last', [
    ('2026-10-14','2026-10','2026-09-15','2026-10-14'),
    ('2026-10-15','2026-11','2026-10-15','2026-11-14'),
    ('2026-12-31','2027-01','2026-12-15','2027-01-14'),
    ('2027-01-14','2027-01','2026-12-15','2027-01-14'),
    ('2028-02-29','2028-03','2028-02-15','2028-03-14'),
])
def test_cycle_boundaries_and_sql_buckets_agree(day,key,first,last):
    parsed = date.fromisoformat(day)
    assert cycle_key(parsed) == key
    assert tuple(d.isoformat() for d in current_cycle(parsed)) == (first,last)
    db = sqlite3.connect(':memory:')
    try:
        assert db.execute("SELECT strftime('%Y-%m',date(?,'-14 days','start of month','+1 month'))",(day,)).fetchone()[0] == key
    finally:
        db.close()


def test_reports_and_department_defaults_share_cycle(app, monkeypatch):
    from onecpase import reports, department_metrics
    monkeypatch.setattr(reports,'business_today',lambda:date(2026,10,27))
    assert reports._date_range('this_month') == ('2026-10-15','2026-11-14')
    assert reports._date_range('last_month') == ('2026-09-15','2026-10-14')
    assert reports._date_range('this_year') == ('2025-12-15','2026-12-14')
    with app.test_request_context('/?from=2026-10-16&to=2026-10-20'):
        assert department_metrics.period()[:2] == ('2026-10-16','2026-10-20')


def test_dashboard_cycle_includes_last_day_and_excludes_next_cycle():
    rows = [{'event_date':day,'amount':amount} for day,amount in [
        ('2026-10-14',1),('2026-10-15',10),('2026-11-14 23:59:59',100),('2026-11-15',1000)]]
    result = _build_trend_ranges(rows,[],datetime(2026,10,27))
    assert result['30d']['date_from'] == '2026-10-15'
    assert result['30d']['date_to'] == '2026-11-14'
    assert result['30d']['revenue']['total'] == 110
    assert result['30d']['revenue']['previous_total'] == 1
    assert result['6m']['revenue']['values'][-1] == 110


def test_postgres_cycle_sql_translation():
    from onecpase.db_adapter import _translate
    query = _translate("SELECT strftime('%Y-%m',date(c.created_at,'-14 days','start of month','+1 month')) FROM collections c")
    assert "DATE_TRUNC('month', (c.created_at)::date - INTERVAL '14 days')" in query
    assert 'strftime' not in query and 'start of month' not in query


def test_collections_report_uses_cycle_instead_of_calendar_month(app):
    from onecpase.database import get_db
    from onecpase.collections_workspace import financial_report
    with app.app_context():
        db = get_db()
        mid = db.execute("INSERT INTO members(first_name,last_name,id_number) VALUES('Cycle','Boundary','cycle-boundary')").lastrowid
        for day, amount in [('2026-10-14',1),('2026-10-15',10),('2026-11-14',100),('2026-11-15',1000)]:
            db.execute("INSERT INTO collections(member_id,collection_date,amount_paid,status) VALUES(?,?,?,'paid')",(mid,day,amount))
        result = financial_report(db,'2026-11')
        assert result['collected'] == 110
        assert result['cycle_start'] == '2026-10-15'
        assert result['cycle_end'] == '2026-11-14'


def test_report_tabs_render_with_cycle_defaults(app):
    from onecpase.database import get_db
    with app.app_context():
        db = get_db()
        uid = db.execute("INSERT INTO users(username,full_name,password_hash,role,department,active) VALUES('cycle-report-admin','Cycle Admin','x','admin','Management',1)").lastrowid
        db.commit()
    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(user_id=uid,role='admin',permissions=['reports_module'])
        for tab in ('overview','membership','finance','arrears','queries'):
            response = client.get('/reports/?tab='+tab)
            assert response.status_code == 200, tab
            assert b'15th to 14th' in response.data
