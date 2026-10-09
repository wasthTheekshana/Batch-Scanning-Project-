"""Engine and session factory. Phase 1 creates tables with create_all();
adopt Alembic before the first schema change after go-live."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from central.models import Base


def make_engine(database_url: str):
    if database_url.startswith("sqlite"):
        engine = create_engine(
            database_url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _enforce_fks(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    else:
        engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
