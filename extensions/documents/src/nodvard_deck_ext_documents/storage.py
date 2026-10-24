"""Dokumente als Dateien unter `<ext_data_dir>/documents/<document_id>.<ext>`; die Datenbank hält nur Metadaten."""

from __future__ import annotations

import os
import re
from pathlib import Path

ALLOWED_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
# Obergrenze beim Hochladen und beim Abruf aus der Nextcloud.
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
# So benennt `save_document` jede Datei: Kennung und feste Endung, kein Punkt davor, kein Pfadtrenner.
_FILENAME_RE = re.compile(r"[0-9A-Za-z_-]{1,64}\.(?:" + "|".join(sorted(set(ALLOWED_CONTENT_TYPES.values()))) + ")")


def documents_dir(ext_data_dir: object) -> Path:
    # `.resolve()`: `Settings.ext_data_dir` ist standardmäßig relativ, jeder abgeleitete Pfad hinge sonst vom
    # Arbeitsverzeichnis des Aufrufers ab.
    d = Path(str(ext_data_dir)).resolve() / "documents"
    # 0700 ausdrücklich (nicht nur über die umask des Prozesses): Rechnungen und Ausweise sollen in einem
    # eingebundenen Hostordner nicht für andere Benutzer lesbar sein.
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _named_path(ext_data_dir: object, filename: str) -> Path | None:
    """Der Pfad im Dokumentordner, `None` für einen Namen, den `save_document` nie vergibt (etwa aus einer veränderten
    Sicherung)."""
    return documents_dir(ext_data_dir) / filename if _FILENAME_RE.fullmatch(filename) else None


def document_path(ext_data_dir: object, filename: str) -> Path | None:
    """Die Datei zum Lesen, `None` für einen fremden Namen oder einen Link, der aus dem Ordner hinausführt."""
    path = _named_path(ext_data_dir, filename)
    try:
        inside = path is not None and path.resolve().parent == path.parent.resolve()
    except (OSError, RuntimeError):  # Link-Schleife
        return None
    return path if inside else None


def save_document(ext_data_dir: object, document_id: str, extension: str, content: bytes) -> str:
    # Der Dateiname entsteht nur aus Kennung und fester Endung, nie aus dem Namen des Nutzers (der steht nur in der
    # Datenbank): so führt kein Upload aus dem Ordner hinaus.
    filename = f"{document_id}.{extension}"
    path = _named_path(ext_data_dir, filename)
    if path is None:
        raise ValueError(f"Ungültiger Dateiname für ein Dokument: {filename!r}")
    # Neue Datei nur für den Besitzer (0600); ein vorhandener Link wird nicht verfolgt.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(content)
    return filename


def delete_document_file(ext_data_dir: object, filename: str) -> None:
    # `unlink` entfernt einen Link selbst, nie sein Ziel.
    path = _named_path(ext_data_dir, filename)
    if path is not None:
        path.unlink(missing_ok=True)
