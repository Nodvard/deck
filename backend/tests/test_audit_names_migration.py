"""Migration `f3a9c6d18e24`: eingetippte Anmeldenamen verschwinden aus alten Protokoll-Eintraegen.

Auf einer echten Datei-DB (wie `test_custom_apps_migration.py`): erst bis zur Revision davor,
dann Altdaten in der Form, wie aeltere Versionen sie schrieben, dann die Migration."""

from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from nodvard_deck import config
from nodvard_deck.migrate import alembic_config
from sqlalchemy import event
from sqlalchemy.engine import Engine

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_CORE_HEAD = "d4b7a2c91e63"  # custom_apps -- die Revision direkt davor
REVISION = "f3a9c6d18e24"
MIGRATION_FILE = REPO_ROOT / "backend" / "migrations" / "versions" / "f3a9c6d18e24_login_protokoll_namen.py"
TYPED = "geheim-sommer2026!"
TYPED_LONG = "x" * 300


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
    """`migrations/env.py` laedt die Logging-Einstellung der `alembic.ini` und schaltet dabei alle
    schon vorhandenen Logger ab. Das darf nicht in Tests durchsickern, die danach Logmeldungen
    pruefen (`caplog`)."""
    loggers = [logging.getLogger(), *(lg for lg in logging.root.manager.loggerDict.values() if isinstance(lg, logging.Logger))]
    saved = [(lg, lg.disabled, lg.level, lg.propagate, list(lg.handlers)) for lg in loggers]
    yield
    for lg, disabled, level, propagate, handlers in saved:
        lg.disabled, lg.propagate = disabled, propagate
        lg.setLevel(level)
        lg.handlers[:] = handlers


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """Datenbank auf dem Stand vor der Migration, mit Altdaten. Gibt (cfg, Pfad) zurueck."""
    monkeypatch.setattr(config, "_settings", _settings(tmp_path))
    monkeypatch.chdir(REPO_ROOT)
    cfg, _ = alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", repo_root=REPO_ROOT)
    command.upgrade(cfg, PREVIOUS_CORE_HEAD)
    db_path = tmp_path / "test.db"
    db = sqlite3.connect(str(db_path))
    try:
        db.execute(
            "INSERT INTO users (id, username, display_name, password_hash, is_active, is_owner, locale, created_at, updated_at) "
            "VALUES ('user-gast', 'gast', 'Gast', 'x', 1, 0, 'de', '2026-10-01', '2026-10-01')"
        )

        def add(row_id, action, actor_type, actor_id, reason=None, detail=None):
            db.execute(
                "INSERT INTO audit_log (id, ts, actor_type, actor_id, action, outcome, reason, detail) "
                "VALUES (?, '2026-10-01 10:00:00', ?, ?, ?, 'failure', ?, ?)",
                (row_id, actor_type, actor_id, action, reason, detail if isinstance(detail, str) else json.dumps(detail or {})),
            )

        # So schrieben aeltere Versionen es: der eingetippte Text als Akteur ...
        add("failed-typed", "login.failed", "user", TYPED, "Unbekannter Benutzername.")
        add("failed-long", "login.failed", "user", TYPED_LONG[:128], "Eingabe zu lang.")
        add("locked-typed", "login.locked", "user", TYPED, "Anmeldung vorübergehend gesperrt.",
            {"scopes": ["user"], "username": TYPED, "window_seconds": 900})
        # ... bei einem echten Konto die Konto-Kennung (bleibt).
        add("failed-known", "login.failed", "user", "user-gast", "Falsches Passwort.")
        add("locked-known", "login.locked", "user", "user-gast", "Anmeldung vorübergehend gesperrt.",
            {"scopes": ["user"], "username": "gast", "window_seconds": 900})
        # Neue Form und Fremdes bleiben unberuehrt.
        add("failed-new", "login.failed", "anonymous", "unbekannt", "Unbekannter Benutzername.",
            {"username_ref": "abcdef123456", "username_length": 5})
        add("other", "auth.password_changed", "user", TYPED, "Unbekannter Benutzername.", {"username": TYPED})
        # Eine Zeile mit kaputtem `detail` darf die Migration nicht abbrechen.
        add("locked-broken", "login.locked", "user", "kaputt", "gesperrt", "das ist kein JSON")
        db.commit()
    finally:
        db.close()
    return cfg, db_path


