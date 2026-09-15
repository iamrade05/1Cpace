"""Which parts of the app are open, phase by phase.

1Cpase goes live one business process at a time. Phase 1 is the sales process
end to end — lead, call, application, contract, DebiCheck mandate — plus the
things that process cannot run without: the member register to check against,
the inboxes leads arrive through, and setup for tariffs, contracts and staff
logins. Everything else is closed until its phase opens.

Closed means closed: hidden from the nav *and* refused if someone types the
URL. A hidden link is not a closed door.

The active phase is set by ACTIVE_PHASE in the environment (default 1).
``ACTIVE_PHASE=all`` opens everything, which is what the test suite uses.
"""
from __future__ import annotations

ALL_PHASES = "all"

# Reachable whatever phase is live: sign-in, the dashboard everything redirects
# to, health checks, tenant/platform administration, and the machine-to-machine
# endpoints — inbound WhatsApp/Facebook webhooks and the PBX, which answer to
# outside systems rather than to staff.
ALWAYS_ON = frozenset(
    {"auth", "dashboard", "health", "platform", "tenants", "comms_webhooks", "pbx"}
)

# phase number → (name, blueprints it opens)
PHASES: dict[int, tuple[str, frozenset[str]]] = {
    1: (
        "Sales",
        frozenset(
            {
                "leads",            # lead capture and pipeline
                "sales_pipeline",
                "call_list",
                "join",             # public join form — the top of the funnel
                "events",           # public event registration link
                "applications",     # application → compliance → approval
                "contracts",
                "debicheck",        # mandates and debit orders
                "members",          # the register a sale is checked against
                "comms",            # WhatsApp and Facebook inboxes
                "internal_comms",   # staff messages and announcements
                "admin",            # tariffs, contract templates, users
                "admin_events",     # create/manage events and their registrations
                "screen_recordings",
            }
        ),
    ),
    2: ("Collections", frozenset({"collections", "ptp"})),
    3: ("Queries", frozenset({"queries"})),
    4: ("Fitness", frozenset({"fitness", "onboarding"})),
    5: ("Reports", frozenset({"reports", "access_report"})),
    6: ("HR", frozenset({"hr"})),
    7: ("Operations", frozenset({"operations", "turnstile"})),
}

# Nav sections in base.html, and the phase each one belongs to. The sales
# section spans two phases' worth of nav in the original design, so the finance
# section is split: DebiCheck is Phase 1, the access report is Phase 5.
NAV_SECTIONS: dict[str, int] = {
    "sales": 1,
    "communication": 1,
    "members": 1,
    "connections": 2,
    "fitness": 4,
    "analytics": 5,
    "hr": 6,
    "operations": 7,
    "admin": 1,
    "finance": 1,
}


def normalise(active_phase: object) -> int | str:
    """Accept 1, "1" or "all"; anything unreadable falls back to Phase 1."""
    if isinstance(active_phase, str) and active_phase.strip().lower() == ALL_PHASES:
        return ALL_PHASES
    try:
        return int(active_phase)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1


def phase_of(blueprint: str) -> int | None:
    """The phase that opens *blueprint*, or None if it is always on."""
    for number, (_name, blueprints) in PHASES.items():
        if blueprint in blueprints:
            return number
    return None


def phase_name(number: int) -> str:
    entry = PHASES.get(number)
    return entry[0] if entry else f"Phase {number}"


def enabled_blueprints(active_phase: object) -> frozenset[str]:
    """Everything open at *active_phase* — earlier phases stay open too."""
    active = normalise(active_phase)
    if active == ALL_PHASES:
        return ALWAYS_ON.union(*(blueprints for _n, blueprints in PHASES.values()))
    return ALWAYS_ON.union(
        *(blueprints for number, (_name, blueprints) in PHASES.items() if number <= active)
    )


def is_enabled(blueprint: str | None, active_phase: object) -> bool:
    """Unknown blueprints are refused rather than waved through — a new area
    has to be placed in a phase deliberately."""
    if blueprint is None:  # static files and other non-blueprint endpoints
        return True
    return blueprint in enabled_blueprints(active_phase)


def nav_section_enabled(section: str, active_phase: object) -> bool:
    active = normalise(active_phase)
    if active == ALL_PHASES:
        return True
    return NAV_SECTIONS.get(section, 1) <= active
