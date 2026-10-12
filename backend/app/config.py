import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class Settings:
    database_url: str = os.getenv("SENTINEL_DATABASE_URL", "sqlite:///./data/sentinel.db")
    public_origin: str = os.getenv("SENTINEL_PUBLIC_ORIGIN", "https://localhost")
    development: bool = os.getenv("SENTINEL_DEVELOPMENT", "false").lower() == "true"
    vapid_private_key: str = os.getenv("SENTINEL_VAPID_PRIVATE_KEY", "")
    vapid_public_key: str = os.getenv("SENTINEL_VAPID_PUBLIC_KEY", "")
    vapid_subject: str = os.getenv("SENTINEL_VAPID_SUBJECT", "")
    google_geolocation_api_key: str = field(default_factory=lambda: os.getenv("GOOGLE_GEOLOCATION_API_KEY", ""), repr=False)
    google_daily_limit: int = int(os.getenv("SENTINEL_GOOGLE_DAILY_LIMIT", "144"))
    google_location_ttl: int = int(os.getenv("SENTINEL_GOOGLE_LOCATION_TTL_SECONDS", "86400"))
    # Enable only with separate permission from Google to cache accuracy content.
    google_accuracy_cache_authorized: bool = os.getenv("SENTINEL_GOOGLE_ACCURACY_CACHE_AUTHORIZED", "false").lower() == "true"

    def validate(self):
        if not 0 <= self.google_daily_limit <= 144:
            raise ValueError("Google daily limit must be between 0 and 144")
        if not 600 <= self.google_location_ttl <= 86400:
            raise ValueError("Google location TTL must be between 600 and 86400 seconds")
        origin = urlsplit(self.public_origin)
        if origin.path or origin.query or origin.fragment or origin.username:
            raise ValueError("PUBLIC_ORIGIN must be an origin without a path")
        if not origin.hostname or (not self.development and origin.scheme != "https"):
            raise ValueError("Production requires an HTTPS PUBLIC_ORIGIN")
        if self.development and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Development HTTP is restricted to loopback")
