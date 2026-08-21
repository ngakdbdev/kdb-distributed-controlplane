"""
cloud_provision.py (router) - the credentials-only auto-provisioning API:
POST your AWS/Azure/GCP account credentials, get back a run you can poll,
end with a real running TickHouse cluster - no cluster, no agent, no
Terraform/Helm knowledge required first (that's routers/tickhouse.py's
option-based path, still there, still the right choice if you already have
a cluster). See app/cloud_provisioner.py for the actual pipeline and the
full trust-model reasoning; this file is the HTTP surface + the
background-thread wrapper around it (terraform apply realistically takes
10-20+ minutes, far too long for a request/response cycle).

Every endpoint here is require_admin - same gate as routers/tickhouse.py's
create()/provision(), for the same reason (defining/creating infrastructure
is a provisioning-scope action, not a viewing one) - and this one additionally
creates real, billed cloud resources, which require_admin alone doesn't
communicate; that's what confirm_ack enforces (see cloud_provisioner.
CONFIRM_PHRASE).

Credentials are accepted once, encrypted immediately, and NEVER returned by
any response model below - _run_api() is the only place a CloudProvisionRun
becomes JSON, and it deliberately has no credentials_encrypted field.
"""
import json
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import cloud_credentials
from .. import cloud_provisioner
from ..db import engine, get_session, log_event
from ..models import CloudProvisionRun
from .auth import CurrentUser, require_admin, require_tenant_scope

router = APIRouter(prefix="/cloud-provision", tags=["cloud-provision"])


def _run_api(r: CloudProvisionRun) -> dict:
    return {
        "id": r.id, "name": r.name, "provider": r.provider, "region": r.region,
        "cluster_profile": r.cluster_profile, "status": r.status, "status_detail": r.status_detail,
        "log_tail": r.log_tail, "error_detail": r.error_detail,
        "tickhouse_id": r.tickhouse_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "created_by": r.created_by,
        # credentials_encrypted is intentionally never included
    }


class AwsProvisionBody(BaseModel):
    name: str
    region: str
    cluster_profile: str = "ha"
    access_key_id: str
    secret_access_key: str
    confirm_ack: str


class AzureProvisionBody(BaseModel):
    name: str
    region: str  # Azure "location"
    cluster_profile: str = "ha"
    tenant_id: str
    client_id: str
    client_secret: str
    subscription_id: str
    confirm_ack: str


class GcpProvisionBody(BaseModel):
    name: str
    region: str
    cluster_profile: str = "ha"
    project_id: str
    service_account_json: str
    confirm_ack: str


def _execute_in_background(run_id: int, req: cloud_provisioner.ProvisionRequest, creds: dict,
                           confirm_ack: str):
    """Runs on its OWN thread with its OWN db Session - a request-scoped
    Session (the `session` param every other endpoint in this codebase
    uses) is not safe to hand to a background thread that outlives the
    request. Every on_progress() call opens a fresh Session rather than
    holding one for the whole 10-20+ minute run, so a transient DB
    hiccup mid-apply doesn't take the whole run down."""
    def on_progress(status: str, detail: str, log_chunk: str):
        with Session(engine) as s:
            run = s.get(CloudProvisionRun, run_id)
            if run is None:
                return
            run.status = status
            run.status_detail = detail
            if log_chunk:
                run.log_tail = (run.log_tail + "\n" + log_chunk)[-8000:]
            run.updated_at = datetime.utcnow()
            s.add(run)
            s.commit()

    try:
        result = cloud_provisioner.run(run_id, req, creds, confirm_ack, on_progress)
    except cloud_provisioner.ProvisionError as exc:
        result = {"ok": False, "terraform_outputs": {}, "error": str(exc)}

    with Session(engine) as s:
        run = s.get(CloudProvisionRun, run_id)
        if run is None:
            return
        if result["ok"]:
            run.status = "complete"
            run.status_detail = "cluster created and Vantik installed"
            run.terraform_outputs_json = json.dumps(result["terraform_outputs"])
        else:
            run.status = "failed"
            run.error_detail = result["error"][-4000:]
        run.updated_at = datetime.utcnow()
        s.add(run)
        log_event(s, run.created_by, "cloud_provision_run_finished", target=f"cloud-provision-run:{run_id}",
                 detail=f"provider={run.provider} status={run.status}", tenant_id=run.tenant_id,
                 outcome="success" if result["ok"] else "failure")
        s.commit()


