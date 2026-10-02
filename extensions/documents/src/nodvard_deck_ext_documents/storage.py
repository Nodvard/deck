"""Dokument-Speicherung -- dieselbe Aufteilung wie
`nodvard_deck_ext_inventory.images`: Bytes auf der Platte unter
`<ext_data_dir>/documents/<document_id>.<ext>`, die DB traegt nur Metadaten.
`.resolve()` aus demselben, bereits einmal live gefundenen Grund wie dort
(`Settings.ext_data_dir` ist standardmaessig relativ)."""

from __future__ import annotations

import os
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
    # 0700 ausdruecklich (nicht nur ueber die umask des Prozesses): Rechnungen und Ausweise sollen in einem
    # eingebundenen Hostordner nicht fuer andere Benutzer lesbar sein.
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def document_path(ext_data_dir: object, filename: str) -> Path:
    return documents_dir(ext_data_dir) / filename


def save_document(ext_data_dir: object, document_id: str, extension: str, content: bytes) -> str:
    filename = f"{document_id}.{extension}"
    path = document_path(ext_data_dir, filename)
    # Neue Datei nur fuer den Besitzer (0600); ein vorhandener Link wird nicht verfolgt.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(content)
    return filename


def delete_document_file(ext_data_dir: object, filename: str) -> None:
    document_path(ext_data_dir, filename).unlink(missing_ok=True)
