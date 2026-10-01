"""Waechter: Der Name "Nexus" ist fuer die Oberflaeche und die Doku vergeben.

Die Sicherheits-Erweiterung heisst "Nodvard Shield", alles mit KI heisst "Nodvard KI". "Nexus" darf nur noch im
Uebergangshinweis zur Umbenennung und im Erkennungsmuster fuer alte KI-Antworten vorkommen. Jede Ausnahme steht in `ALLOWED` als genauer Treffer (Datei-Endung + Teilstring): nur dieser
Teilstring wird aus der Zeile genommen, der Rest wird weiter geprueft ("Der alte Nexus SOC ..." faellt also auf).
Eine Ausnahme, die nirgends mehr greift, laesst den Test scheitern.

Geprueft werden (Schreibweise "Nexus"/"NEXUS", Wortgrenze, jede Zeile): Erweiterungs-Manifeste und
Einstellungsschemas (komplett, also auch Vorgabewerte und Beispiele), Frontend-Quellen samt Vorschau-Testdaten und
HTML-Einstiegsseiten, die gebauten Bundles, Backend-/Erweiterungs-/SDK-Python, Skripte, README, CHANGELOG, Doku,
deploy/README und alle Aenderungsprotokolle (in der App unter "Ueber" sichtbar). Zusaetzlich werden die sichtbaren Textfelder der
Manifeste und Schemas (title, description, name, x-item-title, x-enum-labels, ...) auch klein geschrieben geprueft.

Nicht geprueft: Tests (die pruefen Erkennung/Verhalten und duerfen den alten Namen als Eingabe nutzen, auch
`*.test.ts(x)` im Frontend). Dateien, die nicht veroeffentlicht werden (`export-ignore` in `.gitattributes`),
werden ebenfalls nicht geprueft; die Muster dafuer liest der Test direkt aus `.gitattributes`.
Bekannte Luecke: die klein geschriebene technische Kennung `nexus-soc` (bleibt bis Version 0.7) wird in Quelltexten
nicht geprueft; taucht sie in einem sichtbaren Text auf, faengt das nur die Pruefung der Manifest-/Schemafelder ab.
"""

from __future__ import annotations

import fnmatch
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME_RE = re.compile(r"\b(?:Nexus|NEXUS)\b")
NAME_ANY_CASE_RE = re.compile(r"\bnexus\b", re.IGNORECASE)

# (Pfad-Endung, genauer Teilstring, Begruendung). Nur der Teilstring wird freigegeben, nicht die ganze Zeile.
ALLOWED: list[tuple[str, str, str]] = [
    ("", "bis 0.6 „Nexus SOC“", "Uebergangshinweis zur Umbenennung (Kennung nexus-soc bleibt bis 0.7)"),
    ("parsing.py", "(?:NODVARD|NEXUS)", "alte KI-Antworten mit dem frueheren Marker weiter erkennen"),
]


def _export_ignore_patterns() -> list[str]:
    """Muster aus `.gitattributes`, die das Attribut `export-ignore` tragen (nicht veroeffentlichte Dateien)."""
    attributes = ROOT / ".gitattributes"
    if not attributes.is_file():
        return []
    patterns: list[str] = []
    for line in attributes.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[0].startswith("#") and "export-ignore" in parts[1:]:
            patterns.append(parts[0])
    return patterns


def _is_export_ignored(rel: str, patterns: list[str]) -> bool:
    """fnmatch auf den Repo-relativen Pfad (posix); ein Muster ohne `/` gilt wie bei Git auch fuer den
    Dateinamen bzw. einen gleichnamigen Ordner."""
    name = rel.rsplit("/", 1)[-1]
    for pattern in patterns:
        if fnmatch.fnmatchcase(rel, pattern):
            return True
        if "/" not in pattern and (fnmatch.fnmatchcase(name, pattern) or rel.startswith(pattern + "/")):
            return True
    return False


def _visible_files() -> list[Path]:
    files: list[Path] = []
    for pattern in (
        "extensions/*/frontend/src/**/*.ts*",
        "extensions/*/frontend/dist/*.js",
        "extensions/*/src/**/*.py",
        "extensions/*/extension.toml",
        "extensions/*/settings.schema.json",
        "frontend/src/**/*.ts*",
        "frontend/*.html",
        "backend/src/nodvard_deck/**/*.py",
        "backend/src/nodvard_deck/changelog/unreleased/*.toml",
        "backend/src/nodvard_deck/changelog/versions/*.toml",
        "sdk/python/nodvard_sdk/**/*.py",
        "docs/*.md",
        "docs/en/*.md",
        "scripts/*.py",
    ):
        files += [p for p in ROOT.glob(pattern) if p.is_file()]
    files += [ROOT / n for n in ("README.md", "README.en.md", "CHANGELOG.md", "deploy/README.md")]
    not_published = _export_ignore_patterns()
    return sorted(
        {
            p for p in files
            if p.exists()
            and not _is_export_ignored(p.relative_to(ROOT).as_posix(), not_published)
            and not re.search(r"\.test\.tsx?$", p.name)
            and "__pycache__" not in p.as_posix()
        }
    )


