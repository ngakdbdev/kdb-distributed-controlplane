# Pre-deployment guide — GCP (single VM)

Covers what to have ready **before** running `deploy/gcp/01_provision_vm.sh` through
`04_deploy_stack.sh`. For the run-the-scripts quickstart, see `deploy/gcp/README.md`. Read
[docs/README.md](README.md) first — items 1–4 there (secrets, KX licensing, database, DNS/TLS) apply
here too and aren't repeated in full below.

## 1. IAM — least-privilege role for the deploying identity

GCP's own README doesn't enumerate a role list; here's one scoped to exactly what
`01_provision_vm.sh`, `02_configure_networking.sh`, and `99_teardown.sh` do. Grant this as a **custom
role** (or the closest predefined roles below) on the project, not `roles/editor` or `roles/owner`:

| Permission group | Predefined role that covers it | What it's for |
|---|---|---|
| `compute.instances.*`, `compute.disks.*` | `roles/compute.instanceAdmin.v1` | create/delete/describe the VM and its boot disk |
| `compute.firewalls.*` | `roles/compute.securityAdmin` | the three `kdb-allow-*` firewall rules |
| `compute.resourcePolicies.*` | `roles/compute.instanceAdmin.v1` (included) | the COMPACT placement policy |
| `compute.addresses.*` | `roles/compute.networkAdmin` | the static external IP |
| `serviceusage.services.enable` | `roles/serviceusage.serviceUsageAdmin` | `01_provision_vm.sh` runs `gcloud services enable compute.googleapis.com` — only needed once per project, drop this after first run if you want tighter ongoing scope |
| `compute.instances.osLogin` / OS Login IAM | `roles/compute.osLoginUser` (if OS Login is enforced org-wide) | `01_provision_vm.sh` enables OS Login on the instance |

If your org enforces **OS Login** at the org policy level (common), also grant
`roles/compute.osLoginUser` (or `osAdminLogin` if you need sudo) to whoever needs to SSH in for step
3 of the quickstart — without it, `gcloud compute ssh` will fail even though the VM provisioned fine.

## 2. Quotas, billing, and region

- Default machine type is `c3-standard-8` — confirm your project's C3 quota in the target region
  (`gcloud compute regions describe <region> --format="value(quotas)"`, look for `C3_CPUS` or the
  general `CPUS` quota). New/trial projects sometimes cap C3 availability to specific regions only —
  verify `c3-standard-8` is actually orderable in your chosen `GCP_ZONE` before the demo day
  (`gcloud compute machine-types list --zones=<zone> --filter="name=c3-standard-8"`).
- Billing must be enabled on the project (`01_provision_vm.sh` will fail on `enable
  compute.googleapis.com` otherwise). The $300/90-day free-trial credit is enough to cover this whole
  deployment if that's what you're using.
- GCP has **no FPGA-backed VM family** — don't plan a demo around one; see the honest note already in
  `deploy/gcp/README.md`. If a prospect specifically needs FPGA acceleration, that's an AWS F2 or
  on-prem/colo conversation, not a GCP one.

## 3. Network and DNS

- `02_configure_networking.sh` creates three firewall rules scoped to the `kdb-control-plane-demo`
  network tag: `kdb-allow-ssh` (22, `ALLOWED_SSH_CIDR`), `kdb-allow-http` (80, open), and
  `kdb-allow-control-api` (8000, `ALLOWED_ADMIN_CIDR`). Both CIDR vars default open (`0.0.0.0/0`) —
  override them to your office/VPN range before anything but a fully throwaway demo.
- kdb+ IPC ports are never exposed at the firewall — they stay on the Docker bridge network only.
- For HTTPS: reserve the static external IP first (`01_provision_vm.sh` does this), point a DNS **A
  record** at it, *then* run the `deploy/tls` Caddy overlay so the Let's Encrypt HTTP-01 challenge can
  resolve. See `deploy/tls/README.md` — it's host-agnostic, so the same Caddy config that runs on a
  laptop or an EC2 box runs unchanged on a GCE VM.
- If you'd rather terminate TLS at Google's edge instead of Caddy on-box: put the instance behind a
  Global External HTTPS Load Balancer with a Google-managed certificate, forwarding to `web-ui:80`
  on the instance. Not scripted here (the repo's TLS module is Caddy-first and cloud-agnostic by
  design) — a legitimate alternative if your org standardizes on GCLB.

## 4. Database

Default SQLite is fine for a solo demo only. For anything else, provision **Cloud SQL for PostgreSQL**
(or MySQL/SQL Server — see `control-api/README-database.md`), connect via a Cloud SQL Auth Proxy
sidecar or private IP in the same VPC, and set `DATABASE_URL` in `.env` before first boot. Run
`alembic upgrade head` once against it before pointing the control-api at it for real.

## 5. Sizing beyond the default

`c3-standard-8` (8 vCPU / 32 GiB) runs the 2-shard demo topology comfortably — note it has double the
RAM of AWS's default `c7i.2xlarge` for the same vCPU count, which gives more headroom for `ollama`
(the local NL2Q model server) alongside the data plane. Scale roughly linearly with shard count if you
regenerate the topology with more shards. `Tier_1` networking (gVNIC) is already the default — don't
remove it, it's what gives this VM double the normal per-VM bandwidth cap, which matters once feed rate
or subscriber count climbs. Disk: bump `DISK_SIZE_GB` past the default for any session where the `hdb`
will accumulate more than a day or two of history.

## 6. Post-deploy verification checklist

- [ ] `curl http://<external-ip>:8000/health` returns healthy.
- [ ] Logged in as both seeded accounts with real (non-`changeme`) passwords.
- [ ] Enabled `bpipe-sim`/`crims-sim`, watched Metrics move.
- [ ] Ran the self-healing demo once yourself first (Topology → kill → watch recovery → Audit log).
- [ ] If TLS is in scope: verified the cert and the HTTP→HTTPS redirect.
- [ ] Confirmed every `.env` secret was freshly generated, not copied from `.env.example`.

## 7. Cost / teardown

`c3-standard-8` plus a reserved static IP bill continuously. Run `deploy/gcp/99_teardown.sh`
immediately after the demo — it deletes the VM, the static IP, the placement policy, and all three
firewall rules in one pass. If you're on trial credit, note the countdown is calendar days from
signup, not usage — an idle VM burns the same credit as a busy one.
