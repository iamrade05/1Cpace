# 1Cpace — detailed section audit, 2 November 2026

Follow-up implementation: [priority stabilization record](STABILIZATION_20261102.md). The findings and reproduction evidence below describe the audited state before those fixes; they remain preserved for comparison.

## Verdict

The application has substantial working business logic, but the current working tree is not ready for an unrestricted release sign-off. The most serious problems concern revoking access, preserving a coherent activation state after failure, tenant-specific data sources, and financial reporting. More visual polish will not resolve those problems.

This is an audit, not an implementation change. Existing source changes were preserved. No production database, provider transaction, message, or deployment was initiated. All reproductions used disposable databases; the external push and external roster total were mocked.

Evidence labels used below:

- **Reproduced:** observed against the current application with disposable data.
- **Source-confirmed:** directly visible in the reviewed implementation; concurrency, deployment, or production incidence was not reproduced.
- **Acceptance gap:** current real-world behavior was not established by this audit.
- **Design debt:** a deliberate design or usability limitation, not necessarily a violated requirement.

Severity indicates impact and priority, not an estimate of how frequently the issue occurs. The section assessments are qualitative; a numerical score would suggest more complete coverage than this audit provides.

## Scope and validation

Reviewed the current local working tree, including existing uncommitted work. Read the existing section plan and release review, then examined routes, shared services, permissions, database writes, background work, templates, test configuration, and selected tests.

The documented root command `python -m pytest -q` failed with **seven collection errors**, before executing the application tests. Archived `artifacts/**/tests.txt` files fail UTF-8 decoding, and an archived `test_queries_workflow.py` conflicts with the current module. `pytest.ini` does not restrict discovery to `tests/`.

A separate `pytest tests -q` run was started and later interrupted because it was progressing very slowly. It has no completed summary and is not a passing suite result. A smaller focused run completed with **43 passed in 254 seconds**, covering `test_auth.py`, `test_permissions.py`, `test_application_push_gate.py`, `test_reporting_cycle.py`, and `test_internal_comms.py`. Its [JUnit evidence](artifacts/section-audit-20261102/focused-tests.xml) is preserved. These existing tests do not cover all the failure cases reproduced by the audit probes.

The initial browser attempt timed out waiting for network idle on the first page. A second, deliberately offline-dependency run rendered eight screens at **1366px and 390px**, giving 16 renders. All returned HTTP 200 and none overflowed the document horizontally. External HTTPS resources were blocked in that second run; web-font fidelity and successful CDN loading are therefore outside its coverage. Two dashboard `Chart is not defined` errors made the browser command fail. This establishes missing graceful behavior when the chart CDN is unavailable; it does not establish that the CDN fails for ordinary production users.

Reviewed screenshots of the desktop Sales dashboard, desktop Queries screen, and mobile Member profile. The fixture has sparse fictional records; populated queues, long names, every workflow state, accessibility, and all staff roles were not visually certified.

Raw evidence: [probes.json](artifacts/section-audit-20261102/probes.json), [browser results](artifacts/section-audit-20261102/ui/results.json), [probe runner](artifacts/section_audit_20261102_probes.py), [browser runner](artifacts/section_audit_20261102_ui.py).

## Findings that should control the work order

| ID | Priority | Finding | Evidence |
|---|---|---|---|
| A01 | P0 | Disabled browser accounts retain access and can still write | Reproduced |
| A02 | P1 | An admin demoted to manager retains cached admin authorization | Reproduced |
| A03 | P1 | Failed activation synchronization commits a contradictory state | Reproduced with an injected local failure |
| A04 | P1 | Reconciliation falsely declares different identities clean | Reproduced |
| A05 | P1 | Another tenant uses Elev8's roster total | Reproduced with a mocked external response |
| A06 | P1 | Finance reports count a discount credit as collected money | Reproduced |
| A07 | P2 | Monthly outstanding shows the charge total, including settled charges | Reproduced |
| A08 | P2 | Fitness accepts impossible measurements and class settings | Reproduced |
| A09 | P2 | Fitness member selectors use an old outcome instead of current status | Reproduced |
| A10 | P2 | Operations accepts invalid dates and arbitrary controlled values | Reproduced |
| A11 | P2 | The documented release test command does not collect successfully | Reproduced |
| A12 | P2 | Financial records can be hard-deleted with the payment-entry permission | Source-confirmed design risk |
| A13 | P2 | External push work is held in daemon threads without a durable job lifecycle | Source-confirmed reliability risk |
| A14 | P2 | Internal thread creation has a concurrent first-send race | Source-confirmed |
| A15 | P2 | Several registers/conversations load every matching row | Source-confirmed scaling risk |
| A16 | P2 | Offset-bearing dashboard timestamps lose their offset without conversion | Source-confirmed |
| A17 | P2 | Elev8 visit lookup is global and joined to reports by phone suffix | Source-confirmed tenant/identity risk |
| A18 | P3 | Dashboard charts fail noisily when the CDN is unavailable | Reproduced under intentionally blocked external requests |

