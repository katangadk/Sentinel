from pathlib import Path
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .models import Base


def make_database(url):
    if not url.startswith("sqlite:///"):
        raise ValueError("Sentinel V1 requires a SQLite file database")
    os.umask(0o077)
    database_path = None
    if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
        database_path = Path(url.removeprefix("sqlite:///"))
        database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if database_path.is_symlink():
            raise ValueError("Database path must not be a symlink")
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def sqlite_config(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    if database_path is not None:
        database_path.chmod(0o600)
    return engine, sessionmaker(engine, expire_on_commit=False)
