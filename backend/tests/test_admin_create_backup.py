"""`python -m nodvard_deck.admin backup-key` und `create-backup`: die verschlüsselte Sicherung für `deploy/backup.sh`."""

from __future__ import annotations

import json
import sqlite3

import pytest
from nodvard_deck import admin
from nodvard_deck.core.backup import crypto
from nodvard_deck.core.backup import format as fmt
from restore_helpers import (
    PASSWORD,
    REPO_ROOT,
    fill_live,
    make_layout,
    settings_for,
    stage,
)


@pytest.fixture(autouse=True)
def _no_privilege_switch(monkeypatch):
    monkeypatch.setattr(admin, "drop_root_to_data_owner", lambda settings: None)
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture
def env(tmp_path):
    layout = make_layout(tmp_path)
    fill_live(layout, "live")
    return layout, settings_for(layout)


def _set_key(layout) -> crypto.DerivedKey:
    """Legt den Sicherungsschlüssel so ab, wie ihn die Oberfläche speichert (nur Empfänger, `key_id` und KDF)."""
    key = crypto.derive_key(PASSWORD)
    value = {"recipient": key.recipient, "key_id": key.key_id, "kdf": key.kdf.to_dict(), "created_at": "2026-10-13T08:00:00+00:00"}
    conn = sqlite3.connect(layout.db_path)
    conn.execute(
        "INSERT INTO settings (key, scope, user_id, value) VALUES ('backup.key', 'global', '', ?)", (json.dumps({"value": value}),)
    )
    conn.commit()
    conn.close()
    return key


def test_backup_key_says_whether_a_key_is_set_up(env, capsys):
    layout, settings = env
    assert admin.main(["backup-key"], settings) == 0
    assert capsys.readouterr().out == "kein-schluessel\n"

    key = _set_key(layout)
    assert admin.main(["backup-key"], settings) == 0
    assert capsys.readouterr().out == f"schluessel {key.key_id}\n"


def test_create_backup_without_a_key_writes_nothing_and_says_why(env, capsysbinary):
    _layout, settings = env
    assert admin.main(["create-backup"], settings) == 3
    out, err = capsysbinary.readouterr()
    assert out == b""
    assert "Sicherungsschlüssel" in err.decode()


@pytest.mark.parametrize("secret", ["passwort", "wiederherstellungsschluessel"])
def test_create_backup_writes_an_encrypted_backup_that_restores_with_password_or_recovery_key(env, capsysbinary, tmp_path, secret):
    layout, settings = env
    key = _set_key(layout)

    assert admin.main(["create-backup"], settings) == 0
    out, err = capsysbinary.readouterr()
    assert out.startswith(fmt.MAGIC)
    assert b"master-live" not in out, "der Master-Key steht nur verschlüsselt in der Datei"
    assert key.key_id in err.decode() and PASSWORD not in err.decode()

    upload = tmp_path / "sicherung.ndbak"
    upload.write_bytes(out)
    _rid, staged = stage(layout, upload, PASSWORD if secret == "passwort" else key.identity)
    assert staged.summary["owner_name"] == "live-owner"
    assert any(path.endswith("master.key") for path in staged.files), sorted(staged.files)


def test_create_backup_refuses_to_write_into_a_terminal(env, monkeypatch, capsys):
    layout, settings = env
    _set_key(layout)

    class _Terminal:
        def isatty(self) -> bool:
            return True

    with pytest.raises(admin.CliError, match="Binärdatei"):
        admin.create_backup(settings, _Terminal())
