# 1Cpace Functional Rule Test Matrix — V6.3

## Purpose

This matrix validates the continuous lifecycle rather than individual screens.

**Important:** the source-level compile check passed. Full pytest execution was attempted but could not start in this environment because Flask is not installed and the environment has no network access to install dependencies. Therefore entries below are classified as `TEST DEFINED` unless supported by an existing executable test already present in the project.

## A. Sales → Application

| # | Scenario | Expected enforcement | Source/test coverage |
|---|---|---|---|
| S01 | New lead is captured | Stage = CAPTURED | TEST DEFINED |
| S02 | Lead is assigned | CAPTURED → ASSIGNED | Existing sales pipeline tests |
| S03 | Contact attempt recorded | ASSIGNED → CONTACT_ATTEMPTED | Existing pipeline |
| S04 | Contacted | CONTACT_ATTEMPTED → CONTACTED | Existing pipeline |
| S05 | Qualification missing required data | QUALIFIED transition blocked | Existing qualification gate |
| S06 | Qualification complete | → QUALIFIED | Existing pipeline |
| S07 | Appointment booked | → APPOINTMENT_BOOKED | Existing pipeline |
| S08 | Appointment confirmed | → APPOINTMENT_CONFIRMED | Existing pipeline |
| S09 | Normal lead attempts direct SHOW | BLOCKED | Existing `test_sales_pipeline.py` |
| S10 | Genuine walk-in attempts SHOW | ALLOWED | Existing `test_sales_pipeline.py` |
| S11 | Consultation completed | → CONSULTATION | Existing pipeline |
| S12 | Decision follow-up | → DECISION_FOLLOW_UP | Existing pipeline |
| S13 | Decision follow-up reaches 5 working days | System creates due follow-up | Scheduler implementation present |
| S14 | Lead tries to bypass to Joined | BLOCKED | Test should be expanded |
| S15 | Lead conversion without attendance | BLOCKED | Conversion rule present |
| S16 | Lead conversion after attendance | Creates Unverified application path | Conversion implementation present |

## B. Application → Verification

| # | Scenario | Expected enforcement |
|---|---|---|
| A01 | Application invited | APPLICATION_INVITED |
| A02 | Application started | APPLICATION_STARTED |
| A03 | Missing contract | Not Ready-to-Push |
| A04 | DebiCheck submitted only | Not Ready-to-Push |
| A05 | DebiCheck approved | Can satisfy mandate requirement |
| A06 | Bank statement missing | Not Ready-to-Push |
| A07 | Statement analysed but not qualified | Not Ready-to-Push |
| A08 | ID missing | Not Ready-to-Push |
| A09 | POS unpaid | Not Ready-to-Push |
| A10 | Guardian required but unresolved | Not Ready-to-Push |
| A11 | Manager approval required but unresolved | Not Ready-to-Push |
| A12 | All requirements satisfied | READY_TO_PUSH |
| A13 | Staff attempts to forge terminal checklist state | BLOCKED / source workflow required |
| A14 | High-risk application | Manager approval required |
| A15 | Risk override | Requires authorised override + audit |

## C. Itensity → Active Member

| # | Scenario | Expected enforcement |
|---|---|---|
| I01 | Push incomplete application | BLOCKED unless formal exception |
| I02 | Simultaneous push attempts | One atomic claim succeeds |
| I03 | Itensity returns success without reference | PUSH_FAILED |
| I04 | Itensity returns valid member reference | Activation proceeds |
| I05 | Local activation cannot reconcile sales stage | Local transaction rolls back |
| I06 | Successful activation | Application ACTIVE + member ACTIVE + sales JOINED |
| I07 | Failed push | Member must not become Active because of failed push |

## D. Collections

| # | Scenario | Expected enforcement |
|---|---|---|
| C01 | Active member with current account | Access allowed |
| C02 | Pre-debit window | Reminder task generated |
| C03 | Call not answered | Next channel = WhatsApp |
| C04 | WhatsApp unsuccessful | Next channel = SMS |
| C05 | SMS unsuccessful | Next channel = Email |
| C06 | Email unsuccessful | Escalation sequence ends |
| C07 | Failed debit | Collection case created |
| C08 | Two months arrears, no arrangement | Access blocked |
| C09 | Two months arrears + required verified upfront payment | Access may be allowed |
| C10 | Two months + submitted DebiCheck only | Access blocked |
| C11 | Two months + approved DebiCheck + valid arrangement | Access allowed |
| C12 | Three months + no settlement/DebiCheck | Access blocked |
| C13 | Three months + full verified settlement | Access allowed |
| C14 | Three months + qualifying DebiCheck arrangement | Access allowed |
| C15 | PTP created | Does not itself grant access |
| C16 | PTP payment verified | Ledger/PTP/access recalculated |
| C17 | PTP payment claimed but not verified | Must not be treated as paid |
| C18 | PTP becomes overdue | PTP = BROKEN + recovery action |
| C19 | Manager-required arrangement | Approval required |
| C20 | Access override without reason | BLOCKED |
| C21 | Access allow override while policy says blocked | BLOCKED |
| C22 | Valid manager/admin override | Allowed only with audit trail |
| C23 | More than six months unpaid | INACTIVE |
| C24 | Collections worker runs | Arrears/PTP/access/inactive rules evaluated |

## E. Access

| # | Scenario | Expected enforcement |
|---|---|---|
| X01 | Active + current | ALLOWED |
| X02 | Inactive | BLOCKED |
| X03 | Cancelled | BLOCKED |
| X04 | Two-month restricted | BLOCKED unless rule conditions satisfied |
| X05 | Three-month restricted | BLOCKED unless settlement/qualifying DebiCheck |
| X06 | Stored access says allowed but live policy says blocked | Turnstile denies |
| X07 | Stored access says blocked but live policy allows | Policy result determines the live decision; persistence is updated by the controlled workflow |

## F. Required invariants

These are the most important tests:

1. No normal route can turn an Unverified member into Active without the application activation workflow.
2. No normal route can turn a lead directly into JOINED.
3. No staff action can mark PTP `paid` without verified payment reconciliation.
4. No submitted DebiCheck mandate counts as approved.
5. No PTP creation alone grants access.
6. No normal staff access override bypasses the collections policy.
7. No incomplete application can become Ready-to-Push.
8. No Itensity push is considered successful without a valid external reference.
9. A failed Itensity push cannot create an Active Member.
10. A PTP that passes its due date without verified payment becomes BROKEN.
11. More than six months unpaid triggers INACTIVE through the automation.
12. Every rule-changing override is auditable.

## Current execution result

- Python compilation: **PASS**
- ZIP integrity: **PASS**
- Existing test suite discovered: **26 test modules**
- Full pytest execution: **BLOCKED BY ENVIRONMENT**
- Reason: `ModuleNotFoundError: No module named 'flask'`
- Dependency installation: unavailable because this execution environment has no network access.

The test suite should be run in the user's normal 1Cpace Windows environment after installing `requirements.txt`.
