"""Migration `c7d8e9f0a1b2` (Inventar als Homelab-Baukasten) auf einer echten Datei-DB: neue Spalten und Tabellen,
vorhandene Teile und ihre Bilder bleiben erhalten (hoch und wieder runter), und das Modell passt zur Migration."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from nodvard_deck import config
from nodvard_deck.migrate import alembic_config

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "a1b2c3d4e5f6"
BAUKASTEN = "c7d8e9f0a1b2"
NEW_ITEM_COLUMNS = {
    "manufacturer", "model", "serial_number", "product_url", "host_id", "project_id", "rack_id", "rack_position",
    "rack_units",
}


@pytest.fixture(autouse=True)
def _keep_logging_setup():
    """`migrations/env.py` laedt die Logging-Einstellung der `alembic.ini` und schaltet dabei vorhandene Logger
    ab -- das darf nicht in spaetere `caplog`-Tests durchsickern (Vorlage: test_audit_names_migration.py)."""
    loggers = [logging.getLogger(), *(lg for lg in logging.root.manager.loggerDict.values() if isinstance(lg, logging.Logger))]
    saved = [(lg, lg.disabled, lg.level, lg.propagate, list(lg.handlers)) for lg in loggers]
    yield
    for lg, disabled, level, propagate, handlers in saved:
        lg.disabled, lg.propagate = disabled, propagate
        lg.setLevel(level)
        lg.handlers[:] = handlers


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


def _query(path: Path, sql: str) -> list[tuple]:
    db = sqlite3.connect(str(path))
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def _seed_item_with_image(path: Path) -> None:
    db = sqlite3.connect(str(path))
    try:
        db.execute(
            "INSERT INTO ext_inventory_items (id, name, quantity, created_at, updated_at) "
            "VALUES ('i1', 'Switch', 1, '2026-10-01', '2026-10-01')"
        )
        db.execute(
            "INSERT INTO ext_inventory_item_images (id, item_id, filename, content_type, size_bytes, created_at) "
            "VALUES ('b1', 'i1', 'b1.png', 'image/png', 10, '2026-10-01')"
        )
        db.commit()
    finally:
        db.close()


def test_the_revision_hangs_on_the_inventory_branch():
    from alembic.script import ScriptDirectory

    cfg, _ = alembic_config(repo_root=REPO_ROOT)
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision(BAUKASTEN)
    assert rev.down_revision == BEFORE
    # Der Inventar-Zweig hat genau einen Kopf, und die Baukasten-Revision liegt auf seinem Weg.
    inventory_dir = (REPO_ROOT / "extensions" / "inventory" / "migrations" / "versions").resolve()
    heads = [h for h in script.get_heads() if Path(script.get_revision(h).path).resolve().parent == inventory_dir]
    assert len(heads) == 1, heads
    assert BAUKASTEN in {r.revision for r in script.walk_revisions(base="base", head=heads[0])}


def test_upgrade_keeps_items_and_images_and_adds_columns_and_tables(migrated):
    cfg, db_path = migrated
    command.upgrade(cfg, f"inventory@{BEFORE}")
    _seed_item_with_image(db_path)

    command.upgrade(cfg, "heads")
    cols = {r[1] for r in _query(db_path, "PRAGMA table_info(ext_inventory_items)")}
    assert NEW_ITEM_COLUMNS <= cols
    tables = {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"ext_inventory_projects", "ext_inventory_racks"} <= tables
    indexes = {r[1] for r in _query(db_path, "PRAGMA index_list(ext_inventory_items)")}
    assert {"ix_ext_inventory_items_project_id", "ix_ext_inventory_items_rack_id", "ix_ext_inventory_items_host_id"} <= indexes
    # Rack -> Standort: beim Loeschen des Standorts bleibt das Rack (SET NULL).
    (fk,) = _query(db_path, "PRAGMA foreign_key_list(ext_inventory_racks)")
    assert (fk[2], fk[3], fk[6]) == ("ext_inventory_locations", "location_id", "SET NULL")
    # Das vorhandene Teil und sein Bild sind noch da, die neuen Felder leer.
    assert _query(db_path, "SELECT name, manufacturer, project_id FROM ext_inventory_items") == [("Switch", None, None)]
    assert _query(db_path, "SELECT id FROM ext_inventory_item_images") == [("b1",)]


def test_downgrade_removes_only_the_new_parts_and_keeps_the_images(migrated):
    cfg, db_path = migrated
    command.upgrade(cfg, f"inventory@{BEFORE}")
    _seed_item_with_image(db_path)
    command.upgrade(cfg, "heads")

    command.downgrade(cfg, f"inventory@{BEFORE}")
    cols = {r[1] for r in _query(db_path, "PRAGMA table_info(ext_inventory_items)")}
    assert not (NEW_ITEM_COLUMNS & cols)
    tables = {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not ({"ext_inventory_projects", "ext_inventory_racks"} & tables)
    assert _query(db_path, "SELECT name FROM ext_inventory_items") == [("Switch",)]
    assert _query(db_path, "SELECT id FROM ext_inventory_item_images") == [("b1",)], "die Bilder bleiben"

    command.upgrade(cfg, "heads")
    assert NEW_ITEM_COLUMNS <= {r[1] for r in _query(db_path, "PRAGMA table_info(ext_inventory_items)")}


def test_the_migrated_schema_matches_the_model(migrated):
    """Autogenerate-Gegenprobe: zwischen dem Modell der Erweiterung und der Migration bleibt nichts uebrig."""
    import sys

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    src = str(REPO_ROOT / "extensions" / "inventory" / "src")
    added = src not in sys.path
    if added:
        sys.path.insert(0, src)
    try:
        from nodvard_deck_ext_inventory.models import Base as InventoryBase
    finally:
        if added:
            sys.path.remove(src)

    cfg, db_path = migrated
    command.upgrade(cfg, "heads")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": False})
            diffs = compare_metadata(context, InventoryBase.metadata)
    finally:
        engine.dispose()

    # Nur, was diese Migration anlegt: die beiden Tabellen und die neuen Spalten/Indizes von `ext_inventory_items`.
    # (Andere Tabellen kennt dieses Modell nicht; die Indizes der ersten Revision stehen nicht im Modell -- das ist
    # aelter als dieser Schritt und hier nicht Thema.)
    def ours(diff) -> bool:
        if isinstance(diff, list):
            return any(ours(d) for d in diff)
        text = repr(diff)
        if "ext_inventory_projects" in text or "ext_inventory_racks" in text:
            return True
        return "ext_inventory_items" in text and any(f"'{c}'" in text for c in NEW_ITEM_COLUMNS)

    assert [d for d in diffs if ours(d)] == []
