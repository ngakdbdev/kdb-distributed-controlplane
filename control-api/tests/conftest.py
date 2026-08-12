import os
import tempfile

# Set env BEFORE app modules import (db.py builds its engine at import time).
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")
os.environ.setdefault("SEED_DEMO_TENANT", "true")
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
