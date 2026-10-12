import json
from unittest.mock import Mock
from sqlalchemy import select
from app.models import Event, Subscription
from app.push import deliver
from app.security import now


def test_push_worker_uses_real_outbox_and_records_delivery(app, owner, device, heartbeat, monkeypatch):
    import uuid
    owner.post("/api/agent/heartbeat", headers={"Authorization": "Bearer " + device["credential"]}, json={**heartbeat,
               "events": [{"id": str(uuid.uuid4()), "kind": "network_change", "occurred_at": now()}]})
    with app.state.factory() as db:
        db.add(Subscription(id="sub", user_id="owner", endpoint="https://fcm.googleapis.com/fcm/send/test", keys=json.dumps({"p256dh": "test", "auth": "test"})))
        db.commit()
    app.state.settings.vapid_private_key = "mock-only-key"
    app.state.settings.vapid_subject = "mailto:owner@example.com"
    sender = Mock(return_value=Mock(status_code=201))
    monkeypatch.setattr("app.push.webpush", sender)
    deliver(app.state.factory, app.state.settings)
    assert sender.call_count == 1
    assert json.loads(sender.call_args.kwargs["data"])["title"] == "Sentinel security alert"
    with app.state.factory() as db:
        assert db.scalar(select(Event)).push_status == "sent"
    deliver(app.state.factory, app.state.settings)
    assert sender.call_count == 1


def test_push_failure_is_visible_and_retries(app, owner, device, heartbeat, monkeypatch):
    import uuid
    owner.post("/api/agent/heartbeat", headers={"Authorization": "Bearer " + device["credential"]}, json={**heartbeat,
               "events": [{"id": str(uuid.uuid4()), "kind": "network_change", "occurred_at": now()}]})
    with app.state.factory() as db:
        db.add(Subscription(id="sub", user_id="owner", endpoint="https://fcm.googleapis.com/fcm/send/test", keys="{}"))
        db.commit()
    app.state.settings.vapid_private_key = "mock-only-key"
    monkeypatch.setattr("app.push.webpush", Mock(side_effect=RuntimeError("mock network failure")))
    for attempt in range(3):
        with app.state.factory() as db:
            db.scalar(select(Event)).next_attempt = 0
            db.commit()
        deliver(app.state.factory, app.state.settings)
    with app.state.factory() as db:
        assert db.scalar(select(Event)).push_status == "failed"
        assert db.scalar(select(Event)).push_attempts == 3
