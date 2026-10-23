# 1Cpace V8: section review and implementation plan

Review date: 18 October 2026. Scope: the local working tree in `D:\Dev\1Cpace_V8`.

This document records the initial application-review phase: existing sections and a proposed implementation/consolidation plan, followed by cleanup candidates. Most sections already have implementations. The subsequent Android/iOS implementation is recorded separately in `MOBILE_IMPLEMENTATION.md`; its code changes are not part of this initial review snapshot.

Evidence: sidebar in `onecpase/templates/base.html`, blueprint registration in `onecpase/__init__.py`, route/service source, test filenames, `README.md`, `TESTING.md`, and `onecpase/phases.py`. This is a static review, not a production usage audit. All application sections are expanded below; Section 16 indexes every top-level Python module, its implemented routes and source-level dependencies. Proposed changes are not claims that those changes already exist. No live integrations, databases, credentials, or member uploads were accessed. Existing local edits were left intact. No application files were deleted or changed.

## How to use this plan

Each workflow below states current implementation, data/access where relevant, proposed work and acceptance/removal conditions. Section 14 classifies cleanup evidence; Section 15 orders implementation; Section 16 maps source handlers and Section 17 covers operational scripts. HR remains detailed alongside every other section.

## 1. Dashboard, navigation and daily work

### 1.1 Main dashboard

**Purpose:** provide signed-in staff an overview of membership, collection activity and trends. Department rules may redirect staff to their permitted landing page.

**Current implementation:** `dashboard.py:dashboard()` renders the general dashboard. Helpers construct time windows, series and reconciliation. `member_counts.py` and `elev8_data.py` support counts. `/home` and `/dashboard` both exist and need a canonical-entry decision.

**Data/access:** member and collection records, configured external counts, current tenant and cache; login plus application-level department restrictions. Local and external totals may describe different populations.

**Plan:** define each card's population/date basis/freshness; link cards to matching filtered records; label unavailable sources; centralize count definitions shared with Reports. Preserve source discrepancies instead of adjusting numbers to agree.

**Acceptance/removal:** totals reproduce from detail lists for the same tenant/filter. Check empty periods, month boundaries and role redirects. Remove a card only when it repeats another with the same meaning and audience.

### 1.2 Sales dashboard

**Current implementation:** `/sales-dashboard`, `sales_kpi.py` and sales dashboard template present funnel/consultant performance. Application updates record checklist/status events through KPI helpers.

**Plan:** distinguish attempts, contacted prospects, join agreements, submitted applications and activations; document event-date versus current-status measures and consultant attribution after transfers. Provide consistent drill-down populations.

**Acceptance:** repeated activities do not create repeated sales; agreeing to join is not counted as activation; declined applications reconcile with the funnel. Keep separate from the general dashboard when it serves sales work.

### 1.3 Member reconciliation

**Current implementation:** dashboard reconciliation GET and POST sync paths compare local/external membership and apply sync through helpers; missing external scripts have a dedicated error.

**Plan:** separate comparison from apply; show match key, unmatched records, field differences and source time; define authoritative fields and protect local custom agreements. Preserve run results and uncertain outcomes.

**Acceptance:** repeated sync is safe, ambiguous identities are reviewed and unavailable drivers cannot look like a successful empty import. Keep reconciliation even if its summary moves onto another screen.

### 1.4 Navigation, roles and phases

**Current implementation:** `base.html` combines permission/phase checks, unread counts, notifications and follow-ups. `__init__.py` additionally enforces department profiles and closed phases on direct requests. `phases.py` defines cumulative opening; HR is always available subject to access controls.

**Plan:** map navigation and landing page for each role/phase; choose one primary destination per workflow; consolidate the duplicate Access Report link. Review the Collections navigation key `connections` before renaming because saved state/JS may depend on it.

**Acceptance:** authorized tasks remain reachable and unauthorized bookmarked URLs fail appropriately. Keep server-side enforcement; menu simplification must not weaken it.

## 2. Sales: prospect to activated member

### 2.1 Lead capture, identity and source

**Purpose/current:** `leads.py` supplies register, add/edit/detail, status and activity actions. Source choices include walk-in, referral, outreach, social and website; helpers normalize phone, find duplicates and allocate consultants.

**Data/access:** `leads`, `lead_activities`, users, tariffs and member/application links. View uses `view_leads`; writes principally use `capture_leads`, with additional application-level staff restrictions.

**Plan:** document required contact/source/consent and identity rules; share intake writes across manual, public, OCR and social capture. Distinguish existing members from duplicate prospects; preserve original attribution and define unassigned work when no consultant is available.

**Acceptance:** normalized repeat contacts do not silently create another active prospect; owner/source remain traceable. Check phone formats, missing consent and reassignment. Keep channels; consolidate their duplicated capture logic.

### 2.2 Public enquiry forms

**Current:** `public_leads.py`, `/join/<tenant_slug>/<channel>`, resolves an active tenant from the URL. Channels are facebook, instagram, tiktok and website. Training/joining preferences and contact consent are captured; honeypot/rate limiting exist. Repeated enquiries append activity to an existing lead.

**Plan:** keep URL tenant selection independent of a visitor's remembered staff tenant; reuse intake validation/assignment; return a simple success state without exposing whether a contact already exists.

**Acceptance:** invalid tenant/channel fails; repeat input retains original attribution and tenant separation. Use public-lead tests. This captures an enquiry, not a completed membership.

### 2.3 Assignment, contact and activity history

**Current:** activities include calls, WhatsApp, email, follow-up, appointments, visits, consultation, decisions and notes. Outcomes influence status and next action. PBX logs support calling context.

**Plan:** specify outcome-to-next-action rules and ownership; centralize them for list/detail/scheduler; distinguish pending tasks, completed attempts and successful contact. Retain actor/time and define PBX/manual-call deduplication.

**Acceptance:** completing one activity updates only its intended work; repeat no-answer, callback and wrong-number outcomes behave consistently. Keep history; remove redundant capture widgets after choosing a canonical interaction.

### 2.4 Qualification and stage transitions

**Current:** `sales_pipeline.py` defines allowed stages and transition, qualification, application invitation and appointment actions. Lead-status labels coexist with a richer pipeline including qualification, confirmation, consultation, application milestones and activation.

**Plan:** document the mapping between both progress representations, evidence and side effects; use a shared transition service; explicitly handle walk-in direct-show, no-show, recycle and lost outcomes. Preserve historical events on corrections.

**Acceptance:** forged POST cannot skip required evidence; board, register and KPI agree. Use sales-pipeline/CRM/application tests. Neither representation can be deleted before migrating callers/data.

### 2.5 Appointments, consultation and decision period

**Current:** activity outcomes cover schedule/confirm/reschedule/cancel, arrival/no-show and consultation. UI labels use five working days for decisions; legacy identifiers retain some seven-day wording. Scheduler has decision follow-up work.

**Plan:** calculate deadline/timezone once; preserve identifiers through explicit migration, not text substitution; complete/replace prior appointment tasks on reschedule; display actual due date and owner.

**Acceptance:** no duplicate active appointments or inconsistent follow-up deadlines; no-show/recycle paths remain actionable. Legacy identifiers are cleanup candidates only after schema/caller checks.

### 2.6 Recycle and contact restrictions

**Current:** recycle reasons have different return delays; do-not-contact, duplicate and existing-member are separate outcomes. Legacy reasons remain explicitly supported.

**Plan:** distinguish timed recycle from contact restrictions; validate outcome reasons; preserve source/timeline when reopening; ensure scheduled jobs cannot revive restricted contacts as ordinary callbacks.

**Acceptance:** work returns once at the intended date and restricted contacts stay excluded. Do not merge distinct terminal outcomes merely to shorten the status list.

### 2.7 Call-list upload and OCR

**Current:** `call_list.py` and parser provide upload, review, row correction and selected import. Templates live under `call_list`; `capture_leads` guards the workflow. OCR has an external engine dependency.

**Plan:** document formats/runtime; retain upload provenance; validate corrected rows before import and report duplicate/existing-member rows separately. Reuse intake identity/assignment and make re-import safe.

**Acceptance:** review precedes import, poor OCR remains correctable and repeat import does not create duplicate leads. Keep OCR only on supported deployments using it, after startup/import dependency checks.

### 2.8 Lead conversion and direct member creation

**Current:** conversion links sales to member/application creation; `members.py` also creates members. Progress distinguishes agreed-to-join, application started and activated.

**Plan:** share one creation service for identity, package/tariff, consultant and application evidence structure; keep different entry screens only for different business situations. Repeated conversion should return existing linked records.

**Acceptance:** double-click/retry cannot create duplicate members/applications; source/timeline persist and both entry paths produce consistent records. Check conversion, members, custom package and pipeline suites.

### 2.9 Application evidence and readiness

**Current:** `applications.py` supplies list/detail, checklist, analysis, approvals and push. `membership_applications`, `compliance_checklists`, `approvals`, documents, signatures, analyses and audit logs support draft-to-active progression.

**Access/rules:** view uses `all_members`, evidence/push uses `add_member`, decisions use `application_approvals` plus internal checks. Generic checklist updates cannot manufacture signed contracts, approved mandates, analysed statements, verified IDs or paid POS evidence; their source workflows own those states.

**Plan:** centralize readiness as structured blockers used by list/detail/push; link each blocker to its resolution workflow and source evidence; preserve controlled decisions on partial updates and record status changes consistently.

**Acceptance:** forged form values cannot bypass readiness; guardian, payment/evidence failure and approval scenarios remain blocked correctly. Use application and push-gate suites.

### 2.10 Approval, decline and external activation

**Current:** `integrations.py` loads external Itensity drivers, prepares data and pushes members. Successful push links the external reference and activation; failures remain retryable according to the documented workflow. Drivers are external deployment dependencies.

**Plan:** separate approval, submission attempt and activation; preserve payload identity/result/reference; recheck readiness at push time; distinguish confirmed rejection from timeout/unknown external outcome before retrying a create.

**Acceptance:** repeated push cannot create duplicate external members; ordinary evidence save cannot overwrite approval; missing driver/failure is visible. Keep approval and activation as distinct milestones.

### 2.11 First-month failures

**Current:** `first_month_collections.py` provides consultant-facing early-failure work alongside the shared collection engine.

**Plan:** define first-debit window/owner from shared case/payment facts; transfer responsibility with history; link the sales queue to the existing collection case rather than maintain another balance.

**Acceptance:** sales and collections agree on amount/status/owner; two views do not generate duplicate cases/contact tasks. Keep the role-specific queue when consultants own early failures.

## 3. Communication and calling

### 3.1 Staff messages

**Current:** `internal_comms.py` stores `staff_threads`/`staff_messages`, normalizes participant order for a unique pair and tracks unread messages. Login is required.

**Plan:** verify participant eligibility/tenant, concurrent thread creation and inactive staff behavior; keep read state separate from task acknowledgement.

**Acceptance:** another pair's conversation is inaccessible; reversed participant order does not duplicate a thread; reading clears only the correct recipient's count. Keep separate from external conversations.

### 3.2 Announcements

**Current:** announcements/read records support manager/admin publishing, pin/archive and staff reading. Explicit content limits and active-unread counts exist.

**Plan:** define audience and edit/renewed acknowledgement behavior; preserve per-user read history. Do not equate a read receipt with completed operational work.

**Acceptance:** non-publishers cannot mutate through direct POST; archives leave active counts and tenant-specific reads remain isolated. Retain if staff notices are used.

### 3.3 Reception and sales WhatsApp

**Current:** `comms.py` supports separate number-keyed inbox, compose/chat and lead-link actions; helpers derive number-specific permissions. `whatsapp_contacts.py` supports matching; conversations/messages persist locally.

**Plan:** define queue ownership/handoff/unread state; normalize phones; reuse contact identity rather than infer from a display name. Share components while retaining permissions and number-specific history.

**Acceptance:** changing conversation/number ids cannot cross access boundaries; linking uses the existing prospect/member. Check inbox/compose/contact-link tests. Two team queues are not inherently duplication.

### 3.4 WhatsApp transport and fallback

**Current:** Meta sending is in `comms.py`; `whatsapp_web.py` owns a linked-browser worker, `whatsapp_receive.py` incoming processing and `whatsapp_selenium.py` a configurable Selenium adapter. Playwright and Selenium paths both have callers/tests.

**Plan:** document selected transport per tenant/number; define message identity, retry and supported delivery evidence; serialize browser work; retain session credentials outside source; reconcile echoes/reconnects.

**Acceptance:** reconnection/retry cannot resend old messages and failure cannot become delivered. Keep both adapters until actual configuration/usage and delivery verification prove one can be retired.

### 3.5 Webhook ingestion

**Current:** WhatsApp/Facebook machine routes are CSRF-exempt and use signature, tenant-binding and event-claim helpers.

**Plan:** verify raw-byte signatures, configured provider identity and atomic event claiming/processing; distinguish malformed input from transient failure and define replay/retry behavior.

**Acceptance:** bad signatures/unknown tenant identities cannot write data and replay does not duplicate messages/leads. Use webhook idempotency and security suites; never remove verification during connector simplification.

### 3.6 Facebook messages and comments

**Current:** inbox/reply, flagging comments and comment-to-lead actions use `facebook_messages`/`facebook_comments` permissions and external credentials.

**Plan:** preserve public/private context; retain external comment identity on conversion; make repeated conversion safe; show unconfigured channel state. Gather usage before deciding retirement.

**Acceptance:** one comment cannot become repeated prospects and replies target the correct tenant/conversation. Disable unused channel UI only with an explicit handling plan for incoming webhooks.

### 3.7 PBX calls and codes

**Current:** `pbx.py` handles integration/call records; Admin manages staff PBX codes; sales reads `pbx_call_logs`. Production startup checks for a PBX secret.

**Plan:** document event id, staff-code mapping, phone normalization and call/talk-duration definitions; deduplicate provider retries and match leads/cases conservatively. Surface unknown staff codes as reviewable records.

**Acceptance:** replay cannot inflate activity and missing mapping does not assign a call to the wrong person. Use PBX CRM/account-code suites. Keep setup distinct from operational call views.

### 3.8 Shared collection wording

**Current:** `communication.py` formats care/offer text and email HTML; collection routes orchestrate sends and history. This is not a duplicate of inbox/provider orchestration.

**Plan:** generate amounts/dates from the same reviewed offer; bind preview/send to that offer version; record actor and actual transport outcome. Define invalidation of previews after an offer changes.

**Acceptance:** wording matches the arrangement, failed send is not recorded as delivered and retries retain one send identity. Keep reusable content helpers.

## 4. Members, evidence and Queries

### 4.1 Member register/profile

**Current:** `members.py` exposes directory, add/edit/detail, notes/documents and mandate checking. `members` is shared identity across applications, collections, fitness and queries. Viewing uses `all_members`, creation/edit uses `add_member`; private fields use decryption helpers.

**Plan:** assign authoritative owner to each local/external field; share validation with conversion; preserve negotiated package amounts/duration when tariffs or imports change. Organize profile panels around identity, membership, evidence, account and work history.

**Acceptance:** one identity across modules, no import overwrites protected custom agreements, and direct requests enforce permissions. Use member/list/custom package/security suites.

### 4.2 Notes and documents

**Current:** member routes append notes and upload/download/analyse/delete documents. Metadata is in `member_documents`; configured uploads hold files. Types include ID, statement, address, contract, mandate and clearance/PAR-Q.

**Plan:** define document type/owner/version and verification authority; validate member/document relationship, storage path and content constraints; make evidence removal invalidate dependent readiness appropriately. Preserve attributable removal/correction history.

**Acceptance:** changed ids cannot retrieve another member/tenant's file, paths stay within upload storage and missing evidence cannot leave false eligibility. Retain business evidence; deduplicate only under a reviewed retention rule.

### 4.3 Shared statement analysis

**Current:** `statement_uploads.py`/`statement_parser.py` support several intake/finance paths, PDF batches and persisted analyses. Overlapping statement months must not become multiple months of income.

**Plan:** share one upload/parse/qualification contract; separate raw extraction, normalized transactions, computed observations and human decisions. Preserve source-file links, parser version, unknown values and failures.

**Acceptance:** identical evidence gives consistent outcomes across entry points; overlapping PDFs cannot manufacture history; unreadable input requests review. Use parser/cashflow/intake/batch suites. This documents software behavior, not financial advice.

### 4.4 External member imports

**Current:** `elev8_data.py`, `elev8_sync.py`, Itensity adapters and scripts read/reconcile external records; ODBC and external driver availability matter. Scheduler also imports when dependencies exist.

**Plan:** inventory identifiers and field precedence; use shared matching for manual/scheduled paths; preview destructive differences and preserve custom local fields; record source/run times and uncertainty.

**Acceptance:** re-import is safe, ambiguous identities are not guessed and source outage cannot remove local members. Keep dependencies required by any supported deployment.

### 4.5 Query capture and ownership

**Current:** `queries.py` supplies list/add/detail, notes, update/close/escalate, approval and PTP conversion; `query_routing.py` handles routing. Categories span membership, payments/PTP, access, compliance, cancellation/freeze/refund, sales, facility/classes/PT, complaint/incident and general.

**Data/access:** `queries`, `query_notes`, member, user, document, mandate/PTP links. Most actions use query permission; decisions additionally use manager/admin role checks.

**Plan:** define category-specific fields, owner/department and state transitions; preserve reference/action timeline; handle unavailable owners; distinguish general ticket status from payment progress.

**Acceptance:** no invisible ownerless cases, category changes re-evaluate requirements and notes retain actor/time. Keep case tracking separate from enduring member identity.

### 4.6 Cancellation, freeze and financial decisions

**Current:** account summaries and shared arrears/PTP decisions support financial categories; cancellation calculation has supported contract terms. Closure/approval handlers enforce blockers.

**Plan:** show input dates, contract basis, outstanding amount, evidence and approval; distinguish quote, approved action and completed external update. Preserve cancellation history and follow-up for mandates/provider membership.

**Acceptance:** unmet evidence cannot close the case, unverified payment cannot settle debt and an internal decision cannot imply confirmed external cancellation. Use cancellation/query/rule suites. Policy/legal validity is outside this source review.

### 4.7 Follow-up, escalation and approval

**Current:** follow-up counts feed shared badges; routing/notifications involve owners, supervisors and configured/named staff. Approval is a distinct case action.

**Plan:** define how follow-ups start/replace/resolve, escalation ownership and matching count scopes; preserve decision reasons. Align notification links with actual decision permissions.

**Acceptance:** resolving one task cannot suppress another; reassignment moves intended future work and direct decision URLs enforce policy. Use routing/notification/workflow tests.

### 4.8 Query/PTP linkage

**Current:** `link_ptp()`, `sync_from_ptp()` and approved-discount helpers connect queries and arrangements; paid/broken/superseded outcomes influence query progress.

**Plan:** share arrangement creation, retain query/approved-offer reference and define supersession. Retry must return existing linkage rather than create another agreement.

**Acceptance:** current arrangement is identifiable and updates affect the correct query. Keep both records because one tracks service work and the other financial commitment.

### 4.9 Turnstile and credentials

**Current:** `turnstile_routes.py` provides view/scan (`all_members`) and assignment (`add_member`); `turnstile.py` monitors devices and records events.

**Plan:** define credential uniqueness/reassignment history and unknown-card review; retain raw event identity separately from member linkage; report device/source errors and access reasons.

**Acceptance:** unknown credential does not silently authorize access, duplicated events do not duplicate work and reassignment does not rewrite past attendance. Keep separate from read-only Access Report.

## 5. Collections and Promise to Pay

### 5.1 Case engine and shared facts

**Current:** `collections_engine.py` owns case/policy/workflow logic; workspace reads facts, decision helper calculates offers and `collections.py` supplies routes. Tables cover cases, payments, stage/assignment/communication histories, reminders, discounts/exceptions; legacy collections and PTP/mandate facts also remain.

**Plan:** map authoritative amounts/statuses; share facts across Collections/Queries/PTP/Reports; document legacy/current relationships and retain policy version/input evidence on decisions.

**Acceptance:** one member has consistent debt/offer across screens and historical rows are not counted twice. Keep route, rule and read-model responsibilities separate.

### 5.2 Dashboard, queue and assignment

**Current:** dashboard/queue/report use cases, including mine, due/broken PTP, missing mandate, approval, restricted and ageing filters. Main permission is `all_collections`.

**Plan:** make case queue primary daily entry, define ordering/transfers and show reason/owner/next action/last contact. Cards must share the same filter population as drill-downs.

**Acceptance:** explicit ownership and matching workload counts, preserved transfer history and no duplicate cases from viewing/refresh. Role-specific filtered views can remain.

### 5.3 Calling and outcomes

**Current:** `collections_call_queue` guards queue/profile, result, upload, NuPay check, preview/message and escalation actions. Outcomes distinguish successful contact from no-answer/busy/voicemail/wrong-number; reminder delivery has a separate action.

