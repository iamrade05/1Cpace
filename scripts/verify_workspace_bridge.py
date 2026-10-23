"""Fictional browser checks for workspace printing and Android recording uploads."""
from pathlib import Path
import base64
import json
import subprocess

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts/workspace-review'
OUTPUT.mkdir(parents=True, exist_ok=True)
bridge = json.loads(subprocess.check_output([
    'node', '-e', "process.stdout.write(JSON.stringify(require('./src/workspaceBridge.ts').workspaceBridge))"
], cwd=ROOT / 'mobile', text=True))
env = Environment(loader=ChoiceLoader([
    DictLoader({'base.html': '''<!doctype html><html><head><meta name="csrf-token" content="fictional-csrf">
      <link rel="stylesheet" href="/static/css/tokens.css"><link rel="stylesheet" href="/static/css/components.css">
      </head><body class="oc"><main style="padding:16px">{% block content %}{% endblock %}</main>{% block scripts %}{% endblock %}</body></html>'''}),
    FileSystemLoader(ROOT / 'onecpase/templates'),
]))
env.globals['url_for'] = lambda endpoint, **kwargs: '/upload' if endpoint == 'screen_recordings.upload' else '/unused'
recording_page = env.get_template('screen_recordings/index.html').render(recordings=[])
pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGZkAAAAASUVORK5CYII=')
uploads = []
image_requests = []
contract = '''<!doctype html><html><head><link rel="stylesheet" href="/fixture.css"></head><body>
 <h1>Fictional contract</h1><input id="name" value="Old name"><input id="secret" type="password" value="never-print-this">
 <textarea id="notes">Old notes</textarea><select id="choice"><option>First</option><option>Second</option></select>
 <canvas id="signature" width="200" height="60"></canvas><img id="logo" src="/private-logo.png">
 <button id="print" onclick="window.print()">Print / Save PDF</button><script>window.fixture=true;</script></body></html>'''


def handle(route):
    url = route.request.url
    if url.endswith('/upload'):
        uploads.append({'body': route.request.post_data_buffer.decode('latin1'), 'cookie': route.request.headers.get('cookie', '')})
        route.fulfill(status=201, content_type='application/json', body='{"recording_id":1,"message":"Saved"}')
    elif url.endswith('/recordings'):
        route.fulfill(content_type='text/html', body=recording_page)
    elif url.endswith('/private-logo.png'):
        image_requests.append(route.request.headers.get('cookie', ''))
        route.fulfill(content_type='image/png', body=pixel)
    elif '/static/css/' in url:
        route.fulfill(content_type='text/css', body=(ROOT / 'onecpase/static/css' / url.rsplit('/', 1)[1]).read_text(encoding='utf-8'))
    elif url.endswith('/fixture.css'):
        route.fulfill(content_type='text/css', body='body { color: rgb(21, 46, 43); }\nh1 { font-size: 24px; }')
    else:
        route.fulfill(content_type='text/html', body=contract)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    try:
        context = browser.new_context(viewport={'width': 390, 'height': 844})
        context.route('**/*', handle)
        context.add_cookies([{'name': 'session', 'value': 'fictional-session', 'domain': 'workspace.test', 'path': '/'}])
        context.add_init_script('''window.ReactNativeWebView={postMessage:function(value){window.__printed=JSON.parse(value);}};
          Object.defineProperty(navigator, 'mediaDevices', {value: {}});''')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('https://workspace.test/contracts/1', wait_until='networkidle')
        page.evaluate(bridge)
        page.evaluate('''() => {
          document.querySelector('#name').value='Fictional updated member';
          document.querySelector('#notes').value='Updated fictional notes';
          document.querySelector('#choice').selectedIndex=1;
          document.querySelector('#signature').getContext('2d').fillRect(5,5,30,10);
        }''')
        page.get_by_role('button', name='Print / Save PDF').click()
        page.wait_for_function('window.__printed && window.__printed.type === "print"')
        printed = page.evaluate('window.__printed')
        assert printed['url'] == 'https://workspace.test/contracts/1'
        capture = context.new_page()
        capture.set_content(printed['html'], wait_until='networkidle')
        assert capture.locator('#name').input_value() == 'Fictional updated member'
        assert capture.locator('#notes').input_value() == 'Updated fictional notes'
        assert capture.locator('#choice').input_value() == 'Second'
        assert capture.locator('#secret').input_value() == ''
        assert capture.locator('canvas,script').count() == 0
        assert capture.locator('img[src^="data:image/png"]').count() == 2
        assert 'never-print-this' not in printed['html']
        assert all('session=fictional-session' in cookie for cookie in image_requests)
        capture.screenshot(path=str(OUTPUT / 'fictional-print-390.png'), full_page=True)
        page.goto('https://workspace.test/recordings', wait_until='networkidle')
        assert page.get_by_role('button', name='Start recording', exact=False).is_disabled()
        page.locator('#recordingTitle').fill('Fictional phone recording')
        page.locator('#recordingFile').set_input_files({'name': 'fictional.mp4', 'mimeType': 'video/mp4', 'buffer': b'fictional-upload-test'})
        page.screenshot(path=str(OUTPUT / 'recording-upload-390.png'), full_page=True)
        page.get_by_role('button', name='Upload recording', exact=True).click()
        page.wait_for_function('document.querySelector("#uploadRecording").disabled')
        assert len(uploads) == 1
        assert 'Fictional phone recording' in uploads[0]['body'] and 'fictional.mp4' in uploads[0]['body']
        assert 'fictional-csrf' in uploads[0]['body'] and 'session=fictional-session' in uploads[0]['cookie']
        assert not errors, errors
        result = {'printing_preserves_current_fields_and_signatures': True, 'printing_excludes_passwords_and_scripts': True,
                  'protected_images_use_session': True, 'phone_recording_upload_preserves_csrf_and_session': True, 'page_errors': errors}
        (OUTPUT / 'bridge-results.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        browser.close()
