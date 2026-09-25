# 1Cpace V8 --- Sales Lead-to-Consultation Implementation Specification

**Document Type:** Functional + Technical Implementation Specification\
**Scope:** Lead Capture → Assignment → Contact → Appointment → Show →
Consultation → Decision → Application\
**Version:** V8 Implementation Proposal\
**Status:** Proposed for implementation\
**Primary Objective:** Convert the existing Sales screens into one
controlled, event-driven sales workflow without duplicating or
destroying the existing V8 controls.

------------------------------------------------------------------------

# 1. Purpose

This document defines exactly how the V8 Sales workflow should operate
from the moment a prospect is captured until the prospect either:

-   joins and proceeds to Application;
-   requires follow-up;
-   is lost/not interested; or
-   enters another controlled outcome.

The specification is based on the current V8 screens and workflow
visible in the supplied screenshots.

The current V8 application already contains:

-   Prospect Capture
-   Duplicate-phone checking before capture
-   Original Lead Source
-   Referrer
-   Consent
-   Automatic consultant assignment
-   Controlled Sales Stage
-   Required Next Action
-   Contact Activity
-   Contact Outcome
-   Appointment date/time
-   Sales notes/objections
-   Chronological Sales Timeline
-   Show/attendance handling
-   Controlled stage transitions

The objective is therefore **not to replace the existing Sales system**.

The objective is to make the existing components operate as one coherent
workflow.

------------------------------------------------------------------------

# 2. Current V8 Situation

## 2.1 Prospect Capture

The current Capture New Prospect screen contains:

-   Full Name
-   Phone
-   Email
-   Original Lead Source
-   Referrer
-   Consent Preferences
-   Profile Notes

The screen explicitly instructs the user to search by phone before
capture.

The original source is locked after capture for attribution purposes.

The system also automatically assigns the prospect across available
sales consultants.

### Current strength

The system already establishes:

``` text
Prospect
→ Source
→ Attribution
→ Consent
→ Consultant
→ Initial follow-up
```

### Issue

The capture screen is primarily a data-entry point. The business
workflow begins only after capture.

The system must ensure that capture always creates the correct initial
workflow state and next action.

------------------------------------------------------------------------

# 3. Assignment

After capture, the current V8 screen shows:

``` text
ASSIGNED
```

The system displays a confirmation that the prospect has been assigned
to a sales consultant for contact.

The system also creates:

``` text
Required Next Action
→ Initial contact call
```

The chronological timeline records the assignment/follow-up event.

## Required behaviour

When a prospect is created:

1.  Determine the correct available sales consultant.
2.  Assign the prospect.
3.  Set controlled sales stage to `ASSIGNED`.
4.  Create the initial contact task.
5.  Set the required next action to `INITIAL CONTACT CALL`.
6.  Record the assignment event in the sales timeline.
7.  Record who performed/caused the assignment.
8.  Record the date/time.
9.  Prevent the prospect from appearing as unassigned unless an explicit
    exception exists.

------------------------------------------------------------------------

# 4. Contact Activity

The current V8 Contact Activity screen contains:

-   Contact Method
-   Contact Outcome
-   Appointment Date and Time
-   Notes / Objection
-   Save Contact Result

This is an important workflow point.

## Current issue

The Contact Activity currently combines:

-   communication event;
-   contact result;
-   appointment creation;
-   notes/objection.

These are related, but they have different meanings.

The system must treat them as one interaction with controlled
consequences.

------------------------------------------------------------------------

# 5. Required Contact Model

A Contact Activity should represent:

``` text
CONTACT EVENT
│
├── Who contacted the prospect?
├── When?
├── Method?
├── Was contact successful?
├── Outcome?
├── What was discussed?
├── Objection?
├── Appointment created?
└── What is the next action?
```

The contact result must drive the next workflow.

------------------------------------------------------------------------

# 6. Contact Outcomes

The system should maintain controlled contact outcomes.

Examples:

### Client contacted

The prospect was successfully reached.

Next:

``` text
CONTACT ATTEMPTED
→ CONTACTED
```

### Client made an appointment

Next:

``` text
CONTACTED
→ APPOINTMENT BOOKED
```

The appointment date/time becomes mandatory.

The system creates the appointment event and the next confirmation
action.

