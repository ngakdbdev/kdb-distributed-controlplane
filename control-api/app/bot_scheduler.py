"""
bot_scheduler.py - the server-side poll loop for the trade signal engine
(app/signal_engine.py).

Runs as a background thread INSIDE the control-api process (started at app
startup, app/main.py) rather than a separate container: it reuses the same
DB engine and calls signal_engine.evaluate_tenant directly, in-process - no
HTTP hop, no separate deployment/health-check surface for a v1. Shaped after
watchdog/watchdog.py's loop: a deterministic poll, one pass per tenant, and a
failure evaluating one tenant must never take down the loop or block another
tenant's pass.
"""
import logging
import os
import threading

from sqlmodel import Session, select

from . import signal_engine
from .db import engine
from .models import BotConfig

log = logging.getLogger("bot_scheduler")

POLL_SEC = float(os.environ.get("BOT_POLL_SEC", "12"))
ENABLED = os.environ.get("BOT_SCHEDULER_ENABLED", "true").lower() == "true"

_stop = threading.Event()
_thread = None


def run_once() -> None:
    """One pass over every tenant with an enabled bot config. Public (not
    just called from the loop) so tests can drive a single pass deterministically."""
    with Session(engine) as session:
        configs = session.exec(select(BotConfig).where(BotConfig.enabled == True)).all()  # noqa: E712
        for config in configs:
            try:
                signal_engine.evaluate_tenant(session, config)
            except Exception:  # noqa: BLE001 - one tenant's bot must never crash the loop
                log.exception("bot evaluation failed for tenant_id=%s", config.tenant_id)
                session.rollback()


def _loop() -> None:
    log.info("signal-bot scheduler up, polling every %gs", POLL_SEC)
    while not _stop.is_set():
        try:
            run_once()
        except Exception:  # noqa: BLE001
            log.exception("unexpected error in signal-bot scheduler loop")
        _stop.wait(POLL_SEC)


def start() -> None:
    global _thread
    if not ENABLED:
        log.info("signal-bot scheduler disabled (BOT_SCHEDULER_ENABLED=false)")
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="signal-bot-scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=5)
