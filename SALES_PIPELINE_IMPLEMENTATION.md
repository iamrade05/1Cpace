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
