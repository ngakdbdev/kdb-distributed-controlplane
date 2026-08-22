"""
cloud_provisioner.py - the credentials-only auto-provisioning path: given
just an AWS/Azure/GCP access key pair, stand up an entire TickHouse cluster
end to end (network, managed Kubernetes, storage, then this platform
itself) with no other configuration required. The option-based counterpart
(routers/tickhouse.py) assumes a cluster - and an agent already enrolled
into it - already exist; this module is what makes that no longer a
prerequisite.

Pipeline, each stage persisted to CloudProvisionRun.status so a caller can
poll progress on something that realistically takes 10-20 minutes end to
end (EKS/AKS/GKE control-plane creation alone is often 10+ minutes - this
is genuinely slow infrastructure, not a slow API call):

  pending -> planning -> applying (terraform init + apply against
  terraform/{provider}/) -> installing (helm install the full
  kdb-control-plane chart onto the cluster terraform just created) ->
  provisioning_tickhouse (a bookkeeping TickHouse row in THIS control
  plane's own DB, so it shows up in the UI like any other) -> complete

Deliberately separate plan()/apply() (same split as fleet_agent/
kx_installer.py, for the same reason): plan() is secret-free and
side-effect-free - it returns the labeled command list with credentials
already stripped, safe to log/return/unit-test. apply() is what actually
runs terraform/helm as subprocesses, with real credentials injected only
via subprocess environment variables (or a 0600 tempfile terraform itself
requires, e.g. GCP's service-account JSON) - never a tfvars file, never a
logged command line. apply() is NOT exercised in CI (same as
kx_installer.install()) since it needs a real terraform binary and a real
cloud account; cloud_provisioner_test.py covers plan() and the tfvars/
credential-shaping logic with everything past subprocess.run mocked out.

SAFETY: this creates real, billed cloud infrastructure. apply() refuses to
run unless the caller already confirmed CONFIRM_PHRASE exactly (enforced in
routers/cloud_provision.py, checked again here defensively) - same
deliberate-friction pattern as alpaca_broker.LIVE_ACK_PHRASE for live
trading. There is no "paper" fallback for this the way live trading has;
the only safe default is refusing to run.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import cloud_credentials

log = logging.getLogger("cloud_provisioner")

REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_DIR = REPO_ROOT / "terraform"
HELM_CHART_DIR = REPO_ROOT / "helm" / "kdb-control-plane"
RUNS_DIR = Path(os.environ.get("CLOUD_PROVISION_RUNS_DIR", "/tmp/vantik-cloud-provision-runs"))

# The exact phrase a caller must supply before apply() will do anything -
# see module docstring. Not a plain "true"/"1": those are too easy to set
# by accident (a stray copy-pasted request body, a UI checkbox someone
# scripted around without reading it).
CONFIRM_PHRASE = "I_UNDERSTAND_THIS_CREATES_BILLED_CLOUD_RESOURCES"

PROVIDERS = ("aws", "azure", "gcp")
CLUSTER_PROFILES = ("ha", "performance", "cost_optimized")

# Which credential fields each provider needs, and which of those are
# secret (never appear in plan(), always redacted from any captured
# output). Field names match terraform's own provider auth env vars one
# level down (see _credential_env below) so there's exactly one place that
# maps "form field" -> "env var terraform/the cloud SDK actually reads".
CREDENTIAL_FIELDS = {
    # access_key_id/tenant_id/client_id/subscription_id aren't cryptographic
    # secrets by themselves (AWS's own docs treat an access key ID as an
    # identifier, not a secret - it can't authenticate without its paired
    # secret key) - but they're still account-identifying, never stored in
    # plaintext anywhere else in this codebase (only inside
    # credentials_encrypted), and redacting them from stored logs/errors
    # costs nothing real, so every field here is treated as "secret" for
    # redaction purposes, not just the cryptographically-sensitive ones.
    "aws": {"required": ["access_key_id", "secret_access_key"],
           "secret": ["access_key_id", "secret_access_key"]},
    "azure": {"required": ["tenant_id", "client_id", "client_secret", "subscription_id"],
             "secret": ["tenant_id", "client_id", "client_secret", "subscription_id"]},
    "gcp": {"required": ["service_account_json"], "secret": ["service_account_json"]},
}


class ProvisionError(RuntimeError):
    pass


def validate_credentials(provider: str, creds: dict) -> list:
    """Missing-field problems, checked WITHOUT touching the values
    themselves beyond presence - mirrors fleet_agent/kx_installer.py's own
    preflight() shape."""
    if provider not in CREDENTIAL_FIELDS:
        return [f"unknown provider '{provider}' (use one of {', '.join(PROVIDERS)})"]
    problems = []
    for field_name in CREDENTIAL_FIELDS[provider]["required"]:
        if not creds.get(field_name):
            problems.append(f"missing '{field_name}'")
    if provider == "gcp" and creds.get("service_account_json"):
        try:
            parsed = json.loads(creds["service_account_json"])
            if parsed.get("type") != "service_account":
                problems.append("service_account_json does not look like a GCP service account key "
                                "(missing/wrong 'type' field)")
        except (ValueError, TypeError):
            problems.append("service_account_json is not valid JSON")
    return problems


@dataclass
class ProvisionRequest:
    """Non-secret shape of one auto-provision request - everything
    CloudProvisionRun itself stores as plain columns. Credentials travel
    separately (see run() below) so this dataclass is safe to log/repr."""
    tenant_id: int
    name: str
    provider: str
    region: str                    # AWS/GCP region, or Azure location
    cluster_profile: str = "ha"
    project_id: str = ""           # gcp
    subscription_id: str = ""      # azure


def _run_dir(run_id: int) -> Path:
    return RUNS_DIR / f"run-{run_id}"


def plan(req: ProvisionRequest) -> list:
    """Ordered (label, argv) steps, credential-free - argv uses the same
    placeholder-substitution convention as kx_installer.py's plan()
    (env var NAMES only, e.g. "$AWS_ACCESS_KEY_ID", never a value)."""
    if req.provider not in PROVIDERS:
        raise ProvisionError(f"unknown provider '{req.provider}'")
    if req.cluster_profile not in CLUSTER_PROFILES:
        raise ProvisionError(f"unknown cluster_profile '{req.cluster_profile}' "
                             f"(use one of {', '.join(CLUSTER_PROFILES)})")
    tf_dir = str(_run_dir(0) / "terraform")  # id filled in by caller when it exists; label-only here
    steps = [
        ("copy terraform module", ["cp", "-r", str(TERRAFORM_DIR / req.provider), tf_dir]),
        ("write terraform.tfvars", ["write", f"{tf_dir}/terraform.tfvars"]),
        ("terraform init", ["terraform", "-chdir", tf_dir, "init", "-input=false"]),
        ("terraform apply", ["terraform", "-chdir", tf_dir, "apply", "-auto-approve", "-input=false"]),
        ("terraform output", ["terraform", "-chdir", tf_dir, "output", "-json"]),
        ("configure kubectl", ["<terraform output configure_kubectl command>"]),
        ("helm install", ["helm", "upgrade", "--install", req.name, str(HELM_CHART_DIR),
                          "--namespace", "kdb-control-plane", "--create-namespace"]),
        ("create kdbx-license secret", ["kubectl", "create", "secret", "generic", "kdbx-license",
                                        "--from-literal=KX_BEARER_TOKEN=$KX_BEARER_TOKEN",
                                        "--from-literal=KDB_LICENSE_B64=$KDB_LICENSE_B64"]),
    ]
    return steps


def _tfvars_text(req: ProvisionRequest) -> str:
    """Non-secret .tfvars content - credentials are NEVER written here,
    only via subprocess env (see _credential_env). Matches each module's
    own variables.tf field names exactly (see terraform/{provider}/
    variables.tf) - aws/gcp use `region`, azure uses `location`."""
    lines = [f'environment = "{req.name}"', f'cluster_profile = "{req.cluster_profile}"']
    if req.provider in ("aws", "gcp"):
        lines.append(f'region = "{req.region}"')
    else:
        lines.append(f'location = "{req.region}"')
    if req.provider == "gcp":
        lines.append(f'project_id = "{req.project_id}"')
    return "\n".join(lines) + "\n"


def _credential_env(provider: str, creds: dict) -> dict:
    """Real credential values, mapped to the exact env vars terraform's own
    provider blocks read (see terraform/{provider}/versions.tf's provider
    block - none of these are custom to this module, they're each cloud
    Terraform provider's own documented auth env vars). Returned dict is
    merged into the subprocess's environment ONLY, never written to disk
    as a tfvars file, never appears in a logged argv."""
    if provider == "aws":
        return {
            "AWS_ACCESS_KEY_ID": creds["access_key_id"],
            "AWS_SECRET_ACCESS_KEY": creds["secret_access_key"],
        }
    if provider == "azure":
        return {
            "ARM_TENANT_ID": creds["tenant_id"],
            "ARM_CLIENT_ID": creds["client_id"],
            "ARM_CLIENT_SECRET": creds["client_secret"],
            "ARM_SUBSCRIPTION_ID": creds["subscription_id"],
        }
    if provider == "gcp":
        # GCP's terraform provider wants a FILE path, not inline JSON - the
        # only case here that needs a tempfile rather than a bare env var.
        # Caller (run()) is responsible for writing it 0600 and removing it
        # in a finally block; this function only names the env var.
        return {"GOOGLE_APPLICATION_CREDENTIALS": creds["_service_account_json_path"]}
    raise ProvisionError(f"unknown provider '{provider}'")


def _secret_values(provider: str, creds: dict) -> list:
    """The actual secret string values for this provider, for
    cloud_credentials.redact() to scrub out of any captured subprocess
    output before it's persisted to CloudProvisionRun.log_tail/error_detail
    or returned by any API response."""
    return [creds[f] for f in CREDENTIAL_FIELDS[provider]["secret"] if creds.get(f)]


@dataclass
class StageResult:
    ok: bool
    label: str
    output: str = ""


def _run_step(argv: list, cwd: Optional[str], env: dict, secret_values: list,
              timeout: int = 1800) -> StageResult:
    full_env = {**os.environ, **env}
    try:
        proc = subprocess.run(argv, cwd=cwd, env=full_env, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return StageResult(ok=False, label=argv[0],
                           output=cloud_credentials.redact(f"timed out after {timeout}s", secret_values))
    combined = (proc.stdout or "") + (proc.stderr or "")
    combined = cloud_credentials.redact(combined, secret_values)
    return StageResult(ok=proc.returncode == 0, label=argv[0], output=combined[-4000:])


def run(run_id: int, req: ProvisionRequest, creds: dict, confirm_ack: str,
       on_progress) -> dict:
    """The real, side-effecting pipeline. `on_progress(status, detail,
    log_chunk)` is called after every stage so the caller (routers/
    cloud_provision.py, running this in a background thread) can persist
    progress to the CloudProvisionRun row as it happens, not just at the
    end. Returns {"ok": bool, "terraform_outputs": dict, "error": str}.

    Never call this directly from a request handler - it blocks for as
    long as terraform/helm take (potentially 10-20+ minutes). See
    routers/cloud_provision.py for the background-thread wrapper.
    """
    if confirm_ack != CONFIRM_PHRASE:
        raise ProvisionError(
            f"confirm_ack must exactly equal cloud_provisioner.CONFIRM_PHRASE "
            f"('{CONFIRM_PHRASE}') - refusing to create real, billed cloud resources "
            f"without explicit confirmation")
    problems = validate_credentials(req.provider, creds)
    if problems:
        raise ProvisionError("invalid credentials: " + "; ".join(problems))

    secret_values = _secret_values(req.provider, creds)
    run_dir = _run_dir(run_id)
    tf_dir = run_dir / "terraform"
    gcp_sa_path = None

    try:
        run_dir.mkdir(parents=True, exist_ok=True)

        on_progress("planning", "copying terraform module", "")
        shutil.copytree(TERRAFORM_DIR / req.provider, tf_dir)
        (tf_dir / "terraform.tfvars").write_text(_tfvars_text(req))

        env = {}
        if req.provider == "gcp":
            fd, gcp_sa_path = tempfile.mkstemp(prefix="gcp-sa-", suffix=".json", dir=str(run_dir))
            os.chmod(gcp_sa_path, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(creds["service_account_json"])
            creds = {**creds, "_service_account_json_path": gcp_sa_path}
        env = _credential_env(req.provider, creds)

        on_progress("applying", "terraform init", "")
        init = _run_step(["terraform", f"-chdir={tf_dir}", "init", "-input=false"],
                         cwd=None, env=env, secret_values=secret_values, timeout=300)
        on_progress("applying", "terraform init", init.output)
        if not init.ok:
            return {"ok": False, "terraform_outputs": {}, "error": f"terraform init failed:\n{init.output}"}

        on_progress("applying", "terraform apply (this can take 10-20+ minutes for a managed "
                                "Kubernetes control plane)", "")
        apply_result = _run_step(["terraform", f"-chdir={tf_dir}", "apply", "-auto-approve", "-input=false"],
                                 cwd=None, env=env, secret_values=secret_values, timeout=1800)
        on_progress("applying", "terraform apply", apply_result.output)
        if not apply_result.ok:
            return {"ok": False, "terraform_outputs": {},
                    "error": f"terraform apply failed:\n{apply_result.output}"}

        outputs_result = _run_step(["terraform", f"-chdir={tf_dir}", "output", "-json"],
                                   cwd=None, env=env, secret_values=secret_values, timeout=60)
        try:
            tf_outputs = {k: v.get("value") for k, v in json.loads(outputs_result.output).items()}
        except (ValueError, AttributeError):
            tf_outputs = {}

        on_progress("installing", "configuring kubectl for the new cluster", "")
        configure_cmd = tf_outputs.get("configure_kubectl")
        if configure_cmd:
            kubeconfig_env = {**env, "KUBECONFIG": str(run_dir / "kubeconfig")}
            configure = _run_step(list(configure_cmd) if isinstance(configure_cmd, list)
                                  else configure_cmd.split(), cwd=str(tf_dir),
                                  env=kubeconfig_env, secret_values=secret_values, timeout=300)
            on_progress("installing", "configure kubectl", configure.output)
            if not configure.ok:
                return {"ok": False, "terraform_outputs": tf_outputs,
                        "error": f"kubectl configuration failed:\n{configure.output}"}

        on_progress("installing", "installing Vantik onto the new cluster", "")
        kubeconfig_path = str(run_dir / "kubeconfig")
        helm_env = {**env, "KUBECONFIG": kubeconfig_path}
        jwt_secret = secrets.token_hex(32)
        watchdog_secret = secrets.token_hex(32)
        helm_cmd = [
            "helm", "upgrade", "--install", req.name, str(HELM_CHART_DIR),
            "--namespace", "kdb-control-plane", "--create-namespace",
            "--set", f"secrets.jwtSecret={jwt_secret}",
            "--set", f"secrets.watchdogSharedSecret={watchdog_secret}",
        ]
        helm_result = _run_step(helm_cmd, cwd=None, env=helm_env,
                                secret_values=secret_values + [jwt_secret, watchdog_secret], timeout=600)
        on_progress("installing", "helm install", helm_result.output)
        if not helm_result.ok:
            return {"ok": False, "terraform_outputs": tf_outputs,
                    "error": f"helm install failed:\n{helm_result.output}"}

        kx_token = os.environ.get("KX_BEARER_TOKEN", "")
        kx_license = os.environ.get("KDB_LICENSE_B64", "")
        if kx_token and kx_license:
            on_progress("installing", "creating kdbx-license secret on the new cluster", "")
            secret_cmd = ["kubectl", "create", "secret", "generic", "kdbx-license",
                         "-n", "kdb-control-plane",
                         f"--from-literal=KX_BEARER_TOKEN={kx_token}",
                         f"--from-literal=KDB_LICENSE_B64={kx_license}",
                         "--dry-run=client", "-o", "yaml"]
            # dry-run + apply, not a bare `create`, so re-running this (e.g. a
            # retried/updated provision) doesn't fail with "already exists"
            dry = _run_step(secret_cmd, cwd=None, env={**env, "KUBECONFIG": kubeconfig_path},
                            secret_values=secret_values + [kx_token, kx_license], timeout=60)
            if dry.ok:
                apply_secret = subprocess.run(
                    ["kubectl", "apply", "-f", "-"], input=dry.output,
                    env={**os.environ, **env, "KUBECONFIG": kubeconfig_path},
                    capture_output=True, text=True, timeout=60)
                on_progress("installing", "apply kdbx-license secret",
                           cloud_credentials.redact((apply_secret.stdout or "") + (apply_secret.stderr or ""),
                                                    secret_values + [kx_token, kx_license]))
        else:
            on_progress("installing",
                       "KX_BEARER_TOKEN/KDB_LICENSE_B64 not set on this control plane - "
                       "skipping automatic kdbx-license secret creation; create it manually "
                       "before the kdb+ pods on the new cluster will start (see "
                       "predeploy-kubernetes.md section 4)", "")

        on_progress("complete", "cluster created and Vantik installed", "")
        return {"ok": True, "terraform_outputs": tf_outputs, "error": ""}

    except Exception as exc:  # noqa: BLE001 - this is a background job's top-level boundary
        detail = cloud_credentials.redact(str(exc), secret_values)
        log.exception("cloud_provisioner.run failed for run_id=%s", run_id)
        return {"ok": False, "terraform_outputs": {}, "error": detail}
    finally:
        if gcp_sa_path and os.path.exists(gcp_sa_path):
            os.remove(gcp_sa_path)
