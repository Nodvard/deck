"""DB-Aufräumen auf einer Kopie der Beispiel-DB: Upgrade, Downgrade und erneutes Upgrade."""

from __future__ import annotations

import logging
import logging.config
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from nodvard_deck import config
from nodvard_deck.migrate import alembic_config
from nodvard_deck.models import Base
from sqlalchemy import create_engine

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_CORE_HEAD = "b5d1e9a37c42"
REVISION = "6d9e2a4f8b31"
WARNING = "secret_grants enthält Daten und bleibt erhalten."
REMOVED_INDEXES = {
    "ix_action_flap_history_fingerprint": ("action_flap_history", ("fingerprint",), False),
    "ix_audit_log_ts": ("audit_log", ("ts",), False),
    "ix_jobs_ext_id": ("jobs", ("ext_id",), False),
    "ix_actions_status": ("actions", ("status",), False),
}
ADDED_INDEXES = {
    "ix_actions_host_id": ("actions", ("host_id",), False),
    "ix_notifications_read_at_ts": ("notifications", ("read_at", "ts"), False),
}
KEPT_INDEXES = {
    "ix_flap_fingerprint_ts": ("action_flap_history", ("fingerprint", "ts"), False),
    "ix_audit_ts_action": ("audit_log", ("ts", "action"), False),
    "uq_jobs_ext_key": ("jobs", ("ext_id", "ext_job_key"), True),
    "ix_actions_status_created": ("actions", ("status", "created_at"), False),
    "ix_notifications_ts": ("notifications", ("ts",), False),
}


@pytest.fixture
def example_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(logging.config, "fileConfig", lambda *args, **kwargs: None)
    monkeypatch.chdir(REPO_ROOT)
    source = tmp_path / "example.db"
    settings = config.Settings(
        env="dev",
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{source}",
        master_key_path=tmp_path / "master.key",
        vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key",
        extensions_dir=tmp_path / "extensions",
        ext_data_dir=tmp_path / "ext-data",
    )
    monkeypatch.setattr(config, "_settings", settings)
    cfg, _ = alembic_config(settings.database_url, repo_root=REPO_ROOT)
    command.upgrade(cfg, PREVIOUS_CORE_HEAD)
    with closing(sqlite3.connect(source)) as db:
        db.executescript("""
            INSERT INTO hosts
                (id, name, display_name, address, os_family, is_managed, enabled, status,
                 metadata, created_at, updated_at)
            VALUES ('h1', 'server', 'Server', '192.0.2.1', 'linux', 1, 1, 'online', '{}',
                    '2026-10-01', '2026-10-01');
            INSERT INTO secrets
                (id, label, kind, ciphertext, key_version, description, metadata, created_at, updated_at)
            VALUES ('s1', 'Zugang', 'api_token', x'000102ff', 1, 'Token', '{}', '2026-10-01', '2026-10-01');
            INSERT INTO secret_usages
            VALUES ('s1', 'extension', 'proxmox', '2026-10-01', '2026-10-02');
            INSERT INTO action_flap_history (id, host_id, fingerprint, ts, blocked)
            VALUES ('f1', 'h1', 'fingerprint', '2026-10-01', 0);
            INSERT INTO audit_log (id, ts, actor_type, actor_id, action, outcome, detail)
            VALUES ('l1', '2026-10-01', 'extension', 'proxmox', 'host.check', 'success', '{}');
            INSERT INTO jobs
                (id, ext_id, ext_job_key, name, kind, schedule, timezone, target, params,
                 enabled, created_at, updated_at)
            VALUES ('j1', 'proxmox', 'sync', 'Abgleich', 'sync', '0 * * * *', 'Europe/Berlin',
                    '{}', '{}', 1, '2026-10-01', '2026-10-01');
            INSERT INTO actions
                (id, ext_id, action_type, host_id, payload, risk, status, proposed_by_type,
                 proposed_by_id, reason, gate_decision, result, created_at)
            VALUES ('a1', 'proxmox', 'host.check', 'h1', '{}', 'low', 'pending', 'extension',
                    'proxmox', 'Prüfung', '{}', '{}', '2026-10-01');
            INSERT INTO notifications (id, ts, severity, title, body, read_at, payload)
            VALUES ('n1', '2026-10-01', 'info', 'Bereit', 'Server bereit', NULL, '{}'),
                   ('n2', '2026-10-02', 'info', 'Gelesen', 'Abgleich fertig', '2026-10-03', '{}');
        """)
    target = tmp_path / "copy.db"
    shutil.copy2(source, target)
    settings.database_url = f"sqlite+aiosqlite:///{target}"
    return cfg, source, target


def _indexes(path: Path) -> dict[str, tuple]:
    with closing(sqlite3.connect(path)) as db:
        result = {}
        for table in ("action_flap_history", "audit_log", "jobs", "actions", "notifications"):
            for row in db.execute(f"PRAGMA index_list({table})").fetchall():
                columns = tuple(column[2] for column in db.execute(f'PRAGMA index_info("{row[1]}")'))
                result[row[1]] = (table, columns, bool(row[2]))
        return result


