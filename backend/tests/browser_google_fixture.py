"""Isolated browser QA only: Google replies are mocked, never billable."""
import os
import httpx
from app.config import Settings
from app.main import create_app as sentinel_app


def create_app():
    settings = Settings()
    if os.getenv("SENTINEL_BROWSER_GOOGLE_FIXTURE") != "1" or "/tmp/sentinel-browser-servers-" not in settings.database_url:
        raise RuntimeError("Browser fixture requires the isolated temporary smoke database")
    settings.google_geolocation_api_key = "browser-mock-key-not-real"
    settings.google_accuracy_cache_authorized = True  # simulated contractual permission, QA only
    return sentinel_app(settings, geolocation_transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={"location": {"lat": -12.968, "lng": 28.636}, "accuracy": 42.5})))
