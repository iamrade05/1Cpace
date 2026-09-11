"""Gunicorn settings for 1Cpace V8.

Entry point is ``run:app`` — run.py builds the app through create_app().

WORKERS IS DELIBERATELY 1. onecpase/scheduler.py runs APScheduler inside the
application process, and its only guard against double-starting is a
WERKZEUG_RUN_MAIN check, which applies to the Flask dev-server reloader and
does nothing under gunicorn. With two workers the nightly Itensity import,
export pull and transaction import each fire twice, against the same tenant
databases. Raising this number without first moving the scheduler out of the
web process will silently duplicate every scheduled job.

To scale beyond one worker later: set SCHEDULER_ENABLED=0 here and run the
scheduler as its own single-instance systemd service.
"""

import os

# Beta runs alongside production on the same host; production holds 5000.
bind = f"0.0.0.0:{os.getenv('PORT', '5001')}"

workers = 1          # see the module docstring before changing this
threads = 4          # concurrency without a second scheduler
worker_class = "gthread"

# A NuPay mandate status check drives a real browser through login + 2FA and
# takes ~57s. Keep this above that and in step with nginx's proxy_read_timeout.
timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"

# Identify the process in `ps` so beta is distinguishable from production.
proc_name = "cpace-beta"
