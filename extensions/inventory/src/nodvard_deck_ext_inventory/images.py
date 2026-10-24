"""Bilder der Gegenstände als Dateien unter `<ext_data_dir>/images/<image_id>.<ext>`; die Datenbank hält nur Metadaten."""

from __future__ import annotations

import os
import re
from pathlib import Path

ALLOWED_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# So benennt `save_image` jede Datei: Kennung und feste Endung, kein Punkt davor, kein Pfadtrenner.
_FILENAME_RE = re.compile(r"[0-9A-Za-z_-]{1,64}\.(?:" + "|".join(sorted(set(ALLOWED_CONTENT_TYPES.values()))) + ")")


def images_dir(ext_data_dir: object) -> Path:
    # `.resolve()`: `Settings.ext_data_dir` ist standardmäßig relativ, jeder abgeleitete Pfad hinge sonst vom
    # Arbeitsverzeichnis des Aufrufers ab.
    d = Path(str(ext_data_dir)).resolve() / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _named_path(ext_data_dir: object, filename: str) -> Path | None:
    """Der Pfad im Bildordner, `None` für einen Namen, den `save_image` nie vergibt (etwa aus einer veränderten
    Sicherung)."""
    return images_dir(ext_data_dir) / filename if _FILENAME_RE.fullmatch(filename) else None


def image_path(ext_data_dir: object, filename: str) -> Path | None:
    """Die Datei zum Lesen, `None` für einen fremden Namen oder einen Link, der aus dem Ordner hinausführt."""
    path = _named_path(ext_data_dir, filename)
    try:
        inside = path is not None and path.resolve().parent == path.parent.resolve()
    except (OSError, RuntimeError):  # Link-Schleife
        return None
    return path if inside else None


def save_image(ext_data_dir: object, image_id: str, extension: str, content: bytes) -> str:
    """Schreibt die Datei und gibt den relativen Dateinamen zurück, wie ihn `ItemImage.filename` speichert."""
    filename = f"{image_id}.{extension}"
    path = _named_path(ext_data_dir, filename)
    if path is None:
        raise ValueError(f"Ungültiger Dateiname für ein Bild: {filename!r}")
    # Ein vorhandener Link wird nicht verfolgt.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o666)
    with os.fdopen(fd, "wb") as fh:
        fh.write(content)
    return filename


def delete_image_file(ext_data_dir: object, filename: str) -> None:
    # `unlink` entfernt einen Link selbst, nie sein Ziel.
    path = _named_path(ext_data_dir, filename)
    if path is not None:
        path.unlink(missing_ok=True)
