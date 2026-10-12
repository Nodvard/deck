"""Bilder der Gegenstände als Dateien unter `<ext_data_dir>/images/<image_id>.<ext>`; die Datenbank hält nur Metadaten."""

from __future__ import annotations

from pathlib import Path

ALLOWED_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def images_dir(ext_data_dir: object) -> Path:
    # `.resolve()`: `Settings.ext_data_dir` ist standardmäßig relativ, jeder abgeleitete Pfad hinge sonst vom
    # Arbeitsverzeichnis des Aufrufers ab.
    d = Path(str(ext_data_dir)).resolve() / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def image_path(ext_data_dir: object, filename: str) -> Path:
    return images_dir(ext_data_dir) / filename


def save_image(ext_data_dir: object, image_id: str, extension: str, content: bytes) -> str:
    """Schreibt die Datei und gibt den relativen Dateinamen zurück, wie ihn `ItemImage.filename` speichert."""
    filename = f"{image_id}.{extension}"
    image_path(ext_data_dir, filename).write_bytes(content)
    return filename


def delete_image_file(ext_data_dir: object, filename: str) -> None:
    image_path(ext_data_dir, filename).unlink(missing_ok=True)
