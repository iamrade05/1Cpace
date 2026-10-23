# Backup retention

The user selected **five backups per type** on 18 October 2026:

- Five valid scheduled database snapshots in `/opt/onecpase/backups`.
- Five dated deployment rollback sets in `/opt/onecpase-backups`. Code/data archives with the same timestamp count as one set; a rollback directory can contain several related files.

`onecpase/backup_retention.py` enforces this policy. Scheduled database snapshots are written to a temporary file, checked with SQLite `quick_check`, and published atomically before older backups are pruned. Failed snapshots do not replace complete ones. A database with a nonempty rollback journal is not counted as a complete backup. Pruning requires five valid snapshots before removing anything in that category.

The deployment tool invokes the same policy after completing its backup. The `onecpase-backup-retention.timer` systemd timer also enforces it hourly for ad hoc backups. Its fifteen-minute grace period protects backups that may still be written; a sixth in-progress/recent set can temporarily remain until completion or the next check. Unrecognized files and top-level symlinks are preserved. Deletion targets must resolve inside the explicitly selected backup directory.

The initial cleanup was previewed before applying, including verification of the retained database snapshots. Today's incomplete scheduled snapshot from the disk-full incident is excluded from the retained five. The current mobile API deployment rollback set remains among the five newest sets.

Live cleanup verified exactly five database files and five deployment directories, with about **13.8 GB free**. The staged Linux retention/scheduler/mobile regression gate passed **26 tests**. Local retention tests passed six tests, with the symlink test skipped because Windows lacks the required privilege; the Linux gate exercised that test. The service allows up to fifteen minutes for integrity checks on large snapshots.

Maintenance artifacts, tests, preview and live results: `artifacts/backup-retention/`. Units are installed in `/etc/systemd/system/`; the prior scheduler is backed up within the retained current deployment set under `retention-before/`. The legacy `/opt/cpace` backup timer belongs to the older application and is outside this policy's two 1Cpace backup locations.

```sh
systemctl status onecpase-backup-retention.timer
journalctl -u onecpase-backup-retention.service
/opt/onecpase/venv/bin/python -I /opt/onecpase/onecpase/backup_retention.py \
  --database-dir /opt/onecpase/backups \
  --deployment-dir /opt/onecpase-backups
```

The last command previews changes; `--apply` performs them. Database counts and deployment-set counts are different from the number of individual files within rollback sets.
