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

import topology
from flap import FlapTracker
from orchestrator import orchestrator
from runbooks import RUNBOOKS, classify, HEALTHY

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                     format="%(asctime)s watchdog %(levelname)s %(message)s")
log = logging.getLogger("watchdog")

POLL_INTERVAL_SEC = int(os.environ.get("WATCHDOG_POLL_SEC", "5"))
CONTROL_API_URL = os.environ.get("CONTROL_API_URL", "http://control-api:8000")
WATCHDOG_SHARED_SECRET = os.environ.get("WATCHDOG_SHARED_SECRET", "dev-watchdog-secret-change-in-deploy")

# services the watchdog actively heals - every per-shard q process plus the
# gateway, derived from the shard count so it scales automatically. Feed
# simulators are deliberately excluded: their enabled/disabled state is a user
# choice made via the Connectors screen, not a failure to heal. SHARD_COUNT
# must match the control-api's - both read the same env in every deployment.
SHARD_COUNT = int(os.environ.get("SHARD_COUNT", "2"))
HEALED_SERVICES = topology.healed_services(SHARD_COUNT)

# flapping thresholds: FLAP_THRESHOLD restarts within FLAP_WINDOW_SEC trips
# escalation, after which the service is left alone for FLAP_COOLDOWN_SEC
FLAP_WINDOW_SEC = float(os.environ.get("WATCHDOG_FLAP_WINDOW_SEC", "120"))
FLAP_THRESHOLD = int(os.environ.get("WATCHDOG_FLAP_THRESHOLD", "3"))
FLAP_COOLDOWN_SEC = float(os.environ.get("WATCHDOG_FLAP_COOLDOWN_SEC", "300"))

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


def check_and_heal(orchestrator, flap, health_probe=None, services=None,
                   recovery_delay_sec=None):
    """One healing pass over every managed service.

    Services are visited in dependency order (a shard's tickerplant before its
    wdb/rdb/idb), so when a tickerplant is healed its status is updated live and
    the shard's dependents reclassify from dependency_down to a plain restart in
    the same pass. `health_probe`, if supplied, is `fn(service) -> bool` and
    lets a *running-but-wedged* process be caught as container_unhealthy.
    """
    services = services if services is not None else HEALED_SERVICES
    status_map = {s: orchestrator.status(s) for s in services}

    for service in services:
        if service in _in_progress:
            continue
        if flap.in_cooldown(service):
            continue  # escalated and cooling down - deliberately left alone

        status = status_map.get(service)
        if status == "not_found":
            continue  # not deployed yet (e.g. stack still coming up)

        healthy = None
        if status == "running":
            if health_probe is None:
                continue                       # container up, no deeper probe -> healthy
            healthy = health_probe(service)
            if healthy:
                continue
            # running but the process reports unhealthy -> fall through to heal

        signature = classify(service, status_map, flap, healthy=healthy)
        if signature == HEALTHY:
            continue

        detail = f"status={status}" + ("" if healthy is None else f" healthy={healthy}")
        log.warning("detected %s (%s) - signature=%s", service, detail, signature)
        report("watchdog", "detect_failure", service, f"{detail} signature={signature}", "detected")

        ctx = {"flap": flap, "status_map": status_map}
        if recovery_delay_sec is not None:
            ctx["recovery_delay_sec"] = recovery_delay_sec
        _in_progress.add(service)
        try:
            result = RUNBOOKS[signature](orchestrator, service, ctx)
        finally:
            _in_progress.discard(service)

        # refresh this service's status so dependents visited later in the same
        # pass see the post-heal state (a just-restarted tickerplant now running)
        status_map[service] = orchestrator.status(service)

        report(
            "watchdog", "auto_heal", service,
            f"runbook={result['runbook']} attempts={result['attempts']} "
            f"final_status={result['final_status']}" + (f" {result['detail']}" if result.get("detail") else ""),
            result["outcome"],
        )


def main():
    log.info("watchdog up, polling every %ss (%d services, flap %d/%gs): %s",
             POLL_INTERVAL_SEC, len(HEALED_SERVICES), FLAP_THRESHOLD, FLAP_WINDOW_SEC,
             ", ".join(HEALED_SERVICES))
    flap = FlapTracker(window_sec=FLAP_WINDOW_SEC, threshold=FLAP_THRESHOLD,
                       cooldown_sec=FLAP_COOLDOWN_SEC)
    while True:
        try:
            check_and_heal(orchestrator, flap)
        except Exception:  # noqa: BLE001 - the watchdog must never crash-loop silently
            log.exception("unexpected error in watchdog loop")
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
