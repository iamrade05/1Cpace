# 1Cpace HR plan and implementation specification

Date: 16 October 2026  
Status: Proposed design for review. No application, entitlement, permission or production changes are authorised by this document alone.  
Scope: employee records, leave, rosters, lunch coverage, attendance evidence, weekly timesheets, approvals and employee self-service.

## 1. Intended outcome

Every employee should be able to sign into the app, see their own accurate leave balance, request leave, see their schedule and lunch allocation, and submit a weekly timesheet with an authenticated sign-off. Managers should see the whole team's availability before approving requests, protect department coverage, and approve timesheets without being able to approve their own submissions.

The system must distinguish four things: scheduled work, access-card observations, employee-declared actual work, and approved payable time. A door entry does not prove an entire shift was worked. A scheduled lunch does not prove a lunch was taken.

## 2. Current position and risks

The following combines the 16 October live read-only audit with inspection of the existing HR code. Repeat counts during implementation because operational data may change.

| Area | Current position | Required correction |
|---|---|---|
| Identity | 18 human employee accounts; separate HR `staff` register has zero records | Create one linked employment profile per employee, retaining existing login IDs and employee numbers |
| Access identifiers | Existing `users.staff_id` values include external access-system references | Do not treat this field as a local `staff.id` foreign key; create an explicit identity link |
| Leave | No requests recorded in the audited database; no balance ledger | Import verified opening balances and leave history; calculate entitlements by employment cycle |
| Approval security | `OPEN_HR_PERMISSIONS` opens HR permission labels to signed-in users; no leave self-approval guard | Enforce ownership, team and action permissions on every endpoint |
| Leave paths | Requests can reference either `user_id` or `staff_id`; the main list joins users | Consolidate both paths so no request disappears from approval views |
| Leave validation | Request days are accepted from the browser | Calculate days on the server using effective rosters, holidays and policy |
| Timesheets | Weekly entries can be edited without a signed submission or approval lock | Add weekly status, versions, employee attestation and manager approval |
| Coverage | No department/shift leave or lunch conflict engine | Add effective-dated staffing requirements and relief assignments |
| Access evidence | Current feed supplies entries without reliable exits | Keep evidence separate; never fabricate clock-out or lunch times |
| Privacy | Leave listing lacks own/team filtering | Restrict sensitive records and attachments; show availability separately |

Zero recorded leave is not evidence of zero leave taken. Do not initialise everyone with a full unused annual or sick entitlement.

Relevant implementation files: `onecpase/hr.py`, `onecpase/hr_access.py`, `onecpase/auth.py`, `onecpase/schema.py`, `onecpase/database.py`, `onecpase/staff_attendance.py` and templates under `onecpase/templates/hr/`.

## 3. Employment and legal policy baseline

Use South African BCEA minimums for eligible employees, subject to applicable collective arrangements, exclusions and better contractual terms. Record policy version and effective date. Keep entitlement separate from payment and from UIF benefits. Obtain a final employment-policy review before enabling production enforcement, especially where working-time exceptions or contractual variations are relied upon.

### 3.1 Leave configuration

| Type | Proposed baseline | Cycle and accounting |
|---|---|---|
| Annual | 21 consecutive days, normally 15 working days for a five-day week or 18 for a six-day week | Individual 12-month employment cycle; proposed monthly accounting 1.25 or 1.5 working days, with the agreed method documented |
| Sick | Six weeks of normally worked days: normally 30 days for five-day workers or 36 for six-day workers | 36-month cycle; first six months use one paid day per 26 days worked; deduct early-cycle sick leave already used |
| Family responsibility | Three paid days for qualifying events, after more than four months' employment and at least four working days weekly | Annual cycle; no automatic carry-forward |
| Parental/adoption/commissioning | Event-based entitlement under the current court order and applicable law | Separate case workflow; do not deduct from annual leave or hard-code the superseded ten-day parental default |
| Study or additional compassionate leave | Contractual/company policy where offered | Clearly label as company benefit rather than a universal BCEA minimum |
| Unpaid leave | Recorded request and authorised decision | Separate from paid entitlement; do not silently convert a paid statutory entitlement to unpaid leave |

