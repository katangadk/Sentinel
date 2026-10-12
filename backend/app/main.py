import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from uuid import uuid4
from urllib.parse import urlsplit
from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy import case, delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from . import geolocation, schemas
from .config import Settings
from .database import make_database
from .models import Audit, Command, Device, Enrollment, Event, LocationControl, RateBucket, Session, Subscription, User
from .push import PUSH_HOSTS, deliver
from .security import command_signature, digest, dummy_hash, now, password_hasher, token

log = logging.getLogger("sentinel")


def status(last_seen, at=None):
    if last_seen is None:
        return "unreachable"
    age = (now() if at is None else at) - last_seen
    return "online" if age <= 90 else "stale" if age <= 180 else "offline"


def create_app(settings=None, *, start_worker=True, geolocation_transport=None):
    settings = settings or Settings()
    settings.validate()
    engine, factory = make_database(settings.database_url)
    with factory() as db:
        geolocation.expire_locations(db, settings)
        db.commit()

    async def push_worker():
        while True:
            try:
                await asyncio.to_thread(deliver, factory, settings)
                with factory() as db:
                    cutoff = now() - 30 * 86400
                    for model, column in ((Event, Event.received_at), (Audit, Audit.at), (Command, Command.issued_at)):
                        db.execute(delete(model).where(column < cutoff))
                    db.execute(delete(Session).where(Session.expires_at < now()))
                    db.execute(delete(Enrollment).where(Enrollment.expires_at < now()))
                    db.execute(delete(RateBucket).where(RateBucket.window < now() - 2 * 86400))
                    geolocation.expire_locations(db, settings)
                    db.commit()
                # Erase stale page versions from SQLite WAL as well as live rows.
                # SQLite returns busy if a reader is active; the worker retries.
                with engine.connect() as connection:
                    connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                log.error("Push/retention worker failed; will retry")
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(_):
        task = asyncio.create_task(push_worker()) if start_worker else None
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        engine.dispose()

    app = FastAPI(title="Sentinel", version="1.0.0", lifespan=lifespan,
                  docs_url="/api/docs" if settings.development else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if settings.development else None)
    app.state.factory = factory
    app.state.settings = settings

    @app.middleware("http")
    async def transport(request, call_next):
        if not settings.development and request.url.scheme != "https":
            return Response("HTTPS required", status_code=400)
        if request.method in {"POST", "PUT", "PATCH"}:
            chunks = []
            length = 0
            async for chunk in request.stream():
                length += len(chunk)
                if length > 65536:
                    return Response("Request too large", status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def db_session():
        with factory() as db:
            yield db

    def rate(db, key, limit, seconds=300):
        # Atomic UPSERT: works across threads/restarts in the supported single worker.
        window = now() // seconds * seconds
        key = digest(key)
        stmt = insert(RateBucket).values(key=key, count=1, window=window)
        db.execute(stmt.on_conflict_do_update(index_elements=[RateBucket.key], set_={
            "count": case((RateBucket.window == window, RateBucket.count + 1), else_=1),
            "window": window,
        }))
        db.commit()
        bucket = db.get(RateBucket, key, populate_existing=True)
        if bucket.count > limit:
            raise HTTPException(429, "Too many requests; retry later", headers={"Retry-After": str(seconds)})

    def browser_origin(request):
        if request.headers.get("origin") != settings.public_origin:
            raise HTTPException(403, "Invalid request origin")

    def owner(request: Request, db=Depends(db_session)):
        value = request.cookies.get("sentinel_session", "")
        session = db.get(Session, digest(value)) if value else None
        if not session or session.expires_at <= now():
            raise HTTPException(401, "Login required")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            browser_origin(request)
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
                raise HTTPException(403, "Invalid CSRF token")
        return session

    def agent(request: Request, db=Depends(db_session)):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or len(header) > 256:
            raise HTTPException(401, "Invalid device credentials")
        secret = header[7:]
        device = db.scalar(select(Device).where(Device.credential_hash == digest(secret)))
        if not device:
            rate(db, "agent:" + (request.client.host if request.client else "unknown"), 30)
            raise HTTPException(401, "Invalid device credentials")
        return device, secret

    def owned(db, device_id, user_id):
        device = db.get(Device, device_id)
        if not device or device.owner_id != user_id:
            raise HTTPException(404, "Device not found")
        return device

    def audit(db, actor, command, action):
        db.add(Audit(id=str(uuid4()), at=now(), actor=actor, device_id=command.device_id,
                     command_id=command.id, action=action))

    def expire(db, device_id):
        rows = db.scalars(select(Command).where(Command.device_id == device_id, Command.expires_at <= now(),
                                               Command.status.in_(["pending", "delivered"]))).all()
        for command in rows:
            command.status, command.detail = "expired", "No verified result before expiration"
            audit(db, "backend", command, "expired")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "1.0.0"}

    @app.post("/api/auth/login")
    def login(body: schemas.Login, request: Request, response: Response, db=Depends(db_session)):
        browser_origin(request)
        rate(db, "login-ip:" + (request.client.host if request.client else "unknown"), 20)
        rate(db, "login-user:" + body.username.lower(), 10)
        user = db.scalar(select(User).where(User.username == body.username.lower()))
        try:
            password_hasher.verify(user.password_hash if user else dummy_hash, body.password)
        except VerificationError:
            raise HTTPException(401, "Invalid username or password") from None
        if not user:
            raise HTTPException(401, "Invalid username or password")
        raw = token()
        session = Session(token_hash=digest(raw), user_id=user.id, csrf=token(), expires_at=now() + 43200)
        db.add(session)
        db.commit()
        response.set_cookie("sentinel_session", raw, max_age=43200, secure=not settings.development,
                            httponly=True, samesite="strict", path="/")
        return {"username": user.username, "csrf": session.csrf}

    @app.get("/api/auth/me")
    def me(session=Depends(owner), db=Depends(db_session)):
        return {"username": db.get(User, session.user_id).username, "csrf": session.csrf}

    @app.post("/api/auth/logout")
    def logout(response: Response, session=Depends(owner), db=Depends(db_session)):
        db.delete(session)
        db.commit()
        response.delete_cookie("sentinel_session", path="/")
        return {"ok": True}

    @app.post("/api/enrollment")
    def pairing(session=Depends(owner), db=Depends(db_session)):
        rate(db, "enroll-owner:" + session.user_id, 5)
        if db.scalar(select(Device).where(Device.owner_id == session.user_id)):
            raise HTTPException(409, "A laptop is already enrolled; revoke it before pairing again")
        db.execute(delete(Enrollment).where(Enrollment.user_id == session.user_id))
        raw = token()
        db.add(Enrollment(token_hash=digest(raw), user_id=session.user_id, expires_at=now() + 600))
        db.commit()
        return {"token": raw, "expires_at": now() + 600}

    @app.post("/api/agent/enroll")
    def enroll(body: schemas.Enroll, request: Request, db=Depends(db_session)):
        rate(db, "pair-ip:" + (request.client.host if request.client else "unknown"), 10)
        pair = db.get(Enrollment, digest(body.token))
        if not pair or pair.used or pair.expires_at <= now():
            raise HTTPException(401, "Invalid or expired pairing token")
        result = db.execute(update(Enrollment).where(Enrollment.token_hash == pair.token_hash,
                            Enrollment.used == False, Enrollment.expires_at > now()).values(used=True))
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "Pairing token already consumed")
        raw = token()
        device = Device(id=str(uuid4()), owner_id=pair.user_id, name=body.name, credential_hash=digest(raw))
        db.add(device)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Laptop already enrolled") from None
        return {"device_id": device.id, "credential": raw}

    @app.get("/api/device")
    def dashboard(session=Depends(owner), db=Depends(db_session)):
        geolocation.expire_locations(db, settings)
        device = db.scalar(select(Device).where(Device.owner_id == session.user_id))
        if not device:
            return {"device": None, "server_time": now()}
        expire(db, device.id)
        db.commit()
        commands = db.scalars(select(Command).where(Command.device_id == device.id).order_by(Command.issued_at.desc()).limit(10)).all()
        alerts = db.scalars(select(Event).where(Event.device_id == device.id).order_by(Event.received_at.desc()).limit(30)).all()
        control = db.get(LocationControl, device.id)
        return {"server_time": now(), "device": {
            "id": device.id, "name": device.name, "status": status(device.last_seen), "last_seen": device.last_seen,
            "location": json.loads(device.location) if device.location else None,
            "location_status": device.location_status, "capabilities": json.loads(device.capabilities),
            "location_error": geolocation.ERROR_MESSAGES.get(control.error, "") if control else "",
            "location_accuracy_retained": settings.google_accuracy_cache_authorized,
            "commands": [{"id": c.id, "status": c.status, "issued_at": c.issued_at, "expires_at": c.expires_at, "detail": c.detail} for c in commands],
            "alerts": [{"id": e.id, "kind": e.kind, "occurred_at": e.occurred_at, "message": e.message, "push_status": e.push_status} for e in alerts],
        }}

    @app.post("/api/devices/{device_id}/lock")
    def lock(device_id: str, body: schemas.LockRequest, session=Depends(owner), db=Depends(db_session)):
        owned(db, device_id, session.user_id)
        rate(db, "lock:" + device_id, 5)
        expire(db, device_id)
        existing = db.scalar(select(Command).where(Command.device_id == device_id, Command.status.in_(["pending", "delivered"])))
        if existing:
            db.commit()
            return {"id": existing.id, "status": existing.status, "expires_at": existing.expires_at}
        command = Command(id=str(uuid4()), device_id=device_id, issued_at=now(), expires_at=now() + 120)
        db.add(command)
        audit(db, session.user_id, command, "requested")
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(Command).where(Command.device_id == device_id, Command.status.in_(["pending", "delivered"])))
            if existing:
                return {"id": existing.id, "status": existing.status, "expires_at": existing.expires_at}
            raise HTTPException(409, "Lock request conflicted; refresh and retry") from None
        return {"id": command.id, "status": command.status, "expires_at": command.expires_at}

    @app.delete("/api/devices/{device_id}")
    def revoke(device_id: str, session=Depends(owner), db=Depends(db_session)):
        device = owned(db, device_id, session.user_id)
        for model in (Command, Event, LocationControl):
            db.execute(delete(model).where(model.device_id == device_id))
        db.delete(device)
        db.commit()
        return {"ok": True}

    @app.post("/api/agent/heartbeat")
    def heartbeat(body: schemas.Heartbeat, auth=Depends(agent), db=Depends(db_session)):
        device, secret = auth
        rate(db, "heartbeat:" + device.id, 12, 60)
        at = now()
        if body.location and body.location.source == "google_wifi":
            raise HTTPException(422, "Google locations must be obtained by the backend from Wi-Fi signals")
        if body.location_provider == "google_wifi" and body.location:
            raise HTTPException(422, "Google primary mode does not accept alternate location reports")
        if body.location and (body.location.timestamp > at + 30 or body.location.timestamp < at - 86400):
            raise HTTPException(422, "Location timestamp is outside the accepted interval")
        if (body.location is not None) != (body.location_status == "available"):
            raise HTTPException(422, "Location and availability must agree")
        if any(len(k) > 40 or len(v) > 250 for k, v in body.capabilities.items()):
            raise HTTPException(422, "Capability report too long")
        if any(e.occurred_at > at + 30 or e.occurred_at < at - 7 * 86400 for e in body.events):
            raise HTTPException(422, "Event timestamp outside accepted interval")
        device.last_seen = at
        device.capabilities = json.dumps(body.capabilities)
        geolocation.use_provider(db, device, body.location_provider)
        if body.location_provider == "legacy":
            device.location_status = body.location_status
            if body.location:
                device.location = body.location.model_dump_json()
        elif device.location_status == "not_reported":
            device.location_status = "unavailable"
        accepted = []
        for event in body.events:
            accepted.append(event.id)
            exists = db.scalar(select(Event).where(Event.device_id == device.id, Event.event_id == event.id))
            if exists:
                continue
            if event.kind == "auth_failures" and event.count < 5:
                raise HTTPException(422, "Authentication alert threshold is five failures")
            # Separate event identities also receive a ten-minute per-rule cooldown.
            recent = db.scalar(select(Event).where(Event.device_id == device.id, Event.kind == event.kind,
                                                   Event.received_at > at - 600))
            if recent:
                continue
            message = (f"{event.count} failed authentication attempts within five minutes" if event.kind == "auth_failures"
                       else "Active network changed and remained stable for two observations")
            db.add(Event(id=str(uuid4()), event_id=event.id, device_id=device.id, kind=event.kind,
                         occurred_at=event.occurred_at, received_at=at, message=message))
            db.flush()
        expire(db, device.id)
        commands = db.scalars(select(Command).where(Command.device_id == device.id, Command.status.in_(["pending", "delivered"]),
                                                   Command.expires_at > at)).all()
        payload = []
        for command in commands:
            if command.status == "pending":
                command.status, command.delivered_at = "delivered", at
                audit(db, device.id, command, "delivered")
            payload.append({"id": command.id, "device_id": device.id, "action": "lock", "issued_at": command.issued_at,
                            "expires_at": command.expires_at, "signature": command_signature(secret, command)})
        db.commit()
        return {"server_time": at, "commands": payload, "accepted_events": accepted}

    @app.post("/api/agent/location/wifi")
    def wifi_location(body: schemas.WifiLocationRequest, request: Request, auth=Depends(agent), db=Depends(db_session)):
        # Sensitive Wi-Fi reports require TLS even in local development.
        if request.url.scheme != "https":
            raise HTTPException(400, "Wi-Fi location reports require HTTPS")
        if not now() - 120 <= body.scanned_at <= now() + 30:
            raise HTTPException(422, "Wi-Fi scan is stale or in the future")
        device, _ = auth
        rate(db, "wifi-report:" + device.id, 12, 60)
        return geolocation.position(db, device.id, body, settings, transport=geolocation_transport)

    @app.post("/api/agent/commands/{command_id}/ack")
    def acknowledge(command_id: str, body: schemas.Ack, auth=Depends(agent), db=Depends(db_session)):
        device, _ = auth
        command = db.get(Command, command_id)
        if not command or command.device_id != device.id:
            raise HTTPException(404, "Command not found")
        if command.status in {"successful", "failed", "expired"}:
            if command.status == body.status:
                return {"status": command.status}
            raise HTTPException(409, "Command already finalized")
        if command.status != "delivered":
            raise HTTPException(409, "Command has not been delivered")
        if now() >= command.expires_at:
            command.status, command.detail = "expired", "Result arrived after expiration; execution unconfirmed"
        else:
            if (body.status == "successful") != (body.detail == "locked_verified"):
                raise HTTPException(422, "Invalid result detail")
            command.status, command.detail = body.status, body.detail
        audit(db, device.id, command, command.status)
        db.commit()
        return {"status": command.status}

    @app.get("/api/push/key")
    def push_key(session=Depends(owner)):
        return {"public_key": settings.vapid_public_key, "configured": bool(settings.vapid_private_key and settings.vapid_public_key)}

    @app.post("/api/push/subscription")
    def subscribe(body: schemas.PushSubscription, session=Depends(owner), db=Depends(db_session)):
        try:
            endpoint = urlsplit(body.endpoint)
            invalid = endpoint.scheme != "https" or endpoint.hostname not in PUSH_HOSTS or endpoint.port not in {None, 443} or endpoint.username or endpoint.fragment
        except ValueError:
            invalid = True
        if invalid:
            raise HTTPException(422, "Unsupported push service endpoint")
        rate(db, "push:" + session.user_id, 10)
        sub = db.scalar(select(Subscription).where(Subscription.endpoint == body.endpoint))
        if sub and sub.user_id != session.user_id:
            raise HTTPException(409, "Subscription unavailable")
        if not sub:
            total = len(db.scalars(select(Subscription).where(Subscription.user_id == session.user_id)).all())
            if total >= 5:
                raise HTTPException(409, "Maximum five notification subscriptions")
            sub = Subscription(id=str(uuid4()), user_id=session.user_id, endpoint=body.endpoint)
            db.add(sub)
        sub.keys = body.keys.model_dump_json()
        db.commit()
        return {"ok": True}

    @app.delete("/api/push/subscription")
    def unsubscribe(session=Depends(owner), db=Depends(db_session)):
        db.execute(delete(Subscription).where(Subscription.user_id == session.user_id))
        db.commit()
        return {"ok": True}

    return app
