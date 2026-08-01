# fleet_agent

The process that runs **inside a tenant's own AWS/Azure/GCP/on-prem cluster**
and executes commands from the SaaS control plane. It's how "choose an
environment and create ticker plant components from the UI" actually happens.

## Why an agent at all

The control plane is multi-tenant SaaS; a tenant's data plane runs in *their*
cloud, and we don't get inbound network access to it. So the agent calls out:
it enrolls once with a one-time token, then heartbeats on a loop, pulling
queued commands and reporting results. See `control-api/app/routers/fleet.py`
for the server side.

## What a "provision" command does

When a tenant admin picks their environment + a shard count in the UI, the
control plane queues a `provision` command carrying the **desired topology**
(the canonical `shards_json(N)` from the shared topology module). This agent:

1. reads the current shard count in its cluster,
2. if it already matches, no-ops,
3. otherwise reconciles — `helm upgrade --install ... --set shardCount=N`
   (EKS/AKS/GKE) or regenerates docker-compose via `gen_topology.py` and
   `docker compose up -d` (on-prem/single-box),
4. reports success/failure, which shows up in the UI job list and the Audit tab.

It drives the **same single knob** — `shardCount` — that the whole product is
built around, so it's not a parallel deployment path.

## Layout

- `provisioner.py` — pure reconcile core (no subprocess/network); unit-tested.
- `backends.py` — real `HelmBackend` / `ComposeBackend` executors.
- `control_client.py` — stdlib HTTP client for the fleet protocol.
- `agent.py` — heartbeat loop + command dispatch; unit-tested.
- `config.py` — env config + secret persistence.
- `tests/` — 16 tests, no cluster required (`python -m pytest fleet_agent`).

## Running it

```bash
export CONTROL_PLANE_URL=https://control.example.com/api
export ENROLLMENT_TOKEN=<one-time token from the UI "Register agent" button>
export AGENT_ENVIRONMENT=aws          # informational
export AGENT_BACKEND=helm             # or "compose"
export HELM_RELEASE=kdb-control-plane
export HELM_CHART=helm/kdb-control-plane
python -m fleet_agent
```

On first start it enrolls, stores its secret at `AGENT_SECRET_FILE`
(default `/var/lib/fleet-agent/secret.json`, mode 0600), and re-uses it across
restarts. Deploy it in-cluster with a service account bound to the namespace
so `helm`/`kubectl` use the ambient cluster context — no cloud SDK needed.

## Honest boundary

The reconcile **decisions** are unit-tested; the **execution** (helm/docker)
only works inside a real cluster and isn't exercised in CI. Stand the agent up
against a real EKS/AKS/GKE cluster once before relying on it in a client demo.
The initial cluster/VM bootstrap (and installing this agent) is still the
`deploy/<cloud>/` scripts' job — the agent handles ongoing ticker-plant
provisioning after it's enrolled.
