#!/usr/bin/env python3
"""Release-Skript: aus den Bruchstuecken unter `unreleased/` wird eine neue Version.

    python scripts/release.py 0.5.0 [--title "Kurzer Titel"] [--date 2026-10-01] [--dry-run]

Was passiert (alles oder nichts -- vorher wird alles geprueft, erst dann geschrieben):

1. Alle `backend/src/nodvard_deck/changelog/unreleased/*.toml` werden STRENG gelesen (eine
   kaputte Datei bricht ab, statt still zu fehlen) und zu
   `backend/src/nodvard_deck/changelog/versions/<version>.toml` zusammengefasst, mit dem
   heutigen Datum (oder `--date`) und optional `--title`.
2. Die Bruchstuecke werden geloescht (die README.md bleibt).
3. Die Versionsnummer wird an allen Stellen angehoben: `backend/src/nodvard_deck/version.py`,
   `backend/pyproject.toml`, `frontend/package.json` und die beiden Wurzel-Eintraege in
   `frontend/package-lock.json` (sonst zeigt der naechste `npm install` einen Unterschied).

Abgelehnt wird, wenn die Version nicht groesser ist als die aktuelle, wenn es keine
Bruchstuecke gibt oder wenn die bisherigen Versionsnummern nicht im Gleichschritt sind
(dafuer gibt es auch einen Test). Eine schon vorhandene Versionsdatei faellt unter
"nicht groesser": die neueste Datei nennt immer die aktuelle Version.

Versionsnummern: groessere Funktionsrunden erhoehen die mittlere Zahl (0.5.0 -> 0.6.0),
reine Fehlerkorrekturen die letzte (0.5.0 -> 0.5.1).

Nur Standardbibliothek plus `nodvard_deck.changelog` (selbst nur Standardbibliothek); das
Skript ruft kein git auf -- Commit und PR macht der Mensch bzw. die Sitzung danach.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPO_ROOT = Path(__file__).resolve().parent.parent
# Vor dem Import: das Skript soll den Stand DIESES Repositorys pruefen, nicht ein
# eventuell aelteres, installiertes `nodvard_deck`.
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from nodvard_deck.changelog import (  # noqa: E402
    ChangelogError,
    Entry,
    parse_release,
    parse_version,
    read_fragment,
    read_release,
)


class ReleaseError(Exception):
    """Das Release wird abgelehnt; die Meldung sagt warum (Exit-Code 1)."""


def today() -> str:
    """Heute als JJJJ-MM-TT in deutscher Ortszeit (die Sitzung laeuft meist in UTC --
    kurz nach Mitternacht waere sonst das Datum von gestern eingetragen)."""
    try:
        return datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    except ZoneInfoNotFoundError:
        return datetime.now().date().isoformat()


@dataclass(frozen=True)
class Tree:
    """Alle Pfade, die das Skript anfasst -- als Parameter, damit Tests auf einer
    Kopie in einem Temp-Ordner laufen koennen."""

    root: Path

    @property
    def changelog_dir(self) -> Path:
        return self.root / "backend" / "src" / "nodvard_deck" / "changelog"

    @property
    def versions_dir(self) -> Path:
        return self.changelog_dir / "versions"

    @property
    def unreleased_dir(self) -> Path:
        return self.changelog_dir / "unreleased"

    @property
    def version_py(self) -> Path:
        return self.root / "backend" / "src" / "nodvard_deck" / "version.py"

    @property
    def pyproject(self) -> Path:
        return self.root / "backend" / "pyproject.toml"

    @property
    def package_json(self) -> Path:
        return self.root / "frontend" / "package.json"

    @property
    def package_lock(self) -> Path:
        return self.root / "frontend" / "package-lock.json"


# --- Versionsstellen lesen und anheben -----------------------------------------------

_VERSION_PY_RE = re.compile(r'^(__version__\s*=\s*")([^"]*)(")', re.MULTILINE)
_PYPROJECT_PROJECT_RE = re.compile(r"(?ms)^\[project\][^\n]*\n(.*?)(?=^\[|\Z)")
_PYPROJECT_VERSION_RE = re.compile(r'(?m)^(version\s*=\s*")([^"]*)(")')
_JSON_VERSION_RE = re.compile(r'(?m)^(  "version":\s*")([^"]*)(")')
_LOCK_ROOT_RE = re.compile(r'(?s)("packages":\s*\{\s*"":\s*\{.*?"version":\s*")([^"]*)(")')


def _read(path: Path) -> str:
    # Bytes statt read_text: Zeilenenden der Datei bleiben unangetastet.
    try:
        return path.read_bytes().decode("utf-8")
    except OSError as exc:
        raise ReleaseError(f"{path} laesst sich nicht lesen: {exc}") from exc


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _pyproject_version(text: str) -> str | None:
    block = _PYPROJECT_PROJECT_RE.search(text)
    match = _PYPROJECT_VERSION_RE.search(block.group(1)) if block else None
    return match.group(2) if match else None


def _lock_versions(text: str) -> tuple[str | None, str | None]:
    try:
        data = json.loads(text)
        return data.get("version"), data.get("packages", {}).get("", {}).get("version")
    except (ValueError, AttributeError):
        return None, None


def read_versions(tree: Tree) -> dict[str, str | None]:
    """Die Versionsnummer an jeder Stelle (None = nicht gefunden)."""
    found: dict[str, str | None] = {}
    match = _VERSION_PY_RE.search(_read(tree.version_py))
    found["backend/src/nodvard_deck/version.py"] = match.group(2) if match else None
    found["backend/pyproject.toml"] = _pyproject_version(_read(tree.pyproject))
    try:
        found["frontend/package.json"] = json.loads(_read(tree.package_json)).get("version")
    except ValueError:
        found["frontend/package.json"] = None
    if tree.package_lock.exists():
        top, root_pkg = _lock_versions(_read(tree.package_lock))
        found["frontend/package-lock.json"] = top
        found["frontend/package-lock.json (packages)"] = root_pkg
    return found


def current_version(tree: Tree) -> str:
    """Die aktuelle Version -- nur, wenn ALLE Stellen (auch die neueste Versionsdatei)
    dieselbe nennen. Sonst weiss niemand, von welcher Nummer aus hochgezaehlt wird."""
    found = read_versions(tree)
    if tree.versions_dir.is_dir():
        released = []
        for path in sorted(tree.versions_dir.glob("*.toml")):
            try:
                released.append(read_release(path))
            except ChangelogError as exc:
                raise ReleaseError(f"Versionsdatei ungueltig: {exc}") from exc
        if released:
            newest = max(released, key=lambda r: parse_version(r.version))
            found[f"neueste Versionsdatei ({newest.version}.toml)"] = newest.version
    distinct = {v for v in found.values()}
    if len(distinct) != 1 or None in distinct:
        lines = "\n".join(f"  {where}: {value or 'nicht gefunden'}" for where, value in found.items())
        raise ReleaseError(f"Die Versionsnummern sind nicht im Gleichschritt:\n{lines}")
    return distinct.pop()  # type: ignore[return-value]


def _sub_once(pattern: re.Pattern[str], text: str, version: str, where: str) -> str:
    new, count = pattern.subn(lambda m: f"{m.group(1)}{version}{m.group(3)}", text, count=1)
    if count != 1:
        raise ReleaseError(f"In {where} wurde keine Versionsnummer gefunden.")
    return new


def bumped_files(tree: Tree, version: str) -> dict[Path, str]:
    """Neuer Inhalt aller Dateien mit Versionsnummer (noch nichts geschrieben)."""
    out: dict[Path, str] = {}

    out[tree.version_py] = _sub_once(_VERSION_PY_RE, _read(tree.version_py), version, "version.py")

    pyproject = _read(tree.pyproject)
    block = _PYPROJECT_PROJECT_RE.search(pyproject)
    if not block:
        raise ReleaseError("In pyproject.toml fehlt der Abschnitt [project].")
    new_block = _sub_once(_PYPROJECT_VERSION_RE, block.group(1), version, "pyproject.toml")
    out[tree.pyproject] = pyproject[: block.start(1)] + new_block + pyproject[block.end(1):]

    out[tree.package_json] = _sub_once(_JSON_VERSION_RE, _read(tree.package_json), version, "package.json")

    if tree.package_lock.exists():
        lock = _read(tree.package_lock)
        lock = _sub_once(_JSON_VERSION_RE, lock, version, "package-lock.json")
        out[tree.package_lock] = _sub_once(_LOCK_ROOT_RE, lock, version, "package-lock.json (packages)")

    # Gegenprobe im Speicher: jede Stelle muss danach die neue Nummer nennen.
    if _VERSION_PY_RE.search(out[tree.version_py]).group(2) != version:  # type: ignore[union-attr]
        raise ReleaseError("version.py liess sich nicht richtig anheben.")
    if _pyproject_version(out[tree.pyproject]) != version:
        raise ReleaseError("pyproject.toml liess sich nicht richtig anheben.")
    try:
        if json.loads(out[tree.package_json]).get("version") != version:
            raise ValueError
        if tree.package_lock.exists() and _lock_versions(out[tree.package_lock]) != (version, version):
            raise ValueError
    except ValueError as exc:
        raise ReleaseError("package.json/package-lock.json liessen sich nicht richtig anheben.") from exc
    return out


# --- Versionsdatei erzeugen ------------------------------------------------------------


def toml_string(text: str) -> str:
    """Text als TOML-Basisstring (JSON-Maskierung ist dafuer gueltig; DEL kommt dazu)."""
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def render_release(version: str, day: str, title: str | None, entries: list[Entry]) -> str:
    lines = [f"version = {toml_string(version)}", f"date = {toml_string(day)}"]
    if title:
        lines.append(f"title = {toml_string(title)}")
    for entry in entries:
        lines += ["", "[[entries]]", f"kind = {toml_string(entry.kind)}", f"text = {toml_string(entry.text)}"]
        if entry.prs:
            lines.append(f"prs = [{', '.join(str(n) for n in entry.prs)}]")
    return "\n".join(lines) + "\n"


# --- Ablauf ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    version: str
    previous: str
    release_path: Path
    release_text: str
    fragments: tuple[Path, ...]
    entry_count: int
    bumps: dict[Path, str]


def plan_release(tree: Tree, version: str, *, title: str | None = None, day: str | None = None) -> Plan:
    """Prueft alles und berechnet den neuen Stand -- schreibt nichts."""
    try:
        new_key = parse_version(version)
    except ChangelogError as exc:
        raise ReleaseError(str(exc)) from exc

    previous = current_version(tree)
    if new_key <= parse_version(previous):
        raise ReleaseError(f"Die Version {version} ist nicht groesser als die aktuelle ({previous}).")

    release_path = tree.versions_dir / f"{version}.toml"

    fragments = tuple(sorted(tree.unreleased_dir.glob("*.toml"))) if tree.unreleased_dir.is_dir() else ()
    if not fragments:
        raise ReleaseError("Es gibt keine Eintraege unter unreleased/ -- so ein Release waere leer.")
    entries: list[Entry] = []
    for path in fragments:
        try:
            entries.extend(read_fragment(path))
        except ChangelogError as exc:
            raise ReleaseError(f"Bruchstueck ungueltig: {exc}") from exc

    day = day or today()
    title = (title or "").strip() or None
    text = render_release(version, day, title, entries)
    # Gegenprobe: was geschrieben wuerde, muss sich wieder einlesen lassen und gleich sein.
    try:
        parsed = parse_release(tomllib.loads(text))
    except (ChangelogError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseError(f"Die neue Versionsdatei waere ungueltig: {exc}") from exc
    if parsed.entries != tuple(entries) or parsed.version != version or parsed.date != day or parsed.title != title:
        raise ReleaseError("Die neue Versionsdatei enthaelt nicht dasselbe wie die Bruchstuecke.")

    return Plan(
        version=version,
        previous=previous,
        release_path=release_path,
        release_text=text,
        fragments=fragments,
        entry_count=len(entries),
        bumps=bumped_files(tree, version),
    )


def apply_plan(plan: Plan) -> None:
    plan.release_path.parent.mkdir(parents=True, exist_ok=True)
    _write(plan.release_path, plan.release_text)
    for path, text in plan.bumps.items():
        _write(path, text)
    for path in plan.fragments:
        path.unlink()


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fasst die Bruchstuecke unter unreleased/ zu einer neuen Version zusammen.")
    parser.add_argument("version", help="neue Versionsnummer, z. B. 0.5.0")
    parser.add_argument("--title", help="optionaler kurzer Titel der Version")
    parser.add_argument("--date", help="Datum JJJJ-MM-TT (Standard: heute)")
    parser.add_argument("--dry-run", action="store_true", help="nur zeigen, was passieren wuerde")
    parser.add_argument("--root", type=Path, help="Repository-Wurzel (Standard: dieses Repository)")
    args = parser.parse_args(argv)

    tree = Tree((args.root or root or REPO_ROOT).resolve())
    try:
        plan = plan_release(tree, args.version, title=args.title, day=args.date)
        if not args.dry_run:
            apply_plan(plan)
    except ReleaseError as exc:
        print(f"Abgebrochen: {exc}", file=sys.stderr)
        return 1

    def rel(path: Path) -> str:
        return path.relative_to(tree.root).as_posix()

    print(f"{'Wuerde anlegen' if args.dry_run else 'Angelegt'}: {rel(plan.release_path)} "
          f"({plan.entry_count} Eintraege aus {len(plan.fragments)} Bruchstuecken, {plan.previous} -> {plan.version})")
    print(f"{'Wuerde anheben' if args.dry_run else 'Angehoben'}: " + ", ".join(rel(p) for p in plan.bumps))
    print(f"{'Wuerde loeschen' if args.dry_run else 'Geloescht'}: " + ", ".join(rel(p) for p in plan.fragments))
    if not args.dry_run:
        print("Als Naechstes: alles committen und als eigenen kleinen PR anlegen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