**Plan:** centralize outcome side effects and deduplication; preserve attempt versus contact versus verified payment; bind actions to the correct task/case and record next owner/date.

**Acceptance:** repeated submit cannot duplicate financial/contact effects and unsuccessful calls do not count as successful recovery. Use call-queue/outcome suites.

### 5.4 Offer and discount decisions

**Current:** `collections_decision.py` distinguishes eligibility, conditions, no-discount and manager review using shared arrears policy. Promised upfront money differs from verified receipt. Several rule/configuration sets exist, requiring active-path tracing.

**Plan:** trace effective policy across call/Query/PTP flows; version rules; replace duplicated live calculators only after equivalence checks. Preserve quote, approval, receipt and settlement as distinct states.

**Acceptance:** preview/final agreement agree for identical inputs, unknown/stale evidence is visible and promises never count as received cash. Use decision/category/discount/enforcement suites before policy changes.

### 5.5 Arrangements and installments

**Current:** `ptp.py` handles full, split, top-up, extension and end-of-term arrangements, dates/methods, follow-up and status. `ptp_grant.py`, payment-plan items and mandate links support granting/collection.

**Plan:** specify requirements per type; generate schedule through one service; retain original debt and agreed offer separately; explicitly supersede replacements and show outstanding conditions.

**Acceptance:** schedule totals reconcile, repeat create is safe and replacement preserves previous history. Retire types only after usage/data migration, not because the form is long.

### 5.6 Receipts and settlement

**Current:** upload, upfront recording, receipt verification and status changes are separate actions; manager/admin verify receipts. `ptp_receipts`, payment-plan items, collection payments and documents link evidence.

**Plan:** establish one payment identity/verification service; bind proof to member/arrangement; distinguish rejected proof, verified payment and reversal; avoid counting manual and imported copies twice.

**Acceptance:** upload alone cannot settle debt, one payment credits once and partial payments leave the right balance. Check receipt/settlement/import together.

### 5.7 Monitoring and reminders

**Current:** statuses include pending/partial/paid/broken/rescheduled/cancelled/escalated/superseded. Automation preview/run and scheduled jobs maintain work; reminder delivery is explicit.

**Plan:** define timezone/cutoff and late-import handling; keep preview read-only and jobs idempotent; stop obsolete reminders for settled/superseded agreements.

**Acceptance:** repeated daily runs do not duplicate reminders or revert settlement. Use monitoring/automation/scheduler suites.

### 5.8 Access decisions

**Current:** PTP/member access changes and `access_decisions` connect account conditions to access; manager/admin routes control changes.

**Plan:** share one decision service, record reason/exception and distinguish internal status from external sync confirmation. Preserve authorized exceptions across automatic recomputation.

**Acceptance:** restoration has required evidence and an internal edit is not falsely represented as confirmed device/provider update. Keep access decisions distinct from scan evidence.

### 5.9 Legal referral and exceptions

**Current:** referral create/list/resolve and manager/admin exception decisions maintain separate review records.

**Plan:** document eligibility/evidence/reviewer, require explicit referral, give exceptions owner/reason/next action and link back to case/offer.

**Acceptance:** decisions are attributable, failure is retryable and resolution retains debt/history. They are not duplicate queues simply because both need review.

### 5.10 Payment import/reconciliation

**Current:** `nupay_transactions.py` plans/applies import; `nupay_reconciliation.py` stores/compares source transactions; UI is in `reconciliation.py`. Comparison storage is explicitly separate from payment application.

**Plan:** retain run/transaction identities; separate source, matching, comparison and applied credit; quarantine ambiguity; distinguish failed debit from receipt/reversal.

**Acceptance:** repeated files cannot credit twice, unmatched items remain visible and comparison does not silently mutate payment history. Use transaction/reconciliation/sync suites.

### 5.11 Legacy forms and reporting

**Current:** collection add/edit/delete (`new_collection`) remain alongside the case workspace; reporting reads legacy collections too.

**Plan:** trace all readers/writers before routing forms through a shared payment service; compare totals and preserve historical reports. Review deletion semantics and replace with attributable corrections if required.

**Acceptance:** no competing balances and every account amount explains its source. Legacy list/form is a consolidation candidate; history is not disposable clutter.

## 6. Finance and debit mandates

### 6.1 Register and coverage

**Current:** `debicheck_routes.py` supports register/coverage/add/edit/detail/requirements/documents/push/review/status/sync. View uses `debicheck_mandates`, writes principally `new_debicheck`. Mandates link members, applications and PTP.

**Plan:** distinguish draft, provider status and usable coverage; show reference and last external check; keep encrypted fields within authorized rendering.

**Acceptance:** a local row alone never proves approval, and every coverage state explains current provider/evidence facts. Check route/coverage/member-check suites.

### 6.2 Details and debit-date rules

**Current:** `debicheck_rules.py` validates holder naming and minimum first debit date (South Africa business date plus eight days); JS supports forms but server rules apply. `employee_numbers.py` supplies loader attribution from current user.

**Plan:** reuse rules in add/edit/push/provider preparation; revalidate delayed drafts on submit; show earliest valid date and preserve entered fields after error.

**Acceptance:** bypassing JS cannot submit invalid dates/names and hidden form values cannot impersonate a different loader. Keep attribution and validation shared.

### 6.3 Evidence and submission gates

**Current:** shared requirements/checklist, submission blockers and admin override helpers combine documents/statement analysis with member/application facts.

**Plan:** expose structured blockers, show source document/analysis, keep overrides attributable and prevent source-controlled states being forged through generic forms.

**Acceptance:** all submission entry points apply identical gates; overlapping/partial batches cannot fake missing evidence. Use statement-gate/batch/intake suites.

### 6.4 Provider push, manual review and sync

**Current:** `nupay_driver.py`, `nupay_sync.py` and background review helpers perform provider work. External references and worker runtime are dependencies.

**Plan:** define attempt identity, locking, timeout/unknown states and resumable checks; recheck current mandate/member version before applying a late result; record provider evidence without credentials.

**Acceptance:** retry does not create two mandates, stale background results cannot overwrite newer decisions and a review action is not displayed as approval. Use driver/sync/concurrency/cancellation cases as applicable.

### 6.5 Finance boundaries

**Current:** debit/collection/evidence workflows exist; the source does not establish a full general ledger, invoice/tax/accounting system. Access Report is duplicated in Finance and Reports navigation.

**Plan:** label actual finance tasks clearly, put Access Report primarily in Reports and define accounting integrations separately if needed.

**Acceptance:** mandate, receipt, debt and access scan are not conflated; menu duplication is removed without deleting reporting capability.

## 7. Fitness and onboarding

### 7.1 Classes/bookings

**Current:** `fitness_classes`/`class_bookings` and list/add/edit/book/cancel routes. `classes` guards bookings/view; `fitness_module` guards setup.

**Plan:** specify capacity, schedule, instructor, eligibility and cancellation state; enforce duplicate/capacity checks server-side; establish recurrence requirements before extending.

**Acceptance:** simultaneous booking cannot oversubscribe, retry cannot duplicate and cancellation targets only the intended record. Keep group classes separate from onboarding visits.

### 7.2 Joining cohort and daily onboarding

**Current:** `onboarding.py`, `fitness_cycle.py`, joiner/today screens, arrivals and visit records use members, `member_onboarding`, `onboarding_visits`, events and sync state.

**Plan:** centralize eligibility/cohort dates across today/joiners/reports; distinguish arrival, assignment and completed assistance; deduplicate manual/access-derived arrival.

**Acceptance:** one cohort rule and one journey per intended membership; trainers see pending/unassigned work. Keep one primary daily entry rather than competing overview screens.

### 7.3 Intake and welcome

**Current:** `fitness_journey.py` handles intake, welcome eligibility/queue/send and sign-off using intake/policy/member/WhatsApp records. Dedicated `intake_access` controls entry.

**Plan:** define reception versus trainer responsibilities; preserve required intake/consent/source; queue one welcome per intended journey; show retryable send state and link from member profile.

**Acceptance:** refreshing cannot resend a welcome and role-specific intake cannot alter unrelated coaching evidence. Keep identity capture linked to the existing member.

### 7.4 Visit coaching, assessment and signature

**Current:** visit recording captures welcome/introduction/demo/assistance/independent/PT/follow-up outcomes; assessment and `fitness_signoffs` provide evidence.

**Plan:** specify required fields per visit, instructor attribution and signed snapshot/correction behavior; share assessment storage/validation.

**Acceptance:** complete visits have required evidence, changed ids cannot sign another member's visit and later edits do not silently rewrite signed meaning.

### 7.5 Review and onboarding reports

**Current:** review outcomes identify independent training, extra assistance, PT/classes/programme needs. Reports group eligible periods and instructor sessions/members.

**Plan:** define whether review deadline begins at joining/eligibility/first visit; create actionable follow-up for unresolved needs; count unique members separately from sessions.

**Acceptance:** report totals reconcile to visits/cohort and unresolved needs remain in daily work. Preserve review as a distinct milestone.

### 7.6 Assessments

**Current:** assessment records include member, assessor/date, weight, fat, muscle, BMI, fitness level and notes; list/add use `assessments`. List currently takes latest 50.

**Plan:** share onboarding/standalone validation, units and identity checks; define correction history and optional pagination/filtering rather than assuming complete history appears in the latest list.

**Acceptance:** both capture paths show consistent dated member history. These are software records, not independently validated clinical conclusions.

### 7.7 Workout plans

**Current:** member/trainer/title/description/dates/active state in `workout_plans`; list/add use the corresponding permission and latest-50 listing.

**Plan:** define active/replacement/end-date behavior, date validation, assessment link and review deadline; scope structured exercises separately from current description fields.

**Acceptance:** current and historical plans remain identifiable and attributable; replacement does not erase history. Keep distinct from reference anatomy content.

### 7.8 Dailies

**Current:** `instructor_dailies` and dailies list/add record operational fitness activity, separately from signed HR time.

**Plan:** map each field to a management use; derive existing visit/session counts automatically where practical; retain meaningful manual additions only.

**Acceptance:** no duplicate counting and activity is not labelled approved payable hours. Consolidate redundant capture after checking usage, not the entire feature by assumption.

### 7.9 Incidents

**Current:** incident list/add/update, severity/risk display and prepared WhatsApp wording use `incidents`/`incident_log`.

**Plan:** define owner/status/action/closure evidence, sensitive access and attributable updates; link to Queries or equipment work rather than copy the same incident identity.

**Acceptance:** unresolved incidents remain actionable and sending a message cannot itself resolve the incident.

### 7.10 Body matrix

**Current:** register/member/add routes store measurement history alongside overlapping assessment measurements, under `body_matrix` permission.

**Plan:** map shared/unique measurements/units; share capture/calculation service while keeping a useful progress view; preserve source/date during migration.

**Acceptance:** the same measurement is not entered twice merely to fill two screens and historical trends reconcile to assessments. Consolidate storage only after a migration plan.

### 7.11 Nutrition catalogue — corrected scope

**Current:** `nutrition_foods` is a shared reference catalogue, filtered by macro/micro category/type; add captures serving size, calories and nutrient quantities. These handlers do not establish individualized meal plans. Permission is `nutrition_guide`.

**Plan:** label accurately, validate category/types/numbers and define content ownership/review. Add member-specific planning only as explicit future scope.

**Acceptance:** reference filters/content are consistent. Optional if trainers use another maintained resource. The earlier summary overstated this feature; this description corrects it.

### 7.12 Supplements catalogue/assignment

**Current:** supplements have brand/type/goal/risk/description/dosage; `member_supplements` has dated assignment, attribution and active state. Permission is `supplements`.

**Plan:** validate ids/dates/risk, define expiry/review and content owner; distinguish assignments from stock/sales, which these handlers do not demonstrate.

**Acceptance:** member and catalogue histories agree and expired status is coherent. Optional based on usage; this plan makes no supplement recommendation.

### 7.13 Anatomy reference

**Current:** `fitness_anatomy.py`, images and anatomy route provide trainer reference under `fitness_module`.

**Plan:** verify region-to-asset mapping, reference maintenance and mobile behavior; audit unused asset variants separately.

**Acceptance:** visible regions resolve correctly. Optional reference scope can be retired independently of core intake if usage proves it unnecessary.

## 8. Operations and maintenance

### 8.1 Equipment register

**Current:** `operations.py` searches/adds/edits equipment, condition and active state under `equipment` permission. Types span cardio/weights/machines/stretching/functional/group/general; active condition totals support the list.

**Plan:** define unique asset identity, location/serial requirements, condition/type validation and retirement history; connect repair/out-of-service condition to work records.

**Acceptance:** similar names cannot conflate assets, summaries reproduce from records and inactive equipment retains maintenance history. Keep if this is the gym's operational asset register.

### 8.2 Maintenance history and next due

**Current:** `maintenance_log` records asset/date/description/performer/cost/next due/notes/creator; list totals cost. `maintenance` guards actions. Description is required; cost is parsed as float with broad insertion-error handling.

**Gap/plan:** validate real asset, finite non-negative cost and dates; define planned versus completed work and explicit condition updates. A next-due field alone is not a complete scheduling/reminder system; build a due-work view only if required.

**Acceptance:** malformed amounts/dates cannot create misleading history and totals are labelled recorded maintenance cost, not accounting postings.

### 8.3 Links to incidents/queries and scope

**Plan:** keep equipment maintenance authoritative for repairs, Queries authoritative for member communication and Fitness authoritative for incident follow-up; link records instead of duplicate capture. Add stock/procurement/supplier accounting only if explicitly required.

**Acceptance:** staff can follow complaint/incident through to actual repair resolution. Phase 7 closure does not prove equipment code is unnecessary.


## 9. HR and workforce — detailed implementation review

This section documents current source behavior and proposed work. Rules described below are rules implemented in this repository, not an independent assessment of employment-law compliance. No production data or permission assignments were inspected, and tests listed below were inspected rather than executed.

### 9.1 HR architecture: why this section is large

HR is not one screen. It currently combines account management, employment records, leave accounting, scheduling, signed time reporting, attendance evidence and payroll record status.

- `hr.py`: staff directory, account add/edit, employee register, manual attendance, payroll and compatibility routes.
- `hr_workforce_routes.py`: self-service and reviewed operations exposed under the same `/hr` URL prefix.
- `hr_workforce.py`: accounting, dates, permissions, transactions, leave decisions and timesheet rules.
- `hr_access.py`: shared HR navigation/access policy and restricted payroll identity checks.
- `staff_attendance.py`: read-only calculations from access-card snapshots.
- `templates/hr/`: directory, employee, setup, balances, leave, calendar, review, roster and weekly-timesheet screens.
- `notifications.py` and `staff_notifications`: reminders connected to HR decisions.

The split into routes, rules and evidence processing is useful. Reducing the number of files would not by itself simplify the business workflow.

### 9.2 Staff directory and login-account administration

**Purpose and users:** staff look up colleagues; authorized account administrators create or maintain logins. The directory and account editing are different privileges.

**Current implementation:** `hr.staff_index`, `/hr/`, searches `users` by name, username or department. `staff_add` and `staff_edit` create/update login accounts and selected permissions. Creation requires username, full name, password and valid email; passwords are hashed. Editing can update department, role, email, active status, password-change requirement, optional replacement password and permission rows. Editing replaces the selected `user_permissions` set.

**Data:** `users` and `user_permissions`; these are account records, not the full employment record. The directory query selects all users rather than the active named-employee filter used by workforce setup.

**Access:** directory navigation uses `hr_staff`; add/edit routes use `user_management`. Broad HR navigation availability must not be mistaken for permission to edit accounts.

**Overlap and uncertainty:** Admin also has user administration. Two account-editing entry points create a maintenance risk; this review has not established full field/validation equivalence. Directory inclusion of inactive/system accounts should be reviewed against its intended audience.

**Implementation plan:**

1. Compare HR and Admin account forms, fields, permission replacement and validation line by line.
2. Choose Admin as the proposed canonical account editor, while retaining HR's employee-facing directory.
3. Extract shared account mutations if both entry points must remain; otherwise redirect old add/edit links with the target user preserved.
4. Define directory inclusion and which contact/account fields normal staff may see.
5. Check active/inactive employees, system accounts, password reset and permission revocation. Acceptance: both navigation and direct requests apply the intended permissions, and only one maintained implementation writes account changes.

### 9.3 Employee register and identity linking

**Purpose:** hold employment details independently of login credentials and link them reliably to workforce records.

**Current implementation:** `/hr/employees` searches `staff`; add/edit handlers store names, identity number, job role, department, contact, email, start date, contract end, salary and status. Employee detail loads recent manual attendance, legacy leave and, for permitted payroll viewers, payroll records.

`ensure_profile()` in `hr_workforce.py` links an active named `users` record to `hr_profiles`. It automatically links a staff record only on a unique matching non-empty email and only if that staff record is not already linked. If there are no email matches, it creates a staff record. Ambiguous matches are not automatically resolved. The source explicitly avoids repurposing the external `users.staff_id` reference.

**Data relationship:** `users.id -> hr_profiles.user_id`; `hr_profiles.staff_id -> staff.id`. The profile staff link is unique. The `staff` record stores employment information; the profile stores workforce terms; `users` stores login identity and department/contact fields too.

**Specific gaps to resolve:** employee edit can change `staff.start_date` directly, while workforce setup has guards against changing a start date with confirmed leave history. Those are two write paths to employment information. Employee detail queries legacy `leave_requests`, while the new leave workflow writes `hr_requests`; the detail page therefore needs a deliberate unified history design.

**Implementation plan:**

1. Create a field-ownership map: account fields, employment fields, workforce policy fields and external identifiers.
2. Route changes to employment start, employment classification and normal working days through one guarded service.
3. Add a controlled resolution action for ambiguous/unlinked profiles; never infer identity from name alone.
4. Show new leave requests and clearly labelled legacy history on employee detail without double counting.
5. Verify changing an employee email or department cannot silently create a second staff identity. Acceptance: one employee profile per named login, preserved external references and consistent employment terms across screens.

### 9.4 My HR self-service

**Purpose:** give each employee one place to see their setup, leave balances, requests, weekly submissions and forthcoming shifts.

**Current implementation:** `/hr/me`, `workforce.dashboard`, calls profile creation/linking and renders `hr/me.html`. It reads current leave accounts with calculated balances, the latest 30 leave requests, latest 12 weeks and up to 14 upcoming published shifts. A service account is redirected toward setup rather than treated as a normal employee.

**Behavior to understand:** merely opening the dashboard can create/link the profile and staff record through `profile()` and `ensure_profile()`. This is a database write on a viewing path, not just a read-only dashboard. Missing confirmed accounts must remain distinguishable from a genuine zero balance.

**Implementation plan:** explicitly define onboarding/setup states; show missing employment terms and missing verified opening balances as actionable states; consider moving profile provisioning into account onboarding while preserving safe fallback for existing users. Acceptance: an ordinary employee sees only their own detailed records, receives usable setup guidance and is never assigned an invented balance.

### 9.5 Employment setup and reporting lines

**Purpose:** confirm the terms required before leave, scheduling and approvals can operate correctly.

**Current implementation:** `/hr/people` is HR-admin-only. It handles employment start, normal weekdays, manager, employee/contractor arrangement, 30/60 minute meal break and agreement reference. Setup GET also ensures profiles exist for all eligible named people.

**Rules currently enforced:**

- Profile version must match the submitted version, preventing a stale form from overwriting newer terms.
- Start date cannot be in the future in this active-employee setup flow.
- Normal working days must be Monday through Saturday; at least one is required.
- A manager must be another active non-platform user.
- A 30-minute break requires a recorded agreement reference.
- Start date cannot be casually changed after leave accounts exist.
- Working days cannot change while pending/approved future leave remains unresolved.
- Employment classification cannot change while confirmed leave accounts exist.
- Saving profile start date also updates the linked staff record and records `terms_updated` in the audit trail.

**Implementation plan:** unify the competing employee-register write paths; decide how future starters are represented before becoming active; validate manager assignments beyond simple self-reference if reporting chains are required; make setup completeness visible. Acceptance: all ways of editing employment terms preserve leave history and report the same validation errors.

### 9.6 Opening leave balances: propose and independently confirm

**Purpose:** establish a reconciled starting point from historical evidence instead of guessing an employee's available leave.

**Current implementation:** POST `/hr/opening-balances` stages a JSON proposal in `hr_openings`. It requires confirmed employee terms, a supported account type (annual, sick or family), a balance date between employment start and today, balance/taken quantities and a source/reconciliation explanation. Quantities use integer hundredths of a day.

Account cycles are derived from employment dates. Annual entitlement reads `hr_policy_settings.annual_working_days` when present and otherwise uses three times the weekly working-day count. Sick/family eligibility and opening limits are implemented explicitly, including verified days worked during the initial sick-leave period.

