# HTTPS for TickForge

Serve TickForge over HTTPS at your own domain (e.g. `tickforge.qbytecomputing.com`),
with plain HTTP redirected to HTTPS. Caddy sits in front of the stack as the only
public entrypoint: it terminates TLS, auto-obtains and renews a Let's Encrypt
certificate, and proxies inward to the app. The app's plain-HTTP port is no longer
published to the host.

This runs identically on a single VM today and on an AWS EC2 instance later — the
config is host-agnostic.

## On your VM (now)

1. **DNS** — at whoever hosts DNS for `qbytecomputing.com`, add an **A record**:
   `tickforge` → your VM's public IP. (Confirm with `dig +short tickforge.qbytecomputing.com`.)
2. **Firewall** — open inbound **TCP 80 and 443** to the VM.
3. **Config** — in `.env`:
   ```
   TICKFORGE_DOMAIN=tickforge.qbytecomputing.com
   ACME_EMAIL=admin@qbytecomputing.com          # for renewal notices
   PUBLIC_BASE_URL=https://tickforge.qbytecomputing.com
   ```
4. **Run**:
   ```
   docker compose -f docker-compose.yml -f deploy/tls/docker-compose.tls.yml up -d --build
   ```
   Caddy provisions the certificate on first start (needs DNS resolving + port 80
   reachable). Then open `https://tickforge.qbytecomputing.com`; `http://…`
   redirects to it.

Port 80 must stay open even though the app is HTTPS-only — Let's Encrypt uses it
for the certificate challenge, and Caddy uses it to issue the redirect.

## Local HTTPS testing (no public domain)

To exercise the HTTPS chain on your own machine before pointing real DNS at a
server — Caddy issues a self-signed cert from its internal CA, so no Let's
Encrypt and no public reachability are needed.

1. Point the domain at your machine. Add to your hosts file
   (`C:\Windows\System32\drivers\etc\hosts` as Administrator, or
   `/etc/hosts`):
   ```
   127.0.0.1 tickforge.qbytecomputing.com
   ```
2. Bring down the Let's Encrypt overlay if it's running (it will be stuck
   retrying a cert it can't get), then start the local one:
   ```
   docker compose -f docker-compose.yml -f deploy/tls/docker-compose.tls.yml down
   docker compose -f docker-compose.yml -f deploy/tls/docker-compose.local-tls.yml up -d --build
   ```
3. Open `https://tickforge.qbytecomputing.com` and accept the browser's
   "not trusted" warning — expected for a self-signed cert. (To remove the
   warning, export and trust Caddy's root CA from the `caddy-local-data` volume.)

This is only for local testing. On a real host use `docker-compose.tls.yml`
(Let's Encrypt) with public DNS pointing at the machine.

## On AWS (later) — two options, no app changes

- **Keep this setup on EC2.** Point an Elastic IP (and the DNS record) at the
  instance, open 80/443 in the security group, run the same compose command.
  Identical to the VM path.
- **Terminate TLS at an ALB (recommended at scale).** Request a free public
  certificate for the domain in **AWS Certificate Manager**, attach it to an
  Application Load Balancer's HTTPS (443) listener, add an HTTP (80) listener that
  redirects to 443, and forward to the instance/target group on port 80 → `web-ui`.
  Then you can drop the Caddy overlay and just publish `web-ui` on 80 internally.
  ACM renews the cert automatically. DNS becomes a CNAME/alias to the ALB.

Either way the app is unchanged: it already trusts `X-Forwarded-Proto` (uvicorn
`--proxy-headers`, nginx forwards it), the live-metrics socket uses `wss://` on
HTTPS, and Secure cookies switch on when `PUBLIC_BASE_URL` is `https`.

## Bring your own certificate (corporate CA)

If you must use a certificate from your own CA instead of Let's Encrypt, mount the
cert + key into the Caddy container and replace the site block in `Caddyfile`:

```
{$TICKFORGE_DOMAIN} {
	tls /etc/caddy/cert.pem /etc/caddy/key.pem
	reverse_proxy web-ui:80
}
```

Never commit the private key — mount it as a secret/file on the host.

## What stays private

Only Caddy's 80/443 are exposed. `control-api`, `gateway`, the kdb processes, and
the databases stay on the internal Docker network with no host ports. Keep it that
way in any environment — nothing but the edge should be publicly reachable.
