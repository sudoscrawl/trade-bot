"""Fail-fast migration check used by the live process."""

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from bot.data.db.engine import engine


def assert_schema_current() -> None:
    """Require ``alembic upgrade head`` before a live bot can start."""
    script = ScriptDirectory.from_config(_alembic_config())
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current != script.get_current_head():
        raise RuntimeError(
            "Database schema is not current. Run `alembic upgrade head` before "
            "starting the live bot."
        )


def _alembic_config():
    from alembic.config import Config

    return Config("alembic.ini")
