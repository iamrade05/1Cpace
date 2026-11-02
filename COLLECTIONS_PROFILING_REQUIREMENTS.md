# Collections profiling — discussion requirements

Status: requirements draft from the supplied meeting transcript. This document
does not change application behaviour or authorise account changes or messages.

## Intended outcome

Help collections staff choose the next action using payment history, outstanding
debt, and contact history. Focus conversations on a concrete resolution and keep
account errors visible until corrected.

## Profile inputs

For each member, show verified outstanding arrears separately from recurring
charges, payment history (including never paid and intermittent payments), call
attempts, successful contacts, replies across channels, last interaction,
promises to pay and whether those promises were kept or broken. Show contract
dates, cancellation status, and whether recurring billing is still enabled.

Record missing history as unknown; missing data must not be treated as proof
that the client has never paid or never responded.

## Three proposed profiles

| Profile | Evidence | Staff action and message tone |
| --- | --- | --- |
| Warm | Recent meaningful engagement and evidence of payment or a kept arrangement | Personal, cooperative language; resolve the remaining balance or agree a specific payment arrangement. |
| Intermittent | Payments or responses occur inconsistently; payments may alternate with missed instalments | Clear, direct language; confirm the outstanding amount and obtain a specific payment commitment. |
| Cold | No payment movement and repeated unsuccessful contact attempts supported by available history | Firm payment/contact request; limit repeated manual calls and use the configured communication sequence. |

These are proposed labels. A points system was requested, but weights, time
windows, minimum attempts, and category boundaries were not agreed. Engagement
can make a profile warmer; a reply does not count as a payment or settlement.
Profiles must show the evidence behind their classification and refresh when
payments, replies, or contact outcomes change.

## Queue priority and effort

The discussion asks to start with large outstanding balances, particularly
clients who have never paid, while also considering recovery prospects. Keep
balance, payment behaviour, and engagement visible as separate factors. The
precise order between high-value cold accounts and warmer recoverable accounts
remains to be defined before implementing a combined ranking.

Repeated non-response should move a case through the existing communication
sequence rather than consume indefinite manual calls. Do not delete the debt
or its history when changing the next action. Attempt limits and retry periods
remain to be defined; retain existing configured call-rest rules meanwhile.

## Profile-based WhatsApp drafts

Use the existing WhatsApp option to prepare a message appropriate to the
profile. Example drafts:

- Warm: "Hi {name}, thank you for speaking with us. Your outstanding balance
  is {amount}. Please confirm payment by {date}, or contact us today to agree
  an arrangement."
- Intermittent: "Hi {name}, your account has an outstanding balance of
  {amount}. Please make payment by {date} or contact us today to agree a
  payment arrangement."
- Cold: "Hi {name}, our records show an outstanding balance of {amount}.
  Please make payment or contact us by {date} to resolve your account."

Populate amounts from reconciled balances and dates from a recorded deadline.
Log the template, channel, timestamp, delivery outcome, and reply. The meeting
suggested possible legal-action wording; the trigger and exact wording were
not settled. A cold profile alone does not establish a legal escalation.

## Eligibility and recurring-account cleanup

- Exclude confirmed cancelled members from the routine collections call queue,
  as requested. Preserve their balances and audit history. The meeting did not
  define a separate recovery process for cancelled members with debt.
- Contract expiry alone must not exclude a member with genuine arrears.
- Distinguish legitimate unpaid contract charges from charges incorrectly
  generated after a fixed term. Elapsed time or a completed debit mandate alone
  does not prove settlement or establish that every later charge is incorrect.
- For a verified completed 12-month contract whose billing should stop, correct
  the account and disable the recurring billing mechanism at its source.
  Confirm how account deactivation affects billing and access before applying it.
- Keep recurring-charge discrepancies visible in an assigned cleanup workflow
  until the underlying account is corrected. Removing a queue entry does not
  fix the billing source. After correction, verify that the next billing cycle
  produces no unintended charge.
- Preserve an audit of the contract evidence, balance reconciliation, billing
  correction, responsible staff member, and verification result. Bulk cleanup
  requires the same checks for each account.

## Resolution and staff performance

Every call needs a recorded outcome. When contact is made, seek payment,
a specific dated promise, a valid arrangement, or a documented dispute with an
owner and next action. "I will come next week" is incomplete without an exact
date, agreed action, and recorded follow-up. Staff should seek resolution today
while recording the client's actual response accurately.

Management needs counts and rates rather than statements such as "everyone
does this": attempts, successful contacts, no answers, reasons for non-payment,
dated commitments, promises kept/broken, verified money recovered, and unresolved
cases. Separate account-cleanup work from debt recovery, and keep automated
messages separate from staff calls. Reporting periods and attribution rules
remain to be specified.

## Behaviour to verify when implemented

1. Cancelled members leave the routine call queue without losing their history.
2. Expired members with valid arrears remain eligible for recovery.
3. Fully settled completed contracts with erroneous recurrence reach cleanup;
   correcting them prevents the next unintended charge.
4. Unknown payment/contact history does not create a false cold classification.
5. A reply updates engagement without reducing the outstanding balance.
6. Recorded payments update the balance and profile; settled accounts leave
   collections according to the existing settlement rules.
7. Profile-based WhatsApp drafts use reconciled amounts and log actual outcomes.
8. A vague future visit does not count as a resolved case or formal promise.

This draft extends COLLECTIONS_IMPLEMENTATION_PLAN.md. Scoring, ranking,
contact limits, message deadlines, legal escalation wording, and KPI attribution
require concrete definitions before their corresponding automation is built.
