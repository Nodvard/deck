"""Migration `e2f3a4b5c6d7` (Dokumente mit Gegenständen im Inventar verknüpfen) auf einer echten Datei-DB: neue
Tabelle, vorhandene Dokumente und Tags bleiben (hoch und wieder runter), und das Modell passt zur Migration."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from nodvard_deck import config
from nodvard_deck.migrate import alembic_config

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "9f1e2d3c4b5a"
LINKS = "e2f3a4b5c6d7"


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


def _seed_document_with_tag(path: Path) -> None:
    db = sqlite3.connect(str(path))
    try:
        db.execute(
            "INSERT INTO ext_documents_documents (id, filename, original_filename, content_type, size_bytes, "
            "ocr_status, created_at, updated_at) VALUES ('d1', 'd1.pdf', 'Rechnung.pdf', 'application/pdf', 10, "
            "'done', '2026-10-01', '2026-10-01')"
        )
        db.execute("INSERT INTO ext_documents_tags (id, name, created_at) VALUES ('t1', 'Rechnungen', '2026-10-01')")
        db.execute("INSERT INTO ext_documents_document_tags (document_id, tag_id, created_at) VALUES ('d1', 't1', '2026-10-01')")
        db.commit()
    finally:
        db.close()


def test_the_revision_hangs_on_the_documents_branch():
    from alembic.script import ScriptDirectory

    cfg, _ = alembic_config(repo_root=REPO_ROOT)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision(LINKS).down_revision == BEFORE
    documents_dir = (REPO_ROOT / "extensions" / "documents" / "migrations" / "versions").resolve()
    heads = [h for h in script.get_heads() if Path(script.get_revision(h).path).resolve().parent == documents_dir]
    # Ein Kopf im Zweig der Dokumente; spätere Migrationen hängen dahinter.
    assert len(heads) == 1
    assert LINKS in {r.revision for r in script.iterate_revisions(heads[0], "base")}


def test_upgrade_and_downgrade_keep_documents_and_tags(migrated):
    cfg, db_path = migrated
    command.upgrade(cfg, f"documents@{BEFORE}")
    _seed_document_with_tag(db_path)

    command.upgrade(cfg, "heads")
    tables = {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ext_documents_item_links" in tables
    (fk,) = _query(db_path, "PRAGMA foreign_key_list(ext_documents_item_links)")
    # Wird ein Dokument gelöscht, gehen seine Verknüpfungen mit (CASCADE); zum Inventar gibt es keinen Fremdschlüssel.
    assert (fk[2], fk[3], fk[6]) == ("ext_documents_documents", "document_id", "CASCADE")
    assert {r[1] for r in _query(db_path, "PRAGMA index_list(ext_documents_item_links)")} >= {"ix_ext_documents_item_links_item_id"}
    assert _query(db_path, "SELECT original_filename FROM ext_documents_documents") == [("Rechnung.pdf",)]

    command.downgrade(cfg, f"documents@{BEFORE}")
    tables = {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ext_documents_item_links" not in tables
    assert _query(db_path, "SELECT document_id, tag_id FROM ext_documents_document_tags") == [("d1", "t1")]

    command.upgrade(cfg, "heads")
    assert "ext_documents_item_links" in {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}


def test_the_migrated_schema_matches_the_model(migrated):
    """Autogenerate-Gegenprobe für die neue Tabelle: zwischen Modell und Migration bleibt nichts übrig."""
    import sys

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    src = str(REPO_ROOT / "extensions" / "documents" / "src")
    added = src not in sys.path
    if added:
        sys.path.insert(0, src)
    try:
        from nodvard_deck_ext_documents.models import Base as DocumentsBase
    finally:
        if added:
            sys.path.remove(src)

    cfg, db_path = migrated
    command.upgrade(cfg, "heads")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": False})
            diffs = compare_metadata(context, DocumentsBase.metadata)
    finally:
        engine.dispose()
    ours = [d for d in diffs if "ext_documents_item_links" in repr(d)]
    assert ours == []
