import os
import tempfile

# Set env BEFORE app modules import (db.py builds its engine at import time).
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")
os.environ.setdefault("SEED_DEMO_TENANT", "true")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-definitely-long-enough")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("WEB_UI_URL", "https://ui.example.com")
