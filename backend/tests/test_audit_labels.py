"""Wächter: Jeder Vorgang, den der Code ins Protokoll schreibt, hat eine deutsche Beschriftung.

Die Protokoll-Seite (`frontend/src/routes/settings/AuditSettings.tsx`) zeigt zu einem Vorgang wie `system.restore.applied`
den Text aus `ACTION_LABELS`; fehlt er, steht der technische Name in der Tabelle. Dieser Test liest den Python-Code
(Kern und alle Erweiterungen), sammelt die Namen und prüft sie gegen die Tabelle. Er ist auf diesem Weg robuster als
eine zweite Liste von Hand: Wer einen neuen Vorgang einführt, bekommt sofort einen roten Test, solange die
Beschriftung fehlt.

Gefunden werden Aufrufe mit `action=` und `outcome=` (`audit_service.log`, `ctx.audit.log`, `write_entry`), dazu die
Hilfsfunktionen, die ihren Vorgangsnamen nur durchreichen (`_audit(session, user, "host.created", ...)`, auch über
mehrere Stufen). Was sich im Quelltext nicht auflösen lässt, steht in `DYNAMIC_NAMES` bzw. `NOT_A_WRITE` mit Begründung.
"""

from __future__ import annotations

import ast
import itertools
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_PAGE = REPO_ROOT / "frontend" / "src" / "routes" / "settings" / "AuditSettings.tsx"


SHIELD_SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"


