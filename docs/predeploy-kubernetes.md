# Pre-deployment guide — Kubernetes (EKS / AKS / GKE / on-prem)

Covers what to have ready **before** `helm install`. For the chart itself, see
`helm/kdb-control-plane/values.yaml` (heavily commented) and `templates/NOTES.txt` (printed after
install). Read [docs/README.md](README.md) first, especially the "which path" section — this chart is
the pilot/longer-lived path, not the sales-demo path; treat the secrets/database decisions here as
non-optional, not shortcuts to skip because it "still needs to work like a demo."

## 1. Cluster prerequisites

- **Kubernetes 1.27+** recommended (the chart uses `autoscaling/v2` for the optional HPA, which needs
  a reasonably current API). Works on EKS, AKS, GKE, or on-prem (k3s/RKE/kubeadm) per `Chart.yaml`'s
  own claim — nothing in the templates is cloud-specific except the `storageClassName` and
  `ingress.className` defaults, both overridden per cloud in the overlay files below.
- **Node capacity**: this chart runs the data plane as plain `Deployment`s (not `StatefulSet`s — a
  deliberate choice, see `helm/kdb-control-plane/values.yaml`'s comments) with `replicas: 1` each, so
  it doesn't need anything exotic, but it does need real capacity: `shardCount: 2` (the default) means
  **10 data-plane pods** (5 per shard) plus `gateway`, `control-api`, `watchdog`, `web-ui`, and
  optionally `ollama` if you deploy it alongside (it isn't in this chart — it's compose-only; for
  Kubernetes, point `nl2q.llmBaseUrl` at a model server you run separately, or leave `llmProvider:
  none`). Budget node capacity accordingly and remember it scales with `shardCount`, not with traffic.
- **A default `StorageClass`** that supports `ReadWriteOnce` PVCs, or set `global.storageClassName`
  explicitly. Each shard requests 3 PVCs (`db-{sid}`, `hdb-{sid}`, `tp-log-{sid}`) sized by
  `dataPlane.dbVolumeSize` / `hdbVolumeSize` / `tpLogVolumeSize` — defaults 20Gi/100Gi/5Gi. `hdb`
  specifically **grows every trading day** the cluster stays up; size it for your actual retention
  window, not the demo default.
- **Pod affinity support** — `idb` and `hdb` pods are affinity-pinned to co-locate with their shard's
  `wdb` pod (they share an RWO PVC). Make sure your node pool isn't so fragmented (e.g. tiny node
  sizes, strict anti-affinity elsewhere) that the scheduler can't satisfy this.

## 2. Who needs cluster access, and with what scope

Two different RBAC concerns, don't conflate them:

- **The chart's own in-cluster RBAC** (`templates/rbac.yaml`) is already scoped for you: a
  namespace-only `Role` granting `control-api` and `watchdog` service accounts get/list/watch/patch on
  Deployments (+ `deployments/scale`) and get/list/watch on Pods + read Pod logs — nothing
  cluster-wide, nothing touching Secrets or ConfigMaps beyond what's already mounted. You don't need
  to change this for a normal install.
- **The human/CI identity running `helm install`** needs its own cluster-admin-adjacent access
  (create namespaces, PVCs, RBAC objects, Secrets, run the pre-install migration Job) — scope this
  with your cluster's normal RBAC/IAM-to-Kubernetes-RBAC mapping:
  - **EKS**: an IAM principal mapped via `aws-auth`/EKS access entries to a Kubernetes `Role`/
    `ClusterRole` with `create`/`get`/`list`/`patch` on the namespace's core resource types, plus
    `rbac.authorization.k8s.io` create/bind (the chart creates Roles and RoleBindings).
  - **AKS**: an Azure AD principal with `Azure Kubernetes Service RBAC Writer` (or a custom role) on
    the specific namespace, if Azure RBAC for Kubernetes authorization is enabled; otherwise the
    equivalent Kubernetes `RoleBinding`.
  - **GKE**: an IAM principal with `roles/container.developer` at minimum (namespace-scoped via
    Kubernetes RBAC on top if you want tighter control than that role alone gives).
  - **On-prem**: whatever your cluster's existing RBAC/SSO-to-RBAC mapping is — the ask is the same:
    namespace-scoped admin, not cluster-admin, for whoever runs `helm install`.

## 3. Secrets — do this before `helm install`, not after