Annual leave must be scheduled within the statutory period, including the requirement to grant it within six months after the annual cycle ends. Coverage controls must support scheduling rather than indefinite refusal. Do not deduct rostered rest days or qualifying public holidays as annual working-day leave. Preserve better contractual benefits and verified existing credits.

Parental rules changed following the Constitutional Court's 3 October 2025 judgment. The interim order provides at least four consecutive months for a single or sole employed parent and an aggregate four months and ten days shared by two employed parents, with further protections and conditions. Capture the relevant circumstances and approved allocation; employer-paid leave and UIF benefits require separate treatment. Draft amendment bills must not be treated as enacted rules.

### 3.2 Sources and legal maintenance

- [Department of Employment and Labour: annual leave](https://www.labour.gov.za/DocumentCenter/Pages/Basic-Guide-to-Annual-Leave.aspx).
- [BCEA: sections 9, 14, 15, 20, 22 and 23](https://www.labour.gov.za/DocumentCenter/Acts/Basic%20Conditions%20of%20Employment/Act%20-%20Basic%20Conditions%20of%20Employment.pdf?platform=hootsuite). This published original Act is used here for the cited enduring provisions, not its superseded parental provisions.
- [Department: family responsibility leave](https://www.labour.gov.za/DocumentCenter/Pages/Basic-Guide-to-Family-Responsibility-Leave.aspx). The older guide's childbirth reference must not be used to configure current parental leave.
- [Official Constitutional Court judgment, Van Wyk, 3 October 2025, published through Parliament](https://www.parliament.gov.za/storage/app/media/OISD/ISD_Related_Constitutional_Court_Judgements/2026/5-may/14-05-2026/judgment-for-issuance-cct-309-23-cct-308-23-werner-van-wyk-others-v-minister-for-employment-and-labour-cge-and-others.pdf).

Recheck current law immediately before implementation and when a policy changes. Version rules so a future change does not silently rewrite historical approved records.

## 4. Employee master record

Retain `users` for authentication and establish one canonical `staff` employment record linked through a new unique `staff.user_id`. External card identities belong in a separate mapping table. A person with no login may still have an employment record, but employee self-service requires a verified linked account.

Required fields:

- Employee number, legal name, preferred name, active status and employment type.
- Start date, termination date where applicable, department, manager and authorised substitute approver.
- Effective-dated working pattern, normal paid hours, five-/six-day pattern where applicable, and scheduled rest days.
- Contract/policy assignment, leave-cycle dates, contractual enhancements and meal-break agreement.
- External access-system identity and verification status.

Keep employment records effective-dated when a department, manager or working pattern changes. Do not overwrite history needed to explain an old leave deduction or timesheet.

Employee numbers remain unchanged. DebiCheck Client Reference 2 continues to receive the digits only; HR identity migration must not alter that behaviour.

### Opening balances

Prepare an import worksheet with employee number, employment start date, cycle dates, entitlement, verified credits, leave already taken, approved future leave and supporting source. HR checks it and an authorised second person approves the import.

Employees see either a confirmed balance with an as-of date or **Opening balance awaiting confirmation**. Missing history is not displayed as zero taken. Use a unique import batch/reference to prevent duplicate credits. Retain the import and approval evidence.

## 5. Leave balances and requests

### Employee view

Show each leave type separately: cycle dates, opening balance, earned entitlement, adjustments, taken, approved future reservations, pending requests and available-to-request balance. Explain deductions in working days and show a transaction history.

For annual leave, define:

`current balance = verified opening + credits/earned entitlement + adjustments - taken`

`available to request = current balance - approved future reservations`

Pending requests are displayed separately and do not silently count as approved reservations. For future-dated requests, show a separately labelled projected balance at the start date using the configured accrual policy. Whether projected future earnings may be used is a company policy decision; do not imply they are currently available.

### Request workflow

1. Employee chooses leave type and dates; the server calculates scheduled working days.
2. The form shows relevant balance, projected balance, required evidence and coverage warnings.
3. Employee confirms the request and submits an authenticated declaration.
4. Manager sees team availability and decides; self-approval is prohibited.
5. Approval reserves the appropriate entitlement atomically and notifies the employee.
6. As leave is taken, convert the reservation to a taken transaction exactly once.
7. Approved cancellations or date changes release/recalculate reservations through reversing transactions. Never erase history.

States: `draft`, `submitted`, `approved`, `rejected`, `withdrawn`, `cancel_requested`, `cancelled`. Returned requests require a fresh employee confirmation when dates or quantities change. Taken quantities are transactions, not an ambiguous replacement for the request status.

Validate server-side: valid dates and ordering, employee eligibility, policy, working-day count, overlaps, entitlement, duplicate submissions, ownership and approver scope. Partial-day leave requires an explicit hours/day-conversion policy before it is enabled.

Emergency and qualifying statutory requests must remain recordable even when coverage is insufficient. Flag the staffing problem and arrange relief; do not use a blanket department collision rule to deny statutory rights.

## 6. Whole-team approval calendar and coverage

Provide a day/week/month calendar with department and shift filters, showing scheduled staff, approved absence, pending absence, lunch, rest days and relief staff. Show all employees' availability to authorised approvers, while limiting sensitive reasons and medical evidence to authorised viewers.

For each request, show a before/after coverage panel: required staff, available qualified staff, proposed absences, replacements and uncovered intervals. Pending requests warn of potential conflicts; approval checks confirmed availability again.

Proposed planned annual leave rule: normally one person away from a department at a time, **and** minimum qualified coverage for each affected shift. Permit an authorised override only with a recorded reason and cover arrangement. A staff member covering another role cannot be counted twice at the same time. Generic department headcount is insufficient where the remaining employee lacks the required skills.

Minimum coverage must be configured by department, location, day type and time interval. Leave and lunch use the same coverage calculation. Recheck inside the approval transaction so two simultaneous approvals cannot both consume the final available cover.

## 7. Rosters and lunch

### Known operating rules

| Group/day | Current stated hours |
|---|---|
| Reception, fitness and cleaners, Monday–Thursday | Early 05:00–13:00; late 13:00–21:00 |
| Reception, fitness and cleaners, Friday | Early 05:00–13:00; late 13:00–20:00 |
| Saturday and public holidays | 06:00–14:00; individual assignments still required |
| Sunday | Closed |
| Sales weekdays | 09:00–19:00; individual working days/rest days must be confirmed |
| Sales Saturdays | Not yet confirmed |

For 12–18 October 2026, the supplied early assignments are Lindelani, Mfundentle and Morongoe; late assignments are Busi, Tanki and Mvuleni. Verify canonical employee identities against employee numbers before publishing. These assignments must not automatically repeat into future weeks without approval.

### Lunch design

- Allocate lunch per employee and actual shift, with a start, duration and named relief where required.
- Prevent simultaneous lunch in the same department by default, subject to authorised coverage policy.
- Normally provide an uninterrupted one-hour meal interval after no more than five continuous working hours. A written agreement may reduce it to at least 30 minutes. Pay the interval where work or required availability makes it payable.
- Example slots: early 09:00–10:00; late 17:00–18:00; Saturday 10:00–11:00. Sales can use staggered slots such as 12:00–13:00 and 13:00–14:00, extended according to team size and lawful timing.
- The current single-person department shifts require relief or overlap. Do not publish an infeasible lunch allocation or delay a break indefinitely.
- Record actual lunch separately. A missed/interrupted lunch creates an exception for manager review; never automatically deduct a scheduled lunch as though taken.

Add roster warnings for ordinary hours, applicable overtime agreements/limits, daily/weekly rest and work at night. Review the 05:00 starts and late finishes for applicable night-work arrangements and transport. Avoid late-to-early rotation: 21:00 to 05:00 allows eight hours, and Friday 20:00 to Saturday 06:00 allows ten, against the normal 12-hour daily rest rule, subject to lawful variations. Working-time exemptions must be verified per employee rather than inferred from job title.

## 8. Weekly timesheets and app sign-off

Use Monday–Sunday weeks in Africa/Johannesburg. Show actual work dates, scheduled shifts, observed card entries, declared work/lunch intervals, leave, ordinary hours, overtime candidates and unresolved exceptions.

Workflow: `draft -> submitted -> approved`, or `submitted -> returned -> revised -> submitted`. Approved weeks are locked. A post-approval correction creates a new version, preserves the original and requires fresh employee and manager sign-off before downstream use.

Employee declaration: **I confirm that the work and break times in this version are accurate and that I have identified any missing or incorrect entries.**

Manager approval confirms review of exceptions and payable classifications, rather than merely that the employee entered the premises. A manager's own week goes to a different authorised approver. Acting approvers must be explicitly delegated and auditable.

Each sign-off stores authenticated user, employment identity, version, declaration text/version, timestamp and immutable content hash. Generate a downloadable receipt showing both sign-offs. If a separate electronic-signature standard is required by contract or law, review it before relying on this workflow for that purpose.

Reject invalid interval ordering, overlapping work intervals, negative durations and incomplete lunch pairs. Support overnight shifts explicitly or reject them clearly until supported. Store minutes as integers, round only under a documented policy, and avoid floating-point balance drift.

Proposed operational cadence, configurable after review: employees submit the previous week on Monday; managers review by Tuesday; outstanding submissions and approvals appear on a dashboard. Do not invent employee sign-offs or automatically approve missing weeks.

### Access evidence

Preserve raw scan timestamps, source identity, feed freshness and match confidence. Show raw scan counts separately from clustered repeat entries. Denied access is not attendance. Do not infer exits, lunch duration or worked hours from repeated entries.

The local sync depends on the Windows source machine/session. Show last successful import and latest source event; alert on stale data. Resolve unmatched staff cards before treating missing scans as absence. Payroll must not depend on this feed being continuously available.

## 9. Permissions and privacy

| Capability | Employee | Team manager | HR administrator | Payroll-authorised user |
|---|---|---|---|---|
| Own balances, requests and timesheets | View/submit | Same own rights | Same own rights | Same own rights |
| Approve leave/timesheets | No | Assigned team, never self | Authorised scope, never self | Only if separately assigned approval rights |
| Team availability | Limited availability policy | Team; wider coverage where authorised | All authorised staff | Only as needed |
| Medical reasons/documents | Own records | Minimum necessary, explicitly authorised | Authorised HR access | No blanket access |
| Opening balances/policy changes | No | No default access | Prepare; separate approval for sensitive adjustments | No automatic HR administrator rights |
| Salaries/payroll | No | No default access | No automatic payroll rights | Existing Mvuleni/Ayrton restriction preserved |

Replace the open permission bypass with explicit actions such as `hr_self_service`, `hr_team_view`, `hr_leave_approve`, `hr_timesheet_approve`, `hr_roster_manage`, `hr_balance_manage` and `hr_policy_manage`. Check permissions server-side for HTML, JSON, exports and attachment downloads. Protect mutations with CSRF checks and record successful sensitive actions in an audit log.

Audit actor, subject, action, previous/new version, reason and timestamp. Avoid copying medical details into general audit messages or calendar notifications. Define retention and secure deletion with the organisation's applicable recordkeeping and privacy obligations before storing attachments.

## 10. Proposed database changes

Names below are a design proposal, to be aligned with repository conventions during implementation.

| Table/change | Essential purpose and fields |
|---|---|
| Extend `staff` | Unique nullable `user_id`, employee number, manager link, employment dates/type and active status; preserve existing fields |
| `staff_access_identities` | Staff, source, external reference, validity dates, verification; unique source/reference for overlapping validity |
| `employment_terms` | Staff, effective dates, department, working pattern, policy, contractual hours and break agreement |
| `hr_policy_versions` | Effective dates, entitlement rules, cycle/accrual rules, rounding and policy approval |
| `leave_accounts` | Staff, leave type, cycle, assigned policy; unique staff/type/cycle |
| `leave_transactions` | Signed quantity, unit, event type, request/import reference, effective date, actor and reversal reference |
| Extend `leave_requests` | Canonical staff link, calculated quantity/unit, policy version, state/version, submission and decision metadata |
| `leave_request_days` | Date-level charge, scheduled work basis and holiday/policy snapshot |
| `roster_shifts` | Staff, local work date, start/end instants, department, role/location, publication version |
| `coverage_requirements` | Department/location, interval/day type, minimum staff/skills and override policy |
| `lunch_allocations` | Shift, scheduled interval, relief assignment and publication state |
| `timesheet_weeks` | Staff, week start, state, version, current submission/approval and content hash |
| Extend/version `timesheets` | Weekly version link, declared intervals, paid/unpaid breaks, minutes, source and exception status |
| `hr_attestations` | Subject/version, actor, declaration version, timestamp and content hash |
| `hr_approval_delegations` | Approver, scope, effective dates, delegator and reason |
| `hr_audit_events` | Immutable business-action history with sensitive content minimised |
| `hr_outbox` | Transactional notification events and delivery/retry state |

Use additive, repeatable migrations and foreign keys/indexes. Do not rename or repurpose external IDs. Preserve legacy request IDs, backfill canonical identity links only where matches are verified, and quarantine ambiguous rows for HR review.

Transaction references must be unique for monthly credits, imports, approvals, cancellations and taken-leave posting. Repeated requests or background jobs must not double-credit or double-debit. Store day quantities as fixed units, for example integer hundredths where the selected policy permits them, and actual time as integer minutes.

## 11. Application structure and routes

Keep route handlers thin. Proposed services: `hr_identity`, `hr_policy`, `hr_leave`, `hr_coverage`, `hr_roster`, `hr_timesheets` and `hr_audit`. Reuse the existing attendance evidence module rather than introducing a second scan interpretation.

Proposed screens/routes, adjusted to existing Flask conventions:

- `/hr/me`: employee dashboard, balance cards, requests, upcoming shifts and unsigned weeks.
- `/hr/leave`: scoped request list; `/hr/leave/calendar`: authorised availability calendar.
- Existing request/approve/reject routes: consolidated identity, server-side calculations and transactional decisions.
- `/hr/roster`: weekly planning, coverage warnings, lunch and publication.
- `/hr/timesheets`: scoped weekly list; weekly submit, return, approve and correction actions.
- `/hr/admin/opening-balances`: staged import, validation, second approval and reconciliation.
- `/hr/admin/policies`: versioned policy management with explicit effective dates.

All state-changing actions use POST with ownership checks, CSRF protection, an idempotency reference and expected version. Return a useful conflict message when another user has changed the record. Calendar endpoints return only fields that the viewer may see.

## 12. Delivery phases and exit criteria

| Phase | Work | Exit criteria |
|---|---|---|
| 0: Confirm policy/data | Employee census, terms, approvers, opening history, coverage and break agreements | Named owner signs off input pack; unresolved fields are visible |
| 1: Identity/security | Canonical staff links, permissions, self-approval prevention, scoped records, audit foundation | All active employees linked; unauthorised cross-user actions fail; payroll restriction unchanged |
| 2: Leave | Policy engine, cycle accounts, opening import, balance display and request lifecycle | HR reconciles each imported balance; five-/six-day and statutory eligibility cases pass |
| 3: Calendar/roster/lunch | Team calendar, minimum coverage, relief, lunch conflicts, rest/hours warnings | A complete operating week is feasible and approved; simultaneous approvals cannot overbook cover |
| 4: Weekly sign-off | Actual intervals, submission/versioning, manager review, receipts and correction workflow | Pilot employees and managers complete an entire week; approved records cannot be silently edited |
| 5: Operational rollout | Training, notifications, support process, monitoring and payroll reconciliation | HR approves first live cycle and exceptions; no unreviewed automated payroll writes |

No delivery date should be promised until employee history, policy decisions and deployment constraints are confirmed. Deliver reviewable increments behind separate feature flags rather than enabling unfinished workflows together.

## 13. Verification and acceptance checks

1. Identity migration is repeatable; no duplicate employees; external card references and DebiCheck employee-number behaviour are preserved.
2. A five-day employee's full normal three-week annual absence charges 15 working days; a six-day employee's equivalent charges 18, adjusted for qualifying holidays and their actual schedule.
3. Sick leave uses the 36-month cycle and first-six-month rule, never a fresh 30-day annual allocation. Family responsibility eligibility is applied correctly.
4. Opening imports and accrual jobs can be retried without duplicate credits. Reversal history explains every changed balance.
5. Invalid dates, fabricated browser day counts, overlapping requests and duplicate submissions are rejected or handled consistently.
6. Two concurrent approvals cannot reserve the same final coverage or overspend the same balance. Reapproval of an already approved request has no second debit.
7. Employees cannot approve themselves, edit colleagues' timesheets, download others' documents or see private medical information through exports/URLs.
8. Managers see the appropriate whole-team calendar; sensitive reasons are filtered server-side.
9. Same-department lunch conflicts are detected; one-person shifts require real relief; missed lunch is not automatically deducted.
10. Late-to-early shifts and excess hours generate applicable warnings. Overrides require permission, reason and a lawful policy basis.
11. A week cannot be submitted with invalid time intervals. Editing after employee sign-off invalidates that sign-off and requires resubmission.
12. An approved week is immutable; corrections retain the old signed version and produce new employee/manager attestations.
13. Entry-only, denied, duplicate and stale access observations never become invented worked hours or clock-outs.
14. Existing HR navigation, attendance preview, payroll access and DebiCheck paths pass regression checks.

Use service-level tests for accounting/coverage invariants, route tests for authorisation/CSRF/version conflicts, migration tests on a copied database, and a manual end-to-end pilot with HR. Check responsive layouts on staff phones, clear validation messages, keyboard access and print/download receipts.

## 14. Deployment, monitoring and rollback

Before rollout: take a verified database backup, rehearse restore, run migrations against a copy, reconcile row counts/links/balances and stage a pilot with representative departments. Use a narrow maintenance window if the migration requires it. Apply the permissions correction with an explicit approver map so legitimate approvals remain possible.

Feature flags: employee self-service, leave ledger, coverage enforcement, lunch planning and timesheet sign-off. Keep baseline security checks enforced rather than treating an unsafe permission bypass as a rollback option.

Monitor unlinked employees, unconfirmed balances, failed accruals, stale access data, duplicate transaction attempts, coverage exceptions, outstanding requests and unsigned/returned weeks. Notifications need retries and must not expose medical reasons.

Rollback normally disables new workflows and preserves the additive schema, audit history and financial transactions. Do not drop ledgers or restore a database over legitimate work completed since backup without reconciliation. Correct entitlement errors using reviewed reversing transactions. Payroll export/integration remains disabled until separately reconciled and approved.

## 15. Decisions and input pack required

| Input/decision | Proposed owner | Why needed |
|---|---|---|
| Start dates, employment types, contracts and normal working days | HR/management | Correct eligibility and cycles |
| Historical leave taken and approved future leave | HR plus employee confirmation | Trustworthy opening balances |
| Managers, substitute approvers and HR administrators | Business owner | Scope and separation of duties |
| Minimum department/shift staffing and qualified relief | Department managers | Feasible leave and lunch coverage |
| One-hour lunch or valid written reduced interval; paid availability | Management/HR | Correct schedules and paid hours |
| Sales Saturday hours and all individual weekly rest days | Operations | Work patterns, working-day leave and hours checks |
| Future accrual use, negative balance policy, partial days and additional benefits | Management/HR | Explicit, consistent entitlement decisions |
| Submission/review deadlines and correction escalation | HR/payroll | Weekly accountability |
| Retention, attachment access and notification channels | HR/system owner | Privacy and reliable operations |

Recommended first implementation package after design acceptance: secure permissions, link employee profiles, stage the opening-balance import and provide an employee dashboard that clearly distinguishes confirmed balances from records awaiting verification. Then enable leave approval and coverage together, followed by lunch planning and weekly sign-off.
