"""Audit all web templates and preview reachable HTML routes using disposable data.

Run: python -m scripts.audit_page_consistency
This is a review tool: it does not change application templates or live data.
"""
import ast
import inspect
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import argparse
from unittest.mock import patch

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['APP_ENV'] = 'testing'
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['WHATSAPP_WEB_ENABLED'] = '0'
os.environ['RATELIMIT_STORAGE_URL'] = 'memory://'
os.environ['ENCRYPTION_KEY'] = 'kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8='

from flask import render_template, template_rendered
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server, WSGIRequestHandler
from onecpase import create_app
from onecpase.database import get_db
from onecpase.permissions import default_permissions_for
from scripts.audit_ui import audit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/page-consistency-20261028'
SUPPLEMENT = False
ONLY_PATHS = None
ALL_SCREENSHOTS = False
DASHBOARD_PERIODS = False
VIEW_WIDTHS = (1366,768,390)


def source_inventory():
    rows = []
    debt = audit()
    for name, metrics in debt.items():
        source = (ROOT / 'onecpase/templates' / name).read_text(encoding='utf-8')
        parent = re.search(r'{%\s*extends\s+[\'"]([^\'"]+)', source)
        partial = Path(name).name.startswith('_')
        rows.append(dict(template=name, parent=parent.group(1) if parent else None,
                         kind='partial' if partial else 'layout' if name in ('base.html', 'public.html') else 'page',
                         standard_header=bool(re.search(r'page-header|page_header|dashboard_header|dashboard_topline', source)),
                         has_h1='<h1' in source, **metrics))
    return rows


def rendered_names(fn):
    try:
        tree = ast.parse(inspect.getsource(inspect.unwrap(fn)))
    except (OSError, TypeError, IndentationError):
        return []
    return sorted({n.args[0].value for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == 'render_template' and n.args
                   and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)})


