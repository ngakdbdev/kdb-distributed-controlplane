"""
polling.py - base for REST providers that are polled on an interval rather than
streamed over a websocket (Yahoo, Alpha Vantage, ...). Subclasses implement
`poll()` -> list[Tick]; this handles the loop, the publish, and (for tests) an
injectable fetch + clock/sleep so a poll cycle can be driven with canned JSON
and no network.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Callable, Optional

from .base import MarketDataProvider

log = logging.getLogger("providers.polling")


class PollingProvider(MarketDataProvider):
    poll_interval = 2.0
    user_agent = "Mozilla/5.0 (kdb-control-plane provider)"

    def __init__(self, *args, fetch: Optional[Callable[[str], dict]] = None,
                 poll_interval: Optional[float] = None,
                 sleep: Callable[[float], None] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._fetch = fetch          # test seam: url -> parsed json dict
        if poll_interval is not None:
            self.poll_interval = poll_interval
        import time
        self._sleep = sleep or time.sleep

    def _get_json(self, url: str) -> dict:
        if self._fetch is not None:
            return self._fetch(url)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def poll(self) -> list:
        """Fetch once and return a list of Ticks. Subclasses implement this."""
        raise NotImplementedError

    def run_once(self) -> int:
        """One poll+publish cycle. Split out so tests can drive it directly."""
        try:
            ticks = self.poll()
        except Exception as exc:  # noqa: BLE001 - a bad poll shouldn't kill the loop
            self.log.warning("poll failed: %s", exc)
            return 0
        return self._publish(ticks)

    def run(self) -> None:
        self.log.info("polling %d symbols every %.1fs", len(self.symbols), self.poll_interval)
        while True:
            self.run_once()
            self._sleep(self.poll_interval)