def _start_run(user: CurrentUser, session: Session, provider: str, name: str, region: str,
               cluster_profile: str, confirm_ack: str, creds: dict,
               project_id: str = "", subscription_id: str = "") -> CloudProvisionRun:
    if confirm_ack != cloud_provisioner.CONFIRM_PHRASE:
        raise HTTPException(status_code=400,
                            detail=f"confirm_ack must exactly equal '{cloud_provisioner.CONFIRM_PHRASE}' - "
                                   f"this creates real, billed cloud resources")
    problems = cloud_provisioner.validate_credentials(provider, creds)
    if problems:
        raise HTTPException(status_code=400, detail="invalid credentials: " + "; ".join(problems))
    if cluster_profile not in cloud_provisioner.CLUSTER_PROFILES:
        raise HTTPException(status_code=400,
                            detail=f"cluster_profile must be one of {cloud_provisioner.CLUSTER_PROFILES}")

    try:
        encrypted = cloud_credentials.encrypt_credentials(creds)
    except cloud_credentials.CloudCredentialsError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    run = CloudProvisionRun(
        tenant_id=user.tenant_id, name=name, provider=provider, region=region,
        cluster_profile=cluster_profile, project_id=project_id, subscription_id=subscription_id,
        credentials_encrypted=encrypted, status="pending", created_by=user.email,
    )
    session.add(run)
    log_event(session, user.email, "cloud_provision_run_started", target=f"tenant:{user.tenant_id}",
             detail=f"provider={provider} name={name} region={region} cluster_profile={cluster_profile}",
             tenant_id=user.tenant_id)
    session.commit()
    session.refresh(run)

    req = cloud_provisioner.ProvisionRequest(
        tenant_id=user.tenant_id, name=name, provider=provider, region=region,
        cluster_profile=cluster_profile, project_id=project_id, subscription_id=subscription_id,
    )
    thread = threading.Thread(target=_execute_in_background, args=(run.id, req, creds, confirm_ack),
                              daemon=True, name=f"cloud-provision-{run.id}")
    thread.start()
    return run


@router.post("/aws")
def provision_aws(body: AwsProvisionBody, user: CurrentUser = Depends(require_admin),
                  session: Session = Depends(get_session)):
    run = _start_run(user, session, "aws", body.name, body.region, body.cluster_profile,
                     body.confirm_ack,
                     {"access_key_id": body.access_key_id, "secret_access_key": body.secret_access_key})
    return _run_api(run)


@router.post("/azure")
def provision_azure(body: AzureProvisionBody, user: CurrentUser = Depends(require_admin),
                    session: Session = Depends(get_session)):
    run = _start_run(user, session, "azure", body.name, body.region, body.cluster_profile,
                     body.confirm_ack,
                     {"tenant_id": body.tenant_id, "client_id": body.client_id,
                      "client_secret": body.client_secret, "subscription_id": body.subscription_id},
                     subscription_id=body.subscription_id)
    return _run_api(run)


@router.post("/gcp")
def provision_gcp(body: GcpProvisionBody, user: CurrentUser = Depends(require_admin),
                  session: Session = Depends(get_session)):
    run = _start_run(user, session, "gcp", body.name, body.region, body.cluster_profile,
                     body.confirm_ack,
                     {"service_account_json": body.service_account_json}, project_id=body.project_id)
    return _run_api(run)


@router.get("")
def list_runs(user: CurrentUser = Depends(require_tenant_scope), session: Session = Depends(get_session)):
    runs = session.exec(select(CloudProvisionRun).where(CloudProvisionRun.tenant_id == user.tenant_id)
                        .order_by(CloudProvisionRun.created_at.desc())).all()
    return [_run_api(r) for r in runs]


@router.get("/{run_id}")
def get_run(run_id: int, user: CurrentUser = Depends(require_tenant_scope),
           session: Session = Depends(get_session)):
    run = session.get(CloudProvisionRun, run_id)
    if run is None or run.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="no such cloud-provision run")
    return _run_api(run)