### No answer

Next:

``` text
CONTACT ATTEMPTED
→ FOLLOW-UP REQUIRED
```

The system must create another contact task according to the configured
follow-up rule.

### Not interested

The system must require a reason.

Examples may include:

-   Price
-   Timing
-   Location
-   No longer interested
-   Joined elsewhere
-   Other

The lead then moves to the appropriate controlled lost/nurture state.

### Call back requested

The system creates a follow-up task for the requested date/time.

### Wrong number / invalid contact

The system records the contact failure and creates the appropriate next
action or exception.

------------------------------------------------------------------------

# 7. Important Principle: Stage, Action and Timeline Are Different

The current V8 screen contains all three concepts.

They must remain separate.

## Controlled Stage

Answers:

> Where is the prospect in the Sales process?

Example:

`ASSIGNED`

## Required Next Action

Answers:

> What must the salesperson do next?

Example:

`INITIAL CONTACT CALL`

## Chronological Sales Timeline

Answers:

> What has already happened?

Example:

`Prospect Created → Assigned`

These three components must be linked, but must never be treated as the
same field.

------------------------------------------------------------------------

# 8. Appointment Workflow

When the contact outcome is:

``` text
CLIENT MADE AN APPOINTMENT
```

the system must:

1.  Require appointment date/time.
2.  Validate that the date/time is valid.
3.  Record the appointment.
4.  Change the controlled Sales Stage to `APPOINTMENT_BOOKED`.
5.  Create the confirmation action.
6.  Record the appointment in the chronological timeline.
7.  Preserve the salesperson who created the appointment.
8.  Preserve the original lead source and attribution.
9.  Set the next action to appointment confirmation.

The system must not require staff to manually create a second unrelated
appointment record after recording the contact result.

------------------------------------------------------------------------

# 9. Appointment Confirmation

After an appointment is booked:

``` text
APPOINTMENT_BOOKED
→ APPOINTMENT_CONFIRMATION_REQUIRED
```

The required action should be visible to the assigned salesperson.

The confirmation event must be recorded.

Possible outcomes:

-   Confirmed
-   Reschedule
-   Cancelled
-   No response
-   Cancelled by prospect

Each outcome must create the appropriate next action.

------------------------------------------------------------------------

# 10. Show / Attendance

The existing V8 screen contains a `SHOW` state and a Client Attendance
Result.

This is an important boundary.

## SHOW should answer only:

> Did the prospect attend the appointment?

It should not itself represent the complete sales decision.

The correct sequence is:

``` text
APPOINTMENT
→ ATTENDANCE
→ SHOW
→ CONSULTATION
```

If the prospect did not attend:

``` text
NO SHOW
→ RESCHEDULE / FOLLOW-UP
```

If the prospect attended:

``` text
SHOW
→ CONSULTATION REQUIRED
```

------------------------------------------------------------------------

# 11. Current SHOW Issue

The current screen presents an option such as:

``` text
Client Joined → Membership
```

and a `Not Interested` outcome.

This creates an architectural problem.

Attendance and sales decision are different events.

A prospect can:

-   attend and join;
-   attend and need more information;
-   attend and request follow-up;
-   attend and object to price;
-   attend and not be interested;
-   attend but not yet decide.

Therefore:

``` text
SHOW ≠ JOIN
```

and:

``` text
SHOW ≠ NOT INTERESTED
```

SHOW should initiate the Consultation process.

------------------------------------------------------------------------

# 12. Consultation

Consultation is the actual sales event following a confirmed SHOW.

The Consultation should not be a large unstructured form.

It should be a guided process.

## Consultation sequence

``` text
SHOW
↓
CONSULTATION START
↓
NEEDS ANALYSIS
↓
MEMBERSHIP RECOMMENDATION
↓
PRICING
↓
SALES PITCH
↓
OBJECTION HANDLING
↓
DECISION
```

------------------------------------------------------------------------

# 13. Consultation Step 1 --- Needs Analysis

The salesperson establishes the prospect's requirements.

The system should capture structured information.

## Goal

Examples:

-   Weight management
-   Muscle development
-   Strength
-   General fitness
-   Toning
-   Conditioning
-   Lifestyle/health-related fitness goal
-   Other

