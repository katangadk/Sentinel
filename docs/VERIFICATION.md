# Verification report — 12 October 2026

## Google primary-provider integration

The existing V1 architecture and four features are preserved. Current checks:

| Check | Result | Evidence and limits |
| --- | --- | --- |
| Backend pytest | **57 passed** | Includes mocked Google success/errors, actual returned coordinates, strict AP/RSSI/consent/TLS/freshness limits, missing key, `considerIp: false`, no IP fallback, owner isolation, retained-radius permission default, TTL, revocation, per-device/global usage and concurrent reservations across app instances. Same TestClient deprecation warning remains. |
| Rust unit suite | **13 passed, 1 hardware test ignored by default** | BSSID filtering/dedup/bounds, percentage omission, persistent schedule, old-config consent default, existing signatures/locks/events/private storage. |
| Explicit hardware test and release CLI `scan-wifi` | **Passed as normal user; 0 suitable APs in the Rust scan** | No identifier upload or Google request. Separate NetworkManager inspection saw 5 APs, 4 unsupported BSSIDs and 1 suitable at that moment. These observations do not supply the minimum two suitable APs or establish two physical stationary routers. |
| Release Rust build | **Passed offline/locked** | Updated existing binary; used this checkout's `.cargo-cache` via `CARGO_HOME`. Existing installed background binary/config was not replaced or opted into Google. |
| TypeScript / Next production build | **Passed** | Existing PWA plus location fields/copy/attribution and public privacy/terms pages. No dependency changes. |
| Mobile Chromium smoke | **Passed** | Actual isolated Next/FastAPI servers: unavailable then mocked Google coordinates and radius under simulated separate cache permission, real clipboard readback, attribution, no OpenStreetMap map, no overflow, lock confirmation, PWA and logout. No real Google key or paid request. |
| Real Rust/FastAPI regression smoke | **4 groups passed** | Enrollment/heartbeat, signed lock with honest absent-GNOME failure, persisted outage/reconnection, revoked credential rejection. Uses isolated desktop buses; no real desktop lock. |
| Real binary consent CLI | **Passed** | Temporary config only: HTTP rejected without modification, HTTPS Google consent/disable persisted with mode 0600, enrollment credential preserved. No network or real config changes. |
| Syntax/compile/whitespace | **Passed** | Python compile, launcher shell syntax, Cargo formatting and diff whitespace. |

The browser smoke initially lacked headless clipboard focus; explicit browser
focus and user-gesture emulation resolved it. One integration attempt overlapped
the browser's test port; an occupied-port guard was added and the sequential rerun
passed. No failed production-provider test is represented as a success.

Real Google key/API/billing/IP restrictions, Google's Wi-Fi coverage and physical
accuracy in Ndola, two suitable stationary APs, actual reconnect/movement updates
under the installed user service, and cross-network phone display remain unverified.
No billable Google request was made. Accuracy is parsed in the live response but
not cached by default; numeric retained radius requires separate permission from
Google. See [Google setup and contractual limits](GOOGLE_LOCATION.md).

The running local frontend was restarted after the compile check to avoid serving
obsolete script references; local page, every referenced script, privacy/terms
pages and unauthenticated-device rejection then passed. The previous temporary
phone address later failed DNS lookup. On the owner's restart request, the stopped
backend was started and the tunnel and phone gateway were restarted. The new public
HTTPS address returned the dashboard (200), API health (200), unauthenticated
device rejection (401), and blocked development docs (404). The enrolled user
agent was active. Actual phone sign-in remains a real-device acceptance check.
Google key configuration, backend/gateway restart after changing configuration,
and explicit device consent remain Google activation steps; the existing
enrollment remains intact.

## Follow-up: temporary phone access

