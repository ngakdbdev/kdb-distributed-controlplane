"""
runbooks.py - deterministic remediation the watchdog is allowed to take.

This is intentionally NOT an LLM/agentic decision. Every runbook here is a
pre-validated, hand-written function. The watchdog's job is to match an
observed failure to a *signature*, dispatch to the one runbook for that
signature, and record the outcome. Deterministic pattern -> pre-validated
remediation is a design choice: unpredictable behaviour during an outage is
worse than no automation at all.

The MVP shipped with exactly one runbook (restart the container). This is the
real set, matched to distinct failure signatures:

  container_down        container isn't running                -> restart_and_verify
  container_unhealthy   running but failing its health probe   -> restart_and_verify
                        (a wedged q process that a container-level check misses)
  dependency_down       a shard process is down because ITS    -> defer_to_dependency
                        tickerplant is down; restarting the       (heal the tp first,
                        dependent first is wasted work            let the dependent
                        (q self-heals its tp connection)          recover next cycle)
  flapping              restarted too many times in a window   -> escalate
                        - restarting again would just hammer      (stop restarting,
                        a crash-looping process                    mark degraded)
  oom_crash_loop        flapping AND the last exit was OOM     -> escalate_oom
                        - a plain restart-and-hope is provably    (much longer cooldown
                        futile here: the container will OOM       + a specific,
                        again on the next warm-start unless the   actionable diagnosis
                        underlying data volume actually changes   in the audit trail,
                        (EOD rollover) or someone intervenes -     not just "flapping")
                        confirmed directly, repeatedly, operating
                        this exact deployment (chained-RDB
                        warm-start re-reading an oversized
                        scratch file - see docs/troubleshooting.md)

Runbooks share the signature `fn(orchestrator, service, ctx) -> dict`, where
ctx carries the live status snapshot and the FlapTracker.
"""
import logging
import time

log = logging.getLogger("runbooks")

RECOVERY_CHECK_DELAY_SEC = 5
MAX_RESTART_ATTEMPTS = 3
# how much longer an OOM-caused flap cools down for, vs a plain crash-loop -
# see escalate_oom's docstring for why a short retry is worse than useless here
OOM_COOLDOWN_MULTIPLIER = 6

# ------------------------------------------------------------------ signatures
HEALTHY = "healthy"
CONTAINER_DOWN = "container_down"
CONTAINER_UNHEALTHY = "container_unhealthy"
DEPENDENCY_DOWN = "dependency_down"
FLAPPING = "flapping"
OOM_CRASH_LOOP = "oom_crash_loop"

# tiers whose processes depend on their shard's tickerplant
_DEPENDENT_PREFIXES = ("wdb-", "rdb-", "idb-")


def tickerplant_dependency(service: str) -> str | None:
    """The tickerplant a shard process needs, or None. wdb-s3 -> tp-s3."""
    for prefix in _DEPENDENT_PREFIXES:
        if service.startswith(prefix):
            return "tp-" + service[len(prefix):]
    return None


def classify(service: str, status_map: dict, flap_tracker,
             healthy: bool | None = None, oom: bool = False) -> str:
    """Map an observation to a failure signature.

    `healthy` is the result of an optional deeper health probe: None means "not
    probed" (fall back to container status only), False means "running but the
    process reports unhealthy". `oom` is whether the service's most recent
    exit was an OOM kill (from the orchestrator's own record, not a guess) -
    only consulted once a service is already flapping, since a single OOM
    exit isn't necessarily a pattern (a one-off memory spike restarts fine);
    it's OOM *plus* repeated restarts that means retrying won't help. Order
    matters: a down tickerplant is the root cause, so dependents are
    dependency_down (not flapping/container_down); a service failing on its
    own merits past the threshold is flapping (or oom_crash_loop) before we
    try yet another restart.
    """
    status = status_map.get(service, "not_found")
    running = status == "running"
    unhealthy = running and healthy is False

    if running and not unhealthy:
        return HEALTHY

    dep = tickerplant_dependency(service)
    if dep is not None and status_map.get(dep) != "running":
        return DEPENDENCY_DOWN
    if flap_tracker.is_flapping(service):
        return OOM_CRASH_LOOP if oom else FLAPPING
    return CONTAINER_UNHEALTHY if unhealthy else CONTAINER_DOWN


