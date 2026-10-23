"""Verify exported native-client web rendering with fictional data and mocked auth.

Run after `cd mobile && npm run export`. No live gym requests.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import re
import threading

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts' / 'mobile-review'


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    dist = ROOT / 'mobile' / 'dist'
    if not (dist / 'index.html').is_file():
        raise SystemExit('Export the mobile web bundle first.')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(dist)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    findings = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for width, height in ((390, 844), (360, 800)):
                context = browser.new_context(viewport=dict(width=width, height=height))
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route('https://**/*', lambda route: route.abort())
                page.goto(f'http://127.0.0.1:{server.server_port}', wait_until='networkidle')
                page.get_by_role('button', name='Explore a fictional preview').wait_for()
                page.get_by_role('button', name='Open full staff workspace', exact=True).click()
                page.get_by_text('Full staff workspace', exact=True).wait_for()
                page.get_by_role('button', name='Return to member access', exact=True).click()
                page.get_by_role('tab', name=re.compile('member')).click()
                page.screenshot(path=str(OUTPUT / f'login-{width}.png'))
                page.get_by_role('button', name='Explore a fictional preview').click()
                page.get_by_text('Hello, Alex.', exact=True).wait_for()
                assert page.get_by_text(re.compile('FICTIONAL PREVIEW')).is_visible()
                page.screenshot(path=str(OUTPUT / f'member-home-{width}.png'))
                page.get_by_role('button', name=re.compile('Classes')).click()
                page.get_by_text('Strength foundations', exact=True).wait_for()
                page.get_by_role('button', name=re.compile('Book this class')).first.click()
                page.get_by_text('Preview updated. No real booking was made.', exact=True).wait_for()
                page.screenshot(path=str(OUTPUT / f'member-classes-{width}.png'))
                page.get_by_role('button', name='Sign out', exact=True).click()
                page.get_by_role('tab', name=re.compile('staff')).click()
                page.get_by_role('button', name='Explore a fictional preview').click()
                page.get_by_text('Hello, Taylor.', exact=True).wait_for()
                page.screenshot(path=str(OUTPUT / f'staff-home-{width}.png'))
                page.get_by_role('button', name=re.compile('My leads')).click()
                page.get_by_text('Sam Example', exact=True).wait_for()
                page.get_by_role('textbox', name='Filter loaded records').fill('Jordan')
                page.get_by_text('Jordan Example', exact=True).wait_for()
                assert page.get_by_text('Sam Example', exact=True).count() == 0
                overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth')
                assert not overflow, f'Horizontal overflow at {width}px'
                assert not errors, errors
                findings.append(dict(viewport=width, member_preview=True, staff_preview=True,
                                     simulated_booking=True, filtering=True, overflow=False, page_errors=errors))
                context.close()

            context = browser.new_context(viewport=dict(width=390, height=844))
            page = context.new_page()
            def mock(route):
                path = route.request.url.split('/api/mobile/v1/elev8')[-1]
                headers = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'content-type,authorization',
                           'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'}
                status, data = 200, {}
                if route.request.method == 'OPTIONS':
                    route.fulfill(status=204, headers=headers)
                    return
                if path == '/auth/start': data = {'challenge': 'fictional-challenge'}
                elif path == '/auth/verify': data = {'token': 'fictional-token', 'kind': 'member'}
                elif path == '/home': data = {'kind':'member','name':'Test Member','gym':'Test Gym','package':'Test membership','features':['plans']}
                elif path == '/member/plans': status, data = 401, {'error':'Expired test session'}
                elif path == '/auth/logout': data = {'ok': True}
                else: raise AssertionError('Unexpected API route: '+path)
                route.fulfill(status=status, content_type='application/json', headers=headers, body=json.dumps(data))
            page.route('https://www.1cpace.co.za/api/mobile/**', mock)
            page.goto(f'http://127.0.0.1:{server.server_port}', wait_until='networkidle')
            page.get_by_role('tab', name=re.compile('member')).click()
            page.get_by_role('textbox', name='Membership reference', exact=True).fill('fictional-member')
            page.get_by_role('textbox', name='Email on your membership', exact=True).fill('fictional@example.com')
            page.get_by_role('button', name=re.compile('Send sign-in code')).click()
            page.get_by_role('textbox', name='Sign-in code', exact=True).fill('123456')
            page.get_by_role('button', name='Verify & continue', exact=True).click()
            page.get_by_text('Hello, Test.', exact=True).wait_for()
            page.get_by_role('button', name=re.compile('My programme')).click()
            page.get_by_text('Session expired. Please sign in again.', exact=True).wait_for()
            assert page.get_by_text('Hello, Test.', exact=True).count() == 0
            findings.append(dict(mocked_sign_in=True, expired_session_clears_private_view=True))
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    (OUTPUT / 'results.json').write_text(json.dumps(findings, indent=2), encoding='utf-8')
    print(json.dumps(findings, indent=2))


if __name__ == '__main__':
    main()
