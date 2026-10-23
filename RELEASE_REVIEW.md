# Full application release review — 9 October 2026

## Security configuration update — 13 October 2026

The shared production/beta service now runs in production mode with shared
Valkey rate-limit storage, Secure session cookies, 300-second HSTS without
subdomain coverage, and CSP in report-only mode. The original production
configuration findings below are historical for these specific settings.
Meta remains unconfigured; the app secret is required when a Meta integration
is configured, and unsigned POST webhooks still fail closed. The limiter's URL
setting is now correctly mapped to Flask-Limiter's STORAGE_URI setting.
Public checks and shared-counter verification passed; session/encryption keys
and checked client row counts were preserved. See
`artifacts/production-security-summary.md` and
`artifacts/production-security-verification.json` for validation, backups,
rollback and remaining acceptance work.

## Decision

Target: all modules (`ACTIVE_PHASE=all`). Local fixes and automated validation
are complete for the locally verifiable scope. A full production release still requires the target
configuration and external acceptance below. The initial review made no server
changes; the subsequently authorized phase 3 beta deployment is recorded below.

## Changes completed

| Finding | Resolution |
|---|---|
| Field encryption ignored the Flask app key and used the shell key | Encryption, decryption, lookup hashes, and startup validation now use the same app key. Missing/invalid keys fail closed. Regression tests cover differing shell/app keys. |
| Concurrent Itensity pushes replaced shared callbacks before taking the lock | Both member and payment adapters now take the lock before replacing callbacks. A two-thread test verifies each member receives its own reference and no write reaches the sibling database callback. |
| Release commit lost the outcome-based contact metric | Restored completed contact outcomes as clients reached. PBX-confirmed calls remain a separate metric in reports, queue, and dashboard. |
| Health endpoint only checked whether a file could be opened | Checks the database schema using a read-only SQLite connection or the configured PostgreSQL connection; missing/corrupt databases return 503 without exposing connection details. |
| Startup left database handles open on Windows | Register connection cleanup before the startup context exits. A regression test confirms all startup connections close; migration copies can then be removed. |
| Deployment could proceed without tests and report success after a failed health check | Deployment runs the required pytest suite before opening a deployment connection. Public health failures return a failing exit code. |
| Tests depended on developer .env and some uploads/default databases were not isolated | Disable .env before importing configuration and isolate default database, tenant registry, and upload paths. |
| Old tests conflicted with current Sales/NuPay workflow | Supply document/mandate evidence, require persisted activation, check member-ID references and encrypted storage, verify explicit admin overrides, and use current sales labels/funnel order. Business gates were retained. |
| Call-queue fixtures treated reminder messages as calls and pending charges as confirmed failures | Seed confirmed failures, keep reminder records separate, and freeze the queue clock. |
| Fitness Classes overflowed the phone viewport | Wrap filters/actions and migrate its inline styles into shared components. Lowered the styling regression baseline. |
| Browser checks recorded failures without failing the command | Browser verification now fails on non-200 pages, document overflow, or JavaScript errors, and covers 20 screens at two widths. |
| Setup/release instructions were stale | Added encryption, OTP email, and rate-limit configuration to the example; corrected Itensity setup and beta's observed configuration. |

## Validation

- Full suite: 776 passed, zero failures (503 warnings), in 770.68 seconds.
  Tests use all phases, disposable SQLite databases, isolated uploads,
  and mocked provider calls. See `artifacts/release-tests-final.xml` and
  `artifacts/release-tests-final.txt` for the final full-suite result.
- The startup cleanup fix landed after the full run began. Post-fix checks:
  29 tenant/platform/phase tests and one startup connection regression passed;
  the existing-database migration rehearsal also passed.
- CI now includes browser checks and Python 3.12/3.13; remote CI has not run.
- Focused Sales/member/NuPay/report checks: 53 passed. Focused queue/decision,
  deployment gate, NuPay encryption, and concurrency checks: 59 passed.
