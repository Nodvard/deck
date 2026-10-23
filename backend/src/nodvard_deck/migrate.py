"""Migrations-Einstiegspunkt: `alembic upgrade heads` über den Kern und alle Erweiterungen mit eigenem Alembic-Zweig."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import get_settings


class DuplicateRevisions(RuntimeError):
    """Dieselbe Revision liegt in zwei Erweiterungsordnern (meist blieb nach einer Umbenennung der alte liegen)."""


def _check_duplicate_revisions(extension_locations: list[Path]) -> None:
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
    # Alembic liest `version_locations`, bevor `env.py` läuft; ein statischer Eintrag in `alembic.ini` müsste bei jeder
    # neuen Erweiterung von Hand ergänzt werden. Sie legt nur ihre erste Revision mit `down_revision=None` und eigenem
    # `branch_labels` an.
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
    """Alembic-Konfiguration samt der gefundenen `version_locations`; bei derselben Revision in zwei
    Erweiterungsordnern `DuplicateRevisions`."""
    # Der Arbeitsordner, nicht `Path(__file__)`: Im Container liegt das Paket nicht editierbar in site-packages, neben
    # `__file__` steht kein `backend/alembic.ini`. Der Entrypoint wechselt vorher nach `/app`, die README ruft vom Repo
    # aus auf.
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
    """Alle Revisionen dieses Images und deren Köpfe, zum Prüfen einer fremden Datenbank auf bekannte Stände."""
    from alembic.script import ScriptDirectory

    cfg, _ = alembic_config(repo_root=repo_root)
    script = ScriptDirectory.from_config(cfg)
    every = {rev.revision for rev in script.walk_revisions()}
    return every, set(script.get_heads())


def upgrade_heads() -> list[Path]:
    """Führt die Migration aus und gibt die verwendeten `version_locations` zurück."""
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
