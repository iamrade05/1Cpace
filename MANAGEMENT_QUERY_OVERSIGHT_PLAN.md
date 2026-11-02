# Management query oversight plan

Agreed 24 October 2026. Planning only; application code has not been changed.

## Required visibility

Management must see the full lifecycle of each member application/query, including:

- Application started and when.
- Currently with the member, reception, management or another designated processor.
- Whether reception has checked the returned application.
- Whether every required check has been completed, and exactly what remains outstanding.
- Whether a management decision is pending or recorded.
- Whether the query remains open or is closed, including outcome and closure time.

Do not interpret the user's spoken 'ZQ' as a new technical status/code; the described requirement is end-to-end query visibility.

## Management list

Show member name, query reference/type, started date, current stage, current responsible person/team, waiting-on party, reception verification progress, outstanding items, due/follow-up date and open/closed state. Link directly to the existing query and its retained signed forms, evidence and timeline.

Provide filters for request type, stage, current owner/waiting-on party, verification completeness, due/overdue and open/closed. Use the same underlying query/workflow data as the reception queue so the two views cannot disagree about the current handoff.

## Stage versus checks versus lifecycle

Keep these separate:

1. **Current stage/holder:** awaiting member, reception verification, management decision, approved awaiting execution/payment or completed processing.
2. **Reception checks:** not started, in progress or all required checks confirmed; display individual outcomes and reviewer/time. A returned form or an uploaded file is not itself verification.
3. **Query lifecycle:** open or closed, using existing lifecycle fields and preserving detailed statuses where appropriate.

The current holder is derived from the recorded workflow handoff, not inferred from who last viewed the case. An assigned receptionist can remain the case owner while waiting for the member or manager; show owner and waiting-on party distinctly.

When management needs to act, the item must appear in the management action queue as well as the overview. Already approved requests remain visibly awaiting their next execution step rather than appearing closed. For example, an approved refund awaiting Etienne's payment confirmation remains open.

## Query detail and audit

Show a timeline of intake, form issuance, submission, reception checks/corrections, management decision, execution/payment confirmation and closure. Each event records actor/time and links to applicable evidence. Preserve earlier submissions and rejected/replaced evidence.

Display required-check completion from the relevant request template/rules, not from a blanket reception checkbox. Optional/not-applicable items must not falsely block completion. Any allowed not-applicable decision records who made it and why. Historical decisions retain the checklist/form version reviewed.

Reuse existing permissions and tenant boundaries. Management visibility does not remove document-specific access controls or expose sensitive evidence in list rows. Approval, financial processing and closure actions retain their authorised roles.

## Implementation and acceptance

1. Define a shared workflow summary for reception and management views, mapping current owner, waiting-on party, verification checks and existing lifecycle status.
2. Add management list filters, action queue and query-detail progress/checklist display.
3. Record handoffs and stage timestamps in the existing query history with additive workflow data where needed.
4. Test the member → reception → management → execution → closure journey for every supported request type.

Acceptance: management can determine where each application is, whether reception checked it, which checks remain and whether it is open/closed without guessing from notes. Returning a request or replacing evidence updates the visible state appropriately. Approved-but-unexecuted requests remain open and distinguishable. Both reception and management see consistent state while their permitted actions differ.
