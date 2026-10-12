"""Verify the documented local launcher scripts and Next API proxy.

Refuses a real .env file; all owner/database state is temporary.
Needs free loopback ports 8100/3100 and a built frontend.
"""
import importlib.util
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("integration", ROOT / "scripts/integration-smoke.py")
integration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integration)


def main():
    if (ROOT / ".env").exists():
        sys.exit("This isolated launcher test requires no .env file in the checkout")
    for port in (8100, 3100):
        with socket.socket() as check:
            try:
                check.bind(("127.0.0.1", port))
            except OSError:
                sys.exit(f"Port {port} in use; no existing service was changed")
    with tempfile.TemporaryDirectory(prefix="sentinel-launchers-") as temporary:
        directory = Path(temporary)
        env = dict(os.environ, SENTINEL_DEVELOPMENT="true", SENTINEL_PUBLIC_ORIGIN="http://localhost:3100",
                   SENTINEL_API_PORT="8100", SENTINEL_FRONTEND_PORT="3100", SENTINEL_BACKEND_URL="http://127.0.0.1:8100",
                   SENTINEL_DATABASE_URL=f"sqlite:///{directory}/test.db")
        password = secrets.token_urlsafe(24)
        engine, factory = integration.make_database(env["SENTINEL_DATABASE_URL"])
        with factory() as db:
            db.add(integration.User(id="startup-owner", username="startup-owner", password_hash=integration.password_hasher.hash(password)))
            db.commit()
        engine.dispose()
        processes = []
        with open(directory / "launchers.log", "w") as logs:
            try:
                for script, url in (("run-backend.sh", "http://127.0.0.1:8100/api/health"), ("run-frontend.sh", "http://localhost:3100")):
                    process = subprocess.Popen([str(ROOT / "scripts" / script)], env=env, cwd=ROOT, stdout=logs, stderr=logs)
                    processes.append(process)
                    integration.wait_server(url, process)
                with httpx.Client(base_url="http://localhost:3100", headers={"Origin": "http://localhost:3100"}) as owner:
                    assert owner.get("/api/health").json()["status"] == "ok"
                    login = owner.post("/api/auth/login", json={"username": "startup-owner", "password": password})
                    login.raise_for_status()
                    owner.headers["X-CSRF-Token"] = login.json()["csrf"]
                    assert owner.get("/api/device").json()["device"] is None
                    owner.post("/api/auth/logout").raise_for_status()
                    assert owner.get("/api/device").status_code == 401
                print("PASS: documented launcher scripts on 8100/3100 + actual Next API proxy + login/session/CSRF logout")
            finally:
                for process in reversed(processes):
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
