import hashlib
import hmac
import secrets
import time
from argon2 import PasswordHasher

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
# Equal-cost verification for unknown accounts; never an account credential.
dummy_hash = password_hasher.hash(secrets.token_urlsafe(32))


def now():
    return int(time.time())


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def token():
    return secrets.token_urlsafe(32)


def command_signature(secret: str, command):
    text = f"{command.id}\n{command.device_id}\nlock\n{command.issued_at}\n{command.expires_at}"
    return hmac.new(secret.encode(), text.encode(), hashlib.sha256).hexdigest()
