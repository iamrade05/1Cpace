from flask import g
from onecpase import whatsapp_web
from onecpase.comms import send_whatsapp_message


def test_missing_api_credentials_use_linked_selenium_fallback(app,monkeypatch):
    app.config.update(WA_TOKEN='',WHATSAPP_WEB_ENABLED=True,WHATSAPP_WEB_TENANT='elev8')
    calls=[]
    monkeypatch.setattr(whatsapp_web,'get_status',lambda:dict(started=True,logged_in=True))
    monkeypatch.setattr(whatsapp_web,'queue_message',lambda phone,text:calls.append((phone,text)))
    with app.test_request_context():
        g.tenant_slug='elev8'
        result=send_whatsapp_message('','0821234567','Test')
        assert result['queued'] and result['transport']=='whatsapp_web'
        assert calls==[('0821234567','Test')]
        g.tenant_slug='other-gym'
        assert 'error' in send_whatsapp_message('','0821234567','Test')
        assert len(calls)==1


def test_unlinked_browser_does_not_claim_to_send(app,monkeypatch):
    app.config.update(WA_TOKEN='',WHATSAPP_WEB_ENABLED=True,WHATSAPP_WEB_TENANT='elev8')
    monkeypatch.setattr(whatsapp_web,'get_status',lambda:dict(started=True,logged_in=False))
    with app.test_request_context():
        g.tenant_slug='elev8'
        assert 'error' in send_whatsapp_message('','0821234567','Test')


def test_whatsapp_setup_without_login_redirects_instead_of_forbidden(app,client):
    app.config['TESTING']=False
    response=client.get('/admin/whatsapp-web/')
    assert response.status_code==302
    assert '/login' in response.headers['Location']


def test_whatsapp_setup_without_gym_prompts_for_selection(app,client):
    from onecpase.database import get_db
    with app.app_context():
        db=get_db()
        db.execute("INSERT INTO users(id,username,password_hash,full_name,role,active) VALUES(1,'admin','x','Admin','admin',1)")
        db.commit()
    with client.session_transaction() as session:
        session.update(user_id=1,role='admin',tenant_slug=None)
    app.config['TESTING']=False
    response=client.get('/admin/whatsapp-web/')
    assert response.status_code==302
    assert response.headers['Location']=='/'


def test_pairing_request_timeout_is_reported_not_left_requesting(monkeypatch):
    from datetime import datetime,timedelta,timezone
    monkeypatch.setattr(whatsapp_web,'_state',dict(pairing_status='Requesting',pairing_requested_at=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()))
    status=whatsapp_web.get_status()
    assert status['pairing_status'] is None
    assert 'timed out' in status['last_error']


def test_phone_linked_syncing_is_detected_and_qr_hidden():
    class Hidden:
        @property
        def first(self): return self
        def is_visible(self,**kwargs): return False
    class Page:
        def locator(self,selector): return Hidden()
        def evaluate(self,script): return True
    from pathlib import Path
    assert not whatsapp_web._poll_login_state(Page(),Path('.'))
    status=whatsapp_web.get_status()
    assert status['syncing']
    assert status['pairing_code'] is None


def test_selenium_browser_uses_current_chrome_user_agent(tmp_path,monkeypatch):
    from unittest.mock import MagicMock
    from onecpase.whatsapp_selenium import SeleniumPage
    driver=MagicMock()
    driver.execute_script.return_value='Mozilla/5.0 HeadlessChrome/147.0.0.0 Safari/537.36'
    monkeypatch.setattr('onecpase.whatsapp_selenium.webdriver.Chrome',lambda **kwargs:driver)
    page=SeleniumPage(tmp_path)
    driver.execute_cdp_cmd.assert_called_once_with('Network.setUserAgentOverride',{'userAgent':'Mozilla/5.0 Chrome/147.0.0.0 Safari/537.36'})
    page.close()


def test_dead_browser_ends_the_worker_instead_of_polling_forever():
    import pytest
    class InvalidSessionIdException(Exception): pass
    class Dead:
        @property
        def first(self): return self
        def is_visible(self,**kwargs): raise InvalidSessionIdException('Message: invalid session id')
    class Page:
        def locator(self,selector): return Dead()
    from pathlib import Path
    with pytest.raises(InvalidSessionIdException):
        whatsapp_web._poll_login_state(Page(),Path('.'))
    assert 'invalid session id' in whatsapp_web.get_status()['last_error']


def test_page_errors_that_are_not_a_dead_browser_keep_polling():
    assert whatsapp_web._browser_gone(RuntimeError('Message: chrome not reachable'))
    assert whatsapp_web._browser_gone(RuntimeError('Target page, context or browser has been closed'))
    assert not whatsapp_web._browser_gone(TimeoutError('Timeout 1000ms exceeded'))
    assert not whatsapp_web._browser_gone(RuntimeError('stale element reference'))


def test_supervisor_keeps_restarting_with_backoff(monkeypatch):
    runs,sleeps=[],[]
    monkeypatch.setattr(whatsapp_web,'_run_worker',lambda *args:runs.append(1))
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps)==6: raise SystemExit
    monkeypatch.setattr(whatsapp_web.time,'sleep',sleep)
    import pytest
    with pytest.raises(SystemExit):
        whatsapp_web._supervise_worker(None,None,0)
    assert len(runs)==6
    assert sleeps==[5,10,20,40,80,160]


def test_selenium_close_survives_a_crashed_browser(tmp_path,monkeypatch):
    from unittest.mock import MagicMock
    from onecpase.whatsapp_selenium import SeleniumPage
    driver=MagicMock()
    driver.execute_script.return_value='Mozilla/5.0 Chrome/147.0.0.0'
    driver.quit.side_effect=RuntimeError('invalid session id')
    monkeypatch.setattr('onecpase.whatsapp_selenium.webdriver.Chrome',lambda **kwargs:driver)
    SeleniumPage(tmp_path).close()
