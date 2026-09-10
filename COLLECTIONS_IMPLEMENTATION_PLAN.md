# 1Cpase Collections — Master Implementation Plan

## Purpose

1Cpase is the rule engine for collections. Reception records calls, outcomes,
communications, arrangements, payments, and evidence. The application decides
the next action, access status, repayment choices, escalation, and inactive
status. Management receives exceptions rather than routine cases.

## Rules

1. Pre-debit reminder tasks are created from configured debit dates using a
   configurable reminder offset (default: two days).
2. Every collection task starts with a call. A call is complete only when a
   valid outcome is recorded. A real contact means the client or an appropriate
   person was reached.
3. Unsuccessful contact follows the fixed sequence: `CALL` → `WHATSAPP` →
   `SMS` → `EMAIL`.
4. Each collections caller has a configurable daily call target (default: 40).
   Contact rate is tracked separately from call activity.
5. A failed debit creates or refreshes an open collection case.
6. At two months owing, an active DebiCheck arrangement allows access. Without
   DebiCheck, the configured upfront arrears contribution must be paid before
   access is allowed (default range: 30–40%). Otherwise access is blocked.
7. At three or more months owing, full settlement or a valid DebiCheck
   arrangement is required. A non-standard request belongs in the exception
   queue and cannot be silently overridden by reception.
8. DebiCheck repayment plans support three months, six months, and the remaining
   contract term. The arrears component has a configurable minimum (default:
   R30.00).
9. Every promise to pay is a formal PTP record. The daily automation marks an
   overdue unpaid promise as broken and recalculates access.
10. An unpaid account older than six months is marked inactive. Inactive means
    no deletion; all account, payment, communication, PTP, and decision history
    remains available.

## Implementation

`onecpase/collections_engine.py` is the shared policy layer. It provides:

- Decimal-safe money calculations and DebiCheck options.
- Two-month, three-month-plus, and inactive account decisions.
- Communication sequencing and required call outcome validation.
- Daily call KPI and PTP status calculations.
- SQLite tables for cases, communications, payments, exceptions, access
  decisions, and configurable rules.

The existing modules use the engine as follows:

- `collections.py` reads the configured call/reminder rules, records call and
  communication audit events, and creates failed-debit cases.
- `ptp.py` evaluates every owing member through the shared access decision and
  records the resulting case and access decision.
- `database.py` creates the additive engine tables and seeds defaults on every
  tenant database without replacing existing data or screens.

## Core screens

The existing Collections call queue, client profile, PTP, DebiCheck, access, and
reporting screens remain the user-facing surfaces. Their next integration pass
should expose the engine fields directly:

- Collections dashboard: queue counts, 40-call progress, contact rate, PTP
  status, access restrictions, inactive accounts, and pending exceptions.
  **Built** — the PTP/Collections dashboard now carries Awaiting Manager
  (linking to the exception queue, manager/admin only), Calls Today against the
  configured target, and Contact Rate. `collections.daily_call_progress()` is
  shared with the call queue screen so the two cannot quote different numbers,
  and `collections.pending_manager_decisions()` counts both kinds of
  escalation.
- Reception queue: client, failed debit/reminder reason, months owing, amount,
  next action, priority, and assigned caller.
- Client collections profile: payments, failed debits, call/communication
  history, PTP, DebiCheck, access decision, and exceptions.
- Arrangement screen: rule-approved options with required upfront amount or
  DebiCheck requirement shown before submission.
- Management exception queue: reason, system decision, requested override,
  decision, condition, approver, and audit timestamp.
  **Built** — `/collections/exceptions`, manager/admin only. It shows both
  payment arrangements the discount tiers could not auto-approve and open
  `collection_exceptions` rows, and records every decision with its condition
  against the member.

  The Rule §7 producer is wired: creating a payment arrangement for a member
  at three or more months owing, without full settlement or a qualifying
  DebiCheck arrangement, records the request, forces manager approval, and
  raises a `collection_exceptions` row. Reception is told the request went to
  the manager rather than that it succeeded. Repeat requests do not stack
  duplicate open exceptions.

## Configuration and rollout

Rules are stored in `collection_rules`, so changing the call target, reminder
offset, upfront range, minimum recovery instalment, or inactive threshold does
not require a code deployment. The initial rollout should validate the seeded
defaults against the gym's approved policy, then add management exception
approval UI and provider-backed WhatsApp/SMS sending while retaining the same
audit tables and decision engine.

## V6 enforcement pass

The collections workflow is now enforced without requiring a receptionist to
open a screen first:

- The scheduler runs the collections/arrears automation every 15 minutes.
- Failed/pending/partial outstanding balances are re-evaluated and open cases
  are refreshed automatically.
- Access is evaluated live at the turnstile from the collections policy; a
  stale `gym_access_status=allowed` cannot grant access when the current
  collections rule says blocked.
- Two-month access requires either the qualifying arrangement or the required
  upfront contribution; three-plus months requires full settlement or the
  qualifying DebiCheck arrangement.
- A submitted DebiCheck mandate is not treated as approved.
- PTP expiry and temporary-unblock expiry remain automatic.
- The daily call queue is generated by the scheduler rather than by page load.
- Unsuccessful contact advances to the fixed CALL → WHATSAPP → SMS → EMAIL
  sequence instead of sending the final email immediately after the call.
- Manual access changes are restricted to manager/admin roles.

The remaining provider-specific limitation is deliberate: WhatsApp/SMS cannot
be sent automatically until a provider integration is configured. 1Cpase still
controls the required channel and records the human/provider action in the
audit trail.
