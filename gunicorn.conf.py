"""Gunicorn settings for 1Cpace V8.

Entry point is ``run:app`` — run.py builds the app through create_app().

Bound to 127.0.0.1 only: nginx terminates TLS and proxies to it. Nothing on
this host should be reachable from outside except through nginx.

PORT 5002 is the beta instance. The other ports on that VPS are taken —
5000 is production 1Cpace (a different application) and 5001 is FiksAccounts.
Check with `ss -ltnp` before changing it.

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

bind = f"127.0.0.1:{os.getenv('PORT', '5002')}"

workers = 1          # see the module docstring before changing this
threads = 4          # concurrency without a second scheduler
worker_class = "gthread"

# A NuPay mandate status check drives a real browser through login + 2FA and
# takes ~57s. Keep this above that and in step with nginx's proxy_read_timeout,
# which is 120s in /etc/nginx/conf.d/beta.1cpace.co.za.conf.
timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"

proc_name = "onecpase-beta"
