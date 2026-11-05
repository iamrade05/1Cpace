"""Disposable browser verification for the consultant board."""
import json
import os
from pathlib import Path
import tempfile
import threading

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['WHATSAPP_WEB_ENABLED'] = '0'

from flask import redirect, session
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from onecpase import create_app
from onecpase.database import get_db


def main():
    out = Path('artifacts/sales-board-review')
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sales-board-preview-') as tmp:
        app = create_app({
            'TESTING': True, 'DATABASE_URL': '', 'DATABASE_PATH': f'{tmp}/test.db',
            'PLATFORM_DATABASE_PATH': f'{tmp}/platform.db', 'TENANTS_DIR': f'{tmp}/tenants',
            'UPLOAD_FOLDER': f'{tmp}/uploads', 'SECRET_KEY': 'preview-only',
            'ENCRYPTION_KEY': 'kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8=',
            'WTF_CSRF_ENABLED': False, 'TURNSTILE_ENABLED': False, 'ACTIVE_PHASE': 'all',
        })
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES(1,'review-manager','x','Review Manager','manager','Sales',1)")
            for uid, name, target in ((2, 'Example Consultant A', 25), (3, 'Example Consultant B', 41), (4, 'Example Consultant C', 25)):
                db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES(?,?,'x',?,'staff','Sales',1)", (uid, name, name))
                db.execute("INSERT INTO sales_board_targets(cycle,user_id,target) VALUES('2026-11',?,?)", (uid, target))
                db.execute("INSERT INTO sales_board_commitments(day,user_id,units) VALUES('2026-11-02',?,2)", (uid,))
                lead = db.execute("INSERT INTO leads(full_name,assigned_to) VALUES('Preview prospect',?)", (uid,)).lastrowid
                for outcome in ('follow_up_required', 'not_interested', 'no_response'):
                    db.execute("""INSERT INTO lead_activities(lead_id,activity_type,outcome,created_by,occurred_at)
                        VALUES(?,'call',?,?,'2026-11-02T08:00:00Z')""", (lead, outcome, uid))
                db.execute("""INSERT INTO lead_activities(lead_id,activity_type,outcome,created_by,occurred_at)
                    VALUES(?,'visit','arrived',?,'2026-11-02T10:00:00Z')""", (lead, uid))
            db.commit()
        @app.get('/preview-login')
        def preview_login():
            session.update(user_id=1, role='manager', department='Sales', permissions=['dashboard'], tenant_slug=None)
            return redirect('/sales-dashboard?kpi_date=2026-11-02')
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors, results = [], []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_port}'
                page.goto(base + '/preview-login', wait_until='networkidle')
                for width in (1440, 768, 390):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    response = page.goto(base + '/sales-dashboard?kpi_date=2026-11-02', wait_until='networkidle')
                    assert response.status == 200
                    assert page.locator('.board-table tbody tr').count() == 3
                    assert page.locator('.sales-analysis[open]').count() == 0
                    daily = page.get_by_role('region', name='Daily staff targets')
                    headers = daily.locator('thead th').all_text_contents()
                    assert len(headers) == 6
                    assert all(label in headers[i+1] for i, label in enumerate(
                        ('Calls made', 'Connected calls', 'Referrals', 'Appointments', 'Shows')))
                    assert 'seconds' not in daily.inner_text()
                    assert daily.locator('tbody tr').first.locator('td').first.inner_text() == '3'
                    assert daily.locator('tbody tr').first.locator('td').nth(1).inner_text().startswith('2 / 30')
                    page.screenshot(path=str(out / f'board-{width}.png'), full_page=True)
                    metrics = page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
                    assert metrics['scroll'] <= width, metrics
                    page.get_by_text('Set consultant targets & daily commitments', exact=True).click()
                    form = page.locator('.board-target-form').first
                    form.locator('[name=target]').fill('30')
                    form.locator('[name=commitment]').fill('3')
                    form.get_by_role('button').click()
                    page.wait_for_load_state('networkidle')
                    assert page.locator('.board-table tbody tr').first.locator('td').first.inner_text() == '30'
                    page.locator('.board-day-form [name=kpi_date]').fill('2026-11-03')
                    page.get_by_role('button', name='View board', exact=True).click()
                    page.wait_for_load_state('networkidle')
                    assert 'kpi_date=2026-11-03' in page.url
                    results.append(metrics)
                browser.close()
            assert not errors, errors
            (out / 'results.json').write_text(json.dumps({'viewports': results, 'errors': errors}, indent=2))
            print(json.dumps({'viewports': results, 'errors': errors}))
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
