from datetime import datetime

import pytest
from flask import g

from onecpase import itensity_attendance as attendance
from onecpase.database import get_db
from onecpase.members import _get_last_gym_visit


def report(*dates):
    return '<table><tr><th>Dtae &amp; Time Accessed</th></tr>' + ''.join(
        '<tr><td>' + stamp + '</td></tr>' for stamp in dates) + '</table>'


def test_access_log_uses_latest_entry_not_first_and_ignores_bad_future_dates():
    stamp = attendance.parse_last_visit(report('2026-01-10 11:27:22',
        '2026-03-15 15:42:19', '2026-02-01 09:00:00',
        '2026-99-01 09:00:00', '2027-01-01 09:00:00'), now=datetime(2026,11,6))
    assert stamp.isoformat() == '2026-03-15T15:42:19+02:00'


@pytest.mark.parametrize('html', ['<form>Sign in</form>', '<table><th>Error</th></table>',
                                  '<table><th>Last payment</th><td>2026-10-01 09:00:00</td></table>'])
def test_unexpected_portal_pages_are_not_treated_as_attendance(html):
    with pytest.raises(ValueError):
        attendance.parse_last_visit(html)


def test_empty_confirmed_access_log_remains_unknown():
    assert attendance.parse_last_visit(report()) is None


@pytest.fixture(autouse=True)
def clear_cache():
    attendance._CACHE.clear()
    yield
    attendance._CACHE.clear()


def test_bound_gym_uses_itensity_reference_even_when_local_turnstile_exists(app, monkeypatch):
    calls = []
    stamp = datetime(2026,10,31,14,35,tzinfo=attendance.ZONE)
    monkeypatch.setattr(attendance, 'fetch_last_visit', lambda ref: calls.append(ref) or stamp)
    app.config['ITENSITY_TENANT_SLUG'] = 'elev8'
    with app.test_request_context('/'):
        g.tenant_slug = 'elev8'
        db = get_db()
        mid = db.execute("INSERT INTO members(first_name,last_name,id_number,itensity_ref) VALUES('Test','Client','VISIT-1','2069944')").lastrowid
        db.execute("INSERT INTO turnstile_events(raw_report_hex,member_id,decision,direction,created_at) VALUES('00',?,'allowed','entry','2026-11-01 08:00:00')", (mid,))
        member = {'id':mid, 'itensity_ref':'2069944', 'contact':'0697864015'}
        visit = _get_last_gym_visit(db, member)
        assert visit['last_visited'] == stamp
        assert visit['source'] == 'Itensity member access log'
        assert visit['checked_at']
        assert _get_last_gym_visit(db, member) == visit
        assert calls == ['2069944']
        # A different account sharing a phone number must not reuse the visit.
        _get_last_gym_visit(db, dict(member, itensity_ref='99999'))
        assert calls == ['2069944', '99999']


def test_other_gym_cannot_read_itensity_even_with_cached_matching_reference(app, monkeypatch):
    def forbidden(ref):
        raise AssertionError('Cross-tenant attendance lookup')
    monkeypatch.setattr(attendance, 'fetch_last_visit', forbidden)
    app.config['ITENSITY_TENANT_SLUG'] = 'elev8'
    with app.test_request_context('/'):
        g.tenant_slug = 'othergym'
        assert attendance.member_last_visit({'itensity_ref':'2069944'}) is None


@pytest.mark.parametrize('ref', [None, '', 'bad', '1&name=2'])
def test_missing_or_invalid_reference_never_queries_itensity(app, monkeypatch, ref):
    def forbidden(ref):
        raise AssertionError('Invalid reference was used')
    monkeypatch.setattr(attendance, 'fetch_last_visit', forbidden)
    app.config['ITENSITY_TENANT_SLUG'] = 'elev8'
    with app.test_request_context('/'):
        g.tenant_slug = 'elev8'
        assert attendance.member_last_visit({'itensity_ref':ref}) is None


def test_unavailable_itensity_is_unknown_and_does_not_fall_back_to_sql(app, monkeypatch):
    calls = []
    def unavailable(ref):
        calls.append(ref)
        raise RuntimeError('Unavailable')
    monkeypatch.setattr(attendance, 'fetch_last_visit', unavailable)
    app.config['ITENSITY_TENANT_SLUG'] = 'elev8'
    with app.test_request_context('/'):
        g.tenant_slug = 'elev8'
        member = {'id':999, 'itensity_ref':'2069944'}
        assert _get_last_gym_visit(get_db(), member) is None
        assert _get_last_gym_visit(get_db(), member) is None
        assert calls == ['2069944']
