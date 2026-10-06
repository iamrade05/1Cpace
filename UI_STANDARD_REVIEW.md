# UI standard review and implementation

Reviewed 6 October 2026 against the supplied `UI_STANDARD_FIX_PLAN.md` and `1Cpace Member Profile.md`.

The user's request to review, compare, and implement authorises the local work. The documents are design references. Their audit figures, approval language, and proposed PR/deployment steps are not independent authorisation or verified facts about this checkout.

## Findings and decisions

The audit correctly identifies the primary defect: a dark stylesheet loaded after light templates. The profile already has functioning payment, NuPay, query, mandate, document, and collections actions. Replacing it with the example text would discard working features; the implementation retains those actions and data sources.

The supplied profile is a text outline, with example names and amounts. The referenced Design System artifact and `components/bundle.css` were not supplied or located. The new tokens and components therefore derive from the existing navy/gold brand and the outline; they are not a verified copy of that artifact.

Defaults used: **1Cpace**, a light working area with navy navigation, and removal of the duplicate Collections tabs. Existing package names, asset filenames, email address domains, stored records, and integration source identifiers remain stable.

## Changes delivered

- One shared `tokens.css` / `components.css` foundation; the dark `theme.css`, base style blocks, and dashboard style partial are removed.
- Barlow, Barlow Condensed, and IBM Plex Mono load through the shared layouts, with local fallback fonts. The layouts declare `en-ZA`.
- The top bar provides member search, the signed-in user, and permission-gated Follow-ups. The covering floating button and repeated top-bar title are removed.
- Shared Jinja macros cover headers, panels, key/value facts, KPIs, badges, fields, callouts, and empty states.
- The member profile shows account status and action guidance, personal/debit facts side by side, visible NuPay instalment history, secondary tools on demand, and activity/notes. Missing NuPay history has an explicit empty state. Header actions and all existing permission/phase checks are retained.
- Member list, add, edit, and custom-package templates have **zero inline style attributes, style blocks, hex colours, and emoji**. Form tabs, conditional payer/custom-package fields, validation, address search, and SA ID handling remain functional.
- Shared public layout covers sign-in, OTP, password change, platform pages, welcome, enquiries, event registration/tickets, and contract-share response pages. Event banner delivery, step visibility, social metadata, forms, and scripts are preserved.
- The three dashboards use neutral shared surfaces and typography. The main dashboard chart palette reads CSS tokens; further chart-palette cleanup remains.
- Staff navigation uses line SVG icons; emoji are removed from staff and ordinary public templates. Legacy badge names map to five meanings.
- Tables gain the shared class and scroll container, with common numeric alignment where amounts are directly identifiable.
- Destructive delete/archive/deactivate forms move into expandable action menus and use on-page confirmation. Staff browser pop-ups are replaced with page messages/confirmations; the two contract-signature alerts remain.
- Access-report errors are plain language, with exception details in server logs. WhatsApp setup guidance no longer exposes the raw setting name.
- Displayed branding and email/fallback names use 1Cpace.

## Comparison with the profile outline

| Reference | Result |
|---|---|
| Navy sidebar, light workspace | Implemented, with existing navigation gates preserved |
| Member name and external references | Single profile header, monospace references |
| Package, amount, debit day | Summary uses actual member fields, with explicit missing values |
| Account status and next step visible | Callout at the top, using existing reconciliation guidance where available |
| Personal and debit order side by side | Two panels on desktop, stacked on phones |
| Contract month by month, from NuPay | Existing uploaded transactions shown with instalment number, amount, result, and source; missing future months are not invented |
| Open what you need | Secondary actions grouped in an expandable area; existing panels still open on demand |
| Activity and notes | Remains visible, with existing note submission |

## Remaining migration work