@pytest.fixture(autouse=True)
def _ext_shield_on_path(monkeypatch):
    """Shield ist kein installiertes Paket (CI installiert nur sdk und backend): Pfad nur fuer diese Tests setzen
    und die Module danach wieder vergessen, wie die Tests der Erweiterungen selbst."""
    monkeypatch.syspath_prepend(str(SHIELD_SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def _dynamic_names() -> dict[str, list[str]]:
    """Namen, die der Code aus einem Wert zusammensetzt (`f"system.update.{outcome}"`). Die Werte kommen aus den
    echten Tabellen des Codes, damit die Liste nicht von Hand nachgeführt werden muss; ein neuer Wert dort verlangt
    sofort eine neue Beschriftung. `*` steht in der Fundstelle für den zusammengesetzten Teil."""
    from nodvard_deck.core.updater_client import OUTCOMES
    from nodvard_deck_ext_shield.guard import EVENT_LABEL
    from nodvard_deck_ext_shield.updates import MODE_LABEL

    return {
        # `services/update_helper.py`: Ergebnis des Update-Helfers.
        "system.update.*": [f"system.update.{outcome}" for outcome in OUTCOMES],
        # `extensions/shield/.../guard.py`: Art des Sicherheitsereignisses.
        "shield.guard_*": [f"shield.guard_{kind}" for kind in EVENT_LABEL],
        # `extensions/shield/.../patching.py`: Art des Einspielens.
        "shield.updates_*": [f"shield.updates_{mode}" for mode in MODE_LABEL],
    }


# (Datei, Ausdruck) -> Grund. Aufrufe mit `action=`/`outcome=`, die keinen festen Vorgang schreiben.
NOT_A_WRITE = {
    ("backend/src/nodvard_deck/api/v1/audit.py", "entry.action"): "liest nur: gibt den Eintrag in der Antwort wieder",
    ("backend/src/nodvard_deck/core/audit.py", "_clip('action', action)"): "der Schreibpfad selbst (`write_entry`)",
}

SCANNED = [REPO_ROOT / "backend" / "src", *sorted((REPO_ROOT / "extensions").glob("*/src"))]


def _source_files() -> list[Path]:
    return sorted(p for root in SCANNED for p in root.rglob("*.py"))


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


class _Scan:
    def __init__(self, key: str) -> None:
        self.key = key  # "action" oder "target_type"
        self.trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in _source_files()}
        self.constants: dict[str, list[ast.expr]] = {}
        for tree in self.trees.values():
            for node in tree.body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    self.constants.setdefault(node.targets[0].id, []).append(node.value)
        self.found: dict[str, str] = {}  # Name -> erste Fundstelle
        self.unresolved: list[tuple[str, str, int]] = []  # (Datei, Ausdruck, Zeile)
        self._run()

    # -- Werte auflösen ----------------------------------------------------------------------------------------

    def resolve(self, node: ast.expr, depth: int = 0) -> list[str] | None:
        """Mögliche Werte eines Ausdrucks als Texte; `*` für einen nicht auflösbaren Teil. `None`: gar nicht."""
        if depth > 6:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.IfExp):
            a, b = self.resolve(node.body, depth + 1), self.resolve(node.orelse, depth + 1)
            return None if a is None or b is None else a + b
        if isinstance(node, ast.Name):
            values = self.constants.get(node.id)
            if not values:
                return None
            out: list[str] = []
            for value in values:
                resolved = self.resolve(value, depth + 1)
                if resolved is None:
                    return None
                out += resolved
            return list(dict.fromkeys(out))
        if isinstance(node, ast.JoinedStr):
            parts: list[list[str]] = []
            for piece in node.values:
                if isinstance(piece, ast.Constant):
                    parts.append([str(piece.value)])
                else:
                    resolved = self.resolve(piece.value, depth + 1)  # type: ignore[attr-defined]
                    parts.append(resolved if resolved is not None else ["*"])
            return ["".join(p) for p in itertools.product(*parts)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = self.resolve(node.left, depth + 1), self.resolve(node.right, depth + 1)
            return None if a is None or b is None else [x + y for x in a for y in b]
        return None

    # -- Aufrufe suchen -----------------------------------------------------------------------------------------

    @staticmethod
    def _enclosing(tree: ast.Module) -> dict[int, ast.FunctionDef | ast.AsyncFunctionDef | None]:
        owner: dict[int, ast.FunctionDef | ast.AsyncFunctionDef | None] = {}

        def walk(node: ast.AST, fn: ast.FunctionDef | ast.AsyncFunctionDef | None) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, child)
                else:
                    if isinstance(child, ast.Call):
                        owner[id(child)] = fn
                    walk(child, fn)

        walk(tree, None)
        return owner

    def _rel(self, path: Path) -> str:
        return path.relative_to(REPO_ROOT).as_posix()

    def _record(self, path: Path, call: ast.Call, expr: ast.expr) -> None:
        values = self.resolve(expr)
        if values is None:
            self.unresolved.append((self._rel(path), ast.unparse(expr), call.lineno))
            return
        for value in values:
            self.found.setdefault(value, f"{self._rel(path)}:{call.lineno}")

    def _run(self) -> None:
        owners = {path: self._enclosing(tree) for path, tree in self.trees.items()}
        # (Datei, Funktion, Parametername): Hilfsfunktionen, die den Namen nur durchreichen. Jede kann wieder von einer
        # weiteren aufgerufen werden (`_audited` -> `_audit` -> `audit_service.log`).
        queue: list[tuple[Path, str, str]] = []
        seen: set[tuple[Path, str, str]] = set()

        def classify(path: Path, call: ast.Call, expr: ast.expr) -> None:
            owner = owners[path].get(id(call))
            if isinstance(expr, ast.Name) and owner is not None:
                params = [a.arg for a in (*owner.args.posonlyargs, *owner.args.args, *owner.args.kwonlyargs)]
                if expr.id in params:
                    item = (path, owner.name, expr.id)
                    if item not in seen:
                        seen.add(item)
                        queue.append(item)
                    return
            self._record(path, call, expr)

        for path, tree in self.trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _keyword(node, "outcome") is not None:
                    expr = _keyword(node, self.key)
                    if expr is not None:
                        classify(path, node, expr)

        while queue:
            def_path, name, param = queue.pop()
            fn_def = next(
                n for n in ast.walk(self.trees[def_path])
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
                and param in [a.arg for a in (*n.args.posonlyargs, *n.args.args, *n.args.kwonlyargs)]
            )
            positional = [a.arg for a in (*fn_def.args.posonlyargs, *fn_def.args.args)]
            if positional and positional[0] in ("self", "cls"):
                positional = positional[1:]
            index = positional.index(param) if param in positional else None
            for path, tree in self.trees.items():
                local = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
                    if called != name or name == "log":
                        continue
                    # Eine Funktion gleichen Namens im eigenen Modul ist eine andere (jedes Modul hat sein `_audit`);
                    # ohne eigene Definition ist es die importierte.
                    if isinstance(func, ast.Name) and name in local and path != def_path:
                        continue
                    if isinstance(func, ast.Attribute) and name.startswith("_"):
                        continue
                    expr = _keyword(node, param)
                    if expr is None and index is not None and len(node.args) > index:
                        expr = node.args[index]
                    if expr is not None:
                        classify(path, node, expr)


def _ts_block(source: str, start: str) -> str:
    begin = source.index(start)
    end = source.index("\n};", begin)
    return source[begin:end]