def _data(path: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(path)) as db:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT IN ('alembic_version', 'secret_grants') ORDER BY name"
        ).fetchall()
        return {name: db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for (name,) in tables}


def _grants(path: Path) -> tuple:
    with closing(sqlite3.connect(path)) as db:
        return (
            db.execute("PRAGMA table_info(secret_grants)").fetchall(),
            db.execute("PRAGMA foreign_key_list(secret_grants)").fetchall(),
            db.execute("SELECT * FROM secret_grants ORDER BY secret_id, grantee_type, grantee_id").fetchall(),
            db.execute("SELECT sql FROM sqlite_master WHERE name = 'secret_grants'").fetchone()[0],
        )


def _assert_grant_constraints(path: Path) -> None:
    columns, foreign_keys, _, sql = _grants(path)
    assert [(row[1], row[2], row[3], row[5]) for row in columns] == [
        ("secret_id", "VARCHAR(36)", 1, 1),
        ("grantee_type", "VARCHAR(16)", 1, 2),
        ("grantee_id", "VARCHAR(64)", 1, 3),
    ]
    assert len(foreign_keys) == 1
    assert foreign_keys[0][2:7] == ("secrets", "secret_id", "id", "NO ACTION", "CASCADE")
    assert "pk_secret_grants" in sql
    assert "fk_secret_grants_secret_id_secrets" in sql
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO secret_grants VALUES ('s1', 'extension', 'temporary')")
        db.execute("INSERT INTO secret_grants VALUES ('s1', 'user', 'temporary')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO secret_grants VALUES ('s1', 'extension', 'temporary')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO secret_grants VALUES ('missing', 'extension', 'temporary')")
        db.execute("DELETE FROM secrets WHERE id = 's1'")
        assert db.execute("SELECT count(*) FROM secret_grants").fetchone() == (0,)
        db.rollback()


def test_the_migration_follows_the_previous_core_head():
    cfg, _ = alembic_config(repo_root=REPO_ROOT)
    script = ScriptDirectory.from_config(cfg)
    core_dir = (REPO_ROOT / "backend" / "migrations" / "versions").resolve()
    core_heads = [head for head in script.get_heads() if Path(script.get_revision(head).path).parent == core_dir]
    assert len(core_heads) == 1
    assert script.get_revision(REVISION).down_revision == PREVIOUS_CORE_HEAD
    assert REVISION in {rev.revision for rev in script.walk_revisions(base="base", head=core_heads[0])}


@pytest.mark.parametrize("populated", [False, True], ids=["empty_grants", "existing_grants"])
def test_copy_survives_upgrade_downgrade_and_upgrade(example_copy, populated, caplog):
    cfg, source, path = example_copy
    source_bytes = source.read_bytes()
    if populated:
        with closing(sqlite3.connect(path)) as db:
            db.execute("INSERT INTO secret_grants VALUES ('s1', 'extension', 'proxmox')")
            db.commit()
    original_data = _data(path)
    original_grants = _grants(path)
    original_indexes = _indexes(path)
    assert original_indexes.items() >= (REMOVED_INDEXES | KEPT_INDEXES).items()
    expected_indexes = {name: value for name, value in original_indexes.items() if name not in REMOVED_INDEXES}
    expected_indexes.update(ADDED_INDEXES)

    for step in range(2):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            command.upgrade(cfg, REVISION)
        warnings = [record.getMessage() for record in caplog.records if record.name == "alembic.runtime.migration"]
        assert warnings == ([WARNING] if populated else [])
        assert _indexes(path) == expected_indexes
        assert _data(path) == original_data
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [(REVISION,)]
            exists = db.execute("SELECT name FROM sqlite_master WHERE name = 'secret_grants'").fetchone()
        assert bool(exists) is populated
        if populated:
            assert _grants(path) == original_grants

        if step == 1:
            break
        command.downgrade(cfg, PREVIOUS_CORE_HEAD)
        assert _indexes(path) == original_indexes
        assert _data(path) == original_data
        assert _grants(path)[:3] == original_grants[:3]
        if populated:
            assert _grants(path) == original_grants
        _assert_grant_constraints(path)
        assert _data(path) == original_data
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [(PREVIOUS_CORE_HEAD,)]
    assert source.read_bytes() == source_bytes


def test_upgraded_schema_matches_the_affected_models(example_copy):
    cfg, _, path = example_copy
    command.upgrade(cfg, REVISION)
    tables = {value[0] for value in REMOVED_INDEXES.values()} | {"notifications", "secret_grants"}
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    "compare_server_default": False,
                    "include_object": lambda obj, name, kind, reflected, other: kind != "table" or name in tables,
                },
            )
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()
