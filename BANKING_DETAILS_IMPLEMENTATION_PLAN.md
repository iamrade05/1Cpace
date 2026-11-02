# Banking details and first-time DebiCheck workflow

## Shared retention requirement

On return, automatically retain the signed form, signature and every query attachment on the member profile, linked to the original query. No manual copying by reception. Preserve original versions and protected access. See `MEMBER_QUERY_SHARED_REQUIREMENTS.md` for the cross-query requirements.

Planning record, 24 October 2026. Application code has not been changed.

## Entry and member location

Both first-time banking details and changes to existing details start with reception logging a member-linked query. Keep that query as the workflow record throughout; do not create duplicate cases when opening DebiCheck.

- **At gym:** reception captures the banking details in the query. Submitting opens the existing DebiCheck workflow with the captured data for reception to review and push.
- **Remote:** reception sends a secure form. The member completes banking details and personally signs the request. Submission returns to the assigned consultant. The consultant checks it, then submits into the DebiCheck workflow for review and push.

The signed request is separate from the member's DebiCheck mandate approval. Whether an at-gym request also requires a signature on reception's device remains to be confirmed.

## Required bank statement for changes

A banking/beneficiary-details change must include a bank statement **covering at least two months**. Two months or more is accepted; a statement covering less than two months does not satisfy this requirement. This is a coverage-period requirement, not a rule that a statement must be at least two months old.

Apply the requirement to both at-gym and remote changes. Reception/consultant checks and records the statement coverage and that it supports the submitted details before pushing the change to DebiCheck. Keep the document and review result attached to the query with protected access.

The user specified this condition for changes. Whether the same two-month statement rule applies to first-time mandate applications has not yet been explicitly established; keep this distinction visible rather than inventing an additional first-time eligibility condition.

## DebiCheck and manager handoff

1. Record capture/submission and assigned staff review.
2. Staff opens DebiCheck and pushes the new mandate.
3. Show Awaiting member DebiCheck approval; distinguish a push from an approved mandate.
4. On confirmed member approval from DebiCheck, automatically route the query to the manager to cancel the old debit order, where one exists.
5. Record actual old-order cancellation confirmation, actor and timestamp. Do not mark a failed/pending provider action successful.
6. Close the query with the new mandate approval and applicable old-order cancellation history retained.

Do not cancel the old debit order merely because a form was signed or a new mandate was pushed. For a first-time mandate, mark the old-order cancellation step not applicable when there is no old order. Prevent duplicate pushes or cancellation actions on retries; preserve provider references and outcomes.

## Shared form behaviour

Use the agreed short form style: saved name, contact number and email for confirmation, with a correction line if incorrect. Keep internal member/query references server-side. Include the shared no-guaranteed-approval and 5–10-day processing notice, signature capture and secure document upload. Encrypt sensitive banking data, restrict evidence access and retain an audit trail.

## Checks before completion

- At-gym and remote submissions remain linked to their original query.
- Remote submissions include member signature and return to the assigned consultant.
- Changes with less than two months of statement coverage cannot pass document verification; two months or more can.
- New mandate approval is confirmed independently of signed-form submission.
- Manager cancellation handoff occurs only after confirmed approval of the replacement mandate.
- No old-order cancellation is required when none exists.
- Provider failures, expired links, unauthorised access and repeated actions are handled without false success or duplicate processing.

## Open details

- At-gym request signature requirement.
- Application of the statement coverage rule to first-time mandates.
- Exact account/provider mapping for old-order cancellation and named authorised staff.
- Shared processing-clock interpretation (calendar/working days and starting event).
