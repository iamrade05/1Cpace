"""Exercise the training reader against disposable data at two screen sizes."""
import os
for key, value in {'PYTHON_DOTENV_DISABLED':'1', 'APP_ENV':'testing', 'SCHEDULER_ENABLED':'0', 'TURNSTILE_ENABLED':'0', 'WHATSAPP_WEB_ENABLED':'0', 'RATELIMIT_STORAGE_URL':'memory://'}.items():
    os.environ[key] = value

import tempfile
import threading
from pathlib import Path
from flask import redirect, session
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from onecpase import create_app
from onecpase.database import get_db


def main():
    output = Path('artifacts/training-ui')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='training-ui-') as temporary:
        root = Path(temporary)
        app = create_app({'TESTING':True, 'ENVIRONMENT':'testing', 'DATABASE_URL':'', 'DATABASE_PATH':str(root/'test.db'), 'PLATFORM_DATABASE_PATH':str(root/'platform.db'), 'TENANTS_DIR':str(root/'tenants'), 'UPLOAD_FOLDER':str(root/'uploads'), 'SECRET_KEY':'training-preview', 'ENCRYPTION_KEY':'kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8=', 'WTF_CSRF_ENABLED':False, 'TURNSTILE_ENABLED':False, 'ACTIVE_PHASE':1})
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO users(id,username,password_hash,full_name,role,department) VALUES (1,'preview','x','Training Reviewer','staff','Sales')")
            db.commit()
        @app.get('/training-preview-login')
        def preview_login():
            session.update(user_id=1, role='staff', full_name='Training Reviewer', permissions=[])
            return redirect('/training')
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                for width in [1366, 390]:
                    page = browser.new_page(viewport={'width':width, 'height':900})
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/training-preview-login', wait_until='networkidle')
                    assert page.locator('.training-topic:visible').count() == 7
                    page.screenshot(path=str(output/f'training-{width}.png'), full_page=True)
                    search = page.get_by_label('Find a training topic')
                    search.fill('failed debit')
                    assert page.locator('.training-topic:visible').count() > 0
                    search.fill('no-such-topic-xyz')
                    assert page.locator('#training-empty').is_visible()
                    search.fill('')
                    assert page.locator('.training-topic:visible').count() == 7
                    page.get_by_role('link', name='Promise to Pay (PTP)', exact=True).click()
                    page.wait_for_load_state('networkidle')
                    assert page.locator('.training-reader table').count() > 0
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    page.screenshot(path=str(output/f'ptp-{width}.png'), full_page=True)
                    with page.expect_download() as download:
                        page.get_by_role('link', name='Download Word manual').click()
                    assert download.value.suggested_filename.endswith('.docx')
                    page.close()
                browser.close()
            assert not errors, errors
            print('Training UI passed: desktop/mobile, search, reader tables, no page overflow, Word download, no JavaScript errors.')
        finally:
            server.shutdown()
            thread.join()


if __name__ == '__main__':
    main()
