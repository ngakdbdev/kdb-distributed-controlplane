import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import bot_scheduler
from . import symbol_discovery
from .db import init_db
from . import licensing
from .routers import (audit, auth, auth_ldap, auth_sso, bot, connectors, export, fleet,
                      license as license_router, llm_config, metrics, migration, query, subscribers,
                      symbols, tenants, tickhouse, tickerplants, topology, trading)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(
    title="kdb+ tick control plane API",
    version="0.2.0",
    description="Multi-tenant SaaS control plane. Each tenant's data plane runs in their own "
                 "AWS/Azure/GCP/on-prem environment via an agent that pulls commands from here - "
                 "see /fleet for the agent protocol and /tenants for tenant management.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before anything beyond a local demo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(auth_sso.router)
app.include_router(auth_ldap.router)
app.include_router(tenants.router)
app.include_router(fleet.router)
app.include_router(topology.router)
app.include_router(metrics.router)
app.include_router(connectors.router)
app.include_router(export.router)
app.include_router(subscribers.router)
app.include_router(audit.router)
app.include_router(license_router.router)
app.include_router(tickhouse.router)
app.include_router(query.router)
app.include_router(tickerplants.router)
app.include_router(symbols.router)
app.include_router(trading.router)
app.include_router(bot.router)
app.include_router(llm_config.router)
app.include_router(migration.router)


@app.on_event("startup")
def on_startup():
    init_db()
    _check_license()
    bot_scheduler.start()
    symbol_discovery.start()


@app.on_event("shutdown")
def on_shutdown():
    bot_scheduler.stop()
    symbol_discovery.stop()


def _check_license():
    import os
    info = licensing.validate(os.environ.get("LICENSE_KEY", ""))
    logging.getLogger("license").info(licensing.status_line(info))
    if not info.valid and os.environ.get("LICENSE_ENFORCE", "").lower() in ("1", "true", "yes"):
        raise RuntimeError(f"product licence invalid: {info.reason} "
                           "(set a valid LICENSE_KEY or unset LICENSE_ENFORCE)")


@app.get("/health")
def health():
    return {"status": "up"}
