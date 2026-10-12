# Google Wi-Fi positioning in Sentinel V1

This modifies the existing Rust agent → FastAPI location lifecycle → Next.js
owner dashboard. It adds no fifth feature and no location history. Google is
primary **after separate device-owner consent**. Google mode does not sample
GeoClue or ipapi.co and never falls back to IP. Legacy options remain separately
opted in; an old config does not silently grant Google consent.

## Google Cloud and backend settings

1. Select a Google Cloud project, enable billing and **Geolocation API**. Create
   a server API key restricted to that API and, where possible, your backend's
   stable outgoing IPs. Browser referrer restrictions do not suit server requests.
2. Set a daily Geolocation API quota in Cloud Console and billing alerts. Use a
   low quota during acceptance. Sentinel's cap covers this app only; other apps
   using the project can consume quota. Check current pricing, not old credits.
3. Review your Google agreement and billing-region requirements. Public `/privacy`
   and `/terms` pages are included; adapt their hosting disclosures if needed.
4. Put the key in the **backend environment only**, in ignored root `.env` for local
   launchers or `/etc/sentinel/backend.env` for the production service:

```text
GOOGLE_GEOLOCATION_API_KEY=<your server API key>
SENTINEL_GOOGLE_DAILY_LIMIT=144
SENTINEL_GOOGLE_LOCATION_TTL_SECONDS=86400
SENTINEL_GOOGLE_ACCURACY_CACHE_AUTHORIZED=false
```

Never send the key in chat, pair it into the agent, use `NEXT_PUBLIC_`, commit it,
or put it in frontend files. The frontend launcher and Cloudflare connector strip
it from their environments. The phone gateway is a backend process and reads the
same protected backend settings. Restart the backend and phone gateway after
changing settings. A restarted temporary tunnel changes address; update the agent.

Daily limit is an atomic global UTC-day count, shared across devices/gateway
processes, from 0 (no billable calls) to 144. Failed attempts count before contacting
Google. Each device has a persisted sliding 600-second minimum. Key/billing/quota
rejection backs off at least one hour. Reservations survive restart. No automatic
Google HTTP retries, redirects or IP-only requests occur.

## Consent and activate the existing agent

Build/update the existing binary with the project's Cargo setup, then:

```bash
systemctl --user stop sentinel-agent.service
./agent/target/release/sentinel-agent location-consent --google-wifi --server https://YOUR-SENTINEL-ORIGIN
./scripts/install-agent.sh
```

Use a permanent HTTPS origin or the current approved temporary phone link. This
preserves enrollment/device credentials. The command grants explicit device-owner
consent to sharing nearby Wi-Fi BSSIDs with the backend and Google. It refuses HTTP,
including development-loopback HTTP. New devices can instead enroll with
`enroll --server https://YOUR-SENTINEL-ORIGIN --google-wifi`. The agent runs as the
desktop user, with no API key or unrestricted root/sudo permissions.

Revoke Google consent:

```bash
systemctl --user stop sentinel-agent.service
./agent/target/release/sentinel-agent location-consent --disable-google-wifi
systemctl --user start sentinel-agent.service
```

Previously consented legacy collection remains separately configured. To stop
all collection also disable `location_enabled` and `ip_fallback` in the protected
config or revoke the device. Never share the config: it contains a device secret.

## Scanner and request behavior

```bash
./agent/target/release/sentinel-agent scan-wifi
```

This local diagnostic can run beside the service and prints only suitable counts.
The scanner uses NetworkManager system D-Bus: `RequestScan`, `LastScan`,
`GetAllAccessPoints` and `LastSeen`. It may use a permitted cached scan ≤120 seconds
old when active scanning is denied, times out after eight seconds and requires
global connectivity/infrastructure mode. It filters stale, malformed, multicast,
locally administered, zero and IANA `00:00:5e:*` addresses, respects `_nomap`,
deduplicates and limits reports to 20 APs. SSIDs stay local and are not logged.

NetworkManager `Strength` is percent, **not dBm**, so this scanner omits RSSI. The
schema supports genuine integer RSSI in [-128, -10] dBm for interfaces that can
measure it. BSSID filtering cannot prove two radios belong to two stationary
physical routers: dual-band/virtual APs and mobile hotspots remain a limitation.

