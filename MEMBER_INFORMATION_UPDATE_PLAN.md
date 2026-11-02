# Member information update plan

Planning record, 24 October 2026. Application changes have not been implemented.

## Agreed layout

Reception opens a query linked to the member and selects the area to update. Provide only these five section headings; reveal fields for the selected section rather than displaying the whole member profile:

| Section | Relevant fields |
| --- | --- |
| Personal details | Name and applicable personal fields already supported by the member model. |
| Address | Member address with address search. |
| Contact | Email address and contact number. |
| Emergency contact | Emergency-contact details, including a valid contact number. |
| Work details | Company/employment details, with appropriate company/address search. |

Only expose relevant existing fields within these headings; do not invent additional required personal information. Banking changes remain in their separate DebiCheck workflow.

## In-person path

If the member is at the gym, reception logs/opens the query, selects the relevant section, enters the changes and submits. Apply validated changes to the linked member profile and retain a before/after audit record with staff identity, query reference and time. No separate manager approval was requested for this path.

Keep the existing shared signature and automatic profile-retention requirements. The precise in-person signing interaction has not yet been defined; do not silently remove signatures or invent a manager gate.

## Remote path: details still to finalise

The user distinguished members at the gym from remote members but has not yet finished defining the remote handoff. Use the established secure signed-form foundation and selectable sections when drafting this path. Confirm whether a returned remote form applies valid changes directly or returns to reception for checking before application. Do not silently choose either behaviour as agreed policy.

## Validation and updates

- Names: reject digits; allow legitimate Unicode letters, spaces, apostrophes and hyphens. Do not use a restrictive English-only alphabet rule.
- Email: validate email syntax on the server as well as in the browser. Syntax validation does not prove inbox ownership.
- Contact numbers: validate and normalise the number using appropriate country rules; accept familiar local/international formatting and do not infer ownership from syntax alone.
- Emergency contacts: apply the same name and telephone checks, and relevant email/address validation wherever those fields exist.
- Address: offer address search and let the user review the selected result. Apply equivalent search/validation to address fields in emergency/work sections where available.
- Company details: provide relevant company search and validate each field according to its type. Do not reject numbers in a legitimate company name or company registration field merely because person names reject digits.

Search-provider selection, supported countries and handling of unlisted addresses/companies remain implementation choices to resolve. Do not claim a provider is already connected or that selecting a result verifies identity.

Update only fields deliberately edited in the selected section. Hidden, omitted or unchanged fields must not be overwritten with blanks. Treat clearing a value as an explicit edit; reject clearing required fields. Use an allowlist of editable profile fields rather than applying arbitrary submitted keys. Member identity, financial status, permissions and mandate records are not editable through this form.

If records changed after the form was opened, show a conflict rather than silently overwriting newer information. Reject invalid edits without partial changes or success messages, preserve entered values for correction, and audit successful edits with appropriate protection for personal data.

## Signature, profile documents and history

Follow `MEMBER_QUERY_SHARED_REQUIREMENTS.md`: member signature on query forms; signed forms, signatures and every attachment automatically retained on the member profile and original query; original versions preserved. No additional supporting-document requirement has been specified for information updates.

For remote member forms, show existing name/contact/email for confirmation, using only the selected section's edit fields. Include the shared processing/no-guaranteed-approval notice. Store both the originally displayed values and submitted changes with the signed version.

## Implementation order

1. Map existing member fields into the five headings and define the editable-field allowlist.
2. Build section selection and conditional field display within the member-linked query.
3. Implement shared server/browser field checks and address/company search.
4. Implement direct reception updates with safe patching, conflict checks and audit history.
5. Finalise and implement the remote signed-form handoff and in-person signing interaction.
6. Connect automatic signed-document retention and query outcomes.
7. Test field validation, partial updates, conflicts, permissions, tenant isolation and desktop/mobile usability.

## Acceptance checks

- Selecting a heading displays only that section's fields.
- Name fields reject digits without rejecting legitimate names.
- Invalid emails/contact numbers cannot be saved through browser or direct requests.
- Emergency/work fields use the same applicable field checks and search behaviour.
- Edits update only the intended linked member and deliberately changed fields.
- Hidden values remain unchanged; concurrent changes are not silently overwritten.
- Successful changes and retained signed evidence are traceable from profile and query.
- At-gym reception updates do not gain an invented manager approval step.
- Remote application behaviour is agreed before activation.
