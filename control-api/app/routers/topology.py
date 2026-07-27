from fastapi import APIRouter, Depends
from sqlmodel import Session

from ..config import settings
from ..db import get_session, log_event
from ..orchestrator import orchestrator
from .auth import CurrentUser, require_auth

router = APIRouter(prefix="/topology", tags=["topology"])

# NOTE: this router talks to the orchestrator directly (Docker locally, or
# the Kubernetes API when Helm-deployed with ORCHESTRATOR_BACKEND=kubernetes)
# and is correct for a *single-tenant, dedicated* deployment - one bank, one
# control-api instance, running inside their own cluster.
#
# For the multi-tenant hosted SaaS mode (one shared control-api serving many
# banks, each with their own remote cluster), use /fleet/agents/{id}/commands
# instead - that path never requires the shared control-api to have any
# direct network access into a customer's environment.


@router.get("/status")
def status():
    """Live status of every managed container, for the topology view."""
    return orchestrator.status_all()


@router.post("/service/{service}/start")
def start_service(service: str, user: CurrentUser = Depends(require_auth),
                   session: Session = Depends(get_session)):
    ok = orchestrator.start(service)
    log_event(session, user.email, "start_service", service,
              outcome="success" if ok else "failure", tenant_id=user.tenant_id)
    return {"service": service, "started": ok}


@router.post("/service/{service}/stop")
def stop_service(service: str, user: CurrentUser = Depends(require_auth),
                  session: Session = Depends(get_session)):
    ok = orchestrator.stop(service)
    log_event(session, user.email, "stop_service", service,
              outcome="success" if ok else "failure", tenant_id=user.tenant_id)
    return {"service": service, "stopped": ok}


@router.post("/service/{service}/restart")
def restart_service(service: str, user: CurrentUser = Depends(require_auth),
                     session: Session = Depends(get_session)):
    ok = orchestrator.restart(service)
    log_event(session, user.email, "restart_service", service,
              outcome="success" if ok else "failure", tenant_id=user.tenant_id)
    return {"service": service, "restarted": ok}


@router.get("/service/{service}/logs")
def service_logs(service: str, tail: int = 200, user: CurrentUser = Depends(require_auth)):
    logs = orchestrator.logs(service, tail=tail)
    return {"service": service, "logs": logs or "(no logs / container not found)"}


@router.get("/services")
def list_services():
    return {"services": list(settings.managed_services.keys())}
