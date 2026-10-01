#!/usr/bin/env python3
"""Erzeugt `THIRD_PARTY_LICENSES` im Repo-Root: je ausgeliefertem Fremdpaket Name,
Version, Lizenz und den vollstaendigen Lizenztext.

Was einbezogen wird
-------------------
* Python: genau die Pakete aus `deploy/constraints.txt` (das sind die Pakete des
  Produktions-Images), gelesen aus den INSTALLIERTEN Metadaten (`importlib.metadata`:
  License-Expression / License / Classifier plus die Lizenzdateien in der dist-info).
  Ein Paket aus den constraints, das in der laufenden Umgebung fehlt oder in einer
  anderen Version installiert ist, wird laut gemeldet (Exit-Code 2) -- es wird nie
  still ausgelassen und nie "irgendeine" Version beschrieben.
  Einzige Ausnahme: Pakete, die auf dieser Plattform gar nicht installiert WUERDEN
  (z. B. uvloop unter Windows: `uvicorn[standard]` verlangt es nur mit dem Marker
  `sys_platform != "win32"`). Das erkennt `platform_skip_reason` an den Requires-Dist-
  Markern der installierten Pakete; solche Pakete gelten als "uebersprungen", nicht als
  fehlend. Weil die Datei dann nicht vollstaendig erzeugt werden kann, schreibt/druckt
  das Skript dort nichts (Exit-Code 2); `--check` prueft nur noch die Paketliste.
  Bringt ein Wheel eine SBOM mit (`*.dist-info/sboms/*.json`, CycloneDX), werden die darin
  genannten einkompilierten Bestandteile (Rust-Crates, statisch gelinktes OpenSSL ...) mit
  Name, Version, Lizenz und Urheber angehaengt, samt Lizenztext, wenn die SBOM einen hat
  (`_python_sbom_texts`, Ausnahmen in `_SBOM_SKIP`).
* npm: die Production-Dependencies des Frontends samt allen transitiven
  Abhaengigkeiten (aus `frontend/package-lock.json`, keine devDependencies), dazu
  alles, was in die Extension-Bundles (`extensions/*/frontend/dist/index.js`)
  einkompiliert ist oder von ihnen importiert wird, und Build-Werkzeuge, deren Code im
  Ergebnis landet (`_NPM_BUILD_OUTPUT`, ohne deren Abhaengigkeiten). Lizenztexte kommen aus
  `frontend/node_modules/<paket>/`: die Lizenzdateien im Paketordner, die in Unterordnern
  (mitgelieferter Fremdcode, z. B. noVNC `vendor/pako/LICENSE`) und die Lizenzkoepfe der
  Dateien aus `_NPM_FILE_NOTICES` (im verkleinerten Bundle fehlen diese Kommentare).

Lizenzdateien, die kein gueltiges UTF-8 sind, werden als Latin-1 gelesen (`_read_text`).

Die Ausgabe ist deterministisch (nach Oekosystem und Name sortiert, Zeilenenden und
Leerraum normalisiert, keine Zeitstempel).

Benutzung (Repo-Root)
---------------------
    python scripts/third_party_licenses.py            # schreibt THIRD_PARTY_LICENSES
    python scripts/third_party_licenses.py --check    # prueft, ob die Datei aktuell ist
    python scripts/third_party_licenses.py --print    # nur auf stdout

Exit-Code 0 = ok, 1 = Datei veraltet (nur --check), 2 = Umgebung unvollstaendig
(Paket fehlt, falsche Version, node_modules fehlt, Lizenztext nicht gefunden) bzw. Datei
wegen plattformfremder Pakete hier nicht erzeugbar.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import importlib.metadata as importlib_metadata
import json
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement

REPO_ROOT = Path(__file__).resolve().parent.parent
CONSTRAINTS = REPO_ROOT / "deploy" / "constraints.txt"
FRONTEND = REPO_ROOT / "frontend"
LOCKFILE = FRONTEND / "package-lock.json"
OUTPUT = REPO_ROOT / "THIRD_PARTY_LICENSES"

# Lizenzdateien im Paketverzeichnis (npm) bzw. in der dist-info (Python).
_LICENSE_FILE_RE = re.compile(r"^(licen[cs]e|licen[cs]es|copying|copyright|notice|unlicense)([.\-_].*)?$", re.IGNORECASE)
# Laenger als das ist es keine Lizenzangabe, sondern ein eingebetteter Lizenztext.
_MAX_LICENSE_FIELD = 120

# Plattformabhaengige Unterordner in Wheels (pypdfium2: licenses/data/linux_x64/...):
# fuer die Beschriftung vereinheitlichen, damit die Ausgabe auf x86_64 und arm64 gleich ist.
_PLATFORM_DIR_RE = re.compile(r"(^|/)data/[^/]+/")


@dataclass
class Package:
    ecosystem: str  # "python" | "npm"
    name: str
    version: str
    license: str  # so, wie das Paket es selbst angibt
    license_source: str  # woher die Angabe stammt (nur fuer Fehlermeldungen)
    texts: list[tuple[str, str]] = field(default_factory=list)  # (Beschriftung, Text)
    source_url: str = ""
    download_url: str = ""  # genau diese Version im Paketregister (npm: "resolved" aus dem Lockfile)
    note: str = ""  # warum das Paket hier steht, wenn das nicht offensichtlich ist (Build-Werkzeug)
    problems: list[str] = field(default_factory=list)  # Umgebung passt nicht (fehlt, andere Version)
    text_problems: list[str] = field(default_factory=list)  # Lizenztext nicht auffindbar
    skipped: str = ""  # Grund, warum das Paket auf dieser Plattform nicht installiert wird (dann kein Problem)


# ---------------------------------------------------------------------------
# Gemeinsame Helfer
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Zeilenenden vereinheitlichen, Leerraum am Zeilenende und um den Text entfernen --
    sonst waere die Datei je nach Checkout (CRLF) und Paketstand verschieden."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def decode_text(raw: bytes) -> str:
    """UTF-8, sonst Latin-1. Aeltere Lizenzdateien sind oft Latin-1 (pypdfium2:
    FreeType-Zeile "copyright \\xa9 <year>"); mit `errors="replace"` stuende dort ein
    Ersatzzeichen statt des (c)-Zeichens. Latin-1 kann jedes Byte lesen, scheitert also nie."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _read_text(path: Path) -> str:
    return normalize_text(decode_text(path.read_bytes()))


