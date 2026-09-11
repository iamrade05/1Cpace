# 1Cpace V8 — beta instance

Beta is already provisioned and running. Releasing to it is one command:

```bash
python deploy_beta.py --check-only   # report only, changes nothing
python deploy_beta.py                # prompts before replacing anything
```

## What is where

`102.211.207.233` is **AlmaLinux 10.2**, Python 3.12, and hosts **three
unrelated applications**. Getting these confused is the main hazard.

| | Production 1Cpace | FiksAccounts | **Beta (this app)** |
|---|---|---|---|
| Directory | `/opt/cpace` | `/opt/fiksaccounts` | **`/opt/onecpase`** |
| Port | 5000 | 5001 | **5002** |
| Service | `cpace` | `fiksaccounts` | **`onecpase`** |
| User | `cpace` | `fiks` | **`onecpase`** |
| Hostname | `1cpace.co.za` | — | **`beta.1cpace.co.za`** |

Beta's data lives **outside** the app directory, which is why a release can
replace `/opt/onecpase` safely:

```
/var/data/onecpase/1cpase.db       tenant database
/var/data/onecpase/platform.db     tenant registry
/var/data/onecpase/tenants/        provisioned tenant databases
/var/data/onecpase/uploads/        member documents
```

nginx and TLS are **already set up** — `/etc/nginx/conf.d/beta.1cpace.co.za.conf`
proxies to `127.0.0.1:5002` with a Let's Encrypt certificate and an HTTP-to-HTTPS
redirect, both managed by certbot. Do not hand-edit the certbot-managed lines.
Redis is already running as **valkey** on `127.0.0.1:6379`.

## What a release does

`deploy_beta.py` ships `onecpase/`, `scripts/`, `run.py`, `gunicorn.conf.py`,
`requirements.txt` and `seed_platform_admin.py`. It:

1. compiles every Python file locally and refuses to ship a `.env` or database;
2. backs up the current tree **and** `/var/data/onecpase` into `/opt/onecpase-backups/`;
3. stages the new tree, then **replaces the `onecpase` package outright** so
   modules deleted upstream don't linger — the old one is kept as
   `onecpase.previous`;
4. installs `requirements.txt`, reinstalls the unit, restarts, and reports
   `journalctl` if the service doesn't come back.

It refuses any target that isn't `/opt/onecpase`.

## Configuration

`/opt/onecpase/.env` is the one file that exists only on the server — the
deploy never writes it. Current keys: `SECRET_KEY`, `ENCRYPTION_KEY`,
`DATABASE_PATH`, `PLATFORM_DATABASE_PATH`, `TENANTS_DIR`, `UPLOAD_FOLDER`,
`SCHEDULER_ENABLED=0`, `TURNSTILE_ENABLED`, `ACTIVE_PHASE`.

**`ACTIVE_PHASE`** decides what is reachable — see `onecpase/phases.py`. A
closed phase is hidden from the nav *and* refused by URL.
`1`=Sales `2`=Collections+PTP `3`=Queries `4`=Fitness `5`=Reports `6`=HR
`7`=Operations, or `all`. Unset defaults to `1`.

### Things beta is deliberately without

- **`APP_ENV` is unset**, so the app runs in development mode. Setting
  `APP_ENV=production` turns on HSTS, CSP and `SESSION_COOKIE_SECURE`, but it
  also **fails closed** — it will refuse to boot without `PBX_WEBHOOK_SECRET`
  and a non-`memory://` `RATELIMIT_STORAGE_URL` (valkey is already there:
  `redis://localhost:6379/1`). Worth doing; it needs those two keys added in
  the same edit or beta stops.
- **NuPay and Itensity credentials are absent.** A beta carrying live NuPay
  credentials creates **real mandates** on the merchant portal the moment
  someone clicks through DebiCheck. Leave them out.
- **`pyodbc` is not installed**, and should not be. Beta has no route to the
  Elev8 SQL Server, so `get_elev8_member_count()` returns `None` instantly and
  the member count falls back to local data. With pyodbc installed but no
  route, every dashboard and members-list render would block for its
  8-second connect timeout instead. See `requirements-optional.txt`.
- **`SCHEDULER_ENABLED=0`**, so the nightly Itensity jobs don't run here.

## Operating it

```bash
systemctl status onecpase
journalctl -u onecpase -f
curl -s localhost:5002/healthz
```

**Do not raise `workers` in `gunicorn.conf.py`.** APScheduler runs inside the
web process and its only guard does nothing under gunicorn, so two workers
means every nightly job runs twice. Dormant while `SCHEDULER_ENABLED=0`; a
live trap the moment that changes. `gunicorn.conf.py` explains the way out.

## Rolling back

```bash
ls -la /opt/onecpase-backups/                       # timestamped tarballs
rm -rf /opt/onecpase/onecpase
mv /opt/onecpase/onecpase.previous /opt/onecpase/onecpase
systemctl restart onecpase
```
