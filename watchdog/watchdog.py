"""
watchdog.py - the self-healing loop.

Every POLL_INTERVAL_SEC seconds:
  1. check the live status of every managed container
  2. if a container that should be running is not, treat it as a
     `container_down` event and dispatch to the matching runbook
  3. log the detection and the outcome, both to stdout and (best-effort)
     to the control API's audit trail

This process intentionally has no LLM in its decision loop. "Self-healing"
here means deterministic pattern -> pre-validated remediation, not a model
deciding what to do live during an incident.
"""
import logging
import os
import time

import requests

from orchestrator import orchestrator
from runbooks import RUNBOOKS

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                     format="%(asctime)s watchdog %(levelname)s %(message)s")
log = logging.getLogger("watchdog")

POLL_INTERVAL_SEC = int(os.environ.get("WATCHDOG_POLL_SEC", "5"))
CONTROL_API_URL = os.environ.get("CONTROL_API_URL", "http://control-api:8000")
WATCHDOG_SHARED_SECRET = os.environ.get("WATCHDOG_SHARED_SECRET", "dev-watchdog-secret-change-in-deploy")

# services the watchdog actively heals - feed simulators are excluded since
# their enabled/disabled state is a deliberate user choice made via the
# connectors screen, not a failure
HEALED_SERVICES = [
    "tp-a-m", "tp-n-z", "wdb-a-m", "wdb-n-z",
    "rdb-a-m", "rdb-n-z", "idb-a-m", "idb-n-z", "gateway",
]

# tracks services currently mid-recovery so we don't pile on duplicate
# restart attempts while one is already in flight
_in_progress = set()


def report(actor: str, action: str, target: str, detail: str, outcome: str):
    log.info("%s %s target=%s outcome=%s detail=%s", actor, action, target, outcome, detail)
    try:
        requests.post(
            f"{CONTROL_API_URL}/audit/internal",
            json={"actor": actor, "action": action, "target": target,
                  "detail": detail, "outcome": outcome},
            headers={"X-Internal-Secret": WATCHDOG_SHARED_SECRET},
            timeout=2,
        )
    except requests.RequestException as exc:
        # the control API being unreachable must never block healing itself
        log.warning("could not reach control API to log audit event: %s", exc)


def check_and_heal(orchestrator):
    for service in HEALED_SERVICES:
        if service in _in_progress:
            continue
        status = orchestrator.status(service)
        if status in ("running",):
            continue
        if status == "not_found":
            # container hasn't been created yet (e.g. stack still deploying)
            continue

        log.warning("detected %s in state '%s' - dispatching runbook", service, status)
        report("watchdog", "detect_failure", service, f"status={status}", "detected")

        _in_progress.add(service)
        try:
            result = RUNBOOKS["container_down"](orchestrator, service)
        finally:
            _in_progress.discard(service)

        report(
            "watchdog", "auto_heal", service,
            f"runbook={result['runbook']} attempts={result['attempts']} final_status={result['final_status']}",
            result["outcome"],
        )


def main():
    log.info("watchdog up, polling every %ss: %s", POLL_INTERVAL_SEC, ", ".join(HEALED_SERVICES))
    while True:
        try:
            check_and_heal(orchestrator)
        except Exception:  # noqa: BLE001 - the watchdog must never crash-loop silently
            log.exception("unexpected error in watchdog loop")
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
