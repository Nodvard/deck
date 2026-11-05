"""Die Arten der Änderungen einer Version als Annotation am Image-Index, gelesen aus ihrem Änderungsprotokoll.

    python3 scripts/release_kinds.py 0.12.0   # annotation=index:io.nodvard.deck.release.kinds=behoben,sicherheit
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Vor dem Import: geprüft wird der Stand dieses Repositorys, nicht ein installiertes `nodvard_deck`.
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from nodvard_deck.changelog import KINDS, ChangelogError, parse_version, read_release

ANNOTATION = "io.nodvard.deck.release.kinds"
VERSIONS_DIR = REPO_ROOT / "backend" / "src" / "nodvard_deck" / "changelog" / "versions"

_PRE = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*")


def kinds_annotation(version: str, versions_dir: Path = VERSIONS_DIR) -> str:
    """Der Wert für `annotations` von build-push-action; leer bei einer Vorabversion (die Suche zeigt dann
    „unbekannt“)."""
    if _PRE.fullmatch(version):
        return ""
    try:
        parse_version(version)
    except ChangelogError as exc:
        raise ValueError(f"{version!r} ist keine Versionsnummer.") from exc
    path = versions_dir / f"{version}.toml"
    if not path.is_file():
        raise ValueError(f"{path.name} fehlt: Erst `python scripts/release.py {version}` mergen, dann taggen.")
    release = read_release(path)
    found = {"verbessert" if entry.kind == "dokumentation" else entry.kind for entry in release.entries}
    return f"index:{ANNOTATION}=" + ",".join(kind for kind in KINDS if kind in found)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Aufruf: release_kinds.py <version>", file=sys.stderr)
        return 2
    try:
        annotation = kinds_annotation(argv[1])
    except (ValueError, ChangelogError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"annotation={annotation}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
