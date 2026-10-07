"""Migrations-Einstiegspunkt -- laesst `alembic upgrade heads` laufen (Mehrzahl),
nicht das rohe CLI-Kommando `alembic -c backend/alembic.ini upgrade head` (Einzahl),
das bis zur inventory-Extension (erste mit echten eigenen Tabellen) hier lief.

docs/02-EXTENSION-API.md §7 sieht das seit der ersten Fassung so vor: "Jede
Extension hat einen eigenen Alembic-Branch (`alembic upgrade heads` faehrt Kern +
alle Extensions hoch)." Bis jetzt war das Spekulation ohne echten Abnehmer
(`ext/tables.py`s eigener Docstring: "ohne eine Extension mit eigenem Schema waere
das spekulativ gegen nichts Echtes").

**Warum ein eigener Python-Einstiegspunkt statt eines statischen `version_locations`-
Eintrags in `alembic.ini`:** Alembics `ScriptDirectory` liest `version_locations`,
BEVOR `env.py` ueberhaupt laeuft (`env.py` kann den Wert fuer den aktuellen Lauf
nicht mehr beeinflussen) -- ein rein statischer Ini-Eintrag muesste also bei JEDER
neuen Extension mit eigenen Tabellen von Hand nachgezogen werden. Hier stattdessen
programmatisch: jedes `extensions/<id>/migrations/versions`-Verzeichnis, das beim
Aufruf existiert, wird automatisch aufgenommen (`_discover_version_locations()`) --
eine neue Extension muss nur ihre eigene erste Revision mit `down_revision=None` +
einem eigenen `branch_labels`-Wert anlegen, sonst nichts in diesem Repo aendern.

Mehrere unabhaengige Koepfe in EINER `alembic_version`-Tabelle sind natives Alembic-
Verhalten (eine Zeile pro Kopf) -- kein zweiter Mechanismus noetig.

**Live gefunden beim ersten echten Deploy:** `backend_dir` NICHT relativ zu
`Path(__file__)` ableiten -- das funktioniert nur bei einem editierbaren Dev-Install
(`pip install -e`, README), wo `__file__` noch auf den Repo-Checkout zeigt. Im
Container ist `nodvard_deck` per `pip install ./backend` (nicht editierbar) nach
site-packages installiert, `__file__` zeigt dann auf einen Pfad ohne jedes
`backend/alembic.ini` daneben ("Path doesn't exist: .../migrations"). Der einzige
Anker, der in BEIDEN Faellen stimmt: der Arbeitsordner des Prozesses -- README
fuehrt `python -m nodvard_deck.migrate` explizit vom Repo-Root aus, `entrypoint.sh`
macht vorher ein explizites `cd /app` (Container-Aequivalent des Repo-Roots).
Deshalb `Path.cwd()`, nicht `Path(__file__)`."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import get_settings


class DuplicateRevisions(RuntimeError):
    """Dieselbe Revision liegt in zwei Erweiterungsordnern -- meist ist nach einer Umbenennung (oder
    beim Auspacken ueber einen alten Stand) der alte Ordner liegen geblieben. Die Meldung ist ein ganzer
    deutscher Satz ohne Pfade; `boot` zeigt sie auf der Notseite."""


def _check_duplicate_revisions(extension_locations: list[Path]) -> None:
    """Vor Alembic: Ohne diese Pruefung bricht Alembic mit einer unverstaendlichen Meldung ab (ein
    Zweig-Name sei schon vergeben, oder eine Revision sei mehrfach da)."""
    from .core.backup.snapshot import REVISION_RE

    seen: dict[str, str] = {}
    clashes: set[tuple[str, str]] = set()
    for versions_dir in extension_locations:
        folder = versions_dir.parent.parent.name
        for script in sorted(versions_dir.glob("*.py")):
            try:
                text = script.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for revision in REVISION_RE.findall(text):
                first = seen.setdefault(revision, folder)
                if first != folder:
                    clashes.add((first, folder))
    if clashes:
        first, second = sorted(clashes)[0]
        raise DuplicateRevisions(
            f"Die Migrationen der Erweiterungsordner „{first}“ und „{second}“ sind doppelt – vermutlich ist ein "
            "alter Ordner liegen geblieben (zum Beispiel nach der Umbenennung einer Erweiterung). Bitte den alten "
            "Ordner unter extensions/ löschen und neu starten."
        )


def _discover_version_locations(repo_root: Path, backend_dir: Path) -> list[Path]:
    locations = [backend_dir / "migrations" / "versions"]
    extensions_dir = repo_root / "extensions"
    if extensions_dir.is_dir():
        for ext_dir in sorted(p for p in extensions_dir.iterdir() if p.is_dir()):
            versions_dir = ext_dir / "migrations" / "versions"
            if versions_dir.is_dir():
                locations.append(versions_dir)
    _check_duplicate_revisions(locations[1:])
    return locations


def alembic_config(database_url: str | None = None, repo_root: Path | None = None) -> tuple[Config, list[Path]]:
    """Alembic-Konfiguration samt der gefundenen `version_locations` (Kern + alle Erweiterungen
    mit eigenem Zweig). Ohne `database_url` bleibt die URL leer -- fuer Abfragen am
    Skriptordner (`known_revisions`) braucht es keine Datenbank. Wirft `DuplicateRevisions`, wenn zwei
    Erweiterungsordner dieselbe Revision enthalten."""
    repo_root = repo_root or Path.cwd()
    backend_dir = repo_root / "backend"
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    cfg.set_main_option("path_separator", "os")
    locations = _discover_version_locations(repo_root, backend_dir)
    cfg.set_main_option("version_locations", os.pathsep.join(str(p) for p in locations))
    if database_url is not None:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg, locations


def known_revisions(repo_root: Path | None = None) -> tuple[set[str], set[str]]:
    """-> (alle Revisionen, die dieses Image kennt; die Koepfe davon). Grundlage dafuer,
    eine fremde Datenbank vor dem Einspielen auf bekannte Staende zu pruefen."""
    from alembic.script import ScriptDirectory

    cfg, _ = alembic_config(repo_root=repo_root)
    script = ScriptDirectory.from_config(cfg)
    every = {rev.revision for rev in script.walk_revisions()}
    return every, set(script.get_heads())


def upgrade_heads() -> list[Path]:
    """Fuehrt die Migration aus UND gibt die tatsaechlich verwendeten
    `version_locations` zurueck -- praktisch fuers Logging/Tests, ohne dass ein
    Aufrufer `_discover_version_locations()` ein zweites Mal separat rufen muss."""
    settings = get_settings()
    settings.ensure_data_dir()

    cfg, locations = alembic_config(settings.database_url)
    command.upgrade(cfg, "heads")
    return locations


def main() -> None:
    locations = upgrade_heads()
    print(f"OK: {len(locations)} Migrations-Verzeichnis(se) auf 'heads' gebracht.")
    for loc in locations:
        print(f"  - {loc}")


if __name__ == "__main__":
    main()