The temporary-phone gateway adds four tests to the original suite: **25 backend
tests now pass** (the same TestClient deprecation warning remains). These cover
HTTP rejection, blocked development docs, static asset proxying without forwarding
owner credentials, preserved query strings, Secure cookies, exact public-Origin
and CSRF checks, access to the existing enrolled device, shared lock commands and
an honest 502 when the dashboard is unavailable. The tunnel connector download
was verified against Cloudflare's official GitHub release asset SHA-256 digest
(cloudflared 2026.10.0).

After the user explicitly authorized Cloudflare to handle the temporary link's
sign-in/device traffic, the tunnel and secured phone gateway started successfully.
Requests through the assigned public HTTPS address returned the Sentinel page
(200), API health (200), unauthenticated device access (401), and blocked development
docs (404). The local gateway smoke also verified the real Next.js page, JavaScript
asset and PWA manifest. Actual phone login and mobile-data use still need the user's
device check. The temporary address changes on restart and works only while the
laptop, local servers and tunnel remain running. See [PHONE_ACCESS.md](PHONE_ACCESS.md).

After explicit owner consent, GNOME Location Services and the enrolled agent's
GeoClue collection were enabled, and the existing user service restarted. Its
next heartbeat stored a valid GeoClue position with reported accuracy **25,000 m**.
The separate ipapi.co fallback remains disabled. This verifies acquisition and
backend receipt on this laptop; it establishes an approximate area, not precise
street positioning. The owner later reported being in Ndola while that fix showed
Lusaka; its physical accuracy is **not validated** and the reported radius was
misleading in that instance. Coordinates and credentials are omitted here.

All results below are from commands actually run in this workspace. This report distinguishes local software evidence from hardware and public-network acceptance.

## Passed

| Check | Result | Scope |
| --- | --- | --- |
| `PYTHONPATH=backend .venv/bin/pytest backend/tests -q` | **21 passed** | Login/session/logout, rate limit, CSRF/Origin, ownership, one-time/expired enrollment, invalid device credentials, heartbeat/status thresholds, real/unavailable/invalid location schema, retained last fix, signed lock lifecycle, early/late/expired/replayed results, duplicate lock requests, streamed body limit, alert threshold/deduplication/cooldown, push SSRF validation, revocation, production TLS/cookie/session expiry, push outbox success and retry/failure via mocked sender. |
| `cargo test --offline --locked --manifest-path agent/Cargo.toml` | **9 passed** | Signed command verification; expiry, wrong owner/secret, action and tampering rejection; persistent consumed ledger and mocked lock replay after restart; private file permissions; location validation; deterministic auth and network thresholds/cooldown; log rule filtering; HTTPS origin validation. |
| `cargo build --release --offline --locked --manifest-path agent/Cargo.toml` | **Passed** | Built executable `agent/target/release/sentinel-agent` (approximately 5.3 MiB). |
| `npm run typecheck` | **Passed** | Strict TypeScript checking. |
| `npm run build` | **Passed** | Next.js production build, including its TypeScript checks and generated PWA manifest. |
| `.venv/bin/python scripts/integration-smoke.py` | **4 scenario groups passed** | Actual compiled Rust → actual Uvicorn HTTP enrollment/heartbeat; unavailable location; signed command retrieval and real failed acknowledgement with absent test D-Bus; persistence during API outage and recovery after server restart; revoked secret rejected. Temporary database and credentials only. |
| `.venv/bin/python scripts/browser-smoke.py` | **Passed** | Actual built Next.js PWA + API in isolated mobile Chromium (390×844): login, online/backend data, unavailable location, no horizontal overflow, cancelling a lock produces no command, confirming produces pending, service-worker control, manifest, logout removes owner data. Dedicated loopback ports and test-only reverse proxy. |
| `.venv/bin/python scripts/startup-smoke.py` | **Passed** | Actual documented backend/frontend launcher scripts on local defaults 8100/3100, Next.js API rewrite, login/cookie session, protected dashboard and CSRF logout. Temporary test owner/database; refuses real `.env`. |
| `.venv/bin/pip check` | **Passed** | Installed Python dependency consistency. |
| `.venv/bin/python scripts/generate-vapid.py /tmp/sentinel-vapid-check` | **Passed** | Generated private PEM and base64url public key in protected files; no secrets printed. This does not establish phone delivery. |
| `bash -n scripts/*.sh`, Python compile check, `git diff --check` | **Passed** | Script syntax and changed tracked-file whitespace. |
| `systemd-analyze --user verify agent/packaging/sentinel-agent.service` | **Exit 0** | Unit-file validation only; sandbox emitted socket-permission warnings. Service not installed or started in the user's real session. |

