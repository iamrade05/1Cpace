# HR implementation and operating guide

Deployed to production on 16 October 2026. The scoped release installed 23 reviewed files and created 18 employee identity profiles. Employment terms and opening leave balances remain unconfirmed; no leave, attendance, timesheet or payroll history was fabricated.

Production entry: [My HR](https://www.1cpace.co.za/hr/me). The service health check passed, as did 18 authenticated HR page checks against a protected copy of the upgraded production database and ordinary-staff permission-denial checks. Public HR URLs require sign-in. The release gate passed 96 tests.

Evidence: `artifacts/hr-release/deployment.json`, `artifacts/hr-release/verification.json`, `artifacts/hr-release-tests.xml`. Database/code backup and restore-check location: `/opt/onecpase-backups/hr-workforce-20261016T163805Z`. Existing operational row counts were preserved: 19 users, 2,116 members, 28 DebiCheck mandates, and unchanged leave/attendance/timesheet/payroll counts.

## What is available

- **My HR** (`/hr/me`): own balance cards, taken leave, future reservations, requests, published shifts and saved weekly timesheets.
- **Employee setup** (`/hr/people`): explicit employment start date, normal working days, reporting manager and written meal-break agreement reference. Profiles link login accounts to the existing employee register without repurposing external access IDs.
- **Balance verification**: staged opening accounts and adjustments, independently confirmed by another HR administrator. Unknown balances remain visibly unconfirmed. Old cycles remain visible; nothing is automatically forfeited.
- **Leave**: authenticated employee declaration, server-calculated working days, date/overlap checks, approval permissions, self-approval prevention, entitlement reservations, withdrawals and HR cancellation of approved future leave. Taken quantities are derived from approved date-level snapshots as dates pass; repeated reads cannot create duplicate deductions.
- **Availability** (`/hr/leave/calendar`): all staff availability without private leave reasons. **Review queue** (`/hr/reviews`): pending decisions and employee-by-employee weekly submission status.
- **Roster/lunch** (`/hr/roster`): draft shifts, atomic whole-week publication, staggered departmental lunches, available relief, daily-rest checks, opening-hours checks and ordinary-hours limits. Management/General act as floating cover; an operational relief employee must have their own department covered.
- **Weekly timesheets** (`/hr/weeks`): actual declared work/unpaid breaks, previous manual entries as unsigned drafts, employee signature, manager approval/return, locked approved versions, audited reopening and retained signed snapshots. Browser Print/Save as PDF provides a receipt.
- **Access evidence**: the selected employee's observations can appear beside their timesheet. The original whole-team access preview is restricted to HR administrators. No scan generates clock-out, lunch or paid work automatically.
- **Notifications**: transactional in-app leave/timesheet review and decision notices. No email, SMS or external messages are sent.
- **Privacy/security**: own/assigned-team scope, fresh database permission checks for decisions, CSRF, optimistic versions, serialized balance/coverage decisions and an audit trail. Payroll remains restricted to the existing named staff policy.

## Setup before staff use

1. Start the updated application; the tenant database initializer adds the `hr_*` tables. Back up and rehearse migrations on a database copy before production deployment.
2. An existing administrator, Mvuleni or Ayrton opens **My HR → Employee setup**. Explicitly assigned `hr_manage` also grants HR administration; do not grant it to general staff.
3. Review linked identities for every active human employee. Exact unique email matches can link existing register entries; ambiguous matches are left unlinked rather than guessed. Platform-operator accounts and the generic `admin` account without an employee number are excluded from employee setup. The generic administrator opens HR administration rather than a personal leave account.
4. Confirm employee versus contractor arrangements, actual start dates and normal days; do not infer these from card scans. Select reporting managers. Contractors are not automatically allocated employee statutory banks.
5. Grant `hr_leave_approve` and/or `hr_timesheet_approve` to reporting managers in user administration. A permission alone is insufficient: the employee must also report to that manager. HR administrators have wider review scope, but cannot approve their own leave or timesheets.
6. Reconcile opening leave against existing signed records. Stage each relevant account and have another authorised HR administrator confirm it. Enter remaining balance and historical taken days separately. A first-six-month sick account requires verified actual days worked.
7. Record reduced-lunch written agreements where applicable. Default interval is 60 minutes; 30 requires a reference.
8. Build the complete draft rota, including available qualified relief, then publish the week. Staff see published shifts; draft changes stay with HR. Revise a future published week through the audited return-to-draft action.
9. Employees request their own leave and submit weekly timesheets after each week ends. Managers use the review queue and team calendar to review submissions.

## Policy behaviour and deliberate limits

- Owner-confirmed company enhancement: **21 working days of annual leave per year for everyone**, earning **1.75 days per complete employment month**. This overrides the statutory-baseline annual calculation described below for this tenant. It changes entitlement and accrual rates; it does not certify unverified historical usage or turn the full yearly entitlement into an available opening balance. The company setting is stored in `hr_policy_settings`; other tenants retain their own baseline unless configured separately.
- This enhancement is deployed and verified: 39 automated checks and 18 authenticated page checks passed. Confirmed starts: Thato **1 August 2026**, Thokozani **1 January 2026**, Busi **1 September 2026**. Evidence: `artifacts/hr-annual21/result.json` and `verified.json`. The pre-change database/code backup is `/opt/onecpase-backups/hr-annual21-20261016T173836Z`. Disposable verification database copies were removed after checking; the production database and original pre-change backups were retained.

- Baseline annual entitlement is three normal working weeks: 15 days for five-day staff, 18 for six-day staff. After a verified opening balance, monthly accounting earns **1.25 days for five-day staff or 1.5 for six-day staff** at the end of each complete employment month. The deterministic calculation avoids duplicate accrual jobs. Future earnings cannot be spent in advance.
- Sick leave is a 36-month cycle. The first-six-month entitlement is supported through verified worked-day opening credits and independently reviewed additional credits. At six months, a first-cycle account becomes the full verified normal six-week entitlement without resetting historical usage.
- Family responsibility eligibility checks more than four months' service and at least four normal working days weekly.
- Annual leave excludes the standard South African statutory holidays, including Sunday substitution and Easter-derived dates, and the [gazetted 4 November 2026 election holiday](https://www.gov.za/news/media-statements/president-cyril-ramaphosa-declares-election-day). Other additional gazetted holidays must be supplied in `HR_EXTRA_HOLIDAYS` application configuration as ISO date strings. Check current proclamations before deployment.
- The request must fit one confirmed cycle. Cross-cycle requests must be split; a new cycle needs HR verification. Old balances remain visible for reconciliation rather than being silently transferred, forfeited or reset. Opening imports must not include future reservations that will also be requested in the new workflow.
- Parental and study leave require a recorded HR entitlement/payment review. The UI does not claim an automatically calculated pooled parental balance. UIF processing is outside this release.
- Enhanced contractual benefits are not silently overwritten by the basic defaults. Use reviewed adjustments for verified credits; complex policy changes require further configuration work. First-six-month sick credits remain capped by verified worked days.
- Leave is full working days. Partial days, overnight shifts, automated contractual overtime exceptions, medical attachment uploads, electronic-signature provider integration, scheduled reminder escalation, and payroll export are not enabled in this release.
- Planned annual leave normally requires no department conflict and a published replacement covering the shift. HR can explicitly record a cover arrangement to override; this is visible in the audit history. Qualifying statutory requests remain recordable during shortages. HR must review entitlement and arrange operational relief.
- Coverage minimum is one operational employee per department/interval, with synonymous Cleaner/Cleaners and Training/Fitness department labels normalised. Configurable multi-person/skill minimums from the broader plan remain future work; named cross-department relief is a manager attestation of qualification.
- Weekly hours and work at night generate review checks. Roster publication uses the normal ordinary-hours/rest limits and does not provide an unchecked exemption override. Exceptional schedules require reviewed policy support before publication.
- A roster entry may override a normal nonworking day when calculating leave. Explicit per-day rest exceptions beyond the normal pattern are not yet modelled; confirm normal patterns and resolve changed dates before approving leave.
- Historical leave rows remain visible as earlier records. Pending earlier requests must be resubmitted with the employee declaration. Earlier manual timesheets remain stored and appear as unsigned drafts for review; no historical signature is manufactured.

## Implementation map

- `onecpase/hr_workforce.py`: migration, identity linking, dates/holidays, ledger balances, leave and timesheet state transitions, permission scope, audit and in-app events.
- `onecpase/hr_workforce_routes.py`: employee, HR administration, team availability, roster and weekly review routes.
- `onecpase/hr.py`: legacy route integration and sensitive-route protection; old unversioned timesheet writes return 409.
- `onecpase/database.py`, `onecpase/__init__.py`, `onecpase/phases.py`: additive migration, blueprint registration and existing phase/access-profile integration.
- `onecpase/permissions.py`: three explicit HR management/approval permissions, excluded from ordinary staff defaults.
- `onecpase/notifications.py`: HR in-app event links and preservation through the generic notification refresh.
- `onecpase/templates/hr/` and `onecpase/static/css/hr-workforce.css`: self-service, administration and responsive review screens.

## Verification

Run focused and regression checks using a fresh temporary test directory:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_hr_workforce.py tests/test_hr_access.py tests/test_staff_attendance.py tests/test_notifications.py tests/test_employee_numbers.py tests/test_phases.py tests/test_nav_labels.py tests/test_routes.py tests/test_startup_connections.py -q --basetemp=.pytest-hr-release-check
.venv\Scripts\python.exe -m scripts.verify_hr_workforce_ui
```

The browser script uses a disposable database with invented staff, not a live tenant or the local gym database. Screenshots/results are in `artifacts/hr-workforce-ui/`. Regression evidence is in `artifacts/hr-workforce-final-tests.txt` and `.xml`; the final accounting/workflow follow-up is in `artifacts/hr-workforce-complete.txt` and `.xml`. SQLite workflows are exercised; PostgreSQL DDL/adapter support still needs a PostgreSQL integration run before use on that backend.

Production deployment should copy the reviewed file set, take a database backup, apply the additive migration, verify login/navigation and run a short employee/HR pilot before staff onboarding. Do not populate unverified historical balances or automatically export payroll during deployment.
