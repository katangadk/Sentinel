# Security and privacy review

## Implemented controls

| Requirement | Implementation |
| --- | --- |
| TLS | Agent refuses HTTP except explicitly requested loopback development; validates certificates, TLS 1.2+, refuses redirects. API requires HTTPS outside loopback development. Caddy terminates public TLS. |
| Enrollment | Authenticated owner creates a random 256-bit, 10-minute pairing token. Atomic one-time consumption, one laptop per owner. No public registration. |
| Device identity | Random unique 256-bit bearer secret, only its SHA-256 digest in SQLite. UUID alone cannot authenticate. Dashboard revocation invalidates the credential. |
| Owner authentication | Argon2id, uniform invalid-login response and dummy-password verification. Provision/reset only through server-local CLI. Password reset deletes active sessions. |
| Browser sessions | Random opaque session, hashed server-side, 12-hour expiry, HttpOnly, SameSite=Strict, Secure in production. Exact Origin and session-bound CSRF required for mutations; login validates Origin. No tokens in browser storage. |
| Authorization | Device queries/actions scoped to the current owner; agent acks scoped to the authenticated device. Foreign device IDs return 404. |
| Replay | Device-bound command HMAC, action allowlist, UUID, 120-second TTL, clock validation, durable pre-execution consumed marker and idempotent acks. |
| OS permissions | Agent rejects UID 0; user systemd service with NoNewPrivileges, restricted writes and protected filesystem. Session D-Bus is the narrow lock interface. No shell or root helper. |
| Validation | Strict Pydantic schemas, bounded bodies including streamed requests, bounded events and capability strings, coordinate/time validation, fixed acknowledgement details. |
| Audit | Owner ID, device/command IDs, timestamp, request/delivery/result/expiry stored; no passwords, tokens, raw log text or terminal instructions. |
| Rate limits | Atomic persisted SQLite buckets for login IP (20/5min), account (10/5min), pairing IP (10/5min), owner pairing (5/5min), lock (5/5min), bad device credentials (30/5min), valid heartbeat (12/min), push subscription changes (10/5min). |
| Data storage | Agent directory 0700, config/state 0600, no symlink-following reads, atomically persisted/fsynced state. Database 0600 and process umask 0077. Deploy env/private VAPID file must also be 0600. Secrets excluded from Git. |
| Privacy | Opt-in positioning and IP fallback; user controls map loading. No raw auth log upload, SSID upload, camera, mic, keylogger or file access. No location history beyond last valid fix. |
| Push | Encrypted Web Push/VAPID; subscription host allowlist and disabled redirects; bounded retries with explicit delivery status. No location in push payload. |
| Retention | Worker removes events, commands and audit after 30 days; expired session/pairing/rate rows cleaned. Latest Google coordinates expire within 24 hours; accuracy caching is disabled unless separately authorized by Google. Legacy last fixes persist until revocation. Private backups/snapshots must also honor provider retention. |

Google Wi-Fi mode requires separate owner consent, enrolled-device authentication
and HTTPS even in development. NetworkManager signal percentages are never sent
as dBm. The backend validates 20 APs maximum and at least two distinct supported
BSSIDs, sets `considerIp: false`, uses a fixed verified HTTPS Google endpoint,
disables redirects/retries, and reserves per-device/global daily usage atomically
before a paid call. APs never enter SQLite, logs or retry queues. Google errors are
sanitized; HTTPX request logging for the key-bearing Google URL is suppressed.
Do not enable HTTP wire/body logging. Keys stay in backend configuration and are
stripped from frontend/tunnel processes. No public endpoint returns location.

Google content is never displayed on OpenStreetMap. Coordinate expiration occurs
on startup, owner reads and worker ticks; SQLite secure deletion and periodic WAL
checkpointing limit stale local page copies. Operators must manage backups and
storage snapshots. See [Google location terms and unresolved accuracy caching](GOOGLE_LOCATION.md).

## Boundaries and operational concerns

This protects a running user agent with internet access and a trusted HTTPS server. It cannot track a powered-off laptop, survive a disk reinstall, defeat an attacker with root, or guarantee recovery. Full disk encryption and an existing GNOME login password remain necessary. These are deployment constraints, not additional V1 features.

The backend and enrolled laptop are trusted: compromise of either can falsify heartbeats/events or originate permitted lock requests. HMAC protects against unauthorized delivery and replay; it is not a hardware attestation scheme. Local malware running as the same desktop user can access that user's credentials and bus. A server administrator can read the latest location and push subscription material. Do not expose the database or put it in a shared/synced directory. Store backups encrypted with a separate key.

Use one Uvicorn worker so there is one push consumer and retention worker. Rate buckets are atomic but SQLite is appropriate for this personal-scale workload, not a fleet. Schema creation covers a fresh V1 database; later versions will need explicit migrations. Dependency versions are locked in Cargo.lock, package-lock.json and requirements.lock; keep them reviewed and updated before internet deployment. No independent penetration test is claimed.

Production must use the same public origin for frontend and API. Only trust Caddy's local forwarded headers (`--forwarded-allow-ips=127.0.0.1`); do not trust arbitrary proxy clients. Keep ports 3000/8000 on loopback and open only HTTPS and the HTTP ACME challenge port on the server. Development bypasses Secure cookies/TLS and enables API docs only with `SENTINEL_DEVELOPMENT=true` plus a loopback public origin; never deploy that setting.

The frontend CSP allows inline scripts/styles for Next.js and Leaflet compatibility. It blocks foreign script sources and frame embedding, but is weaker than a nonce-based policy. React escapes displayed user strings. The proxy enforces request limits; protect the host with normal OS updates and access controls. Authentication rate limits bound attempts but do not replace host-level DDoS protection.

System-journal groups provide broad local log-read access, not just authentication records. This optional permission is disclosed; do not grant it automatically or run the agent as root. Without it, auth monitoring explicitly reports unavailable and the other features work. GeoClue provider coverage and consent vary by Kali installation. IP fallback sends the laptop's public IP to ipapi.co; loading map tiles sends viewed-area requests to OpenStreetMap. External service outages are surfaced honestly.

The VAPID private key is a long-lived server credential. Generate your own; never reuse example/test values. After rotating it, unsubscribe/resubscribe on phones. Subscription removal currently applies to all phones signed in as that owner. Browser push may continue after sign-out until subscriptions are removed; lock commands still require a current owner session.