## Current situation

Examples:

-   Current training status
-   Previous gym experience
-   Current activity level
-   Training frequency

## Motivation

The salesperson records why the prospect is considering joining.

## Barriers

Examples:

-   Price
-   Time
-   Transport/location
-   Confidence
-   Commitment
-   Previous experience
-   Payment concern
-   Other

## Desired outcome

The salesperson records what the prospect expects to achieve.

------------------------------------------------------------------------

# 14. Consultation Step 2 --- Membership Recommendation

After Needs Analysis, the salesperson recommends an appropriate
membership.

The system should record:

-   Recommended membership
-   Membership duration
-   Monthly price
-   Joining fee where applicable
-   Card fee where applicable
-   Levy where applicable
-   Payment method
-   Reason for recommendation
-   Relevant facilities/classes/benefits

The recommendation should be connected to the prospect's stated needs.

The salesperson should not need to manually re-enter membership
information later.

------------------------------------------------------------------------

# 15. Consultation Step 3 --- Pricing

The pricing section must present the commercial proposition clearly.

The system should show:

-   Monthly membership amount
-   Joining fee
-   Card fee
-   Levy
-   Payment date
-   Payment method
-   Contract duration
-   Included benefits

The exact tariff must come from the configured V8 membership/pricing
data.

The salesperson should not manually type the price into a free-text
field where the system already has the applicable tariff.

------------------------------------------------------------------------

# 16. Consultation Step 4 --- Sales Pitch

The Sales Pitch should connect:

``` text
PROSPECT NEED
→ MEMBERSHIP SOLUTION
→ VALUE
```

The salesperson should be able to record the key proposition presented.

The pitch may be based on:

-   Prospect goal
-   Recommended membership
-   Facilities
-   Classes
-   Support
-   Convenience
-   Value
-   Other applicable benefits

The system should retain the fact that the pitch occurred and the
relevant notes.

------------------------------------------------------------------------

# 17. Consultation Step 5 --- Objection Handling

Objections must be structured.

The system should allow controlled objection categories.

Examples:

-   Price
-   Contract duration
-   Payment method
-   Facilities
-   Location
-   Time
-   Commitment
-   Comparing competitor
-   Needs approval from another person
-   Not ready
-   Other

Each objection should contain:

``` text
Objection Category
+
Description
+
Response
+
Outcome
```

The system should not force every objection into free text only.

------------------------------------------------------------------------

# 18. Consultation Step 6 --- Decision

The consultation must end in a controlled decision.

Recommended outcomes:

## JOIN

The prospect has agreed to proceed.

Next:

``` text
JOIN
→ APPLICATION
```

The recommended/agreed membership and commercial information must carry
into the Application.

## FOLLOW-UP

The prospect has not rejected the offer but needs another interaction.

Next:

``` text
FOLLOW-UP
→ SALES FOLLOW-UP TASK
```

The follow-up date and reason become mandatory.

## NOT INTERESTED

The prospect does not want to proceed.

A reason becomes mandatory.

Next:

``` text
NOT INTERESTED
→ LOST / NURTURE
```

according to the configured lifecycle rule.

## NOT READY

The prospect is interested but cannot join now.

Next:

``` text
NOT READY
→ FOLLOW-UP
```

## OTHER CONTROLLED OUTCOME

Any additional outcome must be explicitly defined and must create a
valid next action.

------------------------------------------------------------------------

# 19. Application Handoff

When:

``` text
CONSULTATION DECISION = JOIN
```

the system should automatically create/initiate the Application
workflow.

The application should inherit:

-   Prospect
-   Consultant
-   Recommended/agreed membership
-   Price
-   Contract duration
-   Payment method
-   Relevant consultation information
-   Sales source
-   Attribution

The salesperson should not need to recreate the prospect as a new
person.

------------------------------------------------------------------------

# 20. Controlled Stage Transitions

The Sales Stage should be controlled.

Recommended lifecycle:

``` text
CAPTURED
↓
ASSIGNED
↓
CONTACT_ATTEMPTED
↓
CONTACTED
↓
QUALIFIED
↓
INVITED
↓
APPOINTMENT_BOOKED
↓
APPOINTMENT_CONFIRMED
↓
SHOW
↓
CONSULTATION
↓
DECISION_FOLLOW_UP / JOIN / LOST
↓
APPLICATION
```

