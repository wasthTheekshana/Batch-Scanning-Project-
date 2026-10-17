"""Alembic environment. Migrations are run from code (central/migrate.py), which hands in the
engine; `alembic` on the command line also works when CENTRAL_DATABASE_URL is set."""
import os

from alembic import context
from sqlalchemy import create_engine

from central.models import Base

target_metadata = Base.metadata


def _engine():
    engine = context.config.attributes.get("engine")
    if engine is not None:
        return engine
    return create_engine(os.environ["CENTRAL_DATABASE_URL"])


def run_migrations_online() -> None:
    engine = _engine()
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
