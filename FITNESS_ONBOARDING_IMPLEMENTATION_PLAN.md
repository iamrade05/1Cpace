# 1Cpace Fitness Onboarding Implementation Plan

ELEV8 Health & Fitness | Updated 24 October 2026

## Purpose and implementation status

Implement a ten-day onboarding workflow connecting membership, WhatsApp, member availability, automatic instructor allocation, training records and completion review. This document records the agreed requirements. Application implementation and testing have not started because the development environment is disconnected. Use the current 1Cpace project source, supplied as 1Cpace-2026-10-24.zip, rather than the older V8 archive. Do not execute staff setup scripts merely to inspect the source.

## 1. Fitness navigation

Main menu: Dashboard; New Member Onboarding; Boarded Members; Classes; Instructor Dailies; Log Incident; Supplements.

Each Boarded Member profile: Assessments (body and fitness), Body Matrix, Workout Plans, Nutritional Guide, Tracking—in that order.

Use the same visual design as the main and Collections dashboards: layout, fonts, colours, cards, filters, tables and buttons.

## 2. Member entry and immediate welcome

When a member first appears in New Member Onboarding, create one onboarding record and immediately queue their welcome WhatsApp. Compliance and DebiCheck must be tracked independently and MUST NOT delay this welcome.

Use a background messaging job so a provider outage does not block member creation. Store message attempts, delivery state, provider message IDs and failure reasons. Retry failed sends safely, using a unique welcome key per onboarding record to prevent duplicates. A scheduled reconciliation should detect members missing onboarding records or unsent welcomes.

The welcome consists of an Elev8-branded banner, welcoming fitness image, short message and secure form link or supported button labelled “Complete My Fitness Form”. Prepare male, female and neutral banner options; use neutral where preference is unknown. Banner design and image generation remain deliverables, not completed assets.

Suggested wording: Welcome to Elev8 Health & Fitness! Let’s get your fitness journey started. Complete your short onboarding form so we can arrange your introduction and training support.

## 3. Secure onboarding form

Capture training experience (never trained, trained before, currently training); when the member last trained; current fitness level (beginner, intermediate, advanced, unsure); fitness goal; preferred training days; preferred time ranges; and “Do you need personal training?” (yes, no, more information).

Goals include weight loss, toning, muscle building, weight gain, general fitness and other. Medical conditions and medication are optional, clearly labelled and skippable. Do not block submission when these fields are blank. Restrict disclosed medical details to authorised staff; do not expose them in notification previews or application logs.

Use an unguessable, expiring member-specific token. Do not put personal or medical information in URLs. Validate submissions server-side and preserve existing member identity. Form completion, rather than a WhatsApp reply alone, triggers allocation. Link incoming WhatsApp messages to the member’s conversation for assistance.

## 4. Three-day reminder

Send one automatic reminder three days after the initial welcome is successfully sent if the form remains incomplete. Recheck completion and contact preferences immediately before dispatch. Prevent duplicate reminder jobs. Form submission cancels pending reminders. Delivery failures remain visible for follow-up. Additional repeat reminders have not been agreed.

## 5. Roster and automatic weekly shift rotation

Use Africa/Johannesburg for all dates, shifts and reminder scheduling. Anchor the rotation on Monday, 26 October 2026.

Week starting 26 October: Mvuleni 05:00–13:00; Lindelani 13:00–21:00; Kamohelo Thabo Tsotetsi 16:00–20:00.

Week starting 2 November: Lindelani 05:00–13:00; Mvuleni 13:00–21:00; Kamohelo 16:00–20:00.

Continue swapping Mvuleni and Lindelani every week automatically. Kamohelo’s support shift stays fixed. Calculate the week from the anchor date so restarts and regenerated schedules do not reset the rotation.

Apply current gym opening hours, leave, class commitments and existing appointments. Date-specific exceptions must not change the underlying weekly pattern. Group instructors must appear on the roster with client-assistance availability and class commitments.

Fitness instructors: Lindelani, Mvuleni and Kamohelo Thabo Tsotetsi. Update Kamohelo’s job classification from Sales to Fitness Instructor, preserving his identity and history. Review the sales-rotation flag separately as part of that role change.

Group instructors identified in the current source: Louisa, Mduduzi, Melizwe Dlamini, Sibusiso Mabaso and Crosby Machate. Verify active staff records before configuring production. Melizwe and Sibusiso are excluded from client allocation ONLY: do not suspend their accounts or remove their classes.

## 6. Fully automatic instructor allocation

Every eligible instructor added to the roster automatically joins the allocation queue. Staff do not manually select an instructor for a member. Weight-loss and toning clients rotate across eligible fitness and group instructors. Muscle-building and weight-gain clients rotate among fitness instructors only. Define handling for general-fitness, mixed and unclear goals before enabling those cases; request clarification rather than silently assigning an unsuitable queue.

Match the member’s preferred appointment availability against the roster. Use persistent round-robin ordering among eligible available instructors, without weighting by shift length. Exclude leave, conflicting appointments, classes and allocation-disabled staff.