P0 means fix before relying on the affected access control. P1 means fix before broad rollout or trusting the affected output. P2 means schedule stabilization work. P3 is resilience or usability work after correctness.

## 1. Dashboard, navigation, and daily work

**Assessment: useful workspaces, unreliable edges in the numbers and identity reconciliation.**

The department-specific landing pages, server-side dashboard boundaries, date controls, and consolidated work views are useful foundations. The sampled screens fit desktop and phone widths. The main weakness is confidence in what the displays assert.

**A04 — reconciliation can report a false clean result.** `build_member_reconciliation` falls back from identity to phone/email even when the records have different non-empty IDs. It does not enforce one-to-one matching. Two live people with IDs `111` and `222`, sharing one phone, matched the same local person; the result was `matched_count=2`, no missing records, and `is_clean=true`. Email normalization also strips punctuation: `a.b@example.com` and `ab@example.com` were treated as the same key and different IDs again produced `is_clean=true`.

Impact: a reassuring reconciliation result can hide a missing person or a mistaken identity. Fix by preferring stable external references/verified IDs, refusing conflicting identity evidence, preserving meaningful email characters, normalizing phone numbers deliberately, and surfacing ambiguous matches for review. A local row must not satisfy two live rows silently.

**A05 — totals are not reliably tenant-specific.** Both the Member register and admin dashboard call the Elev8 roster-count helper without a tenant guard. The helper queries the configured Elev8 SQL Server. In a request bound to `othergym`, a mocked Elev8 response of `424242` was requested and shown in that gym's member page. The mock proves the routing error; this audit did not connect to the real SQL Server.

**A16 — timestamp boundaries are fragile.** `_event_datetime` parses timezone offsets and then removes the timezone using `replace(tzinfo=None)`. It never converts to Johannesburg time. For example, 23:30 UTC belongs to the following local day, but remains the previous day's 23:30 in this helper. Historical naive dates need an explicit convention, while aware values need conversion before comparison.

**A18 — charts depend on a third-party script with no useful failure state.** Blocking external HTTPS caused `Chart is not defined` twice. Bundle the pinned chart asset locally or guard initialization and show an intelligible fallback.

Sources: [dashboard.py](onecpase/dashboard.py), [elev8_data.py](onecpase/elev8_data.py), [dashboard.html](onecpase/templates/dashboard.html).

Acceptance: ambiguous and conflicting identities never produce a clean result; other tenants never call an Elev8-only source; UTC/local boundary fixtures agree with displayed dates; blocked chart assets do not produce uncaught page errors.

## 2. Sales, leads, applications, and activation

**Assessment: substantial workflow controls, but activation recovery is not dependable enough.**

The application has evidence-derived readiness, an atomic claim before starting a push, a required external member reference, explicit override handling, and a sales-stage synchronization step. These are meaningful controls. They do not make the failure path safe.

**A03 — activation can finish in contradictory states.** After a mocked successful external push, the worker updates the application to `active`, writes the external reference, and synchronizes the Sales stage. When that last step raises, the exception handler updates `push_status` and commits without first rolling back. The reproduction ended with `application_status='active'`, `push_status='push_failed'`, and the member retaining the new external reference. The comment promising a rollback does not match the code.

Fix the local transaction boundary and preserve the confirmed external outcome separately. An external success followed by a local error needs reconciliation, not a generic state that implies it is safe to create the external member again.

