# Shared member-query form requirements

Agreed 24 October 2026. Planning requirements; application changes have not yet been implemented. These requirements apply across cancellation, refund, freeze, banking details and proof-of-payment queries.

## Query scope

Remove the proposed standalone missing-verification-documents query from the implementation scope. The user confirmed that application validation must prevent progression when required documents are absent. Handle that through the application's existing document/compliance gates rather than a separate member query type. This planning decision does not remove document correction requests from cancellation, refund, freeze or other active query workflows. No existing code or application document gate has been removed by this plan change.

Also remove payment arrangements as a separate member-query type from the proposed scope. Reception creates arrangements through the existing PTP/Collections workflow. Collections, Call Queue → PTP and direct PTP creation must open the same arrangement form and save through the same underlying process. See `PTP_WORKFLOW_CONSISTENCY_PLAN.md`. This is a planning change, not a claim that existing query categories or routes have been removed in code.

## Tenant branding on every client-facing form

Every form sent to a member must use the originating tenant/gym's name, logo and brand colours consistently. This includes cancellation, refund, freeze, banking details, proof of payment, information updates and complaints, as well as correction, signing, submission-confirmation and expired-link pages belonging to those forms.

Use one shared branded form layout driven by the tenant's configured branding. Resolve branding from the tenant bound to the secure query link, never from a member-editable field or another tenant's browser session. Do not hardcode Elev8 branding for all gyms. Signed-form records/exports should preserve the originating gym identity and branding shown at submission. Maintain readable text and controls when applying tenant colours; a missing logo must not substitute another gym's logo.

Acceptance: forms for two different tenants show their respective configured name, logo and colours; every step stays consistent; a link opened while signed into another tenant still shows the correct originating gym. Shared form content and workflow rules remain unchanged by branding.

## Signed form behaviour

Member query forms must be signed by the member. The proof-of-payment query also requires a short signed form; earlier wording saying that no form/signature is needed is superseded.

Use the agreed compact style: saved name/contact/email for confirmation, one correction line if incorrect, only relevant request fields and uploads, notice acknowledgment and finger/stylus signature with clear/redraw controls. Do not require unrelated information or documents.

Display the shared notice: Submitting this form does not mean that your application has been approved. Applications take 5 to 10 days to be fully processed. You will be notified of the outcome.

## Automatic retention on the member profile

When any member query form returns, the system automatically attaches its signed form, captured signature and every uploaded document to the correct member profile, retaining the link to the original query. Reception must not have to download/re-upload documents or manually copy them between the query and profile.

Retain all other attachments already associated with that query in the profile's query/document history as well. Do not limit this behaviour to the supporting-document types listed in one form plan.

Store a single logical document with member, query and submission/version references so it is accessible in both views without duplicate storage or duplicate entries on retries. Profile access must still respect document-specific staff permissions; visibility on the profile does not make sensitive evidence available to all staff.

Preserve original submissions and attachments when replacements arrive. Label type, source query, submitted time and version/review status. Signed form retention includes the exact field values and declaration/notice version accepted by the member.

The secure query link determines member association server-side; never trust a submitted name or member ID to select a different profile. Retention must succeed before displaying a successful submission. Incomplete processing must remain retryable and must not silently lose attachments.

Apply tenant isolation, protected downloads, upload validation, appropriate encryption and access audit records. Profile retention does not approve a request, change membership, confirm receipt of funds or execute a payment.

## Acceptance

- Each form returns with the member signature and retains all attachments on the correct profile and original query.
- Reception opens the returned form and evidence directly from either view.
- Repeat submissions/retries do not duplicate records or change member association.
- Replacements preserve original evidence and signature/form versions.
- Unauthorised staff and members cannot access another member's documents.
- Submission success is not shown when evidence retention fails.
