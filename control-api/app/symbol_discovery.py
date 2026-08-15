"""
symbol_discovery.py - keeps app/symbols.py's reference list in sync with
what's ACTUALLY trading, not just its static seed.

A background poll loop (same shape as bot_scheduler.py/watchdog.py) that
periodically asks every RDB shard for `exec distinct sym from trade`
(reusing signal_engine.fetch_universe_symbols - the same universe scan the
auto-mode signal bot already does) and folds any symbol not already in the
static seed into a live cache app/symbols.py's search()/markets() serve
from. So a symbol that starts flowing through a live feed (a newly-enabled
connector, a new crypto pair on Kraken/Coinbase) shows up in the symbol
picker automatically - no code change, no restart, no manually-maintained
symbols file.
"""
import logging
import os
import threading

from sqlmodel import Session

from . import asset_metadata
from . import signal_engine
from . import symbols as symref
from .db import engine

log = logging.getLogger("symbol_discovery")

POLL_SEC = float(os.environ.get("SYMBOL_DISCOVERY_POLL_SEC", "60"))
ENABLED = os.environ.get("SYMBOL_DISCOVERY_ENABLED", "true").lower() == "true"

_stop = threading.Event()
_thread = None


def run_once() -> None:
    """One discovery pass. Public (not just called from the loop) so tests
    and an operator (e.g. right after enabling a new connector) can trigger
    a refresh without waiting for the next tick."""
    try:
        live = signal_engine.fetch_universe_symbols()
    except Exception:  # noqa: BLE001 - a bad pass must never crash the loop
        log.exception("symbol discovery: universe scan failed")
        return
    added = symref.merge_live_symbols(live)
    if added:
        log.info("symbol discovery: added %d newly-seen symbol(s): %s",
                 len(added), ", ".join(sorted(added)))
    try:
        with Session(engine) as session:
            asset_metadata.record_seen(live, session)
    except Exception:  # noqa: BLE001 - persistence is a bonus, never fatal to discovery itself
        log.exception("symbol discovery: failed to persist asset metadata")


def _loop() -> None:
    log.info("symbol discovery up, polling every %gs", POLL_SEC)
    while not _stop.is_set():
        try:
            run_once()
        except Exception:  # noqa: BLE001
            log.exception("unexpected error in symbol discovery loop")
        _stop.wait(POLL_SEC)


def start() -> None:
    global _thread
    if not ENABLED:
        log.info("symbol discovery disabled (SYMBOL_DISCOVERY_ENABLED=false)")
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="symbol-discovery", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=5)