def _rows(db_path: Path) -> dict[str, tuple]:
    db = sqlite3.connect(str(db_path))
    try:
        return {
            r[0]: (r[1], r[2], json.loads(r[3]) if r[3].startswith("{") else r[3])
            for r in db.execute("SELECT id, actor_type, actor_id, detail FROM audit_log").fetchall()
        }
    finally:
        db.close()


def test_the_migration_is_the_only_core_head_and_follows_custom_apps():
    cfg, _ = alembic_config(repo_root=REPO_ROOT)
    script = ScriptDirectory.from_config(cfg)
    revision = script.get_revision(REVISION)
    assert revision.down_revision == PREVIOUS_CORE_HEAD
    assert REVISION in script.get_heads()


def test_typed_names_are_removed_from_old_entries(legacy_db):
    cfg, db_path = legacy_db
    command.upgrade(cfg, REVISION)
    rows = _rows(db_path)

    assert rows["failed-typed"] == ("anonymous", "unbekannt", {})
    assert rows["failed-long"] == ("anonymous", "unbekannt", {})
    assert rows["locked-typed"] == ("anonymous", "unbekannt", {"scopes": ["user"], "window_seconds": 900})
    # Konten bleiben, wie sie sind.
    assert rows["failed-known"][:2] == ("user", "user-gast")
    assert rows["locked-known"] == ("user", "user-gast", {"scopes": ["user"], "username": "gast", "window_seconds": 900})
    # Neue Eintraege, andere Aktionen und kaputte Zeilen bleiben stehen.
    assert rows["failed-new"] == ("anonymous", "unbekannt", {"username_ref": "abcdef123456", "username_length": 5})
    assert rows["other"] == ("user", TYPED, {"username": TYPED})
    assert rows["locked-broken"] == ("user", "kaputt", "das ist kein JSON")


def test_the_text_is_gone_from_the_database_file(legacy_db):
    """Nicht nur aus der Abfrage: auch in den freigewordenen Resten der Datei steht er nicht mehr
    (`PRAGMA secure_delete`). Der Eintrag `other` traegt den Text absichtlich weiter.

    Manche SQLite-Builds haben `secure_delete` schon von sich aus an. Damit der Test nicht davon
    abhaengt, startet jede Verbindung der Migration hier mit `secure_delete = OFF`; nur die
    Migration selbst darf es einschalten."""
    cfg, db_path = legacy_db
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA secure_delete = ON")  # keine Reste vom Aufraeumen der Ausgangslage
    db.execute("DELETE FROM audit_log WHERE id = 'other'")
    db.commit()
    db.close()
    assert TYPED.encode() in db_path.read_bytes(), "Ausgangslage: der Text steht in der Datei"

    def _secure_delete_off(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA secure_delete = OFF")
        cursor.close()

    event.listen(Engine, "connect", _secure_delete_off)
    try:
        command.upgrade(cfg, REVISION)
    finally:
        event.remove(Engine, "connect", _secure_delete_off)

    for path in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-journal")):
        if path.exists():
            assert TYPED.encode() not in path.read_bytes(), path.name


def test_running_the_cleanup_again_changes_nothing(legacy_db):
    cfg, db_path = legacy_db
    command.upgrade(cfg, REVISION)
    before = _rows(db_path)

    spec = importlib.util.spec_from_file_location("audit_names_migration", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import sqlalchemy as sa

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            assert module.anonymize_typed_login_names(connection) == 0
    finally:
        engine.dispose()
    assert _rows(db_path) == before


def test_downgrade_keeps_the_cleaned_entries(legacy_db):
    cfg, db_path = legacy_db
    command.upgrade(cfg, REVISION)
    before = _rows(db_path)
    command.downgrade(cfg, PREVIOUS_CORE_HEAD)
    assert _rows(db_path) == before
    command.upgrade(cfg, REVISION)
    assert _rows(db_path) == before
