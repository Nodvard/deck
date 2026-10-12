"""Modul-Regel fuer die Oberflaeche: Eine Erweiterung haengt an keinem anderen Modul.

Der Code einer Erweiterung unter `extensions/<id>/frontend/src` wird in ihr eigenes Bundle gebaut. Er darf nur
Dateien der eigenen Erweiterung und das gemeinsame UI-Kit (`extensions/_shared/frontend/src`) einbinden -- nie
Kern-Quelltext unter `frontend/` (der landete sonst als Kopie im Bundle, und eine fremde Erweiterung koennte das nie)
und nie eine andere Erweiterung. Was Kern und Erweiterungen teilen, gehoert ins Kit (der Kern bindet es ueber
`frontend/kit.resolve.ts` an). Testdateien (`*.test.ts(x)`) duerfen den Kern laden: Sie pruefen die Seite in der
echten Kern-Shell und landen in keinem Bundle.

Die Regel gilt ohne Ausnahmen. Das Kit selbst ist die eine Bruecke zum Kern und wird hier nicht geprueft:
`_shared/frontend/src/deck.ts` reicht `deck` aus `frontend/src/lib/deckGlobal` weiter (der Zugang zum Kern ueber
`window`), `timezone.ts` die Zeitzone des Dashboards aus `frontend/src/lib/deckTimezone` (fuer den Zeitplan-Waehler),
`testShell.tsx` ist ein Helfer nur fuer Tests.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTENSIONS = REPO_ROOT / "extensions"

# `import … from "x"`, `export … from "x"`, `import "x"` und `import("x")`.
_SPECIFIER = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*)["']([^"']+)["']""")


def _bundled_sources(extensions: Path = EXTENSIONS) -> list[Path]:
    files = []
    for path in sorted(extensions.glob("*/frontend/src/**/*")):
        if path.relative_to(extensions).parts[0] == "_shared":
            continue
        if path.suffix not in (".ts", ".tsx") or path.name.endswith((".test.ts", ".test.tsx", ".d.ts")):
            continue
        files.append(path)
    return files


def _foreign_imports(path: Path, repo: Path = REPO_ROOT) -> list[tuple[str, str]]:
    """Relative Importe dieser Datei, die weder in die eigene Erweiterung noch ins Kit zeigen."""
    extensions = repo / "extensions"
    shared = extensions / "_shared" / "frontend" / "src"
    own_root = extensions / path.relative_to(extensions).parts[0]
    found = []
    for spec in _SPECIFIER.findall(path.read_text(encoding="utf-8")):
        if not spec.startswith("."):
            continue  # Pakete (react, …) loest der Bau gegen frontend/node_modules auf
        target = Path(os.path.normpath(path.parent / spec))
        if target.is_relative_to(own_root) or target.is_relative_to(shared):
            continue
        found.append((path.relative_to(repo).as_posix(), target.relative_to(repo).as_posix()))
    return found


def test_extension_code_imports_only_itself_and_the_ui_kit():
    violations = [hit for path in _bundled_sources() for hit in _foreign_imports(path)]
    assert sorted(set(violations)) == []


def test_the_guard_catches_core_and_foreign_extension_imports(tmp_path):
    """Gegenprobe mit einer Beispiel-Erweiterung: Kern-Code und eine fremde Erweiterung werden gefunden, das Kit,
    die eigene Erweiterung, Pakete und dynamische Importe der eigenen Dateien nicht."""
    ext = tmp_path / "extensions"
    src = ext / "demo" / "frontend" / "src"
    src.mkdir(parents=True)
    (ext / "_shared" / "frontend" / "src").mkdir(parents=True)
    page = src / "Page.tsx"
    page.write_text(
        'import { useState } from "react";\n'
        'import { Button } from "../../../_shared/frontend/src/ui";\n'
        'import { helper } from "./helper";\n'
        'const lazy = () => import("./Lazy");\n'
        'import { HostPicker } from "../../../../frontend/src/components/HostPicker";\n'
        'export { other } from "../../../proxmox/frontend/src/api";\n',
        encoding="utf-8",
    )
    kit = ext / "_shared" / "frontend" / "src" / "deck.ts"
    kit.write_text('export { deck } from "../../../../frontend/src/lib/deckGlobal";\n', encoding="utf-8")
    (src / "Page.test.tsx").write_text('import { x } from "../../../../frontend/src/lib/deckGlobal";\n', encoding="utf-8")

    assert _bundled_sources(ext) == [page]  # Testdateien laufen nur in der Kern-Shell, das Kit ist die Bruecke
    assert sorted(_foreign_imports(page, tmp_path)) == [
        ("extensions/demo/frontend/src/Page.tsx", "extensions/proxmox/frontend/src/api"),
        ("extensions/demo/frontend/src/Page.tsx", "frontend/src/components/HostPicker"),
    ]
