#!/usr/bin/env python3
"""Wächter: keine Zeichenkette sieht für einen Geheimnis-Scanner wie ein echtes Geheimnis aus.

Ohne Argument prüft er die Dateien im Repository, mit einem Ordner alle Dateien darin (so ruft ihn
`scripts/export_public.sh` auf). Attrappen in Tests setzen solche Werte zur Laufzeit aus Teilen zusammen.
Exit-Code 0 = sauber, 1 = Fund.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Aus Teilen zusammengesetzt, damit sich der Wächter nicht selbst meldet.
_KEY = "PRIVATE" + " KEY"
_AGE = "AGE-SECRET" + "-KEY-1"

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Echte Werte enthalten fast immer eine Ziffer (Basic auch `+/`); Prosa wie „Bearer Authentifizierung“ bleibt frei.
    ("Bearer-Wert", re.compile(r"\b(?i:bearer) (?=[A-Za-z0-9._~+/-]*[0-9])[A-Za-z0-9._~+/-]{16,}")),
    ("Basic-Anmeldung", re.compile(r"\bBasic (?=[A-Za-z0-9+/]*[0-9+/])[A-Za-z0-9+/]{16,}={0,2}")),
    ("GitHub-Token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("GitLab-Token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("AWS-Schlüssel", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack-Token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}|hooks\.slack\.com/(?:services|workflows|triggers)/[A-Z0-9]")),
    ("Discord-Webhook", re.compile(r"discord(?:app)?\.com/api/(?:v[0-9]+/)?webhooks/[0-9]")),
    ("npm-Token", re.compile(r"\bnpm_[A-Za-z0-9]{36}(?![A-Za-z0-9])")),
    # Nicht mitten in Namen wie `disk-usage-sk-…`, und ein echter Schlüssel enthält Ziffern.
    ("API-Schlüssel", re.compile(r"(?<![\w-])(?:sk-(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9_-]{20,}|[sr]k_(?:live|test)_[A-Za-z0-9]{10,})")),
    ("Google-Schlüssel", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("ntfy-Token", re.compile(r"\btk_[a-z0-9]{24,}")),
    ("Proxmox-Token", re.compile(r"PVEAPIToken=[^\s=]+=[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.(?:eyJ[A-Za-z0-9_-]{6,}|[A-Za-z0-9_-]{15,})\.")),
    ("Fernet-Token", re.compile(r"\bgAAAAA[A-Za-z0-9_-]{40,}")),
    ("age-Schlüssel", re.compile(_AGE + r"[0-9A-Z]{58}")),
    # Auch mit Treiberzusatz (`mssql+pyodbc`) und ohne Benutzer. Kein Geheimnis sind Platzhalter in spitzen Klammern und
    # Vorlagen wie `${DB_PASSWORD}`, `{passwort}` oder `%(pw)s`.
    (
        "Adresse mit Passwort",
        re.compile(
            r"\b(?:https?|postgres(?:ql)?|mysql|mariadb|mssql|oracle|mongodb|rediss?|amqps?)(?:\+[a-z0-9_]+)?"
            r"://[^\s:/@'\"`]*:(?![${%])[^\s/@'\"`<>]+@"
        ),
    ),
)

# Ein Schlüssel zählt erst mit Kopf und Fuß in derselben Datei: Der Kopf allein steht als Platzhalter im Formular.
KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*" + _KEY + r"(?: BLOCK)?-----")
KEY_END = re.compile(r"-----END [A-Z0-9 ]*" + _KEY + r"(?: BLOCK)?-----")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.text}"


def check_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    has_key_end = KEY_END.search(text) is not None
    for number, line in enumerate(text.splitlines(), 1):
        rules = [name for name, pattern in RULES if pattern.search(line)]
        if has_key_end and KEY_BEGIN.search(line):
            rules.append("privater Schlüssel")
        findings.extend(Finding(path, number, rule, line.strip()[:160]) for rule in rules)
    return findings


def tracked_files(root: Path = REPO_ROOT) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [name for name in out.decode().split("\0") if name]


def files_below(root: Path) -> list[str]:
    names: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        names.extend(str((Path(current) / name).relative_to(root)) for name in sorted(files))
    return names


def find_violations(root: Path = REPO_ROOT, *, tracked: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    for name in tracked_files(root) if tracked else files_below(root):
        path = root / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(check_text(name, text))
    return findings


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("Aufruf: check_secret_lookalikes.py [ORDNER]", file=sys.stderr)
        return 2
    # Die Windows-Konsole schreibt cp1252; ein „→“ aus einer gefundenen Zeile bräche sonst die Ausgabe ab.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")
    findings = find_violations(Path(argv[0]), tracked=False) if argv else find_violations()
    for finding in findings:
        print(finding)
    if findings:
        print(f"\n{len(findings)} Stelle(n) sehen wie echte Geheimnisse aus. Attrappen zur Laufzeit aus Teilen zusammensetzen.")
        return 1
    print("OK: Nichts sieht wie ein echtes Geheimnis aus.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
