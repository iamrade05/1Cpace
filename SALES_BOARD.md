# Consultant sales board

The Sales dashboard now opens with the consultant board and the existing daily
staff goals. The board day controls both. Period comparison, credited application
links, funnel analysis and exception queues remain available in expandable panels;
their reporting filters are independent of the board day.

The board follows the established 15th–14th reporting cycle containing the board
day. Active Sales staff appear even if they have no leads or credited sales.
Managers are excluded from consultant rows, consistent with daily staff goals.
Managers in Sales and administrators may save each consultant's cycle unit target
and a separate dated unit commitment. Blank clears a setting; zero is explicit.
Targets and commitments are stored in additive tenant-scoped tables at startup,
with the last editing user's ID. Staff can read the board but cannot edit targets.
No photograph figures or names are inserted into operational records.

Actuals reuse first compliance-complete sales credit and the original credited
consultant. South African dates define the cycle-to-date and selected-day counts.
Current compliance blockers continue to exclude sales. Package breakdowns retain
the exact names on credited applications; BT and other photograph shorthand are
not assigned guessed meanings.

Planning days are Monday–Saturday, including holidays. Elapsed includes the board
day; remaining begins the following day. These are planning estimates, not
attendance or individual roster counts. Projection is sales / elapsed × total
planning days, shown to one decimal. Required daily pace is max(target − sales, 0)
/ remaining days, rounded upward to one decimal. Missing denominators or targets
show a dash. Achievement bands use exact ratios: Target 1 at 125%, Target 2 at
110%, Target 3 at 100%. Sales credit on this board remains independent of daily
activity targets and commitments.

The daily activity section now shows Calls made, Connected calls, Referrals,
Appointments and Shows. Connections come from selected CRM call outcomes, excluding
No response and legacy non-response outcomes; they require no PBX duration. Calls
made and Shows display actual counts until their numeric daily goals are specified.
The existing 30 connections, 30 referrals and 10 appointments goals are retained.
See `STAFF_DAILY_KPI.md` for attribution and attendance deduplication rules.

Validation commands:

```text
python -m pytest -q tests/test_sales_board.py tests/test_staff_daily_kpi.py tests/test_department_dashboards.py tests/test_dashboard_reporting.py tests/test_sales_kpi.py
python -m scripts.verify_sales_board
```

The browser check creates disposable synthetic data, exercises saving and day
selection, and records desktop/tablet/phone screenshots under
`artifacts/sales-board-review`. It does not use the operational databases.
