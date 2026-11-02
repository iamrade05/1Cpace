# Cancellation workflow implementation plan

## Shared retention requirement

On return, automatically retain the signed form, signature and every query attachment on the member profile, linked to the original query. No manual copying by reception. Preserve original versions and protected access. See `MEMBER_QUERY_SHARED_REQUIREMENTS.md` for the cross-query requirements.

Prepared 24 October 2026. Scope: Members → Queries → Cancellation, from reception intake through member submission, review, decision and recorded completion. This document is the implementation plan; application changes have not yet been made.

## Latest workflow clarification — takes precedence over earlier sequencing

Automate checks from existing member, payment, submission and mandate records wherever possible; staff must not manually reconfirm facts the system can establish. Distinguish automatically confirmed evidence from missing information and staff judgments. Keep manager approval as the gate before any membership or debit-order change.

### Consultant selects the offer before sending

Calculate both cash and six-payment options using the applicable discount band, and show them side by side to the consultant. The consultant explains both and selects the member's agreed option before sending the secure form. The 35% cash / 20% instalment pair applies above R3,000, not universally.

The selected option and its exact quote version are bound to the link. Send the breakdown: cancellation fee, unpaid amount, combined total, discount percentage/amount and final payable amount; for instalments include all six payment amounts and their agreed schedule. The member does not choose again on the form: their request entry is the cancellation reason, followed by explicit acceptance of the displayed terms and signature on their mobile phone. Do not require printing/scanning, a touch-enabled PC or a tablet. Use any already agreed supporting-document requirements without adding new member-entry questions.

Save the signed terms and selected quote version automatically on the profile/query. A changed quote requires renewed acceptance. A phone link is not itself proof of signature or acceptance. Reception and management see the returned form and automatically derived check results.

### Approval and payment handling

Do not stop, replace or remove any debit instructions, or execute membership cancellation, before manager approval.

- **Cash:** after manager approval and confirmed payment of the accepted cash settlement, remove/cancel the membership debit instruction with the provider and record the actual outcome. Proof uploaded or a pending payment is not confirmed payment. Retain historical records rather than deleting the audit trail.
- **Six-payment arrangement:** after approval, suppress the next **six normal membership debit orders** and load **six settlement debit orders** in their place (confirmed 24 October 2026; supersedes the earlier "seven"). The first settlement debit falls on the first payment date the member agreed with the consultant, then monthly. Preserve the underlying schedule/history and record each provider result. Identify how the relevant provider represents recurring instructions versus individual future collections before implementation.
- **Completion:** after the six-payment period, generate an in-app cancellation notification based on the actual payments required and received, showing complete versus incomplete and any missing/failed/partial amounts. Elapsed time alone must never mark the settlement complete. Do not automatically waive unpaid amounts or cancel the remaining settlement instruction on the basis of time alone.

The manager view shows signed-current-quote confirmation, selected option, reception verification completeness, and applicable DebiCheck/bank-statement/income checks. Show provider execution results separately from approval and payment completion. Exact membership/access status transitions during the settlement period still need definition; do not silently equate approval with settled/closed.

### Decisions confirmed 24 October 2026 (follow-up)

- **Debit orders:** suppress six normal membership debit orders and replace them with the six settlement debit orders.
- **First payment date:** the date the member agrees with the consultant. The six payments then fall monthly on the same day (the last day of a shorter month).
- **Member status during the settlement period:** *Cancellation in progress*. This is a new member status. Before it is introduced, map its effect on access, billing, reports, PTP member filters and the Elev8 status sync.
- **Cancelling straight away:** only for a cash settlement, and only once the payment is recorded (for example, the member pays at reception). Never for six instalments.
- **End-of-settlement reminders:** straight after the last settlement debit order (in that week), every cancellation-in-progress account must generate a reminder. The check is how many of the six payments were actually received (6 of 6, 4 of 6, 2 of 6 and so on), based on recorded collections, not elapsed time. The item is already on the manager's desk; the supervisor escalates it there if needed.
- **All six received:** the consultant/manager confirms that all payments were made and records *Payments completed*. This automatically routes to the manager to cancel the debit order mandate. Record the provider outcome.
- **Missing or failed payments:** the member is informed that the debit order failed, and the arrangement continues for an additional month to collect what is outstanding. Still to confirm: whether this is one additional month per failed payment or one in total, and what happens if no payments are received at all.

