"""Render the UI against disposable data, never the local gym databases.

Run with `python -m scripts.verify_ui`. Screenshots and results are written
to artifacts/ui-review; Chromium must be installed for Playwright.
"""
from pathlib import Path
import tempfile, threading, json, os
# Set before importing Config so previews never load local provider credentials.
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from onecpase import create_app
from onecpase.database import get_db
from onecpase.permissions import default_permissions_for
from flask import render_template

out=Path(__file__).resolve().parents[1]/'artifacts/ui-review'
out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='onecpace-ui-') as tmp:
    app=create_app({'TESTING':True,'ENVIRONMENT':'testing','DATABASE_URL':'','DATABASE_PATH':str(Path(tmp)/'test.db'),'PLATFORM_DATABASE_PATH':str(Path(tmp)/'platform.db'),'TENANTS_DIR':str(Path(tmp)/'tenants'),'UPLOAD_FOLDER':str(Path(tmp)/'uploads'),'SECRET_KEY':'ui-preview-only','ENCRYPTION_KEY':'kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8=','WTF_CSRF_ENABLED':False,'TURNSTILE_ENABLED':False,'ACTIVE_PHASE':'all'})
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users (id,username,password_hash,full_name,role,department,active) VALUES (1,'preview','x','UI Reviewer','admin','Management',1)")
        db.execute("INSERT INTO users (id,username,password_hash,full_name,role,department,active) VALUES (2,'supervisor','x','Example Supervisor','manager','Management',1)")
        db.execute("UPDATE users SET supervisor_id=2 WHERE id=1")
        db.execute("INSERT INTO queries(category,description,status,assigned_to,follow_up_date) VALUES ('payments','Example escalation','escalated',1,'2026-10-08')")
        from onecpase.notifications import sync_notifications
        sync_notifications(db)
        mid=db.execute("INSERT INTO members (first_name,last_name,id_number,contact,member_ref,member_status,monthly_installment,tariff,itensity_ref) VALUES ('Example','Member','9001015009087','0712345678','ELE-0000000101','Active',345.58,'Premium 12','48213')").lastrowid
        db.execute("INSERT INTO events (name,slug,active) VALUES ('Example Community Event','ui-review-event',1)")
        from onecpase.nupay_transactions import plan_import, apply_plan
        rows=[{'Mandate ID':'71078982','Debtor ID':'','Instalment Amount':'345.58','Action Date':f'2026-{month:02d}-20','Status':'Success' if month<9 else 'Failed','Client Reference':'48213','Instalment':str(month-5),'Total Instalments':'12','Cycle Date':f'2026-{month:02d}-20','Contract Reference':'UI-EXAMPLE'} for month in range(6,10)]
        apply_plan(db,plan_import(db,rows))
        from onecpase.nupay_reconciliation import read_upload, store_upload, rebuild_snapshot
        header='Mandate ID,Debtor ID,Instalment Amount,Action Date,Status,Client Reference,Instalment,Total Instalments,Cycle Date,Contract Reference\n'
        csv=header+'\n'.join(','.join(str(row[key]) for key in ['Mandate ID','Debtor ID','Instalment Amount','Action Date','Status','Client Reference','Instalment','Total Instalments','Cycle Date','Contract Reference']) for row in rows)
        parsed=read_upload([('ui-preview.csv',csv.encode())])
        store_upload(db,parsed,['ui-preview.csv'],None)
        rebuild_snapshot(db,None)
        db.commit()
        for template in app.jinja_env.list_templates():app.jinja_env.get_template(template)
    @app.route('/ui-preview/login')
    def preview_login(): return render_template('auth/login.html',tenant=None)
    @app.route('/ui-preview/otp')
    def preview_otp(): return render_template('auth/otp.html',remaining=120,masked_email='e***@example.com')
    client=app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,role='admin',full_name='UI Reviewer',permissions=list(default_permissions_for('admin','Management')))
    cookie=client.get_cookie('session')
    server=make_server('127.0.0.1',0,app)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}'
    results=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            context=browser.new_context()
            context.add_cookies([{'name':'session','value':cookie.value,'url':url}])
            page=context.new_page()
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            for width in [1366,390]:
                page.set_viewport_size({'width':width,'height':850})
                for name,route in [('profile',f'/members/{mid}'),('members','/members'),('member-add','/members/add'),('member-edit',f'/members/{mid}/edit'),('dashboard','/dashboard'),('collections-dashboard','/collections/dashboard'),('sales-dashboard','/sales-dashboard'),('leads','/leads'),('applications','/applications'),('queries','/queries'),('debicheck','/debicheck'),('fitness','/fitness/'),('hr','/hr/'),('operations','/operations/'),('reports','/reports/'),('admin','/admin/'),('notifications','/notifications'),('staff-supervisor','/admin/users/1/edit'),('welcome','/'),('login','/ui-preview/login'),('otp','/ui-preview/otp'),('event','/events/ui-review-event')]:
                    response=page.goto(url+route,wait_until='networkidle')
                    page.screenshot(path=str(out/f'{name}-{width}.png'),full_page=True)
                    metrics=page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth,background:getComputedStyle(document.body).backgroundColor})')
                    results.append({'page':name,'width':width,'status':response.status,**metrics})
                    if name=='event':
                        page.evaluate('goToStep(2)')
                        assert page.locator('.step-panel:visible').count()==1
                        assert page.locator('#step2').is_visible()
            # Viewing a notification does not acknowledge it; an explicit action does.
            page.goto(url+'/notifications',wait_until='networkidle')
            assert page.get_by_role('button',name='Acknowledge',exact=True).count() == 1
            page.get_by_role('button',name='Acknowledge',exact=True).click()
            page.wait_for_load_state('networkidle')
            assert page.get_by_text('Acknowledged — work remains open').is_visible()
            # Confirmations must not submit before approval, and must retain the
            # chosen action when a form has several submit buttons.
            page.goto(url+'/members',wait_until='networkidle')
            page.evaluate('''() => {
              const form=document.createElement('form');
              form.method='post'; form.action='/ui-confirmation-test';
              form.innerHTML='<button type="submit" name="action" value="apply" data-confirm="Apply the preview?">Preview action</button>';
              document.querySelector('.content').append(form);
            }''')
            sent=[]
            context.route('**/ui-confirmation-test',lambda route:(sent.append(route.request.post_data),route.fulfill(status=204)))
            page.get_by_role('button',name='Preview action').click()
            assert not sent
            page.locator('.inline-confirmation').get_by_role('button',name='Cancel').click()
            assert not sent
            page.get_by_role('button',name='Preview action').click()
            with page.expect_request('**/ui-confirmation-test'):
                page.locator('.inline-confirmation').get_by_role('button',name='Confirm',exact=True).click()
            assert len(sent)==1 and 'action=apply' in sent[0]
            browser.close()
        (out/'results.json').write_text(json.dumps({'pages':results,'javascript_errors':errors},indent=2),encoding='utf-8')
        print(json.dumps({'pages':results,'javascript_errors':errors},indent=2))
        assert all(row['status'] == 200 for row in results), 'A preview page failed to render'
        assert all(row['scroll'] <= row['width'] for row in results), 'A preview page overflows the viewport'
        assert not errors, 'Preview pages have JavaScript errors'
    finally:server.shutdown()
