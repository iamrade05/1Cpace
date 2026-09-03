# 1Cpace V6.2 Enforcement Matrix

This release closes the cross-module lifecycle bypasses identified in the V6.1 continuous state audit.

| Rule | Enforcement |
|---|---|
| Lead conversion requires attendance | PASS — conversion checks visit/appointment attendance |
| Lead conversion cannot create Active/Joined | PASS — creates Unverified/application state and advances canonical sales stage only |
| Normal member creation cannot create Active/Joined | PASS — direct add is forced to Unverified/application state unless explicit lifecycle permission is held |
| Member profile edit cannot change lifecycle | PASS — member_status/join_date are preserved for ordinary editors |
| Canonical sales stage is used for conversion | PASS — conversion advances through APPLICATION_INVITED → APPLICATION_STARTED |
| Decision Follow-Up 5-working-day task | PASS — scheduler now defines and executes the worker |
| Application verification gate | PASS — Ready-to-Push derives from checklist + latest statement analysis |
| Generic checklist cannot forge terminal evidence | PASS — terminal evidence remains source-workflow controlled |
| Manager application approval | PASS — dedicated application_approvals permission |
| Itensity push | PASS — atomic claim + valid external reference required |
| PTP cannot be marked paid manually | PASS — paid/partially_paid are rejected by status route |
| Verified payment reconciliation | PASS — verified receipt reconciles ledger/PTP and recalculates access |
| PTP creation cannot itself grant access | PASS — live policy and approval must allow it |
| Access override | PASS — manager/admin role controlled and policy checked for allow/temp-unblock |
| Six-month inactive | PASS — background collections automation |
| DebiCheck submitted vs approved | PASS — approved states are explicit |

## Remaining external integrations

- WhatsApp/SMS provider sending is not implemented by this source; the rule engine can determine the required channel.
- Itensity transaction ingestion still depends on the available export/import mechanism.
- Physical turnstile OPEN/DENY hardware protocol requires the vendor integration.
- In-process APScheduler is not a durable distributed worker; production hardening may move these jobs to a dedicated worker.