def _ts_keys(block: str) -> set[str]:
    """Schlüssel eines TypeScript-Objekts, quoted (`"login.failed": "…"`) oder nackt (`user: "…"`)."""
    return set(re.findall(r'(?:^|[\s,{])"?([A-Za-z0-9_.]+)"?:\s*"', block))


def _labels(constant: str) -> set[str]:
    source = AUDIT_PAGE.read_text(encoding="utf-8")
    return _ts_keys(_ts_block(source, f"const {constant}: Record<string, string> = {{"))


def _expand(scan: _Scan) -> tuple[set[str], list[str]]:
    """Konkrete Namen aus dem Fund, plus die Muster, die in `_dynamic_names()` fehlen."""
    dynamic = _dynamic_names()
    names: set[str] = set()
    missing: list[str] = []
    for name, where in scan.found.items():
        if "*" not in name:
            names.add(name)
        elif name in dynamic:
            names.update(dynamic[name])
        else:
            missing.append(f"{name} ({where})")
    return names, missing


def test_every_audit_action_the_code_writes_has_a_german_label():
    scan = _Scan("action")
    names, missing_patterns = _expand(scan)

    assert not missing_patterns, (
        "Der Code setzt diese Vorgangsnamen aus Werten zusammen. In `_dynamic_names()` die möglichen Werte "
        "eintragen (am besten aus der Tabelle im Code, aus der der Wert stammt): " + ", ".join(missing_patterns)
    )
    unlisted = [
        f"{expr} ({file}:{line})" for file, expr, line in scan.unresolved if (file, expr) not in NOT_A_WRITE
    ]
    assert not unlisted, (
        "Vorgangsname nicht auflösbar. Als Text hinschreiben (auch in Hilfsfunktionen) oder in `NOT_A_WRITE` begründen: "
        + ", ".join(unlisted)
    )

    labels = _labels("ACTION_LABELS")
    unlabelled = sorted(n for n in names if n not in labels)
    assert not unlabelled, (
        "Diese Vorgänge schreibt der Code ins Protokoll, `ACTION_LABELS` in "
        "frontend/src/routes/settings/AuditSettings.tsx hat dafür keine deutsche Beschriftung:\n  "
        + "\n  ".join(f"{n}  ({scan.found.get(n) or 'dynamisch'})" for n in unlabelled)
    )


def test_every_audit_target_type_has_a_german_label():
    scan = _Scan("target_type")
    names, missing_patterns = _expand(scan)
    assert not missing_patterns, missing_patterns

    labels = _labels("TARGET_LABELS")
    unlabelled = sorted(n for n in names if n not in labels)
    assert not unlabelled, (
        "Diese Ziel-Arten schreibt der Code ins Protokoll, `TARGET_LABELS` in "
        "frontend/src/routes/settings/AuditSettings.tsx kennt sie nicht:\n  "
        + "\n  ".join(f"{n}  ({scan.found.get(n)})" for n in unlabelled)
    )


def test_the_scanner_still_finds_the_known_actions():
    """Gegenprobe: Wer den Scanner umbaut und dabei Aufrufe übersieht, soll es hier merken und nicht erst, wenn ein
    Vorgang ohne Beschriftung ausgeliefert wird."""
    scan = _Scan("action")
    names, _ = _expand(scan)
    assert {
        "login.succeeded",           # `audit_service.log(action="…")` im Kern
        "host.created",              # über die Hilfsfunktion `_audit(session, user, "…")`
        "files.upload",              # zwei Stufen: `_audited` -> `_audit` -> `log`
        "host.known_key_pinned",     # Hilfsfunktion aus einem anderen Modul importiert
        "system.restore.applied",    # `"a" if ok else "b"`
        "system.restore.uploaded",   # `restore_service.audit(session, scope, "…")`
        "system.update.requested",   # `_helper_audit(…, "…")`
        "system.update.request_refused",  # Stichwort `audit_action=`
        "terminal.open",
        "service_matrix.image_update",  # `ctx.audit.log` einer Erweiterung
        "shield.quarantine",         # `f"{EXT_ID}.quarantine"`
        "shield.guard_new_port",     # aus `_dynamic_names()`
        "system.update.applied",
    } <= names
    assert len(names) >= 80
    assert _labels("ACTION_LABELS") >= {"login.succeeded", "shield.quarantine"}
    assert _labels("TARGET_LABELS") >= {"user", "host", "restore"}
