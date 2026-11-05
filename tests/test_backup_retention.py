import sqlite3
from types import SimpleNamespace
from contextlib import closing

import pytest

from onecpase.backup_retention import ensure_backup_capacity, prune_databases, prune_deployments, snapshot_database


def database(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE example(value TEXT)')
        db.execute("INSERT INTO example VALUES ('retained')")
        db.commit()


def test_keep_three_valid_databases_and_remove_incomplete_copy(tmp_path):
    for day in range(1, 7):
        database(tmp_path / f'backup_202610{day:02d}_040000.db')
    broken = tmp_path / 'backup_20261007_040000.db'
    broken.write_bytes(b'incomplete')
    journal = broken.with_name(broken.name + '-journal'); journal.write_bytes(b'partial')
    plan = prune_databases(tmp_path, apply=False, minimum_age_seconds=0)
    assert len(plan['retained']) == 3 and broken.name in plan['invalid']
    assert len(list(tmp_path.glob('*.db'))) == 7
    prune_databases(tmp_path, apply=True, minimum_age_seconds=0)
    assert len(list(tmp_path.glob('*.db'))) == 3
    assert not broken.exists() and not journal.exists()
    assert not (tmp_path / 'backup_20261001_040000.db').exists()


def test_fewer_than_three_valid_backups_prevents_pruning(tmp_path):
    database(tmp_path / 'backup_20261001.db')
    broken = tmp_path / 'backup_20261002.db'; broken.write_bytes(b'incomplete')
    assert prune_databases(tmp_path, apply=True, minimum_age_seconds=0)['removed'] == []
    assert broken.exists()


def test_code_data_archive_pairs_count_as_one_deployment(tmp_path):
    for day in range(1, 7):
        for kind in ('code', 'data'):
            (tmp_path / f'{kind}_202610{day:02d}_040000.tar.gz').write_bytes(b'archive')
    plan = prune_deployments(tmp_path, apply=True, minimum_age_seconds=0)
    assert len(plan['retained_sets']) == 5
    assert len(plan['removed']) == 2
    assert len(list(tmp_path.glob('*.tar.gz'))) == 10


def test_deletion_stays_in_backup_root_and_preserves_unknown_files(tmp_path):
    root = tmp_path / 'backups'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'data').write_text('keep')
    for day in range(1, 7):
        (root / f'release-202610{day:02d}').mkdir()
    try:
        (root / 'release-20261001' / 'outside-link').symlink_to(outside, target_is_directory=True)
        (root / 'external-20260901').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Creating symlinks requires privileges on this Windows machine.')
    (root / 'notes.txt').write_text('keep')
    prune_deployments(root, apply=True, minimum_age_seconds=0)
    assert (outside / 'data').read_text() == 'keep'
    assert (root / 'external-20260901').is_symlink()
    assert (root / 'notes.txt').exists()
    with pytest.raises(ValueError):
        prune_deployments(root / 'external-20260901', apply=True)


def test_recent_old_set_is_protected_while_it_might_be_written(tmp_path):
    for day in range(1, 7):
        (tmp_path / f'release-202610{day:02d}').mkdir()
    assert prune_deployments(tmp_path, apply=True)['removed'] == []


def test_snapshot_is_published_complete_and_oldest_is_pruned(tmp_path):
    root = tmp_path / 'backups'; root.mkdir()
    for day in range(1, 6):
        database(root / f'backup_202610{day:02d}.db')
    source = tmp_path / 'source.db'; database(source)
    published = snapshot_database(source, root, 'backup_20261006.db')
    with closing(sqlite3.connect(published)) as db:
        assert db.execute('SELECT value FROM example').fetchone()[0] == 'retained'
    assert len(list(root.glob('*.db'))) == 3
    assert not list(root.glob('*.partial'))


def test_failed_snapshot_never_displaces_existing_backups(tmp_path):
    root = tmp_path / 'backups'; root.mkdir()
    source = tmp_path / 'broken.db'; source.write_bytes(b'not a database')
    with pytest.raises(sqlite3.DatabaseError):
        snapshot_database(source, root, 'backup_20261006.db')
    assert list(root.iterdir()) == []


def test_low_disk_space_preserves_existing_backups_and_creates_no_partial(tmp_path, monkeypatch):
    root = tmp_path / 'backups'; root.mkdir()
    source = tmp_path / 'source.db'; database(source)
    for day in range(1, 6):
        database(root / f'backup_202610{day:02d}.db')
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    monkeypatch.setattr('onecpase.backup_retention.shutil.disk_usage', lambda _: SimpleNamespace(free=1))
    with pytest.raises(OSError, match='working-space reserve'):
        snapshot_database(source, root, 'backup_20261006.db')
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


def test_capacity_includes_wal_and_keeps_working_space(tmp_path, monkeypatch):
    source = tmp_path / 'source.db'; source.write_bytes(b'a' * 100)
    source.with_name('source.db-wal').write_bytes(b'b' * 50)
    monkeypatch.setattr('onecpase.backup_retention.shutil.disk_usage', lambda _: SimpleNamespace(free=349))
    with pytest.raises(OSError):
        ensure_backup_capacity(source, tmp_path, reserve_bytes=200)
    monkeypatch.setattr('onecpase.backup_retention.shutil.disk_usage', lambda _: SimpleNamespace(free=350))
    assert ensure_backup_capacity(source, tmp_path, reserve_bytes=200) == 350
