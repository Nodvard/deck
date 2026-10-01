"""Bild-Speicherung fuer inventory-Items.

Bild-BYTES liegen auf der Platte unter `<ext_data_dir>/images/<image_id>.<ext>`,
die DB (`ItemImage`) traegt nur Metadaten (Dateiname, Content-Type, Groesse) --
dasselbe Muster wie `nodvard_deck.branding.save_logo_file()`, nur ein Verzeichnis mit
vielen Dateien statt genau einer. `.resolve()` auf dem Basisverzeichnis aus
demselben, bereits einmal live gefundenen Grund wie
`nodvard_deck_ext_scripts.repo.ScriptRepo.__init__` (`Settings.ext_data_dir` ist
standardmaessig relativ -- ohne `.resolve()` haengt jeder abgeleitete Pfad vom
Arbeitsverzeichnis des Aufrufers ab)."""

from __future__ import annotations

from pathlib import Path

ALLOWED_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def images_dir(ext_data_dir: object) -> Path:
    d = Path(str(ext_data_dir)).resolve() / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def image_path(ext_data_dir: object, filename: str) -> Path:
    return images_dir(ext_data_dir) / filename


def save_image(ext_data_dir: object, image_id: str, extension: str, content: bytes) -> str:
    """Schreibt die Datei, gibt den (relativen) Dateinamen zurueck -- genau das, was
    `ItemImage.filename` speichert, damit ein spaeterer Zugriff nie wieder die
    Endung neu herleiten muss."""
    filename = f"{image_id}.{extension}"
    image_path(ext_data_dir, filename).write_bytes(content)
    return filename


def delete_image_file(ext_data_dir: object, filename: str) -> None:
    image_path(ext_data_dir, filename).unlink(missing_ok=True)
