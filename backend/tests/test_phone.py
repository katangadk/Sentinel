import httpx
from fastapi.testclient import TestClient
from app.config import Settings
from app.phone import create_phone_app


ORIGIN = "https://phone-preview.example.com"


def make_phone(app, transport=None):
    return create_phone_app(Settings(database_url=app.state.settings.database_url, public_origin=ORIGIN),
                            proxy_transport=transport or httpx.MockTransport(lambda request: httpx.Response(200, stream=httpx.ByteStream(b"Sentinel dashboard"))))


def test_phone_rejects_http_and_does_not_forward_development_docs(app):
    with TestClient(make_phone(app), base_url="http://phone-preview.example.com") as phone:
        assert phone.get("/api/health").status_code == 400
    with TestClient(make_phone(app), base_url=ORIGIN) as phone:
        assert phone.get("/api/docs").status_code == 404
        assert phone.get("/api/openapi.json").status_code == 404
        assert phone.get("/api/unknown").status_code == 404
        assert phone.get("/api/device").status_code == 401


def test_phone_proxies_static_pwa_without_leaking_cookie_to_frontend(app):
    seen = []
    def frontend(request):
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"Sentinel dashboard"), headers={"Content-Type": "text/html", "Content-Security-Policy": "default-src 'self'"})
    with TestClient(make_phone(app, httpx.MockTransport(frontend)), base_url=ORIGIN) as phone:
        response = phone.get("/?test=1", headers={"Cookie": "sentinel_session=private", "Authorization": "Bearer private"})
        assert response.status_code == 200 and response.text == "Sentinel dashboard"
        assert response.headers["content-security-policy"] == "default-src 'self'"
        assert seen[0].url.host == "127.0.0.1" and seen[0].url.query == b"test=1"
        assert "cookie" not in seen[0].headers and "authorization" not in seen[0].headers


def test_phone_uses_existing_owner_device_and_secure_csrf_lock(app, owner, device):
    with TestClient(make_phone(app), base_url=ORIGIN) as phone:
        login = phone.post("/api/auth/login", headers={"Origin": ORIGIN}, json={"username": "owner", "password": "test-only-password"})
        assert login.status_code == 200
        cookie = login.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        phone.headers.update({"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf"]})
        data = phone.get("/api/device").json()["device"]
        assert data["id"] == device["device_id"]
        path = f"/api/devices/{device['device_id']}/lock"
        assert phone.post(path, headers={"Origin": "http://localhost:3100"}, json={"confirmed": True}).status_code == 403
        assert phone.post(path, headers={"X-CSRF-Token": "wrong"}, json={"confirmed": True}).status_code == 403
        requested = phone.post(path, json={"confirmed": True})
        assert requested.status_code == 200 and requested.json()["status"] == "pending"
        assert owner.get("/api/device").json()["device"]["commands"][0]["id"] == requested.json()["id"]


def test_phone_static_failure_returns_honest_502(app):
    def unavailable(request):
        raise httpx.ConnectError("test unavailable", request=request)
    with TestClient(make_phone(app, httpx.MockTransport(unavailable)), base_url=ORIGIN) as phone:
        response = phone.get("/")
        assert response.status_code == 502
        assert response.json()["detail"] == "Dashboard server is unavailable"
