"""Hilfsskripte der Module für root-Befehle (`root_helper` im Manifest), gelesen ohne den Code des Moduls zu importieren.

Ein Modul mit root-Bedarf bringt ein festes POSIX-sh-Skript mit, das neben `extension.toml` liegt. Die Einrichtung soll
es root-eigen auf den Server legen und dem Server-Benutzer per sudo genau dieses Skript erlauben statt `ALL`. Weil der
Text später als root läuft, prüft der Kern die Datei streng, bevor er sie je weitergibt. Ist etwas nicht in Ordnung, gibt
es für dieses Modul kein Hilfsskript (eine Zeile im Protokoll) und es gilt die bisherige Regel; das Laden des Moduls
scheitert daran nie. Name und Ort auf dem Server kommen aus dem SDK (`root_helper_path`), nie aus dem Modul."""

from __future__ import annotations

import hashlib
import logging
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from nodvard_sdk import root_helper_path

from .discovery import DiscoveredExtension

logger = logging.getLogger("nodvard_deck.ext")

MAX_HELPER_BYTES = 56 * 1024
"""Größte erlaubte Datei. Das Skript reist base64-kodiert im Einrichtungsbefehl mit, der als ein Argument an `sh -c`
geht (Linux erlaubt 128 KiB je Argument); alle Module zusammen müssen darunter bleiben (`host_setup.MAX_ONE_LINER`,
geprüft mit den mitgelieferten Skripten in `test_bundled_root_helpers.py`)."""
SHEBANG = "#!/bin/sh"
_ALLOWED_BYTES = frozenset(range(0x20, 0x7F)) | {0x0A, 0x09}
"""Druckbares ASCII, Zeilenumbruch und Tabulator. Kein CR: Mit Windows-Zeilenenden liefe das Skript auf dem Server nicht
(und hätte eine andere Prüfsumme als ein Image aus Linux), kein NUL, keine Steuerzeichen."""


@dataclass(frozen=True)
class RootHelperFile:
    """Ein geprüftes Hilfsskript, wie es auf den Server käme."""

    ext_id: str
    ext_name: str
    """Anzeigename des Moduls (für Texte wie „Hilfsskript für …“)."""
    version: int
    install_path: str
    """Ort auf dem Server, z. B. `/usr/local/lib/nodvard-deck/<id>-root`."""
    text: str
    sha256: str


def _reject(ext_id: str, reason: str) -> None:
    logger.warning("root_helper_rejected ext=%s reason=%s", ext_id, reason)


def _has_symlink(base: Path, relative: str) -> bool:
    current = base
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def read_root_helper(discovered: DiscoveredExtension) -> RootHelperFile | None:
    """Das Hilfsskript eines entdeckten Moduls oder `None` (keins angegeben, oder die Datei ist nicht in Ordnung)."""
    manifest = discovered.manifest
    spec = manifest.root_helper if manifest is not None else None
    if manifest is None or spec is None:
        return None
    ext_id = manifest.id
    if discovered.source != "bundled":
        # Bei pip-Paketen ist `source_path` der Wurzelordner der Distribution, eine Ordnergrenze wäre dort schwach.
        _reject(ext_id, "nur mitgelieferte Module dürfen ein Hilfsskript mitbringen")
        return None
    base = discovered.source_path
    path = base / spec.file
    try:
        if _has_symlink(base, spec.file):
            _reject(ext_id, "Pfad enthält einen symbolischen Link")
            return None
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            _reject(ext_id, "keine normale Datei")
            return None
        if not path.resolve(strict=True).is_relative_to(base.resolve(strict=True)):
            _reject(ext_id, "Datei liegt außerhalb des Modulordners")
            return None
        if info.st_size > MAX_HELPER_BYTES:
            _reject(ext_id, "Datei ist zu groß")
            return None
        raw = path.read_bytes()
    except OSError as exc:
        _reject(ext_id, f"Datei nicht lesbar ({type(exc).__name__})")
        return None
    if len(raw) > MAX_HELPER_BYTES:
        _reject(ext_id, "Datei ist zu groß")
        return None
    if not raw or any(byte not in _ALLOWED_BYTES for byte in raw):
        _reject(ext_id, "nur druckbares ASCII mit LF-Zeilenenden erlaubt")
        return None
    text = raw.decode("ascii")
    lines = text.split("\n")
    if not text.endswith("\n") or lines[0] != SHEBANG:
        _reject(ext_id, f"erste Zeile muss {SHEBANG!r} sein, letzte Zeile mit Zeilenumbruch enden")
        return None
    version_lines = [line for line in lines if line.startswith("HELPER_VERSION=")]
    if version_lines != [f"HELPER_VERSION={spec.version}"]:
        _reject(ext_id, "genau eine Zeile HELPER_VERSION=<Version aus dem Manifest> erwartet")
        return None
    try:
        install_path = root_helper_path(ext_id)
    except ValueError:
        _reject(ext_id, "Kennung taugt nicht für den Ort auf dem Server")
        return None
    return RootHelperFile(
        ext_id=ext_id,
        ext_name=manifest.name,
        version=spec.version,
        install_path=install_path,
        text=text,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
