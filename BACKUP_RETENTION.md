# Backup retention

The user selected **five backups per type** on 18 October 2026:

- Five valid scheduled database snapshots in `/opt/onecpase/backups`.
- Five dated deployment rollback sets in `/opt/onecpase-backups`. Code/data archives with the same timestamp count as one set; a rollback directory can contain several related files.

`onecpase/backup_retention.py` enforces this policy. Scheduled database snapshots are written to a temporary file, checked with SQLite `quick_check`, and published atomically before older backups are pruned. Failed snapshots do not replace complete ones. A database with a nonempty rollback journal is not counted as a complete backup. Pruning requires five valid snapshots before removing anything in that category.

The deployment tool invokes the same policy after completing its backup. The `onecpase-backup-retention.timer` systemd timer also enforces it hourly for ad hoc backups. Its fifteen-minute grace period protects backups that may still be written; a sixth in-progress/recent set can temporarily remain until completion or the next check. Unrecognized files and top-level symlinks are preserved. Deletion targets must resolve inside the explicitly selected backup directory.

The initial cleanup was previewed before applying, including verification of the retained database snapshots. Today's incomplete scheduled snapshot from the disk-full incident is excluded from the retained five. The current mobile API deployment rollback set remains among the five newest sets.

The initial cleanup on 18 October 2026 verified exactly five database files and five deployment directories, with about **13.8 GB free** at that time. The staged Linux retention/scheduler/mobile regression gate passed **26 tests**. Local retention tests passed six tests, with the symlink test skipped because Windows lacks the required privilege; the Linux gate exercised that test. The service allows up to fifteen minutes for integrity checks on large snapshots.

Maintenance artifacts, tests, preview and live results: `artifacts/backup-retention/`. Units are installed in `/etc/systemd/system/`; the prior scheduler is backed up within the retained current deployment set under `retention-before/`. The legacy `/opt/cpace` backup timer belongs to the older application and was disabled on 25 October 2026; it is separate from this policy's two 1Cpace backup locations.

On 25 October, the current policy was confirmed active. Disk pressure came from large retained snapshots plus several deployments before the next hourly cleanup, with disposable rehearsal copies outside the policy. Maintenance returned deployment sets to five, preserved the five scheduled database snapshots, and removed only the known disposable `rehearsal` and `database-copies` subdirectories from this session's releases. Release manifests, incoming source, results and the five current rollback sets were preserved. Free disk space increased to approximately 6.25 GB.

Automated database snapshots now check capacity before creating a partial file, allowing for the database, its WAL and a 512 MiB working-space reserve. The scoped release helper also checks aggregate backup capacity before stopping the app, removes failed partial database copies, runs deployment retention after a healthy release, and preserves the healthy app if subsequent retention reports an error. The current scoped rehearsal wrappers clean their disposable copies on success or failure. These guards prevent a new backup from starting when its estimated size would consume the working-space reserve; they do not change the selected five-per-type policy or introduce a new storage-size limit.

The unused legacy Celery worker and failing legacy backup timer were disabled after the worker reported zero active, reserved, scheduled, queued and unacknowledged tasks. Current 1Cpace, Nginx, Valkey, FiksAccounts and the current retention timer remain active. Evidence: `artifacts/server-cleanup-20261025/`; 12 Linux safety checks passed, including the symlink test (11 passed and one skipped on Windows). The previous retention module is preserved under the latest dashboard rollback set's `maintenance-before/` directory.

```sh
systemctl status onecpase-backup-retention.timer
journalctl -u onecpase-backup-retention.service
/opt/onecpase/venv/bin/python -I /opt/onecpase/onecpase/backup_retention.py \
  --database-dir /opt/onecpase/backups \
  --deployment-dir /opt/onecpase-backups
```

The last command previews changes; `--apply` performs them. Database counts and deployment-set counts are different from the number of individual files within rollback sets.
