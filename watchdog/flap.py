"""
flap.py - flapping detection for the watchdog.

Blindly restarting a service that keeps crashing is worse than doing nothing:
it hammers the scheduler, floods the audit log, and hides the real problem. A
production self-healer has to notice "I've restarted this N times in the last
few minutes and it keeps dying" and *escalate* instead of restarting again.

FlapTracker is a sliding-window counter per service plus an escalation
cooldown. The clock is injectable so the behaviour is deterministically
testable without sleeping.
"""
import time
from collections import defaultdict, deque


class FlapTracker:
    def __init__(self, window_sec: float = 120, threshold: int = 3,
                 cooldown_sec: float = 300, clock=time.monotonic):
        # threshold restarts within window_sec => flapping; once escalated, we
        # stop touching the service for cooldown_sec, then give it a fresh start
        self.window = window_sec
        self.threshold = threshold
        self.cooldown = cooldown_sec
        self._clock = clock
        self._restarts: dict[str, deque] = defaultdict(deque)
        self._cooldown_until: dict[str, float] = {}

    def _prune(self, service: str, now: float) -> None:
        dq = self._restarts[service]
        while dq and (now - dq[0]) > self.window:
            dq.popleft()

    def record_restart(self, service: str) -> None:
        now = self._clock()
        self._restarts[service].append(now)
        self._prune(service, now)

    def restart_count(self, service: str) -> int:
        now = self._clock()
        self._prune(service, now)
        return len(self._restarts[service])

    def is_flapping(self, service: str) -> bool:
        return self.restart_count(service) >= self.threshold

    def start_cooldown(self, service: str, cooldown_sec: float = None) -> None:
        """`cooldown_sec` overrides the tracker's default for this one call -
        an OOM-caused flap (see runbooks.escalate_oom) gets a much longer
        cooldown than a plain crash-loop, because retrying sooner is
        provably futile until the underlying data volume changes or someone
        intervenes; a short retry just wastes restart attempts and thrashes
        the host for no benefit."""
        duration = self.cooldown if cooldown_sec is None else cooldown_sec
        self._cooldown_until[service] = self._clock() + duration

    def in_cooldown(self, service: str) -> bool:
        until = self._cooldown_until.get(service)
        if until is None:
            return False
        if self._clock() < until:
            return True
        # cooldown expired - clear it and wipe the restart history so the
        # service gets a clean slate rather than immediately re-flapping
        del self._cooldown_until[service]
        self._restarts[service].clear()
        return False
