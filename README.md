# 1Cpase

A lightweight Flask scaffold implementing a cleaner structure for future migration from `radepulse`.

## Structure

- `onecpase/` — application package
- `onecpase/auth.py` — auth blueprint
- `onecpase/database.py` — DB helpers
- `onecpase/config.py` — configuration
- `onecpase/extensions.py` — extension registration
- `templates/auth/` — auth templates
- `run.py` — dev server runner

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Then open `http://127.0.0.1:5000`.

## Power User and tenant access

The platform Power User signs in at `/platform/login`. This account can manage
the overall 1Cpase installation and use **Open gym** to enter any active tenant
without knowing that tenant's administrator password. A visible banner marks
the temporary tenant session and returns the user to the platform.

Create the first Power User from PowerShell:

```powershell
$env:PLATFORM_ADMIN_USERNAME='owner'
$env:PLATFORM_ADMIN_PASSWORD='use-a-strong-unique-password'
python seed_platform_admin.py
```

To set a new password for an existing Power User, use the same environment
variables and run `python seed_platform_admin.py --reset`.

Normal tenant users cannot access `/platform`, switch into other tenants, or
sign in as the internal identity used during Power User tenant access. Platform
logins, tenant entry, and tenant exit are recorded in `platform_audit_log`.

## Itensity member push

Copy `.env.example` to `.env` and set `ITENSITY_API_URL` and
`ITENSITY_API_KEY`. Once an application passes its compliance checklist,
**Push Application** sends the linked member to Itensity and stores the returned
member reference. A successful push activates an Unverified member and marks the
application Active. Failed pushes remain in **Ready to Push** so they can be retried.
