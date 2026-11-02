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

## Dashboard consistency update — 25 October 2026

The user's requirement is that all dashboards follow the same established design. The current colour source is `onecpase/static/css/tokens.css`: navy frame, gold accents, slate/sage/sand supporting colours, light workspace, Barlow/Barlow Condensed typography and shared spacing. The fitness onboarding plan also explicitly requires the main and Collections dashboard design.

The admin, Sales, Reception, Fitness, personal-work, Collections, PTP and My HR dashboards now share the header macro in `onecpase/templates/_dashboard.html`. Department dashboards use the existing `fd-kpi`, `fd-panel` and filter components rather than independent department-card styling. The PTP dashboard's separate stylesheet was removed; its cards, status colours and filters now use the shared tokens and components. Shared responsive rules handle phones, and the main dashboard range controls retain readable contrast within the navy header. Metrics, filters, action handlers and department access rules are retained.

Validation: 49 focused tests passed. Playwright checked eight pages at 1366px, 768px and 390px (24 renders), with no page overflow or JavaScript errors; it also exercised chart range switching and Sales date filtering. All seven changed template files meet the existing style-debt limits. The wider style audit still flags existing changes in `queries/member_form.html` and `queries/workflow.html`, which are outside this release. A server-side copied-database rehearsal rendered 22 pages and verified 26 denied-access cases with no provider actions.

Eight presentation files were deployed; unrelated working changes were excluded. Evidence is in `artifacts/dashboard-design-release/`; rollback backup: `/opt/onecpase-backups/workflow_dashboard_design_20261025_190512`. To provide sufficient deployment space, the database files in our three preceding dashboard/menu release backups were gzip-compressed and SHA-256 verified before their uncompressed copies were removed. Their `database-map.json` files identify the compressed paths and original hashes. Live databases were not compressed.

## Staff sections and merged fitness intake — 25 October 2026

The joining-cohort and intake-follow-up tables on Fitness Onboarding are now one **New member intake** register. Each row retains the member name, joining date, package, support interest/welcome status, preferred times, instructor and assisted/signed session counts. Both the member name and Open intake action use the fitness intake route. Arrivals/session records and completion reviews remain separate. Joining-cycle and date filters remain intact, and selecting an arrival date preserves the chosen joining period. The intake page links back to the register and shows joining/package details; its main-profile shortcut was removed.

The existing shared navy/gold header treatment now applies to staff page headers across sections. Shared table headings use navy with gold accents; panels have consistent headings, spacing and padded content. Allocation, roster and review pages gained standard page headers, and incidents/supplements layouts were corrected for narrow screens. The shared stylesheet URL is versioned so browsers request the updated design. Public tenant-branded request forms remain outside the staff-workspace selectors.

Validation: 58 focused fitness/access checks passed, along with the final template-structure check. Playwright rendered 28 representative staff pages at 1366px, 768px and 390px (84 views), with no document overflow or JavaScript errors, and exercised the intake-name link, chart range controls and Sales date filtering. The changed templates meet the existing style-debt limits. A copied-live-data rehearsal rendered 26 pages and verified 26 denied-access cases without provider actions. All deployed files were compared to the live baseline to exclude unrelated work.

Eleven presentation files were deployed. Evidence: `artifacts/fitness-onboarding-design-release/`; rollback backup: `/opt/onecpase-backups/workflow_fitness_onboarding_design_20261025_195826`. Immediate post-deployment retention succeeded, retaining five rollback sets, and rehearsal copies were cleaned automatically.

## Page consistency and ELEv8 query forms — 28 October 2026

The shared page header now covers the remaining admin, HR, PBX and query screens. Employee detail stacks on narrow screens. Query workflow panels and member-facing request forms use consistent spacing and controls. ELEv8 member forms retain the existing red (`#D51F37`), white and dark text branding and logo, with the installed `elev8.svg` as the fallback when no uploaded logo is configured. All six request types and the submitted/expired states share that header. Form method handling no longer fails when a payment field is named `method`.

Local validation: 53 focused consistency tests passed; 11 query/template checks passed after the ELEv8 branding adjustment. The final browser evidence covers 135 URLs at 1366, 768 and 390px (405 views) with no remaining flagged layout or JavaScript issues. ELEv8 branding, logo loading, correction-field behavior and signature drawing/clearing passed for all six variants at all three widths.

The user authorized deployment. All 20 live presentation files matched the session's before-work baseline, so the release excludes unrelated workspace changes. A server rehearsal compiled 187 installed templates and rendered 27 pages with disposable synthetic records. The same checks passed after installation. Both public hostnames report healthy; the delivered CSS, JavaScript and ELEv8 logo hashes match the installed files. No database or provider actions were included.

Evidence: `artifacts/page-consistency-release-20261028/`. Presentation-only rollback backup: `/opt/onecpase-backups/workflow_page_consistency_20261028_170051`. Existing retention ran successfully and retained five deployment sets. Disposable rehearsal files were removed.
