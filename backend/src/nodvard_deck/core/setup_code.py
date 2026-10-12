"""Einrichtungscode für die Erstinbetriebnahme: Weil `POST /auth/bootstrap` ohne Anmeldung erreichbar ist, bekäme das
Konto sonst, wer die Seite zuerst aufruft; der Code steht im Container-Protokoll und gehört beim Anlegen eingegeben."""

from __future__ import annotations

import hmac
import os
import re
import secrets
from pathlib import Path

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""32 Zeichen ohne die verwechselbaren I, O, 0, 1; 12 Zeichen = 60 Bit."""

GROUPS = 3
GROUP_LEN = 4
# `NODVARD_DECK_SETUP_CODE` (alt: `LATTICE_SETUP_CODE`) setzt den Code vorab (dann gibt es keine Datei); kürzere als
# 8 Zeichen werden ignoriert, weil ein erratbarer Code den Zweck verfehlt.
MIN_PRESET_LEN = 8
# Klartext in `<data_dir>/setup_code.txt` (0600), kein Hash: Der Code muss bei jedem Start wieder ins Protokoll, und
# ein bei jedem Neustart neu gewürfelter wäre lästig. Gleicher Schutz wie `master.key` daneben; nach der Einrichtung
# wird die Datei gelöscht.
FILE_NAME = "setup_code.txt"

_STRIP = re.compile(r"[^A-Za-z0-9]")


def normalize(code: str) -> str:
    """Groß, ohne Striche und Leerzeichen; so wird gespeichert und verglichen."""
    return _STRIP.sub("", code or "").upper()


def generate() -> str:
    """`XXXX-XXXX-XXXX` aus dem kryptografischen Zufallsgenerator."""
    groups = ("".join(secrets.choice(ALPHABET) for _ in range(GROUP_LEN)) for _ in range(GROUPS))
    return "-".join(groups)


def code_file(data_dir: Path) -> Path:
    return data_dir / FILE_NAME


def valid_preset(preset: str | None) -> str | None:
    """Der vorgegebene Code (normalisiert) oder `None`, wenn keiner gesetzt oder er zu kurz ist."""
    if not preset:
        return None
    normalized = normalize(preset)
    return normalized if len(normalized) >= MIN_PRESET_LEN else None


def read_file(data_dir: Path) -> str | None:
    path = code_file(data_dir)
    try:
        value = normalize(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return value or None


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Von Anfang an 0600, nicht erst schreiben und dann chmod: kein Zeitfenster, in dem andere die Datei lesen könnten.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ensure(data_dir: Path, preset: str | None = None) -> tuple[str, bool]:
    """Gibt `(code, neu_erzeugt)` zurück: gültiger Preset (keine Datei), sonst der Dateicode, sonst ein neuer."""
    forced = valid_preset(preset)
    if forced is not None:
        return _pretty(forced), False
    existing = read_file(data_dir)
    if existing is not None:
        return _pretty(existing), False
    code = generate()
    _write_private(code_file(data_dir), code + "\n")
    return code, True


def clear(data_dir: Path) -> bool:
    """Löscht die Code-Datei; `True`, wenn eine da war."""
    try:
        code_file(data_dir).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def verify(data_dir: Path, candidate: str, preset: str | None = None) -> bool:
    """Vergleicht in konstanter Zeit mit dem gültigen Code; ohne Code (weder Preset noch Datei) immer `False`."""
    expected = valid_preset(preset) or read_file(data_dir)
    if expected is None:
        return False
    return hmac.compare_digest(normalize(candidate).encode("utf-8"), expected.encode("utf-8"))


def _pretty(normalized: str) -> str:
    """`XXXXXXXXXXXX` -> `XXXX-XXXX-XXXX` (Gruppen zu 4); fremde Längen bleiben gruppiert."""
    return "-".join(normalized[i : i + GROUP_LEN] for i in range(0, len(normalized), GROUP_LEN))


def banner(code: str) -> str:
    """Auffälliger Protokoll-Block für den Start (Code zusätzlich allein in einer Zeile); die Zeile
    `Einrichtungscode: ...` bleibt unverändert, weil Doku und `grep` sich darauf verlassen."""
    line = "=" * 64
    return (
        f"\n{line}\n"
        "  Nodvard Deck – Ersteinrichtung\n"
        f"  Einrichtungscode: {code} – im Browser eingeben\n"
        "\n"
        f"      {code}\n"
        "\n"
        "  (Es gibt noch kein Konto. Ohne diesen Code kann niemand das erste\n"
        "   Konto anlegen. Er verschwindet, sobald die Einrichtung fertig ist.)\n"
        f"{line}\n"
    )
