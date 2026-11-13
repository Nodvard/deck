import json
import os
from pathlib import Path

import pytest

from nodvard_deck.core import vault


@pytest.mark.parametrize("failure", ["write", "sync", "replace"])
def test_failed_rotation_keeps_previous_keys(test_settings, monkeypatch, failure):
    keyring = vault.rotate_master_key(test_settings)
    ciphertext = vault.encrypt_str(vault.fernet_for_version(keyring, 2), "Testwert")
    path = test_settings.vault_keyring_path
    previous = path.read_bytes()
    files = set(path.parent.iterdir())

    def fail(*args):
        if failure == "write":
            args[1].write('{"1":')
            args[1].flush()
        raise OSError("Schreiben fehlgeschlagen")

    target, name = {
        "write": (vault.json, "dump"),
        "sync": (vault.os, "fsync"),
        "replace": (vault.os, "replace"),
    }[failure]
    with monkeypatch.context() as patch:
        patch.setattr(target, name, fail)
        with pytest.raises(OSError, match="Schreiben fehlgeschlagen"):
            vault.rotate_master_key(test_settings)

    assert path.read_bytes() == previous
    assert set(path.parent.iterdir()) == files
    reloaded = vault.load_keyring(test_settings)
    assert reloaded == keyring
    assert vault.decrypt_str(vault.fernet_for_version(reloaded, 2), ciphertext) == "Testwert"


def test_rotation_syncs_private_file_before_replacing(test_settings, monkeypatch):
    keyring = vault.rotate_master_key(test_settings)
    path = test_settings.vault_keyring_path
    previous = path.read_bytes()
    synced = []
    replaced = []
    fsync = os.fsync
    replace = os.replace

    def sync(fd):
        fsync(fd)
        synced.append(True)

    def replace_file(source, destination):
        source = Path(source)
        assert synced == [True]
        assert source.parent == path.parent
        assert source != path
        if os.name != "nt":
            assert source.stat().st_mode & 0o777 == 0o600
        assert path.read_bytes() == previous
        stored = json.loads(source.read_text(encoding="utf-8"))
        assert set(stored) == {"1", "2", "3"}
        assert stored["2"].encode("ascii") == keyring[2]
        replace(source, destination)
        replaced.append(True)

    monkeypatch.setattr(vault.os, "fsync", sync)
    monkeypatch.setattr(vault.os, "replace", replace_file)
    rotated = vault.rotate_master_key(test_settings)
    assert replaced == [True]
    assert vault.load_keyring(test_settings) == rotated