POST `/hr/opening-balances/<id>/confirm` requires a different HR administrator from the proposer. It rechecks pending status, profile version, current annual policy and uniqueness of the employee/type/cycle account. Confirmation creates `hr_accounts` plus opening and historical-taken ledger entries, then marks the proposal confirmed. The source reference and actor are retained.

**Important distinction:** this is an accounting import/reconciliation workflow. It is not a normal employee leave request and should not be combined with that screen merely to reduce navigation.

**Implementation plan:** retain independent confirmation; provide proposal detail with source evidence and before/after totals; design an explicit stale/rejected proposal path if required, since the inspected implementation principally covers pending/confirmed. Acceptance: repeated confirmation cannot import twice, the proposer cannot confirm their own proposal, and changed employment terms require renewed reconciliation.

### 9.7 Leave balance accounting and corrections

**Purpose:** explain entitlement, earned credit, leave taken, future reservations and the amount still available.

**Current implementation:** `/hr/balances` reads accounts and ledger history for an allowed employee. `account_balance()` combines dated ledger credits/debits, computed accrual and approved request dates. Future approved days are reserved; approved dates that have passed contribute to usage without requiring duplicate payment-like debit records. Old cycles remain visible rather than being silently discarded.

**Data:** `hr_accounts` identifies employee, type, cycle, opening as-of date and policy. `hr_ledger` records units, kind, unique reference, effective date, actor and reason. Annual accrual is computed at completed employment-month boundaries. Sick-balance transition logic preserves prior use and corrections.

**Corrections:** `/hr/balance-adjustments` stages a non-zero credit/debit proposal with evidence in `hr_openings`. A different HR administrator confirms it through the opening-confirm route. Confirmation appends an adjustment ledger entry and rechecks whether a debit would overspend available balance. Original entries remain intact.

**Implementation plan:** keep the ledger and reviewed corrections; add a visible calculation breakdown and distinguish current balance from available-after-reservations; verify cycle boundaries, first-six-month transitions, policy changes and rounding. Acceptance: every displayed number can be reconciled to dated inputs, and historical records are never overwritten to make a balance fit.

### 9.8 Leave request, approval, withdrawal and cancellation

**Purpose:** let employees submit their own signed request and let an authorized reviewer decide it with balance and cover evidence.

**Current entry points:** `/hr/leave` and `/hr/leave/request` remain in `hr.py` but delegate to workforce handlers. Approval/rejection compatibility routes also delegate to `decide_leave()`. New withdrawal uses `/hr/leave/<id>/withdraw`.

**Submission behavior:** the current employee requests annual, sick, family, parental, study or unpaid leave. Employment must be confirmed; contractors are restricted to the unpaid workflow. An attestation is required. Dates are calculated on the server using the normal pattern and published roster evidence; annual dates exclude configured/calculated public holidays. Overlapping submitted/approved requests and reused form tokens are rejected. The request stores calculated dates, quantity, policy, version and submission token, audits the action and notifies the manager.

**Decision behavior:** the reviewer cannot decide their own request. A non-admin reviewer needs both the approval permission and the employee's manager assignment. A decision note and matching version are required. Approval recalculates chargeable dates, checks an account spanning the whole request for annual/sick/family and checks available balance. Requests crossing account boundaries must be split. Annual leave cover/conflict checks can require HR intervention with a recorded arrangement. Decisions resolve the review notification and notify the employee.

**Withdrawal/cancellation:** employees can withdraw their own submitted request. Approved future leave requires a different HR administrator and a reason to cancel. Version checking prevents stale withdrawal actions. This is distinct from deleting historical leave.

**Implementation plan:** preserve this single decision engine and compatibility URLs; label legacy rows separately; make rejection/withdrawal/cancellation history clear; define operational handling for cycle-split requests and unsupported account states. Acceptance: duplicate submits and concurrent approvals cannot overspend balances, self-approval is rejected and cancelled future reservations are released correctly.

### 9.9 Team availability and review queue

**Purpose:** show team capacity and pending management work without exposing unnecessary personal leave details.

**Current implementation:** `/hr/leave/calendar` reads published shifts and submitted/approved leave for a selected week. Its source explicitly omits reasons, signatures and medical details. Access requires HR administration or a leave/timesheet approval permission. `/hr/reviews` shows the reviewable employees, weekly status, missing/submitted weeks and leave the actor may review; the default review week is the previous week.

**Specific review item:** `calendar()` passes `people(db)` and unscoped date-range leave/shift queries to its template, whereas detailed record access uses `visible_ids()`. The intended visibility of team-wide availability versus reporting-line-only availability must be explicitly decided and checked in the template. This is a scope review item, not a proven production data exposure from a completed runtime test.

**Implementation plan:** define the audience for team availability; apply the chosen scope in the query as well as display; keep personal reasons out; make the review queue the manager's primary entry for pending decisions. Acceptance: a reviewer can reach all assigned work and cannot obtain detailed records outside their permitted scope through changed URLs.

### 9.10 Roster, lunch coverage and relief

**Purpose:** publish workable shifts with breaks and departmental coverage, rather than recording isolated start/end times.

**Current implementation:** `/hr/roster` displays published rows to staff and includes drafts for HR administrators. Admins create draft shifts, delete unpublished drafts, publish a complete week or return a future published week to draft with a reason. One shift exists per employee/date.

**Draft validation:** valid employee/date and ordered intervals; Sunday closure; break duration; no more than the implemented continuous-work interval; opening/closing hours. Existing shifts must be removed as drafts before replacement rather than overwritten silently.

**Publication validation:** confirmed terms, weekly/daily ordinary-hour limits, approved leave, department lunch overlap, available department cover or explicit relief, relief not double-booked, relief's own department remaining covered and adjacent-day rest intervals. Publication checks the complete candidate set and commits the week atomically. Failures roll back the transaction. Returning a future week to draft records the previous published data in audit history.

**Implementation plan:** retain draft-to-publication validation; show which row and cover relationship blocks publication; reconcile department naming with attendance evidence; provide a preview of revised coverage before re-publication. Acceptance: no partially published week, no incompatible lunch relief assignment and a retained reason/snapshot for revisions.

### 9.11 Weekly timesheets and signatures

**Purpose:** have employees declare actual work/break times and have another authorized person approve or return that exact version.

**Current implementation:** `/hr/weeks` accepts date-specific clock-in, lunch-out, lunch-in, clock-out and notes. Time parsing requires HH:MM; intervals must be ordered; overnight shifts are unsupported. Work minutes exclude the entered lunch interval. Blank days have zero minutes. Warning calculations highlight long weeks/days, break/night-work/rest review items; warnings should not be described as automatic payroll calculations.

Only the employee can save their own week. Editing is allowed for draft/returned states with a matching version. Submission requires attestation and the full week to have ended. Saving increments the version, stores entries and a content hash. Submission writes `hr_signatures` with actor, version, declaration, hash, JSON snapshot and timestamp, and notifies the manager.

**State flow:** draft -> submitted -> approved or returned. An HR administrator may reopen an approved week with a reason, making it returned for correction; the old signature snapshot remains. Review checks a matching version and forbids self-approval. Review signatures capture the exact reviewed content.

**Legacy handling:** when no workforce week exists, old `timesheets` rows are shown as draft input requiring employee review/signature; invalid legacy intervals are marked for correction. `/hr/timesheets` redirects to weeks; old save returns HTTP 409; individual legacy staff-week URLs resolve the profile and redirect subject to visibility checks.

**Implementation plan:** retain signatures/versioning; explain edit locks and returned reasons in the UI; keep legacy input labelled; define payroll export only from the approved version if that integration is later implemented. Acceptance: stale edits fail, signed content remains attributable, changed versions need fresh submission and access scans never silently become declared hours.

### 9.12 Attendance evidence and manual attendance

**Purpose:** provide supporting presence evidence without pretending an entry scan proves departure, breaks or payable time.

**Current implementation:** `staff_attendance.build_week()` reads a snapshot, matches the requested week, removes exact duplicate events and groups repeated successful entry scans within 60 seconds. It separates denied scans, missing source days, missing card links, closed days and no successful entry. `clock_out`, lunch and verified hours remain unset. A policy-adjusted start can be shown without replacing the actual first entry.

The access-attendance view and timesheet evidence helper load snapshots, check tenant identity and handle missing/unusable files. Archived week snapshots may be selected. Shift-window labels in `staff_attendance.py` use snapshot assignments and department-specific templates, while published workforce rosters have their own validation and storage.

**Separate manual path:** `/hr/attendance/log` updates/inserts a current-date `attendance` record for a `staff` identity. These manual records differ from card snapshots and signed workforce weeks. Employee detail shows recent manual attendance.

**Implementation plan:** define each record's authority; keep manual corrections attributable; compare snapshot shift assumptions with published rosters and make discrepancies visible. Use one South Africa date helper across legacy attendance/payroll paths where appropriate; some legacy code uses host-local `date.today()`, whereas workforce `today()` explicitly uses UTC+2. Acceptance: missing data is never reported as confirmed absence, repeated entries never generate invented exit/break times and evidence cannot write approved timesheets.

### 9.13 Payroll and salary handling

**Purpose:** show existing payroll records for a month and record when an authorized person marks a record paid.

**Current implementation:** `/hr/payroll` joins `payroll` to `staff`, filters the month, calculates total net and pending count. POST `/hr/payroll/<id>/pay` changes status to paid with today's date. Employee detail loads payroll only for allowed viewers; employee add/edit initializes or preserves salary depending on payroll authorization.

**Access:** `can_access_payroll()` checks an active, non-platform user against two named staff identities resolved through `query_routing.staff()`. A general administrator role alone is not the described payroll rule. Workforce `is_admin()` separately includes role admin, payroll-authorized staff or `hr_manage`, so HR administration and payroll access are not identical concepts.

**Limits observed:** this handler marks a database record paid; it does not demonstrate a bank transfer, tax engine, payslip generation or an automatic approved-timesheet-to-payroll calculation. The inspected pay handler updates without a pending-state condition or explicit HR audit call; that is a concrete review item before relying on it as a controlled payment workflow.

**Implementation plan:** preserve the existing narrow salary/payroll gate until business authorization changes; document named access and account lifecycle behavior; add record existence/state validation and an attributable payment-status audit if modifying this workflow. Define who creates payroll amounts and what evidence permits marking paid. Acceptance: unauthorized users cannot view/change salary or payroll and a paid status is not presented as proof of a bank transaction.

### 9.14 HR access model

| Actor | Current source behavior | What must remain true during simplification |
| --- | --- | --- |
| Named active employee | Own dashboard, leave and weeks; published roster; broad HR entry labels are open to signed-in staff. | Cannot edit another employee's week or approve their own request. |
| Assigned manager with approval permission | Detail visibility extends to reports; review requires both permission and assignment. | Permission alone does not grant arbitrary employee review. |
| HR administrator | Role admin, payroll-authorized identity or explicit `hr_manage`, excluding platform users. | Sensitive employment/balance/roster operations stay guarded and auditable. |
| Payroll-authorized staff | Named active identity check in `hr_access.py`. | General HR access must not grant salary/payment access. |
| Platform/system identity | Platform users excluded from workforce; unnumbered generic admin treated as a service account in employee provisioning. | Platform access is not silently converted into employment history. |

Proposed work: express this matrix in one maintained policy layer, keeping navigation helpers consistent with server checks. Avoid weakening the special payroll policy while consolidating legacy permission names.

### 9.15 Database responsibilities and what must not be deleted

| Table/source | Responsibility | Removal assessment |
| --- | --- | --- |
| `users`, `user_permissions` | Login identity and grants. | Required across the application. |
| `staff` | Employment record and legacy payroll/attendance linkage. | Cannot remove while these links exist. |
| `hr_profiles` | User-to-staff bridge and employment terms. | Core workforce record. |
| `hr_policy_settings` | Effective-dated/source-labelled policy settings. | Required where configured policy overrides defaults. |
| `hr_accounts`, `hr_ledger` | Leave cycles, dated credits/use/corrections. | Required accounting history. |
| `hr_openings` | Proposed/confirmed openings and adjustments. | Required review evidence, not temporary import clutter. |
| `hr_requests` | Current signed leave workflow and calculated dates. | Active leave authority. |
| `hr_shifts` | Draft/published employee schedules and relief. | Required operational schedule. |
| `hr_weeks`, `hr_signatures` | Declared weekly time and version-specific evidence. | Required signed history. |
| `hr_audit` | Actor/action/payload/time history. | Required evidence for reviewed mutations. |
| `leave_requests`, `timesheets`, `attendance` | Legacy history/input and manual attendance. | Still referenced; migrate/reconcile before any retirement. |
| `payroll` | Salary payment records/status. | Active payroll read/update dependency. |
| Access snapshot files | Imported presence evidence and quality metadata. | Retain under an explicit evidence-retention policy. |

Workforce transactions use SQLite `BEGIN IMMEDIATE`; the PostgreSQL branch locks relevant workflow tables for decisions. Unique account, request-token, ledger-reference and signature constraints support duplicate prevention. Preserve these safeguards when extracting services or migrating schema.

### 9.16 What HR actually does not need, and what is only a candidate

**Strong consolidation proposal:** one account editor, one guarded employment-terms editor, one leave decision engine and one weekly timesheet editor. Keep the staff directory as a directory, make My HR the employee entry and Review Queue the reviewer's entry. Preserve separate views when they answer different questions: a roster is planned work; a timesheet is declared work; a card snapshot is presence evidence; payroll is a payment record.

**Compatibility routes:** retain old leave/timesheet URLs while redirecting/delegating. They are small adapters, not evidence that the new engine can replace all of `hr.py` today.

**Potential old templates:** `hr/leave.html`, `hr/timesheets.html`, `hr/timesheet_staff.html` and `hr/employee_leave_form.html` require an explicit caller/template-reference audit. Several associated legacy handlers now delegate or redirect, but this review does not certify every template as unused. Remove only after checking dynamic rendering, tests and packaged training references.

**Do not delete:** employee records, old leave records, signatures, opening proposals, ledger entries or attendance snapshots to reduce module size. Preserve history and external identifiers through an explicit migration.

**Remaining duplication to investigate:** direct staff employment edits versus guarded profile setup, HR/Admin user editing, manual attendance versus signed time presentation, and snapshot shift templates versus published roster facts. These are concrete implementation tasks rather than a blanket instruction to remove half of HR.

### 9.17 HR implementation work packages

| Priority | Work package and affected implementation | Concrete completion condition |
| --- | --- | --- |
| 1 | Map every account/employment field and writer in `admin.py`, `hr.py` and workforce setup. | A documented owner and guarded writer exists for each shared field; conflicting updates cannot bypass history checks. |
| 2 | Make canonical HR navigation: My HR, directory, leave, weeks, roster, review queue, HR setup and separately authorized payroll. | Every role reaches its tasks; duplicate account editors are consolidated; old URLs resolve appropriately. |
| 3 | Resolve ambiguous profile links and add unified employee history. | One reviewed user/profile/staff mapping; current and legacy leave displayed with provenance and no duplicate totals. |
| 4 | Complete setup and reviewed balance UX. | Missing terms/accounts are actionable; proposal details show sources; stale/repeated/self confirmations fail. |
| 5 | Align calendar scope, reporting lines and permission checks. | Intended team visibility is explicit; changed query parameters cannot broaden detailed access. |
| 6 | Improve roster validation feedback and evidence alignment. | Failed publication identifies the shift/coverage issue; published schedule and snapshot assumptions can be compared. |
| 7 | Complete weekly review and correction UX. | Exact signed version remains accessible after return/reopen; warnings and decisions are visible; no scan-derived paid hours. |
| 8 | Strengthen payment-status handling and date consistency. | Valid status transitions and actor/reason history; South Africa business dates consistent across HR paths. |
| 9 | Retire proven unused views and legacy write paths. | Reference audit complete; redirects/migration preserve history; obsolete files removed without deleting required adapters/data. |

### 9.18 Existing verification evidence and planned validation

`tests/test_hr_workforce.py` already contains scenarios for profile linking, second-person openings, chargeable dates, leave reservations, privacy, overlap, signatures, invalid intervals, early submission, atomic roster publication, lunch coverage, initial sick/family eligibility, permission revocation, repeatable migration, concurrent approvals, old cycles, adjustments, notifications, manager assignment, stale forms, relief, review queues and annual policy changes.

`tests/test_hr_access.py` covers broad HR versus restricted payroll/salary access and rollout availability. `tests/test_staff_attendance.py` covers actual versus policy start, repeated/denied scans, missing data, week isolation, authenticated preview, tenant checks and keeping evidence read-only.

These test names establish existing intended coverage, not a fresh passing result. After implementation, run the focused HR/access/attendance suites in the isolated test environment, migration checks and the HR browser review; then run the required release checks. Add targeted cases for the identified competing start-date writers, intended calendar scope, unified current/legacy history and audited payroll status transitions if the relevant fixes proceed.


## 10. Reports and analytics

### 10.1 Overview

**Current:** `reports.py` serves tabs through `/reports` and `/reports/overview`, guarded by `reports_module`. Overview reads shared counts and summary data. It is separate from operational dashboards.

**Plan:** document metric definition, population, source, effective date and refresh for each card; centralize shared calculations with operational modules. Link figures to record-level evidence rather than repeat unlabelled numbers.

**Acceptance:** filters and counts reconcile to source records; unavailable data is distinct from zero. Keep overview when it answers cross-period questions, not simply because a dashboard exists elsewhere.

### 10.2 Membership reporting

**Current:** membership tab loads period-based membership information; supported period choices include current/previous month, quarter, year, last 12 months and all time.

**Plan:** distinguish joins during a period from active membership at a point in time; define cancelled/unverified/reactivated handling and local versus external membership population. Preserve the date basis in titles and exports if provided.

**Acceptance:** members at boundaries appear in the correct period, reactivations do not falsely inflate new joins and the count matches the filtered directory's intended population. Use report/dashboard/member tests together.

### 10.3 Finance reporting

**Current:** finance tab reads local collection/payment-oriented data. Presence of a finance chart does not establish accounting revenue recognition or a general ledger.

**Plan:** label whether values are received, verified, promised, billed or outstanding; centralize verified-payment selection with reconciliation/PTP; handle reversals and duplicate imported/manual records. Define date as receipt, verification or provider transaction date explicitly.

**Acceptance:** source rows reproduce totals and promises are not counted as cash. Keep distinct finance measures where their meaning differs; remove duplicate displays only after defining them.

### 10.4 Arrears reporting

**Current:** arrears loader summarizes account/PTP-related facts alongside collection domain reports.

**Plan:** align debt/ageing/settlement calculations with the collection engine; define snapshot date and whether settled/superseded records participate. Compare account lists and report totals before consolidating legacy datasets.

**Acceptance:** a member does not owe different amounts depending on report entry point; ageing buckets reconcile to total outstanding. Keep operational queue and analytic report as separate views of shared facts.

### 10.5 Query reporting

**Current:** query tab reports case information by period, status/category and related operational facts.

**Plan:** define created-in-period versus closed-in-period versus currently-open measures; agree how reopened/resolved cases and overdue follow-ups are counted. Share status/category labels with Queries.

**Acceptance:** daily work list and analytic totals can be reconciled using their explicit date basis. Avoid calling historical throughput a current backlog.

### 10.6 Access Report

**Current:** `access_report.py` serves `/access-report` using configured external access data and tenant controls. This differs from credential assignment and employee timesheet evidence. The same destination appears under Finance and Reports.

**Plan:** use Reports as its canonical menu; label successful/denied scans, people and repeated visits distinctly; expose source freshness and configured scope. Check unavailable source and cross-tenant requests without changing external data.

**Acceptance:** repeated entries cannot be presented as unique people without an explicit measure; missing source is visible; tenant boundary holds. Remove only duplicated navigation.

### 10.7 Custom report builder and saved definitions

**Current:** `_build_custom_sql()` uses allowlisted tables/columns/operators/aggregation. Sources include members, collections, PTP, queries and contact logs; values use parameters. Optional member joins, selected columns, sorting, four filters and limit are supported. Admin manages saved definitions.

**Specific review:** parsed limit is capped at 1000 but the inspected helper does not show a lower-bound clamp. Sensitive columns are in the allowlist. These require deliberate bounds/audience decisions rather than a generic statement that all custom reports are safe.

**Plan:** clamp positive limits, preserve identifier/operator allowlists, validate saved definitions through the same builder and apply field-level visibility where required. Define query time/row limits and clear unsupported aggregation behavior. Do not add arbitrary SQL entry as a shortcut.

**Acceptance:** tampered fields/operators cannot escape the allowlist; negative/invalid limits do not bypass bounds; saved/current forms give equivalent results and restricted data is not exposed by report permission alone. Check custom report/admin-content suites and targeted cases when implementing fixes.

## 11. Administration, events and platform management

### 11.1 Tenant administration entry