The final backend suite has one dependency deprecation warning: this Starlette version recommends `httpx2` for future TestClient support. The pinned `httpx` test client worked; no failing test is hidden.

## Environment and resolved restrictions

Observed Kali GNU/Linux rolling 2026.3, Python 3.14.7, Rust/Cargo 1.97.1, Node.js 24.19.0, Next.js 16.4.0. Chromium is installed. Minimum-version compatibility was not separately tested; the lock files reflect the verified environment.

Sandbox DNS restrictions initially prevented pip/npm/Cargo downloads. Approved network-enabled runs installed dependencies. The sandbox also stalled Starlette's threaded test client, interfered with Next.js child-compiler output, and prevented the loopback integration check. Those checks passed outside the sandbox after explicit tool approval; no code checks were disabled. The Rust unit suite and release compilation passed offline in the sandbox.

Browser QA's first attempt found port 8000 occupied. Inspection also found port 3000 occupied; both existing services were left untouched. Local launchers now default to confirmed free ports 8100/3100; deployment templates still use the conventional loopback ports on a separate server. The completed browser smoke uses ports 18765–18768 instead. It starts/stops its own temporary services and browser profile, never reusing personal browser data. The final frontend output uses the standard Next.js server build matching `next start`.

## Still requires your real-device acceptance

* **GNOME lock:** real logged-in GNOME screen must visibly lock and require the existing login password. Both `GetActive` and the sole user's graphical session `LockedHint` must confirm it. The successful lock unit test uses a mock; the HTTP smoke verifies an honest failure when GNOME is absent. No actual desktop lock was triggered during this build.
* **Positioning accuracy:** a real GeoClue position reached the backend after owner consent, with reported accuracy 25 km. Agreement with the laptop's actual position, finer Wi-Fi coverage and movement updates remain unverified. Separate IP fallback/provider availability is also not verified.
* **OS event observation:** actual NetworkManager profile changes and SSH/GDM/sudo journal formats/permissions. Unit tests use synthetic records; they verify rule logic, not this machine's logs.
* **Web Push:** actual VAPID/browser subscription, external push-service delivery, Android notification arrival and OS battery behavior. Sender transport is mocked in the automated outbox tests; Chromium verifies PWA registration without requesting real phone notifications.
* **Automatic startup after reboot:** the real enrolled user service was already installed and active; its restart and subsequent heartbeat were verified when enabling location. GNOME login startup, optional lingering and reboot behavior remain unverified.
* **Cross-network phone access:** the temporary Cloudflare HTTPS link passed public page/API checks. Actual phone login on mobile data with the laptop on independent Wi-Fi remains unverified. Permanent server/domain deployment and Caddy certificate issuance also remain unverified.
* **Caddy/service deployment:** Caddy is absent in this development environment, so `caddy validate` and production server installation remain deployment checks. The production API's HTTPS requirement and secure cookie behavior were tested in-process.

Complete [MANUAL_TESTS.md](MANUAL_TESTS.md) before treating Sentinel as operational protection. [SECURITY.md](SECURITY.md) records the trust boundaries and limits; [DEPLOYMENT.md](DEPLOYMENT.md) provides the HTTPS setup. The software implementation and local checks are complete; operational acceptance is pending these real-device/deployment tests.
