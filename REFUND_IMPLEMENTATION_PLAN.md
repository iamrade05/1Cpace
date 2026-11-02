# Refund request implementation plan

## Shared retention requirement

On return, automatically retain the signed form, signature and every query attachment on the member profile, linked to the original query. No manual copying by reception. Preserve original versions and protected access. See `MEMBER_QUERY_SHARED_REQUIREMENTS.md` for the cross-query requirements.

Prepared 24 October 2026. Status: agreed workflow documented; application functionality has not yet been implemented.

## 1. Objective and ownership

Manage the refund through one member-linked query with visible stages, named owners and an audit trail. A submitted or approved request must not be presented as a paid refund.

The request starts with reception. After reception verifies it, it moves to the dedicated manager for the user's approval. Once approved, it moves to **Etienne Emmanuel** for payment processing. Etienne records **Payment made** only after actually making the payment.

Resolve the dedicated approving manager and Etienne to active tenant staff accounts during setup; do not guess account identities from partial names. Configure routing using staff IDs so names can change without breaking ownership.

## 2. Shared requirements for all member application forms

Show the client's saved name, contact number and email address for confirmation. The member selects Yes/No to confirm whether these details are correct. If No, reveal one correction line below for the correct details. Retain corrections with the submission for reception to verify; do not silently overwrite the member profile.

Do not display an ELE/EDL-style member reference. Use the client's name alone in the form example, as authorised by the user. An alternative identity reference was mentioned but its precise source field has not been established; do not substitute a national ID number. Keep the existing internal member/query links server-side.

Show the following notice on every application form, including cancellation, refund and future forms:

> Submitting this form does not mean that your application has been approved. Applications take 5 to 10 days to be fully processed. You will be notified of the outcome.

Require the member to acknowledge the notice, and retain its exact text, version and acknowledgment timestamp with the submission. The stated timeframe is not a promise that a refund will be approved or paid. Show the same notice in the submission acknowledgment.

The user specified days without defining calendar versus working days or when the processing clock starts. Do not silently change the notice to working days or add a reset rule. Resolve those timing details before automated deadline calculations go live.

Amounts and terms the gym asks the member to acknowledge, such as a cancellation quote, remain clearly labelled system-generated information rather than member-entered personal fields. The refund member form does not ask for an amount. Reception determines the verified refundable amount from records and the bank statement; record management's approved amount separately.

## 3. Workflow stages

| Stage shown to staff | Owner | Required action / next transition |
| --- | --- | --- |
| Reception — eligibility check | Reception | Record why the client requests a refund and compare the explanation with system records before issuing a form. |
| Awaiting member form | Reception | Issue secure form only after preliminary eligibility is confirmed; track link issuance and submission separately. |
| Reception — verification | Reception | Review returned form and supporting evidence, verify the claimed transactions/cancellation and record confirmation. |
| Awaiting member information | Reception | Request missing/corrected information; return to verification when supplied. |
| Pending manager approval | Dedicated approving manager | Review reception's confirmed findings and approve or reject with a recorded decision. |
| Approved — awaiting payment | Etienne Emmanuel | Receive the approved amount and processing details; make the payment. |
| Payment made | Etienne Emmanuel | Confirm actual payment in the app and record amount, date, transaction reference and payment evidence. |
| Closed | Completed record | Retain the full history and notify the member of the recorded outcome. |
| Ineligible / rejected | Reception for preliminary ineligibility; manager for rejection | Record the reason and outcome without presenting it as an approved refund. |

These are refund workflow stages mapped to existing query lifecycle states. Do not add duplicate generic query statuses unnecessarily. Show the refund stage alongside the existing query status, current owner and time in stage.

## 4. Reception's preliminary eligibility check

Before sending the client a form, reception records:

1. Why the client is requesting a refund, including the client's explanation.
2. Which payment or debit is disputed, including the date/amount where available.
3. What the system shows and whether it supports proceeding with the request.
4. Preliminary eligibility: confirmed, not eligible or needs investigation, with a reason and evidence references.

