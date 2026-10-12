# Temporary HTTPS phone access

This opens the existing Sentinel dashboard from mobile data or another network
through a Cloudflare Quick Tunnel. No phone app/account or domain is needed;
sign in with the Sentinel owner account you already created.

It is a **temporary testing link**, not permanent anti-theft hosting. The laptop,
local dashboard, primary backend and tunnel must keep running. If the laptop
sleeps, shuts down or loses its connection, this dashboard also becomes
unreachable. For recovery monitoring during laptop outages, use the independent
always-on server in [DEPLOYMENT.md](DEPLOYMENT.md).

## Start

The protected Intel/x86-64 laptop can use Cloudflare's official binary. Download
into this checkout (no root install and no credentials):

```bash
mkdir -p .local-tools
curl -L --fail https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o .local-tools/cloudflared
chmod 755 .local-tools/cloudflared
```

Keep the local servers running in their terminals:

```bash
./scripts/run-backend.sh
```

```bash
./scripts/run-frontend.sh
```

In a third terminal:

```bash
./scripts/run-phone-link.sh
```

Open the printed **https://…trycloudflare.com** URL in Chrome on your phone,
including when using mobile data. Sign in with your existing owner username and
password. Once signed in, choose Chrome's **Add to home screen** / **Install app**
action if you want a home-screen shortcut. Do not use the laptop's `localhost`
URL on the phone; that points at the phone itself.

There is already an enrolled laptop in the shared database if you enrolled
locally. Do not pair it a second time merely to change the browser you use.
The phone gateway shares the same laptop/commands/alerts with the local backend;
the local agent keeps its existing credentials and loopback heartbeat endpoint.

## How access is secured

* Cloudflare provides public HTTPS and the encrypted outbound tunnel. No inbound
  laptop firewall port is opened. Cloudflare terminates public TLS, so its
  service is part of the trusted transport path.
* The separate phone gateway binds only to 127.0.0.1:8110. It forces production
  HTTPS/secure-cookie settings, exact public-Origin checks and normal owner/device
  authorization. Local development settings are not weakened.
* Passwords, device credentials, enrollment tokens and location data remain
  authenticated. Neither a tunnel hostname nor a device ID authenticates anyone.
* Development API docs cannot pass through the gateway. Unauthenticated device
  queries still return 401. Frontend asset proxying excludes authentication
  cookies and bearer headers.
* The existing backend retains sole ownership of push delivery and retention;
  the gateway does not start a second push worker. No agent reconfiguration or
  desktop locking happens just from opening the link.

Cloudflare's address is public; anyone who knows it can reach the login screen,
but device data and actions require your owner session. Keep your owner password
strong and keep the link private.

## Stop/restart and limitations

Press **Ctrl+C in the phone-link terminal** to close the tunnel and gateway.
It leaves the existing backend, dashboard, agent and data intact. Restarting
creates a different hostname, so old bookmarks/home-screen shortcuts may need
updating. The script never edits `.env` or the agent's credentials.

Quick Tunnels have no uptime guarantee, changing hostnames, a concurrent request
limit, and no SSE. Sentinel uses ordinary HTTP polling. Web Push remains dependent
on the configured VAPID keys and the browser/OS; a new phone URL is a new browser
origin and requires notification consent/subscription again.

The script checks local server readiness and whether gateway port 8110 is free.
Set `SENTINEL_PHONE_PORT` in `.env` if that port is occupied. The Cloudflare
connector must be allowed outbound access; `http2` is selected for the tunnel.
If outgoing tunnel connections are blocked, do not expose the unauthenticated
development configuration on your LAN to work around it.

Sources: [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
and [official downloads](https://developers.cloudflare.com/tunnel/downloads/).
