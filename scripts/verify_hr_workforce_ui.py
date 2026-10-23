"""Render HR using disposable data; never open local or live gym databases."""
import os
os.environ['PYTHON_DOTENV_DISABLED']='1'
os.environ['APP_ENV']='testing'
os.environ['SCHEDULER_ENABLED']='0'
os.environ['WHATSAPP_WEB_ENABLED']='0'
os.environ['ENCRYPTION_KEY']='kRWWWElbDm6KCdoe_UpkzAT4BnCrXq4msN8btwUXIS8='
os.environ['RATELIMIT_STORAGE_URL']='memory://'

import json
from pathlib import Path
import tempfile
import threading
from werkzeug.serving import make_server, WSGIRequestHandler
from playwright.sync_api import sync_playwright
from onecpase import create_app
from onecpase.database import get_db
from onecpase.hr_workforce import ensure_profile


def main():
    out=Path('artifacts/hr-workforce-ui'); out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hr-workforce-ui-') as folder:
        root=Path(folder)
        app=create_app(dict(TESTING=True,DATABASE_URL='',DATABASE_PATH=str(root/'app.db'),
            PLATFORM_DATABASE_PATH=str(root/'platform.db'),TENANTS_DIR=str(root/'tenants'),
            UPLOAD_FOLDER=str(root/'uploads'),SECRET_KEY='hr-ui-only',
            ENCRYPTION_KEY=os.environ['ENCRYPTION_KEY'],WTF_CSRF_ENABLED=False,
            TURNSTILE_ENABLED=False,ACTIVE_PHASE='all'))
        with app.app_context():
            db=get_db()
            for uid,name,role,dept in [(1,'Example HR Administrator','admin','Management'),(2,'Example Reception Employee','staff','Reception')]:
                db.execute('INSERT INTO users(id,username,password_hash,full_name,role,department,email,active) VALUES (?,?,?,?,?,?,?,1)',
                           (uid,f'preview{uid}','x',name,role,dept,f'preview{uid}@example.com'))
                ensure_profile(db,uid)
                db.execute("UPDATE hr_profiles SET start_date='2025-01-01',weekdays='[0,1,2,3,4]',employment_type='employee' WHERE user_id=?",(uid,))
            db.commit()
        client=app.test_client()
        with client.session_transaction() as sess:
            sess.update(user_id=1,role='admin',full_name='Example HR Administrator',permissions=[])
        cookie=client.get_cookie('session')
        class QuietHandler(WSGIRequestHandler):
            def log_request(self,*args,**kwargs):
                pass
        server=make_server('127.0.0.1',0,app,request_handler=QuietHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        results=[]; errors=[]
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                context=browser.new_context()
                context.add_cookies([dict(name='session',value=cookie.value,url=url)])
                page=context.new_page(); page.on('pageerror',lambda error:errors.append(str(error)))
                for width in [1366,390]:
                    page.set_viewport_size(dict(width=width,height=900))
                    for name,path in [('me','/hr/me'),('setup','/hr/people'),('leave','/hr/leave/request'),
                                      ('calendar','/hr/leave/calendar'),('roster','/hr/roster'),('week','/hr/weeks')]:
                        response=page.goto(url+path,wait_until='networkidle')
                        page.screenshot(path=str(out/f'{name}-{width}.png'),full_page=True)
                        overflow=page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
                        results.append(dict(page=name,width=width,status=response.status,document_overflow=overflow))
                browser.close()
        finally:
            server.shutdown(); thread.join(timeout=5)
        (out/'results.json').write_text(json.dumps(dict(pages=results,errors=errors),indent=2),encoding='utf-8')
        print(json.dumps(dict(pages=len(results),errors=errors,failed=[r for r in results if r['status']!=200 or r['document_overflow']]),indent=2))
        if errors or any(r['status']!=200 or r['document_overflow'] for r in results):
            raise SystemExit(1)


if __name__=='__main__':
    main()
