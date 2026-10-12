"""Migration `a6c2e8d41f57`: Ablaufdatum an `secrets`, neue Tabelle `secret_usages` (N8, T1).

Auf einer echten Datei-DB (wie `test_audit_names_migration.py`): erst bis zur Revision davor, dann ein
Geheimnis in der Form von heute, dann die Migration hin und zurueck."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from nodvard_deck import config
from nodvard_deck.migrate import alembic_config

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_CORE_HEAD = "f3a9c6d18e24"  # login_protokoll_namen -- die Revision direkt davor
REVISION = "a6c2e8d41f57"


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


@pytest.fixture(autouse=True)
def _keep_logging_setup():
    """`migrations/env.py` laedt die Logging-Einstellung der `alembic.ini` und schaltet dabei alle schon vorhandenen
    Logger ab (Vorlage: `test_audit_names_migration.py`)."""
    loggers = [logging.getLogger(), *(lg for lg in logging.root.manager.loggerDict.values() if isinstance(lg, logging.Logger))]
    saved = [(lg, lg.disabled, lg.level, lg.propagate, list(lg.handlers)) for lg in loggers]
    yield
    for lg, disabled, level, propagate, handlers in saved:
        lg.disabled, lg.propagate = disabled, propagate
        lg.setLevel(level)
        lg.handlers[:] = handlers


@pytest.fixture
def before(tmp_path, monkeypatch):
    """Datenbank auf dem Stand davor, mit einem Geheimnis. Gibt (cfg, Pfad) zurueck."""
    monkeypatch.setattr(config, "_settings", _settings(tmp_path))
    monkeypatch.chdir(REPO_ROOT)
    cfg, _ = alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", repo_root=REPO_ROOT)
    command.upgrade(cfg, PREVIOUS_CORE_HEAD)
    db_path = tmp_path / "test.db"
    db = sqlite3.connect(str(db_path))
    try:
        db.execute(
            "INSERT INTO secrets (id, label, kind, ciphertext, key_version, owner_ext_id, description, metadata, "
            "created_at, updated_at) VALUES ('s1', 'proxmox-token', 'api_token', x'00', 1, 'proxmox', 'Token', '{}', "
            "'2026-10-01', '2026-10-01')"
        )
        db.commit()
    finally:
        db.close()
    return cfg, db_path


def _columns(db_path: Path, table: str) -> dict[str, tuple]:
    db = sqlite3.connect(str(db_path))
    try:
        return {row[1]: row for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        db.close()


def test_the_migration_follows_the_previous_core_head_and_is_the_core_head():
    cfg, _ = alembic_config(repo_root=REPO_ROOT)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision(REVISION).down_revision == PREVIOUS_CORE_HEAD
    core_dir = (REPO_ROOT / "backend" / "migrations" / "versions").resolve()
    core_heads = [h for h in script.get_heads() if Path(script.get_revision(h).path).resolve().parent == core_dir]
    assert core_heads == [REVISION]


def test_upgrade_adds_expiry_and_usage_table_and_keeps_existing_secrets(before):
    cfg, db_path = before
    command.upgrade(cfg, REVISION)

    secret_columns = _columns(db_path, "secrets")
    assert "expires_at" in secret_columns and secret_columns["expires_at"][3] == 0  # NULL erlaubt
    usage_columns = _columns(db_path, "secret_usages")
    assert set(usage_columns) == {"secret_id", "consumer_type", "consumer_id", "first_used_at", "last_used_at"}
    assert {name for name, row in usage_columns.items() if row[5]} == {"secret_id", "consumer_type", "consumer_id"}

    db = sqlite3.connect(str(db_path))
    try:
        assert db.execute("SELECT label, expires_at FROM secrets").fetchall() == [("proxmox-token", None)]
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO secret_usages VALUES ('s1', 'extension', 'proxmox', '2026-10-12 08:00:00', '2026-10-12 08:00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO secret_usages VALUES ('s1', 'extension', 'proxmox', '2026-10-12', '2026-10-12')")
        db.execute("DELETE FROM secrets WHERE id = 's1'")
        assert db.execute("SELECT count(*) FROM secret_usages").fetchone() == (0,)  # Kaskade
    finally:
        db.rollback()
        db.close()


def test_downgrade_removes_both_and_keeps_the_secret(before):
    cfg, db_path = before
    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, PREVIOUS_CORE_HEAD)

    assert "expires_at" not in _columns(db_path, "secrets")
    db = sqlite3.connect(str(db_path))
    try:
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'secret_usages'").fetchall() == []
        assert db.execute("SELECT id, label, owner_ext_id FROM secrets").fetchall() == [("s1", "proxmox-token", "proxmox")]
    finally:
        db.close()
