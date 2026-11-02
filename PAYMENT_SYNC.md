# Daily member payment sync

NuPay payments/mandates and Itensity transactions sync at **05:30 Africa/Johannesburg**.
The scheduler checks every 15 minutes for a missed run, retries failures after an
hour, and recovers abandoned runs after two hours. Each source has a database lease;
imports run inside a SQLite write transaction and take a database backup first.

The default lookback is 120 days. After prolonged downtime the window also covers
the last successful coverage date. Set `PAYMENT_SYNC_LOOKBACK_DAYS` to widen it.
`PAYMENT_SYNC_ENABLED=0` disables these jobs. `SCHEDULER_ENABLED=1` and the existing
NuPay/Itensity credentials are required. The configured `ITENSITY_TENANT_SLUG`
selects the only tenant these shared merchant credentials may update.

Both dashboard revenue and member payment profiles read the imported `collections`
records. The dashboard shows each source's last successful sync, last attempt,
counts and failures separately from the page refresh time. Runs with unmatched or
ambiguous records are marked **needs attention**; valid matched records still post.
Their successful import timestamp advances while the review warning stays visible.
Disputes and reversals require review and are never silently posted as payments.
Existing legacy ledger records and cross-source payments are reconciled before new
rows are inserted. Changed source transactions are held for review rather than
silently replacing a historical amount. Repeated imports add no payments.

NuPay is the authority for debit-order successes and failures. Its mirrored
Itensity debit-order rows are counted as `deferred_to_nupay`, rather than posted
again. Itensity supplies charges, cash/card/EFT payments and discounts. This
preserves the source policy of the reviewed historical payment ledger.

Reports are archived beneath `data/payment_sync/<tenant>/`; these contain private
member information and are excluded from Git. No changes are submitted to either
external payment portal. The CSV inbox remains available as a fallback.

Run immediately from the app directory: `venv/bin/python -m scripts.sync_payments`.
Inspect without importing: `venv/bin/python -m scripts.sync_payments --status`.
Use `--source nupay` or `--source itensity` to run just one source.

The scheduler uses the existing single-worker web deployment. The application must
remain running for scheduled jobs to execute; recovery catches up after downtime.
