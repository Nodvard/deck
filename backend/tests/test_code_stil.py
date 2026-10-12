"""Keine Emojis in Code-Dateien; was heute noch drinsteht, steht in `ALLOWED` und wird mit jedem Aufräum-PR weniger."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CODE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".sh", ".css")
SKIPPED_DIRS = {"dist", "node_modules"}
SKIPPED_ROOTS = {"reference"}  # altes Vergleichsmaterial, kein Produktcode

# Normale Textzeichen der Oberfläche, die kein Emoji sind: U+2713 bis U+2718 (Haken und Kreuze).
TEXT_SYMBOLS = frozenset(range(0x2713, 0x2719))

# Pfad -> Grund. Neue Einträge sind nicht vorgesehen; ein Eintrag ohne Emoji in der Datei lässt den Test fehlschlagen.
ALLOWED: dict[str, str] = {
    "backend/tests/test_apps_api.py": "Testdaten: Nutzer wählen Emojis als App-Symbol",
    "backend/tests/test_custom_apps_service.py": "Testdaten: Nutzer wählen Emojis als App-Symbol",
    "backend/tests/test_ext_documents.py": "Test-Docstring nennt ein Emoji",
    "backend/tests/test_ext_network_address_check.py": "Testdaten: Warnzeichen im Namen eines nicht erreichbaren Servers",
    "backend/tests/test_ext_ntfy_kanaele.py": "Testdaten: Zeichen für die Wichtigkeit in Push-Nachrichten",
    "backend/tests/test_ext_shield_parsing.py": "Testdaten: Antwort der KI enthält Emojis",
    "backend/tests/test_overview_api.py": "Testdaten: Warnzeichen im Namen eines nicht erreichbaren Servers",
    "backend/tests/test_system_restore_api.py": "Testdaten: Emoji im Passwort (Kopfzeile bei der Wiederherstellung)",
    "extensions/inventory/frontend/src/importCsv.test.ts": "Testdaten: Name wird mitten in einem Emoji gekürzt",
    "extensions/ntfy/src/nodvard_deck_ext_ntfy/channels.py": "Meldungen: Zeichen für die Wichtigkeit in Push-Nachrichten",
    "extensions/service-matrix/frontend/src/ServiceMatrixPage.test.tsx": "Testdaten: Warnzeichen im Servernamen",
    "extensions/service-matrix/src/nodvard_deck_ext_service_matrix/capabilities.py": "Oberfläche: Warnzeichen vor dem Servernamen",
    "extensions/shield/src/nodvard_deck_ext_shield/__init__.py": "Shield-Text: Warnzeichen im Platzhalter der KI-Antwort",
    "extensions/shield/src/nodvard_deck_ext_shield/ollama.py": "Shield-Text: Info- und Warnzeichen in Meldungen der KI-Wache",
    "extensions/shield/src/nodvard_deck_ext_shield/parsing.py": "Shield-Text: Parser erkennt die Markierungen in der KI-Antwort",
    "frontend/src/components/AppDialog.test.tsx": "Testdaten: Nutzer wählen Emojis als App-Symbol",
    "frontend/src/components/AppDialog.tsx": "Oberfläche: Beispiel-Emoji im Eingabefeld für das App-Symbol",
    "frontend/src/components/AppsSection.test.tsx": "Testdaten: Nutzer wählen Emojis als App-Symbol",
    "frontend/src/components/FolderPickerDialog.tsx": "Oberfläche: Ordner-Symbol im Ordner-Auswahl-Dialog",
    "frontend/src/lib/apps.test.ts": "Testdaten: Nutzer wählen Emojis als App-Symbol",
    "frontend/src/lib/keyboardLayouts.test.ts": "Testdaten: Emoji, das sich nicht tippen lässt",
    "frontend/src/lib/restore.test.ts": "Testdaten: Emoji im Passwort (Kopfzeile bei der Wiederherstellung)",
    "frontend/src/preview/fixtures.ts": "Testdaten der Design-Vorschau: App-Symbol",
    "frontend/src/routes/Cockpit.test.tsx": "Testdaten: Warnzeichen im Namen eines nicht erreichbaren Servers",
    "frontend/src/routes/FilesPage.test.tsx": "Testdaten: Ordner-Symbol vor dem Namen in der Dateiliste",
    "frontend/src/routes/FilesPage.tsx": "Oberfläche: Ordner- und Link-Symbol in der Dateiliste",
}


def is_emoji(cp: int) -> bool:
    """Emoji-Blöcke, Symbole und Dingbats (ohne Textzeichen), Stern und Kreis, Emoji-Darstellung, U+2139, U+203C, U+2049."""
    if 0x1F000 <= cp <= 0x1FAFF:
        return True
    if 0x2600 <= cp <= 0x27BF:
        return cp not in TEXT_SYMBOLS
    return 0x2B50 <= cp <= 0x2B55 or cp in (0xFE0F, 0x2139, 0x203C, 0x2049)


def _tracked_code_files() -> list[str]:
    if shutil.which("git") is None:
        pytest.skip("git nicht verfügbar")
    git = ["git", "-C", str(REPO)]
    top = subprocess.run([*git, "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != REPO:
        pytest.skip("kein Git-Arbeitsverzeichnis (z. B. Image ohne .git)")
    listed = subprocess.run([*git, "ls-files", "-z"], capture_output=True, check=True).stdout
    paths = [p for p in listed.decode("utf-8").split("\0") if p.endswith(CODE_SUFFIXES)]
    return [p for p in paths if not (set(p.split("/")) & SKIPPED_DIRS) and p.split("/")[0] not in SKIPPED_ROOTS]


def _emoji_lines(path: str) -> dict[int, list[int]]:
    try:
        text = (REPO / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    found: dict[int, list[int]] = {}
    for number, line in enumerate(text.split("\n"), start=1):
        marks = sorted({ord(ch) for ch in line if is_emoji(ord(ch))})
        if marks:
            found[number] = marks
    return found


@pytest.fixture(scope="module")
def emoji_hits() -> dict[str, dict[int, list[int]]]:
    return {path: lines for path in _tracked_code_files() if (lines := _emoji_lines(path))}


def test_keine_emojis_ausserhalb_der_ausnahmeliste(emoji_hits):
    problems = [
        f"{path}:{number}: {', '.join(f'U+{cp:04X}' for cp in marks)}"
        for path, lines in sorted(emoji_hits.items())
        if path not in ALLOWED
        for number, marks in lines.items()
    ]
    assert not problems, "Emoji in Code-Dateien (Zeichen entfernen, nicht in ALLOWED aufnehmen):\n" + "\n".join(problems)


def test_ausnahmeliste_ohne_veraltete_eintraege(emoji_hits):
    stale = sorted(path for path in ALLOWED if path not in emoji_hits)
    assert not stale, "Kein Emoji mehr in diesen Dateien, Einträge aus ALLOWED löschen:\n" + "\n".join(stale)
