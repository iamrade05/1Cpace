# Monthly reporting cycle

All monthly reports, monthly KPI totals and dashboard reporting periods use the
15th through the 14th, inclusive, in South African time. The current cycle on
27 October 2026 is 15 October 2026 through 14 November 2026.

Cycles are identified by their closing month, consistent with the existing
fitness joining cohorts: `2026-11` means 15 October–14 November 2026.

The shared implementation is `onecpase/reporting_cycle.py`. Sales and department
dashboards default to the current cycle through today. All eight dashboards
share Daily, Weekly, Cycle, 3 cycles and 6 cycles selectors, with historical
cycle selection by closing month. Daily compares with the previous day. Weeks
run Monday–Sunday and compare with the previous week. Three/six-cycle blocks
compare with the immediately preceding three/six cycles, without overlap.
The existing twelve-cycle and custom-date options remain available.

Incomplete periods compare the same elapsed number of days in the preceding
period, capped at that period's end. Completed historical periods compare
complete natural periods. Both actual date ranges and day counts are shown.
The shared dashboard resolver is `onecpase/dashboard_reporting.py`; it uses
the cycle boundaries from `onecpase/reporting_cycle.py`.

Reports default to the current cycle. Previous cycle, quarter, year and history
views use complete cycles. Collections report month selection chooses a cycle
by its closing month. New-member, collection, PTP and query history buckets use
the same closing-month convention, including exports sourced from those reports.
Dashboard period selection does not change the existing report/export defaults.

Daily staff targets remain daily, including the 100-call target. Today’s queues,
payment due dates, contract billing and HR leave entitlement anniversaries retain
their existing operational meaning; the reporting change does not alter records.