**A13 — the push lifecycle depends on a daemon thread.** The request commits `push_status='pushing'`, then starts a daemon thread. A worker restart can kill the work. The push guard subsequently refuses records still marked `pushing`. A durable operation record, lease/heartbeat, known external identity, recovery action, and a separate unknown-outcome state would make this operable. The audit did not kill a real provider operation.

The adapter also explicitly does not support editing existing Itensity members or automatic third-party-payer pushes. Those are real limitations, not completed automation. `_consultant_name` falls back to a specific person's name; a missing lookup can silently misattribute external Sales data. Missing consultant attribution should become a visible validation issue or a tenant-configured fallback.

The inspected Sales screen is organized, but the board, daily targets, period controls, and analysis queues create several layers of hierarchy. With real data, verify that a consultant can identify the next action without expanding several panels. The empty preview is not a populated-board usability test.

Sources: [applications.py](onecpase/applications.py), [integrations.py](onecpase/integrations.py), [leads.py](onecpase/leads.py), [sales_dashboard.html](onecpase/templates/sales_dashboard.html).

Acceptance: inject failure after every external/local boundary; application, member, and Sales stage remain coherent; a worker restart leaves recoverable work; a timeout never causes an unreviewed duplicate external create.

## 3. Communication, calling, and inboxes

**Assessment: useful integration coverage, but transport evidence and concurrency need tightening.**

Internal conversations derive threads from the current user's pair with another active user, and announcements have explicit publisher checks. Keeping internal messages distinct from external channels is sensible.

**A14 — simultaneous first messages can fail.** `_thread_for` first selects, then inserts the unique pair, then commits. Two requests can both see no thread; one then hits the unique constraint. The uniqueness rule protects data but does not provide a successful concurrent user experience. Use an atomic insert-on-conflict followed by select, or catch the specific conflict and retry the read.

**A15 — conversation history is unbounded.** Opening a thread fetches every message. The member register and open lead columns also fetch all matching rows. Pagination or bounded incremental loading is needed before realistic histories make these pages progressively slower. This is a source-level scaling concern; no production load test was run.

Collection SMS escalation includes staff-recorded send/failure states; it is not an automated SMS provider implementation. Some WhatsApp paths do use a transport. The system should preserve that distinction in its evidence and wording. A manual attestation, a provider-accepted send, a delivery receipt, and a reply are different events.

Sources: [internal_comms.py](onecpase/internal_comms.py), [collections.py](onecpase/collections.py), [comms.py](onecpase/comms.py).

Acceptance: concurrent first sends both succeed without duplicate threads; history is bounded; replay/reconnect and failure handling are verified against each configured transport; staff-marked sends are clearly identifiable.

## 4. Member register, evidence, and profiles

**Assessment: useful shared identity model, with incorrect reconciliation and source mixing.**

Members connect applications, payments, mandates, documents, Queries, and Fitness. Keeping this shared identity is the right direction. The current profile is information-rich and the sampled phone rendering avoids document overflow.

The identity and tenant-count defects A04/A05 apply directly here. A15 also applies: `members_index` loads all matches with no server-side pagination. The register can show an authoritative external total beside local status counts and a local row list. Those datasets should be explicitly labeled with source and freshness rather than appearing to describe one fully synchronized population.

The phone profile requires substantial scrolling through personal information, debit details, account summaries, and a separate NuPay contract view before reaching notes. Repeated amounts and status concepts add interpretation work. This is a usability judgment from one fictional member, not proof that staff cannot use it. Put the current account state, blocker, and next required action at the top; let supporting history expand below it.

Sources: [members.py](onecpase/members.py), [member profile](onecpase/templates/members/detail.html), [mobile screenshot](artifacts/section-audit-20261102/ui/profile-390.png).

Acceptance: page/filter totals explain their source; large registers have bounded responses; staff can identify a member's current blocker and next action from the first screen.

## 5. Queries, signed forms, and case resolution

**Assessment: comparatively structured, but a case being closed must not be mistaken for an external action being completed.**

The signed-form workflow has tenant-bound expiring links, encrypted submission/evidence snapshots, controlled owners/stages, setup checks requiring separate approver and processor identities, and terminal-stage synchronization with the original ticket. Those are meaningful design choices.

No new independent Query transition bypass was reproduced in this audit. That is narrower than certifying all six forms and every decision path. In particular, approving a refund, cancelling a mandate, recording a payment, changing an external membership, and confirming the client outcome require distinct evidence.