After a successful heartbeat and command handling, the agent posts an enrolled,
bearer-authenticated, certificate-verified HTTPS report to
`/api/agent/location/wifi` with fresh scan time and explicit consent. Fewer than
two suitable APs or scan failure produces an unavailable diagnostic without any
Google call. BSSIDs exist only in request memory, never agent state, backend tables,
events or a retry queue. Reconnection schedules a lookup at the first eligible
heartbeat; periodic lookups are approximately ten minutes apart. Restart and
reconnection cannot bypass the persisted minimum. Failure leaves the other three
features working.

The backend validates reports and sends only `considerIp: false` and suitable
`wifiAccessPoints` to `https://www.googleapis.com/geolocation/v1/geolocate`. It
provides the key itself, validates finite coordinates and radius, and stores results
against the authenticated device. Backend IP, owner/device names and sessions are
never sent as positioning inputs. Google failures are sanitized for the owner;
agent heartbeats cannot claim Google coordinates.

## Display, retention and unresolved restrictions

Reviewed current official sources on **12 October 2026**:

* [Requests and Wi-Fi filtering](https://developers.google.com/maps/documentation/geolocation/requests-geolocation)
* [Policies and attribution](https://developers.google.com/maps/documentation/geolocation/policies)
* [Current non-EEA service-specific terms, section 7](https://cloud.google.com/maps-platform/terms/maps-service-terms)
* [Platform terms](https://cloud.google.com/maps-platform/terms)
* [Key setup](https://developers.google.com/maps/documentation/geolocation/get-api-key)
* [Billing and quotas](https://developers.google.com/maps/documentation/geolocation/usage-and-billing)
* [NetworkManager wireless API](https://networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.Device.Wireless.html)
* [NetworkManager signal units](https://networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.AccessPoint.html)

The non-EEA section 7 allows temporary latitude/longitude caching for up to 30
consecutive days and prohibits use with non-Google maps. Sentinel retains **only
the latest coordinates**, source and timestamp for **24 hours by default**
(configurable 600–86400 seconds). A new fix replaces the previous one. Failure
retains an unexpired Google fix with a current-unavailable banner. Provider changes
remove the other provider's fix so the old Lusaka estimate cannot appear as Google.
Expiration runs at startup, on owner reads and in the 30-second retention worker;
expired positions are never served. SQLite `secure_delete` and periodic WAL
checkpoint/truncation are enabled. Operators
must also expire backups, snapshots and SQLite WAL copies within the applicable
period. SQL deletion does not prove physical erasure on storage hardware. Do not
archive Google locations.

**Accuracy caching is unresolved:** the exception explicitly names latitude and
longitude, not accuracy radius. Sentinel parses the actual radius and returns it
in the live authenticated agent response, but does **not retain it by default**.
The phone consequently shows the latest coordinates with an unknown/not-retained
radius and `Precision unknown`. It never fabricates accuracy. This limits the
requested latest-radius display pending clarification or separate Google permission.

Only if your Google agreement or written Google permission permits accuracy caching
may you set `SENTINEL_GOOGLE_ACCURACY_CACHE_AUTHORIZED=true`. Then the dashboard
displays the retained numeric radius with the same bounded TTL. The variable does
not itself obtain permission; automated tests only simulate such permission.
Owner consent cannot waive Google's caching terms. EEA or negotiated terms and
any personal-device/asset-tracking restrictions need review against your agreement.

The phone card shows latitude, longitude, source, last updated, permitted accuracy,
and Copy Coordinates. Google Maps text attribution uses the allowed narrow-interface
style, capitalization, no-translation, spacing and contrast. Google coordinates
never load Leaflet/OpenStreetMap; the map component also refuses them defensively.
Reported radii ≤100 m are precise *estimates*, larger ones coarse; neither is a
guarantee. Copied coordinates remain Google content subject to retention/display
terms. The phone's own GPS is never substituted for the laptop's position.

## Testing and acceptance

Backend tests mock Google responses; they use no real key or paid calls. Isolated
mobile Chromium checks unavailable and mocked coordinate states, simulated
authorized radius, attribution, no non-Google map, coordinate copy and the existing
lock/PWA/logout flows. Rust tests cover filtering, optional dBm, bounds and scheduling.
An ignored hardware test explicitly accesses real NetworkManager, without uploads.

Real acceptance requires at least two suitable stationary physical APs, normal-user
scan permissions under the installed service sandbox, a valid key/API/billing/IP
restriction configuration, Google coverage and comparison with physical position
in Ndola, reconnect/movement checks, actual Google quota behavior and phone display.
A working local scan does not establish provider coverage or precise positioning.
