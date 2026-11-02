# Local fitness workflow implementation status

Updated 25 October 2026. The completed local workflow changes were deployed to the shared production/beta service on this date.

## Deployment verification

- Scoped 27-file release; 201 focused release tests passed.
- Rehearsed additive migrations on a server-side database copy and rendered all four new staff pages before applying the release.
- Code and both registry/tenant database rollback backups are retained at `/opt/onecpase-backups/workflow_20261025_074354`.
- Verified all 27 installed file hashes, database integrity and workflow tables. Both `https://1cpace.co.za/healthz` and `https://beta.1cpace.co.za/healthz` returned healthy.
- The scheduler is enabled. No fitness allocation job errors were logged after restart. Staff routes require authentication; an invalid public member-form token returns HTTP 410.
- Live setup is still empty: zero eligible roster entries, zero opening-day entries, and no query approver/refund processor mapping. The deployed features require that configuration before they can allocate or complete approvals.
- Evidence: `artifacts/workflow-release/deployment-result.json`, `artifacts/workflow-release/verification.json`, and `artifacts/workflow-release-tests.xml`.

## Confirmed policy

- New ten-calendar-day programmes start the day after joining. Already-started legacy programmes retain their dates.
- Group instructor offers expire after eight hours.
- General fitness, mixed and unclear goals require clarification from the member before allocation.
- Management evaluates decline reasons and decides which declines or timeouts count as skips.
- The fourth management-counted skip opens a PT-rights review. Management records whether to restrict or retain PT rights. Account and class access are separate.
- Freeze notice is measured from signed form submission.

## Implemented locally

- Separate programme start and first assisted session dates.
- Explicit primary-goal categories and an awaiting-goal-clarification state in intake. Unclear goals cannot receive an allocation offer; saving unclear goals clears the intake assignment.
- Persistent offer records with eight-hour deadlines and conditional acceptance/decline updates. Decline reasons are required; acceptance records the assigned instructor.
- Persistent management skip classifications and a review issue on the fourth counted skip. Classifications and review decisions cannot be overwritten through these actions.
- Management-only review page, accessible from fitness intake, records PT eligibility decisions independently of user activation.
- Structured member appointment windows, automatic goal-specific instructor selection and a persistent round-robin cursor. Staff configure roster eligibility and gym hours; they do not pick a member's instructor.
- Weekly early/late shift rotation anchored on 26 October 2026, plus fixed support/group shifts. Approved leave, disabled/inactive accounts, opening hours, existing reservations and instructor dailies exclude conflicting candidates.
- Group offer creation, instructor acceptance/decline screens, in-app notifications and automatic advancement after decline/timeout. A background job processes the tenant-local queue every minute when the scheduler is enabled.
- Member confirmation and appointment outcome capture. Time passing creates attendance-due, never successful attendance. Attended outcomes require an actual recorded assisted session. The original primary instructor survives covering appointments.
- Shared tenant-bound signed request forms, encrypted atomic evidence retention, workflow verification/approval and several case handoffs are implemented in `onecpase/query_forms.py`. They do not execute bank payments or submit mandates.

## Outstanding integration

- Management must configure verified staff/HR identities, allocation exclusions, working days and gym opening hours before allocation can select anyone. No production staff records were changed or accounts guessed.
- Legacy class records lack an end time. An instructor with a class on the requested day is conservatively held for schedule review. Detailed class-duration matching and date-specific shift exceptions remain outstanding.
- Availability currently comes from staff capturing the member-confirmed appointment window. The secure fitness form must still trigger these requests automatically. Appointment confirmation is recorded by staff; automatic member confirmation messaging remains outstanding.
- PT booking/request enforcement of the stored restriction decision, restoration controls and primary-instructor priority. The stored eligibility flag alone does not enforce external PT rights.
- Secure fitness member form and welcome link; automatic reminder three days after actual delivery, cancelled on completion.
- Programme expiry as review-due, final review and boarding completion; full next-action dashboards.
- Approved freeze billing/access execution and cancellation instalment mandate/payment lifecycle remain incomplete. Their policies and execution must not be inferred from approval alone.

The initial implementation and migration rehearsal sent no messages or payments. Deployment changed the authorized application files and applied additive database migrations; existing scheduled services continue under their existing configuration. Verification performed no provider actions.
