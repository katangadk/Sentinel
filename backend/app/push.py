import json
from sqlalchemy import select
from pywebpush import webpush, WebPushException
from .models import Device, Event, Subscription
from .security import now

# A closed allowlist prevents user-supplied subscription URLs from becoming SSRF.
# No redirects accepted by webpush's custom requests session (below).
PUSH_HOSTS = {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}


def deliver(factory, settings):
    import requests

    class NoRedirectSession(requests.Session):
        def post(self, url, **kwargs):
            kwargs["allow_redirects"] = False
            return super().post(url, **kwargs)

    with factory() as db:
        rows = db.scalars(select(Event).where(Event.push_status == "pending", Event.next_attempt <= now()).limit(20)).all()
        for alert in rows:
            device = db.get(Device, alert.device_id)
            subscriptions = db.scalars(select(Subscription).where(Subscription.user_id == device.owner_id)).all()
            if not settings.vapid_private_key or not subscriptions:
                alert.push_status = "not_configured" if not settings.vapid_private_key else "no_subscription"
                continue
            failure = False
            for sub in subscriptions:
                try:
                    response = webpush(
                        subscription_info={"endpoint": sub.endpoint, "keys": json.loads(sub.keys)},
                        data=json.dumps({"title": "Sentinel security alert", "body": alert.message, "tag": alert.id}),
                        vapid_private_key=settings.vapid_private_key,
                        vapid_claims={"sub": settings.vapid_subject},
                        ttl=300, timeout=10, requests_session=NoRedirectSession(),
                    )
                    if response is not None and not 200 <= response.status_code < 300:
                        failure = True
                except WebPushException as exc:
                    if exc.response is not None and exc.response.status_code in {404, 410}:
                        db.delete(sub)
                    else:
                        failure = True
                except Exception:
                    failure = True
            alert.push_attempts += 1
            alert.push_status = "failed" if failure and alert.push_attempts >= 3 else "pending" if failure else "sent"
            alert.next_attempt = now() + 60 * 2 ** alert.push_attempts
        db.commit()
