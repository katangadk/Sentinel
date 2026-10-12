import hashlib
import hmac
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import status
from app.models import Audit, Command, Device, Enrollment, Event, Session
from app.security import now


def agent(device):
    return {"Authorization": "Bearer " + device["credential"]}


def test_login_logout_and_session_revocation(owner, app):
    assert owner.get("/api/auth/me").json()["username"] == "owner"
    cookie = owner.cookies.get("sentinel_session")
    with app.state.factory() as db:
        assert db.scalar(select(Session)).token_hash != cookie
    assert owner.post("/api/auth/logout").status_code == 200
    assert owner.get("/api/device").status_code == 401


def test_invalid_login_and_rate_limit(client):
    for _ in range(10):
        assert client.post("/api/auth/login", headers={"Origin": "http://localhost:3000"},
                           json={"username": "owner", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", headers={"Origin": "http://localhost:3000"},
                       json={"username": "owner", "password": "wrong"}).status_code == 429


def test_csrf_origin_and_confirmation(owner, device):
    assert owner.post("/api/enrollment", headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert owner.post("/api/enrollment", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert owner.post(f"/api/devices/{device['device_id']}/lock", json={"confirmed": False}).status_code == 422


def test_enrollment_single_use_and_unique_credentials(owner, app):
    pair = owner.post("/api/enrollment").json()["token"]
    response = owner.post("/api/agent/enroll", json={"token": pair, "name": "Laptop"})
    assert response.status_code == 200
    assert owner.post("/api/agent/enroll", json={"token": pair, "name": "Laptop"}).status_code == 401
    assert owner.post("/api/enrollment").status_code == 409
    with app.state.factory() as db:
        assert db.scalar(select(Device)).credential_hash != response.json()["credential"]


def test_expired_pairing(owner, app):
    pair = owner.post("/api/enrollment").json()["token"]
    with app.state.factory() as db:
        db.scalar(select(Enrollment)).expires_at = now() - 1
        db.commit()
    assert owner.post("/api/agent/enroll", json={"token": pair, "name": "Laptop"}).status_code == 401


def test_device_credential_and_device_id_not_auth(owner, device, heartbeat):
    assert owner.post("/api/agent/heartbeat", json=heartbeat).status_code == 401
    assert owner.post("/api/agent/heartbeat", headers={"Authorization": "Bearer " + device["device_id"]}, json=heartbeat).status_code == 401
    assert owner.post("/api/agent/heartbeat", headers={"Authorization": "Bearer wrong"}, json=heartbeat).status_code == 401


def test_owner_authorization(app, owner, device):
    with TestClient(app) as other:
        response = other.post("/api/auth/login", headers={"Origin": "http://localhost:3000"},
                              json={"username": "other", "password": "test-only-password"})
        other.headers.update({"Origin": "http://localhost:3000", "X-CSRF-Token": response.json()["csrf"]})
        assert other.get("/api/device").json()["device"] is None
        assert other.post(f"/api/devices/{device['device_id']}/lock", json={"confirmed": True}).status_code == 404
        assert other.delete(f"/api/devices/{device['device_id']}").status_code == 404


def test_heartbeat_and_unavailable_location(owner, device, heartbeat):
    initial = owner.get("/api/device").json()["device"]
    assert initial["status"] == "unreachable" and initial["location"] is None
    assert owner.post("/api/agent/heartbeat", headers=agent(device), json=heartbeat).status_code == 200
    data = owner.get("/api/device").json()["device"]
    assert data["status"] == "online" and data["last_seen"] is not None
    assert data["location"] is None and data["location_status"] == "unavailable"


def test_status_thresholds():
    assert status(None, 1000) == "unreachable"
    assert status(910, 1000) == "online"
    assert status(909, 1000) == "stale"
    assert status(820, 1000) == "stale"
    assert status(819, 1000) == "offline"


def test_location_validation_and_last_known_retention(owner, device, heartbeat):
    location = {"latitude": -15.4, "longitude": 28.3, "accuracy_m": 80, "source": "geoclue", "timestamp": now()}
    assert owner.post("/api/agent/heartbeat", headers=agent(device), json={**heartbeat, "location": location, "location_status": "available"}).status_code == 200
    assert owner.post("/api/agent/heartbeat", headers=agent(device), json=heartbeat).status_code == 200
    result = owner.get("/api/device").json()["device"]
    assert result["location"] == location and result["location_status"] == "unavailable"
    for bad in ({**location, "latitude": 200}, {**location, "timestamp": now() + 100}, {**location, "source": "invented"}):
        assert owner.post("/api/agent/heartbeat", headers=agent(device), json={**heartbeat, "location": bad, "location_status": "available"}).status_code == 422


def test_lock_lifecycle_signature_and_replay(owner, app, device, heartbeat):
    request = owner.post(f"/api/devices/{device['device_id']}/lock", json={"confirmed": True}).json()
    assert request["status"] == "pending"
    commands = owner.post("/api/agent/heartbeat", headers=agent(device), json=heartbeat).json()["commands"]
    command = commands[0]
    assert command["id"] == request["id"] and command["action"] == "lock"
    canonical = f"{command['id']}\n{command['device_id']}\nlock\n{command['issued_at']}\n{command['expires_at']}"
    assert hmac.compare_digest(command["signature"], hmac.new(device["credential"].encode(), canonical.encode(), hashlib.sha256).hexdigest())
    body = {"status": "successful", "detail": "locked_verified"}
    path = f"/api/agent/commands/{command['id']}/ack"
    assert owner.post(path, headers=agent(device), json=body).json()["status"] == "successful"
    assert owner.post(path, headers=agent(device), json=body).status_code == 200
    assert owner.post(path, headers=agent(device), json={"status": "failed", "detail": "lock_unavailable"}).status_code == 409
    assert owner.post("/api/agent/heartbeat", headers=agent(device), json=heartbeat).json()["commands"] == []
    with app.state.factory() as db:
        actions = [a.action for a in db.scalars(select(Audit)).all()]
        assert actions == ["requested", "delivered", "successful"]


def test_offline_expired_commands_and_no_early_ack(owner, app, device, heartbeat):
    request = owner.post(f"/api/devices/{device['device_id']}/lock", json={"confirmed": True}).json()
    path = f"/api/agent/commands/{request['id']}/ack"
    assert owner.post(path, headers=agent(device), json={"status": "successful", "detail": "locked_verified"}).status_code == 409
    with app.state.factory() as db:
        db.get(Command, request["id"]).expires_at = now() - 1
        db.commit()
    assert owner.post("/api/agent/heartbeat", headers=agent(device), json=heartbeat).json()["commands"] == []
    assert owner.get("/api/device").json()["device"]["commands"][0]["status"] == "expired"


def test_failed_lock_and_late_result(owner, app, device, heartbeat):
    request = owner.post(f"/api/devices/{device['device_id']}/lock", json={"confirmed": True}).json()
    owner.post("/api/agent/heartbeat", headers=agent(device), json=heartbeat)
    path = f"/api/agent/commands/{request['id']}/ack"
    assert owner.post(path, headers=agent(device), json={"status": "failed", "detail": "lock_unavailable"}).json()["status"] == "failed"
    request = owner.post(f"/api/devices/{device['device_id']}/lock", json={"confirmed": True}).json()
    owner.post("/api/agent/heartbeat", headers=agent(device), json=heartbeat)
    with app.state.factory() as db:
        db.get(Command, request["id"]).expires_at = now() - 1
        db.commit()
    assert owner.post(f"/api/agent/commands/{request['id']}/ack", headers=agent(device), json={"status": "successful", "detail": "locked_verified"}).json()["status"] == "expired"


def test_alert_threshold_deduplication_and_cooldown(owner, app, device, heartbeat):
    event = {"id": str(uuid4()), "kind": "auth_failures", "occurred_at": now(), "count": 4}
    assert owner.post("/api/agent/heartbeat", headers=agent(device), json={**heartbeat, "events": [event]}).status_code == 422
    event["count"] = 5
    for _ in range(2):
        result = owner.post("/api/agent/heartbeat", headers=agent(device), json={**heartbeat, "events": [event]})
        assert result.json()["accepted_events"] == [event["id"]]
    other = {**event, "id": str(uuid4())}
    owner.post("/api/agent/heartbeat", headers=agent(device), json={**heartbeat, "events": [other]})
    with app.state.factory() as db:
        assert len(db.scalars(select(Event)).all()) == 1
    alerts = owner.get("/api/device").json()["device"]["alerts"]
    assert len(alerts) == 1 and "5 failed" in alerts[0]["message"]


def test_subscription_ssrf_prevention(owner):
    for endpoint in ("http://127.0.0.1/admin", "https://localhost/", "https://fcm.googleapis.com.attacker.example/x", "https://fcm.googleapis.com:8443/x", "https://fcm.googleapis.com:bad/x", "https://[/"):
        assert owner.post("/api/push/subscription", json={"endpoint": endpoint, "keys": {"p256dh": "a" * 87, "auth": "b" * 22}}).status_code == 422


def test_streamed_request_limit(client):
    response = client.post("/api/auth/login", headers={"Origin": "http://localhost:3000", "Content-Type": "application/json"},
                           content=iter([b"x" * 32768, b"x" * 32769]))
    assert response.status_code == 413


def test_duplicate_lock_request_has_one_live_command(owner, app, device):
    path = f"/api/devices/{device['device_id']}/lock"
    first = owner.post(path, json={"confirmed": True}).json()
    second = owner.post(path, json={"confirmed": True}).json()
    assert first["id"] == second["id"]
    with app.state.factory() as db:
        assert len(db.scalars(select(Command)).all()) == 1


def test_revoke_invalidates_agent(owner, device, heartbeat):
    assert owner.delete(f"/api/devices/{device['device_id']}").status_code == 200
    assert owner.get("/api/device").json()["device"] is None
    assert owner.post("/api/agent/heartbeat", headers=agent(device), json=heartbeat).status_code == 401


def test_tls_enforcement_secure_cookie_and_session_expiry(tmp_path):
    from app.config import Settings
    from app.main import create_app
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/tls.db", public_origin="https://sentinel.example", development=False), start_worker=False)
    with TestClient(app, base_url="http://sentinel.example") as http:
        assert http.get("/api/health").status_code == 400
    from app.models import User
    from app.security import password_hasher
    with app.state.factory() as db:
        db.add(User(id="tls-owner", username="owner", password_hash=password_hasher.hash("test-only-password")))
        db.commit()
    with TestClient(app, base_url="https://sentinel.example") as https:
        response = https.post("/api/auth/login", headers={"Origin": "https://sentinel.example"}, json={"username": "owner", "password": "test-only-password"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        with app.state.factory() as db:
            db.scalar(select(Session)).expires_at = now() - 1
            db.commit()
        assert https.get("/api/auth/me").status_code == 401