- Browser checks: 20 pages at 1366px and 390px (40 renders), all HTTP 200,
  no document overflow, no JavaScript errors. Event step visibility and
  confirmation Cancel/Confirm behavior also pass.
- `pip check`, Python compilation, and deployment file validation pass.
- Deployment inventory contains 82 Python files and 235 total files, with
  secrets, databases, uploads, and local state excluded.
- Local upgrade rehearsal: two database snapshots passed integrity checks and
  preserved operational row counts; originals were opened read-only. The
  rehearsal exposed and verified the startup connection cleanup fix.

Browser evidence: `artifacts/ui-review/results.json` and corresponding PNGs.
This checks primary screens, not every data combination, device, or external
integration. PostgreSQL failure handling is mocked; the full suite uses SQLite.

## Remaining full-release blockers and acceptance

| Area | Evidence / unfinished work | Required completion |
|---|---|---|
| Production security | Both local and beta configuration lack shared limiter storage and `FB_APP_SECRET`; `APP_ENV` is unset. | Configure production mode, real existing encryption/session secrets, Meta app secret, Redis/Valkey storage, and the actual trusted proxy count; verify HTTPS/OTP sign-in and startup. Preserve the existing encryption key during upgrades. |
| All modules on target | Local and beta `ACTIVE_PHASE=2`; browser/tests use `all`. | Set target phase to `all` after acceptance and verify staff-role access to Queries, Fitness, Reports, HR, and Operations as well as Sales/Collections. |
| Itensity member/payment push | Both drivers exist at the locally configured path. Beta has no explicit `RADEPULSE_PATH` and both drivers are absent at its default `/opt/radepulse` path. Existing-member updates remain unsupported by the current adapter. | Install/verify target drivers and credentials, run approved provider acceptance, and retain manual Itensity editing for existing members until an edit driver exists. |
| Imports/reconciliation | Source imports and roster reconciliation have tests; live completeness is not established by them. | Reconcile the live roster and balances against provider totals, verify scheduler imports on the target, and test missed-job recovery and backups. |
| Communications | Local/beta WhatsApp and Facebook send tokens are unset. SMS provider sending remains an external gap in the rule-workflow documents. | Configure approved provider accounts and verify message/webhook delivery. Supply an SMS transport/provider if automatic SMS escalation is required. |
| Turnstile | Physical output is disabled and the vendor OPEN/DENY protocol is unconfirmed. Logical access decisions are tested. | Obtain the vendor command protocol, implement its output adapter, and complete hardware acceptance. Do not describe logical decisions as working physical gate control. |
| Deployment/restore | Beta is active and `/healthz` returns 200; current source changes have not been deployed. | Take a consistent data backup, rehearse restore with the unchanged encryption key, run target acceptance and public health after deployment. |

Read-only beta evidence is in `artifacts/release-beta-check.txt` and
`artifacts/release-beta-config-check.txt`. Provider settings were checked only
for presence; no secret values are recorded. Beta has NuPay credentials and
the scheduler enabled, contrary to its older documentation. No live mandate,
payment, message, or turnstile action was initiated by this review.
The read-only dependency check confirms Valkey is active and the two default
Itensity driver files are absent (`artifacts/release-beta-dependency-check.txt`).

## Remaining UI polish

The existing wider component migration remains unfinished: 136 templates have
2,120 inline-style occurrences, 16 page style blocks, 1,480 hex-colour occurrences,
and seven emoji/code-point occurrences (the printable contract exception).
The styling baseline prevents increases. This review fixes the demonstrated
Fitness overflow but does not certify completion of the earlier design plan.

See `UI_STANDARD_REVIEW.md` for the page-by-page migration scope. Further UI
polish is separate from the external and deployment blockers above.

