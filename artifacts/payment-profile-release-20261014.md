# Collections payment profile and completed-mandate cancellations

Deployed to the V8 instance at https://beta.1cpace.co.za on 14 October 2026.

The Collections client brief now has a Payment profile below the payment and
activity cards. It starts collapsed and shows the latest 12 ledger charges,
amounts paid, balances, payment sources, and statuses. An empty profile has an
explicit message. The existing route already supplies the ledger context.

The owner authorised cancelling clients whose fixed-term DebiCheck instalment
count equals the total instalments, with no later successful payment or gym
visit since the final debit date. Perpetual or replacement mandates exclude a
client. Uncertain matches remain unchanged.

The review found 200 completed mandates and 182 recorded final debit dates.
The initial screening produced 48 distinct member candidates. Live Itensity
reports were then retrieved for January through 14 October, with every payment
report page checked against its total count. The Last Visited report was also
retrieved live. Served member identities and payment records were rechecked.
29 members were set to Cancelled; 19 candidates were left unchanged because of
later payments, attendance, or an uncertain attendance identity.

The database operation uses a SQLite backup, one transaction, identity and
payment concurrency checks, an audit record for every change, and post-update
verification. Independent verification confirmed all 29 statuses and audit
entries. The database and report snapshots are private runtime evidence and
are not part of this commit.

Validation: 14 Collections call-queue tests passed; the populated and empty
payment-profile templates render and start collapsed. Deployment checked the
live route's ledger context, parsed the template, backed up the previous
template, restarted the V8 service, verified the deployed file hash, and passed
the public health endpoint. Post-cancellation app health returned HTTP 200.

Template backup: `/opt/onecpase-backups/payment_profile_20261014_153816`.
Database backup:
`/opt/onecpase-backups/completed_mandate_cancellations_20261014_153254/elev8_before.db`.