**Current:** `admin.py` supplies admin landing and management of tariffs, templates, PBX codes, report definitions and users, with `_admin_required` on these routes. Platform tenant configuration is a separate system.

**Plan:** make landing links show configured/available capabilities; define which settings are tenant-scoped and which belong to platform operations. Centralize validation without putting service credentials in ordinary page content.

**Acceptance:** one tenant's administration cannot mutate another tenant and changes are attributable. Do not merge platform administration into this blueprint merely to reduce navigation.

### 11.2 Tariffs

**Current:** list/add/edit/toggle maintain tariff records consumed by sales/member creation. Existing member custom agreements also exist.

**Plan:** define active/retired behavior, effective dates and ownership of agreed prices; validate amount/duration and reference use before deactivation. Preserve the terms accepted on an existing application/contract instead of applying a new catalogue price retroactively.

**Acceptance:** changing a current tariff does not silently rewrite signed/historical terms; retired tariffs remain explainable on old records. Keep catalogue separate from negotiated member agreement.

### 11.3 Contract templates

**Current:** template list/add/edit/preview/default/toggle in Admin; contract rendering/signing/sharing in `contracts.py`. Signer roles include member, representative and witness.

**Plan:** version content and permitted merge fields; preview using representative sample fields without real member data; retain template identity/content relevant to signed evidence. Validate one intended default and safe deactivation.

**Acceptance:** editing the current template cannot silently alter what an earlier signature attested; previews and member rendering agree on field rules. Keep template editing distinct from contract execution.

### 11.4 Contract execution and public sharing

**Current:** authenticated view/sign/signature-image routes and share creation use member access checks. Public share route includes tenant slug/token and its own verification flow. `contract_share_tokens`, `contract_signatures`, documents and checklist are related evidence.

**Plan:** document token lifetime/verification, signer identity and permitted signing steps; bind contract content/signatures to member/application/template version; prevent stale public links from signing a replaced contract. Make share/resend idempotent where appropriate.

**Acceptance:** expired/invalid/cross-tenant links cannot sign/read evidence; each signer role is attributable and checklist readiness derives from actual signatures. Use contract sharing/signing/page-options/security suites.

### 11.5 PBX mapping and integration settings

**Current:** PBX code list/add/edit links code/staff data; WhatsApp browser status/QR has its own administrator page. Integration execution belongs to its worker/provider modules.

**Plan:** validate unique code and active target, show current mapping and protect connection/session actions. Distinguish saved configuration from verified runtime health; record change actor without logging tokens.

**Acceptance:** stale codes do not attribute calls to a replacement employee incorrectly and a connected label reflects actual worker state. Keep connection management out of normal inbox actions.

### 11.6 User administration

**Current:** Admin supports list/add/edit/reset/toggle; HR also has account add/edit. Accounts carry role, permissions and reporting/supervisor-related fields used by workflows. Auth and staff-profile restrictions contribute to effective access.

**Plan:** compare all fields/validators between editors; use one shared mutation service and canonical editor; preserve protected platform/service identities, safe reset behavior and employee links. Display effective permissions rather than imply a role name is the entire policy.

**Acceptance:** disabled/revoked users lose intended access, updates cannot broaden payroll/platform privileges accidentally and HR account paths preserve the same validation. Check staff password, auth, permissions, HR-access and platform tests.

### 11.7 Event administration

**Current:** `events.py` has admin event list/add/edit/toggle, registrations, ticket resend and CSV export. Public banner/registration/ticket/check-in routes are separate. Schema includes event registrations/ticket identities and tenant-related information.

**Plan:** define registration window/capacity and active/closed behavior, event ownership and attribution; preserve registration numbers and ticket state during event edits; record resend outcomes and protect exports. Confirm whether event changes may affect already-issued tickets.

**Acceptance:** an old ticket remains attributable, export scope is correct and repeated resend does not create new registrations. Keep event identity distinct from sales prospect identity while linking lead source when appropriate.

### 11.8 Public registration, tickets and check-in

**Current:** registration and check-in routes have rate limits; tickets use tokens and registration numbering. Message sending supports ticket delivery.

**Plan:** inspect token/authorization semantics on each public action; require the intended evidence for check-in rather than assuming the admin gate applies to public routes. Deduplicate registrations and preserve delivery/check-in history; handle disabled/closed events clearly.

**Acceptance:** repeat registration/check-in cannot inflate attendance, guessed tokens fail and provider delivery failure leaves a recoverable registration. Use event suite and public-route boundary cases.

### 11.9 Screen recordings

**Current:** `screen_recordings.py` provides administrator-only upload/list/download with metadata and configured storage. It is an actual upload workflow, not just screenshots in `artifacts`.

**Plan:** confirm business use and retention; validate file/storage/download ownership and client capture entry paths. If retiring, disable capture/upload callers as well as menu and routes, then preserve required records under retention.

**Acceptance:** unauthorized retrieval is blocked and cleanup does not delete unrelated uploads. Optional feature only after usage/retention decision.

### 11.10 Platform Power User and tenant provisioning

**Current:** `platform_admin.py`, `platform_db.py`, `tenants.py` and seed script manage platform login, gym create/edit/toggle, tenant selection/entry/exit and audit. Tenant databases are distinct from platform database; an entered tenant is visibly marked.

**Plan:** define provisioning lifecycle, database paths and failure recovery; preserve active/inactive handling and platform/tenant session separation. Check the internal identity used for tenant entry cannot sign in as an ordinary user or become an employee.

**Acceptance:** normal tenant users cannot access platform or another gym; entering/exiting is audited; failed provisioning leaves no apparently active half-created gym. Use platform Power User/tenant isolation/startup tests. Keep platform DB and tenant records.

## 12. Notifications and training

### 12.1 Work notifications

**Current:** `notifications.py` creates recipient-scoped source/event notifications for query, sale, application, collection and PTP work. Unique keys coalesce duplicates; owners/supervisors and routing recipients are considered. Daily overdue reminders are bounded. HR transactions own HR notification lifecycles and generic refresh preserves them.

**Data/access:** `staff_notifications`, supervisor links, domain source records; count/list/acknowledge require login and recipient scope. Supervisor validation detects reporting cycles. HR manager assignment is separately stored in `hr_profiles.manager_id`.

**Plan:** document event ownership, recipient changes and lifecycle; distinguish acknowledgement from source completion; reconcile notification supervisor versus HR reporting line deliberately. Bound request refresh cost and preserve idempotency between scheduler/request refresh.

**Acceptance:** repeating refresh does not duplicate alerts, stale sources resolve only their alerts, acknowledged work can remain open and users cannot acknowledge another recipient's record. Use notifications/routing/HR suites together.

### 12.2 Unread and follow-up indicators

**Current:** unread internal messages, announcements, query follow-up count and work notifications coexist in shared UI. They represent different conditions.

**Plan:** define labels/count scopes; provide a clear common work entry if useful while retaining underlying states. Avoid counting the same notification as completed because the related message was opened.

**Acceptance:** opening/acknowledging one item changes only the intended indicator. Consolidate visual clutter, not unrelated data lifecycles.

### 12.3 Staff training content and manuals

**Current:** `training.py` serves authenticated chapter/topic pages and PDF/DOCX from `resources/training`; `build_training_content.py` builds content. Formats are intentional outputs, not automatically redundant copies.

**Plan:** maintain one authored source with build/version linkage; regenerate chapter pages/manuals together after workflow changes; check protected download endpoints and missing-topic handling. Archive outdated manuals with release context if needed rather than leaving contradictory current instructions.

**Acceptance:** staff instructions match final implemented actions and downloaded files are the same published version; public users cannot fetch protected manuals through alternate paths. Use training suite and visual/manual checks if artifacts change.

## 13. Shared architecture, background work and release

### 13.1 Application factory and request lifecycle

**Current:** `__init__.py` loads config/extensions, registers blueprints, initializes databases and starts scheduler/device/WhatsApp components. Request hooks resolve tenant, refresh/enforce staff access and enforce phases; context processors add badges/permissions. Teardown closes tenant/platform connections.

**Plan:** map startup side effects and process ownership; ensure multiple web workers do not each run the same irreversible background operation. Keep request tenant binding established before tenant data access and close connections on failures. Keep lightweight health distinct from provider availability.

**Acceptance:** isolated startup closes connections, tests do not start production workers, multiple workers do not duplicate jobs and an unavailable integration does not falsely report success. Use startup/health/scheduler/tenant tests.

### 13.2 Configuration and environment

**Current:** `config.py`, `.env.example`, optional/development/runtime requirements, Windows and beta deploy instructions. Local `.env` and backups exist but their contents were not reviewed.

**Plan:** document required/optional values per deployment feature and supported database/device/browser modes; validate production requirements early; keep examples free of credentials. Reconcile stale dependency comments with actual included scripts and external drivers.

**Acceptance:** clean declared setup reproduces supported startup, feature absence yields a clear disabled/unavailable state and removing optional packages cannot break import of unrelated modules.

### 13.3 Tenant databases, platform database and migrations

**Current:** `database.py`, `db_adapter.py`, `schema.py`, platform DB and module migration helpers; SQLite tenant files plus configured PostgreSQL path. Initialization and legacy backfills are distributed across helpers.

**Plan:** inventory schema ownership and migration order; make changes repeatable on disposable old/current databases; preserve external keys, signatures, financial evidence and tenant paths. Choose supported database modes explicitly before retiring adapter code.

**Acceptance:** repeat migration preserves row counts/relationships, failure is recoverable and tenant selection cannot resolve outside intended gym data. Use DB adapter/migration/tenant suites. Never delete root DBs merely because their names look old.

### 13.4 Authentication and authorization

**Current:** `auth.py`, `permissions.py`, department profiles and HR-specific access layers combine role, grants, session/tenant, OTP/password and platform boundaries.

**Plan:** document effective authorization for each route/action, not just menu; centralize policy helpers where behavior is equivalent, retain explicit special policies where different. Verify account deactivation and permission changes take effect in ongoing sessions.

**Acceptance:** direct POST/changed id cannot bypass scope, tenant switch invalidates inappropriate session use and protected service/platform accounts cannot act as normal employees. Use auth/permission/tenant/platform/HR suites.

### 13.5 Security controls and private evidence

**Current:** CSRF, encryption, shared production rate-limit requirements, trusted-proxy configuration, webhook verification and security headers. Production startup checks key settings and integration secrets.

**Plan:** preserve controls through refactors; audit file endpoints and logs for evidence/secret handling; review actual trusted proxy count and content policy against deployment. Do not weaken tests to accommodate changed controls accidentally.

**Acceptance:** malformed keys fail visibly, invalid webhook signatures do not mutate data and private documents remain scoped. Use encryption/rate-limit/header/webhook suites. No new approval workflow is required for this documentation review.

### 13.6 Scheduled collection and sales work

**Current:** scheduler runs PTP automation, collection workflow and sales decision follow-ups with active tenant iteration and configured behavior.

**Plan:** record cadence, timezone, tenant selection, unique run/operation keys and failure/retry outcomes; distinguish preview from mutation; ensure one process owns each job. Preserve backups/evidence appropriate to changes.

**Acceptance:** reruns are safe, partial tenant failure does not silently stop or double-apply others and disabled phases do not create unauthorized work. Verify scheduler plus affected domain suites.

### 13.7 External import/sweep and notification jobs

**Current:** scheduler checks external Itensity script availability, imports members/sweeps transactions, records snapshots and generates notifications.

**Plan:** publish deployment dependency manifest and expected source timestamps; separate missing driver, empty source and failed import; share data matching/idempotency with manual runs. Keep notification generation tenant-scoped.

**Acceptance:** unavailable sources do not erase local facts, repeated sweep cannot double-credit and stale snapshots are identified. Keep the jobs if any active tenant depends on them.

### 13.8 Browser/device workers

**Current:** Playwright lock helper, WhatsApp worker/adapters, NuPay browser driver and turnstile monitor coordinate runtime resources; Windows HID and ODBC are conditional/environment dependencies.

**Plan:** define one owner per browser/device session, startup/shutdown, locking and reconnect/timeout; isolate credentials/profiles and verify current tenant on result application. Separate supported optional dependencies from unconditional imports before slimming installs.

**Acceptance:** two workers cannot corrupt the same profile, shutdown releases resources and unavailable hardware cannot create fabricated success/events.

### 13.9 Shared frontend

**Current:** `base.html`, `_ui.html`, CSS tokens/components and shared JS plus feature CSS/JS/assets. Navigation and data-heavy forms depend on shared behavior.

**Plan:** inventory reusable tables/forms/modals/badges; consolidate duplicate components without changing business gates; maintain labels, keyboard access, validation messages and mobile overflow checks. Retire images/scripts only after template/dynamic references are checked.

**Acceptance:** principal role workflows render at desktop/mobile sizes without JS errors, broken links or lost validation. Use template structure/navigation/UI verification rather than unrelated new unit tests for cosmetic changes.

### 13.10 Tests, release and deployment

**Current:** `tests`, CI workflow, `TESTING.md`, UI/migration scripts, `deploy_beta.py`, service/Gunicorn config and release evidence. Existing working tree has substantial prior edits; these were not modified by this review.

**Plan:** map work packages to existing suites, classify failures per testing policy and run required release gates on final source. Record tested revision/dependency/runtime and artifacts; check beta promotion/rollback procedure before deployment.

**Acceptance:** required checks pass on the actual promoted source and rollback material is attributable. Documentation generation alone does not establish tests passed or production readiness.


## 14. What appears unnecessary, duplicated or worth consolidating

### A. Generated clutter: strongest cleanup candidates

| Candidate | Evidence and assessment | Concrete cleanup plan |
| --- | --- | --- |
| Numerous `.pytest-*` folders, `.pytest_cache`, `.mypy_cache`, `__pycache__`, `Dev.pytest_tmp_verify` | Generated test/cache material is present at repository root; some temp directories produced permission warnings during Git inspection. Not application source. | Confirm no test process is using them; remove generated contents using verified workspace-local paths; configure one temp location and ignore the recurring temp naming patterns. |
| Rotated logs and dry-run output logs | Runtime/debug outputs at root. | Keep current operational logs where required; archive needed incident evidence, then rotate/remove expired local logs. |
| Repeated test text/XML outputs and deployment snapshots under `artifacts/` | Many run-specific outputs and dated before/deploy folders. | Group by release and retain the final source-linked evidence and rollback material; expire superseded runs using a documented retention rule. Do not remove the entire artifacts tree. |
| Timestamped `.env` backups | Multiple backup files exist; contents were not opened. | Establish which backup is required for recovery; retain securely outside the source tree and remove redundant copies after recovery verification. Preserve the active `.env` and `.env.example`. |

### B. Confirmed duplication or compatibility worth simplifying

1. **Access report navigation:** the sidebar links the same `access_report.index` destination in Finance and Reports. Keep one main menu item under Reports, then check navigation tests.
2. **Legacy HR entry paths:** leave delegates to workforce; timesheets redirect to workforce weeks; obsolete save is explicitly rejected. Keep compatibility routes initially, remove duplicate primary navigation, then retire unreferenced templates/routes after caller checks.
3. **Release/implementation documents:** several sales, collections and HR plans plus release/audit documents coexist. Consolidate the current operational plan per domain and archive dated decisions/evidence. Historical documents are not proven obsolete simply because another plan exists.
4. **One-off scripts under `artifacts/` and root scratch cancellation scripts:** these look like task-specific operations rather than maintained application entry points. Inspect callers and recovery value; archive completed operational scripts, and promote recurring operations into maintained scripts with dry-run support.

### C. Possible unused assets: not yet proven removable

A source-text search in application/scripts/tests found no literal references to `Training.json`, `maintainance.json`, `nupay.json`, or `1cpase-logo-alt*`. These are candidates for an expanded reference audit, not confirmed dead files. Inspect their purpose, dynamic path construction, deployment packaging and external consumers without publishing their contents. Remove only after those checks; retain a recoverable history.

### D. Optional features: business usage must decide

- Facebook inbox/comments if no active tenant uses the channel.
- Anatomy reference, supplements and nutrition if trainers do not use them.
- Events/ticketing if the gym does not manage events here.
- Screen recording if the recording workflow has been retired.
- Either WhatsApp browser adapter only when deployment configuration and delivery tests establish it is no longer needed. Selenium is actively selected by configuration in `whatsapp_web.py` and has tests; it is not dead merely because Playwright also exists.
- PostgreSQL/ODBC/device dependencies only if all supported deployments are confirmed not to use those modes. Existing imports and adapters make immediate dependency deletion unsafe.

For each optional feature: gather usage across tenants, count existing records without exposing member data, check owners/integrations, hide or disable through supported configuration first, observe a defined period, then remove routes/jobs/assets/dependencies together. No usage findings were established in this review.

### E. Keep: not cleanup targets

Do not delete `1cpase.db`, `platform.db`, tenant data, uploads, backups or WhatsApp session data as general clutter. They may contain active business state, evidence or recovery material. Do not merge `comms.py` with `communication.py` based on naming alone. Keep collection policy/evidence gates, authentication, permissions, tests and release checks.

## 15. Whole-application delivery plan

This is a proposed sequence for implementing the review, not authorization to retire optional business features or change financial/employment policy. Each package should produce a small reviewable change; deploy only after the applicable release gates. No duration estimate is asserted without profiling the actual data and agreeing scope.

### Package 1 ? Establish field and workflow ownership

**Work:** map lead/member/application, account/mandate/payment, user/staff/profile and query/case identifiers; inventory every writer and source precedence. Record stage/status mappings, supported deployment modes and current role/phase matrix. Resolve optional-feature usage through evidence across active tenants.

**Deliverables:** field-ownership matrix, state maps, feature decisions and dependency list. **Completion:** no proposed removal depends on an unidentified caller or active dataset. **Dependency:** none; this precedes schema/service consolidation.

### Package 2 ? Normalize navigation and work entry

**Work:** choose role landing pages; remove the duplicate Access Report menu item; make canonical employee/reviewer and sales/collections work entries clear; keep redirects/delegation for old URLs. Share labels without altering domain states.

**Validation:** navigation/phase/permission suites and desktop/mobile role review. **Completion:** all authorized tasks reachable and direct forbidden routes denied. **Recovery:** restore templates/links; no data migration required.

### Package 3 ? Shared prospect/member/application creation

**Work:** extract or complete common normalization, duplicate checks, consultant attribution, package validation and linked-record creation for manual/public/social/list/conversion paths. Use transactions/unique identity safeguards appropriate to current schema; preserve original source.

**Validation:** repeat/concurrent conversion, existing-member detection, duplicate uploads, custom-package and pipeline tests. **Completion:** retry creates no second member/application; all entry paths establish equivalent evidence structure. **Dependency:** Package 1 mappings.

### Package 4 ? Evidence, document and readiness contract

**Work:** centralize document/statement analysis outcomes and structured readiness blockers for member/application/mandate/collection entry points; preserve source-controlled terminal verification. Define deletion/correction invalidation and version provenance.

**Validation:** forged checklist states, overlapping PDFs, missing/invalid evidence, download scope and cross-entry equivalence. **Completion:** each eligible state traces to current evidence and source workflow. **Recovery:** compatible schema additions first; do not remove original evidence.

### Package 5 ? External submission and import reliability

**Work:** define attempt/run identity and uncertain outcomes for Itensity/NuPay; keep external refs, resumable status checks, matching review and idempotent source transactions. Make manual and scheduled operations share the same preparation/application rules.

**Validation:** missing driver, timeout-after-provider-success, provider rejection, retry and partial tenant failure. **Completion:** no duplicate external member/mandate/credit and every failed/unknown result is reviewable. **Dependency:** Packages 3?4 where they supply identity/evidence.

### Package 6 ? Shared financial facts and policy calculations

**Work:** map legacy collections to cases/payments, consolidate live debt/offer/installment calculations after equivalence checks, distinguish promises/receipts/verification/settlement and preserve policy/version inputs. Share results with Queries and Reports.

**Validation:** financial-rule matrix, receipts, reversals, partial/superseded arrangements and imported/manual duplicates. **Completion:** one consistent member balance and explainable approved offer across screens. **Recovery:** compare before/after snapshots; retain historical records and old read capability until reconciled.

### Package 7 ? Query and collection work lifecycle

**Work:** align task ownership, follow-up, escalation, review, closure and PTP linkage; define reassignments/inactive owners and access-decision synchronization. Preserve category-specific evidence and explicit referral actions.

**Validation:** unauthorized decisions, duplicate conversions, closed/superseded reminders, direct id changes and unsettled closure blockers. **Completion:** every active task has an owner/next action and a decision updates only its intended case/source notifications. **Dependency:** Package 6 for financial outcomes.

### Package 8 ? Communication lifecycle and attribution

**Work:** specify number/channel permissions, provider selection, send identity, inbound deduplication, conversation matching and call attribution. Keep private staff communication distinct. Consolidate shared UI/content, not unrelated histories.

