from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import settings


def make_engine(url):
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {},
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure(dbapi, _):
            dbapi.execute("PRAGMA foreign_keys=ON")
            dbapi.execute("PRAGMA journal_mode=WAL")
            dbapi.execute("PRAGMA synchronous=FULL")
            dbapi.execute("PRAGMA busy_timeout=30000")

    return engine


engine = make_engine(settings.resolved_database_url)
Session = sessionmaker(engine, expire_on_commit=False)
