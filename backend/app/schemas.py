from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Strict):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.@-]+$")
    password: str = Field(min_length=1, max_length=256)


class Enroll(Strict):
    token: str = Field(min_length=32, max_length=128)
    name: str = Field(min_length=1, max_length=80)


class Location(Strict):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    accuracy_m: float | None = Field(default=None, ge=0, le=20000000, allow_inf_nan=False)
    source: Literal["geoclue", "ip_coarse", "google_wifi"]
    timestamp: int = Field(gt=0)


class SecurityEvent(Strict):
    id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    kind: Literal["auth_failures", "network_change"]
    occurred_at: int = Field(gt=0)
    count: int = Field(default=1, ge=1, le=100000)


class Heartbeat(Strict):
    location: Location | None = None
    location_status: Literal["available", "unavailable", "disabled"]
    capabilities: dict[str, str] = Field(default_factory=dict, max_length=8)
    events: list[SecurityEvent] = Field(default_factory=list, max_length=100)
    location_provider: Literal["legacy", "google_wifi"] = "legacy"


class WifiAccessPoint(Strict):
    bssid: str = Field(pattern=r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
    signal_dbm: int | None = Field(default=None, ge=-128, le=-10, strict=True)

    @field_validator("bssid")
    @classmethod
    def suitable_bssid(cls, value):
        octets = bytes.fromhex(value.replace(":", ""))
        if octets[0] & 3 or not any(octets) or octets[:3] == b"\x00\x00\x5e":
            raise ValueError("A universally administered unicast Wi-Fi BSSID is required")
        return value.lower()


class WifiLocationRequest(Strict):
    consent: Literal[True]
    scanned_at: int = Field(gt=0)
    access_points: list[WifiAccessPoint] = Field(max_length=20)
    scan_error: Literal["", "scan_unavailable", "insufficient_access_points"] = ""


class LockRequest(Strict):
    confirmed: Literal[True]


class Ack(Strict):
    status: Literal["successful", "failed", "expired"]
    detail: Literal["locked_verified", "lock_unavailable", "execution_interrupted", "expired", "invalid_command"]


class PushKeys(Strict):
    p256dh: str = Field(min_length=80, max_length=100, pattern=r"^[A-Za-z0-9_=-]+$")
    auth: str = Field(min_length=20, max_length=32, pattern=r"^[A-Za-z0-9_=-]+$")


class PushSubscription(Strict):
    endpoint: str = Field(max_length=2048)
    keys: PushKeys
