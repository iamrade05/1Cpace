# Daily staff KPI targets

Confirmed on 26 October 2026. Targets apply to each staff member on each South African calendar day.

| Department | Metric | Daily target |
| --- | --- | ---: |
| Sales | Connected calls | 30 |
| Sales | Referrals captured | 30+ |
| Sales | Appointments booked | 10 |
| Sales | Joined members | 3 |
| Reception | Outbound calls | 100 |
| Reception | Connected calls | 10 |
| Reception | Phone PTPs | 2 |
| Reception | Separate walk-in PTPs | 2 |
| Reception | Completed/authenticated DebiCheck mandates | 2 |

The scorecards appear on Sales, Reception and Collections dashboards. They show each active department staff member's actual count, target, percentage and whether the target was met. The day selector is independent of the existing reporting-period filters. Existing department access boundaries apply. The counts recalculate from local source records when the dashboard is loaded; the new calendar day does not require a manual reset.

## Counting rules

- Calls use individually attributed outbound PBX records. Connections use the existing `contact_talk_seconds` rule (60 seconds by default). Ringing time, inbound calls and calls without a staff attribution do not earn connection credit. Naive PBX start times follow the existing PBX local-time convention; UTC database clocks are converted to South African time.
- Referrals count non-duplicate leads originally sourced as Referral, credited to their creator on capture day. Reassigning a lead does not create another referral.
- Appointments count the first recorded `APPOINTMENT_BOOKED` transition for a non-duplicate prospect, credited to the booking staff member on booking day. The future appointment date and subsequent rescheduling do not create new credit.
- Joined members reuse the existing Sales credit rule: first compliance completion, attributed to the credited consultant, with current compliance blockers excluded. A lead agreeing to join or an incomplete application does not qualify.
- PTPs carry an explicit phone, walk-in or other contact channel. Collections-call arrangements are recorded as phone. Older clients can still submit an unclassified arrangement, but it does not count toward either split target. Historical channels are not inferred. Cancelled, rejected and superseded agreements are excluded; pending arrangements remain recorded promises and do not become granted through KPI recording.
- DebiChecks count once, on the day approval/authentication is first confirmed locally, against the submitting staff member (owner fallback if no submitter exists). Scheduled NuPay refreshes and manual status checks use the same recording hook. Submission, pending status, and the staff member merely refreshing a mandate do not earn authentication credit. Repeated checks or a later reactivation do not count twice. First local confirmation is used because these integrations do not reliably supply the bank's exact authentication timestamp.

## Historical records and migration

Existing approved mandates are marked as previously authenticated without assigning a guessed completion date. Existing PTPs keep an unknown channel. Their unknown history is excluded from the corresponding daily targets and explained on the dashboard.

The migration adds columns and preserves existing financial, member and employment records. Reception's daily call target is 100. Legacy defaults of 40 or 80 are corrected to 100; other custom target values are preserved. Existing allocated work is retained.

Release evidence and scoped source snapshots are under `artifacts/staff-kpi-release-20261026/`. The HR leave draft and unrelated fitness-library work are outside this release.
