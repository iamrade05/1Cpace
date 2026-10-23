# Elev8 fitness onboarding — agreed workflow

Confirmed on 16 October 2026: onboarding lasts **10 calendar days from the first session**, not 10 attended visits. Machine circuit training precedes free weights. The member's first session is day 1; the window ends on day 10 inclusive. Missed days do not extend it.

## Existing implementation and gaps

At the start of this review the module recorded joiner cohorts, manual/turnstile arrivals, instructor interactions, assessments, body measurements and reviews. It used seven arrivals and a thirty-day expiry, without interest/times/allocation, welcome automation or client signatures. Several report figures were placeholders. The implementation now separates attendance, assistance and signatures, and uses the ten-calendar-day policy below.

## One member journey

1. A genuinely joined/activated member enters the fitness intake list. Unverified applications are excluded.
2. Send one welcome from the linked Reception number. Store queued, submitted or failed state and the conversation reference. Do not resend on page refresh. No automatic historical-member campaign.
3. Record onboarding interest as awaiting reply, interested or declined. Keep the initial response and dated changes. Record preferred days/times separately from a confirmed booking.
4. Assign an active instructor after availability is agreed. Keep the assigned instructor and the instructor who actually conducts each session as distinct fields.
5. On the first session, record goals, training experience, relevant screening information, weight, height and baseline body measurements. Use the same baseline in assessments and Body Matrix.
6. Run the ten-calendar-day introductory programme. Record each actual arrival, planned activity, completed assistance, response, instructor and follow-up. Absence is not a completed session.
7. After each assisted session, the client draws a signature on the app and confirms their name. Store signature, date/time, session, instructor and capturing staff member. Signing must not be possible before the session record is complete. Signed records require an amendment trail rather than silent replacement.
8. At the end of day 10, review actual attended and signed sessions, goal progress and assistance needs. Record independent training, further support, classes or optional paid PT interest. No automatic PT billing or enrolment.

## Beginner guide for trainer adaptation

| Calendar stage | Intended focus |
|---|---|
| Day 1 | Welcome, goals, assessment, equipment introduction and individually suitable cardio |
| Day 2 | Review response, technique, supervised introductory functional movements where suitable |
| Days 3–4 | Machine circuit; assess movement control, mobility and suitable resistance |
| Days 5–10 | Progress to suitable free weights, circuits and functional work; build independence |

The owner's proposed day-one options are treadmill up to 20 minutes, elliptical 10–20 minutes and bike 10–20 minutes, with proposed incline 6 and resistance 5. These are proposed machine-specific settings for trainer review, not mandatory prescriptions for every beginner. The combined duration is 40–60 minutes, excluding assessment, breaks and cooldown. The trainer can shorten or replace exercises. Day-two slam balls and dumbbell squat/snatch/press require suitable technique and readiness; simpler alternatives must be available.

Progression should reflect technique, effort, recovery and symptoms, rather than testing pain tolerance. Calendar day does not establish readiness. Baseline energy expenditure is an estimate, not a measurement of calories burned in training. Distinguish estimated resting expenditure, estimated daily energy needs and an agreed nutrition target. Do not default missing sex/activity inputs to an arbitrary formula or apply adult equations to children.

## Welcome message

Hi {first_name}, welcome to Elev8 Health & Fitness! Thank you for trusting us to support your fitness journey. We offer introductory instructor support during your first 10 calendar days, starting with your first session, to help you get comfortable with the gym and your training. Would you like to take part? Please reply with the days and times you expect to attend so we can arrange an instructor. Afterwards, you can train independently or choose optional personal training. We look forward to welcoming you!

## Daily operational view

Show: welcome status; awaiting reply/interested/declined; preferred days/times; instructor allocated/unallocated; first session and end date; present today; assistance pending/completed/not assisted; baseline outstanding; signature pending/completed; next action and owner; final review due/completed. Interested members who attended without assistance must remain visible as exceptions.

## Acceptance checks

- One welcome per new joined member; failures remain visible and retries are deliberate.
- Existing clients are not bulk-messaged when the feature is enabled.
- Replies use the existing Reception conversation and client profile.
- A declined member is not marked assisted or automatically enrolled in PT.
- Day 1 through day 10 is calculated with Johannesburg dates, including month/year boundaries.
- Turnstile entry does not count as an assisted or signed session.
- Every signed session has a valid instructor, client acknowledgement and immutable timestamp.
- Assessments and Body Matrix share baseline values; missing readings remain missing.
- Reports count real records and distinguish arrivals, assisted clients, sessions, signatures and reviews.
- Staff access remains tenant-bound; no client data leaks into another gym.

## Implementation state

Collections messaging is deployed through Reception. The fitness implementation includes the ten-calendar-day rule, interest/times/allocation, a shared assessment/Body Matrix baseline, new-joiner welcome automation, drawn client signatures and populated reports. Reception can open fitness intake from a linked chat to record the client's reply; replies are not automatically interpreted or appointments automatically scheduled. Existing clients are excluded from automatic welcomes and can be invited explicitly. Signed session records are locked; a dedicated amendment editor is outside this release. Final release evidence is recorded in `artifacts/fitness-journey-deploy/` and `artifacts/fitness-journey-live-verification.json` after deployment.

Exercise guidance is deliberately adaptable. See [NIDDK beginner activity guidance](https://www.niddk.nih.gov/health-information/weight-management/tips-get-active/tips-starting-physical-activity) for gradual progression and responding to symptoms.
