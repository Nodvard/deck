"""Neue Dateien im Datenordner sind nur fuer den Besitzer lesbar, auch ohne passende umask des Prozesses."""

from __future__ import annotations

import ast
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "extensions" / "documents" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nodvard_deck_ext_documents import storage

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="Dateirechte gibt es nur unter POSIX")


@posix_only
def test_saved_documents_and_their_folder_are_owner_only_even_with_an_open_umask(tmp_path):
    old = os.umask(0o022)
    try:
        name = storage.save_document(tmp_path / "ext", "abc", "pdf", b"%PDF-1.4")
    finally:
        os.umask(old)
    folder = tmp_path / "ext" / "documents"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE((folder / name).stat().st_mode) == 0o600
    assert (folder / name).read_bytes() == b"%PDF-1.4"


@posix_only
def test_saving_again_overwrites_and_never_follows_a_planted_link(tmp_path):
    folder = storage.documents_dir(tmp_path / "ext")
    target = tmp_path / "fremd.txt"
    target.write_text("unberuehrt", encoding="utf-8")
    (folder / "abc.pdf").symlink_to(target)
    with pytest.raises(OSError):
        storage.save_document(tmp_path / "ext", "abc", "pdf", b"neu")
    assert target.read_text(encoding="utf-8") == "unberuehrt"
    (folder / "abc.pdf").unlink()
    storage.save_document(tmp_path / "ext", "abc", "pdf", b"eins")
    storage.save_document(tmp_path / "ext", "abc", "pdf", b"zwei")
    assert (folder / "abc.pdf").read_bytes() == b"zwei"


ENTRY_POINTS = [(package, module) for package in ("nodvard_deck", "lattice") for module in ("admin", "boot")]


@pytest.mark.parametrize(("package", "module"), ENTRY_POINTS)
def test_command_line_entry_points_close_the_umask_before_main(package, module):
    # `docker compose exec` erbt die umask des Entrypoints nicht; ohne diese Zeile entstuenden Dateien mit 0644.
    # Gilt auch fuer die Startdateien des alten Paketnamens (der Befehl steht so in der Anleitung zum Wiederherstellen).
    tree = ast.parse((ROOT / "backend" / "src" / package / f"{module}.py").read_text(encoding="utf-8"))
    guard = next(
        node for node in tree.body
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and ast.unparse(node.test).startswith("__name__ ==")
    )
    calls = [ast.unparse(node.value) for node in guard.body if isinstance(node, ast.Expr)]
    assert "os.umask(63)" in calls or "os.umask(0o077)" in calls
    umask_line = next(i for i, node in enumerate(guard.body) if "umask" in ast.unparse(node))
    main_line = next(i for i, node in enumerate(guard.body) if "main()" in ast.unparse(node))
    assert umask_line < main_line


@posix_only
@pytest.mark.parametrize("module", ["admin", "boot"])
def test_old_name_launchers_run_main_with_a_closed_umask(module, monkeypatch):
    import importlib
    import runpy

    real = importlib.import_module(f"nodvard_deck.{module}")
    seen: list[int] = []

    def fake_main(*_args, **_kwargs):
        current = os.umask(0)
        os.umask(current)
        seen.append(current)
        return 0

    monkeypatch.setattr(real, "main", fake_main)
    old = os.umask(0o022)
    try:
        # Die Startdatei selbst ausfuehren (wie `python -m` mit dem alten Namen), unabhaengig davon, was schon importiert ist.
        with pytest.raises(SystemExit) as info:
            runpy.run_path(str(ROOT / "backend" / "src" / "lattice" / f"{module}.py"), run_name="__main__")
    finally:
        os.umask(old)
    assert info.value.code == 0
    assert seen == [0o077]
