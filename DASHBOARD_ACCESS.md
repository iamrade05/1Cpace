# Department dashboard access

Implemented and deployed on 25 October 2026, together with the Fitness navigation cleanup.

Deployment verification: 190 release tests passed; nine server-side page checks and 26 department access denials passed on database copies. All 16 installed file hashes matched, both public health endpoints returned healthy, and no new dashboard or WhatsApp worker errors appeared after restart. Code and database rollback backups are retained at `/opt/onecpase-backups/workflow_dashboard_20261025_182542`. Evidence is in `artifacts/dashboard-navigation-release/verification.json`.

Fitness navigation now omits the standalone Anatomy & exercises item and the onboarding Fitness column. Anatomy & exercises is available from Workout Plans and its new-plan page. The workout programme drafts from chat were not included in this release.

| Account | Landing dashboard | Other dashboard access |
| --- | --- | --- |
| Active tenant admin | Main management overview | May inspect department dashboards |
| Sales staff and Sales managers | Sales dashboard | Sales data only |
| Reception and Collections staff/managers | Reception dashboard | Related collections/PTP work summaries |
| Fitness staff and Fitness managers | Fitness dashboard | Fitness work only |
| Other departments or unclassified managers | My work dashboard | Their own assigned/logged queries only |

The database role and department determine access. A `dashboard` permission, a stale admin session, or a manager role does not grant the main overview. `/dashboard` redirects non-admin staff to their department dashboard. Cross-department dashboard URLs are rejected before business metrics load. The global report overview/custom report builder and reconciliation actions require an admin.

Reception shows the local gym member total and membership states, joins and acquisition sources for the selected period, reconciled outstanding balances, received collections, billing coverage, calls and connection rate, queries logged/open workload, and PTPs made. Financial totals use the same payment matching as member profiles. Collection receipts exclude discount entries. Period activity and current-state totals are labelled separately; there is no external provider call during the department dashboard load.

Sales keeps its funnel and workload views. Consultant filters, completed-sale credit, mandate follow-ups, compliance figures and manager approval metrics are restricted to Sales owners. Filtering by an owner in another department is rejected.

Fitness shows member joins, recorded assisted sessions, goal clarification/allocation queues, welcome delivery follow-ups, instructor offers, PT-rights reviews and completion reviews. It contains no collection, revenue, arrears, sales or optional medical details.

All reads use the resolved tenant database. Department navigation follows the same rules as the routes. Operational permissions for member records, case handling and approvals remain separate from dashboard visibility.
