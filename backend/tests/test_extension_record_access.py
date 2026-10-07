"""Registry-Zeilen einer Erweiterung werden nur ueber `services.extensions.get_record()` gelesen.

Nach einer Umbenennung (`legacy_ids` im Manifest) liegen Einstellungen und Zeitplaene in der Zeile der
alten Kennung, der Speicher-Kennung. Ein direktes `session.get(ExtensionRecord, kennung)` oder eine Suche
`ExtensionRecord.id == kennung` / `ExtensionRecord.id.in_(...)` faende dann keine oder die falsche Zeile
(einen verwaisten Zwilling). Deshalb ist beides ausserhalb von `services/extensions.py` verboten;
`get_record()` loest die Kennung richtig auf. Eine Liste aller Zeilen (`select(ExtensionRecord)` ohne
Bedingung auf die Kennung, `order_by(ExtensionRecord.id)`) bleibt erlaubt.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "nodvard_deck"

DIRECT_GET = re.compile(r"\.get\(\s*(?:[A-Za-z_]\w*\.)*ExtensionRecord\b")
"""`session.get(ExtensionRecord, ...)`: eine Zeile genau zu dieser Kennung."""

ID_LOOKUP = re.compile(r"\bExtensionRecord\.id\s*(?:==|!=|\.in_\()")
"""Suche nach Kennung in einer Abfrage (`ExtensionRecord.id == x`, `ExtensionRecord.id.in_(...)`)."""

PATTERNS = {"session.get": DIRECT_GET, "Suche nach Kennung": ID_LOOKUP}

ALLOWED: dict[str, tuple[set[str], str]] = {
    "services/extensions.py": (
        set(PATTERNS), "Hier liegt get_record() selbst, dazu die Zeilensuche beim Entdecken.",
    ),
}
"""Dateien, die eine Zeile direkt nach Kennung lesen duerfen: welche Muster und warum."""


def _offenders() -> list[str]:
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        allowed = ALLOWED.get(rel, (set(), ""))[0]
        text = path.read_text(encoding="utf-8")
        for name, pattern in PATTERNS.items():
            if name in allowed:
                continue
            for match in pattern.finditer(text):
                found.append(f"{rel}:{text.count(chr(10), 0, match.start()) + 1} ({name})")
    return found


def test_no_direct_registry_row_access_outside_the_extension_service():
    assert _offenders() == [], (
        "Registry-Zeilen nur ueber services.extensions.get_record() lesen (Speicher-Kennung nach einer "
        "Umbenennung)"
    )


def test_the_patterns_catch_the_usual_spellings():
    for text in (
        "await session.get(ExtensionRecord, ext_id)",
        "await session.get(\n    ExtensionRecord, ext_id)",
        "await session.get(models.ExtensionRecord, ext_id)",
    ):
        assert DIRECT_GET.search(text), text
    for text in (
        "select(ExtensionRecord).where(ExtensionRecord.id == x)",
        "select(ExtensionRecord).where(ExtensionRecord.id==x)",
        "select(ExtensionRecord.id).where(ExtensionRecord.id.in_(ids))",
        "select(ExtensionRecord).where(ExtensionRecord.id != x)",
    ):
        assert ID_LOOKUP.search(text), text
    for text in (
        "await get_record(session, ext_id)",
        "select(ExtensionRecord).order_by(ExtensionRecord.id)",
        "select(ExtensionRecord).where(ExtensionRecord.state == 'enabled')",
        "select(ExtensionRecord.id, ExtensionRecord.manifest)",
    ):
        assert not DIRECT_GET.search(text) and not ID_LOOKUP.search(text), text


def test_allowed_files_exist_and_still_need_their_exception():
    """Jede Ausnahme hat einen Grund und wird noch gebraucht (sonst gehoert sie weg)."""
    for rel, (names, reason) in ALLOWED.items():
        assert (SRC / rel).is_file(), rel
        assert reason.strip() and names <= set(PATTERNS), rel
        text = (SRC / rel).read_text(encoding="utf-8")
        assert any(PATTERNS[name].search(text) for name in names), f"{rel}: Ausnahme wird nicht mehr gebraucht"
