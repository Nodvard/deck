"""Dokument-Speicherung -- dieselbe Aufteilung wie
`nodvard_deck_ext_inventory.images`: Bytes auf der Platte unter
`<ext_data_dir>/documents/<document_id>.<ext>`, die DB traegt nur Metadaten.
`.resolve()` aus demselben, bereits einmal live gefundenen Grund wie dort
(`Settings.ext_data_dir` ist standardmaessig relativ)."""

from __future__ import annotations

from pathlib import Path

ALLOWED_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


def documents_dir(ext_data_dir: object) -> Path:
    d = Path(str(ext_data_dir)).resolve() / "documents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def document_path(ext_data_dir: object, filename: str) -> Path:
    return documents_dir(ext_data_dir) / filename


def save_document(ext_data_dir: object, document_id: str, extension: str, content: bytes) -> str:
    filename = f"{document_id}.{extension}"
    document_path(ext_data_dir, filename).write_bytes(content)
    return filename


def delete_document_file(ext_data_dir: object, filename: str) -> None:
    document_path(ext_data_dir, filename).unlink(missing_ok=True)
