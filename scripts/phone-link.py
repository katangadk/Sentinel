"""Start a temporary HTTPS phone link, retaining the existing local backend.

Use scripts/run-phone-link.sh so .env is loaded. No secrets are printed or passed
in command arguments. Stop with Ctrl+C; it stops only its own gateway/tunnel.
"""
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    binary = os.getenv("SENTINEL_CLOUDFLARED", str(ROOT / ".local-tools/cloudflared"))
    if not Path(binary).is_file():
        sys.exit("cloudflared is missing; follow docs/PHONE_ACCESS.md")
    frontend_port = int(os.getenv("SENTINEL_FRONTEND_PORT", "3100"))
    api_port = int(os.getenv("SENTINEL_API_PORT", "8100"))
    gateway_port = int(os.getenv("SENTINEL_PHONE_PORT", "8110"))
    for url in (f"http://127.0.0.1:{frontend_port}", f"http://127.0.0.1:{api_port}/api/health"):
        try:
            httpx.get(url, timeout=5, trust_env=False).raise_for_status()
        except httpx.HTTPError:
            sys.exit("Start Sentinel's local backend and dashboard before creating a phone link")
    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", gateway_port))
        except OSError:
            sys.exit(f"Gateway port {gateway_port} is already in use; no existing process changed")
    tunnel = gateway = None
    messages = queue.Queue()

    def stop_signal(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_signal)
    try:
        # The tunnel connector does not need Sentinel secrets in its environment.
        connector_env = {key: value for key, value in os.environ.items() if not key.startswith("SENTINEL_") and key != "GOOGLE_GEOLOCATION_API_KEY"}
        tunnel = subprocess.Popen([binary, "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", f"http://127.0.0.1:{gateway_port}"],
                                  env=connector_env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        def read_tunnel():
            for line in tunnel.stdout:
                # Retain only public URL and safe connection failures, never tokens.
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if match:
                    messages.put(("url", match.group()))
                elif "ERR" in line:
                    messages.put(("error", "Tunnel connection error; retrying"))
            messages.put(("exit", None))

        threading.Thread(target=read_tunnel, daemon=True).start()
        print("Creating a temporary HTTPS link…", flush=True)
        deadline = time.monotonic() + 60
        origin = None
        while time.monotonic() < deadline:
            try:
                kind, value = messages.get(timeout=1)
            except queue.Empty:
                continue
            if kind == "url":
                origin = value
                break
            if kind == "exit":
                sys.exit("Tunnel connector exited before assigning an HTTPS address")
        if not origin:
            sys.exit("No tunnel address after 60 seconds. Check outgoing Cloudflare connectivity")
        env = dict(os.environ, SENTINEL_DEVELOPMENT="false", SENTINEL_PUBLIC_ORIGIN=origin,
                   SENTINEL_FRONTEND_UPSTREAM=f"http://127.0.0.1:{frontend_port}", PYTHONPATH=str(ROOT / "backend"))
        gateway = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.phone:create_phone_app", "--factory", "--host", "127.0.0.1",
                                    "--port", str(gateway_port), "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=127.0.0.1", "--no-access-log"],
                                   env=env, cwd=ROOT)
        for _ in range(100):
            if gateway.poll() is not None:
                sys.exit("Phone gateway failed to start")
            try:
                response = httpx.get(f"http://127.0.0.1:{gateway_port}/api/health", headers={"X-Forwarded-Proto": "https"}, timeout=1, trust_env=False)
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        else:
            sys.exit("Phone gateway startup timed out")
        print(f"\nOpen on your phone: {origin}\nSign in with your existing Sentinel owner account.\nKeep this terminal, the laptop and local servers running. Ctrl+C closes the link.\n", flush=True)
        while gateway.poll() is None and tunnel.poll() is None:
            time.sleep(1)
        sys.exit("Gateway or tunnel exited; restart to create a new link")
    except KeyboardInterrupt:
        print("\nClosing the temporary phone link…", flush=True)
    finally:
        for process in (gateway, tunnel):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
