from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    csrf: Mapped[str] = mapped_column(String)
    expires_at: Mapped[int] = mapped_column(Integer)


class Enrollment(Base):
    __tablename__ = "enrollments"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[int] = mapped_column(Integer)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    name: Mapped[str] = mapped_column(String)
    credential_hash: Mapped[str] = mapped_column(String, unique=True)
    last_seen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_status: Mapped[str] = mapped_column(String, default="not_reported")
    capabilities: Mapped[str] = mapped_column(Text, default="{}")


class LocationControl(Base):
    """Provider state and usage only. Never store Wi-Fi identifiers here."""
    __tablename__ = "location_controls"
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String, default="legacy")
    next_attempt: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String, default="")


class Command(Base):
    __tablename__ = "commands"
    __table_args__ = (Index("one_live_lock_per_device", "device_id", unique=True, sqlite_where=text("status IN ('pending', 'delivered')")),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    issued_at: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(Integer)
    delivered_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    detail: Mapped[str] = mapped_column(String, default="")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("device_id", "event_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    kind: Mapped[str] = mapped_column(String)
    occurred_at: Mapped[int] = mapped_column(Integer)
    received_at: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(String)
    push_status: Mapped[str] = mapped_column(String, default="pending")
    push_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[int] = mapped_column(Integer, default=0)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    keys: Mapped[str] = mapped_column(Text)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    at: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String)
    device_id: Mapped[str] = mapped_column(String)
    command_id: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    window: Mapped[int] = mapped_column(Integer)
