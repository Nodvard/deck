"""Ob eine fertige Version die höchste unter den Tags ist; nur dann bekommt sie `latest` und die Tags ohne Nummer.

    git ls-remote --tags --refs origin 'v*' | python3 scripts/release_newest.py 0.12.2   # newest=true|false
"""

from __future__ import annotations

import re
import sys

_FINAL = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
_PRE = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*")


def _key(version: str) -> tuple[int, int, int] | None:
    if not (match := _FINAL.fullmatch(version)):
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def tag_versions(lines: list[str]) -> list[str]:
    """Die Versionen aus Zeilen wie `<sha>\trefs/tags/v0.12.1` oder `v0.12.1`, ohne das führende `v`."""
    found = []
    for line in lines:
        name = line.strip().rsplit("refs/tags/", 1)[-1]
        if name.startswith("v"):
            found.append(name[1:])
    return found


def is_newest(version: str, tags: list[str]) -> bool:
    """`True`, wenn `version` fertig ist und kein fertiges Tag höher steht; eine Vorabversion ist es nie."""
    own = _key(version)
    if own is None:
        if _PRE.fullmatch(version):
            return False
        raise ValueError(f"{version!r} ist keine Versionsnummer.")
    if version not in tags:
        raise ValueError(f"Das Tag v{version} steht nicht in der Liste; ohne vollständige Liste wandert kein Tag.")
    return all(key <= own for key in map(_key, tags) if key is not None)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Aufruf: release_newest.py <version>  (Tags auf stdin)", file=sys.stderr)
        return 2
    try:
        newest = is_newest(argv[1], tag_versions(sys.stdin.read().splitlines()))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"newest={'true' if newest else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
