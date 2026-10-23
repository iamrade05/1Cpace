from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

from onecpase import integrations


def test_simultaneous_pushes_keep_each_members_reference(app, monkeypatch):
    entered = Event()
    second_attempt = Event()
    real_lock = Lock()

    class TrackingLock:
        attempts = 0
        def acquire(self):
            self.attempts += 1
            if self.attempts == 2:
                second_attempt.set()
            real_lock.acquire()
        def release(self):
            real_lock.release()

    saved_to_sibling = []
    original_save = lambda *args: saved_to_sibling.append(args)
    driver = SimpleNamespace(
        validate_member=lambda data: [],
        _save_itensity_ref=original_save,
        ItensityPushError=RuntimeError,
    )
    def push(data, **kwargs):
        if data['id'] == 1:
            entered.set()
            assert second_attempt.wait(5)
        driver._save_itensity_ref(data['id'], f"IT-{data['id']}")
    driver.push = push
    monkeypatch.setattr(integrations, '_load_driver', lambda *args: (driver, None))
    monkeypatch.setattr(integrations, 'PLAYWRIGHT_LOCK', TrackingLock())

    def submit(mid):
        with app.app_context():
            return integrations.push_member_to_itensity({'id': mid, 'payment_type': 'Debit Order'})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, 1)
        assert entered.wait(5)
        second = pool.submit(submit, 2)
        assert first.result(timeout=10) == (True, 'IT-1', None)
        assert second.result(timeout=10) == (True, 'IT-2', None)
    assert saved_to_sibling == []
    assert driver._save_itensity_ref is original_save
