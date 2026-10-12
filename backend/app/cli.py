import argparse
import getpass
import os
import re
from uuid import uuid4
from sqlalchemy import select, delete
from .config import Settings
from .database import make_database
from .models import Session, User
from .security import password_hasher


def main():
    parser = argparse.ArgumentParser(description="Sentinel owner provisioning")
    parser.add_argument("action", choices=["create-owner", "reset-password"])
    parser.add_argument("username")
    args = parser.parse_args()
    username = args.username.lower()
    if not re.fullmatch(r"[a-z0-9_.@-]{1,64}", username):
        parser.error("Invalid username")
    password = getpass.getpass("Password (minimum 12 characters): ")
    if len(password) < 12 or len(password) > 256 or password != getpass.getpass("Confirm password: "):
        parser.error("Passwords must match and contain 12–256 characters")
    os.umask(0o077)
    engine, factory = make_database(Settings().database_url)
    with factory() as db:
        user = db.scalar(select(User).where(User.username == username))
        if args.action == "create-owner":
            if user:
                parser.error("Owner already exists")
            user = User(id=str(uuid4()), username=username, password_hash=password_hasher.hash(password))
            db.add(user)
        else:
            if not user:
                parser.error("Owner not found")
            user.password_hash = password_hasher.hash(password)
            db.execute(delete(Session).where(Session.user_id == user.id))
        db.commit()
    engine.dispose()
    print("Owner configured.")


if __name__ == "__main__":
    main()