### Penalty schedule clarification

The six-month penalty in the worked example is illustrative, not a universal cancellation penalty. Calculate the fee using the member's actual monthly fee and applicable contract penalty tier. The user's statement that the penalty table exists does not establish a newly corrected set of numerical boundaries or resolve the end-of-contract case; preserve those as verification items rather than rewriting all contracts to six months.

This clarification supersedes earlier plan text suggesting that the member selects the payment option on the form or that the settlement debit instructions must simply remain untouched while an existing membership instruction is stopped. It also supersedes generic sequencing where it conflicts with confirmed cash payment and manager approval gates above.

## Shared form requirements agreed during refund planning

Updated during the member-form review: show saved name, contact number and email address for Yes/No confirmation, with a correction line if the member selects No. Use the client's name instead of an ELE/EDL reference; keep internal member/query links server-side. Do not assume the suggested alternative identity reference means a national ID number. Display system-generated cancellation quotes separately from member-entered fields.

Every form must display, and retain the member's acknowledgment of, this notice:

> Submitting this form does not mean that your application has been approved. Applications take 5 to 10 days to be fully processed. You will be notified of the outcome.

The user has not yet specified calendar versus working days or the event that starts the processing clock. Do not invent automated deadlines until those details are defined. See `REFUND_IMPLEMENTATION_PLAN.md` for the shared implementation requirements.

## 1. Agreed settlement rules

Calculate the cancellation fee from the member's applicable contract penalty and actual monthly instalment. Add the actual outstanding balance from account records once, without duplicating arrears already included in that balance.

**Discount base = cancellation fee + outstanding balance, before discount.**

| Discount base | Cash: full payment upfront | Instalments: six monthly payments |
| --- | ---: | ---: |
| Below R2,000 | 0% | 0% |
| R2,000 through R3,000 inclusive | 20% | 10% |
| Above R3,000 | 35% | 20% |

The boundary convention above follows the table discussed with the user: exactly R2,000 and R3,000 fall in the middle band.

For instalments, the only eligibility conditions agreed are:

- A DebiCheck mandate is in place.
- A bank statement is supplied.
- The member has an income.

Do not add affordability ratios, minimum income, extra documents, extra eligibility approvals or a requirement to clear arrears before requesting cancellation. Documentary verification and the cancellation decision are workflow steps, not extra instalment eligibility rules. A drawn signature alone does not create a DebiCheck mandate.

Use decimal currency calculations and round the discount to cents first, then subtract it from the gross total. Allocate instalments so the six payments sum exactly to the discounted total; disclose any final-payment rounding adjustment.

### Agreed worked example

36-month contract, R302.25 monthly instalment, 10 months completed, six-month cancellation penalty and four unpaid instalments:

| Item | Amount |
| --- | ---: |
| Cancellation fee: 6 × R302.25 | R1,813.50 |
| Arrears: 4 × R302.25 | R1,209.00 |
| Combined total | R3,022.50 |
| Cash discount: 35% | R1,057.88 |
| Cash payable | R1,964.62 |
| Instalment discount: 20% | R604.50 |
| Instalment payable | R2,418.00 |
| Six monthly payments | R403.00 each |

The cash payer pays R453.38 less than the instalment payer.

## 2. Existing application and changes needed

Reuse existing member links, query references, staff ownership, notes, follow-up dates, account snapshots and manager decision records.

The inspected code currently applies a 50% lump-sum discount or a flat R1,500 instalment settlement in certain cases. In other cases it discounts only the cancellation fee. Replace those behaviours for new cancellation quotes with the agreed combined-total bands above, including when the member has no arrears. Update both server calculations and browser previews; the server remains authoritative.

Current query creation and manager approval can immediately set a member to Cancelled. Remove that coupling for the cancellation workflow: intake, member submission and approval must not execute cancellation. Completion must be a separate authorised action.

Existing historical quotes, completed cancellations and financial arrangements retain their original amounts and history. A revised quote must be a new version; it must not silently rewrite an accepted quote.

Relevant implementation areas:

