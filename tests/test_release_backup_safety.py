import importlib.util
from pathlib import Path
import sqlite3

import pytest
import onecpase.backup_retention as retention


@pytest.fixture
def release(monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'artifacts/workflow_release_remote.py'
    spec = importlib.util.spec_from_file_location('release_safety_under_test', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'retention_policy', lambda: retention)
    return module


def test_failed_release_backup_removes_partial_and_preserves_source(tmp_path, release):
    source = tmp_path / 'source.db'; source.write_bytes(b'not sqlite')
    target = tmp_path / 'backups' / 'snapshot.db'
    with pytest.raises(sqlite3.DatabaseError):
        release.backup_sqlite(source, target)
    assert not target.exists()
    assert source.read_bytes() == b'not sqlite'


def test_release_cannot_overwrite_an_existing_restore_point(tmp_path, release):
    source = tmp_path / 'source.db'; source.write_bytes(b'source')
    target = tmp_path / 'snapshot.db'; target.write_bytes(b'existing recovery data')
    with pytest.raises(AssertionError, match='already exists'):
        release.backup_sqlite(source, target)
    assert target.read_bytes() == b'existing recovery data'


def test_rehearsal_cleanup_preserves_release_evidence_and_other_directories(tmp_path, release, monkeypatch):
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    stage = tmp_path / '.workflow-release-test'; stage.mkdir()
    for name in ('rehearsal', 'database-copies', 'incoming'):
        (stage / name).mkdir(); (stage / name / 'data').write_text('preserved or disposable')
    (stage / 'manifest.json').write_text('evidence')
    release.cleanup_rehearsal(stage)
    assert not (stage / 'rehearsal').exists() and not (stage / 'database-copies').exists()
    assert (stage / 'incoming' / 'data').exists()
    assert (stage / 'manifest.json').read_text() == 'evidence'
    with pytest.raises(AssertionError):
        release.cleanup_rehearsal(tmp_path)
