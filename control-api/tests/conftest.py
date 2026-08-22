import os
import tempfile
from pathlib import Path

# Set env BEFORE app modules import (db.py builds its engine at import time).
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")
os.environ.setdefault("SEED_DEMO_TENANT", "true")

# app.db.init_db() no longer calls create_all() - schema creation/evolution
# is Alembic's job now (see control-api/Dockerfile's CMD). Tests never go
# through that Dockerfile, so they need their own equivalent: run the real
# migration chain, in-process, against this session's fresh temp DB, before
# anything imports app.main and triggers the startup event. This is
# deliberately the SAME migration chain production runs, not a shortcut -
# a test suite that skips real migrations wouldn't have caught the batch-
# mode/SQLite bugs those migrations themselves had until this session.
from alembic import command as _alembic_command  # noqa: E402
from alembic.config import Config as _AlembicConfig  # noqa: E402

_cfg = _AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
_alembic_command.upgrade(_cfg, "head")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-definitely-long-enough")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("WEB_UI_URL", "https://ui.example.com")
# The signal-bot's background poll loop (app/bot_scheduler.py) has nothing
# useful to do against a test DB with no live q processes, and a stray
# thread outliving a test module's TestClient teardown is a flakiness risk
# for no benefit - tests that want the scheduler drive it explicitly via
# bot_scheduler.run_once() instead of the real interval thread.
os.environ.setdefault("BOT_SCHEDULER_ENABLED", "false")
# Same reasoning as BOT_SCHEDULER_ENABLED above - no live q processes in the
# test DB for symbol_discovery.py's background loop to usefully scan.
os.environ.setdefault("SYMBOL_DISCOVERY_ENABLED", "false")
# Same reasoning - no live orchestrator/gateway to usefully snapshot in a
# test DB, and a background thread writing rows during a test run is a
# flakiness risk for no benefit; tests that want this drive it explicitly
# via metrics_history.capture_once().
os.environ.setdefault("METRICS_HISTORY_ENABLED", "false")