The desktop queue is readable at the sampled size, though there are several competing filters: six summary cards, dropdowns, and a row of quick filters. Their counts and scopes should be explicit. The synthetic seed also lacks some real-world metadata, so its placeholder labels are not evidence of a production reference-generation defect.

The global browser-session findings A01/A02 affect this section's authorization too. A locally correct approval function is insufficient if its caller retains a revoked role.

Sources: [queries.py](onecpase/queries.py), [query_forms.py](onecpase/query_forms.py), [query_funnel.py](onecpase/query_funnel.py), [query_routing.py](onecpase/query_routing.py).

Acceptance: run one complete success and rejection journey per form with actual staff roles; verify the preserved signed snapshot, handoff owner, external evidence, final member state, and client outcome separately. A01/A02 must be fixed first.

## 6. Collections and Promise to Pay

**Assessment: deep business logic, with fragile ledger maintenance and misleading reporting around it.**

There is a shared case engine, reconciled arrears calculation, explicit grant/evidence checks, verified upfront-payment handling, and saved repayment schedules. Those foundations are valuable. Complexity alone is not the problem; duplicated interpretations of money and work status are.

**A12 — payment-entry permission also allows hard deletion.** `collections_delete` uses `new_collection`, deletes the collection row, writes a brief member-activity message, and updates application payment state. It does not preserve the original financial row as a reversal/version and, unlike add/edit, does not call `reconcile_open_cases` in that route. Some linked/imported rows may be protected by foreign keys; this is not a claim that every imported record can be deleted. For deletable records, a text message with the amount does not retain all original fields and provenance.

Fix by defining correction/reversal behavior, retaining full before/after facts and source identity, and reconciling all dependent states through one service. Restrict sensitive correction rights separately if that matches the intended staffing policy.

The reports issues A06/A07 can make Collections look healthier or worse than its reconciled account state. The collection form's `outstanding_balance` is also used as a gross charge in parts of the ledger. The label should not invite staff to enter an already-netted balance and then have the system subtract payment again.

Manual SMS and some escalation confirmations are staff evidence, not proof of provider delivery. PTP approval and a working debit mandate must also remain distinct; the existing confirmation helper's evidence gates are worth preserving.

Sources: [collections.py](onecpase/collections.py), [collections_engine.py](onecpase/collections_engine.py), [ptp.py](onecpase/ptp.py), [collection_arrangement.py](onecpase/collection_arrangement.py).

Acceptance: a correction produces a traceable adjustment and immediately updates cases, arrears, application readiness, and reports consistently. The saved arrangement and every displayed repayment amount must agree to the cent.

## 7. Finance, DebiCheck, and reconciliation

**Assessment: integrations are substantial; the aggregate reporting cannot yet be trusted as a cash view.**

The NuPay importer distinguishes charges and payment rows and carries source identity. Payment synchronization attempts to reconcile existing records instead of importing duplicate money. Those are important controls.

**A06 — discount credits inflate money collected.** The Itensity import adapter represents `Discount` as a paid credit in `amount_paid`. Finance aggregates sum `amount_paid` without excluding discounts, while the dashboard has explicit discount exclusion. In the reproduction, R100 actual payment plus R50 imported discount produced `total_collected=150` against `total_billed=100`. The Finance cards would therefore derive 150% collection and a negative R50 outstanding balance from that sample. A discount can reduce debt without being cash received.

**A07 — monthly outstanding is the gross charge total.** For a single R100 charge fully paid by R100 on the same row, Finance totals correctly imply zero remaining debt, but the monthly table's `outstanding` field remains R100 and is rendered under “Outstanding.” A separate charge/payment representation has the same labeling problem: monthly raw charges are not net arrears.

Use one explicit classification of charges, actual receipts, discounts/credits, and reversals, with shared calculations for dashboards, Finance, profiles, and exports. Report both historical movement and current debt clearly; do not sum historical arrangements as though they were an independently reconciled current balance.

Real NuPay authentication, provider UI changes, banking submission, and live transaction completeness remain acceptance gaps. Existing mocks and historical deployment artifacts do not establish today's provider behavior.

