# Shared PTP workflow consistency plan

Agreed 24 October 2026. Planning only; application code has not been changed.

## Requirement

Reception must be able to create a PTP directly from Collections or PTP without visiting Member Queries. Do not build a separate payment-arrangement query/form duplicating this process.

These entry points must open the same PTP arrangement experience:

- Collections → create PTP.
- Call Queue → select PTP.
- PTP → create arrangement.

Use the same field definitions, defaults, calculations, validation, approval requirements, save operation and resulting PTP record. Entry points may preselect the member and record their source, but must not use divergent business rules or fields that fail to create a working arrangement.

**Navigation clarification:** all entry actions must land directly on one canonical PTP arrangement page: the same page used by Call Queue → PTP. Collections → Create PTP and PTP → Create arrangement must open that page immediately with the member context already supplied. Do not send staff to the general Call Queue or PTP listing and require another PTP click. Separate pages that merely share a form are insufficient. Reusing the Call Queue's PTP page does not require a collection-origin request to create a call record or start a call.

## Implementation

1. Inspect the three entry points and the existing PTP creation path. Identify duplicate templates, browser logic and server handlers before selecting the canonical implementation.
2. Route every creation entry action directly to the single canonical PTP arrangement page used by Call Queue → PTP. Reuse its form/service, pass member/source context securely and preserve appropriate return navigation. Do not add an intermediate page or extra PTP click.
3. Validate the member and account on the server. Save one PTP agreement using the same process and display clear success/failure feedback everywhere.
4. Link the saved agreement to the originating collection/call activity where applicable so staff can find the actual arrangement. An activity note alone is not a created PTP.
5. Show the same agreement and status in direct PTP, Collections and the relevant Call Queue context. Avoid duplicate agreements on retries/double-clicks.
6. Apply existing staff permissions and financial controls consistently. Reception should use the authorised PTP functionality without an invented Member Queries gate.
7. Remove or retire redundant arrangement UI paths only after mapping their dependencies. Do not delete historical query/PTP records or unrelated collection workflows.

## Financial policy scope

The user requested the same PTP format and functioning arrangement process across entry points. This does not by itself apply cancellation discount bands to ordinary arrears arrangements. Preserve existing approved collections policy until any separate financial rule changes are expressly defined. Cancellation settlements that use PTP should link to the same agreement machinery without duplicating debt, while retaining their distinct approved quote terms.

## Acceptance

- The same member and inputs produce the same fields, calculations, validation and saved agreement through all three entry points.
- All three entry actions open the same canonical arrangement page immediately, with the selected member; no intermediate listing, second PTP click, duplicate creation page or unnecessary call step is introduced.
- Call Queue → PTP creates a real PTP agreement, not only a note or a non-functional panel.
- Collections and direct PTP creation work without opening a member query first.
- Errors retain entered values and do not falsely report a successful arrangement.
- A successful save returns a traceable agreement visible from all relevant screens.
- Permissions, approvals and account updates behave consistently.
- Repeated submissions do not duplicate the arrangement or debt.
- Existing agreements/history are retained and unrelated collection actions are unaffected.
