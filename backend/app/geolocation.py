"""Google Wi-Fi positioning inside the existing device location lifecycle.

Only coordinates may be cached by default. Raw access points, response bodies and
credentials never enter the database or logs. Accuracy caching needs separate
Google permission; see docs/GOOGLE_LOCATION.md.
"""
import json
import logging
import math
import httpx
from sqlalchemy import select, text
from .models import Device, LocationControl, RateBucket
from .security import digest, now

GOOGLE_URL = "https://www.googleapis.com/geolocation/v1/geolocate"
MIN_INTERVAL = 600
ERROR_MESSAGES = {
    "not_configured": "Google Geolocation API key is not configured on the backend",
    "insufficient_access_points": "At least two suitable distinct Wi-Fi access points are required",
    "credentials_or_billing": "Google rejected credentials, API restrictions or billing; check Cloud Console",
    "quota": "Google quota or rate limit reached",
    "not_found": "Google could not position these Wi-Fi access points; IP fallback is disabled",
    "provider_unavailable": "Google location provider is temporarily unavailable",
    "invalid_response": "Google did not return a valid location and accuracy radius",
    "daily_limit": "Sentinel's daily Google request limit has been reached",
    "throttled": "Next Google lookup is scheduled; minimum interval is ten minutes",
    "scan_unavailable": "Wi-Fi scan unavailable; check laptop hardware, NetworkManager, permissions and internet",
}


class NoGoogleRequestLogs(logging.Filter):
    def filter(self, record):
        # httpx's INFO request log includes the API-key query string.
        return "www.googleapis.com/geolocation/" not in record.getMessage()


logging.getLogger("httpx").addFilter(NoGoogleRequestLogs())


def use_provider(db, device, provider):
    control = db.get(LocationControl, device.id)
    if not control:
        control = LocationControl(device_id=device.id, provider=provider, next_attempt=0, error="")
        db.add(control)
    elif control.provider != provider:
        control.provider, control.error = provider, ""
    if device.location:
        saved = json.loads(device.location)
        if (saved.get("source") == "google_wifi") != (provider == "google_wifi"):
            device.location = None
            device.location_status = "unavailable"
    return control


def expire_locations(db, settings):
    for device in db.scalars(select(Device).where(Device.location.is_not(None))):
        saved = json.loads(device.location)
        if saved.get("source") == "google_wifi":
            if saved["timestamp"] + settings.google_location_ttl <= now():
                device.location = None
                device.location_status = "unavailable"
            elif not settings.google_accuracy_cache_authorized and saved.get("accuracy_m") is not None:
                saved["accuracy_m"] = None
                device.location = json.dumps(saved)


def lookup(settings, points, *, transport=None):
    """Single bounded HTTPS attempt. Return sanitized codes, never Google errors."""
    payload = {"considerIp": False, "wifiAccessPoints": [
        {"macAddress": point.bssid, **({"signalStrength": point.signal_dbm} if point.signal_dbm is not None else {})}
        for point in points
    ]}
    try:
        with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False,
                          trust_env=False, transport=transport) as client:
            with client.stream("POST", GOOGLE_URL, params={"key": settings.google_geolocation_api_key}, json=payload) as response:
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 16384:
                        return None, "invalid_response"
                code = response.status_code
        if code != 200:
            # Do not expose Google's error message: it can echo request/key data.
            reason = ""
            try:
                reason = json.loads(content).get("error", {}).get("errors", [{}])[0].get("reason", "")
            except (ValueError, AttributeError, IndexError, TypeError):
                pass
            if code == 429 or reason in {"dailyLimitExceeded", "userRateLimitExceeded", "rateLimitExceeded", "quotaExceeded"}:
                return None, "quota"
            if code in {401, 402, 403} or reason in {"keyInvalid", "billingNotEnabled", "accessNotConfigured"}:
                return None, "credentials_or_billing"
            return None, "not_found" if code == 404 else "provider_unavailable"
        data = json.loads(content)
        lat, lng, accuracy = data["location"]["lat"], data["location"]["lng"], data["accuracy"]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (lat, lng, accuracy)):
            return None, "invalid_response"
        if not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= accuracy <= 20000000):
            return None, "invalid_response"
        return {"latitude": lat, "longitude": lng, "accuracy_m": accuracy,
                "source": "google_wifi", "timestamp": now()}, ""
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None, "provider_unavailable"


def position(db, device_id, body, settings, *, transport=None):
    """Reserve usage before network I/O, across gateway processes and restarts."""
    points = list({p.bssid: p for p in body.access_points}.values())
    # BEGIN IMMEDIATE serializes the reservation, including first-row creation.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    device = db.get(Device, device_id)
    if not device:
        db.rollback()
        return {"status": "unavailable", "error": "revoked", "retry_after": 600}
    control = use_provider(db, device, "google_wifi")
    at = now()
    error = ""
    retry = MIN_INTERVAL
    if body.scan_error:
        error = body.scan_error
    elif len(points) < 2:
        error = "insufficient_access_points"
    elif not settings.google_geolocation_api_key:
        error = "not_configured"
    elif control.next_attempt > at:
        error, retry = "throttled", control.next_attempt - at
    else:
        day = at // 86400 * 86400
        key = digest("google-usage-day:" + str(day))
        bucket = db.get(RateBucket, key)
        if (bucket.count if bucket else 0) >= settings.google_daily_limit:
            error, retry = "daily_limit", day + 86400 - at
        else:
            if not bucket:
                bucket = RateBucket(key=key, count=0, window=day)
                db.add(bucket)
            bucket.count += 1
            control.next_attempt = at + MIN_INTERVAL
    if error:
        if error != "throttled":
            control.error = error
            device.location_status = "unavailable"
        db.commit()
        return {"status": "unavailable", "error": error, "retry_after": retry}
    db.commit()  # Release SQLite lock before calling Google; failed attempts count.
    location, error = lookup(settings, points, transport=transport)
    # APs live only in this request's memory. No retries or background queue.
    db.expire_all()
    device = db.get(Device, device_id)
    control = db.get(LocationControl, device_id)
    if not device or not control or control.provider != "google_wifi":
        return {"status": "unavailable", "error": "revoked", "retry_after": MIN_INTERVAL}
    control.error = error
    if location:
        cached = dict(location)
        if not settings.google_accuracy_cache_authorized:
            cached["accuracy_m"] = None
        device.location = json.dumps(cached)
        device.location_status = "available"
    else:
        device.location_status = "unavailable"
        if error in {"credentials_or_billing", "quota"}:
            control.next_attempt = max(control.next_attempt, now() + 3600)
    db.commit()
    # Radius is available in this live authenticated response, without caching.
    return {"status": "available" if location else "unavailable", "location": location,
            "error": error, "retry_after": max(MIN_INTERVAL, control.next_attempt - now())}