- `onecpase/queries.py`: calculation, capture, approval, closure and cancellation execution.
- `onecpase/templates/queries/add.html` and `detail.html`: reception quote and review controls.
- Database schema and additive migrations: quote versions, links, submissions, evidence and reviews.
- Existing contract sharing, communications, DebiCheck, PTP and notification modules: reuse suitable facilities without conflating a signed cancellation form with a debit mandate.
- Cancellation and query workflow tests: replace obsolete expected rules and cover the complete journey.

## 3. Delivery stages

### Stage 1 — Correct calculation and reception quote

Reception selects the member and opens a cancellation query from the member profile. Show contract length, start date, months completed, actual monthly fee, penalty months and current outstanding balance. Clearly identify any missing contract information rather than defaulting it to the maximum penalty.

Display cash and six-month options side by side: gross total, discount percentage, discount amount, net amount and payment schedule. Save a versioned quote with the calculation inputs, policy version, staff identity and timestamp. Any correction to inputs must be recorded.

Verify the existing penalty schedule against the gym's contract source before changing penalty tiers. The agreed settlement discounts can be implemented independently. The first-year six-month example is agreed; later contract penalty boundaries were not newly defined in this conversation.

**Acceptance:** the R3,022.50 example matches the amounts above; all discount boundaries work; cash and instalment previews match persisted server calculations; no membership status changes occur.

### Stage 2 — Member's cancellation form: completed and signed by the member

Reception selects the proposed payment option and generates a member-specific, expiring secure link. Support both a member completing it at the gym on a suitable device and completing it remotely on their phone. Reception assistance must be recorded as assistance; the member signs their own declaration.

Reception prepares and issues the request. The member reviews the cancellation fee, arrears, discount, total payable and selected payment terms, completes the member fields, accepts the declaration and personally signs the cancellation form. Provide clear and redraw controls for the finger/stylus signature. Reception cannot sign on the member's behalf. Store the signed form against the existing member profile and cancellation query, then return it to reception for verification. Signing acknowledges the request and quoted terms; it does not itself execute cancellation.

Show only the relevant member details, quote version, itemised amounts, requested cancellation date and declaration. Capture the reason for cancellation, payment choice, declaration acceptance and a finger/stylus signature. For instalments, collect the bank statement, record income confirmation and link/check the actual DebiCheck record. Allow submission with outstanding items, but flag the arrangement as pending verification until the three agreed conditions are met.

Preserve the exact declaration, quoted amounts, form version, timestamp, signature and verification method with each submission. Add identity verification using the member's registered contact before signing. If the quote changes, obtain acceptance of the revised quote.

**Acceptance:** mobile completion works; expired or superseded links cannot submit; access stays within the correct tenant and query; a submission never cancels membership or initiates a debit.

### Stage 3 — Reception review and corrections

Add a Client Submission panel to the existing cancellation query. Show link created/opened, submission received, signature, payment choice, outstanding items and verification results. An opened link is not proof that a member read or accepted a message.

For each relevant item, reception can record awaiting review, accepted, missing, rejected or not applicable. Require a reason for rejection. Keep original files and submissions when replacements arrive. Provide a secure correction link so the member can supply missing items without completing the entire original form again.

Use authenticated staff downloads, validated file formats and size limits, encryption for sensitive evidence, tenant isolation and download audit records. Do not place sensitive documents in public static storage.

**Acceptance:** reception can complete a correction loop; prior evidence remains available to authorised staff; eligibility uses only the three agreed conditions.

### Stage 4 — Decision and separate completion

Reuse the existing manager decision records for cancellation approval or rejection, retaining actor, timestamp and reason. Show an approved request separately from an executed cancellation. Do not introduce a separate discretionary discount approval for the standard published bands.

Provide a distinct completion action, showing the approved terms, effective date and outstanding execution steps. Track membership/access updates and external provider actions separately, with retries and failure visibility. Stop or amend the membership collection instruction as appropriate without accidentally stopping the six-payment settlement mandate.

Reuse or link the existing PTP machinery for the six-payment arrangement rather than creating a duplicate debt. Record payments against that arrangement. Do not reinterpret a six-month arrangement as a requirement for six months of payment before a member can even request cancellation; the effective-date/payment relationship must be explicitly defined.

**Acceptance:** approval alone never changes member status; completion cannot execute twice; provider failure cannot be reported as success; the instalment arrangement survives the membership cancellation as agreed.

