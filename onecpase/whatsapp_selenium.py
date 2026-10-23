"""Selenium browser adapter for the existing single-thread WhatsApp worker."""
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait


class Locator:
    def __init__(self,driver,selector):
        self.driver,self.selector=driver,selector
    @property
    def first(self):
        return self
    def element(self):
        # WhatsApp refresh buttons have a text fallback in the Playwright selectors.
        for part in self.selector.split(', '):
            if ':has-text(' in part:
                tag,text=part.split(':has-text(',1)
                text=text.rstrip(')').strip('"')
                found=self.driver.find_elements(By.XPATH,f"//{tag}[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text.lower()}')]")
            else:
                found=self.driver.find_elements(By.CSS_SELECTOR,part)
            for item in found:
                if item.is_displayed():
                    return item
        return None
    def is_visible(self,timeout=0):
        return self.element() is not None
    def wait_for(self,state='visible',timeout=30000):
        return WebDriverWait(self.driver,timeout/1000).until(lambda _:self.element())
    def click(self,timeout=5000):
        self.wait_for(timeout=timeout).click()
    def screenshot(self,path):
        self.wait_for().screenshot(path)


class Keyboard:
    def __init__(self,driver): self.driver=driver
    def press(self,key):
        self.driver.switch_to.active_element.send_keys(*({'Enter':[Keys.ENTER],'Control+A':[Keys.CONTROL,'a']}[key]))
    def type(self,text,delay=0): self.driver.switch_to.active_element.send_keys(text)


class SeleniumPage:
    def __init__(self,session_dir,binary=None):
        session_dir.mkdir(parents=True,exist_ok=True)
        options=webdriver.ChromeOptions()
        options.page_load_strategy="eager"
        options.add_argument('--headless=new')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-background-timer-throttling')
        options.add_argument('--disable-renderer-backgrounding')
        options.add_argument('--disable-backgrounding-occluded-windows')
        options.add_argument('--window-size=1280,900')
        options.add_argument('--user-data-dir='+str(session_dir.resolve()))
        if binary: options.binary_location=binary
        self.driver=webdriver.Chrome(options=options)
        self.driver.command_executor._client_config.timeout=20
        self.driver.set_page_load_timeout(25)
        # WhatsApp rejects the HeadlessChrome product token as an unsupported
        # browser even when the installed Chromium version is current.
        user_agent = self.driver.execute_script('return navigator.userAgent')
        self.driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            'userAgent': user_agent.replace('HeadlessChrome/', 'Chrome/')})
        self.keyboard=Keyboard(self.driver)
    def locator(self,selector): return Locator(self.driver,selector)
    def goto(self,url,**kwargs): self.driver.get(url)
    def evaluate(self,script): return self.driver.execute_script('return '+script)
    def wait_for_timeout(self,ms): time.sleep(ms/1000)
    def close(self):
        # quit() on a crashed Chrome can raise; the restart must still go ahead.
        try: self.driver.quit()
        except Exception: pass
