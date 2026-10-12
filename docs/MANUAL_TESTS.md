# Real-device acceptance checklist

Perform on the protected Kali/GNOME laptop and your own phone. Record the OS/GNOME/browser versions, timestamp, result and relevant permission/capability reports. None of these checks are claimed complete by the automated suite.

## 1. Startup and authenticated connectivity

1. Deploy HTTPS as documented. Sign in from your phone; pair the laptop with the one-time token. Confirm the token cannot be used a second time.
2. Install the user service. Inspect `systemctl --user status sentinel-agent` and `journalctl --user -u sentinel-agent`. Check config/state mode 0600 and config directory mode 0700.
3. Wait a heartbeat interval. Verify your real laptop name, online status and a recent server receipt timestamp on the phone.
4. If lingering is enabled, reboot, leave the laptop at the GNOME login screen and verify heartbeats begin. Locking must report unavailable without a desktop. If lingering is not enabled, verify automatic startup after desktop login.

## 2. Disconnection and reconnection

1. Keep the phone connected. Disconnect the laptop network. Check online through 90 seconds after last contact, stale through 180 seconds, then offline/unreachable. Sleep/poweroff should behave similarly. None should claim theft.
2. Restore laptop connectivity. Within the next normal heartbeat and UI refresh, verify recent last-seen and online.
3. Disconnect the **phone** or stop the API server: the phone must show the dashboard/backend is unreachable, not claim a fresh laptop state. A cold offline PWA launch shows an honest reconnect page.

## 3. Approximate positioning and consent

For the Google primary provider, follow [the Google setup guide](GOOGLE_LOCATION.md):

1. Confirm that old enrollment does not enable `google_wifi_enabled` automatically.
   Run `scan-wifi` as your desktop user. Record suitable count, without publishing
   BSSIDs/SSIDs. Two distinct physical stationary routers are needed; one router
   with multiple radios does not prove this requirement.
2. Configure a restricted backend-only key, enabled Geolocation API, billing and
   a low Google daily quota. Review actual contractual/region requirements. Grant
   device consent with `location-consent --google-wifi --server https://...` and
   restart/install the updated service. Confirm no root privilege is needed.
3. On the phone, compare returned Google coordinates with the laptop's actual
   position. Verify source, timestamp, Copy Coordinates and visible attribution,
   with no OpenStreetMap map. Default retained accuracy is explicitly unknown;
   do not enable accuracy caching without separate Google permission.
4. Repeat after at least ten minutes, move/reconnect and verify updates after the
   next eligible heartbeat. Restart/reconnect within ten minutes must not produce
   an extra billable attempt. Inspect Cloud Console usage, without logging keys.
5. Test missing/invalid key, unavailable Wi-Fi, fewer than two suitable APs and a
   deliberately low daily cap. Expect unavailable/sanitized status or an unexpired
   last Google fix with a warning; never backend-IP or invented coordinates. Locks,
   monitoring and alerts must continue. After the configured TTL, old Google fixes
   must disappear, including during prolonged laptop disconnection.
6. Revoke Google consent; confirm AP uploads stop. Separately turn off any legacy
   location flags when stopping all collection. Check the service sandbox's scan
   permissions and real phone mobile-data behavior.

The following steps cover the separately consented legacy provider:

1. Begin with both config consent flags false. Confirm no location is transmitted and the dashboard says location unavailable/collection disabled.
2. Enable `location_enabled`, restart the agent and grant GNOME/GeoClue permission. With the service stopped, `sentinel-agent diagnose` reports whether a real GeoClue fix was available. Record the installed provider and actual D-Bus behavior.
3. If a fix succeeds, check its real coordinates, timestamp, GeoClue label and provider accuracy. Tap Show interactive map; inspect pan/zoom and confirm it corresponds to the returned coordinates. Do not assume GPS precision.
4. Deny permission or disable the provider. Verify current acquisition is unavailable. An existing last valid fix may remain visible with the unavailable warning and original timestamp; no fabricated replacement appears.
5. If separately consenting to IP fallback, check that it is explicitly labeled coarse, accuracy unknown, and may show the ISP/VPN area. Disable the option to stop third-party requests. Powered-off tracking is not supported.

## 4. Screen lock and actual status

1. Save desktop work. Ensure a GNOME graphical session is logged in with a login password. Phone and laptop should be on different networks.
2. Tap Lock Laptop, cancel once and verify no new command. Tap again and confirm. Expect pending, possibly delivered, then successful **only after the desktop visibly locks and both GNOME activity and logind LockedHint verify it**. The GNOME login password must be required to regain access.
3. Offline case: disconnect laptop, request a lock, wait over two minutes, reconnect. It must expire without claiming success or executing the stale request.
4. Unavailable desktop: log out, use a lingering agent, request lock. It should return failed, not successful. Multiple graphical sessions are intentionally not supported by verified locking.
5. Delivery/restart: while a lock is pending, restart the agent once, then inspect the command result and agent's protected ledger. The same UUID must never be executed twice. Do not edit the replay state during normal operation.
6. If GNOME visually locks but logind verification is unsupported, Sentinel must report failed. Record the interface limitation; never weaken result verification merely to obtain a green status.

## 5. Security alerts and phone Web Push

1. Confirm VAPID keys configured. Enable phone notifications and grant browser/OS permission. Keep the phone on mobile data, background the installed PWA.
2. Network test: switch between two saved NetworkManager profiles on the laptop and leave the second stable for at least two heartbeats. Expect one network-change alert. Repeat within ten minutes; no duplicate alert should appear. This is an observed profile change, not proof of an attack.
3. Authentication test only when permissions/capabilities show supported: make five deliberate wrong-password attempts on your own existing sudo/GDM/SSH authentication path within five minutes, avoiding account lockout policies. Confirm canonical journal messages exist locally, then wait for a heartbeat. Expect one minimal count/timestamp alert without raw usernames/logs. No event must be invented if the journal is unreadable or the service logs elsewhere.
4. Confirm the phone receives the Web Push notification and tapping it opens Sentinel. Compare the in-app event timestamp and push status. `sent` means service acceptance, so actual phone arrival must be separately checked.
5. Deny notifications or remove the subscription; in-app alerts still work. Restore permission and subscribe again. Check delivery limitations during browser/phone battery restrictions.

## 6. Authorization and cleanup

1. Sign out; dashboard data and Lock Laptop must require login. A different owner must never see this device or authorize its lock.
2. Revoke laptop in the dashboard. Confirm subsequent agent heartbeats fail authentication. Generate a fresh token if re-enrolling.
3. Run the uninstall script. Reboot/sign in and confirm the service no longer exists/runs. Decide whether to remove retained private config, undo lingering/journal group changes and remove the GeoClue stanza.

## Cross-network evidence

Record phone carrier/mobile connection, laptop Wi-Fi connection, the public HTTPS hostname, valid certificate, successful heartbeat timestamp, lock UUID/result and push arrival timestamp. A localhost smoke, mocked lock or synthetic unit event is not cross-network/hardware verification. No inbound laptop port should be open for Sentinel.