Group instructors receive an availability offer. Acceptance reserves the allocation; decline passes it to the next eligible instructor automatically. An unanswered offer advances after a configurable deadline, whose value is still to be agreed. Expired offers cannot later claim a member already allocated elsewhere.

Use a transaction or atomic reservation so two instructors cannot accept the same allocation. Track offers, responses and timestamps. If no instructor is available, retain “Awaiting allocation” and show the case on the dashboard; do not assign unavailable staff. Confirm the appointment with the member after instructor availability is established.

## 7. Ten-day programme and first sessions

The programme lasts ten calendar days, beginning on the joining date or following day. The default start-day rule is unresolved and must be configured explicitly before production. Store start/end dates and track attended training sessions separately. Do not treat the programme as ten completed visits. Apply the introductory routine to the first and second attended training sessions.

Treadmill: 20 minutes; proposed incline 6–10 and speed 4–6. Record machine units.

Elliptical: 20 minutes; actual resistance recorded. Resistance 2 and 4 were discussed, but no fixed starting/progression rule was approved.

Bicycle or recumbent bike: 20 minutes at proposed resistance 4.

Optional light bodyweight movements and slam-ball work may be included within the remaining time. Total session duration: 60–90 minutes including transitions and rest. The instructor adjusts settings and activity to the assessment and member’s response; do not force a fixed intensity.

Record goals, training experience, relevant disclosed limitations, and body/fitness assessments before the first workout. The remaining programme activities require definition; do not invent a fixed days 3–10 exercise template.

## 8. Session records, handovers and PT priority

Record attendance, actual session instructor, times, exercises, durations, machine settings, member response, difficulties and next-session instructions. Gym entry alone is not proof of a completed session. Missed, cancelled and rescheduled appointments remain distinct.

Keep primary onboarding instructor separate from the instructor assisting each session. When Mvuleni and Lindelani swap shifts, the instructor on duty continues the shared programme using prior notes. The handover must not restart onboarding or overwrite previous work.

If the member requests personal training, the primary onboarding instructor has the first opportunity. A covering session instructor does not automatically take that priority. Record instructor acceptance/decline and the member’s agreement. PT interest must not create charges or paid bookings automatically.

## 9. Completion review and boarded profile

At the end of the ten-day window, create “Review due”; elapsed time alone must not mark onboarding successfully completed. Record attendance, assistance, assessment results, confidence using equipment, programme/nutrition guidance, PT outcome, outstanding support and next review date.

Move the member to Boarded Members only after the completion review. Keep members needing additional assistance visible with a next action. Preserve all assessments, measurements, programmes, messages, offers and session history under the same member identity.

## 10. Dashboard and proposed implementation records

Dashboard views: new members; welcome delivery failures; incomplete forms; three-day reminders; pending instructor offers; unallocated members; today’s roster and appointments; ten-day progress; missed appointments; reviews due; and compliance/DebiCheck exceptions. Show responsible instructor, next action and due time without exposing optional medical details.

Inspect current schema before adding migrations. Reuse existing onboarding, staff, assessments and messaging entities where suitable. Proposed logical records: onboarding lifecycle; secure form submission; roster rules and exceptions; allocation eligibility/queue; offers and reservations; appointments; sessions; completion reviews; message outbox and audit events.

Keep data scoped to the member’s tenant in reads, writes, jobs and webhooks. Use permissions for roster administration, fitness records and restricted medical information. Audit status changes, shift exceptions and queue eligibility without logging sensitive form contents.

## 11. Build sequence and acceptance checks

Build 1: inspect current source, data model, WhatsApp integration and shared dashboard components; prepare additive migrations.

Build 2: onboarding creation, ten-day dates, staff eligibility and repeating roster.

Build 3: secure form, banner assets, immediate welcome outbox and three-day reminder.

Build 4: automatic allocation, availability offers, timeout processing and appointment confirmation.

Build 5: assessments, session tracking, shift handovers and PT-priority records.

Build 6: completion review, Boarded Members navigation and dashboard.

Verify: welcome is sent without compliance/DebiCheck gating; repeated join events do not duplicate messages or onboarding; completed forms suppress reminders; weekly shifts swap correctly across month/year boundaries; leave/classes prevent conflicts; Melizwe/Sibusiso remain account-active but allocation-excluded; duplicate/late acceptances are rejected; no-instructor cases remain queued; optional medical fields can be skipped; tenant access is isolated; handovers preserve history/PT priority; expiry creates a review rather than automatic successful boarding.

Run appropriate existing tests plus targeted workflow tests. Use a controlled test recipient/provider mode for WhatsApp validation before enabling production sends. Existing members must not receive a mass welcome from a migration unless separately authorised.

## 12. Decisions still required

Confirm joining-day versus following-day programme start; instructor availability-response deadline; elliptical resistance starting/progression rule; remaining training activities; and routing for general-fitness/mixed/unclear goals. These settings must remain visibly unresolved in the implementation until agreed. They must not prevent preparation of the rest of the workflow.