The exact existing V8 stage names must be preserved where they already
match the business rules. Changes should be made centrally rather than
by creating duplicate statuses.

------------------------------------------------------------------------

# 21. State Transition Rules

Each transition must have:

``` text
CURRENT STATE
→ EVENT / OUTCOME
→ VALIDATION
→ NEXT STATE
→ NEXT ACTION
→ TIMELINE EVENT
```

Example:

``` text
ASSIGNED
→ Contact Activity saved
→ Contact method + outcome valid
→ CONTACT_ATTEMPTED
→ Follow-up/contact action
→ Timeline entry
```

Example:

``` text
CONTACTED
→ Client made appointment
→ Appointment date/time supplied
→ APPOINTMENT_BOOKED
→ Confirmation task
→ Timeline entry
```

Example:

``` text
SHOW
→ Attendance confirmed
→ SHOW
→ Consultation required
→ Timeline entry
```

Example:

``` text
CONSULTATION
→ Decision = JOIN
→ Consultation complete
→ APPLICATION
→ Application task
→ Timeline entry
```

------------------------------------------------------------------------

# 22. Timeline Rules

The chronological Sales Timeline should be an event history.

It should record events such as:

-   Prospect Created
-   Prospect Assigned
-   Follow-up Scheduled
-   Call Attempted
-   Contacted
-   Appointment Booked
-   Appointment Confirmed
-   Appointment Rescheduled
-   Appointment Cancelled
-   Show
-   No Show
-   Consultation Started
-   Needs Analysis Completed
-   Membership Recommended
-   Pricing Presented
-   Objection Recorded
-   Decision Made
-   Application Started
-   Application Submitted

The timeline must not be manually edited to manufacture a past event.

Events should be generated by successful workflow actions.

------------------------------------------------------------------------

# 23. Required Next Action

At all times, a prospect that is still active in Sales should have a
clear next action unless the current stage is legitimately awaiting an
external event.

Examples:

``` text
ASSIGNED
→ Initial Contact Call

CONTACT_ATTEMPTED
→ Follow-up Call

APPOINTMENT_BOOKED
→ Confirm Appointment

APPOINTMENT_CONFIRMED
→ Attend Appointment

SHOW
→ Complete Consultation

CONSULTATION / FOLLOW-UP
→ Contact Prospect

JOIN
→ Complete Application

APPLICATION
→ Complete Verification
```

This prevents prospects from becoming inactive simply because they are
still technically in the database.

------------------------------------------------------------------------

# 24. Data Integrity Rules

## Duplicate prevention

Phone-number search must remain before prospect creation.

A duplicate prospect should not be created merely because a salesperson
uses the capture screen again.

## Source integrity

Original Lead Source must remain immutable after capture for attribution
accuracy.

## Assignment integrity

Every active prospect must have an accountable consultant unless
explicitly placed in an unassigned exception state.

## Appointment integrity

Appointment outcomes requiring a date/time must not save without a valid
date/time.

## Decision integrity

A `JOIN` decision must not bypass Application.

## Consultation integrity

A SHOW must not automatically equal JOIN.

## Lost integrity

A NOT INTERESTED outcome must require an appropriate reason.

------------------------------------------------------------------------

# 25. What Should NOT Happen

The following behaviour should be prevented:

### Do not allow:

``` text
SHOW
→ JOIN
```

without Consultation.

### Do not allow:

``` text
ASSIGNED
→ JOIN
```

without the controlled stages.

### Do not allow:

``` text
CLIENT MADE AN APPOINTMENT
```

without an appointment date/time.

### Do not allow:

``` text
NOT INTERESTED
```

without a reason.

### Do not allow:

A salesperson to manually change a stage to a later stage simply to make
the pipeline look complete.

### Do not create duplicate records when moving from Prospect to Application.

### Do not duplicate the same information across unrelated tables when the information already has a single authoritative source.

------------------------------------------------------------------------

# 26. UI Structure

The existing Sales Profile layout should remain recognizable.

The permanent profile panel should continue to show:

-   Phone
-   Email
-   Original Source
-   Channel
-   Referrer
-   Campaign
-   Consultant
-   Package
-   Consent
-   Captured date

