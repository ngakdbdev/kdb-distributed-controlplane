import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import bot_scheduler
from . import metrics_history
from . import symbol_discovery
from .db import init_db
from . import licensing
from .routers import (audit, auth, auth_ldap, auth_sso, backtest as backtest_router, bot,
                      connectors, export, feedhandlers, fleet, infra_profiles, license as license_router,
                      llm_config, metrics, migration, platform_health, query, signals,
                      subscribers, symbols, tenants, tickhouse, tickerplants, topology, trading,
                      users)

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
app.include_router(infra_profiles.router)
app.include_router(platform_health.router)
app.include_router(backtest_router.router)
app.include_router(users.router)
app.include_router(query.router)
app.include_router(tickerplants.router)
app.include_router(symbols.router)
app.include_router(trading.router)
app.include_router(bot.router)
app.include_router(signals.router)
app.include_router(llm_config.router)
app.include_router(migration.router)
app.include_router(feedhandlers.router)


@app.on_event("startup")
def on_startup():
    init_db()
    _check_license()
    bot_scheduler.start()
    symbol_discovery.start()
    metrics_history.start()


@app.on_event("shutdown")
def on_shutdown():
    bot_scheduler.stop()
    symbol_discovery.stop()
    metrics_history.stop()


def _check_license():
    import os
    info = licensing.validate(os.environ.get("LICENSE_KEY", ""))
    logging.getLogger("license").info(licensing.status_line(info))
    if not info.valid and licensing.enforcement_active():
        deployment_env = os.environ.get("DEPLOYMENT_ENV", "local")
        raise RuntimeError(
            f"product licence invalid: {info.reason} - a licence key is mandatory for this "
            f"deployment (DEPLOYMENT_ENV={deployment_env!r}). Set a valid LICENSE_KEY, or if "
            f"this really is local/dev use, set DEPLOYMENT_ENV=local. LICENSE_ENFORCE overrides "
            f"either way if set explicitly.")


@app.get("/health")
def health():
    return {"status": "up"}
