# Staff notifications and escalation follow-up

Deployed on 9 October 2026 to the shared production/beta service at phase 3.
Production and beta currently share one service; a deployment updates both.

## Staff setup

In **Admin → Users → Edit**, choose each employee's **Supervisor** and save.
The supervisor must be an active staff account in the same gym. Self-assignment
and reporting cycles are rejected. No existing employee relationships are
guessed or populated automatically. Employees without a supervisor see a setup
notice in Notifications; their own alerts still work.

## Behavior

The top bar has a Notifications count, refreshed every minute while the page
is visible. Notifications are private to the recipient. The assigned employee
and that employee's own supervisor receive separate alerts. When work has no
individual assignment, the recorded creator/requester receives the employee
alert. If neither exists, there is no employee recipient: assign an owner in
the underlying workflow. Existing manager queues still expose unassigned work.

Sources covered:

- Due Queries, escalated Queries, and pending Query approvals.
- Pending Sales follow-up activities when their scheduled/follow-up date is due,
  and Sales activity approvals.
- Membership application approvals, including automatically triggered reviews.
- Collections exceptions and pending payment-arrangement approvals. A linked
  pending exception replaces the arrangement alert to avoid duplicate cards.

Alerts remain visible until the underlying task is resolved, completed, or
decided. Each recipient may explicitly acknowledge their own alert. Opening
the page does not acknowledge it; acknowledgment does not close the task or
approve a financial request. The other recipient must acknowledge separately.

Once overdue, the alert receives one reminder per Johannesburg calendar day
until that recipient acknowledges it or the source is resolved. The scheduler
checks every five minutes; opening Notifications or polling its count also
refreshes the sources. Reminder date/count are visible on the alert. Missed
days are coalesced on recovery rather than generating a burst of old reminders.
New escalations/approval requests create fresh alerts even after an earlier
alert was acknowledged. Changes in assignment/supervisor retire the previous
recipient's alert. Source links retain existing module permissions.

The standard reminder workflow uses in-app alerts. Elev8 query assignment
and escalation emails are described below. Only sources enabled by the active phase generate alerts.
The additive migration preserves existing users and business records.

## Validation

See `artifacts/notification-expanded-tests.txt` for the final source/recipient
checks (46 passed), `artifacts/notification-background-tests.txt` for background
creation/schedule checks (2 passed), and `artifacts/notification-final-check-0.txt` for the wider 81-test
workflow, permissions, tenant, scheduler, and template checks.
Browser checks cover 22 screens at two widths (44 renders), all HTTP 200,
without overflow or JavaScript errors; explicit acknowledgment is exercised.
Evidence: `artifacts/notification-ui-final.txt` and `artifacts/ui-review/`.
Both copied database upgrades pass integrity and preserve operational row
counts: `artifacts/notification-final-check-1.txt`. Original databases are not
modified. The complete release suite passed before deployment: 797 tests, zero failures.
Deployment and public checks: `artifacts/deployment-notifications.txt`,
`artifacts/notifications-public-check.json`, and
`artifacts/notifications-live-schema-check.json`. All 237 release files match
the deployed copy, the service is active at phase 3, and both sites are healthy.
Fifteen active staff accounts still need an individual supervisor selected.


## Elev8 query routing ? 11 October 2026 (local changes)

New Elev8 queries route refunds to Ayrton, Sales and membership renewal,
upgrade/downgrade and reactivation queries to Thato, and other queries to the
Admin queue. The department appears as the owner when the queue is shared.
Named staff are matched by the supplied email, or a unique active staff name;
no account is created when a match is missing. Routing is restricted to the
Elev8 tenant; other gyms keep their existing assignment behavior.

Assignment emails use the application SMTP configuration. Refunds notify
ayrton@elev8healthandfitnessgym.com. Sales assignments notify
sales@elev8healthandfitnessgym.com and ayrton@elev8healthandfitnessgym.com.
Escalations notify mvuleni@elev8healthandfitnessgym.com and
ayrton@elev8healthandfitnessgym.com, including when approval is still pending.
Admin-queue assignments notify active admins using their staff email; Mvuleni's
supplied address is used when his matched account is an admin. Emails contain
query reference, category, priority and follow-up date, with no member banking
or query-description details. Delivery failure shows a warning and preserves
the saved query. Unchanged assignment updates do not resend an assignment email.

Due in-app Sales alerts also reach Ayrton; escalation alerts reach Mvuleni and
Ayrton. Scheduler and request refreshes use the same routing recipients.
These changes have not been deployed and do not bulk reassign older queries.