**Validation:** bad signatures, replay, wrong tenant/number ids, reconnect/echo, failed send and duplicate PBX event. **Completion:** one message/call identity per event and truthful transport state. **Dependency:** Package 1 identity; coordinate external checks with Package 5.

### Package 9 ? Fitness journey and measurement consolidation

**Work:** one joining cohort/daily queue, linked intake/visits/review, attributable signature/corrections and shared assessment/body measurements. Define classes/plans/dailies lifecycle; label nutrition/supplement/reference scope accurately. Decide optional retirement from actual use.

**Validation:** duplicate arrival/welcome, signature ownership, cohort/report totals, booking limits and repeated measurement capture. **Completion:** staff can follow a member from join through intake and review without recreating identity or entering the same measurement twice. **Dependency:** shared member/evidence packages.

### Package 10 ? HR integrity and self-service completion

**Work:** execute Section 9.17 packages: canonical account/terms writers, reviewed profile links and balances, privacy scope, roster validation, signed weeks/evidence alignment, legacy history and payment-status attribution.

**Validation:** existing HR/access/attendance suites plus targeted identified gaps and HR UI checks. **Completion:** one guarded employment-terms path, independently reviewed balance corrections and preserved signed/legacy history. **Dependency:** user/identity mapping; HR does not need to wait for optional fitness/reference changes.

### Package 11 ? Operations and cross-domain resolution

**Work:** validate asset/history/cost/dates; clarify next-due work versus completed history; link incident/Query to equipment repair and condition. Build reminders only when included in agreed operational scope.

**Validation:** invalid asset/cost/date, inactive equipment, role boundaries and linked resolution. **Completion:** recorded repair and equipment state are consistent and duplicate complaint/repair entry is avoided. **Dependency:** query linkage convention, not financial-engine refactoring.

### Package 12 ? Reporting consistency and bounded custom reporting

**Work:** document date/population/source for each measure, share facts with domain services, bound report builder and apply intended field visibility; align saved definitions and access reporting. Preserve historical interpretation through labels/versioning.

**Validation:** card-to-detail reconciliation, date boundaries, repeated imports, negative/invalid limits and restricted fields. **Completion:** reports are reproducible and do not label promises/scans as verified receipts/work hours. **Dependency:** Packages 3/6/7/9 for canonical facts; definitions can begin earlier.

### Package 13 ? Background-process and runtime ownership

**Work:** assign single ownership of jobs/browser/device workers, tenant context, retry keys, startup/stop and freshness/error reporting. Reconcile requirements with unconditional imports and external runtime installation.

**Validation:** isolated startup, multiworker duplicate suppression, resource release and partial job failure. **Completion:** multiple web workers do not duplicate irreversible jobs and missing optional services yield usable errors. **Dependency:** integration contracts from Package 5; no need to retire a supported database mode.

### Package 14 ? Cleanup, documentation and release

**Work:** remove verified caches/temp outputs; organize final source-linked evidence and rollback copies; archive superseded plans/one-off scripts; remove only audited unused views/assets/dependencies. Update training/current setup documentation for final behavior.

**Validation:** path/caller/package audit, focused suites followed by required full release/migration/UI checks on final source. **Completion:** intended deletions are reviewable, business/history files remain, docs match behavior and deploy/rollback evidence identifies the tested build. **Recovery:** keep cleanup/schema/application changes separable; do not combine data purges with the refactor release.

### What has actually been validated for this plan

The current review used source/route/decorator/template inspection and a syntax-tree index of all 80 top-level application Python modules. It did not run the application, execute tests, inspect production configuration/records or call external services. Named test suites are planned verification entry points, not fresh passing evidence. Only this Markdown document was changed; application code and business data were left intact.

## 16. Source implementation index

Generated from Python syntax trees before the subsequent native mobile implementation. The 80-module index below is that review snapshot; see `MOBILE_IMPLEMENTATION.md` for the new mobile API, shared booking service and Android/iOS client. This index identifies handlers, route decorators, templates and directly named SQL tables. Tables inferred from SQL-looking literals are navigation aids, not complete lineage. Dynamic references need caller checks; handler, blueprint and application guards also apply beyond the listed decorators.

### 16.1 `onecpase/__init__.py`

**Service/class entry points:** `create_app`.

**Direct SQL table names:** `tenants`, `users`.

**Application imports:** `access_report`, `admin`, `applications`, `auth`, `call_list`, `collections`, `comms`, `config`, `contracts`, `dashboard`, `database`, `debicheck_routes`, `encryption`, `events`, `extensions`, `first_month_collections`, `fitness`, `fitness_journey`, `health`, `hr`, `hr_access`, `hr_workforce`, `hr_workforce_routes`, `inspect_mode`, `internal_comms`, `leads`, `members`, `notifications`, `onboarding`, `operations`, `pbx`, `permissions`, `platform_admin`, `platform_db`, `ptp`, `public_leads`, `queries`, `reconciliation`, `reports`, `sales_pipeline`, `scheduler`, `screen_recordings`, `tenants`, `training`, `turnstile`, `turnstile_routes`, `whatsapp_web`.

### 16.2 `onecpase/access_report.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `index` :194 | `access_report_bp.route('/')` | Check handler/blueprint/app guards |

**Direct SQL table names:** `call_activity`, `dbo`, `itensity_payments`, `mandate_status`, `member_access`, `ptp`, `raw_member_access`, `total_visits`.

**Direct templates:** `access_report/index.html`.

### 16.3 `onecpase/admin.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `admin_index` :68 | `admin_bp.route('/')` | `_admin_required` |
| `tariffs_index` :90 | `admin_bp.route('/tariffs')` | `_admin_required` |
| `tariff_add` :98 | `admin_bp.route('/tariffs/add', methods=['GET', 'POST'])` | `_admin_required` |
| `tariff_edit` :123 | `admin_bp.route('/tariffs/<int:tid>/edit', methods=['GET', 'POST'])` | `_admin_required` |
| `tariff_toggle` :154 | `admin_bp.post('/tariffs/<int:tid>/toggle')` | `_admin_required` |
| `pbx_codes_index` :170 | `admin_bp.route('/pbx-codes')` | `_admin_required` |
| `pbx_code_add` :185 | `admin_bp.route('/pbx-codes/add', methods=['GET', 'POST'])` | `_admin_required` |
| `pbx_code_edit` :211 | `admin_bp.route('/pbx-codes/<int:code_id>/edit', methods=['GET', 'POST'])` | `_admin_required` |
| `contract_templates_index` :243 | `admin_bp.route('/contract-templates')` | `_admin_required` |
| `contract_template_add` :366 | `admin_bp.route('/contract-templates/add', methods=['GET', 'POST'])` | `_admin_required` |
| `contract_template_edit` :381 | `admin_bp.route('/contract-templates/<int:template_id>/edit', methods=['GET', 'POST'])` | `_admin_required` |
| `contract_template_preview` :403 | `admin_bp.route('/contract-templates/<int:template_id>/preview')` | `_admin_required` |
| `contract_template_default` :415 | `admin_bp.post('/contract-templates/<int:template_id>/default')` | `_admin_required` |
| `contract_template_toggle` :433 | `admin_bp.post('/contract-templates/<int:template_id>/toggle')` | `_admin_required` |
| `report_definitions_index` :503 | `admin_bp.route('/report-definitions')` | `_admin_required` |
| `report_definition_add` :551 | `admin_bp.route('/report-definitions/add', methods=['GET', 'POST'])` | `_admin_required` |
| `report_definition_edit` :564 | `admin_bp.route('/report-definitions/<int:report_id>/edit', methods=['GET', 'POST'])` | `_admin_required` |
| `report_definition_toggle` :583 | `admin_bp.post('/report-definitions/<int:report_id>/toggle')` | `_admin_required` |
| `users_index` :598 | `admin_bp.route('/users')` | `_admin_required` |
| `user_add` :617 | `admin_bp.route('/users/add', methods=['GET', 'POST'])` | `_admin_required` |
| `user_edit` :693 | `admin_bp.route('/users/<int:uid>/edit', methods=['GET', 'POST'])` | `_admin_required` |
| `user_reset_password` :808 | `admin_bp.post('/users/<int:uid>/reset-password')` | `_admin_required` |
| `user_toggle` :847 | `admin_bp.post('/users/<int:uid>/toggle')` | `_admin_required` |

**Service/class entry points:** `is_protected_user`.

**Direct SQL table names:** `contract_templates`, `members`, `pbx_account_codes`, `report_definitions`, `tariff`, `tariffs`, `user`, `user_permissions`, `users`.

**Direct templates:** `admin/contract_template_form.html`, `admin/contract_templates.html`, `admin/index.html`, `admin/pbx_code_form.html`, `admin/pbx_codes.html`, `admin/report_definition_form.html`, `admin/report_definitions.html`, `admin/tariff_form.html`, `admin/tariffs.html`, `admin/user_form.html`, `admin/users.html`.

**Application imports:** `auth`, `contracts`, `database`, `notifications`, `permissions`, `reports`.

### 16.4 `onecpase/applications.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `applications_index` :396 | `applications_bp.route('/')` | `permission_required('all_members')` |
| `application_detail` :468 | `applications_bp.route('/<int:aid>')` | `permission_required('all_members')` |
| `update_checklist` :582 | `applications_bp.post('/<int:aid>/checklist')` | `permission_required('add_member')` |
| `submit_analysis` :694 | `applications_bp.post('/<int:aid>/analyse')` | `permission_required('add_member')` |
| `push_application` :911 | `applications_bp.post('/<int:aid>/push')` | `permission_required('add_member')` |
| `approvals_queue` :990 | `applications_bp.route('/approvals')` | `permission_required('application_approvals')` |
| `approve_application` :1015 | `applications_bp.post('/<int:aid>/approve')` | `permission_required('application_approvals')` |
| `decline_application` :1064 | `applications_bp.post('/<int:aid>/decline')` | `permission_required('application_approvals')` |

**Service/class entry points:** `ensure_member_application`, `sync_application_status`, `statement_months_analysed`, `bank_statement_qualified`, `nupay_submission_checklist`, `bank_statement_activation_blockers`, `nupay_submission_blockers`, `authorize_nupay_override`, `registration_fee_paid`, `compliance_completion_blockers`.

**Direct SQL table names:** `approvals`, `audit_logs`, `collections`, `compliance_checklists`, `contract_share_tokens`, `contract_signatures`, `debicheck_mandates`, `leads`, `member_documents`, `members`, `membership_applications`, `stage_events`, `statement_analyses`, `users`.

**Direct templates:** `applications/approval.html`, `applications/detail.html`, `applications/index.html`.

**Application imports:** `auth`, `database`, `debicheck`, `encryption`, `integrations`, `sales_kpi`, `sales_pipeline`.

### 16.5 `onecpase/auth.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `login` :266 | `auth_bp.route('/login', methods=['GET', 'POST'])` | `limiter.limit('10 per minute')` |
| `verify_otp` :327 | `auth_bp.route('/login/verify', methods=['GET', 'POST'])` | `limiter.limit('15 per minute')` |
| `resend_otp` :394 | `auth_bp.post('/login/resend')` | `limiter.limit('3 per minute')` |
| `change_password` :433 | `auth_bp.route('/change-password', methods=['GET', 'POST'])` | Check handler/blueprint/app guards |
| `logout` :467 | `auth_bp.get('/logout')` | Check handler/blueprint/app guards |

**Service/class entry points:** `valid_email_address`, `session_tenant_ok`, `login_required`, `roles_required`, `permission_required`.

**Direct SQL table names:** `login_otp_challenges`, `user_permissions`, `users`.

**Direct templates:** `auth/change_password.html`, `auth/login.html`, `auth/otp.html`.

**Application imports:** `database`, `extensions`, `hr_access`, `permissions`, `tenants`.

### 16.6 `onecpase/call_list.py`

**Source responsibility:** Photographed/scanned call-list upload, OCR review, and import into leads.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `index` :50 | `call_list_bp.get('')` | `permission_required('capture_leads')` |
| `upload` :62 | `call_list_bp.route('/upload', methods=['GET', 'POST'])` | `permission_required('capture_leads')` |
| `review` :136 | `call_list_bp.get('/<int:upload_id>/review')` | `permission_required('capture_leads')` |
| `update_row` :152 | `call_list_bp.post('/<int:upload_id>/row/<int:row_id>')` | `permission_required('capture_leads')` |
| `import_rows` :231 | `call_list_bp.post('/<int:upload_id>/import')` | `permission_required('capture_leads')` |

**Direct SQL table names:** `call_list_upload_rows`, `call_list_uploads`, `leads`, `users`.

**Direct templates:** `call_list/index.html`, `call_list/review.html`, `call_list/upload.html`.

**Application imports:** `auth`, `call_list_parser`, `database`, `leads`.

### 16.7 `onecpase/call_list_parser.py`

**Source responsibility:** OCR and parsing helpers for handwritten/scanned call-list documents.

**Service/class entry points:** `class CallListParserError`, `class ParsedCallRow`, `class ExtractionResult`, `normalize_phone`, `map_response`, `extract_appointment_time`, `parse_ocr_lines`, `extract_call_list`.

### 16.8 `onecpase/collections.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `collections_index` :443 | `collections_bp.route('/')` | `permission_required('all_collections')` |
| `dashboard` :493 | `collections_bp.route('/dashboard')` | `permission_required('all_collections')` |
| `member_profile` :510 | `collections_bp.route('/member/<int:mid>')` | `permission_required('all_collections')` |
| `add_note` :522 | `collections_bp.post('/member/<int:mid>/notes')` | `permission_required('all_collections')` |
| `resubmit_discount` :539 | `collections_bp.post('/discount-requests/<int:request_id>/resubmit')` | `permission_required('all_collections')` |
| `reports` :557 | `collections_bp.route('/reports')` | `permission_required('all_collections')` |
| `case_queue` :583 | `collections_bp.route('/queue')` | `permission_required('all_collections')` |
| `call_queue` :603 | `collections_bp.route('/call-queue')` | `permission_required('collections_call_queue')` |
| `record_reminder_delivery` :683 | `collections_bp.post('/reminders/<int:reminder_id>/delivered')` | `permission_required('collections_call_queue')` |
| `offer_preview` :811 | `collections_bp.get('/call-queue/<int:task_id>/offer-preview')` | `permission_required('collections_call_queue')` |
| `upload_bank_statement` :841 | `collections_bp.post('/call-queue/<int:task_id>/bank-statement')` | `permission_required('collections_call_queue')` |
| `upload_documents` :876 | `collections_bp.post('/call-queue/<int:task_id>/documents')` | `permission_required('collections_call_queue')` |
| `nupay_check` :890 | `collections_bp.post('/call-queue/<int:task_id>/nupay-check')` | `permission_required('collections_call_queue')` |
| `call_profile` :919 | `collections_bp.route('/call-queue/<int:task_id>/profile')` | `permission_required('collections_call_queue')` |
| `record_call_result` :1061 | `collections_bp.post('/call-queue/<int:task_id>/result')` | `permission_required('collections_call_queue')` |
| `send_collection_whatsapp` :1283 | `collections_bp.route('/whatsapp/<kind>/<int:record_id>', methods=['GET', 'POST'])` | `permission_required('collections_call_queue')` |
| `toggle_escalation` :1351 | `collections_bp.post('/call-queue/<int:task_id>/escalation')` | `permission_required('collections_call_queue')` |
| `send_collection_email` :1421 | `collections_bp.post('/call-queue/<int:task_id>/email')` | `permission_required('collections_call_queue')` |
| `collections_add` :1484 | `collections_bp.route('/add', methods=['GET', 'POST'])` | `permission_required('new_collection')` |
| `collections_edit` :1576 | `collections_bp.route('/<int:cid>/edit', methods=['GET', 'POST'])` | `permission_required('new_collection')` |
| `collections_delete` :1628 | `collections_bp.post('/<int:cid>/delete')` | `permission_required('new_collection')` |
| `exception_queue` :1761 | `collections_bp.get('/exceptions')` | `roles_required('admin', 'manager')` |
| `exception_decide` :1782 | `collections_bp.post('/exceptions/<int:exception_id>/decide')` | `roles_required('admin', 'manager')` |

**Service/class entry points:** `below_installment_not_callable`, `mandate_state_for`, `daily_call_progress`, `pending_manager_decisions`.

**Direct SQL table names:** `collection_call_tasks`, `collection_communications`, `collection_discount_requests`, `collection_exceptions`, `collection_reminders`, `collections`, `collections_cases`, `compliance_checklists`, `debicheck_mandates`, `legal_referrals`, `member_documents`, `members`, `nupay_mandate_checks`, `ptp_agreements`, `ptp_mandate_links`, `queries`, `users`, `wa_conversations`.

**Direct templates:** `collections/add.html`, `collections/call_profile.html`, `collections/call_queue.html`, `collections/dashboard.html`, `collections/edit.html`, `collections/exceptions.html`, `collections/index.html`, `collections/member.html`, `collections/queue.html`, `collections/reports.html`, `collections/whatsapp_preview.html`.

**Application imports:** `applications`, `auth`, `collections_decision`, `collections_engine`, `comms`, `communication`, `database`, `debicheck`, `encryption`, `extensions`, `integrations`, `members`, `ptp`, `ptp_grant`, `queries`, `statement_uploads`, `whatsapp_contacts`.

### 16.9 `onecpase/collections_decision.py`

**Source responsibility:** Promise-to-Pay decision calculation.

**Service/class entry points:** `stale_cutoff`, `payment_is_stale`, `authorisation_lapsed`, `load_policy`, `decide_offer`.

**Application imports:** `collections_engine`.

### 16.10 `onecpase/collections_engine.py`

**Source responsibility:** Shared collections policy, workflow, and audit helpers.

**Service/class entry points:** `discount_range_label`, `discount_needs_manager`, `arrears_discount_policy`, `money`, `calculate_arrears_installment`, `calculate_debicheck_options`, `is_debicheck_approved`, `upfront_percentage`, `required_upfront_amount`, `ptp_settlement_offer`, `arrears_category`, `category_offer`, `repayment_schedule`, `ptp_debit_orders_needed`, `calculate_realistic_recovery_plan`, `evaluate_collection_case`, `next_communication`, `complete_call`, `calculate_daily_call_kpi`, `evaluate_ptp`, `resolve_case`, `reconcile_open_cases`, `classify_ptp`, `supersede_open_ptps`, `refresh_member_cases`, `evaluate_member_access`, `determine_access`, `ensure_collections_engine_schema`, `get_collection_rules`, `get_collection_rule`, `get_collection_rule_int`, `talk_seconds_sql`, `reached_sql`, `verified_sql`, `sync_collection_case`, `find_original_sales_consultant`, `is_first_debit_failure`, `determine_owner`, `second_scheduled_debit`, `apply_case_ownership`, `transfer_due_sales_cases`, `determine_collection_stage`, `determine_priority`, `determine_next_action`, `refresh_case_workflow`, `find_duplicate_collection`, `log_collection_communication`, `record_access_decision`, `create_collection_exception`, `decide_collection_exception`, `settle_exception_for_arrangement`, `apply_manager_exception`, `grant_exception_access`, `add_collection_note`, `record_discount_request`, `settle_discount_request`, `return_discount_request`, `resubmit_discount_request`, `return_collection_exception`, `reactivation_upfront_required`, `visit_call_approach`.

**Direct SQL table names:** `access_decisions`, `collection_assignment_history`, `collection_communications`, `collection_discount_requests`, `collection_exceptions`, `collection_notes`, `collection_payments`, `collection_reminders`, `collection_rules`, `collection_stage_history`, `collections`, `collections_cases`, `debicheck_mandates`, `leads`, `members`, `membership_applications`, `nupay_import_runs`, `nupay_transactions`, `pbx_call_logs`, `ptp_agreements`, `ptp_receipts`, `reconciliation_snapshot`.

**Application imports:** `database`, `ptp`, `queries`.

### 16.11 `onecpase/collections_workspace.py`

**Source responsibility:** Data for the Collections dashboard and case queue.

**Service/class entry points:** `case_queue`, `queue_counts`, `dashboard_metrics`, `account_summary`, `member_profile`, `report_month`, `financial_report`, `sales_quality_report`, `performance_report`, `daily_call_report`.

**Direct SQL table names:** `access_decisions`, `collection_assignment_history`, `collection_call_tasks`, `collection_communications`, `collection_discount_requests`, `collection_exceptions`, `collection_notes`, `collection_stage_history`, `collections`, `collections_cases`, `debicheck_mandates`, `leads`, `members`, `membership_applications`, `ptp_agreements`, `ptp_mandate_links`, `ptp_receipts`, `users`.

**Application imports:** `collections`, `collections_engine`, `debicheck`, `ptp`, `queries`.

### 16.12 `onecpase/comms.py`