`values.yaml`'s `secrets.*` block ships **insecure dev defaults** (`dev-secret-change-in-deploy`,
etc.) on purpose, so a bare `helm install` never silently looks production-ready. Two ways to fix it,
in order of preference:

1. **`secrets.existingSecret`** (recommended for anything beyond a demo): create a Secret out-of-band
   with your cluster's normal secret-management path (External Secrets Operator, Sealed Secrets, or
   just `kubectl create secret` from a value pulled out of your vault) containing keys
   `ADMIN_USER`, `ADMIN_PASSWORD_HASH`, `JWT_SECRET`, `WATCHDOG_SHARED_SECRET`, `NL2Q_LLM_API_KEY`,
   then set `secrets.existingSecret: <name>` — the chart-managed `Secret` template
   (`templates/secrets.yaml`) simply doesn't render when this is set, so there's no dev-default
   object left lying around to accidentally fall back to.
2. **`--set` at install time**, if you don't have an external-secrets pipeline yet:
   ```bash
   helm install kdb-control-plane . -n kdb-control-plane --create-namespace \
     --set secrets.jwtSecret=$(openssl rand -hex 32) \
     --set secrets.watchdogSharedSecret=$(openssl rand -hex 32) \
     --set secrets.adminPasswordHash='<bcrypt hash - see below>'
   ```
   Never commit these values into a `values-<env>.yaml` file that lands in git — `--set` (or a
   separately-vaulted values file kept out of version control) only.

Generate the bcrypt hash the same way as the VM path:
`python3 -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"`.
And per [docs/README.md](README.md) item 1 — never reuse anything that ever appeared in the repo's
`.env.example`, on a VM deploy or here.

## 4. The KX-X license Secret — required, not optional

Every kdb+ container mounts a Secret named by `kdbx.licenseSecretName` (default `kdbx-license`) at
`/usr/local/kdbx/{q,kc.lic}`. **Pods crash-loop without it** — create it before or immediately after
install:
```bash
kubectl create secret generic kdbx-license -n kdb-control-plane \
  --from-file=q=./q --from-file=kc.lic=./kc.lic
```
This is never chart-managed (licensing terms — same reason the binary isn't bundled anywhere else in
this repo). If you're on the portal-pull path instead of a locally-staged binary, that mechanism is
compose/`fleet_agent`-specific (`kx_installer.py`) — this chart expects a pre-staged Secret, full stop.

## 5. Database — the chart enforces this one for you

