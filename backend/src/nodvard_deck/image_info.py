"""Herkunft des laufenden Images: aus welchem Image (ohne Tag) es stammt und welche Version es ist.

Das Release-Image (`.github/workflows/release.yml`) bekommt beim Bauen die Build-Argumente `IMAGE` (z. B.
`ghcr.io/nodvard/deck`) und `VERSION` (z. B. `0.6.0` oder `0.6.0-rc1`). `deploy/Dockerfile` ruft damit
`python -m nodvard_deck.image_info /app/image-info.json` auf und legt so die Datei `PATH` an
(`{"image": ..., "version": ...}`, nur die Angaben, die nicht leer sind). Ein selbst gebautes Image ohne diese
Build-Argumente (z. B. ueber `scripts/deploy_pi.sh`) hat keine Datei.

Bewusst eine Datei im Image und keine Umgebungsvariable (`ENV` im Dockerfile): Docker uebernimmt die ENV eines
Images in die Einstellungen jedes Containers. Wer einen Container mit dessen Einstellungen neu anlegt und dabei nur
das Image tauscht (Portainer "Recreate", Update-Helfer, die die Konfiguration klonen), nimmt so die alte Version
in den neuen Container mit; dort ueberdeckte sie die des neuen Images. Eine Datei im Image kommt dagegen immer aus
dem Image, das gerade laeuft.

Gelesen wird sie von `config.Settings` (Felder `image` und `build`): Eine ausdruecklich gesetzte, nicht leere
Umgebungsvariable `NODVARD_DECK_IMAGE`/`NODVARD_DECK_BUILD` (Rueckfall `LATTICE_*`) geht vor, dann diese Datei,
sonst gibt es keinen Wert. Fehlt die Datei oder ist sie kaputt, gilt sie als nicht da -- nie ein Fehler beim Start.

Nur Standardbibliothek: Das Modul laeuft auch beim Bauen des Images.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

PATH = Path("/app/image-info.json")
"""Ort der Datei im Image (`deploy/Dockerfile`). Ueberschreibbar mit `NODVARD_DECK_IMAGE_INFO_PATH`, nur fuer Tests."""

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
    """Inhalt der Datei (nur gueltige Angaben aus `FIELDS`) oder `{}`: fehlt, zu gross, kein JSON, kein Objekt."""
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
    """Beim Bauen des Images: `python -m nodvard_deck.image_info [PFAD]`, mit `IMAGE` und `VERSION` aus der
    Umgebung (die Build-Argumente). Eine gesetzte, aber unbrauchbare Angabe bricht den Build ab."""
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
