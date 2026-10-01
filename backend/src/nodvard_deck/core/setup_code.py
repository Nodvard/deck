"""Einrichtungscode fuer die Erstinbetriebnahme.

Problem: `POST /auth/bootstrap` legt den ersten Nutzer als Owner an und ist dafuer ohne
Anmeldung erreichbar. Wer die Seite nach der Installation zuerst aufruft, bekaeme das
Konto -- im Heimnetz meist der Richtige, aber nicht zwingend (Gast im WLAN, ein Geraet
mit Schadsoftware, ein versehentlich nach aussen freigegebener Port).

Loesung: solange kein Owner existiert, gibt es einen zufaelligen Einrichtungscode
(3 x 4 Zeichen, ohne verwechselbare Zeichen). Er steht im Protokoll des Containers
(`docker compose logs nodvard-deck`) -- also nur fuer den, der Zugriff auf den Server hat --
und muss beim Anlegen des Kontos eingegeben werden.

**Ablage:** als Klartext in `<data_dir>/setup_code.txt` (0600), NICHT nur als Hash. Der
Code muss bei JEDEM Start wieder ins Protokoll, bis die Einrichtung fertig ist -- ein
Hash liesse sich nicht erneut anzeigen, und ein bei jedem Neustart neu gewuerfelter Code
waere beim Probieren (Container neu gestartet, Code nochmal aus dem Protokoll suchen)
unnoetig lästig. Die Datei liegt neben `master.key` und `jwt_secret.key` und hat dieselbe
Schutzwirkung: wer sie lesen kann, hat ohnehin Zugriff auf alles im Datenverzeichnis.
Nach der Einrichtung wird sie geloescht, der Code ist dann wertlos (der Endpunkt lehnt
ohnehin ab, sobald ein Nutzer existiert).

`NODVARD_DECK_SETUP_CODE` (alt: `LATTICE_SETUP_CODE`) setzt den Code vorab (z. B. fuer Automatisierung oder wenn das
Protokoll nicht lesbar ist); dann wird keine Datei angelegt. Zu kurze Vorgaben
(weniger als 8 Zeichen) werden ignoriert, weil ein erratbarer Code den Zweck verfehlt.

Verglichen wird in konstanter Zeit (`hmac.compare_digest`), Gross-/Kleinschreibung,
Striche und Leerzeichen sind egal. Die Drosselung der Fehlversuche passiert im
Endpunkt (`core.login_limit`, wie beim Login).
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
from pathlib import Path

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""32 Zeichen, ohne I, O, 0, 1 (verwechselbar). 12 Zeichen = 60 Bit."""

GROUPS = 3
GROUP_LEN = 4
MIN_PRESET_LEN = 8
FILE_NAME = "setup_code.txt"

_STRIP = re.compile(r"[^A-Za-z0-9]")


def normalize(code: str) -> str:
    """Gross, ohne Striche/Leerzeichen -- so wird gespeichert und verglichen."""
    return _STRIP.sub("", code or "").upper()


def generate() -> str:
    """`XXXX-XXXX-XXXX` aus dem kryptografischen Zufallsgenerator."""
    groups = ("".join(secrets.choice(ALPHABET) for _ in range(GROUP_LEN)) for _ in range(GROUPS))
    return "-".join(groups)


def code_file(data_dir: Path) -> Path:
    return data_dir / FILE_NAME


def valid_preset(preset: str | None) -> str | None:
    """Der vorgegebene Code (normalisiert) oder `None`, wenn keiner gesetzt/zu kurz ist."""
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
    # Von Anfang an 0600 anlegen (nicht erst schreiben, dann chmod): kein Zeitfenster,
    # in dem die Datei fuer andere lesbar waere.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ensure(data_dir: Path, preset: str | None = None) -> tuple[str, bool]:
    """Gibt `(code, neu_erzeugt)` zurueck. Ein gueltiger Preset gewinnt (keine Datei);
    sonst bleibt ein vorhandener Dateicode bestehen, sonst wird einer erzeugt."""
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
    """Loescht die Code-Datei. `True`, wenn eine da war."""
    try:
        code_file(data_dir).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def verify(data_dir: Path, candidate: str, preset: str | None = None) -> bool:
    """Konstantzeit-Vergleich mit dem gueltigen Code. Ohne Code (weder Preset noch Datei)
    gibt es nichts Richtiges -> immer `False`; der Aufrufer legt vorher per `ensure()` an."""
    expected = valid_preset(preset) or read_file(data_dir)
    if expected is None:
        return False
    return hmac.compare_digest(normalize(candidate).encode("utf-8"), expected.encode("utf-8"))


def _pretty(normalized: str) -> str:
    """`XXXXXXXXXXXX` -> `XXXX-XXXX-XXXX` (Gruppen zu 4); fremde Laengen bleiben gruppiert."""
    return "-".join(normalized[i : i + GROUP_LEN] for i in range(0, len(normalized), GROUP_LEN))


def banner(code: str) -> str:
    """Auffaelliger Protokoll-Block fuer den Start.

    Die Zeile mit "Einrichtungscode: ..." bleibt wie sie ist (Doku und `grep` verlassen sich
    darauf). Zusaetzlich steht der Code gross und allein in einer Zeile, mit Leerzeilen davor und
    danach -- so ist er in einem langen Protokoll (Docker Desktop, Portainer, NAS-Oberflaechen)
    sofort zu sehen und laesst sich per Doppelklick bzw. Zeilenauswahl kopieren.
    """
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
