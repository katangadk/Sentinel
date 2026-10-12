import json
import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.config import Settings
from app.main import create_app
from app.models import Device, LocationControl, RateBucket, User
from app.security import now, password_hasher


@pytest.fixture
def google():
    state = SimpleNamespace(calls=[], code=200, data={"location": {"lat": -12.968, "lng": 28.636}, "accuracy": 42.5}, fail=False)
    def handler(request):
        state.calls.append(request)
        if state.fail:
            raise httpx.ConnectError("test provider offline", request=request)
        return httpx.Response(state.code, json=state.data)
    state.transport = httpx.MockTransport(handler)
    return state


@pytest.fixture
def app(tmp_path, google):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/google.db", development=True,
                        public_origin="http://localhost:3000", google_geolocation_api_key="test-key-not-real",
                        google_accuracy_cache_authorized=False)
    app = create_app(settings, start_worker=False, geolocation_transport=google.transport)
    with app.state.factory() as db:
        for name in ("owner", "other"):
            db.add(User(id=name, username=name, password_hash=password_hasher.hash("test-only-password")))
        db.commit()
    return app


def body(**changes):
    return {"consent": True, "scanned_at": now(), "access_points": [
        {"bssid": "F0:D5:BF:FD:12:AE", "signal_dbm": -61},
        {"bssid": "30:86:2d:c4:29:d0"},
    ], **changes}


def report(owner, device, payload=None):
    return owner.post("https://localhost:3000/api/agent/location/wifi",
                      headers={"Authorization": "Bearer " + device["credential"]}, json=payload or body())


