# Sentinel 1.0

A personal Kali Linux/GNOME security agent and mobile dashboard: last-known approximate location, online monitoring, remote **screen lock**, and security alerts. The laptop makes outbound HTTPS requests, so it and your phone can use different networks without laptop port forwarding.

Software is implemented in this repository. Automated checks do not establish real-world GNOME locking, GeoClue coverage, phone push delivery or cross-network access. See [actual verification results](docs/VERIFICATION.md) and [manual acceptance tests](docs/MANUAL_TESTS.md).

## Requirements and installation

On Kali, install Python 3.11+ with venv support, a maintained Node.js release (22.9+; tested on Node 24), Rust stable 1.85+, a C compiler/linker, and GeoClue2. GNOME is the supported desktop. First inspect the installed versions:

```bash
sudo apt update
sudo apt install -y build-essential pkg-config python3 python3-venv python3-pip nodejs npm geoclue-2.0
python3 --version
node --version
```

If Rust is absent, install rustup using the official installer (review it before running):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/sentinel-rustup.sh
less /tmp/sentinel-rustup.sh
sh /tmp/sentinel-rustup.sh -y --profile minimal
source "$HOME/.cargo/env"
rustc --version
```

From this repository's root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
cp .env.example .env
chmod 600 .env
set -a
source .env
set +a
cd frontend
npm ci
npm run build
cd ..
cargo build --release --locked --manifest-path agent/Cargo.toml
```

`requirements.lock` records the Python versions tested here; compiled wheels are platform/Python dependent. The Rust binary dynamically needs only normal Linux runtime libraries and uses Rustls rather than OpenSSL for HTTP. `Cargo.lock` and `frontend/package-lock.json` are tracked for reproducible dependency selection.

## Local development: backend, frontend and pairing

`.env.example` is explicitly **loopback development only**. Use `http://localhost:3100` consistently because Origin and cookie rules are deliberate; `127.0.0.1:3100` is a different origin. Both servers bind to loopback; local defaults are port 3100 for the PWA and 8100 for the API, avoiding occupied development ports on this machine. Change `SENTINEL_API_PORT`, `SENTINEL_FRONTEND_PORT`, `SENTINEL_BACKEND_URL` and `SENTINEL_PUBLIC_ORIGIN` together if needed, then rebuild the frontend with those environment variables loaded.

Create an owner (password prompted twice; no public registration):

```bash
set -a
source .env
set +a
PYTHONPATH=backend .venv/bin/python -m app.cli create-owner yourname
```

Run the servers in two terminals from the repository root:

```bash
./scripts/run-backend.sh
```

```bash
./scripts/run-frontend.sh
```

Open `http://localhost:3100`, sign in, and generate a pairing token. It is valid once, for 10 minutes. On the laptop, enroll and paste the token at the hidden prompt:

```bash
./agent/target/release/sentinel-agent enroll --server http://127.0.0.1:8100 --development
./agent/target/release/sentinel-agent run --once
./agent/target/release/sentinel-agent diagnose
```

This enrollment collects **no location**. For real deployment use an HTTPS origin and omit `--development`. To opt into GeoClue add `--location`; to separately consent to sending your public IP to ipapi.co add `--ip-fallback`. Either option can be changed later in the private config, followed by a service restart. The config path is `~/.config/sentinel/config.json` (0600); never share its `credential`. Failed pairing responses that are lost can leave an enrolled server record: revoke it in the dashboard and generate a new pairing token to recover.

Install the background user service after enrollment:

```bash
./scripts/install-agent.sh
systemctl --user status sentinel-agent.service
journalctl --user -u sentinel-agent.service --since '10 minutes ago' --no-pager
```

The service starts automatically with your user manager. For boot monitoring before login and after logout, optionally enable user lingering:

```bash
sudo loginctl enable-linger "$USER"
```

Heartbeats then run at boot, but a logged-out desktop cannot be locked and GeoClue may need a logged-in permission agent. These limits are reported; the agent is never installed as root. Your GNOME login password must already be configured. No secret is passed as a systemd command argument.

## Google primary location provider

Google Wi-Fi positioning is integrated into the existing agent, backend and mobile
dashboard. Follow [Google setup and restrictions](docs/GOOGLE_LOCATION.md) to set
the backend-only key and separately consent on the laptop. It requires two suitable
BSSIDs, HTTPS and `considerIp: false`, with a persisted ten-minute minimum and daily
cap. Google coordinates have attribution and never appear on OpenStreetMap.
Accuracy retention is disabled pending separate Google permission; the guide
documents that limit on displaying the latest radius.

## Legacy location consent and system permissions

Install `sentinel.desktop` via the install script so GeoClue can identify the client. Enable GNOME Location Services in Settings → Privacy (the exact label varies by GNOME version). With `location_enabled` set true in the config, restart the service and inspect:

```bash
systemctl --user restart sentinel-agent.service
"$HOME/.local/bin/sentinel-agent" diagnose
```

Stop the service before `diagnose` if it reports another agent is running; the single-instance guard is intentional:

```bash
systemctl --user stop sentinel-agent.service
"$HOME/.local/bin/sentinel-agent" diagnose
systemctl --user start sentinel-agent.service
```

If GNOME's GeoClue agent cannot authorize the client, review [the optional per-user allowlist example](agent/packaging/geoclue.conf.example), substitute your `id -u`, and manually add it to `/etc/geoclue/geoclue.conf` with `sudoedit`. Never use an unrestricted empty `users=` list. GeoClue positioning depends on the provider configured in that file and its actual coverage. Wi-Fi positioning is preferred when the installed service supports it; no GPS hardware is assumed. Without a fix the dashboard says unavailable or explicitly shows an older last-known fix.