`database.url` defaults to SQLite, which works for a first install but **hard-fails** `helm
install`/`upgrade` the moment you also enable `controlApi.autoscaling.enabled` (see
`templates/control-api.yaml`'s guard) — SQLite has no concurrent-writer safety across replicas. Decide
your real database before you need to scale the API tier, not while an install is already failing in
front of someone:
- **EKS** → Amazon RDS (Postgres/MySQL/SQL Server)
- **AKS** → Azure Database for PostgreSQL/MySQL, or Azure SQL Database
- **GKE** → Cloud SQL (Postgres/MySQL/SQL Server)
- **On-prem** → whatever your DBA team already runs

See `control-api/README-database.md` for exact connection-string dialects and what's actually been
tested (Postgres and MySQL end-to-end; SQL Server's driver needs an extra `Dockerfile` step, documented
there). The chart runs `alembic upgrade head` automatically as a `pre-install,pre-upgrade` Helm hook
(`templates/migrate-job.yaml`) — you don't run it manually here, just make sure `database.url` points
at the real thing before the hook fires.

## 6. Ingress and TLS

`ingress.enabled` is `false` by default (port-forward to reach the UI, per `NOTES.txt`). To expose it
properly, set `ingress.className` to your cluster's controller and `ingress.host` to your domain:

| Cloud | `ingress.className` | Typical cert path |
|---|---|---|
| EKS | `alb` (AWS Load Balancer Controller) | ACM certificate on the ALB listener, or cert-manager + Let's Encrypt |
| AKS | `azure-application-gateway` (AGIC) or `nginx` | AGIC + App Gateway managed cert, or cert-manager |
| GKE | `gce` (GKE Ingress) | Google-managed certificate, or cert-manager |
| On-prem | `nginx` (ingress-nginx) or your controller of choice | cert-manager + Let's Encrypt, or your internal CA |

Set `ingress.tls: true` once a certificate is available at the `kdb-control-plane-tls` Secret name the
template expects (`templates/ingress.yaml`) — populate that Secret via cert-manager, your cloud's
managed-cert integration, or manually, depending on which column above you're in. The app already
trusts `X-Forwarded-Proto` and upgrades the metrics WebSocket to `wss://` under HTTPS — no app-side
change needed regardless of which ingress controller terminates TLS.

## 7. NL2Q / LLM provider — decide before install, it's low-stakes either way

`nl2q.llmProvider: none` (the default) uses the query workspace's offline regex generator only — no
model, no key, no outbound calls, works fully air-gapped. If you want the real natural-language-to-q
generation, set `llmProvider` to `anthropic` (+ `NL2Q_LLM_API_KEY` in your secret) or
`openai_compatible` (+ `llmBaseUrl` pointed at a self-hosted Ollama/vLLM/LM Studio endpoint for
air-gapped clusters, or a hosted API). This is the one config surface where getting it wrong just
degrades a nice-to-have feature to its offline fallback — not a launch blocker, revisit any time via
`helm upgrade`.

## 8. Cloud-specific values overlays

Three starting overlays are provided — copy and adjust rather than using as-is, they set the storage
class, ingress class, and image registry pattern per cloud but still need your actual registry path,
domain, and DB connection string filled in:

- `helm/kdb-control-plane/values-aws.yaml`
- `helm/kdb-control-plane/values-azure.yaml`
- `helm/kdb-control-plane/values-gcp.yaml`

```bash
helm install kdb-control-plane . -n kdb-control-plane --create-namespace \
  -f values-aws.yaml \
  --set secrets.jwtSecret=$(openssl rand -hex 32) \
  --set secrets.watchdogSharedSecret=$(openssl rand -hex 32) \
  --set secrets.adminPasswordHash='<bcrypt hash>'
```

## 9. Post-install verification checklist

- [ ] `kubectl get pods -n kdb-control-plane` — every pod `Running`, none crash-looping (a
      crash-loop here is almost always the license Secret missing, or the migration Job failing
      against an unreachable `database.url`).
- [ ] `kubectl logs -n kdb-control-plane job/kdb-control-plane-migrate` — confirms the Alembic
      migration actually ran before assuming the API is healthy.
- [ ] Verified `helm template . -f <your-overlay>.yaml | kubectl apply --dry-run=server -f -` (or
      equivalent) at least once against a real cluster before trusting it in front of a client — the
      root `README.md` is explicit that Helm rendering isn't exercised in CI.
- [ ] Confirmed `secrets.existingSecret` (or `--set`) is actually in effect — `kubectl get secret
      kdb-control-plane-secrets -n kdb-control-plane -o yaml` should not exist at all if you used
      `existingSecret`, or should not contain the literal string `dev-secret-change-in-deploy` if you
      used `--set`.
- [ ] Logged in as both seeded accounts with real passwords; ran the self-healing demo once
      (Topology → kill a process — this now goes through the Kubernetes orchestrator backend instead
      of the Docker socket, same UI, different backend — → Audit log).
- [ ] If autoscaling is enabled: confirmed `database.url` is a real dialect, not SQLite (the chart
      would have refused the install otherwise, but double-check nobody re-ran with `--set
      database.url=sqlite://...` afterward).

## 10. Scaling shard count later

`shardCount` is the single scaling knob (see `values.yaml`'s comments) — `helm upgrade --set
shardCount=N` regenerates every per-shard Deployment, PVC, and the gateway's routing ConfigMap
together, guarded against drift by the same logic `scripts/check_topology_sync.py` checks for the
compose path. Existing shards' data isn't touched; new shards start empty. This is also exactly what
`fleet_agent` does on a tenant's behalf in the hosted multi-tenant path (`AGENT_BACKEND=helm`) — see
[docs/README.md](README.md)'s "which path" table for how that relates to this chart.

## 11. Teardown

```bash
helm uninstall kdb-control-plane -n kdb-control-plane
kubectl delete pvc -n kdb-control-plane -l app.kubernetes.io/instance=kdb-control-plane   # PVCs outlive helm uninstall by design - delete explicitly if you want the data gone
kubectl delete secret kdbx-license -n kdb-control-plane   # if you created it manually
```
PVCs persisting after `helm uninstall` is standard Kubernetes/Helm behavior, not a bug — it's what
lets you `helm install` again without losing the `hdb`. Delete them explicitly once you're actually
done with the data.