Only confirmed preliminary eligibility enables form issuance. A case needing investigation stays with reception. Preliminary eligibility is not manager approval and does not guarantee payment.

The eligibility check must use the applicable gym policy and actual records. This plan does not invent additional refund entitlements, exclusions or supporting-document rules.

## 5. Member form

The member receives a secure, tenant- and query-bound link. Show saved name, contact number and email address for Yes/No confirmation, with a correction line if No. Use the name alone rather than an ELE/EDL reference. Retain the reason selection, then ask how many times/months the incorrect debit occurred, using selectable Once / one month or Twice / two months. Do not include a free-text explanation, requested amount, debit dates, second debit date or cancellation-request date. Reception obtains dates, amounts and cancellation details internally from system records and the bank statement.

The member uploads a bank statement, then signs and acknowledges the shared notice. Do not include a banking-details section or ask for an alternative refund account. The only required supporting document is a bank statement indicating the payment or debit concerned. Remove separate payment-proof, cancellation-confirmation and banking-confirmation upload requirements from the member form; reception verifies cancellation and collection records internally.

Display in Important information: **Any approved refund will be paid back to the same bank account from which the original payment or debit was made.** The processor verifies the originating account from authorised source records and supporting evidence. Do not pay a different account supplied through the form.

Use a responsive form with finger/stylus signing, clear/redraw controls and a submission acknowledgment. Preserve the form version, exact declaration, entered information, signature, timestamp and identity-verification method. A signature alone is not proof of identity or bank-account ownership.

Validate fields and uploads, limit sizes/types, encrypt sensitive information, restrict downloads to authorised staff, and protect links with expiry and revocation. Avoid asking for sensitive information without an operational need. The bank statement showing the payment is the sole member supporting-document requirement; do not add ID, cancellation letters or banking-confirmation documents.

## 6. Reception's verification after submission

Reception verifies the returned form against actual account and payment records, then explicitly records **Confirmed**, with staff identity, timestamp and evidence.

Checks depend on the claimed reason:

- **Debit claimed:** did the gym actually debit the client? Distinguish a debit instruction or pending/failed attempt from a successfully collected payment.
- **Double debit claimed:** are there two actual collected transactions for the alleged duplicate, rather than two records of the same event? Identify both transaction references, amounts and dates.
- **Debit after cancellation claimed:** was the membership/premium actually cancelled, what was the effective date, and did an actual collection occur afterward? Review the membership cancellation and related recurring collection instruction separately.
- **Other refund reason:** record the specific factual check and the evidence that supports or contradicts the claim.
- **Prior repayment/reversal:** check whether the disputed funds have already been refunded or reversed so the same payment is not refunded twice.

Record the member's selected reason and occurrence count, reception's verified refundable amount and the manager's approved amount separately. Do not require the member to supply amounts or dates. Attach evidence or link source records. Missing information returns the case to the member correction stage while reception retains ownership. Original submissions and documents remain in the history.

Do not route to the manager while required factual checks remain unconfirmed. A simple unchecked checkbox must not be treated as verification.

## 7. Dedicated manager approval

After reception confirms verification, route the application to the configured dedicated manager for the user's approval. Show the selected reason and occurrence count, reception's intake notes, signed form, transaction evidence, findings and verified amount together.

The manager records approve/reject, amount and decision notes. Rejection requires a reason. Approval records the approver and timestamp, then routes the request to Etienne Emmanuel. Approval must not initiate or claim a bank payment.

Material changes to the request, verified evidence or approved amount after approval require a new review/approval version; do not silently edit an approved instruction. Keep every previous decision.

## 8. Etienne Emmanuel's processing and Payment made action

Etienne receives the approved request in a dedicated awaiting-payment queue. Show the authorised amount and confirmed payment details with supporting records. Restrict Payment made confirmation to the designated processor or an explicitly configured authorised replacement.