def write_report():
    folder = ROOT / 'artifacts/page-consistency-20261028'
    main = json.loads((folder/'results.json').read_text(encoding='utf-8'))
    extra_path = folder/'supplement/results.json'
    extra = json.loads(extra_path.read_text(encoding='utf-8')) if extra_path.exists() else dict(views=[],routes=[])
    views = {(r['path'],r['width']):dict(r,evidence_prefix='') for r in main['views']}
    views.update({(r['path'],r['width']):dict(r,evidence_prefix='supplement/') for r in extra['views']})
    views = list(views.values())
    # The checklist API renders an HTML fragment inside JSON, not a standalone page.
    views = [r for r in views if not r['path'].startswith('/debicheck/requirements/')]
    inventory = source_inventory()
    by_template = {}
    for view in views:
        for template in view.get('templates',[]):
            by_template.setdefault(template,[]).append(view)
    missing_headers = sorted({r['path'] for r in views if 'missing standard header' in r['flags']})
    overflow = [r for r in views if 'document overflow' in r['flags'] or 'content clipped or overflowing' in r['flags']]
    blank = [r for r in views if 'blank content' in r['flags']]
    lines = ['# App styling and alignment audit — 28 October 2026','',
        '**Result: the app is not fully standardized.** The shared staff layout loads consistently, but some pages omit its header treatment, and the signed member request form uses independent styling.','',
        f"Inspected all **{len(inventory)} HTML templates** in the local web app. Browser checks cover **{len({r['path'] for r in views})} page URLs / {len(views)} views** at 1366, 768 and 390 pixels, with disposable synthetic data. This is a local-checkout audit, not a certification of the deployed app or the separate React Native mobile app.",'',
        '## Confirmed findings','',
        '1. **Independent member request form:** `queries/member_form.html` does not inherit either shared layout or load shared tokens/components. Refund, freeze, complaint, cancellation, banking and proof-of-payment variants use system fonts, grey native buttons and independent padding. This is a public tenant-branded form, so the staff sidebar is not expected; matching shared public controls, spacing and typography still needs a design decision. The initial correction field is also visible because `label { display:block }` overrides its `hidden` attribute.','',
        '2. **Missing standard staff headers:** the following rendered pages omit the navy/gold header used on other staff pages. They retain the common navigation/CSS but have a noticeably plainer content area.','']
    lines += [f'- `{path}`' for path in missing_headers]
    lines += ['', '3. **Alignment and clipping:**', '']
    if overflow:
        for r in overflow:
            lines.append(f"- `{r['path']}` at {r['width']}px: {', '.join(f for f in r['flags'] if 'overflow' in f or 'clipped' in f)}. Inspect its linked screenshot below.")
    else:
        lines.append('- No document overflow or content outside the viewport was detected in the rendered fixtures. This does not prove every populated table or conditional workflow state fits.')
    lines += ['', f"4. **Blank pages:** {len(blank)} rendered views had less than 25 characters of visible content. No blank page should be inferred from an intentional empty data state.",'',
        '5. **Additional browser defect:** Record Payment (`/collections/add`) raises `form.method.toLowerCase is not a function`. Its field named `method` shadows the form method property used by the shared scripts. The page is styled; this is a separate interaction issue. No payment was submitted.','',
        '6. **Remaining styling maintenance:** '+str(sum(r['inline_styles'] for r in inventory))+' inline style occurrences and '+str(sum(r['style_blocks'] for r in inventory))+' page-local style blocks remain. These counts are maintenance indicators, not proof that each affected page looks wrong. Printable/signable contracts have an intentional independent document layout.','',
        '## Evidence for flagged pages','', '| Page | Width | Finding | Screenshot |','|---|---:|---|---|']
    for r in views:
        if not r['flags']: continue
        screenshot = r.get('screenshot')
        link = f"[View]({r['evidence_prefix']}{screenshot})" if screenshot else 'Not captured'
        lines.append(f"| `{r['path']}` | {r['width']} | {', '.join(r['flags'])} | {link} |")
    lines += ['', '## Every-template checklist','',
        'Browser evidence applies to the fixture/state tested. “Source only” is deliberately not a visual pass. Shared headers can be generated through macros; partials and layouts are not separate pages.','',
        '| Template | Type / parent layout | Browser coverage | Header source | Inline styles / local blocks |','|---|---|---|---|---:|']
    for row in inventory:
        checks = by_template.get(row['template'],[])
        flags = sorted({flag for r in checks for flag in r['flags']})
        coverage = ('Flags: '+', '.join(flags) if flags else 'No measured issues in fixture') if checks else 'Source only'
        lines.append(f"| `{row['template']}` | {row['kind']}; {row['parent'] or 'independent'} | {coverage} | {'shared header marker' if row['standard_header'] else 'none; inspect context'} | {row['inline_styles']} / {row['style_blocks']} |")
    gaps = {r.get('path',r.get('rule')):r for r in main['routes'] if r['status']!=200}
    successful_paths = {r['path'] for r in views}
    lines += ['', '## Unverified routes and states','',
        'Some routes require secure tokens, selected records or a separate platform-admin login. Authentication forms were rendered through disposable preview endpoints; the signed member request variants were rendered directly with synthetic context. Redirects and missing fixtures are coverage gaps, not application failures.','',
        '| Route | Preview result |','|---|---|']
    for path,r in sorted(gaps.items()):
        if path in successful_paths: continue
        lines.append(f"| `{path}` | {r['status']}; {r.get('location') or r.get('error') or 'requires representative fixture'} |")
    lines += ['', '## Scope and limitations','',
        '- App templates and CSS were not changed or deployed. This deliverable contains an audit runner, results and screenshots.',
        '- Administrator permissions were used. Department-specific access, other roles, long real data, every table row and every conditional action state are not certified.',
        '- Office SQL access, scheduler and WhatsApp workers were disabled. Access Report was inspected in its source-unavailable state.',
        '- External fonts were blocked, so browser screenshots use fallback fonts. Supplemental dashboard checks used the app’s pinned Chart.js 4.4.0 dependency; those supersede the initial blocked-CDN results.',
        '- Measured overflow excludes properly scrollable containers. Screenshots were manually inspected for selected plain-header, form and responsive cases.',
        '- Raw browser results: [main](results.json), [supplement](supplement/results.json). Source checklist includes unused/legacy templates to avoid silently omitting them.','']
    (folder/'AUDIT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(templates=len(inventory),urls=len({r['path'] for r in views}),views=len(views),
                         missing_headers=missing_headers,overflow=[(r['path'],r['width']) for r in overflow],
                         blank_views=len(blank)),indent=2))


