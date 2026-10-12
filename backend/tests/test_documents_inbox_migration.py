"""Migration `a7b8c9d0e1f4` (Nextcloud-Ordner als Eingang der Dokumente): neue Tabelle hängt am Zweig der Dokumente,
hoch und wieder runter ohne Verlust, und das Modell passt zur Migration."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from test_documents_item_links_migration import (  # noqa: F401
    REPO_ROOT,
    _keep_logging_setup,
    _query,
    _seed_document_with_tag,
    migrated,
)

LINKS = "e2f3a4b5c6d7"
INBOX = "a7b8c9d0e1f4"


def _tables(db_path: Path) -> set[str]:
    return {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}


def test_the_revision_follows_the_item_links():
    from alembic.script import ScriptDirectory
    from nodvard_deck.migrate import alembic_config

    cfg, _ = alembic_config(repo_root=REPO_ROOT)
    assert ScriptDirectory.from_config(cfg).get_revision(INBOX).down_revision == LINKS


def test_upgrade_and_downgrade_keep_the_documents(migrated):  # noqa: F811
    cfg, db_path = migrated
    command.upgrade(cfg, f"documents@{LINKS}")
    _seed_document_with_tag(db_path)

    command.upgrade(cfg, "heads")
    assert "ext_documents_inbox_files" in _tables(db_path)
    columns = {r[1]: r for r in _query(db_path, "PRAGMA table_info(ext_documents_inbox_files)")}
    assert [name for name, col in columns.items() if col[5]] == ["source_id", "path"], "Schlüssel: Quelle und Pfad"
    assert _query(db_path, "PRAGMA foreign_key_list(ext_documents_inbox_files)") == []

    command.downgrade(cfg, f"documents@{LINKS}")
    assert "ext_documents_inbox_files" not in _tables(db_path)
    assert _query(db_path, "SELECT document_id, tag_id FROM ext_documents_document_tags") == [("d1", "t1")]


def test_the_migrated_schema_matches_the_model(migrated):  # noqa: F811
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
    assert [d for d in diffs if "ext_documents_inbox_files" in repr(d)] == []