Sources: [reports.py](onecpase/reports.py), [report template](onecpase/templates/reports/index.html), [Itensity transaction adapter](scripts/import_itensity_transactions.py), [nupay_transactions.py](onecpase/nupay_transactions.py), [payment_sync.py](onecpase/payment_sync.py).

Acceptance: identical fixtures for actual cash, discount-only credits, partial settlement, reversals, repeated imports, and cross-source duplicate payments must produce consistent totals everywhere.

## 8. Fitness, classes, and onboarding

**Assessment: broad feature coverage, weak validation in older forms.**

The shared class-booking service serializes capacity checks and prevents repeat active bookings. That is a better foundation than separate web/mobile booking logic.

**A08 — impossible data is persisted.** The application accepted class capacity `-5`, day `made-up`, and time `29:99`. An assessment accepted weight `-80` and body fat `150`. A supplied invalid assessment date silently became today's date. These were direct form submissions, showing that HTML control constraints are not equivalent to server validation.

**A09 — member pickers do not mean “active members.”** `get_active_members` selects `outcome='joined'`. A currently Active member with a different outcome was absent; an Inactive member retaining `joined` was present. Assessments, plans, dailies, incidents, and supplement assignment reuse this helper. In contrast, class booking explicitly checks `member_status='Active'`.

Validate measurement ranges, finite numbers, dates, schedule enums, times, capacity, and selected member/trainer eligibility on the server. Use an explicit eligibility policy per action; some historical incident/assessment work may legitimately include inactive members, but the choice should be deliberate.

Sources: [fitness.py](onecpase/fitness.py), [fitness_bookings.py](onecpase/fitness_bookings.py), [fitness_journey.py](onecpase/fitness_journey.py).

Acceptance: invalid submissions return useful errors and persist nothing; current member state drives selectors; both web and native booking use the same tested capacity rules. Real onboarding allocation and trainer handoffs still need role-based acceptance.

## 9. Operations, equipment, and maintenance

**Assessment: a basic equipment register, not a mature maintenance control system.**

Equipment search, condition counts, maintenance history, dates, and costs are present. It is a useful starting point.

**A10 — controlled fields are not enforced server-side.** The reproduction saved `condition='made-up'`, `equipment_type='made-up'`, and `purchase_date='not-a-date'`. Maintenance writes similarly convert cost directly and do not visibly validate date ordering or a non-negative finite amount. Those latter cases are source concerns, not separately reproduced failures.

The reviewed Operations module is primarily CRUD. It does not by itself establish reliable due-work ownership, an escalation process, completion evidence, or prevention of unsafe equipment use. Treat those as product-scope gaps if Operations is expected to manage maintenance beyond recording it.

Sources: [operations.py](onecpase/operations.py).

Acceptance: controlled values and dates cannot be bypassed through POST; invalid costs persist nothing; every due maintenance item has a defined owner and completion outcome if those are intended operational requirements.

## 10. HR and workforce

**Assessment: the newer workforce workflow is substantially more deliberate than the older generic forms; identity policy remains special-cased.**

The workforce code has a leave ledger, approval identities, versioned signed weeks, transaction wrappers, and coverage/manager checks. The legacy HR layer now redirects several actions into that service and adds protection to sensitive legacy routes. Preserve those controls.

Payroll access deliberately recognizes two named people using configured email matches and a fallback to first name/username. A disposable staff user named `Ayrton Example` with username `ayrton` received payroll eligibility without an explicit payroll grant. This reproduces the documented policy; it is **not** being presented as an accidental bypass of the current requirement. It is design debt: mutable human names are being used as authorization identity, and the policy is embedded across gyms instead of being a reviewed tenant-specific assignment.

Separate that policy question from A01/A02, which are actual revocation failures. The newer HR actor checks may reject a disabled account on some routes, but that does not repair the global browser-session gap.

No labor-law compliance opinion is provided. No full payroll calculation, production leave balance reconciliation, or signed-week acceptance was performed.

Sources: [hr_access.py](onecpase/hr_access.py), [query_routing.py](onecpase/query_routing.py), [hr_workforce.py](onecpase/hr_workforce.py), [hr_workforce_routes.py](onecpase/hr_workforce_routes.py), [hr.py](onecpase/hr.py).

