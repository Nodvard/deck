"""Hilfsskripte der Module (`root_helper` im Manifest, `ext/root_helpers.py`): Der Kern liest die Datei beim Einschalten
selbst, ohne Code des Moduls, und gibt sie nur weiter, wenn sie streng in Ordnung ist. Eine schlechte Datei kostet nur
das Hilfsskript (eine Warnung im Protokoll), nie das Modul.

Test-Erweiterungen im tmp-Ordner wie in `test_extensions_legacy_ids.py`."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from nodvard_deck.core.scheduler import reset_scheduler_service
from nodvard_deck.ext import discovery
from nodvard_deck.ext.root_helpers import MAX_HELPER_BYTES, read_root_helper
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extensions as extensions_service

EXT_LOGGER = "nodvard_deck.ext"
GOOD = "#!/bin/sh\nset -eu\nHELPER_VERSION=2\ncase ${1:-} in version) echo \"$HELPER_VERSION\" ;; *) exit 64 ;; esac\n"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    # Ein früherer Test, der Alembic im Prozess laufen ließ, kann vorhandene Logger abgeschaltet haben.
    monkeypatch.setattr(logging.getLogger(EXT_LOGGER), "disabled", False)
    monkeypatch.setattr(logging.getLogger("nodvard_deck.services.extensions"), "disabled", False)
    reset_extension_runtime()
    reset_scheduler_service()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    reset_scheduler_service()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_helper_"):
            del sys.modules[name]


def _write_ext(root: Path, ext_id: str, *, helper: str | None = 'file = "root-helper/x-root.sh", version = 2',
               text: str | bytes | None = GOOD) -> Path:
    module = "nodvard_deck_ext_" + ext_id.replace("-", "_")
    ext_dir = root / ext_id
    (ext_dir / "src" / module).mkdir(parents=True)
    line = f"root_helper = {{ {helper} }}\n" if helper else ""
    (ext_dir / "extension.toml").write_text(
        f'[extension]\nid = "{ext_id}"\nname = "Test"\nversion = "0.1.0"\napi_version = "0.1"\n'
        f'entrypoint = "{module}:Extension"\n{line}permissions = ["hosts.read"]\n',
        encoding="utf-8",
    )
    (ext_dir / "src" / module / "__init__.py").write_text(
        "class Extension:\n"
        "    async def setup(self, ctx):\n        pass\n"
        "    async def on_start(self, ctx):\n        pass\n"
        "    async def on_stop(self, ctx):\n        pass\n",
        encoding="utf-8",
    )
    if text is not None:
        (ext_dir / "root-helper").mkdir()
        data = text.encode("ascii") if isinstance(text, str) else text
        (ext_dir / "root-helper" / "x-root.sh").write_bytes(data)
    return ext_dir


def _found(tmp_path: Path, ext_id: str = "helper-ext") -> discovery.DiscoveredExtension:
    [found] = [d for d in discovery.scan_directory(tmp_path) if d.ok and d.id == ext_id]
    return found


def _rejections(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == EXT_LOGGER and "root_helper_rejected" in r.getMessage()]


def test_a_good_helper_is_read_with_its_checksum_and_place(tmp_path, caplog):
    _write_ext(tmp_path, "helper-ext")
    helper = read_root_helper(_found(tmp_path))
    assert helper is not None
    assert (helper.ext_id, helper.version, helper.text) == ("helper-ext", 2, GOOD)
    assert helper.install_path == "/usr/local/lib/nodvard-deck/helper-ext-root"
    assert helper.sha256 == hashlib.sha256(GOOD.encode()).hexdigest()
    assert _rejections(caplog) == []


@pytest.mark.parametrize(("extra", "fallback"), [("", True), (", sudo_fallback = true", True), (", sudo_fallback = false", False)])
def test_the_helper_says_whether_the_module_falls_back_to_the_full_rule(tmp_path, extra, fallback):
    _write_ext(tmp_path, "helper-ext", helper=f'file = "root-helper/x-root.sh", version = 2{extra}')
    helper = read_root_helper(_found(tmp_path))
    assert helper is not None and helper.sudo_fallback is fallback


def test_sudo_fallback_must_be_a_real_bool(tmp_path):
    _write_ext(tmp_path, "helper-ext", helper='file = "root-helper/x-root.sh", version = 2, sudo_fallback = "false"')
    assert not [d for d in discovery.scan_directory(tmp_path) if d.ok and d.id == "helper-ext"]


def test_without_root_helper_there_is_nothing_to_read(tmp_path, caplog):
    _write_ext(tmp_path, "helper-ext", helper=None, text=None)
    assert read_root_helper(_found(tmp_path)) is None
    assert _rejections(caplog) == []


@pytest.mark.parametrize(
    ("text", "case"),
    [
        (b"", "leer"),
        (GOOD.replace("\n", "\r\n").encode(), "Windows-Zeilenenden"),
        (GOOD.encode() + b"\x00", "NUL"),
        (GOOD.replace("set -eu", "set -eu # \xfc").encode("latin-1"), "kein ASCII"),
        (GOOD.replace("set -eu", "set -eu \x1b[0m").encode(), "Steuerzeichen"),
        (GOOD.replace("#!/bin/sh", "#!/bin/bash").encode(), "andere erste Zeile"),
        (GOOD.replace("#!/bin/sh\n", "\n#!/bin/sh\n").encode(), "erste Zeile leer"),
        (GOOD.rstrip("\n").encode(), "ohne Zeilenumbruch am Ende"),
        (GOOD.replace("HELPER_VERSION=2", "HELPER_VERSION=3").encode(), "andere Version"),
        (GOOD.replace("HELPER_VERSION=2", "HELPER_VERSION=02").encode(), "Version mit Null"),
        (GOOD.replace("HELPER_VERSION=2", "HELPER_VERSION=2 ").encode(), "Version mit Leerzeichen"),
        (GOOD.replace("HELPER_VERSION=2", "HELPER_VERSION=2\nHELPER_VERSION=2").encode(), "Version doppelt"),
        (GOOD.replace("HELPER_VERSION=2\n", "").encode(), "Version fehlt"),
        (b"#!/bin/sh\nHELPER_VERSION=2\n" + b"#" * MAX_HELPER_BYTES + b"\n", "zu groß"),
    ],
)
def test_a_bad_file_gives_no_helper_and_a_warning(tmp_path, caplog, text, case):
    _write_ext(tmp_path, "helper-ext", text=text)
    assert read_root_helper(_found(tmp_path)) is None, case
    assert len(_rejections(caplog)) == 1, case


def test_a_missing_file_gives_no_helper(tmp_path, caplog):
    _write_ext(tmp_path, "helper-ext", text=None)
    assert read_root_helper(_found(tmp_path)) is None
    assert len(_rejections(caplog)) == 1


def test_a_directory_with_the_name_gives_no_helper(tmp_path, caplog):
    ext_dir = _write_ext(tmp_path, "helper-ext", text=None)
    (ext_dir / "root-helper" / "x-root.sh").mkdir(parents=True)
    assert read_root_helper(_found(tmp_path)) is None
    assert len(_rejections(caplog)) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="symbolische Links brauchen unter Windows eigene Rechte")
def test_symlinks_are_refused_for_the_file_and_for_a_folder_on_the_way(tmp_path, caplog):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x-root.sh").write_text(GOOD, encoding="ascii")

    ext_dir = _write_ext(tmp_path / "a", "helper-ext", text=None)
    (ext_dir / "root-helper").mkdir()
    os.symlink(outside / "x-root.sh", ext_dir / "root-helper" / "x-root.sh")
    assert read_root_helper(_found(tmp_path / "a")) is None

    ext_dir = _write_ext(tmp_path / "b", "helper-ext", text=None)
    os.symlink(outside, ext_dir / "root-helper", target_is_directory=True)
    assert read_root_helper(_found(tmp_path / "b")) is None
    assert len(_rejections(caplog)) == 2


def test_pip_packages_cannot_bring_a_helper(tmp_path, caplog):
    _write_ext(tmp_path, "helper-ext")
    found = _found(tmp_path)
    as_pip = discovery.DiscoveredExtension(
        id=found.id, source="pip", source_path=found.source_path, manifest=found.manifest
    )
    assert read_root_helper(as_pip) is None
    assert len(_rejections(caplog)) == 1


# -- beim Einschalten ---------------------------------------------------------------------------------


async def _boot(db_session, settings) -> None:
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    for record in (await db_session.execute(select(ExtensionRecord))).scalars():
        record.state = "enabled"
    await db_session.commit()
    await extensions_service.load_enabled_from_registry(app, db_session, settings)
    await db_session.commit()


@pytest.mark.asyncio
async def test_enabling_reads_the_helper_and_a_bad_one_does_not_stop_the_module(tmp_path, db_session, test_settings, caplog):
    root = tmp_path / "extensions"
    _write_ext(root, "helper-good")
    _write_ext(root, "helper-bad", text=GOOD.replace("HELPER_VERSION=2", "HELPER_VERSION=7"))
    _write_ext(root, "helper-none", helper=None, text=None)
    _write_ext(root, "helper-strict", helper='file = "root-helper/x-root.sh", version = 2, sudo_fallback = false')
    settings = test_settings.model_copy(update={"extensions_dir": root, "ext_data_dir": tmp_path / "ext-data"})

    await _boot(db_session, settings)

    loaded = get_extension_runtime().loaded
    assert sorted(loaded) == ["helper-bad", "helper-good", "helper-none", "helper-strict"]
    assert loaded["helper-good"].root_helper is not None
    assert loaded["helper-good"].root_helper.install_path.endswith("/helper-good-root")
    assert loaded["helper-bad"].root_helper is None
    assert loaded["helper-none"].root_helper is None
    assert _rejections(caplog) == [
        "root_helper_rejected ext=helper-bad reason=genau eine Zeile HELPER_VERSION=<Version aus dem Manifest> erwartet"
    ]

    stored = {
        r.id: r.manifest for r in (await db_session.execute(select(ExtensionRecord))).scalars()
    }
    assert stored["helper-good"]["root_helper"] == {"file": "root-helper/x-root.sh", "version": 2}
    assert stored["helper-strict"]["root_helper"] == {"file": "root-helper/x-root.sh", "version": 2, "sudo_fallback": False}
    # Ohne Hilfsskript bleibt die Registry-Zeile, wie sie war.
    assert "root_helper" not in stored["helper-none"]
    assert {(r.state, r.last_error) for r in (await db_session.execute(select(ExtensionRecord))).scalars()} == {
        ("enabled", None)
    }
