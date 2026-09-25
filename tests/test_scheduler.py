"""The nightly job schedule. itensity_member_import is a live network call to
a third party with no guaranteed speed, and writes to the same per-tenant
database as ptp_automations - the two must not be scheduled close together,
or a slow Itensity pull could still be running when ptp_automations starts.
"""
from onecpase import scheduler as scheduler_module


def _init_and_get_jobs(app):
    app.config["SCHEDULER_ENABLED"] = True
    # init_scheduler() deliberately refuses to start under app.testing (the
    # app fixture sets TESTING=True) - this test wants to inspect the real
    # job registration, so it turns that guard off for just this call.
    app.config["TESTING"] = False
    scheduler_module._scheduler = None  # each test starts clean
    try:
        scheduler_module.init_scheduler(app)
        jobs = {job.id: job for job in scheduler_module._scheduler.get_jobs()}
    finally:
        if scheduler_module._scheduler is not None:
            scheduler_module._scheduler.shutdown(wait=False)
            scheduler_module._scheduler = None
        app.config["TESTING"] = True
    return jobs


def _minutes_of_day(job):
    hour = job.trigger.fields[5].expressions[0].first
    minute = job.trigger.fields[6].expressions[0].first
    return int(hour) * 60 + int(minute)


def test_itensity_import_is_well_separated_from_ptp_automations(app):
    # The two write to the same per-tenant database, and the Itensity pull is
    # a live network call to a third party with no guaranteed speed. Which
    # one runs first doesn't matter - what matters is that a slow Itensity
    # pull can't still be running when the other one starts. A single live
    # pull measured well under 2 minutes end to end (2026-09-23 runs), but
    # that's no guarantee for every night, so this requires real separation
    # rather than trusting a tight window to hold.
    jobs = _init_and_get_jobs(app)
    itensity_at = _minutes_of_day(jobs["itensity_member_import"])
    ptp_at = _minutes_of_day(jobs["ptp_automations"])
    gap_minutes = abs(itensity_at - ptp_at)

    assert gap_minutes >= 60, (
        f"only {gap_minutes} minutes between itensity_member_import and "
        "ptp_automations - too tight for a live network pull that writes to "
        "the same database ptp_automations reads"
    )
