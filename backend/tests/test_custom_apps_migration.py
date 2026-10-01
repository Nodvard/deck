"""Migration der Tabelle `custom_apps` (eigene App-Kacheln): hoch, runter, wieder hoch -- auf einer
echten Datei-DB (wie `test_migrate.py`), dazu die Zusicherungen, dass der Kern-Zweig genau EINEN Kopf
hat und das Modell zur Migration passt (kein Unterschied zwischen `Base.metadata` und dem echten Schema)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from nodvard_deck import config
from nodvard_deck.migrate import alembic_config, known_revisions

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_CORE_HEAD = "e7a1c4b93d52"  # recovery_codes -- die Revision direkt vor custom_apps


def _settings(tmp_path: Path) -> config.Settings:
    return config.Settings(
        env="dev",
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        master_key_path=tmp_path / "master.key",
        vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key",
        extensions_dir=tmp_path / "extensions",
        ext_data_dir=tmp_path / "ext-data",
    )


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_settings", _settings(tmp_path))
    monkeypatch.chdir(REPO_ROOT)
    cfg, _ = alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", repo_root=REPO_ROOT)
    return cfg, tmp_path / "test.db"


def _tables(path: Path) -> set[str]:
    db = sqlite3.connect(str(path))
    try:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        db.close()


def test_there_is_exactly_one_core_head_and_it_is_custom_apps():
    from alembic.script import ScriptDirectory

    cfg, _ = alembic_config(repo_root=REPO_ROOT)
    script = ScriptDirectory.from_config(cfg)
    core_dir = (REPO_ROOT / "backend" / "migrations" / "versions").resolve()
    core_heads = [
        h for h in script.get_heads()
        if Path(script.get_revision(h).path).resolve().parent == core_dir
    ]
    assert len(core_heads) == 1, f"mehrere Kern-Koepfe: {core_heads}"
    revision = script.get_revision(core_heads[0])
    assert revision.down_revision == PREVIOUS_CORE_HEAD
    assert "custom_apps" in (revision.doc or "")
    assert core_heads[0] in known_revisions(REPO_ROOT)[1]


def test_upgrade_creates_custom_apps_with_all_columns_and_the_host_link(migrated):
    from nodvard_deck.migrate import upgrade_heads

    _cfg, db_path = migrated
    upgrade_heads()
    assert "custom_apps" in _tables(db_path)

    db = sqlite3.connect(str(db_path))
    try:
        cols = {r[1]: r for r in db.execute("PRAGMA table_info(custom_apps)").fetchall()}
        assert {
            "id", "name", "url", "icon", "color", "group_name", "sort_order", "open_in_new_tab", "host_id",
            "created_by_user_id", "created_at", "updated_at",
        } == set(cols)
        # NOT NULL: name, url, sort_order, open_in_new_tab, Zeitstempel; alles andere darf leer sein.
        not_null = {name for name, row in cols.items() if row[3]}
        assert {"id", "name", "url", "sort_order", "open_in_new_tab", "created_at", "updated_at"} <= not_null
        assert not ({"icon", "color", "group_name", "host_id", "created_by_user_id"} & not_null)
        # Server-Bezug: Fremdschluessel auf hosts, beim Loeschen des Servers bleibt die App (SET NULL).
        (fk,) = db.execute("PRAGMA foreign_key_list(custom_apps)").fetchall()
        assert (fk[2], fk[3], fk[6]) == ("hosts", "host_id", "SET NULL")
        indexes = {r[1] for r in db.execute("PRAGMA index_list(custom_apps)").fetchall()}
        assert "ix_custom_apps_host_id" in indexes and "ix_custom_apps_sort_order" in indexes
    finally:
        db.close()


def test_downgrade_removes_only_the_table_and_upgrade_brings_it_back(migrated):
    from nodvard_deck.migrate import upgrade_heads

    cfg, db_path = migrated
    upgrade_heads()
    db = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT INTO custom_apps (id, name, url, sort_order, open_in_new_tab, created_at, updated_at) "
        "VALUES ('a1', 'Router', 'http://192.168.2.1', 0, 1, '2026-10-01', '2026-10-01')"
    )
    db.commit()
    db.close()

    command.downgrade(cfg, PREVIOUS_CORE_HEAD)
    tables = _tables(db_path)
    assert "custom_apps" not in tables
    assert {"users", "hosts", "recovery_codes"} <= tables, "die Revisionen davor bleiben unberuehrt"
    db = sqlite3.connect(str(db_path))
    try:
        heads = {r[0] for r in db.execute("SELECT version_num FROM alembic_version").fetchall()}
        indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    finally:
        db.close()
    assert PREVIOUS_CORE_HEAD in heads
    assert not any("custom_apps" in name for name in indexes), "auch die Indizes sind weg"

    upgrade_heads()
    assert "custom_apps" in _tables(db_path)
    db = sqlite3.connect(str(db_path))
    try:
        assert db.execute("SELECT count(*) FROM custom_apps").fetchone()[0] == 0
    finally:
        db.close()


def test_the_migrated_schema_matches_the_model(migrated):
    """Autogenerate-Gegenprobe: zwischen Modell und Migration darf fuer `custom_apps` nichts uebrig bleiben."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from nodvard_deck.migrate import upgrade_heads
    from nodvard_deck.models import Base
    from sqlalchemy import create_engine

    _cfg, db_path = migrated
    upgrade_heads()
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": False})
            diffs = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    def mentions_custom_apps(diff) -> bool:
        items = diff if isinstance(diff, list) else [diff]
        return any("custom_apps" in repr(item) for item in items)

    assert [d for d in diffs if mentions_custom_apps(d)] == []