### Stage 5 — Communication, follow-ups and queue visibility

Record link issuance, submission, correction requests, decision and completion against the same query. Reuse supported communication providers and store actual outcomes: generated, sent, failed, and delivery/read confirmations only when the provider supplies them. Never report a copied link as a delivered message.

Show reception a queue of awaiting form, pending verification, missing items, awaiting member response and pending approval. Map these to existing query lifecycle states plus a form/review stage, rather than duplicating the entire query status model. Use existing owners, due dates and notifications for follow-ups.

**Acceptance:** a member response brings the query back to reception's attention; overdue cases surface to the responsible staff; completed tasks stop producing outstanding-work reminders.

### Stage 6 — Verification and controlled release

Test the full reception → member → reception → decision → completion journey using disposable data. Cover both payment options, zero arrears, three/four unpaid instalments, eligibility evidence, corrected quotes, replaced files, permissions, tenant isolation, expired links, repeat submissions and repeated completion attempts.

Test totals immediately below, exactly at and immediately above R2,000 and R3,000 for each payment option. Verify fractional-cent rounding and that six payments sum to the net total. Confirm unrelated collection discount rules are unaffected.

Review desktop and phone layouts, then prepare additive migrations and release notes. Existing production records are not recalculated. Production rollout and real outbound messages are separate from development/testing and must follow the authorised release process.

**Acceptance:** focused cancellation/query tests and relevant regression checks pass, the example is demonstrable, and the remaining business decisions below are resolved before live completion is enabled.

## 4. Decisions to resolve during implementation

These do not prevent starting Stage 1:

1. Confirm the contract source for penalty tiers beyond the agreed first-year example. Do not assume the existing 36-month transition at 18 completed months is a newly approved policy.
2. Confirm the effective cancellation date and the first instalment date, including when access and recurring membership billing end while settlement payments continue.
3. Define how long a cash quote remains valid and what happens if it is not paid; do not invent a deadline.
4. Confirm which existing Cognito cancellation fields/declarations must be retained, and the intended bank-statement period. No statement period was agreed in this conversation.
5. Confirm which existing DebiCheck status/reference counts as in place and how the income confirmation is recorded. Do not add payslips or other income documents as new eligibility conditions without agreement.

## Progress

**Stage 1 implemented 24 October 2026 (not yet released):**

- `calculate_cancellation_quote` in `onecpase/queries.py` replaces the 50% lump-sum / flat R1,500 rule with the agreed bands. It uses exact cent arithmetic: the discount is rounded to cents first, and the final instalment absorbs any rounding so the six payments add up to the net total.
- A new `cancellation_quotes` table saves every quote as a version, with its inputs, policy version, staff member and timestamp. Revising from the query page requires a reason, saves the next version, logs the old and new values and uses the current outstanding balance.
- The Add Query form shows a live preview of both options. The preview comes from the server's calculation, so it matches the saved quote. The manual discount field has been removed for new quotes.
- Existing queries keep their stored amounts and are labelled as quoted under the earlier rule.

**Follow-up, same day:**

- Logging a cancellation query never cancels the account. The intake checkbox has been removed.
- On the query page, "cancel the account" appears only when the selected option is cash and the recorded payments cover the cash total. Those payments must be cash, card, EFT, SnapScan or other; be paid or partially paid; and be dated on or after the quote. DebiCheck debits and pending, failed, reversed or registration-fee payments do not count. Reception's request goes to a manager. A manager making the request is the approver, as before. On approval, the payment is checked again before the account is cancelled.
- Six-instalment quotes require the agreed first payment date. The quote stores all six payment dates, and the preview and query page show them.

**Not built yet:** the *Cancellation in progress* status, suppressing and loading debit orders, the end-of-settlement reminder and payment count, the *Payments completed* action, and routing the mandate cancellation to the manager (Stage 4/5).

## 5. Immediate next step and completion milestone

Start with Stage 1: replace the obsolete settlement formula and build the two-option reception quotation, with focused tests. Demonstrate the agreed example before extending it into the member form.

Cancellation is ready for the next form type only when both cash and six-month journeys can be demonstrated end to end, evidence and corrections remain traceable, and membership execution is separate from approval. The reusable forms, links, review and communication components can then support freezes and refunds.