The left-side Controlled Sales Stage should continue to show:

-   Current stage
-   Next controlled stage
-   Reason/notes
-   Stage audit history

The Required Next Action should remain visible.

The chronological timeline should remain visible.

The main activity panel should change according to the current workflow
stage.

For example:

``` text
ASSIGNED
→ Contact Activity

APPOINTMENT_BOOKED
→ Appointment Confirmation

SHOW
→ Attendance / Consultation Start

CONSULTATION
→ Guided Consultation

DECISION
→ Decision Outcome

APPLICATION
→ Application workflow
```

------------------------------------------------------------------------

# 27. Consultation UI Proposal

The Consultation screen should be divided into controlled steps.

``` text
CONSULTATION
────────────────────────────────────

1. NEEDS ANALYSIS
   Goal
   Current situation
   Motivation
   Barriers
   Desired outcome

2. RECOMMENDATION
   Recommended membership
   Reason
   Benefits

3. PRICING
   Monthly amount
   Fees
   Levy
   Payment method
   Contract

4. SALES PITCH
   Proposition
   Value presented

5. OBJECTIONS
   Category
   Details
   Response
   Outcome

6. DECISION
   Join
   Follow-up
   Not Ready
   Not Interested

[Complete Consultation]
```

The consultation should show the salesperson what must be completed
next.

------------------------------------------------------------------------

# 28. Implementation Architecture

The implementation should use the existing V8 controlled workflow
architecture.

The preferred architecture is:

``` text
UI
↓
Sales Service / Workflow Handler
↓
Validation
↓
Controlled State Transition
↓
Business Event
├── Next Action
├── Timeline Event
├── Appointment / Task
└── Audit Record
```

The UI must not independently change multiple database fields and assume
that this constitutes a valid business transition.

The workflow/service layer should perform the transition atomically.

------------------------------------------------------------------------

# 29. Transaction Principle

A workflow transition should be treated as one business transaction.

Example:

When the prospect makes an appointment:

``` text
BEGIN TRANSACTION

Validate contact outcome
Validate appointment datetime
Create/update appointment
Change sales stage
Create confirmation action
Create timeline event
Create audit record

COMMIT
```

If one required operation fails, the system should not leave the
prospect half-transitioned.

------------------------------------------------------------------------

# 30. Event and Timeline Separation

The system should distinguish between:

### Business State

Where the prospect currently is.

### Business Event

What happened.

### Task

What staff must do.

### Audit

Who changed what.

Example:

``` text
Stage:
APPOINTMENT_BOOKED

Event:
Appointment booked for 15 Sep 2026 09:00

Task:
Confirm appointment

Audit:
Consultant X created appointment at 14:42
```

These should not be collapsed into one field.

------------------------------------------------------------------------

# 31. Reporting Consequences

Once this workflow is implemented correctly, management reporting
becomes more meaningful.

V8 should be able to calculate:

### Lead volume

How many leads were captured?

### Source performance

How many came from:

-   Referral
-   Social Media
-   Website
-   Walk-in

### Contact performance

-   Contact attempts
-   Contacts achieved
-   Contact rate

### Appointment performance

-   Appointments booked
-   Confirmed
-   Show
-   No-show
-   Rescheduled

### Consultation performance

-   Consultations completed
-   Membership recommendations
-   Objections
-   Decisions

### Conversion

``` text
Lead
→ Contacted
→ Appointment
→ Show
→ Consultation
→ Join
→ Application
→ Verified
→ Joined Member
```

This allows conversion rates to be calculated at every stage.

------------------------------------------------------------------------

# 32. Acceptance Criteria

The implementation is considered successful only when all of the
following work.

## Capture

-   Duplicate phone search occurs before creation.
-   Source is recorded.
-   Source cannot be silently changed after capture.
-   Consultant is assigned.
-   Initial contact task is created.

## Contact

-   Contact activity is recorded.
-   Outcome drives the next workflow.
-   Appointment outcome requires appointment datetime.
-   No-answer creates the required follow-up.
-   Not-interested requires a reason.

## Appointment

-   Appointment is recorded once.
-   Confirmation task is created.
-   Confirmation outcome changes the workflow correctly.

## Show

