import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.models import User
from app.security import password_hasher


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/test.db", development=True,
                              public_origin="http://localhost:3000"), start_worker=False)
    with app.state.factory() as db:
        for name in ("owner", "other"):
            db.add(User(id=name, username=name, password_hash=password_hasher.hash("test-only-password")))
        db.commit()
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def owner(client):
    response = client.post("/api/auth/login", headers={"Origin": "http://localhost:3000"},
                           json={"username": "owner", "password": "test-only-password"})
    assert response.status_code == 200
    client.headers.update({"Origin": "http://localhost:3000", "X-CSRF-Token": response.json()["csrf"]})
    return client


@pytest.fixture
def device(owner):
    pair = owner.post("/api/enrollment").json()["token"]
    response = owner.post("/api/agent/enroll", json={"token": pair, "name": "Test Laptop"})
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def heartbeat():
    return {"location": None, "location_status": "unavailable", "capabilities": {}, "events": []}