**Source responsibility:** WhatsApp (Meta Graph API) + Facebook Messenger/comments — inbound webhooks, staff inboxes, and comment-to-lead conversion.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `whatsapp_webhook` :153 | `webhooks_bp.route('/whatsapp', methods=['GET', 'POST'])` | `csrf.exempt` |
| `facebook_webhook` :250 | `webhooks_bp.route('/facebook', methods=['GET', 'POST'])` | `csrf.exempt` |
| `communications_index` :343 | `comms_bp.get('')` | Check handler/blueprint/app guards |
| `whatsapp_inbox` :361 | `comms_bp.get('/whatsapp/<number>')` | Check handler/blueprint/app guards |
| `whatsapp_new_message` :393 | `comms_bp.route('/whatsapp/<number>/new', methods=['GET', 'POST'])` | Check handler/blueprint/app guards |
| `whatsapp_conversation` :450 | `comms_bp.route('/whatsapp/<number>/chat/<int:conv_id>', methods=['GET', 'POST'])` | Check handler/blueprint/app guards |
| `whatsapp_link_lead` :483 | `comms_bp.route('/whatsapp/<number>/chat/<int:conv_id>/link-lead', methods=['GET', 'POST'])` | Check handler/blueprint/app guards |
| `facebook_messages_inbox` :521 | `comms_bp.get('/facebook/messages')` | `permission_required('facebook_messages')` |
| `facebook_conversation` :537 | `comms_bp.route('/facebook/messages/<int:conv_id>', methods=['GET', 'POST'])` | `permission_required('facebook_messages')` |
| `facebook_comments_view` :571 | `comms_bp.get('/facebook/comments')` | `permission_required('facebook_comments')` |
| `facebook_comment_flag` :586 | `comms_bp.post('/facebook/comments/<int:comment_id>/flag')` | `permission_required('facebook_comments')` |
| `facebook_comment_create_lead` :600 | `comms_bp.post('/facebook/comments/<int:comment_id>/create-lead')` | `permission_required('facebook_comments')` |

**Service/class entry points:** `send_whatsapp_message`, `send_facebook_reply`, `verify_meta_signature`.

**Direct SQL table names:** `SET`, `fb_comments`, `fb_conversations`, `fb_messages`, `leads`, `members`, `this`, `users`, `wa_conversations`, `wa_messages`.

**Direct templates:** `communications/facebook_comments.html`, `communications/facebook_conversation.html`, `communications/facebook_messages.html`, `communications/whatsapp_compose.html`, `communications/whatsapp_conversation.html`, `communications/whatsapp_inbox.html`, `communications/whatsapp_link.html`.

**Application imports:** `auth`, `database`, `extensions`, `leads`, `platform_db`, `whatsapp_contacts`, `whatsapp_web`.

### 16.13 `onecpase/communication.py`

**Service/class entry points:** `collection_care_message`, `collection_care_email_subject`, `collection_care_email_html`, `collection_offer_message`.

### 16.14 `onecpase/config.py`

**Service/class entry points:** `class Config`.

### 16.15 `onecpase/contracts.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `share_contract` :288 | `contracts_bp.route('/<int:mid>/share', methods=['POST'])` | `permission_required('all_members')` |
| `public_share` :322 | `contracts_bp.route('/share/<tenant_slug>/<token>', methods=['GET', 'POST'])` | Check handler/blueprint/app guards |
| `view_contract` :485 | `contracts_bp.route('/<int:mid>')` | `permission_required('all_members')` |
| `sign_contract` :541 | `contracts_bp.post('/<int:mid>/sign')` | `permission_required('all_members')` |
| `signature_image` :578 | `contracts_bp.get('/<int:mid>/signature/<role>')` | `permission_required('all_members')` |

**Service/class entry points:** `render_contract`.

**Direct SQL table names:** `compliance_checklists`, `contract_share_tokens`, `contract_signatures`, `contract_templates`, `member_documents`, `members`, `membership_applications`, `tenants`, `users`.

**Direct templates:** `contracts/share_expired.html`, `contracts/share_response.html`, `contracts/view.html`.

**Application imports:** `applications`, `auth`, `comms`, `database`, `encryption`, `extensions`, `platform_db`.

### 16.16 `onecpase/dashboard.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `dashboard` :373 | `dashboard_bp.route('/dashboard')` | `login_required` |
| `sales_dashboard` :507 | `dashboard_bp.route('/sales-dashboard')` | `login_required` |
| `dashboard_reconciliation` :537 | `dashboard_bp.route('/dashboard/reconciliation')` | `login_required` |
| `dashboard_reconciliation_sync` :570 | `dashboard_bp.route('/dashboard/reconciliation/sync', methods=['POST'])` | `login_required` |
| `home` :592 | `dashboard_bp.route('/home')` | `login_required` |

**Service/class entry points:** `build_itensity_reconciliation`, `build_member_reconciliation`, `class ItensityScriptsUnavailable`, `sync_itensity_members_to_local`.

**Direct SQL table names:** `collections`, `itensity_live_snapshot`, `leads`, `leave_requests`, `members`, `users`.

**Direct templates:** `dashboard.html`, `dashboard_reconciliation.html`, `sales_dashboard.html`.

**Application imports:** `auth`, `database`, `elev8_data`, `member_counts`, `ptp`, `sales_kpi`.

### 16.17 `onecpase/database.py`

**Source responsibility:** Database connection factory for 1Cpace.

**Service/class entry points:** `get_db`, `close_db`, `init_db`, `member_ref_prefix`, `next_member_ref`, `current_tenant_name`, `default_sections_config`, `default_terms_parts_config`, `default_debit_mandate_text`, `audit_log`, `member_activity`, `query_scalar`.

**Direct SQL table names:** `audit_logs`, `collections`, `compliance_checklists`, `contract_templates`, `leads`, `leave_requests`, `member_documents`, `member_notes`, `members`, `membership_applications`, `pbx_account_codes`, `pbx_call_logs`, `pbx_webhook_events`, `tenants`, `users`.

**Application imports:** `collections_engine`, `db_adapter`, `events`, `hr_workforce`, `notifications`, `platform_db`, `sales_kpi`, `sales_pipeline`, `schema`.

### 16.18 `onecpase/db_adapter.py`

**Source responsibility:** PostgreSQL compatibility adapter for 1Cpase.

**Service/class entry points:** `class _Row`, `class _CursorWrapper`, `class DbAdapter`, `pg_table_columns`.

**Direct SQL table names:** `information_schema`.

### 16.19 `onecpase/debicheck.py`

**Source responsibility:** 1Cpase DebiCheck mandate persistence and NuPay hand-off.

**Service/class entry points:** `itensity_client_ref_number`, `mandate_from_member`, `validate_mandate`, `validate_stored_mandate`, `insert_mandate`, `push_mandate_to_nupay`, `push_mandate_to_nupay_manual_review`, `check_mandate_status_on_nupay`, `check_member_on_nupay`, `record_push_result`, `normalise_debicheck_status`, `sync_debicheck_to_application`.

**Direct SQL table names:** `compliance_checklists`, `debicheck_mandates`, `membership_applications`.

**Application imports:** `applications`, `debicheck_rules`, `ptp_grant`.

### 16.20 `onecpase/debicheck_routes.py`

**Source responsibility:** Standalone Finance > DebiCheck workflow.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `index` :36 | `debicheck_bp.get('')` | `permission_required('debicheck_mandates')` |
| `coverage` :77 | `debicheck_bp.get('/coverage')` | `permission_required('debicheck_mandates')` |
| `upload_documents` :119 | `debicheck_bp.post('/members/<int:member_id>/documents')` | `permission_required('new_debicheck')` |
| `sync_with_nupay` :126 | `debicheck_bp.route('/sync', methods=['GET', 'POST'])` | `permission_required('debicheck_mandates')` |
| `add` :213 | `debicheck_bp.route('/add', methods=['GET', 'POST'])` | `permission_required('new_debicheck')` |
| `detail` :282 | `debicheck_bp.get('/<int:mandate_id>')` | `permission_required('debicheck_mandates')` |
| `edit` :301 | `debicheck_bp.route('/<int:mandate_id>/edit', methods=['GET', 'POST'])` | `permission_required('new_debicheck')` |
| `requirements` :381 | `debicheck_bp.get('/requirements/<int:member_id>')` | `permission_required('new_debicheck')` |
| `push` :442 | `debicheck_bp.post('/<int:mandate_id>/push')` | `permission_required('new_debicheck')` |
| `review_on_nupay` :508 | `debicheck_bp.post('/<int:mandate_id>/review-on-nupay')` | `permission_required('new_debicheck')` |
| `check_status` :564 | `debicheck_bp.post('/<int:mandate_id>/check-status')` | `permission_required('debicheck_mandates')` |

**Direct SQL table names:** `debicheck_mandates`, `members`, `ptp_agreements`, `ptp_mandate_links`, `users`.

**Direct templates:** `debicheck/_checklist.html`, `debicheck/add.html`, `debicheck/coverage.html`, `debicheck/detail.html`, `debicheck/edit.html`, `debicheck/index.html`, `debicheck/sync.html`.

**Application imports:** `applications`, `auth`, `database`, `debicheck`, `debicheck_rules`, `employee_numbers`, `encryption`, `members`, `ptp_grant`.

### 16.21 `onecpase/debicheck_rules.py`

**Source responsibility:** Shared DebiCheck name and first-debit date requirements.

**Service/class entry points:** `minimum_debit_date`, `debit_date_error`, `account_holder_error`, `mandate_rules_context`.

### 16.22 `onecpase/elev8_data.py`

**Source responsibility:** Read-only access to the Elev8 source SQL Server data.

**Service/class entry points:** `get_elev8_member_count`.

**Direct SQL table names:** `dbo`.

### 16.23 `onecpase/elev8_sync.py`

**Source responsibility:** Authoritative Elev8 member synchronisation.

**Service/class entry points:** `fetch_source_members`, `reconcile_source`, `sync_source_members`, `open_source_connection`.

**Direct SQL table names:** `dbo`, `members`.

**Application imports:** `database`, `encryption`.

### 16.24 `onecpase/employee_numbers.py`

**Source responsibility:** Employee attribution for newly loaded DebiCheck mandates.

**Service/class entry points:** `current_employee_number`, `employee_number_context`.

**Direct SQL table names:** `users`.

**Application imports:** `database`.

### 16.25 `onecpase/encryption.py`

**Source responsibility:** Field-level encryption for 1Cpace PII.

**Service/class entry points:** `class EncryptionConfigurationError`, `encryption_enabled`, `encrypt`, `decrypt`, `hash_for_lookup`, `encrypt_member`, `decrypt_member`, `encrypt_mandate`, `decrypt_mandate`.

### 16.26 `onecpase/events.py`

**Source responsibility:** One-off event registration: a public link anyone can register through, and a staff-facing view of who signed up.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `event_banner` :272 | `events_bp.get('/<slug>/banner')` | Check handler/blueprint/app guards |
| `register` :287 | `events_bp.route('/<slug>', methods=['GET', 'POST'])` | `limiter.limit('10 per minute')` |
| `ticket` :439 | `events_bp.get('/<slug>/ticket/<token>')` | Check handler/blueprint/app guards |
| `ticket_checkin` :454 | `events_bp.post('/<slug>/ticket/<token>/checkin')` | `limiter.limit('10 per minute')` |
| `events_index` :476 | `admin_events_bp.route('/')` | `_admin_required` |
| `event_add` :489 | `admin_events_bp.route('/add', methods=['GET', 'POST'])` | `_admin_required` |
| `event_edit` :550 | `admin_events_bp.route('/<int:event_id>/edit', methods=['GET', 'POST'])` | `_admin_required` |
| `event_toggle` :609 | `admin_events_bp.post('/<int:event_id>/toggle')` | `_admin_required` |
| `event_registrations` :621 | `admin_events_bp.get('/<int:event_id>/registrations')` | `_admin_required` |
| `event_registrations_resend_tickets` :643 | `admin_events_bp.post('/<int:event_id>/registrations/resend-tickets')` | `_admin_required` |
| `event_registrations_export` :675 | `admin_events_bp.get('/<int:event_id>/registrations/export.csv')` | `_admin_required` |

**Service/class entry points:** `ensure_events_schema`, `normalise_phone`.

**Direct SQL table names:** `event_registrations`, `events`.

**Direct templates:** `admin/event_form.html`, `admin/event_registrations.html`, `admin/events.html`, `public/event_register.html`, `public/event_register_success.html`, `public/event_ticket.html`.

**Application imports:** `admin`, `comms`, `database`, `db_adapter`, `extensions`.

### 16.27 `onecpase/extensions.py`

### 16.28 `onecpase/first_month_collections.py`

**Source responsibility:** A sales consultant's own first-month failed debits.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `index` :37 | `first_month_bp.get('/')` | `login_required` |
| `record_contact` :73 | `first_month_bp.post('/<int:case_id>/contact')` | `login_required` |

**Direct SQL table names:** `collection_communications`, `collections_cases`, `members`, `users`.

**Direct templates:** `collections/first_month.html`.

**Application imports:** `auth`, `collections`, `collections_engine`, `database`, `ptp`.

### 16.29 `onecpase/fitness.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `fitness_index` :97 | `fitness_bp.route('/')` | `permission_required('classes')` |
| `fitness_add` :124 | `fitness_bp.route('/add', methods=['GET', 'POST'])` | `permission_required('fitness_module')` |
| `fitness_edit` :157 | `fitness_bp.route('/<int:fid>/edit', methods=['GET', 'POST'])` | `permission_required('fitness_module')` |
| `fitness_bookings` :194 | `fitness_bp.route('/<int:fid>/bookings')` | `permission_required('classes')` |
| `fitness_book` :228 | `fitness_bp.post('/<int:fid>/book')` | `permission_required('classes')` |
| `booking_cancel` :259 | `fitness_bp.post('/bookings/<int:bid>/cancel')` | `permission_required('classes')` |
| `assessments_index` :276 | `fitness_bp.get('/assessments')` | `permission_required('assessments')` |
| `assessment_add` :293 | `fitness_bp.route('/assessments/add', methods=['GET', 'POST'])` | `permission_required('assessments')` |
| `workout_plans_index` :331 | `fitness_bp.get('/workout-plans')` | `permission_required('workout_plans')` |
| `workout_plan_add` :348 | `fitness_bp.route('/workout-plans/add', methods=['GET', 'POST'])` | `permission_required('workout_plans')` |
| `dailies_index` :381 | `fitness_bp.get('/dailies')` | `permission_required('dailies')` |
| `daily_add` :439 | `fitness_bp.post('/dailies/add')` | `permission_required('dailies')` |
| `incidents_index` :482 | `fitness_bp.get('/incidents')` | `permission_required('incident_log')` |
| `incident_add` :548 | `fitness_bp.post('/incidents/add')` | `permission_required('incident_log')` |
| `incident_update` :587 | `fitness_bp.post('/incidents/<int:iid>/update')` | `permission_required('incident_log')` |
| `body_matrix_index` :611 | `fitness_bp.get('/body-matrix')` | `permission_required('body_matrix')` |
| `body_matrix_member` :636 | `fitness_bp.get('/body-matrix/<int:mid>')` | `permission_required('body_matrix')` |
| `body_matrix_add` :692 | `fitness_bp.post('/body-matrix/<int:mid>/add')` | `permission_required('body_matrix')` |
| `nutrition_index` :729 | `fitness_bp.get('/nutrition')` | `permission_required('nutrition_guide')` |
| `nutrition_add` :758 | `fitness_bp.route('/nutrition/add', methods=['GET', 'POST'])` | `permission_required('nutrition_guide')` |
| `supplements_index` :797 | `fitness_bp.get('/supplements')` | `permission_required('supplements')` |
| `supplement_add` :827 | `fitness_bp.route('/supplements/add', methods=['GET', 'POST'])` | `permission_required('supplements')` |
| `supplement_detail` :862 | `fitness_bp.get('/supplements/<int:sid>')` | `permission_required('supplements')` |
| `supplement_assign` :888 | `fitness_bp.post('/supplements/<int:sid>/assign')` | `permission_required('supplements')` |
| `member_supplements_view` :922 | `fitness_bp.get('/members/<int:mid>/supplements')` | `permission_required('supplements')` |
| `anatomy` :950 | `fitness_bp.get('/anatomy')` | `permission_required('fitness_module')` |

**Service/class entry points:** `as_int`, `as_float`, `parse_date_value`, `get_active_members`, `get_fitness_staff`, `member_full_name`, `whatsapp_url`, `incident_whatsapp_message`.

**Direct SQL table names:** `assessments`, `body_matrix`, `class_bookings`, `fitness_classes`, `incidents`, `instructor_dailies`, `member_supplements`, `members`, `nutrition_foods`, `supplements`, `users`, `workout_plans`.

**Direct templates:** `fitness/add.html`, `fitness/anatomy.html`, `fitness/assessment_form.html`, `fitness/assessments.html`, `fitness/body_matrix_index.html`, `fitness/body_matrix_member.html`, `fitness/bookings.html`, `fitness/dailies.html`, `fitness/edit.html`, `fitness/incidents.html`, `fitness/index.html`, `fitness/member_supplements.html`, `fitness/nutrition.html`, `fitness/nutrition_add.html`, `fitness/plan_form.html`, `fitness/plans.html`, `fitness/supplement_add.html`, `fitness/supplement_detail.html`, `fitness/supplements.html`.

**Application imports:** `auth`, `database`, `encryption`, `fitness_anatomy`.

### 16.30 `onecpase/fitness_anatomy.py`

**Source responsibility:** Selectable muscle regions and introductory exercise cues.

### 16.31 `onecpase/fitness_cycle.py`

**Source responsibility:** Member joining cohorts in complete 15th–14th business cycles.

**Service/class entry points:** `today`, `cycle_key`, `cycle_bounds`, `joining_window`, `joiners`, `onboarding_cohort`.

**Direct SQL table names:** `members`.

**Application imports:** `encryption`.

### 16.32 `onecpase/fitness_journey.py`

**Source responsibility:** Fitness intake, Reception welcomes and client session acknowledgements.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `member_intake` :121 | `journey_bp.route('/<int:mid>', methods=['GET', 'POST'])` | `intake_access` |
| `send_welcome` :158 | `journey_bp.post('/<int:mid>/welcome')` | `intake_access` |
| `sign_session` :172 | `journey_bp.post('/visit/<int:vid>/sign')` | `permission_required('fitness_module')` |

**Service/class entry points:** `intake_access`, `display_stamp`, `ensure_journey`, `welcome_text`, `queue_welcome`, `sync_welcomes`.

**Direct SQL table names:** `assessments`, `fitness_intake`, `fitness_journey_policy`, `fitness_signoffs`, `member_onboarding`, `members`, `onboarding_visits`, `tenants`, `users`, `wa_conversations`, `wa_messages`.

**Direct templates:** `fitness/intake.html`.

**Application imports:** `auth`, `comms`, `database`, `encryption`, `fitness`, `fitness_cycle`, `onboarding`, `phases`, `platform_db`, `whatsapp_contacts`.

### 16.33 `onecpase/health.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `healthz` :10 | `health_bp.get('/healthz')` | Check handler/blueprint/app guards |

**Direct SQL table names:** `users`.

**Application imports:** `database`.

### 16.34 `onecpase/hr.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `staff_index` :89 | `hr_bp.route('/')` | `permission_required('hr_staff')` |
| `staff_add` :104 | `hr_bp.route('/add', methods=['GET', 'POST'])` | `permission_required('user_management')` |
| `staff_edit` :155 | `hr_bp.route('/<int:uid>/edit', methods=['GET', 'POST'])` | `permission_required('user_management')` |
| `leave_index` :223 | `hr_bp.route('/leave')` | `login_required` |
| `leave_request` :230 | `hr_bp.route('/leave/request', methods=['GET', 'POST'])` | `login_required` |
| `leave_approve` :237 | `hr_bp.post('/leave/<int:lid>/approve')` | `permission_required('hr_module')` |
| `leave_reject` :244 | `hr_bp.post('/leave/<int:lid>/reject')` | `permission_required('hr_module')` |
| `employee_index` :251 | `hr_bp.route('/employees')` | `permission_required('hr_staff')` |
| `employee_add` :266 | `hr_bp.route('/employees/add', methods=['GET', 'POST'])` | `permission_required('hr_staff')` |
| `employee_edit` :302 | `hr_bp.route('/employees/<int:sid>/edit', methods=['GET', 'POST'])` | `permission_required('hr_staff')` |
| `employee_detail` :347 | `hr_bp.get('/employees/<int:sid>')` | `permission_required('hr_staff')` |
| `attendance_log` :372 | `hr_bp.post('/attendance/log')` | `permission_required('hr_staff')` |
| `staff_leave_add` :406 | `hr_bp.route('/employees/<int:sid>/leave/add', methods=['GET', 'POST'])` | `permission_required('hr_leave')` |
| `payroll_index` :416 | `hr_bp.get('/payroll')` | `permission_required('hr_payroll')` |
| `payroll_pay` :437 | `hr_bp.post('/payroll/<int:pid>/pay')` | `permission_required('hr_payroll')` |
| `access_attendance` :452 | `hr_bp.get('/access-attendance')` | `permission_required('hr_timesheets')` |
| `timesheets_index` :483 | `hr_bp.get('/timesheets')` | `permission_required('hr_timesheets')` |
| `timesheets_save` :489 | `hr_bp.post('/timesheets/save')` | `permission_required('hr_timesheets')` |
| `timesheet_staff_week` :495 | `hr_bp.get('/timesheets/<int:sid>')` | `permission_required('hr_timesheets')` |