-   Attendance is recorded.
-   SHOW does not automatically mean JOIN.
-   SHOW creates Consultation as the next required action.

## Consultation

-   Needs Analysis is completed.
-   Recommendation is recorded.
-   Pricing is sourced from configured membership data.
-   Pitch is recorded.
-   Objections are structured.
-   Decision is mandatory to complete Consultation.

## Decision

-   JOIN starts Application.
-   FOLLOW-UP creates a follow-up task.
-   NOT INTERESTED requires a reason.
-   NOT READY creates a follow-up workflow.

## Timeline

-   Every important business event appears once.
-   Timeline events correspond to actual system events.
-   Staff cannot fabricate historical events through manual status
    changes.

## Audit

-   State changes are recorded.
-   User, date/time and reason are retained.

------------------------------------------------------------------------

# 33. Final Target Workflow

The final V8 Sales flow should be:

``` text
CAPTURE
   ↓
ASSIGN
   ↓
INITIAL CONTACT
   ↓
CONTACT ATTEMPT
   ↓
CONTACTED
   ↓
QUALIFIED
   ↓
APPOINTMENT
   ↓
CONFIRMATION
   ↓
SHOW
   ↓
CONSULTATION
   ↓
NEEDS ANALYSIS
   ↓
MEMBERSHIP RECOMMENDATION
   ↓
PRICING
   ↓
SALES PITCH
   ↓
OBJECTION HANDLING
   ↓
DECISION
   ├───────────────┐
   ↓               ↓
 JOIN           FOLLOW-UP
   ↓               ↓
APPLICATION     CONTACT
   ↓
VERIFICATION
   ↓
ITENSITY
   ↓
JOINED MEMBER
```

Alternative branches:

``` text
NO SHOW
→ RESCHEDULE / FOLLOW-UP

NOT INTERESTED
→ LOST / NURTURE

NOT READY
→ FOLLOW-UP

APPLICATION INCOMPLETE
→ CORRECTION

VERIFICATION FAILED
→ CORRECTION / EXCEPTION

ITENSITY FAILED
→ RETRY / EXCEPTION
```

------------------------------------------------------------------------

# 34. Implementation Priority

## P0 --- Correct the workflow boundary

1.  SHOW must not equal JOIN.
2.  SHOW must create Consultation.
3.  Consultation must produce a controlled Decision.
4.  JOIN must initiate Application.
5.  Stage, Next Action and Timeline must remain separate but connected.

## P1 --- Build the Consultation workflow

1.  Needs Analysis
2.  Membership Recommendation
3.  Pricing
4.  Pitch
5.  Objections
6.  Decision

## P1 --- Connect events

1.  Contact outcome → next stage
2.  Appointment → confirmation
3.  Show → consultation
4.  Consultation decision → next workflow

## P2 --- Reporting

Use the controlled events and stages to calculate conversion metrics.

## P2 --- UI refinement

Only after the business workflow is correct should the visual layout be
refined.

------------------------------------------------------------------------

# 35. Core Design Principle

The fundamental rule for V8 Sales is:

> **A screen records an activity, but the workflow determines what that
> activity means and what must happen next.**

Therefore:

``` text
DATA
does not drive the process by itself.

EVENT
→ STATE CHANGE
→ NEXT ACTION
→ AUDIT
→ TIMELINE
```

This prevents V8 from becoming a collection of disconnected forms.

The objective is a **closed-loop Sales workflow** where every meaningful
action produces the next appropriate action and every stage has a
controlled business meaning.

------------------------------------------------------------------------

# 36. Implementation Decision

Do not remove the current Prospect Profile, Controlled Sales Stage,
Required Next Action, Contact Activity or Chronological Sales Timeline.

Instead, connect them properly.

The target is:

``` text
PERMANENT PROFILE
        +
CONTROLLED STAGE
        +
CURRENT ACTIVITY
        +
REQUIRED NEXT ACTION
        +
CHRONOLOGICAL TIMELINE
        +
AUDIT
        =
ONE CONTROLLED SALES WORKFLOW
```

The next development task should therefore be an audit of the existing
V8 code for:

**SHOW → CONSULTATION → DECISION → APPLICATION**

before modifying the UI.

This specification should be treated as the functional reference for
that implementation work.
