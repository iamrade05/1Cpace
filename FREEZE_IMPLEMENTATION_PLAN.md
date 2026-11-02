# Account freeze implementation plan

## Shared retention requirement

On return, automatically retain the signed form, signature and every query attachment on the member profile, linked to the original query. No manual copying by reception. Preserve original versions and protected access. See `MEMBER_QUERY_SHARED_REQUIREMENTS.md` for the cross-query requirements.

Prepared 24 October 2026. Planning only; application code has not been changed.

## Agreed rules

- Reception starts a freeze query linked to the existing member profile.
- The member completes and personally signs a short form, remotely or at reception.
- Evidence must be issued by the relevant organisation, rather than written by the member.
- Exams: timetable showing the member's details.
- Employment: employer-issued letter addressed to the member.
- Study travel: letter issued to the member confirming the study/travel arrangement.
- No exception allowing a member-written letter has been agreed. Medical evidence and the full allowed reason list still require confirmation.

## Application timing

Apply at least **14 days before the member's payment/debit-order due date**. The cutoff is tied to that due date, not month-end or an arbitrary requested start date.

An application received on or before the cutoff may be considered for the upcoming cycle. A later application takes effect in the following month's payment cycle, subject to approval; it does not stop the imminent payment automatically.

Example: payment due 25 November 2026; cutoff 11 November 2026. Applying on 11 November meets the notice requirement; applying on 12 November misses it.

Read the actual payment schedule for subsequent cycles; do not assume every month has 30 days. Missing or conflicting due dates require reception confirmation. Store the source due date, receipt timestamps, cutoff and proposed effective cycle, using Africa/Johannesburg dates.

Before automated enforcement, define whether receipt means reception intake, signed submission or complete-evidence submission. Preserve all three timestamps. Do not silently reset receipt time after a correction.

## Short member form

Show saved name, contact number and email, with Yes/No confirmation and one correction line when No is selected. Use the member's name rather than an ELE/EDL reference. Keep internal member/query links server-side, and route corrections to staff review rather than silently changing the profile.

The member selects a reason, enters requested start/end dates, uploads the relevant evidence and signs. Show calculated due date/cutoff and proposed effective cycle separately from member-entered fields.

Include the shared notice:

> Submitting this form does not mean that your application has been approved. Applications take 5 to 10 days to be fully processed. You will be notified of the outcome.

Also show:

> Apply at least 14 days before your payment due date. Late applications take effect in the following month's payment cycle, subject to approval.

Retain acknowledgment, exact notices, form version, signature and submission time. The 5–10-day processing notice and the 14-day advance-application rule serve different purposes. Calendar versus working days and the start of the processing clock remain undefined for the processing notice.

## Stages and ownership

| Stage | Owner | Action |
| --- | --- | --- |
| Reception intake | Reception | Check reason, payment schedule and cutoff; issue form. |
| Awaiting member form | Reception | Track submission separately from link issuance. |
| Pending verification | Reception | Check signature, issuing organisation, member identity on evidence and supporting dates. |
| Awaiting information | Reception | Request corrections/replacements while retaining original evidence. |
| Pending approval | Configured manager | Decide and record approved dates or rejection reason. |
| Approved — awaiting application | Authorised implementing staff | Apply approved membership, billing and access changes. |
| Freeze applied / scheduled | Implementing staff | Record confirmed outcomes and notify the member. |
| Closed / rejected | Final record | Retain history and outcome. |

Use freeze-specific stages alongside existing query states. Reuse references, owners, notes, follow-ups and notifications. Freeze approver and implementing staff are not yet named; do not assume refund routing to Etienne applies.

Reception records individual evidence outcomes and confirmation timestamps. Rejected documents require reasons; secure correction links preserve earlier submissions. Manager handoff requires completed verification.

Approval does not itself pause billing or change access. Execution is a separate authorised step with confirmed provider results, visible failures and safe retries. Material changes to signed dates/terms require member acknowledgment. Keep a full audit trail and protect documents with validated uploads, encryption and permission-controlled access.

## Implementation order

1. Add stage, timing, evidence, decision and execution records through additive migrations.
2. Build reception intake and central due-date/cutoff calculations.
3. Build the compact secure mobile form, document upload and signature capture.
4. Add reception verification and correction requests.
5. Add manager decisions and separate authorised execution.
6. Add queues, owner visibility, follow-ups and member outcome notifications.
7. Test with disposable data and prepare migrations/release notes before live rollout.

## Acceptance checks

- Exactly 14 days before due meets the cutoff; 13 days before misses it.
- The November example, month/year boundaries and February calculate correctly.
- Missing payment dates require confirmation; late requests show the following payment cycle.
- Evidence is reason-specific and organisation-issued; exam timetables show member details.
- Signed requests return to reception and corrections preserve original evidence/timestamps.
- Approval alone changes neither membership billing nor access.
- Execution cannot duplicate changes, and provider failures are not marked successful.
- Mobile/desktop forms remain short; staff can see current stage, owner and next action.
- Permissions, tenant isolation, link expiry, file validation, signature retention and audit history are verified.

## Resolve before live execution

- Receipt event used for the notice cutoff.
- Final allowed reasons and any additional reason-specific evidence rules.
- Freeze duration limits, any fees, and treatment of a requested end date when the start shifts to the following month.
- Billing/access behaviour, contract extension and resumption rules.
- Named approving and implementing staff.
- Interpretation of the shared processing timeframe.

Do not invent extra eligibility conditions or operational rules. These open details do not prevent planning the next form.
