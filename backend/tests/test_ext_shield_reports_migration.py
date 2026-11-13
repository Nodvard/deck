"""Wochenberichte erweitern den bestehenden Shield-Zweig ohne Änderungen an bisherigen Daten."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from nodvard_deck import config
from nodvard_deck.migrate import alembic_config

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "b8c9d0e1f2a3"
REVISION = "c9d0e1f2a3b4"
TABLE = "ext_shield_weekly_reports"


@pytest.fixture(autouse=True)
def _keep_logging_setup():
    loggers = [logging.getLogger(), *(lg for lg in logging.root.manager.loggerDict.values() if isinstance(lg, logging.Logger))]
    saved = [(lg, lg.disabled, lg.level, lg.propagate, list(lg.handlers)) for lg in loggers]
    yield
    for lg, disabled, level, propagate, handlers in saved:
        lg.disabled, lg.propagate = disabled, propagate
        lg.setLevel(level)
        lg.handlers[:] = handlers


def test_weekly_reports_continue_the_shield_head():
    cfg, _ = alembic_config(repo_root=ROOT)
    script = ScriptDirectory.from_config(cfg)
    revision = script.get_revision(REVISION)
    assert revision.down_revision == PREVIOUS
    labels = script.get_revision(PREVIOUS).branch_labels
    assert revision.branch_labels == labels
    heads = [script.get_revision(head) for head in script.get_heads()]
    assert [head.revision for head in heads if labels & head.branch_labels] == [REVISION]


def test_upgrade_and_downgrade_preserve_existing_shield_data(tmp_path, monkeypatch):
    db_path = tmp_path / "reports.db"
    settings = config.Settings(
        env="dev", data_dir=tmp_path, database_url=f"sqlite+aiosqlite:///{db_path}",
        master_key_path=tmp_path / "master.key", vault_keyring_path=tmp_path / "keyring.json",
        ext_data_dir=tmp_path / "ext-data",
    )
    monkeypatch.setattr(config, "_settings", settings)
    cfg, _ = alembic_config(settings.database_url, repo_root=ROOT)
    command.upgrade(cfg, PREVIOUS)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO ext_nexus_soc_baselines (id, host_id, kind, data, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("host:updates", "host", "updates", '{"count": 4}', "2026-11-02 08:00:00"),
        )
        before = db.execute("SELECT * FROM ext_nexus_soc_baselines").fetchall()

    command.upgrade(cfg, REVISION)
    with sqlite3.connect(db_path) as db:
        columns = {row[1]: (row[2], row[3], row[5]) for row in db.execute(f"PRAGMA table_info({TABLE})")}
        assert columns == {
            "id": ("VARCHAR(64)", 1, 1), "created_at": ("DATETIME", 1, 0),
            "period_start": ("DATETIME", 1, 0), "period_end": ("DATETIME", 1, 0),
            "data": ("JSON", 1, 0), "notified_at": ("DATETIME", 0, 0),
        }
        indexes = {row[1] for row in db.execute(f"PRAGMA index_list({TABLE})")}
        assert f"ix_{TABLE}_created_at" in indexes
        snapshot = {"hosts": [{"host_id": "host", "host_name": "Büro"}], "summary": {"updates": 4}}
        db.execute(
            f"INSERT INTO {TABLE} (id, created_at, period_start, period_end, data) VALUES (?, ?, ?, ?, ?)",
            ("2026-W45", "2026-11-02 08:00:00", "2026-10-26 08:00:00", "2026-11-02 08:00:00", json.dumps(snapshot)),
        )
        data, notified = db.execute(f"SELECT data, notified_at FROM {TABLE}").fetchone()
        assert json.loads(data) == snapshot and notified is None
        assert db.execute("SELECT * FROM ext_nexus_soc_baselines").fetchall() == before

    command.downgrade(cfg, PREVIOUS)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name = ?", (TABLE,)).fetchall() == []
        assert db.execute("SELECT * FROM ext_nexus_soc_baselines").fetchall() == before
    command.upgrade(cfg, REVISION)
    with sqlite3.connect(db_path) as db:
        assert db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0] == 0
        assert db.execute("SELECT * FROM ext_nexus_soc_baselines").fetchall() == before
