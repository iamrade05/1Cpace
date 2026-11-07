"""Verify call-guide reading and layout using disposable member data."""
import json
import os
from pathlib import Path
import tempfile
import threading
from datetime import date

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['WHATSAPP_WEB_ENABLED'] = '0'

from flask import redirect, session
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from onecpase import create_app
from onecpase.database import get_db


def main():
    out = Path('artifacts/collection-call-script-review')
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='collection-call-script-') as tmp:
        app = create_app({
            'TESTING':True, 'DATABASE_URL':'', 'DATABASE_PATH':f'{tmp}/test.db',
            'PLATFORM_DATABASE_PATH':f'{tmp}/platform.db', 'TENANTS_DIR':f'{tmp}/tenants',
            'UPLOAD_FOLDER':f'{tmp}/uploads', 'SECRET_KEY':'preview-only',
            'ENCRYPTION_KEY':'kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8=',
            'WTF_CSRF_ENABLED':False, 'TURNSTILE_ENABLED':False, 'ACTIVE_PHASE':'all',
        })
        from onecpase.collections import _ensure_daily_call_tasks
        from tests.test_collections_call_queue import _seed_callers_and_members
        with app.app_context():
            _seed_callers_and_members(2)
            db = get_db()
            _ensure_daily_call_tasks(db, date(2026,8,12))
            task = dict(db.execute('SELECT * FROM collection_call_tasks ORDER BY id LIMIT 1').fetchone())
            for stamp in ('2026-08-15', '2026-09-15'):
                db.execute("""INSERT INTO collections(member_id,outstanding_balance,amount_paid,collection_date,status,notes)
                    VALUES(?,375,0,?,'failed','type=Recurring Fee')""", (task['member_id'], stamp))
            db.execute("UPDATE members SET first_name='Lerato',last_name='Dube',join_date='2025-06-15',member_status='Blocked' WHERE id=?", (task['member_id'],))
            db.execute("UPDATE users SET full_name='Thandi Mokoena' WHERE id=?", (task['assigned_to'],))
            db.execute("INSERT INTO turnstile_events(raw_report_hex,member_id,decision,direction,created_at) VALUES('00',?,'allowed','entry','2026-09-01 08:30:00')", (task['member_id'],))
            pid = db.execute("""INSERT INTO ptp_agreements(member_id,promise_date,payment_method,arrears_amount,
                discount_pct,discount_amount,remaining_balance,payment_count,first_collection_date,recovery_installment,
                upfront_amount,upfront_verified,grant_status,kpi_contact_channel,manager_approval_status)
                VALUES(?,'2027-01-31','debicheck',1000,50,250,250,2,'2027-01-31',125,500,1,'awaiting_debicheck','call','not_required')""", (task['member_id'],)).lastrowid
            did = db.execute("""INSERT INTO debicheck_mandates(member_id,merchant_id,client_ref1,client_ref2,
                account_name,account_type,account_number,branch_code,submit_date,status)
                VALUES(?,'test','test','test','Test Member','1','0000000000','000000','2027-01-31','submitted')""", (task['member_id'],)).lastrowid
            db.execute("INSERT INTO ptp_mandate_links(ptp_id,purpose,amount,mandate_id) VALUES(?,'Arrangement (PTP)',125,?)", (pid,did))
            db.commit()
        @app.get('/preview-login')
        def preview_login():
            session.update(user_id=task['assigned_to'], role='reception', department='Reception',
                           permissions=['collections_call_queue'], tenant_slug=None)
            return redirect(f"/collections/call-queue/{task['id']}/profile")
        server = make_server('127.0.0.1',0,app,threaded=True)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        results, errors = [], []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.set_default_navigation_timeout(90000)
                page.route('https://fonts.googleapis.com/**', lambda route: route.abort())
                page.route('https://fonts.gstatic.com/**', lambda route: route.abort())
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_port}'
                page.goto(base+'/preview-login', wait_until='domcontentloaded')
                for width in (1440,768,390):
                    page.set_viewport_size({'width':width,'height':1000})
                    response = page.goto(base+f"/collections/call-queue/{task['id']}/profile", wait_until='domcontentloaded')
                    assert response.status == 200
                    assert 'approval outstanding' in page.get_by_role('region',name=f'Arrangement confirmation · #{pid}').inner_text().lower()
                    guide = page.get_by_role('region', name='Your conversation guide')
                    assert guide.locator('.call-script-step').count() == 8
                    assert '15 Jun 2025' in guide.locator('.call-script-facts').inner_text()
                    assert 'No confirmed payment date' in guide.locator('.call-script-facts').inner_text()
                    guide.locator('.call-script-step').nth(1).locator('summary').click()
                    assert 'membership as blocked' in guide.inner_text()
                    close = guide.locator('.call-script-step').last
                    close.locator('summary').click()
                    assert 'exact payment date' in close.inner_text()
                    guide.locator('.call-script-step').first.locator('summary').click()
                    assert 'last two months' in guide.locator('.call-script-step').nth(5).text_content()
                    assert guide.locator('.call-script-step[open]').count() == 1
                    assert 'Thandi Mokoena from Elevate Health and Fitness' in guide.locator('.call-script-say').first.inner_text()
                    for i in range(4):
                        step = guide.locator('.call-script-step').nth(i)
                        if i: step.locator('summary').click()
                        assert step.locator('.call-script-say').first.is_visible()
                        assert guide.locator('.call-script-step[open]').count() == 1
                        if i == 2:
                            assert 'overdue balance of R1,000.00' in step.inner_text()
                            guide.screenshot(path=str(out/f'account-step-{width}.png'))
                    choice = guide.locator('#conversation-intention')
                    choice.select_option('return')
                    assert guide.locator('#return-access-guidance').is_visible()
                    assert 'R500.00 (50%' in guide.locator('#return-access-guidance').inner_text()
                    assert 'separate check' in guide.locator('#return-access-guidance').inner_text()
                    assert page.locator('#client-goal').is_visible()
                    page.locator('#client-goal').fill('Improve fitness')
                    assert page.locator('#offer-returning').is_checked()
                    choice.select_option('moved')
                    assert not guide.locator('#return-access-guidance').is_visible()
                    assert guide.locator('#settlement-membership-guidance').is_visible()
                    assert 'does not cancel the membership' in guide.locator('#settlement-membership-guidance').inner_text()
                    assert not page.locator('#client-goal').is_visible()
                    assert 'settle the current arrears' in guide.locator('[data-conversation-path=moved]').inner_text()
                    assert not page.locator('#offer-returning').is_checked()
                    page.locator('#training-barrier').fill('Moved to Durban')
                    fields = page.locator('#collection-call-result-form').evaluate('(form) => Object.fromEntries(new FormData(form))')
                    assert fields['conversation_intention'] == 'moved'
                    assert fields['training_barrier'] == 'Moved to Durban'
                    guide.screenshot(path=str(out/f'settlement-path-{width}.png'))
                    guide.locator('.call-script-step').nth(4).locator('summary').click()
                    page.get_by_role('button',name='Explore payment options').click()
                    assert page.locator('#offer-panel').is_visible()
                    assert page.locator('#outcome-select').input_value() == ''
                    with page.expect_response(lambda r: 'offer-preview?' in r.url and 'upfront_offered=500' in r.url) as response_info:
                        page.locator('#offer-upfront').fill('500')
                    quote = response_info.value.json()
                    assert quote['discount_percent'] == 50
                    assert quote['upfront_received'] == 0
                    assert any(c['key']=='upfront' and not c['met'] for c in quote['checklist'])
                    assert not any(c['key']=='returning_debicheck' for c in quote['checklist'])
                    with page.expect_response(lambda r: 'offer-preview?' in r.url and 'monthly_affordable=200' in r.url) as response_info:
                        page.locator('#offer-affordable').fill('200')
                    quote = response_info.value.json()
                    assert quote['proposed_schedule']
                    page.wait_for_function("!document.getElementById('offer-readback').hidden")
                    readback = page.locator('#offer-readback').inner_text()
                    assert 'Read back before saving' in readback
                    assert 'Do you agree' in readback and 'remain conditional' in readback
                    assert quote['proposed_schedule'][0]['date'] in readback
                    with page.expect_response(lambda r: 'offer-preview?' in r.url and 'first_collection_date=2027-01-31' in r.url) as response_info:
                        page.locator('#offer-first-date').fill('2027-01-31')
                        page.locator('#offer-first-date').dispatch_event('change')
                    quote = response_info.value.json()
                    assert quote['first_collection_date'] == '2027-01-31'
                    assert quote['payment_method'] == 'debicheck'
                    assert quote['proposed_schedule'][0]['date'] == '2027-01-31'
                    page.wait_for_function("document.getElementById('offer-readback').textContent.includes('2027-01-31')")
                    assert page.locator('#offer-payment-method').input_value() == 'debicheck'
                    assert page.locator('#offer-payment-method option').count() == 1
                    assert page.locator('#outcome-select').input_value() == ''
                    page.locator('#offer-panel').screenshot(path=str(out/f'negotiation-{width}.png'))
                    guide.get_by_role('link',name='Go to call notes').click()
                    assert page.locator('#notes-input').is_visible()
                    page.evaluate('scrollTo(0,0)')
                    guide.locator('summary').first.click()
                    page.screenshot(path=str(out/f'profile-{width}.png'),full_page=True)
                    metrics = page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
                    assert metrics['scroll'] <= width, metrics
                    results.append(metrics)
                with app.app_context():
                    db = get_db()
                    db.execute("INSERT INTO member_documents(member_id,document_type,original_name,stored_name,analysis_json) VALUES(?,'Bank Statement','s.pdf','s.pdf',?)", (task['member_id'],json.dumps({'cashflow':{'months':2,'enough_months':True}})))
                    db.execute("UPDATE debicheck_mandates SET status='approved' WHERE id=?", (did,))
                    from onecpase.ptp_grant import evaluate_grant
                    assert evaluate_grant(db,pid) == 'granted'
                    db.commit()
                for width in (1440,768,390):
                    page.set_viewport_size({'width':width,'height':1000})
                    page.goto(base+f"/collections/call-queue/{task['id']}/profile", wait_until='domcontentloaded')
                    closing = page.get_by_role('region',name=f'Arrangement confirmation · #{pid}')
                    assert 'Your arrangement has been approved' in closing.inner_text()
                    assert '28 Feb 2027' in closing.inner_text() and 'R750.00' in closing.inner_text()
                    closing.screenshot(path=str(out/f'approved-confirmation-{width}.png'))
                    metrics = page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
                    assert metrics['scroll'] <= width, metrics
                browser.close()
            assert not errors, errors
            (out/'results.json').write_text(json.dumps({'viewports':results,'errors':errors},indent=2))
            print(json.dumps({'viewports':results,'errors':errors}))
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
