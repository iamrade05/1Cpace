# 1Cpace V8 — beta instance

Beta runs as a second, independent instance on the same VPS as production
1Cpace. Production is a **different application** (the Flask monolith in
`/opt/cpace`, served at `1cpace.co.za`). Nothing here touches it.

| | Production | Beta |
|---|---|---|
| App | 1Cpace monolith | 1Cpace **V8** (`onecpase` package) |
| Directory | `/opt/cpace` | `/opt/cpace-beta` |
| Port | 5000 | 5001 |
| Service | `cpace` | `cpace-beta` |
| Hostname | `1cpace.co.za` | `beta.1cpace.co.za` |
| Databases | `/var/data/cpace/` | `/var/data/cpace-beta/` |

Once the one-time setup below is done, every later release is just
`python deploy_beta.py` from the repository root.

---

## One-time server setup

Run as root on the VPS.

### 1. Directories

The `cpace` user already exists (production runs as it).

```bash
mkdir -p /opt/cpace-beta /var/data/cpace-beta /opt/cpace-beta/uploads /opt/cpace-beta/logs
chown -R cpace:cpace /opt/cpace-beta /var/data/cpace-beta
```

### 2. Virtualenv

```bash
python3 -m venv /opt/cpace-beta/venv
/opt/cpace-beta/venv/bin/pip install --upgrade pip wheel
```

`deploy_beta.py` installs `requirements.txt` into this venv on every run.

### 2b. Redis

Rate-limit storage. Production mode refuses to start on in-memory storage,
because a restart would otherwise wipe every limit counter.

```bash
apt-get install -y redis-server
systemctl enable --now redis-server
redis-cli ping        # expect: PONG
```

If production 1Cpace already uses this Redis, beta's `db 1` keeps the two
apart; check with `redis-cli info keyspace`.

### 3. Configuration

Beta's `.env` lives at `/opt/cpace-beta/.env` and is **never** written by the
deploy script — it is the one file that only exists on the server.

```bash
# Generate the three secrets. They must NOT be copied from production.
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python3 -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
python3 -c "import secrets; print('PBX_WEBHOOK_SECRET=' + secrets.token_urlsafe(32))"
```

`APP_ENV=production` is not cosmetic: it turns on `SESSION_COOKIE_SECURE`,
HSTS and CSP, and it makes `create_app()` **fail closed** — it refuses to
start without `SECRET_KEY`, `ENCRYPTION_KEY`, `PBX_WEBHOOK_SECRET` and a
non-`memory://` rate-limit store. All four are therefore mandatory below.

```ini
APP_ENV=production
SECRET_KEY=<generated above>
ENCRYPTION_KEY=<generated above>
PBX_WEBHOOK_SECRET=<generated above>

# Rate-limit storage. memory:// is refused in production mode; db 1 keeps
# beta clear of anything else using this Redis.
RATELIMIT_STORAGE_URL=redis://localhost:6379/1
# One trusted proxy: nginx. At 0 every request looks like it comes from
# 127.0.0.1 and the per-client rate limits become one shared global limit.
RATELIMIT_TRUSTED_PROXY_COUNT=1

DATABASE_PATH=/var/data/cpace-beta/1cpase.db
PLATFORM_DATABASE_PATH=/var/data/cpace-beta/platform.db
TENANTS_DIR=/var/data/cpace-beta/tenants
UPLOAD_FOLDER=/opt/cpace-beta/uploads

# Which areas are reachable — see onecpase/phases.py. 1 = Sales only.
ACTIVE_PHASE=1

# Leave the integrations inert until someone decides otherwise. A beta
# carrying live NuPay credentials will create REAL mandates on the merchant
# portal the moment a tester clicks through DebiCheck.
NUPAY_NEW_EMAIL=
NUPAY_NEW_PASSWORD=
NUPAY_TOTP_SECRET=
ITENSITY_USERNAME=
ITENSITY_PASSWORD=

# The scheduler fires the nightly Itensity jobs. Off until those are wanted.
SCHEDULER_ENABLED=0
```

> **On `ENCRYPTION_KEY`:** a fresh key is correct for a beta starting from an
> empty or scrubbed database. If beta is ever loaded with a copy of production
> data, it needs *production's* key or every encrypted ID number becomes
> unreadable. Decide which before importing anything.

Then lock it down:

```bash
chown cpace:cpace /opt/cpace-beta/.env && chmod 600 /opt/cpace-beta/.env
```

### 4. First release

From the repository root on your machine:

```bash
python -m pip install paramiko     # once
python deploy_beta.py --check-only # confirms access, changes nothing
python deploy_beta.py
```

### 5. Web server and TLS

```bash
cp /opt/cpace-beta/deploy/beta/nginx-beta.conf /etc/nginx/sites-available/beta.1cpace.co.za
ln -s /etc/nginx/sites-available/beta.1cpace.co.za /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d beta.1cpace.co.za
```

`beta.1cpace.co.za` already resolves to this host, so certbot's HTTP challenge
will pass without a DNS change.

### 6. Power User

The account that provisions tenants. It cannot be created from any screen.

```bash
cd /opt/cpace-beta
sudo -u cpace PLATFORM_ADMIN_USERNAME=owner PLATFORM_ADMIN_PASSWORD='<strong password>' \
  venv/bin/python seed_platform_admin.py
```

### 7. Browser automation (only if NuPay/Itensity are enabled)

Skipped by default — the drivers are inert while the credentials above are
blank, and Chromium is a large install.

```bash
sudo -u cpace /opt/cpace-beta/venv/bin/playwright install --with-deps chromium
```

---

## Operating it

```bash
systemctl status cpace-beta
journalctl -u cpace-beta -f
systemctl restart cpace-beta
curl -s localhost:5001/healthz
```

**Do not raise `workers` in `gunicorn.conf.py`.** The APScheduler jobs run
inside the web process and its only guard does nothing under gunicorn, so two
workers means every nightly job runs twice. The file explains the way out.