Acceptance: decide explicitly whether named-person payroll access remains the policy; bind it to stable reviewed identities if retained. Test self/manager/admin boundaries, rejected leave, balance changes, and superseded week signatures using actual role assignments.

## 11. Reports and analytics

**Assessment: the highest risk is believable presentation of inconsistent facts.**

A06/A07 are demonstrated financial-reporting defects. A05 shows a tenant-specific source being used globally.

**A17 — external attendance data is also not tenant-scoped.** `_itensity_last_visits` reads Elev8's SQL Server into a process-global five-minute cache. `_load_register` calls it without checking the tenant, then joins dates by the last nine digits of the phone. The dedicated Access Report has an explicit tenant boundary; this helper bypasses that route boundary. Another tenant sharing a phone suffix can receive an unrelated external visit date if the source is reachable. This path was source-confirmed, not exercised against real attendance data.

There is a further source concern in the membership register: any non-empty mandate status sets `mandate_ok=True`, rather than using the shared approved-status normalization. A rejected or failed status should not receive the same readiness classification as an approved one. Its exact downstream display/segment behavior should be covered by a regression fixture.

The custom-report SQL builder does allowlist tables/columns/operators and parameterizes values, which is a useful control. However, selecting member columns alongside grouped aggregations can have ambiguous behavior, and negative limits are not clamped at a lower bound. Those are secondary robustness concerns rather than the main reporting risk.

Sources: [reports.py](onecpase/reports.py), [access_report.py](onecpase/access_report.py), [membership_register.html](onecpase/templates/reports/membership_register.html).

Acceptance: every metric names its grain, source, time basis, and exclusions. Failed/pending/approved mandates remain distinct. No report or process cache supplies another gym's source facts. Exported totals agree with the screen and the account ledger.

## 12. Administration, permissions, tenants, and platform access

**Assessment: urgent access-revocation defect despite otherwise useful tenant boundaries.**

**A01 — disabling an account does not reliably revoke the existing browser session.** The generic login/role/permission decorators trust session presence and cached values. The general staff hook queries role/department without checking `active`. In the reproduction, a disabled admin received HTTP 200 from `/members`, then successfully saved a new equipment record. This is a retained session problem, not a new password-login bypass.

**A02 — demotion does not reliably refresh authorization.** The staff hook computes a restricted department profile, then returns early when no such profile applies. Admins and managers are in that no-profile category, so role/permission refresh below the early return never executes. An account changed in the database from admin to manager still had session role `admin` and received HTTP 200 from `/admin/`.

Fix with one request-level identity check for all authenticated browser routes: account exists, account active, platform-shadow rules, tenant binding, current role/permissions, and password/session revocation state. Route-specific checks remain necessary but should consume a current actor. Do not depend on visiting a dashboard first to refresh privileges.

Sources: [auth.py](onecpase/auth.py), [application request hooks](onecpase/__init__.py), [dashboard_access.py](onecpase/dashboard_access.py), [permissions.py](onecpase/permissions.py).

Acceptance: log in, then disable/delete/demote/change permissions/reset credentials in another session. Every subsequent protected read and write must reflect the new authorization immediately, across operational, admin, HR, document, and background-action entry points.

## 13. Notifications and training

**Assessment: sound distinction between acknowledging an alert and completing the work; operational coverage still depends on ownership.**

Notifications are keyed by source event and recipient, and acknowledgment is distinct from resolving the source task. The browser interaction check confirmed explicit acknowledgment while the underlying work remained open. Training resources are protected routes, not publicly served static documents, and normal permissions still govern the actions described.

Notification generation starts from valid owners/supervisors, with additional named routing for some Elev8 Query events. If ownership or supervisor configuration is missing, not every source has a general fallback recipient. This is an operational risk, not a demonstrated loss of a particular production alert. Add visibility for unassigned/undeliverable work rather than allowing an empty recipient set to look like successful notification handling.

Training is a searchable guide with downloadable manuals. That is helpful documentation, but it does not establish staff competency, correct live provider behavior, or a completed operational workflow. New source changes should trigger review of the affected chapters and screenshots; static manuals can otherwise diverge from the application.

Sources: [notifications.py](onecpase/notifications.py), [training.py](onecpase/training.py), [training resources](onecpase/resources/training/).

Acceptance: every critical unowned task is visible to an accountable role; acknowledgments never close work; reopened/escalated events generate the right fresh notification; manuals identify the application edition they describe.

