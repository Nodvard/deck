"""Wache: Wer auf das Netz oder SSH wartet, hält dabei keine Datenbankverbindung. Erst lesen, dann
`release_connection(session)` (bzw. den `async with`-Block der Session verlassen), dann der Fremddienst."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Teile eines Aufrufs, hinter denen ein Fremddienst steht (SSH, HTTP, Clients der Erweiterungen).
EXT_NETWORK_PARTS = {"http", "exec", "ssh", "sftp"}
EXT_NETWORK_HINTS = ("connector", "client")

# Im Kern: Anbieter der Erweiterungen, SSH und die Prüf- und Testläufe gegen Fremddienste.
CORE_NETWORK_METHODS = {
    "sample", "sample_many", "history", "host_status", "host_actions", "places_for_host", "file_sources",
    "run_check", "run_connection_test", "send_test_message", "check_now",
}
CORE_NETWORK_PREFIXES = ("ssh.",)

# Stellen, die heute noch über einen Fremddienst eine Verbindung halten (Route und Aufruf), jeweils mit Grund.
KNOWN_CORE: dict[str, str] = {
    "api/v1/hosts.py:get_host_status:ssh.get_ssh_pool.get": "`ssh.connect` liest Schlüssel über die Session des "
    "Aufrufers; die Weboberfläche ruft die Route nicht auf",
    "api/v1/hosts.py:get_host_status:ssh.run": "wie oben",
    "api/v1/host_access.py:check_connection:host_check.run_check": "„Verbindung prüfen“ schreibt das Ergebnis in "
    "derselben Transaktion; auf Klick, gedrosselt",
    "api/v1/host_access.py:credential_setup_with_password:host_check.run_check": "Einrichtung per Passwort, einmalig "
    "auf Klick",
    "api/v1/extension_settings.py:test_extension:extension_test.run_connection_test": "„Verbindung testen“ der "
    "Einstellungen, auf Klick",
    "api/v1/extension_settings.py:test_extension:extension_test.send_test_message": "wie oben",
    "api/v1/system.py:update_check:update_check_service.check_now": "„Jetzt nach Updates suchen“, auf Klick, "
    "höchstens einmal je Minute",
}


def _chain(node: ast.AST) -> str:
    parts: list[str] = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Name):
            parts.append(node.id)
            break
        else:
            break
    return ".".join(reversed(parts))


def _awaited_calls(nodes: list[ast.stmt]):
    """Alle `await <Aufruf>` in `nodes`, ohne verschachtelte Funktionen (die laufen später, nicht im Block)."""
    stack: list[ast.AST] = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            yield node
        stack.extend(ast.iter_child_nodes(node))


def _calls(node: ast.AST):
    """Alle Aufrufe in `node`, auch die als Argument eines `await` (etwa in `asyncio.gather` oder `wait_for`)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            yield sub


def _is_ext_network(chain: str) -> bool:
    parts = chain.split(".")
    return bool(EXT_NETWORK_PARTS & set(parts)) or any(h in p.lower() for p in parts for h in EXT_NETWORK_HINTS)


def test_extensions_never_await_the_network_inside_a_session_block():
    found = []
    for path in sorted((ROOT / "extensions").glob("*/src/**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncWith):
                continue
            if not any("session" in _chain(item.context_expr).lower() for item in node.items):
                continue
            for awaited in _awaited_calls(node.body):
                chain = _chain(awaited.value)
                if _is_ext_network(chain):
                    found.append(f"{path.relative_to(ROOT)}:{awaited.lineno}: await {chain}")
    assert not found, "Netzaufruf innerhalb einer DB-Session:\n" + "\n".join(found)


def _is_core_network(chain: str) -> bool:
    return chain.split(".")[-1] in CORE_NETWORK_METHODS or chain.startswith(CORE_NETWORK_PREFIXES)


def _network_in(node: ast.AST, helpers: dict[str, str]) -> str | None:
    """Der erste Netzaufruf in `node`; ein Aufruf einer lokalen Hilfsfunktion, die selbst ins Netz geht, zählt mit."""
    for call in _calls(node):
        chain = _chain(call)
        if _is_core_network(chain):
            return chain
        if chain in helpers:
            return f"{chain}→{helpers[chain]}"
    return None


def _local_network_helpers(tree: ast.Module) -> dict[str, str]:
    helpers: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef):
            inner = _network_in(node, {})
            if inner is not None:
                helpers[node.name] = inner
    return helpers


def _is_release(stmt: ast.stmt) -> bool:
    value = getattr(stmt, "value", None)
    return isinstance(value, ast.Await) and _chain(value.value).endswith("release_connection")


def _released_before(target: ast.AST, func: ast.AsyncFunctionDef, parents: dict[ast.AST, ast.AST]) -> bool:
    """Ob auf dem Weg von `func` zu `target` vorher eine eigene Anweisung `await release_connection(...)` steht; eine
    Freigabe in einem anderen Zweig (`if`/`else`, `try`/`except`) zählt nicht."""
    node = target
    while node is not func:
        parent = parents[node]
        for field in ("body", "orelse", "finalbody"):
            block = getattr(parent, field, None)
            if isinstance(block, list) and node in block and any(_is_release(s) for s in block[: block.index(node)]):
                return True
        node = parent
    return False


def _core_routes_holding_a_connection() -> dict[str, str]:
    found: dict[str, str] = {}
    api = ROOT / "backend" / "src" / "nodvard_deck" / "api"
    for path in sorted(api.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        helpers = _local_network_helpers(tree)
        for func in tree.body:
            if not isinstance(func, ast.AsyncFunctionDef):
                continue
            if not any("router." in ast.unparse(d) for d in func.decorator_list):
                continue
            args = func.args.args + func.args.kwonlyargs
            sessions = {a.arg for a in args if a.annotation is not None and "SessionDep" in ast.unparse(a.annotation)}
            if not sessions:
                continue
            # Die Anmeldung gibt ihre Verbindung frei; gehalten wird erst ab der ersten Nutzung in der Route.
            uses = [n.lineno for n in ast.walk(func) if isinstance(n, ast.Name) and n.id in sessions]
            first_use = min(uses, default=None)
            parents = {child: parent for parent in ast.walk(func) for child in ast.iter_child_nodes(parent)}
            for awaited in sorted(_awaited_calls(func.body), key=lambda a: (a.lineno, a.col_offset)):
                if first_use is None or awaited.lineno < first_use:
                    continue
                chain = _network_in(awaited, helpers)
                if chain is not None and not _released_before(awaited, func, parents):
                    key = f"{path.relative_to(api.parent).as_posix()}:{func.name}:{chain}"
                    found.setdefault(key, f"{key} (Zeile {awaited.lineno})")
    return found


def test_core_routes_release_the_connection_before_the_network():
    new = [line for key, line in _core_routes_holding_a_connection().items() if key not in KNOWN_CORE]
    assert not new, (
        "Route wartet auf einen Fremddienst und hält dabei eine DB-Verbindung; vorher "
        "`await release_connection(session)`:\n" + "\n".join(new)
    )


def test_known_cases_still_exist():
    stale = sorted(set(KNOWN_CORE) - set(_core_routes_holding_a_connection()))
    assert not stale, f"Erledigt, bitte aus KNOWN_CORE streichen: {stale}"
