# Controlled Sales Pipeline

1Cpase keeps the existing `leads` CRM and adds a canonical `sales_stage` field.
The controlled stages are:

`CAPTURED → ASSIGNED → CONTACT_ATTEMPTED → CONTACTED → QUALIFIED → INVITED → APPOINTMENT_BOOKED → APPOINTMENT_CONFIRMED → SHOW → CONSULTATION → APPLICATION_INVITED → APPLICATION_STARTED → APPLICATION_SUBMITTED → JOINED`

`NO_SHOW`, `RECYCLED`, and `LOST` are controlled branches. Each accepted move is recorded in `sales_stage_history` with the old stage, new stage, authenticated user, timestamp, reason, and notes.

## Protected actions

- `POST /sales/leads/<lead_id>/stage` moves one valid stage at a time.
- `POST /sales/leads/<lead_id>/qualification` stores qualification evidence and moves to `QUALIFIED` when valid.
- `POST /sales/leads/<lead_id>/appointment` stores a booked appointment and moves to `APPOINTMENT_BOOKED`.

Qualification and appointment stages enforce their prerequisites. Existing CRM writes are bridged into the canonical stage only when the corresponding controlled move is valid; legacy shortcuts remain visible in the original CRM but cannot silently advance the controlled funnel.


## 2026-09-01 Pipeline Revision

The controlled pipeline has been tightened around the existing CRM/application workflow:

- Added a dedicated `DECISION_FOLLOW_UP` stage so the legacy five-working-day decision follow-up is no longer represented as `CONSULTATION`.
- Added `decision_follow_up_at` to the lead timestamps.
- Added an explicit direct-walk-in rule: `SHOW` may be entered directly from `CAPTURED` only when `entry_path=direct_show`; normal leads still require an appointment path.
- Added a controlled `/sales/leads/<lead_id>/application-invitation` action that records the invitation before moving the canonical stage to `APPLICATION_INVITED`.
- Added an application-to-sales bridge so application progress can advance `APPLICATION_INVITED → APPLICATION_STARTED → APPLICATION_SUBMITTED → JOINED` when the application status proves the corresponding milestone.
- Replaced the SQLite-specific `updated_at=datetime('now')` transition write with an application-supplied timestamp, improving backend portability.
- Added regression tests for direct walk-ins, invalid appointment bypasses, and the distinct decision follow-up stage.

The canonical sales stage remains the authoritative prospect progression; legacy `lead_status` is retained as a compatibility field and bridge.


## 2026-09-01 pipeline hardening

The implementation now treats the controlled `sales_stage` as the canonical
prospect path while retaining the legacy `lead_status` field for compatibility.

Additional safeguards:
- `DECISION_FOLLOW_UP` is a dedicated stage between consultation and application invitation.
- Legacy `hot_prospect_7day` maps to `DECISION_FOLLOW_UP`.
- `SHOW` may be reached directly from `CAPTURED` only for an explicit walk-in/direct-show lead.
- Normal prospects must have a booked/confirmed appointment before `SHOW`.
- Every canonical transition is recorded in both `sales_stage_history` and the cross-module `stage_events` stream.
- Application status changes advance the sales pipeline only when the corresponding controlled predecessor stage exists.
- The final background Itensity activation can advance `APPLICATION_SUBMITTED -> JOINED` without relying on a Flask request/session.
- Application creation helpers can preserve the `lead_id` relationship when an application is created from a member workflow.


## Application / DebiCheck gate (2026-09 update)

The application compliance gate treats a successful NuPay submission as **submitted**, not approved.
The DebiCheck checklist is satisfied only after a live status check returns a recognised approval/
authorisation outcome. Recognised approval outcomes are normalised to `approved`; rejected/declined/
failed outcomes are normalised to `failed`. Unknown outcomes do not satisfy the gate.

This keeps the canonical sales path aligned with the application workflow:
`APPLICATION_INVITED → APPLICATION_STARTED → APPLICATION_SUBMITTED → JOINED`.


