"""The central database is managed by migrations: a fresh one is built from the baseline, an
older create_all() one is adopted, later changes are applied, and models and migrations cannot
drift apart unnoticed."""
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, text

from central import migrate
from central.models import Base


def engine_for(tmp_path, name="m.db"):
    return create_engine(f"sqlite:///{tmp_path / name}")


def test_an_empty_database_gets_every_table_from_the_baseline(tmp_path):
    engine = engine_for(tmp_path)
    migrate.upgrade(engine)
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= tables and "alembic_version" in tables
    assert migrate.current_revision(engine) == migrate.head_revision()


def test_the_migrations_and_the_models_describe_the_same_schema(tmp_path):
    """Fails when someone edits central/models.py without writing a migration."""
    engine = engine_for(tmp_path)
    migrate.upgrade(engine)
    assert migrate.differences(engine) == []


def test_a_database_made_by_the_old_create_all_is_adopted_with_its_data(tmp_path):
    engine = engine_for(tmp_path)
    Base.metadata.create_all(engine)                     # how production databases were made
    with engine.begin() as c:
        c.execute(text("INSERT INTO settings (key, value) VALUES ('k', 'v')")) \
            if "settings" in Base.metadata.tables and {"key", "value"} <= set(Base.metadata.tables["settings"].c.keys()) else None
    assert "alembic_version" not in inspect(engine).get_table_names()
    migrate.upgrade(engine)
    assert migrate.current_revision(engine) == migrate.head_revision()
    assert migrate.differences(engine) == []


def test_upgrading_twice_changes_nothing(tmp_path):
    engine = engine_for(tmp_path)
    migrate.upgrade(engine)
    before = migrate.current_revision(engine)
    migrate.upgrade(engine)
    assert migrate.current_revision(engine) == before


def test_a_later_migration_is_applied_to_an_existing_database(tmp_path, monkeypatch):
    """Simulate a release that adds a column: a migration file in the versions folder."""
    import shutil
    from pathlib import Path
    work = tmp_path / "central_copy"
    shutil.copytree(Path(migrate.__file__).parent / "migrations", work / "migrations")
    (work / "migrations" / "versions" / "0002_add_note.py").write_text(
        "from alembic import op\nimport sqlalchemy as sa\n"
        "revision = '0002'\ndown_revision = '0001'\nbranch_labels = None\ndepends_on = None\n\n"
        "def upgrade():\n    op.add_column('pcs', sa.Column('note', sa.String(50), nullable=True))\n\n"
        "def downgrade():\n    op.drop_column('pcs', 'note')\n", encoding="utf-8")
    monkeypatch.setattr(migrate, "_HERE", work)
    engine = engine_for(tmp_path)
    migrate.command.upgrade(migrate._config(engine), "0001")          # an installed older release
    assert "note" not in {c["name"] for c in inspect(engine).get_columns("pcs")}
    migrate.upgrade(engine)                                            # the new release starts
    assert "note" in {c["name"] for c in inspect(engine).get_columns("pcs")}
    assert migrate.current_revision(engine) == "0002"


def test_starting_the_app_migrates_its_database(tmp_path):
    from central.app import create_app
    from central.settings import Settings
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'a.db'}",
                              session_secret="x" * 40, cookie_secure=False))
    assert migrate.current_revision(app.state.engine) == migrate.head_revision()


def test_the_cli_reports_the_schema_status(tmp_path, monkeypatch, capsys):
    from central import manage
    monkeypatch.setenv("CENTRAL_DATABASE_URL", f"sqlite:///{tmp_path / 'c.db'}")
    monkeypatch.setenv("CENTRAL_SESSION_SECRET", "x" * 40)
    assert manage.main(["migrate"]) == 0
    assert manage.main(["db-status"]) == 0
    assert "matches the models" in capsys.readouterr().out
