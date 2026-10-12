"""Actual HTTP + release Rust binary smoke, isolated from the real desktop.

Uses a temporary DB and random test credentials, refuses real DBus interfaces,
and never attempts to lock the operator's real GNOME desktop.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import make_database
from app.models import User
from app.security import password_hasher


def wait_server(url, process):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Server exited unexpectedly")
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Server startup timed out")


def main():
    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", 18765))
        except OSError:
            sys.exit("Port 18765 is in use; no existing service was changed")
    binary = ROOT / "agent/target/release/sentinel-agent"
    if not binary.exists():
        sys.exit("Build the release Rust agent first")
    with tempfile.TemporaryDirectory(prefix="sentinel-smoke-") as temporary:
        directory = Path(temporary)
        env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"), SENTINEL_DEVELOPMENT="true",
                   SENTINEL_PUBLIC_ORIGIN="http://localhost:3000", SENTINEL_DATABASE_URL=f"sqlite:///{directory}/test.db",
                   DBUS_SESSION_BUS_ADDRESS=f"unix:path={directory}/absent-session-bus",
                   DBUS_SYSTEM_BUS_ADDRESS=f"unix:path={directory}/absent-system-bus")
        engine, factory = make_database(env["SENTINEL_DATABASE_URL"])
        password = secrets.token_urlsafe(24)
        with factory() as db:
            db.add(User(id="smoke-owner", username="smoke-owner", password_hash=password_hasher.hash(password)))
            db.commit()
        engine.dispose()
        config = directory / "agent/config.json"
        output = open(directory / "server.log", "w")
        server = None

        def start():
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1", "--port", "18765", "--no-access-log"],
                                       cwd=ROOT, env=env, stdout=output, stderr=output)
            try:
                wait_server("http://127.0.0.1:18765/api/health", process)
            except Exception:
                process.terminate()
                process.wait(timeout=10)
                raise
            return process

        def run_agent(*args, stdin=None, success=True):
            result = subprocess.run([str(binary), "--config", str(config), *args], cwd=ROOT, env=env, input=stdin,
                                    text=True, capture_output=True, timeout=40)
            assert (result.returncode == 0) == success, "Unexpected agent result: " + result.stderr
            return result

        try:
            server = start()
            with httpx.Client(base_url="http://127.0.0.1:18765", headers={"Origin": "http://localhost:3000"}) as owner:
                login = owner.post("/api/auth/login", json={"username": "smoke-owner", "password": password})
                assert login.status_code == 200
                owner.headers["X-CSRF-Token"] = login.json()["csrf"]
                pair = owner.post("/api/enrollment").json()["token"]
                run_agent("enroll", "--server", "http://127.0.0.1:18765", "--development", stdin=pair + "\n")
                assert config.stat().st_mode & 0o777 == 0o600
                run_agent("run", "--once")
                device = owner.get("/api/device").json()["device"]
                assert device["status"] == "online" and device["location"] is None and device["location_status"] == "disabled"
                print("PASS: actual Rust enrollment + authenticated HTTP heartbeat + unavailable location")
                command = owner.post(f"/api/devices/{device['id']}/lock", json={"confirmed": True}).json()
                assert command["status"] == "pending"
                run_agent("run", "--once")
                result = owner.get("/api/device").json()["device"]["commands"][0]
                assert result["status"] == "failed" and result["detail"] == "lock_unavailable"
                print("PASS: signed lock delivery + honest failure with isolated absent GNOME bus")
                ledger = json.loads((config.parent / "state.json").read_text())
                assert ledger["commands"][command["id"]]["acknowledged"]
                server.terminate()
                server.wait(timeout=10)
                server = None
                run_agent("run", "--once", success=False)
                assert json.loads((config.parent / "state.json").read_text())["last_contact"] == ledger["last_contact"]
                server = start()
                run_agent("run", "--once")
                assert owner.get("/api/device").json()["device"]["status"] == "online"
                print("PASS: backend outage, persisted contact/ledger and reconnect after backend restart")
                owner.delete(f"/api/devices/{device['id']}").raise_for_status()
                run_agent("run", "--once", success=False)
                print("PASS: revoked credentials rejected by actual Rust agent HTTP request")
        finally:
            if server:
                server.terminate()
                server.wait(timeout=10)
            output.close()


if __name__ == "__main__":
    main()