After actually making the payment, Etienne records:

- Actual amount paid.
- Payment date.
- Transaction/payment reference.
- Payment confirmation evidence.
- Optional processing notes.

Require the payment amount to match the approved amount for the initial workflow. A changed amount must return for approval rather than being silently paid under the old approval. Do not assume partial payments are supported until that process is defined.

The app changes the stage to **Payment made** and records the processor and timestamp. Make the operation idempotent: repeated clicks must not create duplicate payment records. Manual confirmation is recorded as confirmation by Etienne, not as independent verification from a bank provider.

Notify the member of the confirmed outcome and retain the payment evidence before closing the query. A notification failure remains visible and retryable without undoing the payment record or causing another payment.

## 9. Staff screens, communications and audit

Extend the existing query detail and queue views with stage badges, assigned owner, next action, age and timeline. Keep the member form, documents, verification checklist, decision and payment record inside the same query and member profile.

Audit form issuance, first access, submissions, corrections, factual confirmations, assignments, decisions, payment confirmation and closure. Preserve original evidence. Record actual provider results for outbound messages; created, sent, delivered and read are distinct events.

Use existing query follow-up dates and staff notifications to surface outstanding cases. Configure the processing clock only after its interpretation is confirmed. Internal owners and bank details must not leak through a public link.

## 10. Implementation sequence

1. **Data and routing:** add refund-stage records, verification findings, versioned submissions/decisions and payment confirmations using additive migrations. Configure reception, the dedicated manager and Etienne account mappings.
2. **Reception intake:** build reason capture and eligibility checks that gate form issuance.
3. **Member form:** implement saved-detail confirmation/corrections, reason and occurrence selectors, signing, bank-statement upload and the shared all-forms notice.
4. **Verification:** add reason-specific checks, evidence confirmation, correction links and manager handoff.
5. **Decision and processing:** add dedicated approval routing, Etienne's queue and Payment made confirmation.
6. **Visibility and notifications:** show every stage and owner in the existing member/query screens; retain delivery failures for follow-up.
7. **Verification and release preparation:** exercise the complete journey with disposable data and prepare release notes/migrations. Production payments and real outbound messages are outside development testing.

## 11. Acceptance checks

- Reception cannot issue the form before preliminary eligibility is confirmed.
- Saved name, contact number and email address are shown for Yes/No confirmation; No reveals one correction line. No ELE/EDL reference or national ID is displayed.
- The refund form asks for reason and Once / one month or Twice / two months, not narrative explanation, amounts, debit dates or cancellation dates. Those facts are verified internally.
- No banking-details section or alternative refund account is collected. The sole supporting document is a bank statement showing the payment, and the form states that any approved refund returns to the originating bank account.
- All application forms display and retain acknowledgment of the 5–10-day/no-automatic-approval notice.
- Submitted requests return to reception, not directly to a manager or payment processor.
- Actual collection evidence is required for debit-related claims; duplicate debit records are not mistaken for duplicate collected payments.
- Post-cancellation claims show actual cancellation/effective-date and collection evidence.
- Manager handoff requires reception confirmation and preserves its evidence.
- Approval routes to Etienne but does not mark a refund paid.
- Payment made requires authorised processing, amount/date/reference/evidence and cannot be recorded twice.
- Rejected/ineligible requests keep reasons and history without producing payment instructions.
- Permissions, tenant isolation, link expiry, file validation, encrypted storage and audit history are tested.
- Desktop/mobile screens clearly show current stage, owner and next action.

## 12. Setup details to resolve before live use

- Identify the dedicated approving manager's active staff account and Etienne Emmanuel's active staff account.
- Define whether 5–10 days means calendar or working days and which event starts that clock.
- Confirm applicable refund eligibility policy and the internal source-account verification process. Member evidence is already agreed: a bank statement showing the payment, with refunds returned to the originating account.

These do not prevent proceeding to plan the next request type.
