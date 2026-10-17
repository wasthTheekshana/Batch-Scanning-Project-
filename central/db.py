"""Engine and session factory. The schema is managed by Alembic migrations
(central/migrate.py): starting the service brings the database to the latest one."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from central import migrate


def make_engine(database_url: str):
    if database_url.startswith("sqlite"):
        engine = create_engine(
            database_url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _enforce_fks(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    else:
        engine = create_engine(database_url, pool_pre_ping=True)
    migrate.upgrade(engine)
    return engine


def make_session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
