# 1Cpace V6.1 — Rule Enforcement Audit

This release hardens the existing V6 workflow. The objective is that ordinary staff actions cannot manufacture compliance/payment/access states that are owned by system or approval workflows.

| Rule / Function | V6.1 | Enforcement |
|---|---|---|
| Pre-debit reminder | PASS | Existing configured debit-cycle automation retained |
| 40-call daily target | PASS | Existing call-cycle/KPI retained |
| Call → WhatsApp → SMS → Email | PASS | Next channel is enforced by task state |
| Failed debit → collection case | PASS | Existing case sync retained |
| 2-month arrears | PASS | Live policy evaluation; verified upfront receipts can satisfy configured minimum |
| 3+ month arrears | PASS | Full settlement or qualifying confirmed DebiCheck arrangement |
| DebiCheck submitted ≠ approved | PASS | Canonical approved states only |
| PTP requires monitoring | PASS | PTP payment states are system-controlled |
| PTP paid from verified payment | PASS | Manual paid/partially_paid route blocked; verified receipt reconciles ledger/PTP |
| PTP broken automatically | PASS | Scheduler marks overdue unpaid PTP broken |
| Temporary access expiry | PASS | Scheduler blocks expired temporary access |
| PTP temporary unblock | PASS | Access only granted when live policy permits and approval condition is resolved |
| Manual access override | PASS | Manager/admin only, reason required, live policy cannot be bypassed for ALLOWED/TEMP |
| Six-month inactive | PASS | Existing automatic inactive rule retained |
| Legal referral >6 months | PASS | Existing threshold retained |
| Application contract signed | PASS | Generic checklist cannot manufacture signed state; contract workflow owns it |
| Application DebiCheck approved | PASS | Generic checklist cannot manufacture approved mandate |
| Bank statement analysed | PASS | Generic checklist cannot manufacture analysis; analysis workflow owns it |
| ID verified | PASS | Generic checklist cannot manufacture verification |
| POS paid | PASS | Generic checklist cannot manufacture payment |
| Manager application approval | PASS | Dedicated `application_approvals` permission; Sales department no longer receives HR approval permission |
| Risk rating | PASS | System-derived by default; manager/admin override is audited |
| High-risk approval gate | PASS | Underlying high-risk indicators always trigger manager approval even if displayed risk is overridden |
| Contract approval sync | PASS | Approved shared contract updates application checklist and status |
| Decision follow-up | PASS | Scheduler maintains five-working-day follow-up action |
| Itensity push | PASS | Existing atomic push and external reference validation retained |
| Turnstile | PASS | Live collections policy is evaluated at entry; policy failure denies access |
| WhatsApp/SMS physical sending | EXTERNAL | Provider integration is still required; system enforces required channel/order |
| Physical turnstile OPEN/DENY command | EXTERNAL | Hardware protocol must be connected/confirmed |
| Durable background worker | EXTERNAL | Current APScheduler remains in-process |