def test_wifi_success_google_payload_auth_and_no_backend_ip(owner, device, app, google, heartbeat, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    result = report(owner, device)
    assert result.status_code == 200
    location = result.json()["location"]
    assert location == {"latitude": -12.968, "longitude": 28.636, "accuracy_m": 42.5, "source": "google_wifi", "timestamp": now()}
    request = google.calls[0]
    assert request.url.scheme == "https" and request.url.host == "www.googleapis.com"
    assert request.url.params["key"] == "test-key-not-real"
    assert json.loads(request.content) == {"considerIp": False, "wifiAccessPoints": [
        {"macAddress": "f0:d5:bf:fd:12:ae", "signalStrength": -61}, {"macAddress": "30:86:2d:c4:29:d0"}]}
    assert "test-key-not-real" not in caplog.text
    # Heartbeats never supply or overwrite a Google result from the agent/cloud IP.
    assert owner.post("/api/agent/heartbeat", headers={"Authorization": "Bearer " + device["credential"]},
                      json={**heartbeat, "location_provider": "google_wifi"}).status_code == 200
    saved = owner.get("/api/device").json()["device"]
    assert saved["location"]["latitude"] == -12.968 and saved["location"]["longitude"] == 28.636
    assert saved["location"]["accuracy_m"] is None  # default policy caches coordinates only
    assert saved["location_status"] == "available"
    with app.state.factory() as db:
        assert all(p["bssid"].lower() not in db.get(Device, device["device_id"]).location for p in body()["access_points"])
    assert len(google.calls) == 1


@pytest.mark.parametrize("points", [[], [{"bssid": "30:86:2d:c4:29:d0"}], [{"bssid": "30:86:2d:c4:29:d0"}] * 2])
def test_insufficient_distinct_access_points_never_call_google(owner, device, google, points):
    assert report(owner, device, body(access_points=points)).json()["error"] == "insufficient_access_points"
    assert not google.calls
    assert owner.get("/api/device").json()["device"]["location"] is None


@pytest.mark.parametrize("bssid", ["bad", "00:00:00:00:00:00", "02:11:22:33:44:55", "01:11:22:33:44:55", "ff:ff:ff:ff:ff:ff", "00:00:5e:00:53:01"])
def test_invalid_unsupported_bssids_rejected(owner, device, google, bssid):
    assert report(owner, device, body(access_points=[{"bssid": bssid}])).status_code == 422
    assert not google.calls


@pytest.mark.parametrize("signal", [85, -9, -129, "-60", True])
def test_rssi_is_valid_dbm_never_percentage(owner, device, google, signal):
    assert report(owner, device, body(access_points=[{"bssid": "30:86:2d:c4:29:d0", "signal_dbm": signal}])).status_code == 422
    assert not google.calls


def test_requires_device_auth_consent_https_fresh_bounded_report(owner, device, google):
    endpoint = "https://localhost:3000/api/agent/location/wifi"
    assert owner.post(endpoint, json=body()).status_code == 401
    assert report(owner, device, body(consent=False)).status_code == 422
    assert report(owner, device, body(scanned_at=now() - 121)).status_code == 422
    assert report(owner, device, body(scanned_at=now() + 31)).status_code == 422
    assert report(owner, device, body(access_points=body()["access_points"] * 11)).status_code == 422
    assert owner.post("/api/agent/location/wifi", headers={"Authorization": "Bearer " + device["credential"]}, json=body()).status_code == 400
    assert not google.calls


def test_missing_key_scanner_failure_and_switch_remove_legacy_city(owner, device, app, google, heartbeat):
    previous = {"latitude": -15.4, "longitude": 28.3, "accuracy_m": 25000, "source": "geoclue", "timestamp": now()}
    owner.post("/api/agent/heartbeat", headers={"Authorization": "Bearer " + device["credential"]}, json={**heartbeat, "location": previous, "location_status": "available"})
    app.state.settings.google_geolocation_api_key = ""
    assert report(owner, device).json()["error"] == "not_configured"
    dashboard = owner.get("/api/device").json()["device"]
    assert dashboard["location"] is None and "not configured" in dashboard["location_error"]
    assert report(owner, device, body(access_points=[], scan_error="scan_unavailable")).json()["error"] == "scan_unavailable"
    assert not google.calls


@pytest.mark.parametrize("code,reason,expected", [(400, "keyInvalid", "credentials_or_billing"), (403, "billingNotEnabled", "credentials_or_billing"), (403, "dailyLimitExceeded", "quota"), (429, "", "quota"), (404, "notFound", "not_found"), (500, "", "provider_unavailable"), (302, "", "provider_unavailable")])
def test_google_errors_sanitized_no_ip_fallback_and_backoff(owner, device, app, google, code, reason, expected):
    google.code = code
    google.data = {"error": {"message": "sensitive test-key-not-real", "errors": [{"reason": reason}]}}
    result = report(owner, device).json()
    assert result["error"] == expected
    assert result["location"] is None
    assert "test-key-not-real" not in json.dumps(result)
    assert len(google.calls) == 1 and json.loads(google.calls[0].content)["considerIp"] is False
    assert result["retry_after"] >= (3600 if expected in {"quota", "credentials_or_billing"} else 600)
    assert owner.get("/api/device").json()["device"]["location"] is None


def test_timeout_and_invalid_google_response(owner, device, google):
    google.fail = True
    assert report(owner, device).json()["error"] == "provider_unavailable"


@pytest.mark.parametrize("data", [{"location": {"lat": 91, "lng": 28}, "accuracy": 10}, {"location": {"lat": -12, "lng": 28}, "accuracy": -1}, {"location": {"lat": True, "lng": 28}, "accuracy": 10}])
def test_invalid_coordinates_radius_rejected(owner, device, google, data):
    google.data = data
    assert report(owner, device).json()["error"] == "invalid_response"
    assert owner.get("/api/device").json()["device"]["location"] is None


def test_throttle_shared_across_app_instances_and_daily_cap(owner, device, app, google):
    settings = app.state.settings
    settings.google_daily_limit = 1
    assert report(owner, device).json()["status"] == "available"
    second = create_app(settings, start_worker=False, geolocation_transport=google.transport)
    with TestClient(second) as other:
        assert other.post("https://localhost:3000/api/agent/location/wifi", headers={"Authorization": "Bearer " + device["credential"]}, json=body()).json()["error"] == "throttled"
    with app.state.factory() as db:
        db.get(LocationControl, device["device_id"]).next_attempt = 0
        db.commit()
    assert report(owner, device).json()["error"] == "daily_limit"
    assert len(google.calls) == 1


def test_concurrent_requests_reserve_only_one_paid_attempt(owner, device, app, google):
    def request(_):
        with TestClient(app) as client:
            return client.post("https://localhost:3000/api/agent/location/wifi", headers={"Authorization": "Bearer " + device["credential"]}, json=body()).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request, range(2)))
    assert len(google.calls) == 1
    assert sorted(r["status"] for r in results) == ["available", "unavailable"]


def test_expiry_latest_only_accuracy_permission_owner_only_and_revoke(owner, device, app, google):
    app.state.settings.google_accuracy_cache_authorized = True  # simulate separate contractual permission
    report(owner, device)
    assert owner.get("/api/device").json()["device"]["location"]["accuracy_m"] == 42.5
    with TestClient(app) as other:
        login = other.post("/api/auth/login", headers={"Origin": "http://localhost:3000"}, json={"username": "other", "password": "test-only-password"})
        assert login.status_code == 200 and other.get("/api/device").json()["device"] is None
    with app.state.factory() as db:
        row = db.get(Device, device["device_id"])
        saved = json.loads(row.location)
        saved["timestamp"] = now() - 86400
        row.location = json.dumps(saved)
        db.commit()
    assert owner.get("/api/device").json()["device"]["location"] is None
    assert owner.delete(f"/api/devices/{device['device_id']}").status_code == 200
    assert report(owner, device).status_code == 401


def test_agent_cannot_forge_google_location(owner, device, heartbeat, google):
    forged = {"latitude": -12, "longitude": 28, "accuracy_m": 1, "source": "google_wifi", "timestamp": now()}
    result = owner.post("/api/agent/heartbeat", headers={"Authorization": "Bearer " + device["credential"]}, json={**heartbeat, "location": forged, "location_status": "available"})
    assert result.status_code == 422 and not google.calls