METRICS = r'''() => {
 const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
 const content = document.querySelector('.content');
 const heading = content?.querySelector('h1');
 const header = content?.querySelector('.page-header, .fd-topline');
 const rect = e => e ? {x:Math.round(e.getBoundingClientRect().x),width:Math.round(e.getBoundingClientRect().width)} : null;
 const overflowing = content ? [...content.querySelectorAll('*')].filter(e => {
   if (!visible(e) || e.closest('.table-wrap,.table-container,.training-table,svg')) return false;
   for(let p=e.parentElement;p&&p!==content;p=p.parentElement) {
     if(['auto','scroll'].includes(getComputedStyle(p).overflowX)) return false;
   }
   const r=e.getBoundingClientRect();
   return r.right > innerWidth+2 || r.left < -2;
 }).slice(0,15).map(e=>({tag:e.tagName,cls:e.className.toString(),text:e.textContent.trim().slice(0,65),...rect(e)})) : [];
 return {width:innerWidth,documentWidth:document.documentElement.scrollWidth,
   styled:!!getComputedStyle(document.documentElement).getPropertyValue('--navy').trim(),
   stylesheets:[...document.querySelectorAll('link[rel=stylesheet]')].map(e=>({url:e.getAttribute('href'),loaded:!!e.sheet})),
   staff:!!content,content:rect(content),header:rect(header),heading:heading?.textContent.trim(),
   headingColor:heading?getComputedStyle(heading).color:null,
   headerBackground:header?getComputedStyle(header).backgroundImage:null,
   visibleText:(content||document.body).innerText.trim().length,
   contentOverflow:overflowing,
   tables:content?[...content.querySelectorAll('table')].map(e=>({standard:e.classList.contains('table'),scrollContainer:!!e.closest('.table-wrap,.table-container,.training-table')})):[]};
}'''