**Service/class entry points:** `protect_legacy_hr`, `as_float`, `parse_date_value`, `week_bounds`, `calculate_hours_worked`.

**Direct SQL table names:** `attendance`, `hr_profiles`, `leave_requests`, `payroll`, `staff`, `user_permissions`, `users`.

**Direct templates:** `hr/access_attendance.html`, `hr/add.html`, `hr/edit.html`, `hr/employee_detail.html`, `hr/employee_form.html`, `hr/employees.html`, `hr/index.html`, `hr/payroll.html`.

**Application imports:** `auth`, `database`, `hr_access`, `hr_workforce`, `hr_workforce_routes`, `permissions`, `staff_attendance`.

### 16.35 `onecpase/hr_access.py`

**Source responsibility:** Shared HR access policy for routes, templates, and salary updates.

**Service/class entry points:** `can_access_payroll`.

**Direct SQL table names:** `users`.

**Application imports:** `database`, `query_routing`.

### 16.36 `onecpase/hr_workforce.py`

**Source responsibility:** HR accounting and workflow rules. No access scan is interpreted as paid time.

**Service/class entry points:** `today`, `department_key`, `service_account`, `migrate`, `transaction`, `audit`, `actor`, `is_admin`, `granted`, `can_review`, `visible_ids`, `ensure_profile`, `iso`, `days_between`, `anniversary`, `add_months`, `cycle`, `holidays`, `work_dates`, `units`, `account_balance`, `current_accounts`, `annual_entitlement`, `submit_leave`, `leave_conflicts`, `uncovered_leave`, `decide_leave`, `minute`, `interval`, `week_start`, `digest`, `week_warnings`, `notify`, `save_week`, `sign_week`, `decide_week`.

**Direct SQL table names:** `hr_accounts`, `hr_audit`, `hr_ledger`, `hr_openings`, `hr_policy_settings`, `hr_profiles`, `hr_requests`, `hr_shifts`, `hr_signatures`, `hr_weeks`, `staff`, `staff_notifications`, `user_permissions`, `users`.

**Application imports:** `database`, `hr_access`.

### 16.37 `onecpase/hr_workforce_routes.py`

**Source responsibility:** Employee self-service and reviewed HR operations.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `reviews` :56 | `workforce_bp.get('/reviews')` | `login_required` |
| `dashboard` :71 | `workforce_bp.get('/me')` | `login_required` |
| `setup` :85 | `workforce_bp.route('/people', methods=['GET', 'POST'])` | `login_required` |
| `opening_propose` :143 | `workforce_bp.post('/opening-balances')` | `login_required` |
| `opening_confirm` :186 | `workforce_bp.post('/opening-balances/<int:oid>/confirm')` | `login_required` |
| `adjustment_propose` :234 | `workforce_bp.post('/balance-adjustments')` | `login_required` |
| `balances` :267 | `workforce_bp.get('/balances')` | `login_required` |
| `calendar` :277 | `workforce_bp.get('/leave/calendar')` | `login_required` |
| `withdraw` :339 | `workforce_bp.post('/leave/<int:lid>/withdraw')` | `login_required` |
| `weeks` :364 | `workforce_bp.route('/weeks', methods=['GET', 'POST'])` | `login_required` |
| `week_decision` :411 | `workforce_bp.post('/weeks/<int:wid>/decision')` | `login_required` |
| `roster` :443 | `workforce_bp.route('/roster', methods=['GET', 'POST'])` | `login_required` |
| `publish_roster` :461 | `workforce_bp.post('/roster/publish')` | `login_required` |
| `delete_draft_shift` :479 | `workforce_bp.post('/roster/<int:sid>/delete')` | `login_required` |
| `revise_roster` :492 | `workforce_bp.post('/roster/revise')` | `login_required` |

**Service/class entry points:** `employee_only`, `invalid`, `forbidden`, `profile`, `people`, `require_admin`, `allowed_view`, `leave_list`, `request_leave`, `leave_decision`, `attendance_evidence`, `save_shift`.

**Direct SQL table names:** `hr_accounts`, `hr_ledger`, `hr_openings`, `hr_profiles`, `hr_requests`, `hr_shifts`, `hr_signatures`, `hr_weeks`, `leave_requests`, `staff`, `staff_notifications`, `timesheets`, `users`.

**Direct templates:** `hr/availability.html`, `hr/balances.html`, `hr/leave_request.html`, `hr/me.html`, `hr/people_setup.html`, `hr/reviews.html`, `hr/roster.html`, `hr/workforce_error.html`, `hr/workforce_leave.html`, `hr/workforce_week.html`.

**Application imports:** `auth`, `database`, `staff_attendance`.

### 16.38 `onecpase/inspect_mode.py`

**Source responsibility:** Dev-only 'Inspect' overlay.

**Service/class entry points:** `class _InspectEnvironment`, `enable_inspect_mode`.

### 16.39 `onecpase/integrations.py`

**Source responsibility:** External service integrations (Itensity, etc.).

**Service/class entry points:** `push_member_to_itensity`, `update_member_in_itensity`, `push_pos_payment_to_itensity`.

**Direct SQL table names:** `the`, `users`.

**Application imports:** `database`, `playwright_lock`.

### 16.40 `onecpase/internal_comms.py`

**Source responsibility:** Internal staff communication: direct messages and an announcements board.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `messages_index` :107 | `internal_comms_bp.get('/messages')` | `login_required` |
| `thread` :140 | `internal_comms_bp.route('/messages/<int:other_id>', methods=['GET', 'POST'])` | `login_required` |
| `announcements_index` :194 | `internal_comms_bp.get('/announcements')` | `login_required` |
| `announcement_create` :217 | `internal_comms_bp.post('/announcements/new')` | `login_required` |
| `announcement_read` :239 | `internal_comms_bp.post('/announcements/<int:announcement_id>/read')` | `login_required` |
| `announcement_archive` :256 | `internal_comms_bp.post('/announcements/<int:announcement_id>/archive')` | `login_required` |
| `announcement_pin` :269 | `internal_comms_bp.post('/announcements/<int:announcement_id>/pin')` | `login_required` |

**Service/class entry points:** `may_announce`, `unread_counts`.

**Direct SQL table names:** `announcement_reads`, `announcements`, `staff_messages`, `staff_threads`, `users`.

**Direct templates:** `internal/announcements.html`, `internal/messages.html`, `internal/thread.html`.

**Application imports:** `auth`, `database`.

### 16.41 `onecpase/leads.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `leads_index` :535 | `leads_bp.route('/')` | `permission_required('view_leads')` |
| `leads_add` :653 | `leads_bp.route('/add', methods=['GET', 'POST'])` | `permission_required('capture_leads')` |
| `lead_detail` :774 | `leads_bp.route('/<int:lid>')` | `permission_required('view_leads')` |
| `lead_edit` :845 | `leads_bp.route('/<int:lid>/edit', methods=['GET', 'POST'])` | `permission_required('capture_leads')` |
| `update_status` :918 | `leads_bp.post('/<int:lid>/status')` | `permission_required('capture_leads')` |
| `add_activity` :997 | `leads_bp.post('/<int:lid>/activities')` | `permission_required('capture_leads')` |
| `complete_activity` :1239 | `leads_bp.post('/activities/<int:activity_id>/complete')` | `permission_required('capture_leads')` |
| `approve_offer` :1264 | `leads_bp.post('/activities/<int:activity_id>/approve')` | `permission_required('approve_sales_offers')` |
| `funnel_report` :1285 | `leads_bp.route('/funnel')` | `permission_required('view_leads')` |
| `convert_lead` :1383 | `leads_bp.route('/<int:lid>/convert', methods=['GET', 'POST'])` | `permission_required('capture_leads')` |

**Service/class entry points:** `sa_id_birth_date`, `sa_id_luhn_valid`, `normalise_phone`.

**Direct SQL table names:** `compliance_checklists`, `lead_activities`, `leads`, `members`, `membership_applications`, `pbx_call_logs`, `tariffs`, `users`.

**Direct templates:** `leads/add.html`, `leads/convert.html`, `leads/detail.html`, `leads/edit.html`, `leads/funnel.html`, `leads/index.html`.

**Application imports:** `auth`, `database`, `encryption`, `sales_kpi`, `sales_pipeline`.

### 16.42 `onecpase/member_audit.py`

**Source responsibility:** Classify every member by what Itensity, NuPay and this app each say about them.

**Service/class entry points:** `classify`, `mismatch_flags`.

### 16.43 `onecpase/member_counts.py`

**Source responsibility:** Canonical member population counts for application-facing summaries.

**Service/class entry points:** `get_member_counts`.

**Direct SQL table names:** `members`.

### 16.44 `onecpase/members.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `members_index` :341 | `members_bp.route('/members')` | `permission_required('all_members')` |
| `members_add` :520 | `members_bp.route('/members/add', methods=['GET', 'POST'])` | `permission_required('add_member')` |
| `member_detail` :728 | `members_bp.route('/members/<int:mid>')` | `permission_required('all_members')` |
| `member_check_debicheck` :950 | `members_bp.post('/members/<int:mid>/check-debicheck')` | `permission_required('all_members')` |
| `member_add_note` :978 | `members_bp.route('/members/<int:mid>/notes', methods=['POST'])` | `permission_required('all_members')` |
| `member_upload_doc` :989 | `members_bp.route('/members/<int:mid>/documents', methods=['POST'])` | `permission_required('all_members')` |
| `member_download_doc` :1052 | `members_bp.route('/members/<int:mid>/documents/<int:doc_id>/download')` | `permission_required('all_members')` |
| `member_analyse_doc` :1069 | `members_bp.route('/members/<int:mid>/documents/<int:doc_id>/analyse', methods=['POST'])` | `permission_required('all_members')` |
| `member_delete_doc` :1123 | `members_bp.route('/members/<int:mid>/documents/<int:doc_id>/delete', methods=['POST'])` | `permission_required('all_members')` |
| `member_edit` :1161 | `members_bp.route('/members/<int:mid>/edit', methods=['GET', 'POST'])` | `permission_required('add_member')` |

**Direct SQL table names:** `collections`, `collections_cases`, `compliance_checklists`, `contact_logs`, `dbo`, `debicheck_mandates`, `member`, `member_documents`, `member_notes`, `members`, `membership_applications`, `nupay_mandate_checks`, `payment_plan_items`, `ptp_agreements`, `ptp_receipts`, `queries`, `statement_analyses`, `tariffs`, `turnstile_events`, `users`.

**Direct templates:** `members/add.html`, `members/detail.html`, `members/edit.html`, `members/index.html`.

**Application imports:** `applications`, `auth`, `communication`, `database`, `debicheck`, `debicheck_rules`, `elev8_data`, `employee_numbers`, `encryption`, `member_counts`, `nupay_reconciliation`, `ptp`, `statement_parser`, `statement_uploads`.

### 16.45 `onecpase/notifications.py`

**Source responsibility:** Tenant-local staff follow-ups; no external message transport.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `index` :159 | `notifications_bp.get('')` | `login_required` |
| `count` :197 | `notifications_bp.get('/count')` | `login_required` |
| `acknowledge` :205 | `notifications_bp.post('/<int:notification_id>/acknowledge')` | `login_required` |

**Service/class entry points:** `ensure_schema`, `validate_supervisor`, `sync_notifications`, `refresh`, `pending_count`.

**Direct SQL table names:** `approvals`, `collection_exceptions`, `hr_weeks`, `lead_activities`, `leads`, `membership_applications`, `ptp_agreements`, `queries`, `query_notes`, `staff_notifications`, `users`.

**Direct templates:** `notifications/index.html`.

**Application imports:** `auth`, `database`, `phases`, `query_routing`.

### 16.46 `onecpase/nupay_driver.py`

**Source responsibility:** NuPay new merchant portal mandate push (merchant.nupay.co.za).

**Service/class entry points:** `class PushResult`, `class MandateStatusResult`, `push_mandate`, `push_mandate_manual_review`, `fetch_mandate_report_rows`, `match_mandate_row`, `refresh_mandate_status`, `wait_for_mandate_status`, `class CancelResult`, `check_cancellation_target`, `read_maintenance_reply`, `extract_maintenance_result`, `cancel_mandate`.

**Application imports:** `debicheck_rules`, `playwright_lock`.

### 16.47 `onecpase/nupay_reconciliation.py`

**Source responsibility:** NuPay reconciliation: store the uploaded Transaction Report, compare every member, answer for one.

**Service/class entry points:** `read_upload`, `store_upload`, `stored_rows`, `latest_roster`, `rebuild_snapshot`, `overview`, `member_list`, `member_view`.

**Direct SQL table names:** `collections`, `debicheck_mandates`, `members`, `nupay_import_runs`, `nupay_transactions`, `reconciliation_snapshot`, `users`.

**Application imports:** `debicheck`, `members`, `nupay_sync`, `ptp`.

### 16.48 `onecpase/nupay_sync.py`

**Source responsibility:** Bring V8's DebiCheck mandates in line with NuPay's Mandate Report in one pass.

**Service/class entry points:** `sync_from_report`.

**Direct SQL table names:** `debicheck_mandates`, `members`, `the`, `those`.

**Application imports:** `database`, `debicheck`, `encryption`, `ptp_grant`.

### 16.49 `onecpase/nupay_transactions.py`

**Source responsibility:** NuPay Transaction Report -> member collection history.

**Service/class entry points:** `normalise_row`, `parse_csv`, `transaction_key`, `plan_import`, `apply_plan`, `paying_but_in_arrears`.

**Direct SQL table names:** `collections`, `members`.

**Application imports:** `nupay_sync`, `ptp`.

### 16.50 `onecpase/onboarding.py`

**Source responsibility:** Daily fitness onboarding for newly joined members.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `today_list` :148 | `onboarding_bp.route('/')` | `login_required`, `permission_required('fitness_module')` |
| `manual_arrival` :178 | `onboarding_bp.post('/arrive')` | `login_required`, `permission_required('fitness_module')` |
| `record_visit` :187 | `onboarding_bp.route('/visit/<int:vid>', methods=['GET', 'POST'])` | `login_required`, `permission_required('fitness_module')` |
| `save_assessment` :232 | `onboarding_bp.post('/visit/<int:vid>/assessment')` | `login_required`, `permission_required('fitness_module')` |
| `seven_day_review` :296 | `onboarding_bp.route('/<int:oid>/review', methods=['GET', 'POST'])` | `login_required`, `permission_required('fitness_module')` |
| `report` :310 | `onboarding_bp.route('/report')` | `login_required`, `permission_required('fitness_module')` |
| `pending_count` :330 | `onboarding_bp.get('/pending-count')` | `login_required` |
| `cycle_joiners` :336 | `onboarding_bp.get('/joiners')` | `permission_required('fitness_module')` |

**Service/class entry points:** `calculate_bmi`, `calculate_daily_calories`, `add_onboarding_columns`, `ensure_onboarding_rows`, `register_arrival`, `sync_arrivals_from_turnstile`.

**Direct SQL table names:** `assessments`, `body_matrix`, `fitness_intake`, `fitness_signoffs`, `information_schema`, `member_onboarding`, `members`, `onboarding_sync_state`, `onboarding_visits`, `turnstile_events`, `users`.

**Direct templates:** `fitness/cycle_joiners.html`, `fitness/onboarding_report.html`, `fitness/onboarding_review.html`, `fitness/onboarding_session.html`, `fitness/onboarding_today.html`.

**Application imports:** `auth`, `database`, `encryption`, `fitness_cycle`, `fitness_journey`.

### 16.51 `onecpase/operations.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `equipment_index` :13 | `operations_bp.route('/')` | `permission_required('equipment')` |
| `equipment_add` :50 | `operations_bp.route('/add', methods=['GET', 'POST'])` | `permission_required('equipment')` |
| `equipment_edit` :81 | `operations_bp.route('/<int:eid>/edit', methods=['GET', 'POST'])` | `permission_required('equipment')` |
| `maintenance_log` :117 | `operations_bp.route('/<int:eid>/maintenance')` | `permission_required('maintenance')` |
| `maintenance_add` :139 | `operations_bp.post('/<int:eid>/maintenance/add')` | `permission_required('maintenance')` |

**Direct SQL table names:** `equipment`, `maintenance_log`, `users`.

**Direct templates:** `operations/add.html`, `operations/edit.html`, `operations/index.html`, `operations/maintenance.html`.

**Application imports:** `auth`, `database`.

### 16.52 `onecpase/pbx.py`

**Source responsibility:** Yeastar P-Series PBX integration for 1Cpace.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `dial` :255 | `pbx_bp.post('/dial')` | `login_required`, `permission_required('pbx_call')` |
| `webhook` :318 | `pbx_bp.post('/webhook')`<br>`pbx_bp.post('/webhook/<tenant_slug>')` | `csrf.exempt` |
| `call_outcome` :355 | `pbx_bp.post('/calls/<int:call_id>/outcome')` | `login_required`, `permission_required('pbx_call')` |
| `calls` :379 | `pbx_bp.get('/calls')` | `login_required` |
| `health` :390 | `pbx_bp.get('/health')` | `login_required` |

**Direct SQL table names:** `collection_call_tasks`, `leads`, `members`, `pbx_account_codes`, `pbx_call_logs`, `tenants`, `users`.

**Direct templates:** `pbx/calls.html`.

**Application imports:** `auth`, `database`, `extensions`, `leads`, `platform_db`.

### 16.53 `onecpase/permissions.py`

**Source responsibility:** All module permission definitions for 1Cpase.

**Service/class entry points:** `staff_access_profile`, `effective_permissions_for`, `default_permissions_for`.

### 16.54 `onecpase/phases.py`

**Source responsibility:** Which parts of the app are open, phase by phase.

**Service/class entry points:** `normalise`, `phase_of`, `phase_name`, `enabled_blueprints`, `is_enabled`, `nav_section_enabled`.

### 16.55 `onecpase/platform_admin.py`

**Source responsibility:** Platform-operator area: add/manage gyms (tenants) on this 1Cpace install.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `login` :136 | `platform_bp.route('/login', methods=['GET', 'POST'])` | `limiter.limit('10 per minute')` |
| `logout` :158 | `platform_bp.get('/logout')` | Check handler/blueprint/app guards |
| `index` :170 | `platform_bp.route('/')` | `_platform_login_required` |
| `tenants_index` :175 | `platform_bp.route('/tenants')` | `_platform_login_required` |
| `tenant_enter` :185 | `platform_bp.post('/tenants/<int:tenant_id>/enter')` | `_platform_login_required`, `limiter.limit('30 per minute')` |
| `tenant_exit` :245 | `platform_bp.post('/tenant/exit')` | `_platform_login_required` |
| `tenants_add` :262 | `platform_bp.route('/tenants/add', methods=['GET', 'POST'])` | `_platform_login_required`, `limiter.limit('5 per hour')` |
| `tenants_edit` :334 | `platform_bp.route('/tenants/<int:tenant_id>/edit', methods=['GET', 'POST'])` | `_platform_login_required` |
| `tenants_toggle` :373 | `platform_bp.post('/tenants/<int:tenant_id>/toggle')` | `_platform_login_required` |

**Direct SQL table names:** `platform_admins`, `platform_audit_log`, `tenants`, `users`.

**Direct templates:** `platform/login.html`, `platform/tenant_form.html`, `platform/tenants.html`.

**Application imports:** `database`, `extensions`, `platform_db`, `tenants`.

### 16.56 `onecpase/platform_db.py`

**Source responsibility:** Connection + schema management for platform.db — the small tenant registry that lists every gym on this 1Cpase install (slug, name, logo, which SQLite file holds its data). Never holds any gym's actual data.

**Service/class entry points:** `get_platform_db`, `close_platform_db`, `resolve_integration_tenant`, `register_integration`, `init_platform_db`.

**Direct SQL table names:** `SET`, `tenant_integrations`, `tenants`.

**Application imports:** `schema`.

### 16.57 `onecpase/playwright_lock.py`

**Source responsibility:** Process-wide lock serializing all Playwright browser automation.

### 16.58 `onecpase/ptp.py`

