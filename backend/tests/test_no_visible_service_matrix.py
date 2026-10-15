"""Wächter: Das Modul heißt „Docker“; „Service-Matrix“ steht in Oberfläche und aktueller Doku nur als Übergangshinweis."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Die technische Kennung `service-matrix` (klein, mit Bindestrich) und die Protokoll-Namen `service_matrix.*` bleiben erlaubt.
NAME_RE = re.compile(r"\b(?:Service-[Mm]atrix|[Ss]ervice [Mm]atrix)\b")
FILES = (
    "extensions/*/frontend/src/**/*.ts*",
    "extensions/*/frontend/dist/*.js",
    "extensions/*/src/**/*.py",
    "extensions/*/extension.toml",
    "extensions/*/settings.schema.json",
    "frontend/src/**/*.ts*",
    "backend/src/nodvard_deck/**/*.py",
    "sdk/python/nodvard_sdk/**/*.py",
    "README.md",
    "README.en.md",
    "deploy/README.md",
    "docs/0[1-4]-*.md",
    "docs/1[0-2]-*.md",
    "docs/en/*.md",
)
# (Pfad, genauer Teilstring, Grund): nur der Teilstring wird freigegeben, der Rest der Zeile weiter geprüft.
ALLOWED = [
    ("docs/01-ARCHITECTURE.md", "bis 0.8 „Service-Matrix“", "Übergangshinweis zur Umbenennung"),
    ("frontend/src/preview/fixtures.ts", "Service-Matrix zeigt in der neuen Spalte", "Vorschau-Daten: ein alter Eintrag im Änderungsprotokoll"),
]


def _files() -> list[Path]:
    found = {p for pattern in FILES for p in ROOT.glob(pattern) if p.is_file()}
    return sorted(p for p in found if ".test." not in p.name and "/test/" not in p.as_posix())


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_no_visible_service_matrix():
    findings = []
    for path in _files():
        rel = _rel(path)
        allowed = [text for where, text, _ in ALLOWED if rel == where]
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for text in allowed:
                line = line.replace(text, "")
            if NAME_RE.search(line):
                findings.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    assert not findings, "Sichtbares „Service-Matrix“, bitte „Docker“ schreiben:\n" + "\n".join(findings)


def test_every_exception_is_still_needed():
    for where, text, _ in ALLOWED:
        assert text in (ROOT / where).read_text(encoding="utf-8"), f"Ausnahme greift nicht mehr: {where}: {text}"
