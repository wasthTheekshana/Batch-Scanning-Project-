"""Schema migrations for the central database (Alembic).

`upgrade(engine)` brings any database to the latest schema:
  - an empty database gets every migration, starting with the baseline;
  - a database made by the earlier `create_all()` (tables, no alembic_version) IS the
    baseline, so it is stamped as such and then upgraded - nothing is dropped or copied;
  - a database already under migration gets the ones it lacks.
To change the schema: edit central/models.py, then
    python -m central.manage new-migration "what changed"
and review the file it writes in central/migrations/versions/.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from central.models import Base

BASELINE = "0001"
_HERE = Path(__file__).resolve().parent


def _config(engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_HERE / "migrations"))
    cfg.attributes["engine"] = engine
    return cfg


def head_revision() -> str:
    head = ScriptDirectory.from_config(_config(None)).get_current_head()
    if head is None:
        raise RuntimeError("no migrations found")
    return head


def current_revision(engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def upgrade(engine) -> None:
    cfg = _config(engine)
    tables = set(inspect(engine).get_table_names())
    if tables and "alembic_version" not in tables:
        command.stamp(cfg, BASELINE)          # made by create_all(): it is the baseline
    command.upgrade(cfg, "head")


def differences(engine) -> list:
    """What the models have that the database lacks, or the reverse: [] when they match.
    A non-empty answer after `upgrade` means a model was changed without a migration."""
    with engine.connect() as connection:
        return compare_metadata(MigrationContext.configure(connection), Base.metadata)


def new_migration(engine, message: str) -> str:
    """Write a migration for the difference between the models and `engine`'s database."""
    cfg = _config(engine)
    command.revision(cfg, message=message, autogenerate=True)
    return head_revision()
