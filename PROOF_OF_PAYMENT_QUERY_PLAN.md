# Proof of payment query plan

Agreed 24 October 2026. Planning only; no application changes implemented.

## Purpose

Handle a member reporting that they have paid through a member-linked payment query with a short signed proof-of-payment form.

## Member requirement

Send the member a short form showing saved name/contact/email for confirmation and a correction line if incorrect. The member uploads proof of payment, acknowledges the shared processing/no-guaranteed-approval notice and personally signs before submission. Proof of payment is the only supporting-document requirement; do not add a narrative questionnaire or extra evidence requirements.

On submission, automatically retain the signed form, signature and all uploaded attachments on the member's profile, linked to the originating query. See `MEMBER_QUERY_SHARED_REQUIREMENTS.md` for the rule applying to all query workflows.

## Reception workflow

1. Open/log the member-linked payment query and issue the short form. On its return, show the signed form and proof automatically on both the query and member profile.
2. Inspect DebiCheck transaction history, including the latest actual payment received and its result. Distinguish a successful collection from a pending/failed instruction.
3. Check when the last payment was updated on the member's account and which payment that update represents.
4. Compare the proof, transaction history and account update: payment date, amount and reference where available. Do not match on date alone or allocate a payment twice.
5. Record the comparison and outcome: payment already reflected, received but not yet reflected, or not yet confirmed / discrepancy requiring investigation.
6. Retain query ownership and follow-up while a discrepancy remains unresolved. Record the resolution once confirmed.

Uploading proof does not mark the account paid or automatically create/allocate a financial entry. A successful transaction is different from its posting/update time in the member account. If the proof concerns another payment channel, missing DebiCheck activity alone does not prove nonpayment; investigate the relevant receipt records internally without automatically demanding extra member documents.

## Staff view and history

Show the uploaded proof, latest DebiCheck payment/result, last account-payment update, reception comparison, current owner and next action within the existing query. Keep attachments protected and record reviewer/time and the resolution. No separate approval or routing to a payment processor has been agreed.

## Acceptance

- A returned payment form requires a member signature, and proof of payment is the only supporting document.
- The signed form, signature and every attachment automatically appear on the correct member profile, linked to the query, without duplicate records on repeat submission.
- Reception can compare source transactions with the last payment update.
- Pending/failed transactions are not mistaken for received funds.
- Already-posted payments cannot be duplicated through query handling.
- Unresolved discrepancies remain visible for follow-up.
- Only authorised staff can access proof or record resolution.

## Detail still to define

Who is authorised to correct/allocate an unposted payment, and the existing reconciliation action to use. Do not grant a new financial-write permission or invent a manager approval step through this query plan.
