import json
from types import SimpleNamespace
from urllib.error import URLError

import pytest
import deploy_beta


def test_release_test_helper_rejects_failed_suite(monkeypatch):
    monkeypatch.setattr(deploy_beta.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(SystemExit, match="Release tests failed"):
        deploy_beta.run_release_tests()


def test_legacy_beta_target_refuses_deployment_before_testing_or_connecting(monkeypatch):
    monkeypatch.setattr(deploy_beta, "parse_args", lambda: SimpleNamespace(check_only=False))
    monkeypatch.setattr(deploy_beta, "release_paths", lambda: [])
    monkeypatch.setattr(deploy_beta, "validate_release", lambda paths: None)
    monkeypatch.setattr(deploy_beta, "run_release_tests", lambda: pytest.fail("Unsafe target must be refused before testing"))

    def unexpected_connection(args):
        pytest.fail("The shared production target must be refused before connecting")

    monkeypatch.setattr(deploy_beta, "connect", unexpected_connection)
    with pytest.raises(SystemExit, match="Beta deployment refused"):
        deploy_beta.main()


@pytest.mark.parametrize("status,payload,expected", [
    (200, {"status": "ok"}, True),
    (200, {"status": "degraded"}, False),
    (503, {"status": "ok"}, False),
])
def test_public_health_requires_success_status_and_healthy_body(monkeypatch, status, payload, expected):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps(payload).encode()
    response = Response()
    response.status = status
    monkeypatch.setattr(deploy_beta, "urlopen", lambda *a, **k: response)
    assert deploy_beta.check_public_health() is expected


def test_unreachable_public_health_fails(monkeypatch):
    def unavailable(*args, **kwargs):
        raise URLError("offline")
    monkeypatch.setattr(deploy_beta, "urlopen", unavailable)
    assert deploy_beta.check_public_health() is False