| Audit issue | Status in this change |
|---|---|
| UI-01 competing stylesheets | Shared foundation replaced; 16 legacy page style blocks still require migration |
| UI-02 unreadable text | Light workspace fixes the root mismatch; individual page review remains |
| UI-03 white boxes on dark pages | Root mismatch removed; old card markup remains on unmigrated pages |
| UI-04 brand spelling | Template/display/email defaults corrected; historical data identifiers retained |
| UI-05 page titles | Repeated top-bar title removed and missing main headers added; page-by-page title polish remains |
| UI-06 dashboards | Three dashboard themes replaced with neutral components; residual chart/inline palette cleanup remains |
| UI-07 KPI designs | Shared neutral KPI styles and member strips implemented; legacy inline KPI borders remain elsewhere |
| UI-08 emoji | Staff/public templates cleaned, sidebar line icons added; printable contract exception remains |
| UI-09 arbitrary colour | Member and shared counts neutralised; leads/fitness/incident page migration remains |
| UI-10 buttons | Shared button styles, action menus, and confirmations implemented; other primary-action grouping remains |
| UI-11 badges/filter pills | Legacy badges mapped and member chips migrated; other filter chips remain |
| UI-12 duplicate Collections tabs | Removed from all five identified pages |
| UI-13 forms | Member forms migrated; shared fields/layout supplied; other forms and reception-PC locale verification remain |
| UI-14 tables | Shared class/container applied outside printable contracts; some inline cell styling and currency formatting remain |
| UI-15 technical errors | Access errors logged server-side; plain-language access/WhatsApp messages supplied |
| UI-16 Follow-ups covering content | Moved to top bar, retaining permission and phase gates |
| UI-17 unloaded fonts | Font families loaded once per shared document layout, with fallbacks |
| UI-18 independent layouts | Auth/platform/public/share pages migrated; printable/signable contract remains independent |
| UI-19 member profile | Restructured around the outline, with source data/actions retained and zero styling debt |
| UI-20 browser pop-ups | Staff pop-ups replaced; two contract-signature alerts remain |

This is a foundation and member-area migration with shared improvements across the app, **not completion of every page in the supplied multi-phase plan**.

The source scan currently measures 136 templates with **2,133 inline-style occurrences, 16 style blocks, 1,488 hex-colour occurrences, and 7 emoji/code-point occurrences** remaining. Hex occurrences are not the document's distinct-colour metric. The remaining emoji and signature alerts are in the printable/signable contract template, whose custom accent and print layout remain intact.

Collections, PTP/legal, leads/applications, queries, DebiCheck, communications, fitness, HR, operations, reports, and admin still need page-specific component migration. Shared fixes improve their foundation but do not certify their full page checklist. Existing filters, coloured counts, form layouts, per-page styles, and chart palettes must be reviewed individually. Native date-field presentation also depends on the reception PCs' browser/OS locale.

The per-template tracker is `tests/template_ui_baseline.json`. Tests reject any increase and require new templates to start at zero. `scripts/audit_ui.py --ratchet-baseline` can only lower limits.

## Validation

- The full suite ran: **761 passed, 2 failed**. Both failures were defects introduced during migration (WhatsApp header syntax and event banner omission), fixed and included in the final focused rerun: **53 passed**.
- Additional focused checks: **38 passed** for template structure, NuPay rendering, contract-share verification, and SA ID handling; event-banner/structure checks: **3 passed**.
- Final brand/dashboard checks: **27 passed** (template structure, authentication, platform power users, dashboard, sales KPIs, and collections workspace).
- Playwright checked **11 screens** (member profile/list/add/edit, all three dashboards, welcome, login, OTP, and event registration) at **1366px and 390px**. All returned 200, had no document-level horizontal overflow, and produced no JavaScript errors. The event step test confirms only the selected step is visible.
- The browser confirmation test verifies Cancel sends no request and Confirm sends exactly one request with the selected submit button's action intact.
- Screenshots and measured results are in `artifacts/ui-review/`. `scripts/verify_ui.py` uses disposable databases and synthetic example records.

The user authorised commit and deployment on 6 October 2026. The release includes the UI changes and the existing query/reconciliation application dependencies used by these templates. Scratch files, local databases, credentials, and operational data are excluded.
