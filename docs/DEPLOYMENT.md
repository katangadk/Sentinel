# Same-origin HTTPS deployment

This is a concrete single-host production template, not a deployed service. You supply a server, DNS hostname and server access. Keep the protected laptop out of the server role when you need monitoring during its sleep/disconnection. Back up the private SQLite database and VAPID key securely.

## Host preparation

Install Python 3.11+, Node 22.9+/24, Git/build tooling and Caddy from your distribution/official packages. Configure your DNS A/AAAA records to point at the server and allow inbound TCP 80/443 on this server only. Keep 3000/8000 bound to 127.0.0.1. The laptop only needs outbound TCP 443.

Copy this checkout into `/opt/sentinel`, without `.env`, local databases, config, credentials, `.venv` or platform-specific build caches. Then on the server:

```bash
sudo useradd --system --home /opt/sentinel --shell /usr/sbin/nologin sentinel
sudo install -d -o sentinel -g sentinel -m 700 /var/lib/sentinel
sudo install -d -o root -g sentinel -m 750 /etc/sentinel
sudo chown -R sentinel:sentinel /opt/sentinel
cd /opt/sentinel
sudo -u sentinel python3 -m venv .venv
sudo -u sentinel .venv/bin/pip install -r backend/requirements.lock
sudo -u sentinel npm --prefix frontend ci
sudo -u sentinel env NEXT_TELEMETRY_DISABLED=1 npm --prefix frontend run build
sudo -u sentinel .venv/bin/python scripts/generate-vapid.py /var/lib/sentinel/vapid
```

Protect `/etc/sentinel/backend.env` with `sudoedit` and `sudo chmod 600`. It is read by systemd before dropping privileges:

```text
SENTINEL_DEVELOPMENT=false
SENTINEL_PUBLIC_ORIGIN=https://sentinel.your-actual-domain.example
SENTINEL_DATABASE_URL=sqlite:////var/lib/sentinel/sentinel.db
SENTINEL_VAPID_PRIVATE_KEY=/var/lib/sentinel/vapid/private.pem
SENTINEL_VAPID_PUBLIC_KEY=<base64url contents of /var/lib/sentinel/vapid/public.txt>
SENTINEL_VAPID_SUBJECT=mailto:your-real-address@example.com
GOOGLE_GEOLOCATION_API_KEY=<your backend-only restricted server key>
SENTINEL_GOOGLE_DAILY_LIMIT=144
SENTINEL_GOOGLE_LOCATION_TTL_SECONDS=86400
SENTINEL_GOOGLE_ACCURACY_CACHE_AUTHORIZED=false
```

No quotes or shell substitutions are needed in the systemd environment file. Substitute real values; do not paste the angle-bracket placeholder. The frontend calls relative `/api` paths; Caddy routes them straight to FastAPI and keeps the cookies same-origin.

Create your owner using the production database as the service user:

```bash
sudo -u sentinel env PYTHONPATH=/opt/sentinel/backend SENTINEL_DATABASE_URL=sqlite:////var/lib/sentinel/sentinel.db /opt/sentinel/.venv/bin/python -m app.cli create-owner yourname
```

## Install services and TLS proxy

```bash
sudo install -m 644 deploy/sentinel-backend.service /etc/systemd/system/sentinel-backend.service
sudo install -m 644 deploy/sentinel-frontend.service /etc/systemd/system/sentinel-frontend.service
sudo install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile
sudoedit /etc/caddy/Caddyfile
```

Replace `sentinel.example.com` with exactly the hostname in `SENTINEL_PUBLIC_ORIGIN`. Keep the API and frontend `handle` blocks. Caddy obtains and renews certificates for a reachable public hostname. Validate before enabling:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now sentinel-backend sentinel-frontend
sudo systemctl reload caddy
curl --fail https://sentinel.your-actual-domain.example/api/health
```

Expected response: `{"status":"ok","version":"1.0.0"}`. Direct HTTP to the API fails in production by design. Uvicorn trusts forwarded headers only from the local proxy; do not replace the allowlist with `*`. Run **one** worker: it owns the durable push outbox. Template systemd write access is limited to `/var/lib/sentinel` and Next's `.next` cache respectively.

Audit/alerts/commands are retained for 30 days by the background worker. Latest Google coordinates expire within 24 hours; backups must obey the same provider retention policy. SQLite contains private location and push subscription data; maintain mode 0600 and private directory permissions. Sentinel request URLs contain no secrets. The outbound Google API requires a key query parameter; its HTTPX request logging is suppressed. Never enable wire/body logging or trace those requests. API access logging is disabled and the Caddy template does not enable it. Never expose docs, debug mode or `SENTINEL_DEVELOPMENT=true` publicly. See [Google configuration, display and storage restrictions](GOOGLE_LOCATION.md).

## Pair and accept

On your smartphone using mobile data, open the HTTPS hostname, sign in and generate a pairing token. On the laptop connected through Wi-Fi:

```bash
./agent/target/release/sentinel-agent enroll --server https://sentinel.your-actual-domain.example --location
./scripts/install-agent.sh
```

Optionally add `--ip-fallback` after considering its privacy limitations. Complete [the manual cross-network test](MANUAL_TESTS.md). The localhost smoke test does not establish cross-network access. Never forward ports to the laptop or run a publicly reachable agent listener.

Inspect service errors on the server with `sudo journalctl -u sentinel-backend -u sentinel-frontend --since '10 minutes ago' --no-pager`. For updates, stop services, back up the SQLite database consistently, install dependencies/build, and restart. V1 supports fresh database creation; schema changes in later releases require migrations. Do not delete the production database to fix a build or configuration error.
