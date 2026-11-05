# Stabilization following the section audit — 2 November 2026

Scope: local fixes for access revocation, activation recovery, tenant boundaries, and financial calculations. Existing unrelated working-tree changes were preserved. No deployment or live provider operation is part of this work.

## Access revocation

- Protected browser requests reload the current account, active state, role, department access profile, permissions, consultant restriction, and password-change requirement before authorization.
- Missing/disabled accounts and invalid Power User identities lose their sessions. Demotion and permission removal do not depend on visiting a dashboard.
- Password changes/resets and account enable/disable changes increment an additive `users.session_version`. Old version-zero cookies remain compatible through upgrade, but a subsequent reset/state change revokes them. The current browser can remain signed in after its own verified password change; other sessions expire.
- Native staff session fingerprints include the same revocation version.
- Pending web sign-in/reset challenges carry that version too, so a pre-reset code cannot issue new authority. Power User sessions and tenant access are bound to the current platform password hash. Existing native and Power User sessions will require sign-in after upgrading their fingerprint format.
- Tenant active state is checked on each request rather than behind the previous 90-second cache.
- Test discovery is restricted to `tests/`, preventing archived test copies and logs from entering the release gate.

## Activation recovery

- The confirmed external member reference is persisted separately before local activation starts.
- Local activation, member state, Sales stage, and compliance updates share a rollback boundary. An error leaves `reconcile_required`, preserving the external reference without committing a partial local activation.
- Recovery reuses the confirmed reference and performs local activation only. It never reissues an external create/update.
- Unconfirmed external outcomes become `push_unknown` and cannot be submitted again until an administrator records the checked outcome and supporting notes.
- Interrupted `pushing` records can be reviewed after 30 minutes if no worker for that tenant/application is running. The existing single-worker Gunicorn requirement remains; this is recoverable operation state, not a distributed queue.
- Existing `active`/`push_failed` inconsistencies can be explicitly reviewed and reconciled without recreating the external member.
- The requesting account and evidence gates are checked before external submission and again before local activation. Revocation during submission leaves the confirmed reference available for authorized recovery.
- Known references cannot be replaced during review or assigned to another member. Recovery respects the current evidence gates and explicit admin override policy.

## Tenant boundaries

- Elev8 SQL roster totals, membership-report attendance, profile attendance, and shared roster sync require the explicitly bound configured tenant. Other gyms keep their own local data.
- Attendance cache reads are checked after tenant eligibility and are keyed by registry/tenant.
- Shared Itensity drivers require their configured tenant. Background member/POS work derives that tenant from the registered database owner, preventing fallback to the default gym or its staff identities.
- Member reconciliation preserves email punctuation, normalizes ordinary SA phone variants, rejects contradictory IDs/references, and requires unique one-to-one matches. Names without a date of birth do not establish identity.

## Financial calculations

- A shared decimal-based reporting module distinguishes actual successful cash receipts from discount credits.
- Finance reports, operational collection reports, register totals, member/profile totals, and payment statistics exclude credits and unconfirmed payment rows from cash.
- Discounts still reduce unsettled charges. Net period movement is calculated separately per member; one member's overpayment cannot hide another member's debt.
- Period/cycle net movement is labeled separately from current reconciled account arrears, which can include transactions outside the selected period.
- Registration-fee completion and refund evidence cannot treat a discount credit as an actual collected payment.
- Mandate readiness uses the common approved-status normalizer. Failed/pending mandates are not shown as approved.
- Offset-bearing dashboard timestamps are converted to Johannesburg time before period comparisons.

## Validation record

- Initial access batch: 46 passed; one old Power User test expected a bare 403 rather than session expiry. It was updated to verify redirect plus removal of tenant identity.
- Core regressions and platform checks: 25 passed.
- Expanded core regressions: 37 passed. Later integration-boundary and legacy-recovery additions were included in the completed full collection below.
- Subsequent revocation/Power User check: 20 passed. Final fixture/access/finance check: 22 passed.
- Test fixtures now persist their intended accounts, roles, and grants instead of relying on invented cookie authority. Ordinary tests clone an isolated migrated baseline; tests creating an app themselves retain real startup/migration execution.
- An initial reporting/mobile batch had 50 passes and three failures caused by nonexistent-account test fixtures. Those fixtures were corrected to create actual authorized users.
- Final verified result: **1,408 passed, 1 skipped, zero unresolved failures across all 1,409 collected tests**. The full run initially had 30 stale-fixture failures. Whole-module reruns after fixture corrections cover those failures; the application source was unchanged between the full run and reruns. `artifacts/stabilization-suite-20261102/verified-summary.json` verifies identical test identities and full coverage rather than adding rerun counts to the original total. The original batch logs retain their initial failures.
- Corrected-module rerun: 135 passed and two remaining fixture failures; the final whole reconciliation module passed all 14. The separate parser module passed all 20.
- Final browser check: six successful desktop/mobile renders of unknown-outcome review, local recovery, and finance; all HTTP 200, no JavaScript errors or horizontal document overflow. Screenshots and measurements are in `artifacts/stabilization-ui-20261102/`. The initial memory-limited attempt was superseded by this completed retry. External fonts/CDNs were blocked for this disposable preview.
- Python compilation and `git diff --check` passed after the final changes.

## Upgrade and operating limits

The new columns are additive startup migrations. Upgrade each tenant database through the normal initialization path. Native staff and Power User sessions require a fresh login after their fingerprint format changes. Test data and browser previews were disposable; no deployment, live member creation, or provider acceptance test was performed.

Unknown external outcomes require an administrator to check the actual provider record and record evidence before retry or recovery. Automatic activation recovery remains subject to the documented single-process worker model. Period finance reporting does not claim to reconstruct all-history account arrears.

The remaining audit findings about Fitness/Operations validation, financial hard deletion, large-list loading, training/ownership coverage, and physical device/provider acceptance are not marked fixed by this stabilization work.