def _strip_allowed(rel: str, line: str, used: set[int]) -> str:
    """Nimmt die erlaubten Teilstrings aus der Zeile; uebrig bleibt, was weiter geprueft wird."""
    for i, (suffix, text, _why) in enumerate(ALLOWED):
        if rel.endswith(suffix) and text in line:
            used.add(i)
            line = line.replace(text, " ")
    return line


def test_kein_nexus_in_sichtbaren_quellen() -> None:
    funde: list[str] = []
    used: set[int] = set()
    for path in _visible_files():
        rel = path.relative_to(ROOT).as_posix()
        for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if NAME_RE.search(_strip_allowed(rel, line, used)):
                funde.append(f"{rel}:{no}: {line.strip()[:120]}")
    assert not funde, "Der Name 'Nexus' ist vergeben (Nodvard Shield / Nodvard KI):\n" + "\n".join(funde)
    tot = [f"{ALLOWED[i][0] or '*'}: {ALLOWED[i][1]}" for i in range(len(ALLOWED)) if i not in used]
    assert not tot, "Ausnahmen, die nirgends mehr greifen (entfernen):\n" + "\n".join(tot)


def test_ausnahmen_geben_nur_ihren_teilstring_frei() -> None:
    """Die Ausnahmen nehmen nur ihren Teilstring aus der Zeile, nicht die Zeile, in der sie stehen."""
    used: set[int] = set()
    for line in (
        "Der alte Nexus SOC prüft alles.",
        "bis 0.6 „Nexus SOC“ (Nexus Deck)",
        "Nexus SOC und bis 0.6 „Nexus SOC“",
        "bis 0.6 „Nexus SOC“ und Nexus Deck",
    ):
        assert NAME_RE.search(_strip_allowed("frontend/src/x.tsx", line, used)), line
    assert not NAME_RE.search(_strip_allowed("frontend/src/x.tsx", "Shield (bis 0.6 „Nexus SOC“)", used))


# Schluessel, deren Text in der Oberflaeche steht.
_VISIBLE_KEYS = ("title", "description", "name", "x-item-title", "x-empty-label", "x-pattern-message", "placeholder")


def _walk_texts(node: object, path: str = ""):
    """Sichtbare Texte aus einem Schema, auch verschachtelt (x-enum-labels, Eintraege usw.)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _VISIBLE_KEYS and isinstance(value, str):
                yield f"{path}/{key}", value
            elif key == "x-enum-labels" and isinstance(value, dict):
                for k, v in value.items():
                    if isinstance(v, str):
                        yield f"{path}/{key}/{k}", v
            else:
                yield from _walk_texts(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_texts(value, f"{path}/{i}")


def test_kein_nexus_in_sichtbaren_feldern_von_manifesten_und_schemas() -> None:
    """Auch klein geschrieben (z. B. "KI-Vorfallserkennung (nexus-soc)") -- die Kennung bleibt technisch."""
    funde: list[str] = []
    for manifest in sorted(ROOT.glob("extensions/*/extension.toml")):
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        for where, text in _walk_texts(data.get("extension", data)):
            if NAME_ANY_CASE_RE.search(text):
                funde.append(f"{manifest.relative_to(ROOT)}{where}: {text[:80]}")
    for schema in sorted(ROOT.glob("extensions/*/settings.schema.json")):
        for where, text in _walk_texts(json.loads(schema.read_text(encoding="utf-8"))):
            if NAME_ANY_CASE_RE.search(text):
                funde.append(f"{schema.relative_to(ROOT)}{where}: {text[:80]}")
    assert not funde, "Sichtbarer Text mit 'Nexus':\n" + "\n".join(funde)


def test_shield_heisst_nodvard_shield() -> None:
    data = tomllib.loads((ROOT / "extensions/nexus-soc/extension.toml").read_text(encoding="utf-8"))
    assert data.get("extension", data)["name"] == "Nodvard Shield"
