# Pre-deployment guide — AWS (single VM)

Covers what to have ready **before** running `deploy/aws/01_provision_vm.sh` through
`04_deploy_stack.sh`. For the run-the-scripts quickstart, see `deploy/aws/README.md`. Read
[docs/README.md](README.md) first — items 1–4 there (secrets, KX licensing, database, DNS/TLS) apply
here too and aren't repeated in full below.

## 1. IAM — least-privilege policy for the deploying identity

`deploy/aws/README.md` says the identity needs "EC2 create/terminate, security-group,
placement-group, and EIP permissions" — here's that translated into an actual policy you can attach,
scoped as tightly as the scripts allow (they don't tag-scope resource ARNs before creation, so the
`Resource` blocks below are `*` for the create calls and tag-conditioned for destroy/mutate calls
where AWS supports it):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Ec2InstanceLifecycle",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceStatus",
        "ec2:CreateTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "NetworkingForTheDemoVpc",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateSecurityGroup",
        "ec2:DeleteSecurityGroup",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupIngress",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PlacementGroupForLowLatency",
      "Effect": "Allow",
      "Action": [
        "ec2:CreatePlacementGroup",
        "ec2:DeletePlacementGroup",
        "ec2:DescribePlacementGroups"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ElasticIpForAStablePublicAddress",
      "Effect": "Allow",
      "Action": [
        "ec2:AllocateAddress",
        "ec2:AssociateAddress",
        "ec2:DisassociateAddress",
        "ec2:ReleaseAddress",
        "ec2:DescribeAddresses"
      ],
      "Resource": "*"
    },
    {
      "Sid": "KeyPairForSsh",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateKeyPair",
        "ec2:DeleteKeyPair",
        "ec2:DescribeKeyPairs"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AmiLookupViaSsm",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters"],
      "Resource": "arn:aws:ssm:*::parameter/aws/service/canonical/ubuntu/*"
    }
  ]
}
```

Notes:
- This is EC2 + SSM-read only. It does **not** grant IAM, S3, RDS, or KMS access — add those
  explicitly and separately if you also provision a managed database (§4) or store secrets in
  Secrets Manager, don't fold them into this policy blindly.
- `ec2:RunInstances`/`TerminateInstances` are `Resource: "*"` because the scripts don't pre-create the
  instance ARN to scope to. If your org requires tighter scoping, wrap this in a permissions boundary
  that restricts instance type / tag requirements instead of trying to scope the resource ARN.
- Prefer an SSO/assumed role over a long-lived IAM user access key for whoever runs these scripts.

## 2. Quotas and region

- Default instance type is `c7i.2xlarge` — check your account's running On-Demand vCPU quota for the
  "Standard" (C/M/R) family covers it (`aws service-quotas get-service-quota --service-code ec2
  --quota-code L-1216C47A`). A fresh account's default is usually enough for one `c7i.2xlarge`, but
  confirm before the demo, not during it.
- If you plan to set `ENABLE_FPGA=1`: F2 needs a **separate quota request** (default limit is often
  0), is **Linux-only**, and is only available in a subset of regions (N. Virginia, Oregon, London,
  Frankfurt, Tokyo, Seoul, Sydney, Canada Central at time of writing — verify current availability).
  Request the quota increase days in advance, not the morning of. Re-read `deploy/aws/README.md`'s
  FPGA section before you reach for this flag at all — it provisions an FPGA-*capable* box, nothing
  more; kdb+ never runs on the FPGA without a custom AFI you build yourself.
- Pick `AWS_REGION` close to your demo audience; it's the only region-sensitive input the scripts take.

## 3. Network and DNS

- `deploy/aws/02_configure_networking.sh` opens TCP 22 (SSH), 80 (web UI), and 8000 (control-api
  debug) — 22 and 8000 default to `ALLOWED_SSH_CIDR`/`ALLOWED_ADMIN_CIDR` (`0.0.0.0/0` unless you
  override them; **override them** to your office/VPN CIDR before running it against anything but a
  fully throwaway demo). Port 80 is intentionally open to everyone — it's the UI.
- kdb+ IPC ports (5010–5050) are **never** opened in the security group — deliberate, they stay on the
  Docker bridge network only. Don't add a rule for them.
- If you want HTTPS (recommended beyond a same-day demo): register a DNS **A record** pointing at the
  Elastic IP `01_provision_vm.sh` allocates *before* running `deploy/tls`'s Caddy overlay — Let's
  Encrypt's HTTP-01 challenge needs it resolvable first. See `deploy/tls/README.md`. Once TLS is live
  you can also close port 80 externally at the security-group level down to just what Caddy's
  ACME challenge needs (leave it open — Caddy also uses 80 for the HTTP→HTTPS redirect).
- **Alternative for anything more than a demo**: terminate TLS at an Application Load Balancer with an
  ACM certificate instead of running Caddy on-box — `deploy/tls/README.md` documents this as the
  "recommended at scale" option. No app changes needed either way; it already trusts
  `X-Forwarded-Proto`.

## 4. Database

Default `sqlite:///./data/control_plane.db` (baked into the compose file) is fine for a solo demo.
For anything a second person might touch concurrently, provision **Amazon RDS for PostgreSQL** (or
MySQL/SQL Server — see `control-api/README-database.md` for exact connection-string dialects and what's
actually been tested), put it in the same VPC as the demo instance or peer it, and set `DATABASE_URL`
in `.env` before first boot. Run `alembic upgrade head` once against it (the compose stack does this
automatically for you via the same mechanism the Helm chart uses as a pre-install hook — see
`control-api/README-database.md` for the manual command if you're not using compose).

## 5. Sizing beyond the default

`c7i.2xlarge` (8 vCPU / 16 GiB) comfortably runs the 2-shard demo topology (10 kdb+ processes +
gateway + control-api + watchdog + web-ui + ollama). If you provision more shards
(`scripts/gen_topology.py --shards N` before deploying), scale the instance up roughly linearly — each
additional shard adds 5 more q processes. Disk: default `DISK_SIZE_GB=100` is enough for a demo session;
the `hdb` volume grows every trading day the stack stays up, so bump it (or move to Kubernetes with a
separately-sized `hdb` PVC, see [predeploy-kubernetes.md](predeploy-kubernetes.md)) for anything
long-running.

## 6. Post-deploy verification checklist

- [ ] `curl http://<public-ip>:8000/health` returns healthy (the deploy script already polls this for
      you, but re-check after any manual restart).
- [ ] Logged in as both seeded accounts (`PLATFORM_ADMIN_EMAIL` and `DEMO_TENANT_ADMIN_EMAIL`) with
      the passwords you actually set — not the `changeme` fallback.
- [ ] Enabled `bpipe-sim`/`crims-sim` from the Connectors tab and watched the Metrics tab move.
- [ ] Ran the self-healing demo once yourself (Topology → kill a process → watch it recover → check
      Audit log) before doing it live in front of anyone.
- [ ] If TLS is in scope: confirmed `https://<domain>` loads with a valid cert and `http://` redirects.
- [ ] Confirmed `.env` is **not** the repo's `.env.example` values — every secret was actually
      generated fresh (§1 of [docs/README.md](README.md)).

## 7. Cost / teardown

`c7i.2xlarge` and an Elastic IP bill continuously once allocated. Run `deploy/aws/99_teardown.sh` the
moment you're done — it terminates the instance, releases the EIP, and deletes the security group and
placement group (it deliberately leaves the SSH key pair behind; delete it manually if you're fully
done with this demo identity). If you enabled FPGA, tear that down first and fastest — `f2.6xlarge`
and larger bill significantly more than the CPU-only default.
