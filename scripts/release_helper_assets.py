#!/usr/bin/env python3
"""Dateien fürs GitHub-Release: die Compose-Datei mit Helfer, fest auf den geprüften Digest, und der Text dazu.

Läuft im Job `helper-release` von `.github/workflows/release.yml`; im Repository bleibt `:1`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "deploy" / "compose.standalone-mit-helfer.yml"
NOTES_DIR = REPO_ROOT / "docs" / "release-notes"

COMPOSE_NAME = "compose.standalone-mit-helfer.yml"
SECTION_NAME = "helper-digest.md"
NOTES_NAME = "release-notes.md"
TITLE_NAME = "release-title.txt"

MARKER = "<!-- nodvard-helper-digest -->"
MARKER_END = "<!-- /nodvard-helper-digest -->"

_NUM = r"(0|[1-9][0-9]{0,8})"
VERSION_RE = re.compile(rf"{_NUM}\.{_NUM}\.{_NUM}", re.ASCII)
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
IMAGE_RE = re.compile(r"[a-z0-9]+(\.[a-z0-9-]+)*(:[0-9]+)?(/[a-z0-9]+([._-][a-z0-9]+)*)+", re.ASCII)


class AssetError(Exception):
    """Abgelehnt; die Meldung sagt warum (Exit-Code 1)."""


def pin_compose(text: str, image: str, digest: str) -> str:
    """`text` mit `image: <image>@<digest>` statt der einzigen Zeile `image: <image>:1`."""
    tagged = f"image: {image}:1"
    lines = text.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.strip() == tagged]
    if len(hits) != 1:
        raise AssetError(f"In {COMPOSE_NAME} steht „{tagged}“ {len(hits)}-mal, erwartet genau einmal.")
    line = lines[hits[0]]
    indent = line[: len(line) - len(line.lstrip())]
    ending = line[len(line.rstrip("\r\n")):]
    lines[hits[0]] = f"{indent}image: {image}@{digest}{ending}"
    return "".join(lines)


def section(version: str, image: str, digest: str) -> str:
    return (
        f"{MARKER}\n"
        "## Update-Helfer / Update helper\n\n"
        f"`{image}@{digest}` (Version {version})\n\n"
        f"Die Datei `{COMPOSE_NAME}` an diesem Release nennt genau diesen Helfer. Vergleichen kannst du den Digest mit "
        f"`docker buildx imagetools inspect {image}:{version}` (Zeile „Digest“).\n\n"
        f"The file `{COMPOSE_NAME}` attached to this release pins exactly this helper. Compare the digest with "
        f"`docker buildx imagetools inspect {image}:{version}` (line “Digest”).\n"
        f"{MARKER_END}\n"
    )


def notes(version: str, part: str, notes_dir: Path = NOTES_DIR) -> tuple[str, str]:
    """Titel und Text eines neuen Entwurfs aus `docs/release-notes/<version>.md`; ohne die Datei nur Titel und Abschnitt."""
    path = notes_dir / f"{version}.md"
    title = f"Nodvard Deck {version}"
    if not path.is_file():
        return title, part
    body = path.read_text(encoding="utf-8")
    first, _, rest = body.partition("\n")
    if first.startswith("# "):
        title, body = first[2:].strip() or title, rest
    return title, f"{body.strip()}\n\n{part}"


def merge(existing: str, part: str) -> str:
    """Der Text eines vorhandenen Entwurfs oder Releases mit `part` statt seines Abschnitts, sonst am Ende; der Rest bleibt."""
    starts, ends = existing.count(MARKER), existing.count(MARKER_END)
    if starts == ends == 0:
        kept = existing.rstrip()
        return f"{kept}\n\n{part}" if kept else part
    start = existing.find(MARKER)
    end = existing.find(MARKER_END, start)
    if starts != 1 or ends != 1 or end < 0:
        # Was nach einer kaputten Markierung steht, kann von Hand geschrieben sein: lieber nichts ändern.
        raise AssetError(
            f"Im Text des Releases steht der Abschnitt „{MARKER}“ … „{MARKER_END}“ nicht genau einmal vollständig. "
            "Bitte den Text auf GitHub von Hand richten und den Job neu starten."
        )
    return existing[:start] + part.rstrip("\n") + existing[end + len(MARKER_END):]


def _check(version: str, image: str, digest: str) -> None:
    if VERSION_RE.fullmatch(version) is None:
        raise AssetError(f"Keine fertige Version: {version!r}.")
    if IMAGE_RE.fullmatch(image) is None:
        raise AssetError(f"Kein Image-Name: {image!r}.")
    if DIGEST_RE.fullmatch(digest) is None:
        raise AssetError(f"Kein Digest der Form sha256:<64 Hex-Zeichen>: {digest!r}.")


def write_files(version: str, image: str, digest: str, out: Path) -> None:
    _check(version, image, digest)
    pinned = pin_compose(COMPOSE.read_text(encoding="utf-8"), image, digest)
    part = section(version, image, digest)
    title, body = notes(version, part)
    out.mkdir(parents=True, exist_ok=True)
    (out / COMPOSE_NAME).write_text(pinned, encoding="utf-8")
    (out / SECTION_NAME).write_text(part, encoding="utf-8")
    (out / NOTES_NAME).write_text(body, encoding="utf-8")
    (out / TITLE_NAME).write_text(title + "\n", encoding="utf-8")


USAGE = """\
files schreibt compose.standalone-mit-helfer.yml (die Datei aus deploy/, nur das Image des Helfers per Digest statt :1),
helper-digest.md (Abschnitt mit dem Digest), release-notes.md (docs/release-notes/<version>.md ohne die Überschrift, dazu
der Abschnitt) und release-title.txt, die beiden letzten nur für einen neuen Entwurf. merge setzt den Abschnitt in den Text
eines vorhandenen Entwurfs oder Releases: genau an die Stelle des älteren Abschnitts, sonst ans Ende; der übrige Text bleibt.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog=USAGE,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    files = commands.add_parser("files")
    files.add_argument("--version", required=True)
    files.add_argument("--image", required=True)
    files.add_argument("--digest", required=True)
    files.add_argument("--out", required=True, type=Path)
    combine = commands.add_parser("merge")
    combine.add_argument("--existing", required=True, type=Path)
    combine.add_argument("--section", required=True, type=Path)
    combine.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "files":
            write_files(args.version, args.image, args.digest, args.out)
        else:
            part = args.section.read_text(encoding="utf-8")
            if not (part.startswith(MARKER) and part.rstrip("\n").endswith(MARKER_END)):
                raise AssetError(f"{args.section} ist kein Abschnitt aus „files“.")
            # Als Bytes, damit Zeilenenden aus dem Editor von GitHub (\r\n) bleiben; das letzte \n hängt `gh --jq` an.
            existing = args.existing.read_bytes().decode("utf-8").removesuffix("\n")
            args.out.write_bytes(merge(existing, part).encode("utf-8"))
    except (AssetError, OSError) as exc:
        print(f"Abgelehnt: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