def _dedupe(files: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Gleiche Texte (z. B. dieselbe Lizenz je Plattformordner) nur einmal."""
    seen: set[str] = set()
    out = []
    for label, text in sorted(files, key=lambda item: (item[0].lower(), item[0])):
        if text and text not in seen:
            seen.add(text)
            out.append((label, text))
    return out


def pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def read_constraints(path: Path = CONSTRAINTS) -> list[tuple[str, str]]:
    """[(Name wie in der Datei geschrieben, Version)] -- Kommentare/Leerzeilen weg."""
    pins = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "==" not in line:
            raise ValueError(f"{path.name}: '{line}' ist nicht auf eine feste Version gepinnt (name==version).")
        name, version = line.split("==", 1)
        pins.append((name.strip(), version.strip()))
    return sorted(pins, key=lambda pin: pep503(pin[0]))


def python_declared_license(meta) -> tuple[str, str]:
    """(Lizenzangabe, Quelle). Reihenfolge: License-Expression (SPDX), dann das kurze
    License-Feld, dann die Trove-Classifier (mehrere gelten als UND: alle muessen erlaubt sein)."""
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression, "License-Expression"
    legacy = (meta.get("License") or "").strip()
    if legacy and "\n" not in legacy and len(legacy) <= _MAX_LICENSE_FIELD and legacy.upper() != "UNKNOWN":
        return legacy, "License"
    classifiers = []
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            last = classifier.split("::")[-1].strip()
            if last != "OSI Approved":  # Sammelklasse ohne Aussage
                classifiers.append(last)
    if classifiers:
        return " AND ".join(classifiers), "Classifier"
    return "", "keine Angabe"


def _python_license_files(dist) -> list[tuple[str, str]]:
    files = []
    seen_paths: set[str] = set()
    for entry in dist.files or []:
        parts = entry.parts
        dist_info_index = next((i for i, part in enumerate(parts) if part.endswith(".dist-info")), None)
        if dist_info_index is None:
            continue
        inside = parts[dist_info_index + 1 :]
        if not inside:
            continue
        in_licenses_dir = inside[0] in ("licenses", "license", "LICENSES")
        top_level_match = len(inside) == 1 and _LICENSE_FILE_RE.match(inside[0])
        if not (in_licenses_dir or top_level_match):
            continue
        full = Path(dist.locate_file(entry))
        if not full.is_file() or str(full) in seen_paths:
            continue
        seen_paths.add(str(full))
        label = "/".join(inside[1:] if in_licenses_dir and len(inside) > 1 else inside)
        label = _PLATFORM_DIR_RE.sub(r"\1data/<plattform>/", label)
        files.append((label, _read_text(full)))
    return _dedupe(files)


# SBOMs, die NICHT beschreiben, was im Wheel steckt -- mit Begruendung, Schluessel: Paketname nach PEP 503.
_SBOM_SKIP: dict[str, str] = {
    "pillow": (
        "Pillows eigene SBOM beschreibt die Quell-Optionen, auch nicht gebuendelte (libimagequant, FriBiDi), "
        "die von auditwheel nur eine der gebuendelten Bibliotheken. Die Lizenztexte aller im Wheel gebuendelten "
        "Bibliotheken stehen schon in Pillows LICENSE (dist-info/licenses/LICENSE)."
    ),
}

# Fehlt einer Komponente in der SBOM die Lizenzangabe, aber sie ist eindeutig bekannt.
# (Name klein, ab welcher Hauptversion) -> (Lizenz, Begruendung, die mit ausgegeben wird)
_SBOM_KNOWN_LICENSES: dict[str, tuple[int, str, str]] = {
    "openssl": (3, "Apache-2.0", "OpenSSL ab Version 3.0; die SBOM nennt keine Lizenz"),
}

_SBOM_NOTE = (
    "Diese Bestandteile sind laut der SBOM, die das Wheel mitbringt (CycloneDX), in das Wheel\n"
    "einkompiliert (bei Rust-Erweiterungen die Crates; die Liste kann auch Crates enthalten, die nur\n"
    "beim Bauen oder nur für andere Betriebssysteme gebraucht werden). Name, Version, Lizenz und\n"
    "Urheber stammen aus ihr.\n"
    "Die vollständigen Texte der genannten Standardlizenzen stehen unter https://spdx.org/licenses/\n"
    "(z. B. https://spdx.org/licenses/MIT.html); bei \"A OR B\" gilt eine der Lizenzen nach Wahl."
)


def _version_major(version: str) -> int:
    match = re.match(r"\d+", version or "")
    return int(match.group(0)) if match else -1


def _sbom_license(component: dict) -> tuple[str, list[str]]:
    """(Lizenzangabe als ein Ausdruck, [Lizenztexte aus der SBOM]). Mehrere Eintraege gelten
    gemeinsam (UND), so meint es CycloneDX (Pillow: libjpeg-turbo = IJG und BSD-3-Clause)."""
    parts: list[str] = []
    texts: list[str] = []
    for entry in component.get("licenses") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("expression"):
            parts.append(str(entry["expression"]).strip())
            continue
        lic = entry.get("license") or {}
        name = str(lic.get("id") or lic.get("name") or "").strip()
        text = lic.get("text") or {}
        content = text.get("content") if isinstance(text, dict) else None
        if content:
            raw = content.encode("utf-8")
            if isinstance(text, dict) and str(text.get("encoding", "")).lower() == "base64":
                try:
                    raw = base64.b64decode(content, validate=True)
                except (binascii.Error, ValueError) as exc:
                    # Nicht still den Base64-Text als "Lizenztext" ausgeben: laut melden (_python_sbom_texts).
                    raise ValueError(f"Lizenztext von {component.get('name', '?')} ist kein gültiges Base64") from exc
            texts.append(normalize_text(decode_text(raw)))
            if not name or name.lower() == "unknown":
                name = "Lizenztext siehe unten"
        if name:
            parts.append(name)
    if len(parts) > 1:
        parts = [f"({part})" if re.search(r"\s(OR|AND)\s", part) else part for part in parts]
    return " AND ".join(parts), texts


def _sbom_author(component: dict) -> str:
    """Urheber laut SBOM, ohne E-Mail-Adressen (die braucht ein Lizenzhinweis nicht)."""
    author = component.get("author") or ""
    if not author and isinstance(component.get("authors"), list):
        author = ", ".join(str(a.get("name", "")) for a in component["authors"] if isinstance(a, dict))
    author = re.sub(r"\s*<[^>]*>", "", str(author))
    return re.sub(r"\s+", " ", author).strip(" ,")


def _sbom_distribution_url(component: dict) -> str:
    for ref in component.get("externalReferences") or []:
        if isinstance(ref, dict) and ref.get("type") == "distribution" and ref.get("url"):
            return str(ref["url"])
    return ""


def sbom_texts(sbom: dict, label: str) -> list[tuple[str, str]]:
    """Ein SBOM-Dokument als Textbloecke: die Liste der Bestandteile, danach je Bestandteil mit
    eigenem Lizenztext in der SBOM dieser Text. Deterministisch (sortiert, ohne Zeitstempel,
    Build-Pfade und Architektur -- die stehen nur in Feldern, die hier nicht ausgegeben werden)."""
    rows: dict[tuple[str, str], tuple[str, str]] = {}
    extra: dict[tuple[str, str], list[str]] = {}
    for component in sbom.get("components") or []:
        if not isinstance(component, dict) or not component.get("name"):
            continue
        name, version = str(component["name"]).strip(), str(component.get("version") or "").strip()
        license_, texts = _sbom_license(component)
        if not license_:
            known = _SBOM_KNOWN_LICENSES.get(name.lower())
            if known and _version_major(version) >= known[0]:
                license_ = f"{known[1]} ({known[2]})"
            else:
                license_ = "(keine Angabe in der SBOM)"
        details = []
        author = _sbom_author(component)
        if author:
            details.append(f"Urheber: {author}")
        url = _sbom_distribution_url(component)
        if url:
            details.append(f"Quelle: {url}")
        rows[(name, version)] = (license_, "; ".join(details))
        if texts:
            extra[(name, version)] = texts
    if not rows:
        return []
    keys = sorted(rows, key=lambda key: (key[0].lower(), key[0], key[1]))
    lines = [_SBOM_NOTE, ""]
    for name, version in keys:
        license_, details = rows[(name, version)]
        line = f"  {(name + ' ' + version).strip():<34} {license_}"
        lines.append(f"{line}  ({details})" if details else line)
    count = "1 einkompilierter Bestandteil" if len(keys) == 1 else f"{len(keys)} einkompilierte Bestandteile"
    out = [(f"{label}: {count}", "\n".join(lines))]
    for key in keys:
        for text in extra.get(key, []):
            out.append((f"Lizenztext aus {label}: {key[0]} {key[1]}".rstrip(), text))
    return out


def _python_sbom_texts(dist, package_name: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(Textbloecke, Probleme) fuer alle SBOMs in der dist-info des Pakets."""
    if pep503(package_name) in _SBOM_SKIP:
        return [], []
    found: list[tuple[str, Path]] = []
    for entry in dist.files or []:
        parts = entry.parts
        dist_info_index = next((i for i, part in enumerate(parts) if part.endswith(".dist-info")), None)
        if dist_info_index is None:
            continue
        inside = parts[dist_info_index + 1 :]
        if len(inside) < 2 or inside[0] != "sboms" or not inside[-1].lower().endswith(".json"):
            continue
        full = Path(dist.locate_file(entry))
        if full.is_file():
            found.append(("dist-info/" + "/".join(inside), full))
    texts: list[tuple[str, str]] = []
    problems: list[str] = []
    for label, full in sorted(found):
        try:
            sbom = json.loads(decode_text(full.read_bytes()))
        except (OSError, ValueError) as exc:
            problems.append(f"SBOM {label} nicht lesbar: {exc}")
            continue
        if not isinstance(sbom, dict) or sbom.get("bomFormat") != "CycloneDX":
            problems.append(f"SBOM {label} ist kein CycloneDX-Dokument")
            continue
        try:
            texts += sbom_texts(sbom, label)
        except ValueError as exc:
            problems.append(f"SBOM {label}: {exc}")
    return texts, problems


def _python_source_url(meta) -> str:
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in ("source", "source code", "repository", "homepage", "home"):
            return url.strip()
    return (meta.get("Home-page") or "").strip()


def platform_skip_reason(name: str, environment: dict[str, str] | None = None) -> str:
    """Nicht-leerer Text, wenn `name` auf dieser Plattform gar nicht installiert wuerde.

    Begruendung der Loesung: Eine feste Liste "plattformgebundener Pakete" wuerde veralten und
    koennte ein wirklich fehlendes Paket verdecken. Stattdessen wird gefragt, ob irgendein
    installiertes Paket `name` per `Requires-Dist` verlangt -- und ob JEDE dieser Anforderungen
    hier zu False ausgewertet wird (Marker wie `sys_platform != "win32"`). Ein Paket, das niemand
    verlangt oder dessen Marker hier zutrifft, bleibt "fehlt" (Umgebung kaputt).

    Extras: `uvloop ; extra == "standard" and sys_platform != "win32"` haengt an einem Extra,
    das wir nicht kennen. Darum wird der Marker fuer jedes Extra des verlangenden Pakets (und
    ohne Extra) ausgewertet; trifft er in einem Fall zu, gilt das Paket als verlangt.
    `environment` ueberschreibt die Umgebungswerte (Tests: {"sys_platform": "win32"}).
    """
    target = pep503(name)
    base = {**default_environment(), **(environment or {})}
    reasons: list[str] = []
    for dist in importlib_metadata.distributions():
        extras = [""] + list(dist.metadata.get_all("Provides-Extra") or [])
        for raw in dist.requires or []:
            try:
                req = Requirement(raw)
            except InvalidRequirement:
                continue
            if pep503(req.name) != target:
                continue
            if req.marker is None:
                return ""
            if any(req.marker.evaluate({**base, "extra": extra}) for extra in extras):
                return ""
            reasons.append(f"{dist.metadata['Name']}: {req.marker}")
    if not reasons:
        return ""
    return "nicht für diese Plattform: " + "; ".join(sorted(set(reasons)))


def collect_python(pins: list[tuple[str, str]], environment: dict[str, str] | None = None) -> list[Package]:
    packages = []
    for name, version in pins:
        pkg = Package("python", name, version, "", "")
        try:
            dist = importlib_metadata.distribution(name)
        except importlib_metadata.PackageNotFoundError:
            pkg.skipped = platform_skip_reason(name, environment)
            if pkg.skipped:
                packages.append(pkg)
                continue
            pkg.problems.append(f"nicht installiert (constraints verlangen {version})")
            packages.append(pkg)
            continue
        if dist.version != version:
            pkg.problems.append(f"installiert ist {dist.version}, die constraints verlangen {version}")
        meta = dist.metadata
        pkg.license, pkg.license_source = python_declared_license(meta)
        pkg.source_url = _python_source_url(meta)
        pkg.texts = _python_license_files(dist)
        if not pkg.texts:
            embedded = (meta.get("License") or "").strip()
            if embedded and len(embedded) > _MAX_LICENSE_FIELD:
                pkg.texts = [("License (Metadaten)", normalize_text(embedded))]
        if not pkg.texts:
            pkg.text_problems.append("keine Lizenzdatei in der dist-info gefunden")
        sbom_blocks, sbom_problems = _python_sbom_texts(dist, name)
        pkg.texts += sbom_blocks
        pkg.text_problems += sbom_problems
        packages.append(pkg)
    return packages


# ---------------------------------------------------------------------------
# npm
# ---------------------------------------------------------------------------

# Manche npm-Pakete nennen ihre Lizenz nur in package.json/README und liefern keine
# Lizenzdatei mit (z. B. react-remove-scroll-bar). Fuer den haeufigsten Fall (MIT) wird
# dann der Standardtext ergaenzt und als solcher gekennzeichnet; fuer jede andere
# Lizenz bleibt es ein Problem, das der Mensch klaeren muss.
_MIT_STANDARD_TEXT = """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def _standard_text_fallback(license_id: str, manifest: dict) -> list[tuple[str, str]]:
    if license_id != "MIT":
        return []
    author = manifest.get("author")
    author = author.get("name") if isinstance(author, dict) else author
    author = re.sub(r"\s*[<(].*$", "", author or "").strip()
    holder = f"Urheber laut package.json: {author}" if author else "Urheber nicht angegeben"
    return [(f"Standardtext, das Paket liefert keine Lizenzdatei ({holder})", _MIT_STANDARD_TEXT)]


def load_lock(path: Path = LOCKFILE) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))["packages"]


def _resolve(packages: dict[str, dict], from_path: str, dep: str) -> str | None:
    """Wie Node: erst <paket>/node_modules/<dep>, dann jeweils eine Ebene hoeher."""
    base = from_path
    while True:
        candidate = f"{base}/node_modules/{dep}" if base else f"node_modules/{dep}"
        if candidate in packages:
            return candidate
        if not base:
            return None
        cut = base.rfind("/node_modules/")
        base = base[:cut] if cut >= 0 else ""


def _package_name(lock_path: str) -> str:
    return lock_path.rsplit("node_modules/", 1)[1]


def bundle_npm_roots(repo: Path = REPO_ROOT) -> set[str]:
    """npm-Pakete, die die Extension-Bundles einkompilieren (esbuild schreibt
    `// .../node_modules/<paket>/...` als Quellkommentar) oder als bare Import erwarten
    (react ... kommt vom Host)."""
    names: set[str] = set()
    for bundle in sorted((repo / "extensions").glob("*/frontend/dist/index.js")):
        source = bundle.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"^// .*?node_modules/((?:@[^/\s]+/)?[^/\s]+)/", source, re.MULTILINE):
            names.add(match.group(1))
        for match in re.finditer(r"""^(?:import|export)\b[^;'"]*?from\s*["']([^"']+)["']""", source, re.MULTILINE):
            spec = match.group(1)
            if spec.startswith((".", "/", "node:")):
                continue
            parts = spec.split("/")
            names.add("/".join(parts[:2]) if spec.startswith("@") else parts[0])
    return names


def npm_closure(packages: dict[str, dict], root_names: set[str]) -> tuple[list[str], list[str]]:
    """(Lock-Pfade aller ausgelieferten Pakete sortiert, Probleme)."""
    problems: list[str] = []
    todo: list[str] = []
    for name in sorted(root_names):
        path = _resolve(packages, "", name)
        if path is None:
            problems.append(f"{name}: steht nicht in package-lock.json")
        else:
            todo.append(path)
    seen: set[str] = set()
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        entry = packages[path]
        for kind in ("dependencies", "optionalDependencies", "peerDependencies"):
            for dep in entry.get(kind, {}) or {}:
                target = _resolve(packages, path, dep)
                if target is None:
                    if kind == "dependencies":
                        problems.append(f"{_package_name(path)} -> {dep}: steht nicht in package-lock.json")
                    continue
                todo.append(target)
    return sorted(seen, key=lambda p: (_package_name(p).lower(), p)), problems


def npm_root_names(repo: Path = REPO_ROOT) -> set[str]:
    frontend_pkg = json.loads((repo / "frontend" / "package.json").read_text(encoding="utf-8"))
    return set(frontend_pkg.get("dependencies", {})) | bundle_npm_roots(repo)


# Build-Werkzeuge (devDependencies), deren eigener Code im ausgelieferten Frontend landet. Nur das
# Paket selbst, NICHT seine Abhaengigkeiten (die laufen nur beim Bauen). Wert: was davon im Bundle steckt.
_NPM_BUILD_OUTPUT: dict[str, str] = {
    "tailwindcss": (
        "Build-Werkzeug. Im ausgelieferten CSS stecken seine Grundstile (Preflight, `@tailwind base`, "
        "beruht auf modern-normalize: Lizenz unter lib/css/LICENSE); den Lizenzkommentar, den Tailwind "
        "dazuschreibt, entfernt das Verkleinern. Seine eigenen Abhängigkeiten werden nicht mitgeliefert."
    ),
}

# Dateien mit EIGENEM Lizenzkopf (fremder Code im Paket oder weitere Urheber), die im Bundle landen.
# Das verkleinerte Bundle enthaelt keine Kommentare mehr, deshalb stehen die Koepfe hier. Ermittelt mit
# einem Build mit Quellkarten (`npx vite build --sourcemap`, Liste `sources`): genau diese Dateien
# werden gebuendelt. Fehlt eine Datei nach einem Update, meldet das Skript ein Problem.
_NPM_FILE_NOTICES: dict[str, tuple[str, ...]] = {
    # DES fuer die VNC-Anmeldung, aus Acme.Crypto/Flashlight-VNC uebernommen (BSD-artige Lizenzen).
    "@novnc/novnc": ("core/crypto/des.js", "core/decoders/tight.js"),
    # Teile aus jslinux (Fabrice Bellard) und aus VS Code (Microsoft, MIT).
    "@xterm/xterm": ("lib/xterm.mjs",),
    "@xterm/addon-fit": ("lib/addon-fit.mjs",),
    # Liefert keine Lizenzdatei; der Kopf nennt Urheber und Lizenz (und den Markenhinweis "QR Code").
    "qrcode-generator": ("dist/qrcode.mjs",),
    # Enthaelt einen Ausschnitt aus Modernizr (MIT).
    "react-dom": ("cjs/react-dom.production.min.js",),
}

# Diese Endungen sind Code, keine Lizenzdateien (lucide-react hat ein Symbol `icons/copyright.mjs`).
_CODE_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx", ".map", ".json", ".css", ".scss")


def leading_comments(source: str) -> str:
    """Die Kommentare am Dateianfang bis zur ersten Leerzeile nach einem fertigen Kommentar oder
    bis zum ersten Code -- dort steht bei JavaScript-Dateien der Lizenzkopf. Leer, wenn die Datei
    nicht mit einem Kommentar beginnt."""
    out: list[str] = []
    in_block = False
    for line in source.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if in_block:
            out.append(line)
            in_block = "*/" not in stripped
            continue
        if not stripped:
            if out:
                break
            continue
        if stripped.startswith("//"):
            out.append(line)
        elif stripped.startswith("/*"):
            out.append(line)
            in_block = "*/" not in stripped[2:]
        else:
            break
    return normalize_text("\n".join(out))


def _npm_nested_license_files(installed_dir: Path) -> list[tuple[str, str]]:
    """Lizenzdateien in Unterordnern des Pakets (mitgelieferter Fremdcode wie noVNC `vendor/pako/`,
    weitere Lizenztexte wie noVNC `docs/LICENSE.*`). Verschachtelte node_modules sind eigene Pakete."""
    files = []
    for child in sorted(installed_dir.rglob("*")):
        rel = child.relative_to(installed_dir)
        if len(rel.parts) < 2 or "node_modules" in rel.parts or not child.is_file():
            continue
        if _LICENSE_FILE_RE.match(child.name) and not child.name.lower().endswith(_CODE_SUFFIXES):
            files.append((rel.as_posix(), _read_text(child)))
    return _dedupe(files)


def _npm_file_notices(name: str, installed_dir: Path) -> tuple[list[tuple[str, str]], list[str]]:
    texts, problems = [], []
    for rel in _NPM_FILE_NOTICES.get(name, ()):
        path = installed_dir / rel
        if not path.is_file():
            problems.append(f"{rel} fehlt (Liste _NPM_FILE_NOTICES in scripts/third_party_licenses.py prüfen)")
            continue
        header = leading_comments(decode_text(path.read_bytes()))
        if not header:
            problems.append(f"{rel} beginnt nicht mit einem Lizenzkopf (Liste _NPM_FILE_NOTICES prüfen)")
            continue
        texts.append((f"Lizenzkopf aus {rel}", header))
    return texts, problems


def _npm_license_from_package_json(data: dict) -> str:
    value = data.get("license")
    if isinstance(value, dict):
        value = value.get("type")
    if not value and isinstance(data.get("licenses"), list):
        value = " OR ".join(item.get("type", "") if isinstance(item, dict) else str(item) for item in data["licenses"])
    return (value or "").strip()


def _npm_repo_url(data: dict) -> str:
    repo = data.get("repository")
    url = repo.get("url") if isinstance(repo, dict) else repo
    url = (url or data.get("homepage") or "").strip()
    return re.sub(r"^git\+", "", url).removesuffix(".git")


def collect_npm(repo: Path = REPO_ROOT, node_modules: Path | None = None, with_texts: bool = True) -> list[Package]:
    """Die ausgelieferten npm-Pakete. Name, Version und Lizenzangabe kommen aus dem
    Lockfile (umgebungsunabhaengig); die Lizenztexte aus node_modules (nur `with_texts`)."""
    node_modules = node_modules or (repo / "frontend" / "node_modules")
    lock = load_lock(repo / "frontend" / "package-lock.json")
    paths, problems = npm_closure(lock, npm_root_names(repo))
    for name in sorted(_NPM_BUILD_OUTPUT):
        path = _resolve(lock, "", name)
        if path is None:
            problems.append(f"{name}: steht nicht in package-lock.json (Liste _NPM_BUILD_OUTPUT prüfen)")
        elif path not in paths:
            paths.append(path)
    paths.sort(key=lambda p: (_package_name(p).lower(), p))
    packages = []
    for problem in problems:
        packages.append(Package("npm", "(Lockfile)", "", "", "", problems=[problem]))
    for lock_path in paths:
        entry = lock[lock_path]
        name = _package_name(lock_path)
        pkg = Package("npm", name, entry.get("version", ""), (entry.get("license") or "").strip(), "package-lock.json")
        pkg.download_url = (entry.get("resolved") or "").strip()
        if lock_path == f"node_modules/{name}" and name in _NPM_BUILD_OUTPUT:
            pkg.note = _NPM_BUILD_OUTPUT[name]
        installed_dir = node_modules / lock_path.removeprefix("node_modules/")
        manifest_file = installed_dir / "package.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.is_file() else {}
        if manifest:
            pkg.source_url = _npm_repo_url(manifest)
            if not pkg.license:
                pkg.license, pkg.license_source = _npm_license_from_package_json(manifest), "package.json"
            if manifest.get("version") and manifest["version"] != pkg.version:
                pkg.problems.append(f"installiert ist {manifest['version']}, package-lock.json verlangt {pkg.version}")
        elif with_texts:
            pkg.problems.append(f"nicht in {node_modules} installiert ({lock_path}); `npm ci` ausführen")
        if with_texts and installed_dir.is_dir():
            files = [
                (child.name, _read_text(child))
                for child in installed_dir.iterdir()
                if child.is_file() and _LICENSE_FILE_RE.match(child.name)
            ]
            pkg.texts = _dedupe(files) or _standard_text_fallback(pkg.license, manifest)
            if not pkg.texts:
                pkg.text_problems.append("keine Lizenzdatei im Paketordner gefunden")
            notices, notice_problems = _npm_file_notices(name, installed_dir)
            seen = {text for _, text in pkg.texts}
            for label, text in _npm_nested_license_files(installed_dir) + notices:
                if text not in seen:
                    seen.add(text)
                    pkg.texts.append((label, text))
            pkg.text_problems += notice_problems
        if not pkg.license:
            pkg.license_source = "keine Angabe"
        packages.append(pkg)
    return packages


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------

_RULE = "=" * 78
_THIN = "-" * 78

_MPL_NOTE = (
    "Diese Komponenten stehen unter der Mozilla Public License 2.0 (Datei-Copyleft). Sie werden\n"
    "UNVERÄNDERT aus dem Paketregister übernommen (npm: Prüfsumme in frontend/package-lock.json,\n"
    "Python: Version in deploy/constraints.txt). Den Quelltext gibt es unter den folgenden Adressen\n"
    "und über das jeweilige Paketregister (npm bzw. PyPI) in genau dieser Version."
)

# Je MPL-Komponente: Adresse des Quelltexts GENAU dieser Version (`{version}` wird ersetzt, leer =
# keine bekannt) und wo die Dateien im Image stecken. MPL-2.0 Abschnitt 3.2: wer die ausfuehrbare
# Form weitergibt (hier: gebuendelt und verkleinert), muss sagen, wie man an den Quelltext kommt.
_MPL_DETAILS: dict[tuple[str, str], tuple[str, str]] = {
    ("npm", "@novnc/novnc"): (
        "https://github.com/novnc/noVNC/tree/v{version}",
        (
            "Frontend-Bundle /app/frontend/dist/assets/rfb-*.js (die grafische Konsole). Darin stecken die "
            "Dateien aus core/ und vendor/pako/ des Pakets: unverändert, nur gebündelt und verkleinert."
        ),
    ),
    ("python", "certifi"): (
        "",
        "Python-Paket unter site-packages/certifi, unverändert.",
    ),
}


def _is_mpl(pkg: Package) -> bool:
    return bool(re.search(r"\bMPL\b|MPL-2|Mozilla Public", pkg.license, re.IGNORECASE))


def _wrap(text: str, indent: str, width: int = 100) -> list[str]:
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent, break_on_hyphens=False)


def mpl_lines(pkg: Package) -> list[str]:
    """Hinweiszeilen zu einer MPL-Komponente: Projekt, Quelltext und Paket genau dieser Version, Ort im Image."""
    lines = [f"  {pkg.name} {pkg.version} ({pkg.ecosystem}): {pkg.source_url or 'Quelle über das Paketregister'}"]
    tag_url, where = _MPL_DETAILS.get((pkg.ecosystem, pkg.name.lower() if pkg.ecosystem == "npm" else pep503(pkg.name)), ("", ""))
    if tag_url:
        lines.append(f"      Quelltext genau dieser Version: {tag_url.format(version=pkg.version)}")
    download = pkg.download_url or (f"https://pypi.org/project/{pkg.name}/{pkg.version}/" if pkg.ecosystem == "python" else "")
    if download:
        lines.append(f"      Paket genau dieser Version: {download}")
    if where:
        lines += _wrap(f"Im Image: {where}", "      ")
    return lines


def sorted_packages(packages: list[Package]) -> list[Package]:
    return sorted(packages, key=lambda p: (p.ecosystem, p.name.lower(), p.version))


def render(python_packages: list[Package], npm_packages: list[Package]) -> str:
    python_sorted = sorted_packages(python_packages)
    npm_sorted = sorted_packages(npm_packages)
    out: list[str] = []
    out += [
        "THIRD_PARTY_LICENSES",
        _RULE,
        "",
        "Nodvard Deck steht unter der PolyForm Noncommercial License 1.0.0 (siehe LICENSE).",
        "Diese Datei nennt die Fremdkomponenten, die im Docker-Image bzw. im ausgelieferten",
        "Frontend stecken, mit Version, Lizenz und vollem Lizenztext. Bei den Paketen",
        "steht außerdem, was sie selbst mitbringen: einkompilierte Bestandteile laut SBOM",
        "(z. B. Rust-Crates), mitgelieferter Fremdcode und die Lizenzköpfe einzelner Dateien.",
        "",
        "Automatisch erzeugt mit `python scripts/third_party_licenses.py` -- nicht von Hand",
        "ändern. Erlaubt sind nur freizügige Lizenzen; `python scripts/check_licenses.py`",
        "prüft das bei jedem Testlauf.",
        "",
        "Inhalt",
        _THIN,
    ]
    for title, group in (("Python", python_sorted), ("npm", npm_sorted)):
        out += [f"[{title}] {len(group)} Pakete"]
        out += [f"  {p.name:<34} {p.version:<14} {p.license or '(keine Angabe)'}" for p in group]
        out.append("")
    mpl = [p for p in python_sorted + npm_sorted if _is_mpl(p)]
    if mpl:
        out += ["Hinweis zu MPL-2.0-Komponenten", _THIN, _MPL_NOTE, ""]
        for pkg in mpl:
            out += mpl_lines(pkg)
        out.append("")
    for title, group in (("Python-Pakete", python_sorted), ("npm-Pakete", npm_sorted)):
        out += [_RULE, title, _RULE, ""]
        for pkg in group:
            out += [_THIN, f"{pkg.name} {pkg.version}", f"Lizenz: {pkg.license or '(keine Angabe)'}", _THIN]
            if pkg.note:
                out += _wrap(f"Hinweis: {pkg.note}", "", 90) + [""]
            for label, text in pkg.texts:
                if len(pkg.texts) > 1 or label.startswith("Standardtext"):
                    out += [f"[{label}]", ""]
                out += [text, ""]
    return "\n".join(out).rstrip("\n") + "\n"


def parse_index(text: str) -> dict[str, list[tuple[str, str]]]:
    """Liest den Inhaltsabschnitt der Datei zurueck: {"Python": [(name, version)], "npm": [...]}.
    Damit prueft der Test, ob die eingecheckte Datei zu constraints/Lockfile passt."""
    result: dict[str, list[tuple[str, str]]] = {}
    current = None
    for line in text.replace("\r\n", "\n").split("\n"):
        header = re.match(r"^\[(Python|npm)\] \d+ Pakete$", line)
        if header:
            current = header.group(1)
            result[current] = []
        elif current and line.startswith("  "):
            parts = line.split(None, 2)
            if len(parts) >= 2:
                result[current].append((parts[0], parts[1]))
        elif (current and not line.strip()) or line.startswith(("Hinweis", "=====")):
            current = None
    return result


def expected_index(repo: Path = REPO_ROOT) -> dict[str, list[tuple[str, str]]]:
    """Was im Inhaltsabschnitt stehen MUSS -- allein aus constraints.txt und
    package-lock.json abgeleitet (braucht keine installierten Pakete)."""
    python = sorted(read_constraints(repo / "deploy" / "constraints.txt"), key=lambda p: p[0].lower())
    npm = sorted_packages(collect_npm(repo, with_texts=False))
    return {"Python": python, "npm": [(p.name, p.version) for p in npm]}


# ---------------------------------------------------------------------------
# Kommandozeile
# ---------------------------------------------------------------------------


def collect_all(repo: Path = REPO_ROOT) -> tuple[list[Package], list[Package]]:
    return collect_python(read_constraints(repo / "deploy" / "constraints.txt")), collect_npm(repo)


def report_problems(packages: list[Package]) -> int:
    count = 0
    for pkg in packages:
        for problem in pkg.problems + pkg.text_problems:
            count += 1
            print(f"PROBLEM  [{pkg.ecosystem}] {pkg.name} {pkg.version}: {problem}", file=sys.stderr)
    return count


def skipped_packages(packages: list[Package]) -> list[Package]:
    return [pkg for pkg in packages if pkg.skipped]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Erzeugt THIRD_PARTY_LICENSES aus constraints.txt und package-lock.json.")
    parser.add_argument("--check", action="store_true", help="nur pruefen, ob THIRD_PARTY_LICENSES aktuell ist")
    parser.add_argument("--print", dest="to_stdout", action="store_true", help="auf stdout statt in die Datei")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    python_packages, npm_packages = collect_all()
    if report_problems(python_packages + npm_packages):
        print(
            "\nDie Umgebung passt nicht zu deploy/constraints.txt bzw. package-lock.json -- es wurde nichts geschrieben.\n"
            "Abhilfe: `pip install -c deploy/constraints.txt -e sdk/python -e backend` und\n"
            "`npm ci` (in frontend/) ausführen, dann erneut starten.",
            file=sys.stderr,
        )
        return 2
    skipped = skipped_packages(python_packages)
    if skipped:
        names = ", ".join(f"{pkg.name} {pkg.version}" for pkg in skipped)
        print(f"Übersprungen (nicht für diese Plattform): {names}")
        if args.check:
            # Vollvergleich geht nicht (Lizenztexte der uebersprungenen Pakete fehlen); die Paketliste
            # der Datei wird trotzdem gegen constraints/Lockfile geprueft, den Rest macht Linux/CI.
            current = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
            if parse_index(current) != {k: list(v) for k, v in expected_index().items()}:
                print(f"{args.output.name} ist veraltet. Neu erzeugen mit: python scripts/third_party_licenses.py (unter Linux)", file=sys.stderr)
                return 1
            print(f"{args.output.name}: Paketliste aktuell (Lizenztexte nur unter Linux/CI vollständig geprüft).")
            return 0
        print(
            "Die Datei kann hier nicht vollständig erzeugt werden (Texte der übersprungenen Pakete fehlen) -- "
            "es wurde nichts geschrieben. Bitte unter Linux bzw. im Docker-Build erzeugen.",
            file=sys.stderr,
        )
        return 2
    content = render(python_packages, npm_packages)
    if args.to_stdout:
        sys.stdout.write(content)
        return 0
    if args.check:
        current = args.output.read_text(encoding="utf-8").replace("\r\n", "\n") if args.output.is_file() else ""
        if current != content:
            print(f"{args.output.name} ist veraltet. Neu erzeugen mit: python scripts/third_party_licenses.py", file=sys.stderr)
            return 1
        print(f"{args.output.name} ist aktuell.")
        return 0
    args.output.write_text(content, encoding="utf-8", newline="\n")
    print(f"{args.output} geschrieben ({len(python_packages)} Python-, {len(npm_packages)} npm-Pakete).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
