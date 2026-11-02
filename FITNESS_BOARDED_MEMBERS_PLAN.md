# 1Cpace Fitness: Boarded Members Implementation Plan

Updated 25 October 2026. The five-item submenu and reviewed-members list have been implemented; the individual profile, payment gate and later build steps remain planned. Builds on the deployed onboarding journey (FITNESS_WORKFLOW.md, FITNESS_WORKFLOW_LOCAL_STATUS.md, FITNESS_ONBOARDING_IMPLEMENTATION_PLAN.md).

The submenu and list were deployed on 25 October 2026 after 67 passing local tests and a copied-database rehearsal (19 page renders, 26 access-denial checks). Production and beta health checks passed. Evidence: `artifacts/fitness-submenu-release/`; rollback backup: `/opt/onecpase-backups/workflow_fitness_submenu_20261025_184207`. Only the navigation template, onboarding route module and new list template were deployed.

## 1. Fitness menu (updated by user 25 October 2026)
1. Dashboard
2. Onboarding
3. Boarded Members
4. Incident Logs
5. Supplements

The fifth item was changed from Nutrition to Supplements at the user's request and deployed on 25 October 2026. Three focused tests and the copied-database rehearsal passed; production and beta are healthy. Evidence: `artifacts/fitness-supplements-menu-release/`. Only `onecpase/templates/base.html` was deployed for this adjustment.

The submenu contains exactly these five items in this order. Instructor Dailies and allocations remain accessible from the Fitness dashboard work queues. Existing assessment, Body Matrix, workout plan, class and supplement pages are accessible from the Boarded Members page. Onboarding owns the first 10 calendar days and the completion review. A member appears in Boarded Members only after the programme is complete and the review is recorded; the outcome remains visible, including further support needs. Classes and bookings inside an individual boarded profile remain planned below.

## 2. Boarded member profile (tab order)
Assessments, Body Matrix, Workout Plan, Nutritional Guide, Classes and Bookings, Tracking.
- Everything captured during onboarding carries over under the same member identity: assessments, baseline measurements, Body Matrix readings, signed sessions, goals, instructor history. Nothing is re-entered or copied; the profile reads the same records.
- Assessments and Body Matrix share one baseline (existing rule). Missing readings stay missing.

## 3. Owing-fitness gate (read only)
- The profile reads the member's payment profile using the existing arrears computation (`compute_arrears_from_profile` in ptp.py). No new arrears, PTP or first-payment rules are built in fitness; whatever the payment profile says is final.
- If the member owes fitness: plan, nutritional guide, bookings and tracking are not provided. The profile shows one message: please speak to reception. Details and past records stay visible, read only.
- No override, workflow or approval in fitness. Reception resolves it through payments; the gate lifts when the payment profile is clear.
- To verify in code: whether the arrears result separates fitness-owing from other balances.

## 4. Library (built on fitness_anatomy.py)
- fitness_anatomy.py is the body-region library (14 regions, one introductory exercise each, clickable body map at /fitness/anatomy). It becomes the source for workout plans.
- Extend it to many exercises per region: name, region, muscles, equipment, level, steps, goal tags, condition flags (swap or exclude), MET value for the calorie engine, source link.
- Library entries are managed content, versioned, tenant-safe.

## 5. Plan templates
- Keyed on goal x level x age band (25-35, 36-45, 46-55, 56-65) x condition band.
- Goals follow onboarding categories: weight loss, toning, muscle building, weight gain, general fitness. General, mixed and unclear goals need clarification first, as already agreed.
- Template: days, exercises from the library, sets, reps, rest, progression rule. The instructor can adjust any template. Progression follows technique, effort and recovery, not calendar day.
- Condition flags come from optional onboarding screening and the contract PAR-Q; they restrict or swap exercises. Medical detail stays restricted to authorised staff and out of logs and previews.

## 6. Calorie engine and two nutrition tiers
- Resting expenditure: Mifflin-St Jeor from sex, age, height, weight. Missing sex or activity input is not defaulted; the engine says what is missing. No adult equations for children.
- Daily needs: job activity + walking (steps or distance) as its own line + planned workouts from library MET values, weight and duration. Workouts and walking are added separately so nothing is double counted.
- Target: deficit for loss (capped at a safe rate) or surplus for gain, from the goal.
- Tier 1, members not on personal training: nutritional plan (calorie and macro targets, how to calculate calories, basic food guidance). Uses the existing nutrition_foods table.
- Tier 2, personal training clients: full meal plan prepared for the person on the same targets.
- Estimates are labelled as estimates. Supplements stay outside both tiers.

## 7. Tracking
- Session logs per exercise (sets, reps, load, time), personal bests, attendance, goal progress from each re-assessment.
- Signed sessions reuse the existing drawn-signature records; they stay locked once signed.

## 8. Signed forms (reuse query_forms.py engine and fitness signoffs)
Fitness consent and POPIA health-data consent; screening declaration; medical clearance where flagged; waiver where training proceeds without clearance; plan acknowledgement (new version = new signing); meal plan sign-off for PT; paid extra request quote acceptance. Each stores signer, time, version, encrypted evidence, tenant binding.
Members without an email need a coach-assisted in-person signing.

## 9. Open decisions
- Where class setup (creating and scheduling classes) lives, now that classes and bookings show inside the boarded profile.
- Who writes plans and meal plans (suggest a fitness_coach permission; sales staff assign templates only).
- Members app (fitness only) scope and timing; extra paid requests and pricing.
- Already open in the onboarding plan: elliptical resistance rule, days 3-10 activities, general/mixed goal routing.
- Live setup still empty: roster, gym hours, approver mapping.

## 10. Build sequence
- B1: boarded profile shell, tabs, carry-over of onboarding data, owing gate.
- B2: library extension and exercise tagging.
- B3: workout plans with exercises and templates.
- B4: calorie engine and the two nutrition tiers.
- B5: tracking and performance.
- B6: fitness signed forms.
- B7: members app.
- B8: chatbot.
Each step: additive migrations, tenant-scoped routes and tests, rehearsed on a copy before any deploy.
