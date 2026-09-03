"""Process-wide lock serializing all Playwright browser automation.

Playwright's sync API is not safe for concurrent use from multiple threads
within one process — two `sync_playwright()` sessions running at once here
(a double-submitted push racing itself, an Itensity push overlapping a NuPay
push, etc.) corrupts Playwright's internal dispatcher state and raises
errors like `'PlaywrightContextManager' object has no attribute
'_playwright'` on BOTH sides, even though neither push individually did
anything wrong. Every call site that opens a Playwright browser — NuPay
push/status-check, the manual-review NuPay push, Itensity push, Itensity POS
push — must hold this lock for the duration of that browser session.

The manual-review NuPay push can hold this for minutes while a human
reviews the form in the visible browser window; other Playwright-based
pushes simply wait their turn rather than racing and corrupting each other.
"""
import threading

PLAYWRIGHT_LOCK = threading.Lock()