# -------------------------------------------------------------------- runbooks
def restart_and_verify(orchestrator, service: str, ctx: dict) -> dict:
    """Restart a stopped/crashed/wedged container, wait, verify it recovered.
    Records the restart with the FlapTracker so repeated incidents over time
    trip the flapping signature."""
    ctx["flap"].record_restart(service)
    for attempt in range(1, MAX_RESTART_ATTEMPTS + 1):
        log.info("runbook restart_and_verify: %s (attempt %d/%d)", service, attempt, MAX_RESTART_ATTEMPTS)
        orchestrator.start(service)
        time.sleep(ctx.get("recovery_delay_sec", RECOVERY_CHECK_DELAY_SEC))
        if orchestrator.status(service) == "running":
            return {"service": service, "runbook": "restart_and_verify",
                    "attempts": attempt, "outcome": "success", "final_status": "running"}
    return {"service": service, "runbook": "restart_and_verify",
            "attempts": MAX_RESTART_ATTEMPTS, "outcome": "failure",
            "final_status": orchestrator.status(service)}


def defer_to_dependency(orchestrator, service: str, ctx: dict) -> dict:
    """The service is down because its tickerplant is down. Don't restart the
    dependent - that's wasted work (a fresh rdb/wdb/idb self-heals its tp
    connection once the tp is back). Heal the tickerplant; the dependent
    recovers on the next cycle. Naming the dependency makes the audit trail
    show the *root cause*, not just the symptom."""
    dep = tickerplant_dependency(service)
    return {"service": service, "runbook": "defer_to_dependency",
            "attempts": 0, "outcome": "deferred", "final_status": ctx["status_map"].get(service, "unknown"),
            "detail": f"waiting on dependency {dep} (status={ctx['status_map'].get(dep, 'unknown')})"}


def escalate(orchestrator, service: str, ctx: dict) -> dict:
    """Flapping: restarting again would just hammer a crash-looping process.
    Stop restarting, open a cooldown so we leave it alone for a while, and
    record an escalation for a human to look at."""
    ctx["flap"].start_cooldown(service)
    count = ctx["flap"].restart_count(service)
    log.warning("runbook escalate: %s flapping (%d restarts in window) - escalating, not restarting", service, count)
    return {"service": service, "runbook": "escalate",
            "attempts": 0, "outcome": "escalated", "final_status": ctx["status_map"].get(service, "unknown"),
            "detail": f"{count} restarts within flap window; cooling down"}


def escalate_oom(orchestrator, service: str, ctx: dict) -> dict:
    """Flapping AND the last exit was an OOM kill: don't just cool down like
    a plain flap, cool down much longer, because a short retry is worse than
    doing nothing here. Restarting will make this container re-run its
    warm-start recovery, which reads however much data drove it OOM in the
    first place - that data doesn't shrink on its own between restarts (it
    only shrinks at EOD sealing, or if someone manually intervenes), so
    retrying every few minutes just repeats the same failure and thrashes
    the host for no benefit. Confirmed directly, repeatedly, operating this
    exact deployment - see docs/troubleshooting.md's OOM-crash-loop entry
    for the actual manual remediation (trim the oversized scratch file,
    clean restart) this points the audit trail at."""
    cooldown = ctx["flap"].cooldown * OOM_COOLDOWN_MULTIPLIER
    ctx["flap"].start_cooldown(service, cooldown_sec=cooldown)
    count = ctx["flap"].restart_count(service)
    log.warning("runbook escalate_oom: %s OOM-crash-looping (%d restarts in window) - "
               "cooling down %gs (%dx normal), see docs/troubleshooting.md",
               service, count, cooldown, OOM_COOLDOWN_MULTIPLIER)
    return {"service": service, "runbook": "escalate_oom",
            "attempts": 0, "outcome": "escalated", "final_status": ctx["status_map"].get(service, "unknown"),
            "detail": (f"{count} restarts in window, last exit was OOM - retrying won't help until "
                      f"the data volume changes; cooling down {cooldown:g}s. Manual fix: trim the "
                      f"oversized scratch file (docs/troubleshooting.md's OOM-crash-loop entry)")}


RUNBOOKS = {
    CONTAINER_DOWN: restart_and_verify,
    CONTAINER_UNHEALTHY: restart_and_verify,
    DEPENDENCY_DOWN: defer_to_dependency,
    FLAPPING: escalate,
    OOM_CRASH_LOOP: escalate_oom,
}
