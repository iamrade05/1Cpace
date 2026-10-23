# 1Cpase

A Flask gym-management application covering Sales, Collections/PTP, Queries,
Fitness, Reports, HR, Operations, tenant administration, and external integrations.

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

Copy `.env.example` to `.env` and configure `RADEPULSE_PATH` to the installed
Itensity browser drivers and their provider credentials. The current push uses
`push_to_itensity.py`, rather than the legacy API settings. Once an application passes its evidence gates,
**Push Application** sends the linked member to Itensity and stores the returned
member reference. A successful push activates an Unverified member and marks the
application Active. Failed pushes remain in **Ready to Push** so they can be retried.

## Staff training

Signed-in staff can open **Staff Training** in the sidebar at `/training`.
Seven searchable topics cover Sales, Collections, PTP, DebiCheck, Queries,
notifications and practice checklists. The complete screenshot manual is
available as a PDF and Word download. Resources require the normal tenant
session and are included in the application package, outside public static files.
Training is available in every release phase; it does not grant access to
the business actions described in the manual.

The reviewed edition is in `onecpase/resources/training/`. After revising
`artifacts/training-manual/expanded-content.md`, run
`python -m scripts.build_training_content` and replace the PDF and DOCX with
the matching reviewed edition. Verify with `python -m pytest tests/test_training.py`
and `python -m scripts.verify_training_ui` (disposable preview data).

## Release validation

Install `requirements-dev.txt`, then run `python -m pytest -q`. Run
`python -m scripts.verify_ui` for browser checks against disposable data.
The deployment script requires passing tests before it connects to deploy.

For a full release use `ACTIVE_PHASE=all` on the target, with production
security settings, provider configuration, and hardware acceptance completed.
See `RELEASE_REVIEW.md` for the current evidence and outstanding release work.
