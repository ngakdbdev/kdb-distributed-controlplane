"""
runbooks.py - deterministic remediation actions the watchdog is allowed to
take. This is intentionally NOT an LLM/agentic decision - every runbook here
is a pre-validated, hand-written function. The watchdog's job is pattern
matching a failure to one of these, executing it, and recording the outcome.
Keeping this deterministic is a design choice, not a limitation: unpredictable
behaviour during an outage is worse than no automation at all.
"""
import logging
import time

log = logging.getLogger("runbooks")

# how long to wait after a restart before checking whether it actually
# recovered, and how many times to retry before giving up and escalating
RECOVERY_CHECK_DELAY_SEC = 5
MAX_RESTART_ATTEMPTS = 3


def restart_and_verify(orchestrator, service: str) -> dict:
    """
    The one runbook this MVP ships: restart a stopped/crashed container,
    wait, and verify it came back up. A real deployment would add more
    runbooks (e.g. promote a standby RDB, shed a slow subscriber, trigger a
    TP-log replay) behind the same pattern - detect a specific failure
    signature, dispatch to the matching function below, log the outcome.
    """
    for attempt in range(1, MAX_RESTART_ATTEMPTS + 1):
        log.info("runbook: restarting %s (attempt %d/%d)", service, attempt, MAX_RESTART_ATTEMPTS)
        started = orchestrator.start(service)
        time.sleep(RECOVERY_CHECK_DELAY_SEC)
        status = orchestrator.status(service)
        if status == "running":
            return {
                "service": service, "runbook": "restart_and_verify",
                "attempts": attempt, "outcome": "success", "final_status": status,
            }
    return {
        "service": service, "runbook": "restart_and_verify",
        "attempts": MAX_RESTART_ATTEMPTS, "outcome": "failure",
        "final_status": orchestrator.status(service),
    }


RUNBOOKS = {
    "container_down": restart_and_verify,
}
