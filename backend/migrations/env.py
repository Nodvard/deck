"""Alembic-Umgebung.

Zwei Abweichungen vom generierten Standard:

1. `sqlalchemy.url` kommt aus `nodvard_deck.config.Settings` (also derselben
   `NODVARD_DECK_DATABASE_URL`-Umgebungsvariable wie die Anwendung; der alte Name
   `LATTICE_DATABASE_URL` gilt als Rueckfall weiter), nicht aus einem
   zweiten, separat gepflegten Wert in `alembic.ini`.
2. Die Engine ist async (aiosqlite/asyncpg per D-02) - Migrationen laufen ueber
   `run_sync`, dem von SQLAlchemy dokumentierten Muster fuer Alembic + Async-Engines.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from nodvard_deck.config import get_settings
from nodvard_deck.models import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_settings = get_settings()
_settings.ensure_data_dir()  # sqlite muss das Zielverzeichnis vorfinden
config.set_main_option("sqlalchemy.url", _settings.database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
