"""Test the built PWA in mobile Chromium with isolated backend data.

Requires free loopback ports 18765-18768, installed Chromium and Node.
Uses an isolated Chromium profile with its own sandbox disabled for headless QA;
does not change or reuse the user's normal browser profile.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
import httpx
import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("integration", ROOT / "scripts/integration-smoke.py")
integration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integration)


class TestProxy(BaseHTTPRequestHandler):
    """Same-origin QA proxy, matching Caddy's /api routing on dedicated ports."""
    def forward(self):
        target = "http://127.0.0.1:18765" if self.path.startswith("/api/") else "http://127.0.0.1:18768"
        headers = {key: value for key, value in self.headers.items() if key.lower() not in {"host", "connection", "content-length"}}
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        response = httpx.request(self.command, target + self.path, headers=headers, content=data, timeout=20)
        self.send_response(response.status_code)
        for key, value in response.headers.multi_items():
            if key.lower() not in {"content-length", "content-encoding", "transfer-encoding", "connection"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(response.content)))
        self.end_headers()
        self.wfile.write(response.content)

    do_GET = forward
    do_POST = forward
    do_DELETE = forward

    def log_message(self, *_):
        pass


def main():
    for port in (18765, 18766, 18767, 18768):
        with socket.socket() as check:
            try:
                check.bind(("127.0.0.1", port))
            except OSError:
                sys.exit(f"Port {port} in use. Stop test/development servers before this isolated smoke.")
    with tempfile.TemporaryDirectory(prefix="sentinel-browser-servers-") as temporary:
        directory = Path(temporary)
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "backend"), str(ROOT / "backend/tests")]), SENTINEL_DEVELOPMENT="true",
                   SENTINEL_BROWSER_GOOGLE_FIXTURE="1", GOOGLE_GEOLOCATION_API_KEY="",
                   SENTINEL_PUBLIC_ORIGIN="http://localhost:18766", SENTINEL_DATABASE_URL=f"sqlite:///{directory}/test.db", NEXT_TELEMETRY_DISABLED="1")
        password = secrets.token_urlsafe(24)
        engine, factory = integration.make_database(env["SENTINEL_DATABASE_URL"])
        with factory() as db:
            db.add(integration.User(id="browser-owner", username="browser-owner", password_hash=integration.password_hasher.hash(password)))
            db.commit()
        engine.dispose()
        processes = []
        proxy = None
        with open(directory / "servers.log", "w") as logs:
            try:
                backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "browser_google_fixture:create_app", "--factory", "--host", "127.0.0.1", "--port", "18765", "--no-access-log"], env=env, cwd=ROOT, stdout=logs, stderr=logs)
                processes.append(backend)
                integration.wait_server("http://127.0.0.1:18765/api/health", backend)
                with httpx.Client(base_url="http://127.0.0.1:18765", headers={"Origin": "http://localhost:18766"}) as owner:
                    login = owner.post("/api/auth/login", json={"username": "browser-owner", "password": password})
                    login.raise_for_status()
                    owner.headers["X-CSRF-Token"] = login.json()["csrf"]
                    pairing = owner.post("/api/enrollment").json()["token"]
                    enrolled = owner.post("/api/agent/enroll", json={"token": pairing, "name": "Browser Test Laptop"}).json()
                    owner.post("/api/agent/heartbeat", headers={"Authorization": "Bearer " + enrolled["credential"]}, json={"location": None, "location_status": "unavailable", "events": [], "capabilities": {"authentication": "Unavailable in isolated browser test"}}).raise_for_status()
                frontend = subprocess.Popen(["node", "node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", "18768"], env=env, cwd=ROOT / "frontend", stdout=logs, stderr=logs)
                processes.append(frontend)
                integration.wait_server("http://127.0.0.1:18768", frontend)
                proxy = ThreadingHTTPServer(("127.0.0.1", 18766), TestProxy)
                threading.Thread(target=proxy.serve_forever, daemon=True).start()
                result = subprocess.run(["node", str(ROOT / "scripts/browser-smoke.mjs")], cwd=ROOT, input=json.dumps({"username": "browser-owner", "password": password, "deviceCredential": enrolled["credential"]}), text=True, capture_output=True, timeout=90)
                if result.returncode:
                    sys.exit(result.stderr)
                print(result.stdout.strip())
            finally:
                if proxy:
                    proxy.shutdown()
                    proxy.server_close()
                for process in reversed(processes):
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
