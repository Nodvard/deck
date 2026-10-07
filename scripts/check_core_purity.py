#!/usr/bin/env python3
"""CI-Waechter: der Kern kennt keine Extension.

Docs: docs/01-ARCHITECTURE.md §1.

Durchsucht `backend/src/nodvard_deck/**/*.py` nach Woertern, die auf eine konkrete
Extension/Fremdsoftware hindeuten (Proxmox, Shield, Ollama, ntfy, Nextcloud, TrueNAS,
Syncthing, Teleport, Docker als Produktbegriff, dazu der alte Produktname in der
Schreibweise "nexus") und nach dem Praefix `nodvard_deck_ext_` der Extension-Pakete:
der Kern importiert keine Extension. "Shield" gilt nur als eigenes Wort ohne `-` oder
`(` dahinter: `asyncio.shield(...)` und Symbolnamen wie "shield-check" (Icon-Auswahl)
sind keine Abhaengigkeit von Nodvard Shield. Kommentare und
String-Literal-Anweisungen (Modul-/Klassen-/Funktions-Docstrings UND die in diesem
Codebase durchgaengig genutzten "Attribut-Docstrings" -- ein freistehender
Dreifach-String direkt nach einer Feldzuweisung) sind ausgenommen: verboten ist die
*Abhaengigkeit* (ein Import, ein API-Aufruf, ein hartcodierter Connector), nicht die
*Erwaehnung* in Prosa, die zum Erklaeren des generischen Mechanismus oft noetig ist.

Der Standard-Produktname in `branding.py` ("Nodvard Deck") steht nicht auf der Sperrliste.

Exit-Code 0 = sauber, 1 = Verstoss gefunden, 2 = Aufrufproblem.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "backend" / "src" / "nodvard_deck"

FORBIDDEN_WORDS = [
    "proxmox",
    "nexus",
    "ollama",
    "ntfy",
    "nextcloud",
    "truenas",
    "syncthing",
    "teleport",
    "docker",
]

# Verbotene Muster mit eigener Regel (Name -> Regex-Quelltext), wenn ein einfaches Wort nicht reicht.
FORBIDDEN_PATTERNS: dict[str, str] = {
    # Der Name der Extension "Nodvard Shield". Ausgenommen sind `asyncio.shield(...)` (Aufruf, `(` dahinter) und Symbolnamen
    # mit Bindestrich wie "shield-check" (Icons in services/custom_apps.py und core/demo_seed.py). Jede dieser Ausnahmen ist
    # in backend/tests/test_core_purity.py an der echten Fundstelle belegt.
    "shield": r"\bshield\b(?![-(])",
    # Python-Pakete der Extensions heissen `nodvard_deck_ext_<id>`: der Kern importiert und nennt keine.
    "nodvard_deck_ext_": r"\bnodvard_deck_ext_",
}

# Datei -> Woerter, die dort ausdruecklich erlaubt sind (mit Begruendung im Docstring
# der jeweiligen Datei).
ALLOWLIST: dict[str, set[str]] = {}

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    *((word, re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)) for word in FORBIDDEN_WORDS),
    *((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in FORBIDDEN_PATTERNS.items()),
)


def _code_only(source: str) -> str:
    """Neutralisiert jede bare String-Literal-Anweisung, damit sie nicht mitgeprueft
    wird -- nicht nur klassische Modul-/Klassen-/Funktions-Docstrings, sondern auch die
    in diesem Codebase durchgaengig genutzten Attribut-Docstrings (`feld: Mapped[...] =
    ...` gefolgt von einem freistehenden Dreifach-String, wie Sphinx/griffe sie
    interpretieren). Eine freistehende String-Anweisung hat nie eine Laufzeitwirkung --
    sie ist per Definition Dokumentation, niemals eine echte Abhaengigkeit.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    string_expr_spans: list[tuple[int, int]] = [
        (node.lineno, getattr(node, "end_lineno", node.lineno))
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]

    lines = source.splitlines(keepends=True)
    for start, end in string_expr_spans:
        for lineno in range(start, end + 1):
            idx = lineno - 1
            if 0 <= idx < len(lines):
                # Zeileninhalt neutralisieren, Zeilenumbruch behalten (Zeilennummern
                # in der Fehlerausgabe sollen stimmen).
                newline = "\n" if lines[idx].endswith("\n") else ""
                lines[idx] = "#" * (len(lines[idx]) - len(newline)) + newline
    return "".join(lines)


def _strip_comments(line: str) -> str:
    """Grobe Heuristik: alles ab einem `#` ignorieren. Reicht fuer diesen Codebase-Stil
    (kein `#` in String-Literalen, die relevante Woerter enthalten koennten)."""
    return line.split("#", 1)[0]


def check_file(path: Path) -> list[tuple[int, str, str]]:
    """Gibt Liste von (Zeilennummer, Zeile, gefundenes Wort) zurueck."""
    source = path.read_text(encoding="utf-8")
    allowed = ALLOWLIST.get(path.name, set())
    code = _code_only(source)

    findings: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(code.splitlines(), start=1):
        stripped = _strip_comments(line)
        hits = [
            (match.start(), word)
            for word, rule in RULES
            if word not in allowed
            for match in rule.finditer(stripped)
        ]
        findings.extend((lineno, line.rstrip(), word) for _pos, word in sorted(hits))
    return findings


def main() -> int:
    if not CORE_ROOT.is_dir():
        print(f"FEHLER: {CORE_ROOT} existiert nicht.", file=sys.stderr)
        return 2

    all_findings: list[tuple[Path, int, str, str]] = []
    for path in sorted(CORE_ROOT.rglob("*.py")):
        for lineno, line, word in check_file(path):
            all_findings.append((path.relative_to(REPO_ROOT), lineno, line, word))

    if not all_findings:
        print(f"OK: Kern ist sauber ({CORE_ROOT.relative_to(REPO_ROOT)}).")
        return 0

    print("Verstoss gegen die Kern-Reinheit (docs/01-ARCHITECTURE.md Paragraph 1):\n")
    for rel_path, lineno, line, word in all_findings:
        print(f"  {rel_path}:{lineno}: Wort '{word}'\n    {line.strip()}")
    print(
        f"\n{len(all_findings)} Fund(e). Der Kern darf keine konkrete Extension/Software "
        f"kennen -- fehlt eine Faehigkeit, gehoert sie als Protokoll in nodvard_sdk, "
        f"implementiert von einer Extension."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
