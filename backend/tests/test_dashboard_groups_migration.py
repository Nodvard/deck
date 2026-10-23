"""Migration `dashboard_layouts.groups`: hoch, runter, wieder hoch auf einer echten Datei-DB; bestehende Layouts bleiben."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from nodvard_deck import config
from nodvard_deck.migrate import alembic_config, known_revisions

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_CORE_HEAD = "a6c2e8d41f57"
GROUPS_REVISION = "b5d1e9a37c42"


@pytest.fixture(autouse=True)
def _keep_logging_setup():
    loggers = [logging.getLogger(), *(lg for lg in logging.root.manager.loggerDict.values() if isinstance(lg, logging.Logger))]
    saved = [(lg, lg.disabled, lg.level, lg.propagate, list(lg.handlers)) for lg in loggers]
    yield
    for lg, disabled, level, propagate, handlers in saved:
        lg.disabled, lg.propagate = disabled, propagate
        lg.setLevel(level)
        lg.handlers[:] = handlers


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_settings", config.Settings(
        env="dev", data_dir=tmp_path, database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        master_key_path=tmp_path / "master.key", vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key", extensions_dir=tmp_path / "extensions", ext_data_dir=tmp_path / "ext-data",
    ))
    monkeypatch.chdir(REPO_ROOT)
    cfg, _ = alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", repo_root=REPO_ROOT)
    return cfg, tmp_path / "test.db"


def _columns(path: Path) -> dict[str, tuple]:
    db = sqlite3.connect(str(path))
    try:
        return {r[1]: r for r in db.execute("PRAGMA table_info(dashboard_layouts)").fetchall()}
    finally:
        db.close()


def test_the_groups_revision_follows_the_previous_core_head_in_the_one_core_chain():
    from alembic.script import ScriptDirectory

    cfg, _ = alembic_config(repo_root=REPO_ROOT)
    script = ScriptDirectory.from_config(cfg)
    core_dir = (REPO_ROOT / "backend" / "migrations" / "versions").resolve()
    core_heads = [h for h in script.get_heads() if Path(script.get_revision(h).path).resolve().parent == core_dir]
    assert len(core_heads) == 1
    assert GROUPS_REVISION in {rev.revision for rev in script.walk_revisions(base="base", head=core_heads[0])}
    assert script.get_revision(GROUPS_REVISION).down_revision == PREVIOUS_CORE_HEAD
    assert GROUPS_REVISION in known_revisions(REPO_ROOT)[1]


def test_existing_layouts_keep_their_widgets_and_get_no_groups(migrated):
    from nodvard_deck.migrate import upgrade_heads

    cfg, db_path = migrated
    command.upgrade(cfg, PREVIOUS_CORE_HEAD)
    db = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT INTO dashboard_layouts (id, user_id, name, is_default, items, created_at, updated_at) "
        "VALUES ('l1', 'u1', 'Standard', 1, '[{\"widget_id\": \"w\"}]', '2026-10-01', '2026-10-01')"
    )
    db.commit()
    db.close()

    upgrade_heads()
    assert _columns(db_path)["groups"][3] == 0, "die Spalte darf leer sein"
    db = sqlite3.connect(str(db_path))
    try:
        assert db.execute("SELECT items, groups FROM dashboard_layouts WHERE id = 'l1'").fetchone() == ('[{"widget_id": "w"}]', None)
    finally:
        db.close()

    command.downgrade(cfg, PREVIOUS_CORE_HEAD)
    assert "groups" not in _columns(db_path)
    db = sqlite3.connect(str(db_path))
    try:
        assert db.execute("SELECT items FROM dashboard_layouts WHERE id = 'l1'").fetchone() == ('[{"widget_id": "w"}]',)
    finally:
        db.close()

    upgrade_heads()
    assert "groups" in _columns(db_path)
