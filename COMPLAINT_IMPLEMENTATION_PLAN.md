# Complaint implementation plan

Planning record, 24 October 2026. Application code has not been changed.

## Agreed routing

Every complaint goes to management. Reception logs the member-linked query and issues the short signed form. Returned complaints route to the configured management queue rather than being resolved by reception as routine cases. The specific manager/account assignment remains to be configured.

## Short member form

- Show saved name, contact number and email for confirmation, with a correction line if incorrect.
- Ask briefly what happened.
- Allow supporting attachments; do not make unspecified evidence mandatory.
- Include the shared no-guaranteed-approval and 5–10-day processing notice.
- Require acknowledgment and the member's finger/stylus signature, with clear/redraw controls.

Follow `MEMBER_QUERY_SHARED_REQUIREMENTS.md`: automatically retain the signed form, signature and every attachment on the member profile and original query, with version history and protected access.

## Visible workflow

1. Reception logs the complaint query and sends the form.
2. Awaiting member submission.
3. Signed submission received and assigned to management.
4. Management investigates; requests clarification when needed.
5. Management records findings, action taken and member response/outcome.
6. Outcome communicated and complaint closed with resolution notes.

Show current stage, management owner and next action. Reuse existing query states, notes, follow-ups and notifications; preserve original complaints and subsequent responses. Document missing evidence without preventing management from seeing a submitted complaint.

## Implementation and checks

1. Configure mandatory management routing for complaint queries.
2. Build the compact signed form and secure attachment upload.
3. Add management review, clarification requests, resolution and communication history.
4. Connect profile retention, permissions and audit history.
5. Test that every returned complaint reaches management, repeat submissions do not duplicate cases, evidence remains linked to the correct member/tenant, and closure requires a recorded outcome.

Do not introduce a separate reception approval gate. Clarify the shared processing-clock interpretation before enabling automatic deadlines. Document access should respect confidentiality and authorised roles rather than granting unrestricted visibility just because evidence appears on a profile.
