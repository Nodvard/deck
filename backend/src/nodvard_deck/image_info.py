"""Herkunft des laufenden Images (Image ohne Tag und Version) aus einer Datei, die beim Bauen angelegt wird. Nur
Standardbibliothek, weil das Modul auch beim Bauen des Images läuft."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Bewusst eine Datei im Image und keine Umgebungsvariable (`ENV` im Dockerfile): Docker übernimmt die ENV eines Images in die
# Einstellungen jedes Containers. Wer einen Container mit dessen Einstellungen neu anlegt und nur das Image tauscht (Portainer
# "Recreate", Update-Helfer, die die Konfiguration klonen), nähme sonst die alte Version mit, und sie überdeckte die des neuen
# Images. Eine Datei im Image kommt immer aus dem Image, das gerade läuft.
PATH = Path("/app/image-info.json")
"""Ort der Datei im Image (`{"image": ..., "version": ...}`, `deploy/Dockerfile`). Ein selbst gebautes Image ohne die
Build-Argumente hat keine. Überschreibbar mit `NODVARD_DECK_IMAGE_INFO_PATH`, nur für Tests."""

FIELDS = ("image", "version")
MAX_BYTES = 4096
MAX_VALUE_LENGTH = 200


def clean(value: Any) -> str | None:
    """Text ohne Leerraum am Rand. Kein Text, leer, zu lang oder mit Steuerzeichen -> `None`."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_VALUE_LENGTH or not value.isprintable():
        return None
    return value


def read(path: Path | str = PATH) -> dict[str, str]:
    """Inhalt der Datei (nur gültige Angaben aus `FIELDS`) oder `{}` (fehlt, zu groß, kein JSON, kein Objekt): nie ein
    Fehler beim Start. `config.Settings` liest sie hinter einer ausdrücklich gesetzten Umgebungsvariable."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError:
        return {}
    if len(raw) > MAX_BYTES:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key in FIELDS if (value := clean(data.get(key))) is not None}


def write(path: Path | str, *, image: str | None, version: str | None) -> bool:
    """Schreibt die Datei mit den nicht leeren Angaben. Ohne Angaben keine Datei (`False`)."""
    info = {key: value for key, value in (("image", clean(image)), ("version", clean(version))) if value is not None}
    if not info:
        return False
    Path(path).write_text(json.dumps(info, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    """Beim Bauen des Images: `python -m nodvard_deck.image_info [PFAD]`, mit `IMAGE` (z. B. `ghcr.io/nodvard/deck`) und
    `VERSION` (z. B. `0.6.0-rc1`) aus den Build-Argumenten. Eine gesetzte, aber unbrauchbare Angabe bricht den Build ab."""
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else PATH
    given = {key: os.environ.get(key.upper(), "") for key in FIELDS}
    bad = [key.upper() for key, value in given.items() if value.strip() and clean(value) is None]
    if bad:
        print(f"image-info: unbrauchbare Angabe in {', '.join(bad)} (zu lang oder mit Steuerzeichen).", file=sys.stderr)
        return 1
    if write(path, image=given["image"], version=given["version"]):
        print(f"image-info: {path} geschrieben.")
    else:
        print("image-info: keine Angaben (selbst gebautes Image), keine Datei.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