## Reproduce

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q --basetemp "$env:TEMP\onecpase-release-check"
python -m scripts.verify_ui
python -m scripts.verify_migrations
python -m pip check
python -m compileall -q onecpase scripts deploy_beta.py
python deploy_beta.py --check-only
```

`--check-only` reads beta status and configuration phase; it does not deploy.
Actual deployment requires the passing test gate. Production secrets and the
vendor protocol must come from their real owners; substitutes cannot establish
full-release readiness.

## Authorized phase 3 beta deployment — 9 October 2026

The current application was deployed to beta after the complete current suite
passed: 777 tests, zero failures (504 warnings), in 801.89 seconds. The service
was stopped for consistent code/configuration and data backups; both archives
passed listing verification before deployment. Existing secrets were preserved.
Only `ACTIVE_PHASE` changed to `3`. The restarted service is active, its process
environment confirms phase 3, and the public health endpoint returned HTTP 200
with `status=ok`. Sales, Collections, and Queries are enabled.

Evidence: `artifacts/deployment-phase3.txt` and
`artifacts/deployment-phase3-result.json`. Pre-deployment backup prefix: `/opt/onecpase-backups/phase3_predeploy_20261009_060456`.

This beta deployment does not complete the full-release blockers above. The
phase-2 beta configuration observations in that earlier review are historical;
beta now runs phase 3. Real provider/hardware acceptance and a restore exercise
remain outstanding.

## Production promotion verification — 9 October 2026

On the subsequently authorized production promotion, read-only inspection found
that both `1cpace.co.za` (including www) and `beta.1cpace.co.za` already proxy
to the same `onecpase` service on port 5002. The legacy `cpace` service is inactive.
Consequently, the preceding beta deployment also updated the application served
on the production hostname. These domains do not currently provide independent
release environments. No further deployment or routing change was required.

All 235 deployed release files match the tested local release. The running
service has `ACTIVE_PHASE=3`; all three public health URLs return HTTP 200
with `status=ok`. nginx configuration validation passes. The production `/login`
request follows a redirect to `/` and returns HTTP 200. This does not verify OTP
or a complete authenticated workflow. Evidence:
`artifacts/production-promotion-check.json`. The full-release limitations above
remain outstanding.

## Staff notification deployment — 9 October 2026

The separately authorized notification changes were deployed to the shared
production/beta service after 797 tests passed (zero failures; 524 warnings).
A stopped-service code/configuration and data backup was verified before the
swap. Existing server settings and phase 3 were preserved. All 237 deployed
files match this tested release. Production, www, and beta health endpoints
return HTTP 200 with `status=ok`; nginx configuration validation passes.
The live notification table and supervisor column are present, and the live
SQLite quick check passes. Fifteen active staff accounts still need their own
supervisors configured in Admin → Users. No reporting relationships were guessed.

Details: `NOTIFICATIONS.md`. Evidence: `artifacts/deployment-notifications.txt`,
`artifacts/deployment-notifications-result.json`,
`artifacts/notifications-public-check.json`, and
`artifacts/notifications-live-schema-check.json`. Backup prefix: `/opt/onecpase-backups/notifications_predeploy_20261009_065317`.

The existing full-release/provider/hardware limitations remain outstanding.


## HR access and Phase 4 ? 11 October 2026 (local changes)

HR is available to all signed-in staff in every phase. Staff records, leave,
employee records and timesheets use the shared non-payroll HR access policy.
Staff login-account administration continues to use user-management permissions.
Payroll routes, payroll payment actions, salary form fields and salary updates
use the same named-account gate for Mvuleni and Ayrton. Accounts are matched by
their supplied staff email, or a unique active staff name/username. Other admins
and old payroll grants cannot bypass it; inactive and platform accounts are denied.

Local ACTIVE_PHASE and the example/default configuration are now 4, enabling
Fitness and onboarding alongside Sales, Collections and Queries. HR is always
available; Reports and Operations retain their later phase gates. The live server
has not been promoted: the read-only deployment preflight failed after three SSH
connection timeouts. No remote code or phase setting changed during this attempt.