**Source responsibility:** PTP / Collection module for 1Cpase.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `ptp_dashboard` :264 | `ptp_bp.route('/ptp/')` | `permission_required('all_collections')` |
| `legal_referrals` :356 | `ptp_bp.route('/ptp/legal')` | `permission_required('all_collections')` |
| `refer_to_legal` :387 | `ptp_bp.post('/members/<int:mid>/legal/refer')` | `permission_required('all_collections')` |
| `resolve_legal_referral` :454 | `ptp_bp.post('/members/<int:mid>/legal/resolve')` | `permission_required('all_collections')` |
| `preview_automations` :728 | `ptp_bp.route('/ptp/run-automations/preview')` | `permission_required('all_collections')` |
| `run_automations` :739 | `ptp_bp.route('/ptp/run-automations', methods=['POST'])` | `permission_required('all_collections')` |
| `ptp_create` :757 | `ptp_bp.route('/members/<int:mid>/ptp/create', methods=['POST'])` | `permission_required('all_collections')` |
| `ptp_log_contact` :1093 | `ptp_bp.route('/members/<int:mid>/ptp/contact', methods=['POST'])` | `permission_required('all_collections')` |
| `ptp_upload_receipt` :1124 | `ptp_bp.route('/members/<int:mid>/ptp/receipt', methods=['POST'])` | `permission_required('all_collections')` |
| `ptp_record_upfront` :1192 | `ptp_bp.route('/members/<int:mid>/ptp/<int:ptp_id>/upfront', methods=['POST'])` | `permission_required('all_collections')` |
| `ptp_verify_receipt` :1261 | `ptp_bp.route('/members/<int:mid>/ptp/receipt/<int:rid>/verify', methods=['POST'])` | `roles_required('admin', 'manager')` |
| `ptp_update_status` :1337 | `ptp_bp.route('/members/<int:mid>/ptp/<int:ptp_id>/status', methods=['POST'])` | `permission_required('all_collections')` |
| `ptp_approve` :1392 | `ptp_bp.route('/members/<int:mid>/ptp/<int:ptp_id>/approve', methods=['POST'])` | `roles_required('admin', 'manager')` |
| `ptp_update_access` :1439 | `ptp_bp.route('/members/<int:mid>/ptp/access', methods=['POST'])` | `roles_required('admin', 'manager')` |

**Service/class entry points:** `compute_arrears_from_profile`, `bulk_member_arrears`.

**Direct SQL table names:** `collection_exceptions`, `collections`, `contact_logs`, `debicheck_mandates`, `legal_referrals`, `member_documents`, `members`, `payment_plan_items`, `ptp_agreements`, `ptp_mandate_links`, `ptp_receipts`, `users`.

**Direct templates:** `ptp/dashboard.html`, `ptp/legal.html`.

**Application imports:** `auth`, `collections`, `collections_decision`, `collections_engine`, `database`, `members`, `queries`.

### 16.59 `onecpase/ptp_grant.py`

**Source responsibility:** The gate between recording a PTP and granting it.

**Service/class entry points:** `has_approved_mandate`, `required_mandates`, `record_required`, `link_mandate`, `evaluate_grant`, `manager_review_reasons`, `next_debit_date`, `create_arrangement`, `on_mandate_status_changed`.

**Direct SQL table names:** `debicheck_mandates`, `legal_referrals`, `member_documents`, `members`, `ptp_agreements`, `ptp_mandate_links`.

**Application imports:** `collections_engine`, `database`, `debicheck`.

### 16.60 `onecpase/public_leads.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `public_join` :43 | `join_bp.route('/<tenant_slug>/<channel>', methods=['GET', 'POST'])` | `limiter.limit('10 per minute')` |

**Direct SQL table names:** `leads`, `tenants`, `users`.

**Direct templates:** `public/join.html`, `public/join_success.html`.

**Application imports:** `database`, `extensions`, `leads`, `platform_db`.

### 16.61 `onecpase/queries.py`

**Source responsibility:** Queries & Tickets — Phase 3.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `member_summary` :820 | `queries_bp.route('/member-summary/<int:member_id>')` | `permission_required(QUERY_PERMISSION)` |
| `index` :842 | `queries_bp.route('/')` | `permission_required(QUERY_PERMISSION)` |
| `add` :950 | `queries_bp.route('/add', methods=['GET', 'POST'])` | `permission_required(QUERY_PERMISSION)` |
| `detail` :1220 | `queries_bp.route('/<int:qid>')` | `permission_required(QUERY_PERMISSION)` |
| `add_note` :1303 | `queries_bp.route('/<int:qid>/note', methods=['POST'])` | `permission_required(QUERY_PERMISSION)` |
| `update` :1318 | `queries_bp.route('/<int:qid>/update', methods=['POST'])` | `permission_required(QUERY_PERMISSION)` |
| `close` :1409 | `queries_bp.route('/<int:qid>/close', methods=['POST'])` | `permission_required(QUERY_PERMISSION)` |
| `escalate` :1448 | `queries_bp.route('/<int:qid>/escalate', methods=['POST'])` | `permission_required(QUERY_PERMISSION)` |
| `request_approval` :1477 | `queries_bp.route('/<int:qid>/request-approval', methods=['POST'])` | `permission_required(QUERY_PERMISSION)` |
| `decide` :1528 | `queries_bp.route('/<int:qid>/decide', methods=['POST'])` | `roles_required('admin', 'manager')` |
| `convert_ptp` :1572 | `queries_bp.route('/<int:qid>/convert-ptp', methods=['POST'])` | `permission_required(QUERY_PERMISSION)` |

**Service/class entry points:** `calculate_cancellation_fee`, `calculate_cancellation_settlement`, `status_options_for`, `followup_due_count`, `link_ptp`, `sync_from_ptp`, `approved_discount_for`.

**Direct SQL table names:** `collections`, `debicheck_mandates`, `form`, `member_documents`, `members`, `ptp_agreements`, `queries`, `query_notes`, `users`.

**Direct templates:** `queries/add.html`, `queries/detail.html`, `queries/index.html`.

**Application imports:** `auth`, `collections_engine`, `database`, `encryption`, `members`, `notifications`, `ptp`, `query_routing`.

### 16.62 `onecpase/query_routing.py`

**Source responsibility:** Elev8 query owners and assignment email notifications.

**Service/class entry points:** `enabled`, `staff`, `route`, `notify_assignment`.

**Direct SQL table names:** `queries`, `users`.

**Application imports:** `extensions`.

### 16.63 `onecpase/reconciliation.py`

**Source responsibility:** Collections > Reconciliation: NuPay, Itensity and this app compared for every member.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `overview` :29 | `reconciliation_bp.get('')` | `permission_required('all_collections')` |
| `members` :37 | `reconciliation_bp.get('/members')` | `permission_required('all_collections')` |
| `upload` :56 | `reconciliation_bp.post('/upload')` | `permission_required('all_collections')` |
| `rebuild` :87 | `reconciliation_bp.post('/rebuild')` | `permission_required('all_collections')` |

**Direct templates:** `collections/reconciliation.html`, `collections/reconciliation_members.html`.

**Application imports:** `auth`, `database`.

### 16.64 `onecpase/reports.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `index` :427 | `reports_bp.route('/')`<br>`reports_bp.route('/overview')` | `permission_required('reports_module')` |
| `run_custom` :474 | `reports_bp.route('/run-custom', methods=['POST'])` | `permission_required('reports_module')` |

**Direct SQL table names:** `collections`, `contact_logs`, `members`, `ptp_agreements`, `queries`, `report_definitions`, `users`.

**Direct templates:** `reports/index.html`.

**Application imports:** `auth`, `database`, `encryption`, `member_counts`, `ptp`.

### 16.65 `onecpase/sales_kpi.py`

**Source responsibility:** Append-only sales KPI recording helpers.

**Service/class entry points:** `ensure_sales_kpi_schema`, `record_stage_event`, `record_lead_status_change`, `record_first_contact`, `record_application_status_change`, `snapshot_checklist`, `record_checklist_changes`, `build_sales_report`.

**Direct SQL table names:** `approvals`, `debicheck_mandates`, `lead_activities`, `leads`, `mandate_followups`, `members`, `membership_applications`, `stage_events`, `users`.

**Application imports:** `applications`, `db_adapter`, `sales_pipeline`.

### 16.66 `onecpase/sales_pipeline.py`

**Source responsibility:** Controlled sales-stage progression layered onto the existing CRM.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `transition_stage` :439 | `sales_pipeline_bp.post('/leads/<int:lead_id>/stage')` | `permission_required('capture_leads')` |
| `save_qualification` :459 | `sales_pipeline_bp.post('/leads/<int:lead_id>/qualification')` | `permission_required('capture_leads')` |
| `invite_to_application` :497 | `sales_pipeline_bp.post('/leads/<int:lead_id>/application-invitation')` | `permission_required('capture_leads')` |
| `book_sales_appointment` :526 | `sales_pipeline_bp.post('/leads/<int:lead_id>/appointment')` | `permission_required('capture_leads')` |

**Service/class entry points:** `membership_progress`, `sales_milestones`, `ensure_sales_pipeline_schema`, `current_sales_stage`, `transition_sales_stage`, `sync_legacy_stage`, `pipeline_context`.

**Direct SQL table names:** `SET`, `application_invitations`, `lead_qualifications`, `leads`, `membership_applications`, `sales_appointments`, `sales_stage_history`, `stage_events`.

**Application imports:** `auth`, `database`, `db_adapter`.

### 16.67 `onecpase/scheduler.py`

**Source responsibility:** Background job runner — replaces the manual "Run Automations" button and the hand-run Itensity import scripts with jobs that fire on a clock.

**Service/class entry points:** `itensity_scripts_available`, `init_scheduler`.

**Direct SQL table names:** `SET`, `itensity_live_snapshot`, `lead_activities`, `leads`, `sales_stage_history`, `tenants`.

**Application imports:** `collections`, `leads`, `notifications`, `phases`, `ptp`, `sales_pipeline`.

### 16.68 `onecpase/schema.py`

**Source responsibility:** Database schema for 1Cpase.

**Service/class entry points:** `get_schema`.

**Direct SQL table names:** `announcement_reads`, `announcements`, `approvals`, `assessments`, `attendance`, `audit_logs`, `body_matrix`, `call_list_upload_rows`, `call_list_uploads`, `class_bookings`, `collection_call_tasks`, `collections`, `compliance_checklists`, `contact_logs`, `contract_share_tokens`, `contract_signatures`, `contract_templates`, `debicheck_mandates`, `equipment`, `fb_comments`, `fb_conversations`, `fb_messages`, `fitness_classes`, `incidents`, `instructor_dailies`, `itensity_live_snapshot`, `lead_activities`, `leads`, `leave_requests`, `legal_referrals`, `login_otp_challenges`, `maintenance_log`, `member_documents`, `member_notes`, `member_onboarding`, `member_supplements`, `members`, `membership_applications`, `nupay_mandate_checks`, `nutrition_foods`, `onboarding_sync_state`, `onboarding_visits`, `payment_plan_items`, `payroll`, `platform_admins`, `platform_audit_log`, `ptp_agreements`, `ptp_mandate_links`, `ptp_receipts`, `queries`, `query_notes`, `report_definitions`, `screen_recordings`, `staff`, `staff_messages`, `staff_threads`, `statement_analyses`, `supplements`, `tariffs`, `tenant_integrations`, `tenants`, `the`, `timesheets`, `turnstile_events`, `user_permissions`, `users`, `wa_conversations`, `wa_messages`, `webhook_events`, `workout_plans`.

### 16.69 `onecpase/screen_recordings.py`

**Source responsibility:** Admin-only browser screen recording storage and review.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `index` :47 | `screen_recordings_bp.get('/')` | `login_required`, `_admin_required` |
| `upload` :61 | `screen_recordings_bp.post('/upload')` | `login_required`, `_admin_required` |
| `download` :116 | `screen_recordings_bp.get('/<int:recording_id>/download')` | `login_required`, `_admin_required` |

**Direct SQL table names:** `screen_recordings`, `users`.

**Direct templates:** `screen_recordings/index.html`.

**Application imports:** `auth`, `database`.

### 16.70 `onecpase/staff_attendance.py`

**Source responsibility:** Read-only staff attendance evidence from an access-card snapshot.

**Service/class entry points:** `shift_window`, `build_week`.

### 16.71 `onecpase/statement_parser.py`

**Source responsibility:** South African bank statement PDF analyser — Standard Bank aware.

**Service/class entry points:** `analyse_cashflow`, `analyse_pdf`.

### 16.72 `onecpase/statement_uploads.py`

**Source responsibility:** Common bank statement upload and cashflow rules across the app.

**Service/class entry points:** `upload_statements`.

**Direct SQL table names:** `compliance_checklists`, `member_documents`, `members`, `statement_analyses`.

**Application imports:** `applications`, `members`, `statement_parser`.

### 16.73 `onecpase/tenants.py`

**Source responsibility:** Public tenant picker: the welcome page and the cookie that remembers which gym a browser last chose. No login required for any route here.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `welcome` :14 | `tenants_bp.route('/')` | Check handler/blueprint/app guards |
| `select_tenant` :25 | `tenants_bp.post('/select-tenant')` | Check handler/blueprint/app guards |
| `switch_tenant` :43 | `tenants_bp.get('/switch-tenant')` | Check handler/blueprint/app guards |

**Direct SQL table names:** `tenants`.

**Direct templates:** `welcome.html`.

**Application imports:** `platform_db`.

### 16.74 `onecpase/training.py`

**Source responsibility:** Staff training resources; never served from the public static directory.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `index` :21 | `training_bp.get('')` | `login_required` |
| `topic` :27 | `training_bp.get('/topics/<slug>')` | `login_required` |
| `manual` :36 | `training_bp.get('/manual/<format>')` | `login_required` |

**Service/class entry points:** `chapters`.

**Direct templates:** `training/index.html`, `training/topic.html`.

**Application imports:** `auth`.

### 16.75 `onecpase/turnstile.py`

**Source responsibility:** USB HID turnstile detection and read-only event monitoring.

**Service/class entry points:** `get_turnstile_status`, `enumerate_turnstile`, `decode_credential`, `evaluate_access`, `start_turnstile_monitor`.

**Direct SQL table names:** `members`, `tenants`, `turnstile_events`.

**Application imports:** `collections_engine`.

### 16.76 `onecpase/turnstile_routes.py`

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `index` :13 | `turnstile_bp.route('/')` | `permission_required('all_members')` |
| `scan_credential` :35 | `turnstile_bp.post('/scan')` | `permission_required('all_members')` |
| `assign_credential` :70 | `turnstile_bp.post('/events/<int:event_id>/assign')` | `permission_required('add_member')` |

**Direct SQL table names:** `members`, `turnstile_events`.

**Direct templates:** `turnstile/index.html`.

**Application imports:** `auth`, `database`, `turnstile`.

### 16.77 `onecpase/whatsapp_contacts.py`

**Source responsibility:** Exact, tenant-local client phone matching for WhatsApp conversations.

**Service/class entry points:** `phone_key`, `identify_conversations`.

**Direct SQL table names:** `members`, `wa_conversations`.

**Application imports:** `encryption`, `whatsapp_web`.

### 16.78 `onecpase/whatsapp_receive.py`

**Source responsibility:** Receive recent direct-chat text from the linked browser's visible UI.

**Service/class entry points:** `save_incoming`, `poll_incoming`, `record_outgoing_result`.

**Direct SQL table names:** `app`, `collection_call_tasks`, `collection_communications`, `collection_reminders`, `fitness_intake`, `tenants`, `wa_conversations`, `wa_messages`.

**Application imports:** `database`, `fitness_journey`, `phases`, `platform_db`, `whatsapp_web`.

### 16.79 `onecpase/whatsapp_selenium.py`

**Source responsibility:** Selenium browser adapter for the existing single-thread WhatsApp worker.

**Service/class entry points:** `class Locator`, `class Keyboard`, `class SeleniumPage`.

### 16.80 `onecpase/whatsapp_web.py`

**Source responsibility:** Send WhatsApp messages by driving a real WhatsApp Web session, for use when the Meta Cloud API (comms.py's send_whatsapp_message) isn't set up.

| Handler and line | Route declaration | Additional decorators |
| --- | --- | --- |
| `status_page` :454 | `whatsapp_web_bp.get('/')` | `_admin_required`, `limiter.limit(POLLING_LIMIT)` |
| `status_json` :461 | `whatsapp_web_bp.get('/status.json')` | `_admin_required`, `limiter.limit(POLLING_LIMIT)` |
| `link_by_phone` :468 | `whatsapp_web_bp.post('/link-by-phone')` | `_admin_required`, `limiter.limit('6 per minute')` |
| `show_qr` :481 | `whatsapp_web_bp.post('/show-qr')` | `_admin_required` |
| `qr_image` :489 | `whatsapp_web_bp.get('/qr.png')` | `_admin_required`, `limiter.limit(POLLING_LIMIT)` |

**Service/class entry points:** `restrict_linked_tenant`, `class LinkBlocked`, `get_status`, `queue_message`, `normalise_phone`, `link_blocked_message`, `request_phone_link`, `request_qr_mode`, `init_whatsapp_web`.

**Direct SQL table names:** `queued`.

**Direct templates:** `admin/whatsapp_web.html`.

**Application imports:** `admin`, `extensions`, `fitness_journey`, `whatsapp_receive`, `whatsapp_selenium`.

## 17. Operational script inventory

These scripts are not automatically required by web requests. Caller, deployment and recovery checks must determine whether each is maintained tooling or completed one-off work. Do not execute import/deploy/reset/cleanup scripts just to inspect them.

| Script | Source-stated purpose or entry points | Proposed treatment |
| --- | --- | --- |
| `scripts/audit_ui.py` | Measure template styling debt and optionally lower its regression baseline. | Retain as repeatable verification/build tooling; check generated-output retention |
| `scripts/build_training_content.py` | Compile the reviewed training text into escaped, structured reader content. | Retain as repeatable verification/build tooling; check generated-output retention |
| `scripts/cleanup_hr_rehearsals.py` | Remove only disposable DB copies created by these HR verification runs. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/configure_hr_reporting.py` | Read/dry-run/apply the specifically authorised HR reporting and date setup. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/deploy_hr_annual_policy.py` | Deliver the tested company annual-leave policy update only. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/deploy_hr_workforce.py` | Deploy exactly the inspected HR file set, after a passing scoped release gate. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/hr_annual_policy_remote.py` | Scoped annual entitlement deployment and explicitly confirmed date updates. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/hr_release_remote.py` | Remote scoped release: rehearsal, verified backup, additive HR upgrade and rollback. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/import_itensity_members.py` | Import Itensity members into 1Cpase with deterministic statuses. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/import_itensity_transactions.py` | Import Itensity transactions into 1Cpase member collection history. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/import_webapp_ledger.py` | Import the real transaction ledger from WEBAPPL\1Cpace into V8's collections table. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/itensity_banking.py` | Read-only banking enrichment from individual Itensity member pages. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/itensity_portal.py` | Shared session helpers for Itensity's classic portal. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/itensity_reconcile.py` | Prove a pulled roster is complete before anything is imported. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/prepare_hr_release.py` | Read-only live source inspection for the scoped HR release. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/preview_staff_access.py` | Read gym access scans and match them to the live staff directory. | Retain as repeatable verification/build tooling; check generated-output retention |
| `scripts/probe_itensity_endpoints.py` | Find which Itensity endpoint returns the *full* member roster. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/pull_itensity_export.py` | Log into Itensity and download the live member Data Export. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/pull_itensity_roster.py` | Pull Itensity's full member roster — all of it, plus the counts to prove it. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/push_tenant_db_to_beta.py` | Replace the beta tenant database with this machine's copy. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/reset_staff_passwords.py` | Reset every staff account to the default password. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/shorten_usernames.py` | Shorten sign-in usernames from firstname.lastname to firstname. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/sync_elev8_members.py` | Synchronise Elev8 dbo.Members into the active 1Cpace tenant. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/sync_staff_accounts.py` | Correct staff departments and create the staff accounts that were missing. | Check scheduled/deployment/manual callers and recovery value before consolidation or archival |
| `scripts/verify_hr_annual_policy.py` | Read-back policy/dates and verify authenticated screens on a protected DB copy. | Retain as repeatable verification/build tooling; check generated-output retention |
| `scripts/verify_hr_workforce_ui.py` | Render HR using disposable data; never open local or live gym databases. | Retain as repeatable verification/build tooling; check generated-output retention |
| `scripts/verify_migrations.py` | Rehearse startup migrations on disposable copies of local SQLite databases. | Retain as repeatable verification/build tooling; check generated-output retention |
| `scripts/verify_training_ui.py` | Exercise the training reader against disposable data at two screen sizes. | Retain as repeatable verification/build tooling; check generated-output retention |
| `scripts/verify_ui.py` | Render the UI against disposable data, never the local gym databases. | Retain as repeatable verification/build tooling; check generated-output retention |
