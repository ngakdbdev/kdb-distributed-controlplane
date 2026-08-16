# Pre-deployment guide — Azure (single VM)

Covers what to have ready **before** running `deploy/azure/01_provision_vm.sh` through
`04_deploy_stack.sh`. For the run-the-scripts quickstart, see `deploy/azure/README.md`. Read
[docs/README.md](README.md) first — items 1–4 there (secrets, KX licensing, database, DNS/TLS) apply
here too and aren't repeated in full below.

## 1. RBAC — least-privilege role for the deploying identity

Assign a **custom role** scoped to the resource group the scripts create (`AZURE_RG`, default
`kdb-control-plane-demo-rg`), rather than `Contributor` on the whole subscription:

```json
{
  "Name": "kdb-control-plane-demo-deployer",
  "IsCustom": true,
  "Description": "Least-privilege role for deploy/azure/*.sh - VM, networking, NSG only.",
  "Actions": [
    "Microsoft.Resources/subscriptions/resourceGroups/write",
    "Microsoft.Resources/subscriptions/resourceGroups/delete",
    "Microsoft.Compute/virtualMachines/*",
    "Microsoft.Compute/disks/*",
    "Microsoft.Compute/proximityPlacementGroups/*",
    "Microsoft.Network/networkInterfaces/*",
    "Microsoft.Network/networkSecurityGroups/*",
    "Microsoft.Network/publicIPAddresses/*",
    "Microsoft.Network/virtualNetworks/read",
    "Microsoft.Network/virtualNetworks/subnets/join/action"
  ],
  "NotActions": [],
  "AssignableScopes": ["/subscriptions/<your-subscription-id>"]
}
```

Notes:
- `01_provision_vm.sh` auto-generates SSH keys and creates the resource group itself — the role above
  covers both.
- `99_teardown.sh` does `az group delete` on the whole resource group in one call, which is why
  `resourceGroups/delete` is in the list — make sure whoever runs teardown is trusted with that, since
  it takes out everything in the RG, not just what these scripts created (a reason to keep this demo
  in its own dedicated resource group and never add unrelated resources to it).
- If your org enforces Azure AD Conditional Access or PIM for `az login`, account for the extra
  approval step in your demo-day timeline — it's easy to be blocked at `az login` five minutes before
  a call.

## 2. Quotas and region

- Default VM size is `Standard_D8s_v5` (8 vCPU / 32 GiB, accelerated-networking capable). Check your
  subscription's regional vCPU quota for the Dsv5 family (`az vm list-usage --location <region> -o
  table | grep Dsv5`). New/trial subscriptions sometimes start at very low regional quotas.
- If you want the highest single-core clock instead (README suggests this for the most latency-
  sensitive demos), use an `Fsv2` size instead via `VM_SIZE=Standard_F16s_v2` — check that family's
  quota separately, it's not shared with Dsv5.
- **No FPGA path here, on purpose.** Azure's NP-series (the only FPGA family) is being wound down:
  new reserved-instance purchases ended April 2026, Microsoft advises against deploying new NP VMs,
  the attestation service needed to run a bitstream on one closed to new sign-ups mid-2026, and the
  family fully retires May 31 2027. Don't build a demo around it — see `deploy/azure/README.md`'s
  FPGA section for the full honest note. If a prospect needs FPGA acceleration, point them at AWS F2
  or an on-prem/colo conversation instead.

## 3. Network and DNS

- `02_configure_networking.sh` operates on the NSG `az vm create` auto-creates (`<vmname>NSG`). It
  optionally restricts SSH to `ALLOWED_SSH_CIDR` — if you don't set it, `az vm create`'s **default SSH
  rule is left open to the internet**. Set `ALLOWED_SSH_CIDR` explicitly before running this against
  anything but a fully throwaway box. The control-api port (8000) defaults `ALLOWED_ADMIN_CIDR` to
  `*` — override this too.
- kdb+ IPC ports are never opened at the NSG level — stay on the Docker bridge network only.
- For HTTPS: `01_provision_vm.sh` allocates a Standard SKU public IP. Point a DNS **A record** at it,
  then run the `deploy/tls` Caddy overlay (`deploy/tls/README.md`) — it's the same config used on the
  other two clouds, host-agnostic by design.
- Alternative: put **Azure Application Gateway** or **Azure Front Door** in front with a managed
  certificate instead of Caddy on-box, forwarding to the VM's `web-ui:80`. Not scripted in this repo
  (the TLS module standardizes on Caddy across all three clouds) — a legitimate substitution if your
  org already standardizes on App Gateway.

## 4. Database

Default SQLite is fine for a solo demo only. For anything else, provision **Azure Database for
PostgreSQL** (or MySQL, or **Azure SQL Database** for the SQL Server dialect — see
`control-api/README-database.md` for exact connection strings and what's actually been tested), reach
it via private endpoint or VNet integration from the demo VM's VNet, and set `DATABASE_URL` in `.env`
before first boot. Run `alembic upgrade head` once against it before relying on it.

## 5. Sizing beyond the default

`Standard_D8s_v5` (8 vCPU / 32 GiB) comfortably runs the 2-shard demo topology with headroom for
`ollama`. Accelerated networking (SR-IOV) and the proximity placement group are both defaults — don't
disable either, they're what keep inter-process latency low on a single box. Scale the VM size up
roughly linearly if you regenerate the topology with more shards. Bump the OS disk size for any
session where the `hdb` will accumulate more than a day or two of history — the default is sized for
a demo, not a pilot.

Going the other direction — a free/trial subscription can't fit any of the above. `01_provision_vm.sh`
and `04_deploy_stack.sh` detect that automatically and fall back to a 1-shard, ollama-off topology on a
`Standard_B1s`; see [deploy/azure/README.md](../deploy/azure/README.md#deploying-on-a-free-tier--brand-new-azure-subscription).

## 6. Post-deploy verification checklist

- [ ] `curl http://<public-ip>:8000/health` returns healthy.
- [ ] Logged in as both seeded accounts with real (non-`changeme`) passwords.
- [ ] Enabled `bpipe-sim`/`crims-sim`, watched Metrics move.
- [ ] Ran the self-healing demo once yourself first (Topology → kill → watch recovery → Audit log).
- [ ] If TLS is in scope: verified the cert and the HTTP→HTTPS redirect.
- [ ] Confirmed every `.env` secret was freshly generated, not copied from `.env.example`.
- [ ] Double-checked the NSG's default SSH rule was actually restricted — it's open by default unless
      you set `ALLOWED_SSH_CIDR` before provisioning.

## 7. Cost / teardown

`Standard_D8s_v5` plus a Standard public IP bill continuously. Run `deploy/azure/99_teardown.sh`
immediately after — it's a single `az group delete`, which removes the VM, disk, NIC, public IP, NSG,
and proximity placement group together since everything lives in one resource group. That's also why
this demo should stay in its **own** resource group: a group-delete teardown is only safe to run
unattended if nothing else was ever added to it.