Authentication monitoring is **optional**. Without full system-journal read access it reports unavailable. If you accept that this grants broad local log-read permission, add your desktop user to the available journal group and log out/in:

```bash
getent group systemd-journal
sudo usermod -aG systemd-journal "$USER"
```

Only existing SSH password, GDM PAM and sudo PAM journal failure messages are counted (5 in 5 minutes, 10-minute cooldown). Do not install/enable SSH merely for Sentinel. Network changes require a different saved NetworkManager profile to remain active for two consecutive observations. Neither rule proves theft.

## Phone notifications

Generate your own Web Push keys on the **backend host**:

```bash
.venv/bin/python scripts/generate-vapid.py
```

Set these entries in `.env` (or the protected production environment file):

```text
SENTINEL_VAPID_PRIVATE_KEY=/absolute/path/to/data/vapid/private.pem
SENTINEL_VAPID_PUBLIC_KEY=<contents of data/vapid/public.txt>
SENTINEL_VAPID_SUBJECT=mailto:your-real-address@example.com
```

Restart the backend. On the phone, sign in over HTTPS, add Sentinel to the home screen, and tap **Enable phone notifications**. Chrome/Firefox on Android support the expected push services; browser/OS permission and battery policies can affect delivery. iOS users may need to install the PWA to the home screen. Alerts remain in-app when push is not configured. The list shows whether the push service accepted, rejected or has not yet received an alert. This is not a read receipt.

## Secure access from different networks

For a temporary phone link using mobile data, see [temporary HTTPS phone access](docs/PHONE_ACCESS.md). Run `./scripts/run-phone-link.sh` after installing the Cloudflare connector and starting the local servers. This keeps local enrollment intact and adds a production-secured phone gateway; the laptop must stay on and connected while you test.

Deploy the backend and PWA on a separate always-on Linux host with a public domain and HTTPS. The laptop does **not** need a public IP, static address or inbound port. [Deployment instructions](docs/DEPLOYMENT.md) provide Caddy TLS, loopback services, protected secrets and single-worker configuration. Enroll with:

```bash
./agent/target/release/sentinel-agent enroll --server https://sentinel.your-domain.example --location
./scripts/install-agent.sh
```

Use your actual hostname; no remote server/domain/account is provisioned by this checkout. Do not expose the local development server to a network. Do not bypass TLS certificate validation.

## Verification and troubleshooting

```bash
PYTHONPATH=backend .venv/bin/pytest backend/tests -q
cargo test --locked --manifest-path agent/Cargo.toml
cd frontend
npm run typecheck
npm run build
cd ..
.venv/bin/python scripts/integration-smoke.py
# Optional browser QA if Chromium is installed and ports 18765–18768 are free:
.venv/bin/python scripts/browser-smoke.py
```

The integration smoke starts a temporary loopback backend and runs the release Rust binary with deliberately absent D-Bus sockets. It checks real enrollment, heartbeat, command delivery with honest lock failure, backend outage/reconnection, and revocation; it cannot lock your actual desktop. Port 18765 must be free. The browser smoke uses temporary test data and its own headless Chromium profile; it does not touch your normal browser profile. Use [manual tests](docs/MANUAL_TESTS.md) for the four real-device features and cross-network acceptance. `./scripts/check.sh` performs the unit checks and production builds together.

Common causes:

* **Sign-in/CSRF error:** use the exact configured origin. Verify proxy routing and production `X-Forwarded-Proto=https`. Sessions expire after 12 hours.
* **Location unavailable:** collection disabled, denied GeoClue permission, absent/outdated positioning provider, lack of Wi-Fi data or service outage. Run `diagnose` with the service stopped; there is no fabricated fallback.
* **Lock failed:** no GNOME desktop session, unreachable session bus, absent `org.gnome.ScreenSaver` or `LockedHint`, multiple graphical sessions, or insufficient permission. Check diagnostics and the agent journal. A logged-out/suspended/powered-off laptop cannot produce a verified screen-lock success.
* **Command expired:** laptop did not finish and acknowledge within 120 seconds. Reconnect, then explicitly request another lock.
* **Offline/stale:** sleep/power/network/API interruption. Run `run --once` with the service stopped. Check domain certificate, outgoing HTTPS and NTP; never interpret a missed heartbeat as theft.
* **Auth monitoring unavailable:** optional journal permission is missing, records aren't in journald or their format is unsupported. Never solve it by running Sentinel as root.
* **Notifications missing:** VAPID configuration, browser permission, expired push subscription, external push outage or OS restrictions. Read each alert's push status and try resubscribing.

Reset an owner password on the server (this invalidates sessions):

```bash
set -a; source .env; set +a
PYTHONPATH=backend .venv/bin/python -m app.cli reset-password yourname
```

## Uninstall

Revoke the laptop credentials in the dashboard first, then run as your desktop user:

```bash
./scripts/uninstall-agent.sh
```

The uninstall script removes the service/binary/desktop ID and retains private config/state intentionally. If you want these gone, remove `~/.config/sentinel` after revocation. If lingering or journal group membership was added solely for Sentinel, undo it and remove any Sentinel GeoClue stanza; see the script's printed commands. The server and its database are separate: stopping/deleting them or their backups requires your own deployment cleanup.

Read [implementation decisions](docs/IMPLEMENTATION.md) and [security controls and limits](docs/SECURITY.md). Sentinel cannot track a powered-off laptop, defeat a root attacker or survive disk replacement. It implements no remote shell, desktop stream, disk wipe, file browsing, AI or surveillance.
