#!/usr/bin/env python3
"""Lizenz-Wächter: keine (A)GPL-/LGPL-/SSPL-Abhängigkeit, keine unbekannte Lizenz.

Nodvard Deck steht unter der PolyForm Noncommercial License 1.0.0 und wird zusätzlich
kommerziell lizenziert. Das geht nur, wenn jede mitgelieferte Abhängigkeit selbst
freizügig lizenziert ist -- eine Copyleft-Abhängigkeit würde verlangen, das ganze
Programm unter ihre Lizenz zu stellen. Geprüft werden (wie in `scripts/third_party_licenses.py`):

* alle Python-Pakete aus `deploy/constraints.txt` (installierte Metadaten),
* alle ausgelieferten npm-Pakete (Production-Dependencies des Frontends und der
  Extension-Bundles, Lizenzangabe aus `frontend/package-lock.json`).

Erlaubt: MIT, BSD (alle Varianten), Apache-2.0, ISC, PSF-2.0/Python-2.0, Unlicense, 0BSD,
Zlib sowie MPL-2.0 (nur UNVERÄNDERT verwenden; erscheint als Hinweis im Bericht).
"A OR B" ist erlaubt, wenn mindestens eine Option erlaubt ist (dulwich: Apache-2.0 OR GPL
-> Apache-2.0 gilt). "A AND B": beide müssen erlaubt sein.

Was nicht eindeutig ist (falsche/unklare Metadaten, bewusst geprüfte Sonderfälle), steht
mit Begründung in `EXCEPTIONS`. Eine Ausnahme gilt nur für die dort genannte Version:
ändert sich die Version, schlägt der Wächter wieder an und die Lizenz wird neu geprüft.

    python scripts/check_licenses.py            # Exit 0 = sauber, 1 = Verstoß, 2 = Umgebung unvollständig
    python scripts/check_licenses.py --liste    # zusätzlich jedes Paket mit Lizenz auflisten
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import third_party_licenses as tpl

REPO_ROOT = tpl.REPO_ROOT


@dataclass(frozen=True)
class Ausnahme:
    version: str  # nur diese Version wurde geprüft
    bewertet_als: str  # wie wir die Lizenz einordnen
    grund: str


# Schlüssel: (Ökosystem, Paketname klein; Python nach PEP 503 normalisiert).
EXCEPTIONS: dict[tuple[str, str], Ausnahme] = {
    ("python", "asyncssh"): Ausnahme(
        version="2.24.0",
        bewertet_als="EPL-2.0 (Option von 'EPL-2.0 OR GPL-2.0-or-later')",
        grund=(
            "Doppellizenz EPL-2.0 oder GPL. Wir nehmen die EPL-Option: schwaches Datei-Copyleft, "
            "asyncssh wird unverändert als Bibliothek eingebunden (kein Patch, kein Kopieren von "
            "Dateien). Steht nicht auf der Erlaubt-Liste, deshalb bewusst hier eingetragen."
        ),
    ),
    ("python", "pillow"): Ausnahme(
        version="12.3.0",
        bewertet_als="MIT-CMU (HPND-Familie, permissiv)",
        grund=(
            "Die Pillow-Lizenz heißt in SPDX 'MIT-CMU' (früher HPND): MIT-ähnlich, freizügig, "
            "kein Copyleft. Steht nicht wörtlich auf der Erlaubt-Liste."
        ),
    ),
    ("python", "pyrage"): Ausnahme(
        version="1.4.0",
        bewertet_als="MIT (plus gebündelte Rust-Crates unter MIT/Apache-2.0/BSD/Unicode)",
        grund=(
            "Die Metadaten nennen keine Lizenz, die Lizenzdatei im Wheel (dist-info/licenses/LICENSE) und das "
            "README sagen MIT. Die einkompilierten Rust-Crates (rage/age u. a.) stehen laut mitgelieferter "
            "SBOM (dist-info/sboms/pyrage.cyclonedx.json) unter MIT, Apache-2.0, BSD, Unlicense-oder-MIT bzw. "
            "Unicode-3.0; die eine Doppellizenz 'Apache-2.0 OR GPL-2.0-only' nutzen wir als Apache-2.0."
        ),
    ),
    ("python", "pypdfium2"): Ausnahme(
        version="5.13.0",
        bewertet_als="BSD-3-Clause AND Apache-2.0 (plus gebündelte freizügige Bibliotheken)",
        grund=(
            "Die Metadaten nennen 'BSD-3-Clause, Apache-2.0, dependency licenses' (keine SPDX-Formel). "
            "Gemeint sind pypdfium2 selbst (Apache-2.0/BSD-3) und die im PDFium-Binärpaket gebündelten "
            "Bibliotheken (PDFium, FreeType, ICU, libjpeg-turbo, libpng, zlib, lcms2, OpenJPEG, abseil ...), "
            "alle freizügig; ihre Texte stehen in THIRD_PARTY_LICENSES."
        ),
    ),
}

# Reihenfolge = Schweregrad (kleiner ist besser).
OK, HINWEIS, UNBEKANNT, COPYLEFT = 0, 1, 2, 3
_NAMES = {OK: "erlaubt", HINWEIS: "erlaubt mit Hinweis", UNBEKANNT: "unbekannt", COPYLEFT: "Copyleft"}

_ALLOWED_IDS = {"mit", "mit-0", "0bsd", "isc", "apache-2.0", "psf-2.0", "python-2.0", "python-2.0.1", "unlicense", "zlib"}
_NOTE_IDS = {"mpl-2.0"}
_BSD_RE = re.compile(r"^bsd(-\d-clause(-[a-z0-9]+)*)?$")
_COPYLEFT_RE = re.compile(
    r"(?<![a-z])(a|l)?gpl|sspl|\bgnu\b|affero|lesser general|server side public|general public license"
)

# Freitext-Angaben älterer Pakete (Classifier, License-Feld) auf SPDX-Kennungen abbilden.
_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(the )?mit( license)?( \(mit\))?$"), "mit"),
    (re.compile(r"^apache[- ]?(software )?(license)?[, ]*(version |v)?(2\.0|2)?$"), "apache-2.0"),
    (re.compile(r"^(new |simplified |modified )?bsd( license| licence)?( \(.*\))?$"), "bsd-3-clause"),
    (re.compile(r"^isc( license)?( \(iscl\))?$"), "isc"),
    (re.compile(r"^(python software foundation license|psf|psf license)$"), "psf-2.0"),
    (re.compile(r"^(mozilla public license( 2\.0)?( \(mpl 2\.0\))?|mpl 2\.0)$"), "mpl-2.0"),
    (re.compile(r"^(the )?unlicense( \(unlicense\))?$"), "unlicense"),
    (re.compile(r"^zlib(/libpng)?( license)?( \(zlib\))?$"), "zlib"),
]


@dataclass(frozen=True)
class Verdict:
    rank: int
    hinweise: frozenset[str] = frozenset()
    schlimmster: str = ""  # der Teil der Lizenzangabe, an dem es hängt


def canonical(phrase: str) -> str:
    low = re.sub(r"\s+", " ", phrase.strip().lower())
    for pattern, spdx in _ALIASES:
        if pattern.match(low):
            return spdx
    return low


def classify_token(phrase: str) -> Verdict:
    name = canonical(phrase)
    if _COPYLEFT_RE.search(name):
        return Verdict(COPYLEFT, schlimmster=phrase.strip())
    if name in _ALLOWED_IDS or _BSD_RE.match(name):
        return Verdict(OK)
    if name in _NOTE_IDS:
        return Verdict(HINWEIS, frozenset({"MPL-2.0"}))
    return Verdict(UNBEKANNT, schlimmster=phrase.strip())


_TOKEN_RE = re.compile(r"\(|\)|,|;|\b(?:AND|OR|WITH)\b|\b(?:and|or|with)\b|[^\s(),;]+")
_OPERATORS = {"AND", "OR", "WITH", ",", ";", "(", ")"}


def _tokens(expression: str) -> list[str]:
    """Operatoren einzeln, dazwischen zusammenhängende Wörter zu einer Bezeichnung
    ('Apache Software License' bleibt ein Stück)."""
    out: list[str] = []
    words: list[str] = []
    for raw in _TOKEN_RE.findall(expression):
        up = raw.upper()
        if up in _OPERATORS:
            if words:
                out.append(" ".join(words))
                words = []
            out.append(up)
        else:
            words.append(raw)
    if words:
        out.append(" ".join(words))
    return out


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def parse_or(self) -> Verdict:
        options = [self.parse_and()]
        while self.peek() == "OR":
            self.take()
            options.append(self.parse_and())
        # Die beste Option gilt; bei Gleichstand die mit weniger Hinweisen.
        return min(options, key=lambda v: (v.rank, len(v.hinweise)))

    def parse_and(self) -> Verdict:
        parts = [self.parse_with()]
        while self.peek() in ("AND", ",", ";"):
            self.take()
            parts.append(self.parse_with())
        worst = max(parts, key=lambda v: v.rank)
        hinweise = frozenset().union(*(v.hinweise for v in parts))
        return Verdict(worst.rank, hinweise, worst.schlimmster)

    def parse_with(self) -> Verdict:
        verdict = self.parse_atom()
        if self.peek() == "WITH":  # 'Apache-2.0 WITH LLVM-exception': es zählt die Lizenz davor
            self.take()
            if self.peek() not in (None, ")"):
                self.take()
        return verdict

    def parse_atom(self) -> Verdict:
        token = self.peek()
        if token is None:
            raise ValueError("unvollständiger Ausdruck")
        if token == "(":
            self.take()
            verdict = self.parse_or()
            if self.peek() != ")":
                raise ValueError("Klammer nicht geschlossen")
            self.take()
            return verdict
        if token in _OPERATORS:
            raise ValueError(f"unerwarteter Operator {token!r}")
        return classify_token(self.take())


_FORMULA_RE = re.compile(r";|\b(AND|OR|WITH)\b", re.IGNORECASE)


def evaluate_license(declared: str) -> Verdict:
    """Bewertet eine Lizenzangabe (SPDX-Formel oder Freitext) nach der Erlaubt-Liste."""
    declared = (declared or "").strip()
    if not declared:
        return Verdict(UNBEKANNT, schlimmster="(keine Lizenzangabe)")
    if not _FORMULA_RE.search(declared):
        whole = classify_token(declared)  # ganze Angabe als Freitext ('Apache License, Version 2.0')
        if whole.rank != UNBEKANNT:
            return whole
    try:
        parser = _Parser(_tokens(declared))
        verdict = parser.parse_or()
        if parser.peek() is not None:
            raise ValueError("Rest nach dem Ausdruck")
        return verdict
    except (ValueError, IndexError):
        return Verdict(UNBEKANNT, schlimmster=declared)


# ---------------------------------------------------------------------------
# Prüfung
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    ecosystem: str
    name: str
    version: str
    license: str
    verdict: Verdict
    exception: Ausnahme | None = None
    exception_problem: str = ""

    @property
    def failed(self) -> bool:
        if self.exception_problem:
            return True
        if self.exception is not None:
            return False
        return self.verdict.rank >= UNBEKANNT


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    env_problems: list[str] = field(default_factory=list)
    unused_exceptions: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # auf dieser Plattform nicht installiert (Marker), kein Fehler

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.failed]

    @property
    def notes(self) -> list[Finding]:
        return [f for f in self.findings if not f.failed and f.verdict.hinweise]


def exception_key(ecosystem: str, name: str) -> tuple[str, str]:
    return ecosystem, (tpl.pep503(name) if ecosystem == "python" else name.lower())


def check_packages(packages: list[tpl.Package], exceptions: dict[tuple[str, str], Ausnahme] | None = None) -> Report:
    exceptions = EXCEPTIONS if exceptions is None else exceptions
    report = Report()
    used: set[tuple[str, str]] = set()
    for pkg in packages:
        if pkg.skipped:
            # Z. B. uvloop unter Windows: wird dort nie installiert, die Lizenz prüft der Lauf unter Linux/CI.
            report.skipped.append(f"{pkg.name} {pkg.version}: übersprungen ({pkg.skipped})")
            used.add(exception_key(pkg.ecosystem, pkg.name))
            continue
        for problem in pkg.problems:
            report.env_problems.append(f"[{pkg.ecosystem}] {pkg.name} {pkg.version}: {problem}")
        if not pkg.license and not pkg.version:
            continue  # reiner Problem-Platzhalter (Lockfile-Fehler), oben gemeldet
        if any("nicht installiert" in p for p in pkg.problems):
            continue  # Lizenz nicht lesbar, Umgebungsproblem ist schon gemeldet
        finding = Finding(pkg.ecosystem, pkg.name, pkg.version, pkg.license, evaluate_license(pkg.license))
        key = exception_key(pkg.ecosystem, pkg.name)
        exc = exceptions.get(key)
        if exc is not None:
            used.add(key)
            if exc.version == pkg.version:
                finding.exception = exc
            else:
                finding.exception_problem = (
                    f"Die Ausnahme in scripts/check_licenses.py gilt für Version {exc.version}, "
                    f"eingesetzt wird {pkg.version}."
                )
        report.findings.append(finding)
    report.unused_exceptions = [f"{eco}:{name}" for (eco, name) in sorted(exceptions) if (eco, name) not in used]
    report.findings.sort(key=lambda f: (f.ecosystem, f.name.lower()))
    return report


def collect_packages(repo: Path = REPO_ROOT) -> list[tpl.Package]:
    python_packages = tpl.collect_python(tpl.read_constraints(repo / "deploy" / "constraints.txt"))
    npm_packages = tpl.collect_npm(repo, with_texts=False)
    return python_packages + npm_packages


def violation_message(finding: Finding) -> str:
    """Deutsche Fehlermeldung: Paket, Lizenz, was zu tun ist."""
    where = f"{finding.name} {finding.version} ({'Python' if finding.ecosystem == 'python' else 'npm'})"
    if finding.exception_problem:
        return (
            f"{where}: Lizenz '{finding.license}'. {finding.exception_problem}\n"
            f"    Was tun: Lizenz der neuen Version prüfen. Passt sie weiterhin, die Version im Eintrag "
            f"EXCEPTIONS (scripts/check_licenses.py) anpassen; sonst das Paket ersetzen."
        )
    if finding.verdict.rank == COPYLEFT:
        return (
            f"{where}: steht unter '{finding.license}' (Copyleft: GPL/AGPL/LGPL/SSPL). Das verträgt sich nicht "
            f"mit der Lizenz von Nodvard Deck (PolyForm Noncommercial + kommerziell).\n"
            f"    Was tun: Paket durch ein freizügig lizenziertes ersetzen oder entfernen. Bietet es "
            f"eine freizügige Option an (z. B. 'Apache-2.0 OR GPL'), diese in der Lizenzangabe des Pakets prüfen."
        )
    detail = f" (nicht erkannt: '{finding.verdict.schlimmster}')" if finding.verdict.schlimmster else ""
    return (
        f"{where}: Lizenz '{finding.license or 'keine Angabe'}' ist unbekannt oder nicht erlaubt{detail}.\n"
        f"    Was tun: Lizenz des Pakets nachlesen. Ist sie freizügig (MIT/BSD/Apache/ISC ...) und nur die Metadaten "
        f"unklar, das Paket mit Begründung und Version in EXCEPTIONS (scripts/check_licenses.py) eintragen; "
        f"sonst das Paket ersetzen."
    )


def license_overview(report: Report) -> list[tuple[str, int]]:
    counter = Counter(f.license or "(keine Angabe)" for f in report.findings)
    return sorted(counter.items(), key=lambda item: (-item[1], item[0].lower()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prüft die Lizenzen aller ausgelieferten Abhängigkeiten.")
    parser.add_argument("--liste", action="store_true", help="jedes Paket mit Lizenz und Bewertung auflisten")
    args = parser.parse_args(argv)

    report = check_packages(collect_packages())

    if report.env_problems:
        print("Die Umgebung ist unvollständig, die Prüfung ist nicht aussagekräftig:", file=sys.stderr)
        for problem in report.env_problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Abhilfe: `pip install -c deploy/constraints.txt -e sdk/python -e backend` ausführen "
            "(bzw. das Paket aus constraints.txt/package-lock.json korrigieren).",
            file=sys.stderr,
        )
    for line in report.skipped:
        print(f"Hinweis: {line}")
    if args.liste:
        by_license: dict[str, list[str]] = defaultdict(list)
        for f in report.findings:
            by_license[f.license or "(keine Angabe)"].append(f"{f.name} {f.version}")
        for lic, names in sorted(by_license.items(), key=lambda item: item[0].lower()):
            print(f"{lic}: {', '.join(sorted(names, key=str.lower))}")
        print()

    summary = ", ".join(f"{lic} ({count})" for lic, count in license_overview(report))
    print(f"{len(report.findings)} Pakete geprüft. Lizenzen: {summary}")
    for finding in report.findings:
        if finding.exception is not None:
            print(f"Ausnahme: {finding.name} {finding.version} ({finding.license}) gilt als {finding.exception.bewertet_als}.")
            print(f"    Grund: {finding.exception.grund}")
    for finding in report.notes:
        print(f"Hinweis: {finding.name} {finding.version} steht unter MPL-2.0 -- nur unverändert verwenden (Datei-Copyleft).")
    for unused in report.unused_exceptions:
        print(f"Hinweis: Die Ausnahme für {unused} wird nicht mehr gebraucht -- aus EXCEPTIONS entfernen.")

    if report.violations:
        print(f"\nFEHLER: {len(report.violations)} Lizenzproblem(e):", file=sys.stderr)
        for finding in report.violations:
            print(f"  - {violation_message(finding)}", file=sys.stderr)
        return 1
    if report.env_problems:
        return 2
    print("Lizenzprüfung bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