## 14. Mobile and physical access

**Assessment: useful delivery paths, not independently certified production experiences.**

The native API has tenant-bound bearer identity, hashed sessions/challenges, current subject checks, and shared class-booking writes. Its current identity revalidation is stronger than the generic browser-session behavior found in A01/A02. The staff workspace still inherits the website's security and workflow defects.

The local mobile documentation describes a transition between the published installer and newer workspace behavior, and explicitly leaves phone acceptance incomplete. This audit did not install an APK, test iOS, revalidate signing, or verify the current public download. No mobile implementation was changed, so mobile lint/typecheck was not claimed.

The turnstile module explicitly monitors HID events and does not implement confirmed physical OPEN/DENY output. Logical permission decisions and recorded “granted” events do not prove that a person entered through a working gate. Keep physical actuation and observed attendance distinct.

Sources: [mobile_api.py](onecpase/mobile_api.py), [mobile README](mobile/README.md), [turnstile.py](onecpase/turnstile.py), [gunicorn.conf.py](gunicorn.conf.py).

Acceptance: actual-device sign-in, expired/revoked sessions, uploads/downloads, and member booking; separately verify the approved hardware protocol and an observed physical access outcome.

## 15. Architecture, release discipline, and recovery

**Assessment: the operational process has not kept pace with the number of features and one-off releases.**

**A11 — test discovery is currently broken.** Restrict the application test root explicitly and keep archived test/code copies out of discovery. Do not delete historical evidence to make collection pass. Re-run the required suite after that configuration repair; this audit did not alter it.

The working tree includes changes across shared database initialization, dashboards, Collections, DebiCheck, statement uploads, and tests, plus substantial untracked implementation and release material. That does not prove any change is wrong. It does mean that the old release manifest and old passing XML cannot certify this exact tree.

The release manifest describes an October 9 phase-3 snapshot. Later release documents and artifacts exist. A single clear release record should map the exact source revision or hashes, runtime configuration, migration result, tests, browser checks, deployment identity, and rollback/restore evidence. Historical documents also record production and beta sharing one service; current environment separation was not checked here, so that historical warning should be verified rather than assumed resolved or current.

The scheduler is embedded in the web worker and Gunicorn explicitly requires one worker to avoid duplicated jobs. That is documented, but it is a scaling and availability constraint. A13 adds volatile daemon-thread operations to the same process. Health checks confirm a default database table can be read; they do not establish tenant-by-tenant readiness, a running scheduler, fresh imports, working OTP, or successful providers.

Backup retention includes validation before pruning, which is a useful control. A validated backup file is still not proof of a successful restore with the original encryption key, uploads, tenant registry, and integration state. This audit did not perform a restore or contact a deployment target.

Sources: [pytest.ini](pytest.ini), [TESTING.md](TESTING.md), [release-manifest.json](release-manifest.json), [RELEASE_REVIEW.md](RELEASE_REVIEW.md), [scheduler.py](onecpase/scheduler.py), [health.py](onecpase/health.py), [backup_retention.py](onecpase/backup_retention.py).

Acceptance: reproducible source/test/deploy identity, successful clean test discovery, observed CI for that identity, per-tenant smoke checks, stale-job visibility, and a timed restore exercise.

## Recommended repair order

1. Fix A01/A02 and add revoked-session regression coverage across reads and writes.
2. Repair activation transaction/recovery states, including confirmed-external-success/local-failure and interrupted-worker cases.
3. Bind Elev8 sources and caches to the intended tenant; prevent ambiguous identity reconciliation.
4. Correct cash/credit classification and net outstanding calculations, then verify mandate classifications.
5. Replace inconsistent Fitness eligibility and add server-side validation to Fitness and Operations; audit the same pattern in financial forms.
6. Repair test discovery, run the required suite, and tie the result to an exact release snapshot.
7. Replace hard financial deletes with reviewed correction behavior and introduce durable background-operation recovery.
8. Then improve large-list loading, UI hierarchy, missing-owner visibility, external-asset resilience, and actual-device/provider/hardware acceptance.

The application does not need another broad layer of features before these defects are resolved. It needs a short stabilization cycle with explicit exit criteria and evidence against the exact version being released.