## v5 Itensity handoff hardening

The Itensity handoff now atomically claims a push worker, requires a valid Itensity member reference before activation, and prevents duplicate pushes. The local application activation and canonical `JOINED` sales transition are committed together; a sales-stage reconciliation failure rolls the local activation back.


## Membership milestones

The Sales CRM now distinguishes **Agreed to Join**, **Application Started**, and
**Activated**. Agreeing to join records the client's decision; starting an
application creates the application/member record; activation requires an
application with status `active`. Pending documents, verification, ready-to-push,
and legacy `pushed` applications are not displayed as activated. Declined
applications are labelled separately.

Prospect details show all three milestones, and the Membership Progress section
of the CRM board displays each prospect's current milestone. Sales reports label
the existing agreement counts as Agreed to Join, before applications and activation.
Stored legacy `joined` and canonical `JOINED` keys are retained for compatibility:
legacy `joined` means agreement, while canonical `JOINED` is displayed as Activated.
A controlled transition to `JOINED` requires an active linked application, including
manual manager transitions. This change does not alter payment/document activation
requirements or the existing report counting rules.


## Evidence-based sales funnel counts

Both the CRM funnel and Sales dashboard use shared milestone rules. Successful
remote contact requires an appointment-booked, follow-up-required, or not-interested
outcome (or an explicit legacy Contacted stage). No answer, voicemail, dropped calls,
invalid numbers, unreachable numbers, and no response do not count as conversations.
Call attempts remain recorded and contribute to Calls Made.

Attendance does not imply an appointment or consultation: walk-ins only count toward
those milestones when the corresponding activity is recorded. Appointment activity
or a current Appointment Booked stage proves the appointment milestone. Consultation
activity or a current Decision Follow-Up stage proves consultation. The reports count
prospects per milestone, not the number of retries.

Activation counts require application status `active`; legacy `pushed` does not prove
activation and can now appear as a stalled application. Declined applications are
excluded from the stalled list. Existing lead-cohort, application-date and ownership
filters are retained. Reports may flag variances because walk-ins can reach attendance
without passing through a remote-contact or appointment stage.


## ID-based NuPay submission and compliance sales credit

New mandates use the member ID as NuPay Client Reference 1; a third-party payer's
ID remains in the payer identity field. New references are encrypted at rest.
Historical submitted references remain unchanged for reconciliation. NuPay
submission requires uploaded ID and bank-statement documents and the latest
statement analysis to qualify under the existing two-month income or 30% balance
rule. Failed qualification directs the consultant to inform the client or escalate.

Draft saving precedes document upload and analysis. The final Submit & Push to
NuPay action checks evidence on the server, including automatic and manual-review
submission paths. A successful submission is pending authorisation. Only confirmed
approval of the latest mandate unlocks Itensity activation. A member ID reference
never falls back to a shared payer bank account; multiple contracts for the same
ID require a specific contract reference. Confirmed submission responses preserve
that contract reference when NuPay supplies it.

Only an explicit admin override bypasses submission requirements; each exception
is audited. Managers can review escalations but cannot bypass submission gates.
An admin override permits the external submission, but does not manufacture
DebiCheck approval, document evidence, fee payment, or consultant sales credit.

Itensity activation and compliance completion are separate. A confirmed signed
contract (uploaded, captured member signature, or verified remote approval) and a fully paid registration fee are required after activation before
compliance becomes complete. Registration-fee payments are explicitly classified;
general payments, failed or partial payments, and unpaid checklist claims cannot
unlock credit. Where a joining fee is recorded, full payment must cover that fee.

Monthly credited sales use the first compliance-completion event and its consultant
owner, independent of the month the lead or application was created. Each
application earns credit once; incomplete compliance earns none. Operational
activation figures remain separately labelled. Later payment/document changes do
not undo an actual Itensity activation, but they remove eligibility while compliance
is incomplete. No existing sales are retroactively given invented completion dates.