def main():
    # Access-report and count readers otherwise attempt the hardcoded office SQL
    # host even without .env. Exercise their unavailable-source UI immediately.
    try:
        import pyodbc
    except ImportError:
        pass
    else:
        def unavailable(*args, **kwargs):
            raise RuntimeError('External SQL disabled for local UI audit')
        patch('pyodbc.connect', side_effect=unavailable).start()
    OUT.mkdir(parents=True, exist_ok=True)
    inventory = source_inventory()
    results = []
    routes = []
    seen_templates = set()
    with tempfile.TemporaryDirectory(prefix='page-audit-') as tmp:
        root = Path(tmp)
        app = create_app(dict(TESTING=True, ENVIRONMENT='testing', DATABASE_URL='',
            DATABASE_PATH=str(root/'app.db'), PLATFORM_DATABASE_PATH=str(root/'platform.db'),
            TENANTS_DIR=str(root/'tenants'), UPLOAD_FOLDER=str(root/'uploads'),
            SECRET_KEY='disposable-page-audit', ENCRYPTION_KEY=os.environ['ENCRYPTION_KEY'],
            WTF_CSRF_ENABLED=False, TURNSTILE_ENABLED=False, ACTIVE_PHASE='all',
            SCHEDULER_ENABLED=False, WHATSAPP_WEB_ENABLED=False))
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES (1,'review','x','Example Administrator','admin','Management',1)")
            db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department,active) VALUES (2,'trainer','x','Example Trainer','staff','Fitness',1)")
            db.execute("INSERT INTO members(id,first_name,last_name,id_number,contact,member_ref,member_status,monthly_installment,tariff,itensity_ref) VALUES (1,'Example','Member','9001015009087','0712345678','UI-001','Active',345.58,'Premium 12','48213')")
            db.execute("INSERT INTO leads(id,full_name,phone,assigned_to,created_by) VALUES (1,'Example Prospect','0712345678',1,1)")
            db.execute("INSERT INTO membership_applications(id,lead_id,member_id,package,created_by) VALUES (1,1,1,'Premium 12',1)")
            db.execute("INSERT INTO queries(id,reference,member_id,category,query_type,description,assigned_to,logged_by) VALUES (1,'UI-001',1,'refund','Refund','Example request for review',1,1)")
            db.execute("INSERT INTO wa_conversations(id,wa_number,contact_phone,contact_name,linked_member_id) VALUES (1,'reception','27712345678','Example Member',1)")
            db.execute("INSERT INTO fb_conversations(id,sender_psid,sender_name) VALUES (1,'example','Example Prospect')")
            db.execute("INSERT INTO equipment(id,name) VALUES (1,'Example Treadmill')")
            db.execute("INSERT INTO fitness_classes(id,name,instructor_id,schedule_day,schedule_time) VALUES (1,'Example Class',2,'Monday','08:00')")
            db.execute("INSERT INTO events(id,name,slug,active) VALUES (1,'Example Event','ui-audit',1)")
            db.execute("INSERT INTO debicheck_mandates(id,member_id,merchant_id,client_ref1,client_ref2,account_name,account_type,account_number,branch_code,submit_date) VALUES (1,1,'example','UI-001','Example','Example Member','1','0000000000','470010','2026-10-28')")
            from onecpase.hr_workforce import ensure_profile
            ensure_profile(db, 1)
            ensure_profile(db, 2)
            if DASHBOARD_PERIODS:
                from onecpase.dashboard_reporting import resolve_period
                sample=resolve_period({'period':'cycle'})
                for stamp,amount in ((sample.date_to,1234567.89),(sample.previous_end.isoformat(),987654.32)):
                    db.execute("INSERT INTO collections(member_id,collection_date,amount_paid,status,method,notes) VALUES(1,?,?,'paid','eft','Synthetic dashboard preview')",(stamp,amount))
            db.commit()
            for name in app.jinja_env.list_templates():
                app.jinja_env.get_template(name)

        # Safe read-only preview endpoints for auth screens that normally redirect signed-in staff.
        @app.get('/ui-audit/auth/<name>')
        def auth_preview(name):
            assert name in ('login','otp','forgot_password','reset_password','change_password')
            return render_template('auth/'+name+'.html', tenant=None, remaining=120, masked_email='e***@example.com', token='preview')

        @app.get('/ui-audit/member-request/<kind>')
        def request_preview(kind):
            assert kind in ('refund','freeze','complaint','cancellation','banking','proof_of_payment')
            return render_template('queries/member_form.html',tenant=dict(slug='elev8',name='ELEv8 Health & Fitness',logo_filename=None),
                                   member=dict(first_name='Example',last_name='Member',contact='0712345678',email='example@example.com'),
                                   kind=kind,expired=False,submitted=False,quote=None,notice='Example notice')

        client = app.test_client()
        with client.session_transaction() as sess:
            sess.update(user_id=1,role='admin',full_name='Example Administrator',
                        permissions=list(default_permissions_for('admin','Management')))

        captured = []
        def record(sender, template, context, **extra):
            captured.append(template.name)
        template_rendered.connect(record, app)
        candidates = []
        for rule in sorted(app.url_map.iter_rules(), key=lambda r:r.rule):
            if 'GET' not in rule.methods:
                continue
            names = rendered_names(app.view_functions[rule.endpoint])
            if not names:
                continue
            values = {}
            unsupported = False
            for key, converter in rule._converters.items():
                if converter.__class__.__name__ == 'IntegerConverter': values[key] = 1
                elif key == 'number': values[key] = 'reception'
                elif key == 'slug' and 'event' in rule.endpoint: values[key] = 'ui-audit'
                else: unsupported = True
            if unsupported:
                routes.append(dict(rule=rule.rule,endpoint=rule.endpoint,templates=names,status='needs token or fixture'))
                continue
            path = rule.build(values)[1]
            if 'debicheck.add' == rule.endpoint: path += '?member_id=1'
            candidates.append((path, rule.endpoint, names))
        candidates += [(f'/ui-audit/auth/{name}',f'preview.{name}',[f'auth/{name}.html'])
                       for name in ('login','otp','forgot_password','reset_password','change_password')]
        from onecpase.training import chapters
        candidates += [(f"/training/topics/{chapters()[0]['slug']}",'training.topic',['training/topic.html']),
                       ('/reception-dashboard','dashboard.reception_dashboard',['department_dashboard.html']),
                       ('/fitness-dashboard','dashboard.fitness_dashboard',['department_dashboard.html']),
                       ('/my-dashboard','dashboard.work_dashboard',['department_dashboard.html']),
                       ('/communications/whatsapp/reception/new','comms.whatsapp_new_message',['communications/whatsapp_compose.html'])]
        candidates += [(f'/ui-audit/member-request/{kind}',f'preview.member_request.{kind}',['queries/member_form.html'])
                       for kind in ('refund','freeze','complaint','cancellation','banking','proof_of_payment')]
        if SUPPLEMENT:
            candidates = [c for c in candidates if c[0].startswith(('/ui-audit/member-request/','/training/topics/',
                '/reception-dashboard','/fitness-dashboard','/my-dashboard','/communications/whatsapp/reception/',
                '/communications/facebook/messages/1','/queries/1/workflow','/queries/workflow-setup','/dashboard'))]
        if ONLY_PATHS:
            candidates = [c for c in candidates if c[0] in ONLY_PATHS]
        if DASHBOARD_PERIODS:
            dashboard_paths={
                '/dashboard':'dashboard.html','/sales-dashboard':'sales_dashboard.html',
                '/reception-dashboard':'department_dashboard.html','/fitness-dashboard':'department_dashboard.html',
                '/my-dashboard':'department_dashboard.html','/collections/dashboard':'collections/dashboard.html',
                '/ptp/':'ptp/dashboard.html','/hr/me':'hr/me.html'}
            candidates=[(path+'?period='+mode,'preview.dashboard_period',[template])
                for path,template in dashboard_paths.items() for mode in ('day','week','cycle','3cycles','6cycles')]
        for path, endpoint, names in candidates:
            print(f'Checking route {path}', flush=True)
            captured.clear()
            try:
                response = client.get(path)
                row = dict(path=path,endpoint=endpoint,templates=names,status=response.status_code,
                           rendered=list(captured),location=response.headers.get('Location'),
                           html='text/html' in response.content_type)
                if response.status_code == 200 and 'text/html' in response.content_type:
                    seen_templates.update(captured)
                    routes.append(row)
                else:
                    routes.append(row)
            except Exception as exc:
                routes.append(dict(path=path,endpoint=endpoint,templates=names,status='error',error=str(exc)))
        template_rendered.disconnect(record, app)

        class Quiet(WSGIRequestHandler):
            def log_request(self, *args, **kwargs): pass
        server = make_server('127.0.0.1', 0, app, request_handler=Quiet)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                context = browser.new_context()
                context.add_cookies([dict(name='session',value=client.get_cookie('session').value,url=url)])
                # Use the pinned Chart.js dependency when cached for this audit.
                chart = ROOT / 'artifacts/page-consistency-20261028/chart.umd.min.js'
                def resource(route):
                    if route.request.url.startswith(url):
                        route.continue_()
                    elif route.request.url == 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js' and chart.exists():
                        route.fulfill(path=str(chart),content_type='application/javascript')
                    else:
                        route.abort()
                context.route('**/*', resource)
                page = context.new_page()
                page.set_default_timeout(10000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                checked = set()
                for row in routes:
                    if row['status'] != 200 or not row.get('rendered') or not row.get('html'): continue
                    key = (row['path'],) if DASHBOARD_PERIODS or row['path'].startswith('/ui-audit/member-request/') or 'dashboard' in row['path'] else tuple(row['rendered'])
                    if key in checked: continue
                    checked.add(key)
                    for width in VIEW_WIDTHS:
                        errors.clear()
                        page.set_viewport_size(dict(width=width,height=900))
                        try:
                            response = page.goto(url+row['path'],wait_until='load',timeout=15000)
                            page.wait_for_timeout(100)
                            metrics = page.evaluate(METRICS)
                            if DASHBOARD_PERIODS:
                                from urllib.parse import urlparse,parse_qs
                                from onecpase.dashboard_reporting import resolve_period
                                params={k:v[0] for k,v in parse_qs(urlparse(row['path']).query).items()}
                                expected=resolve_period(params)
                                form=page.locator('[data-dashboard-period-form]')
                                assert form.locator('[name=period]').input_value()==expected.mode
                                assert page.locator('[data-current-period]').inner_text()==f'{expected.start:%d %b %Y} – {expected.end:%d %b %Y}'
                                assert page.locator('[data-previous-period]').inner_text()==f'{expected.previous_start:%d %b %Y} – {expected.previous_end:%d %b %Y}'
                                assert page.locator('.dashboard-comparison-table tbody tr').count()>0
                                assert form.locator('[name=on]').is_visible()==(expected.mode in ('day','week'))
                                assert form.locator('[name=cycle]').is_visible()==(expected.mode not in ('day','week','custom'))
                                metrics['reporting_period']=dict(mode=expected.mode,start=expected.date_from,end=expected.date_to,
                                    previous_start=str(expected.previous_start),previous_end=str(expected.previous_end))
                            if row['path'].startswith('/ui-audit/member-request/'):
                                logo = page.locator('.member-request-header img')
                                assert logo.get_attribute('src').endswith('/tenant_logos/elev8.svg')
                                assert logo.evaluate('(e) => e.complete && e.naturalWidth > 0'), 'ELEv8 logo must load'
                                assert page.locator('.member-request-header').evaluate('(e) => getComputedStyle(e).backgroundColor') == 'rgb(213, 31, 55)'
                                assert page.locator('button[type=submit]').evaluate('(e) => getComputedStyle(e).backgroundColor') == 'rgb(213, 31, 55)'
                                assert not page.locator('#correctionLabel').is_visible(), 'Correction field should start hidden'
                                page.locator('#correct').select_option('no')
                                assert page.locator('#correctionLabel').is_visible()
                                assert page.locator('#correction').evaluate('(e) => e.required')
                                page.locator('#correct').select_option('yes')
                                assert not page.locator('#correctionLabel').is_visible()
                                assert not page.locator('#correction').evaluate('(e) => e.required')
                                canvas = page.locator('#signatureCanvas')
                                canvas.scroll_into_view_if_needed()
                                box = canvas.bounding_box()
                                page.mouse.move(box['x']+20,box['y']+20)
                                page.mouse.down()
                                page.mouse.move(box['x']+65,box['y']+40,steps=4)
                                page.mouse.up()
                                assert page.locator('#signature').input_value() != '[]', 'Signature should be captured'
                                page.locator('#clearSignature').click()
                                assert page.locator('#signature').input_value() == '[]', 'Signature clear should reset strokes'
                            flags = []
                            if response.status != 200: flags.append('render failed')
                            if not metrics['styled']: flags.append('missing shared CSS')
                            if metrics['visibleText'] < 25: flags.append('blank content')
                            if metrics['staff'] and not metrics['header']: flags.append('missing standard header')
                            if metrics['documentWidth'] > width+1: flags.append('document overflow')
                            if metrics['contentOverflow']: flags.append('content clipped or overflowing')
                            if errors: flags.append('JavaScript errors')
                            result = dict(path=row['path'],templates=row['rendered'],status=response.status,flags=flags,errors=list(errors),**metrics)
                            results.append(result)
                            if flags or width == 1366 or ALL_SCREENSHOTS or (DASHBOARD_PERIODS and width==390 and 'period=cycle' in row['path']):
                                name = re.sub(r'[^a-zA-Z0-9]+','-',row['path']).strip('-') or 'welcome'
                                screenshot = f'{name}-{width}.png'
                                page.screenshot(path=str(OUT/screenshot),full_page=True,timeout=10000,animations='disabled')
                                result['screenshot'] = screenshot
                            if DASHBOARD_PERIODS and row['path']=='/sales-dashboard?period=cycle' and width==1366:
                                form=page.locator('[data-dashboard-period-form]')
                                form.locator('[name=consultant]').select_option('1')
                                form.locator('[name=period]').select_option('week')
                                assert form.locator('[name=on]').is_visible()
                                form.get_by_role('button',name='Apply period').click()
                                page.wait_for_load_state('load')
                                assert 'period=week' in page.url and 'consultant=1' in page.url
                                form=page.locator('[data-dashboard-period-form]')
                                form.locator('[name=period]').select_option('cycle')
                                form.locator('[name=cycle]').fill('2026-10')
                                form.get_by_role('button',name='Apply period').click()
                                page.wait_for_load_state('load')
                                assert '15 Sep 2026 – 14 Oct 2026' in page.locator('[data-current-period]').inner_text()
                                assert not errors, errors
                                result['selector_interaction']='week and historical cycle submitted; consultant retained'
                        except Exception as exc:
                            results.append(dict(path=row['path'],width=width,flags=['browser check failed'],error=str(exc)))
                    print(f"Reviewed {row['path']}", flush=True)
                    (OUT/'checkpoint.json').write_text(json.dumps(dict(routes=routes,views=results),indent=2),encoding='utf-8')
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
    for row in inventory:
        row['rendered'] = row['template'] in seen_templates
    payload = dict(templates=inventory,routes=routes,views=results,
                   limitations=['Local checkout, not deployed production.',
                                'Disposable synthetic data and administrator session; role-specific and populated workflow variants require separate coverage.',
                                'Token-protected and missing-fixture pages are explicitly listed; source inspection does not certify their rendered layout.',
                                'External fonts blocked; local fallback typography tested. Chart.js uses the pinned cached dependency when available.'])
    (OUT/'results.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(dict(templates=len(inventory),rendered_templates=len(seen_templates),views=len(results),
                         flagged_views=sum(bool(r['flags']) for r in results)),indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--supplement',action='store_true',help='Review extra fixtures and helper-rendered screens only.')
    parser.add_argument('--report',action='store_true',help='Build the Markdown report from completed browser runs.')
    parser.add_argument('--output',help='Write evidence to a separate directory, preserving the original audit.')
    parser.add_argument('--paths',nargs='+',help='Review only the specified fixture URLs.')
    parser.add_argument('--all-screenshots',action='store_true',help='Save screenshots at every tested width.')
    parser.add_argument('--dashboard-periods',action='store_true',help='Check all eight dashboards across five reporting views.')
    parser.add_argument('--widths',type=int,nargs='+',default=[1366,768,390],help='Viewport widths for this review.')
    args = parser.parse_args()
    SUPPLEMENT = args.supplement
    ONLY_PATHS = args.paths
    ALL_SCREENSHOTS = args.all_screenshots
    DASHBOARD_PERIODS = args.dashboard_periods
    VIEW_WIDTHS = args.widths
    if args.output:
        OUT = Path(args.output).resolve()
    if SUPPLEMENT:
        OUT = OUT / 'supplement'
    if args.report:
        write_report()
    else:
        main()
