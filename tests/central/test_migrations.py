"""The central database is managed by migrations: a fresh one is built from the baseline, an
older create_all() one is adopted, later changes are applied, and models and migrations cannot
drift apart unnoticed."""

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


def test_a_database_from_before_migrations_existed_is_adopted_and_brought_up_to_date(tmp_path):
    """An install made by the old create_all() has the baseline schema and its data."""
    engine = engine_for(tmp_path)
    migrate.command.upgrade(migrate._config(engine), migrate.BASELINE)     # the baseline schema
    with engine.begin() as c:
        c.execute(text("INSERT INTO settings (key, value) VALUES ('poll', '600')"))
        c.execute(text("DROP TABLE alembic_version"))                      # what create_all() never had
    migrate.upgrade(engine)
    assert migrate.current_revision(engine) == migrate.head_revision()
    assert migrate.differences(engine) == []
    with engine.connect() as c:
        assert c.execute(text("SELECT value FROM settings WHERE key='poll'")).scalar() == "600"


def test_a_database_made_by_create_all_from_the_newest_models_is_stamped_not_migrated_twice(tmp_path):
    engine = engine_for(tmp_path)
    Base.metadata.create_all(engine)
    assert "alembic_version" not in inspect(engine).get_table_names()
    migrate.upgrade(engine)                              # would fail on "table already exists" otherwise
    assert migrate.current_revision(engine) == migrate.head_revision()
    assert migrate.differences(engine) == []


def test_upgrading_twice_changes_nothing(tmp_path):
    engine = engine_for(tmp_path)
    migrate.upgrade(engine)
    before = migrate.current_revision(engine)
    migrate.upgrade(engine)
    assert migrate.current_revision(engine) == before


def test_a_later_migration_is_applied_to_an_existing_database(tmp_path, monkeypatch):
    """Simulate a later release that adds a column: a new migration file after the current head."""
    import shutil
    from pathlib import Path
    head = migrate.head_revision()
    engine = engine_for(tmp_path)
    migrate.command.upgrade(migrate._config(engine), head)                  # an installed older release
    work = tmp_path / "central_copy"
    shutil.copytree(Path(migrate.__file__).parent / "migrations", work / "migrations")
    later = f'''from alembic import op
import sqlalchemy as sa

revision = '9999'
down_revision = '{head}'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('pcs', sa.Column('note', sa.String(50), nullable=True))


def downgrade():
    op.drop_column('pcs', 'note')
'''
    (work / "migrations" / "versions" / "9999_add_note.py").write_text(later, encoding="utf-8")
    monkeypatch.setattr(migrate, "_HERE", work)
    assert "note" not in {c["name"] for c in inspect(engine).get_columns("pcs")}
    migrate.upgrade(engine)                                                  # the new release starts
    assert "note" in {c["name"] for c in inspect(engine).get_columns("pcs")}
    assert migrate.current_revision(engine) == "9999"


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
