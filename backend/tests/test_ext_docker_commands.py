"""Befehls-Baukasten des Docker-Moduls: dieselben Befehle wie bisher, nur geprüfte Werte, kein Aufruf baut selbst."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "docker"
SRC = EXT_DIR / "src" / "nodvard_deck_ext_docker"


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _commands():
    from nodvard_deck_ext_docker import commands

    return commands


# Zeichen für Zeichen die Befehle, die das Modul bisher geschickt hat.
SAME_AS_BEFORE = [
    (lambda c: c.ps_all(), (
        "docker ps -a --format '{{.ID}}|{{.Names}}|{{.State}}|{{.Status}}|{{.Image}}"
        '|{{.Label "com.docker.compose.project"}}|{{.Ports}}|{{.Label "com.docker.compose.project.working_dir"}}\''
    )),
    (lambda c: c.stats(), "docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}'"),
    (lambda c: c.port_bindings(["abc123def456", "0" * 64]),
     f"docker inspect --type container --format '{{{{.Id}}}}|{{{{json .HostConfig.PortBindings}}}}' abc123def456 {'0' * 64}"),
    (lambda c: c.system_df(), "docker system df --format '{{json .}}'"),
    (lambda c: c.images(), "docker images --format '{{json .}}'"),
    (lambda c: c.inspect_container("web.1"), "docker inspect --type container web.1"),
    (lambda c: c.container_id("web"), "docker inspect -f '{{.Id}}' web"),
    (lambda c: c.container_full_id("web"), "docker inspect --type container -f '{{.Id}}' web"),
    (lambda c: c.container_id_state("web"), "docker inspect --type container -f '{{.Id}}|{{.State.Status}}' web"),
    (lambda c: c.container_verb("restart", "web"), "docker restart web"),
    (lambda c: c.container_verb("rm", "f" * 64), f"docker rm {'f' * 64}"),
    (lambda c: c.logs("web", 200, follow=True), "docker logs --tail 200 --timestamps --follow web"),
    (lambda c: c.logs("web", 0, follow=False), "docker logs --tail 0 --timestamps web"),
    (lambda c: c.compose_restart("my_stack-2"), "docker compose -p my_stack-2 restart"),
    (lambda c: c.prune("docker.prune_images"), "docker image prune -f"),
    (lambda c: c.prune("docker.prune_unused_images"), "docker image prune -a -f"),
    (lambda c: c.prune("docker.prune_build_cache"), "docker builder prune -f"),
    (lambda c: c.shell("nginx-1"),
     "docker exec -it nginx-1 sh -c 'if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi'"),
]


@pytest.mark.parametrize(("build", "expected"), SAME_AS_BEFORE)
def test_the_commands_are_the_same_as_before(build, expected):
    assert build(_commands()) == expected


BAD_NAMES = ["x; rm -rf /", "$(reboot)", "-u0", "--rm", "a b", "", "web\n", "web`id`", "../x", "a" * 256]


@pytest.mark.parametrize("bad", BAD_NAMES)
def test_every_name_is_checked(bad):
    c = _commands()
    for build in (c.inspect_container, c.container_id, c.container_full_id, c.container_id_state, c.shell,
                  lambda n: c.container_verb("stop", n), lambda n: c.logs(n, 10, follow=False)):
        with pytest.raises(ValueError):
            build(bad)


@pytest.mark.parametrize(
    "build",
    [
        lambda c: c.container_verb("kill", "web"),
        lambda c: c.container_verb("rm -f", "web"),
        lambda c: c.logs("web", -1, follow=False),
        lambda c: c.logs("web", 5001, follow=False),
        lambda c: c.logs("web", True, follow=False),
        lambda c: c.logs("web", "10; id", follow=False),
        lambda c: c.compose_restart("Shop"),
        lambda c: c.compose_restart("shop; id"),
        lambda c: c.compose_restart("-x"),
        lambda c: c.port_bindings([]),
        lambda c: c.port_bindings(["abc123def456; id"]),
        lambda c: c.port_bindings(["ABC123DEF456"]),
        lambda c: c.port_bindings(["-abc123def456"]),
    ],
)
def test_other_values_are_checked_too(build):
    with pytest.raises(ValueError):
        build(_commands())


def test_an_unknown_cleanup_has_no_command():
    assert _commands().prune("docker.prune_volumes") is None


# --- Wächter: kein Aufruf baut seinen Befehl selbst ------------------------------------------------------------------

_BUILDER_NAME = re.compile(r"^(?:cmd_\w+|\w+_command)$")
"""Die Bauer außerhalb von `commands.py` (`image_apply`, `detached`, `after_check`, `image_updates`) prüfen und quoten
selbst; ihre eigenen Tests decken das ab."""
_CHANGING = {"container_verb", "compose_restart", "prune", "image_remove", "volume_remove", "network_remove"}
"""Bauer für ändernde Befehle: nur in den Ausführern der Gate-Aktionen."""
_PASS_THROUGH = {"_exec"}
"""Hüllen, die ihren Befehl nur weiterreichen; ihre Aufrufer prüft der Wächter selbst."""


def _callee(node: ast.Call) -> tuple[str | None, str]:
    """(Modul-Alias oder None, Funktionsname) eines Aufrufs."""
    func = node.func
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else None
        return owner, func.attr
    if isinstance(func, ast.Name):
        return None, func.id
    return None, ""


def _is_builder_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    owner, name = _callee(node)
    if name.startswith("_"):
        return False
    return owner == "commands" or bool(_BUILDER_NAME.match(name))


def _is_exec(node: ast.AST) -> bool:
    """`….exec` oder `….exec.run` usw.: so ein Wert in einer Variable würde den Wächter umgehen."""
    while isinstance(node, ast.Attribute):
        if node.attr == "exec":
            return True
        node = node.value
    return False


def _command_arg(call: ast.Call) -> ast.AST | None:
    """Das Befehls-Argument von `ctx.exec.run/stream(host, cmd)`, `self._exec(host, cmd, ...)` und `open_shell(command=)`."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    on_exec = isinstance(func.value, ast.Attribute) and func.value.attr == "exec"
    if (on_exec and func.attr in ("run", "stream")) or func.attr == "_exec":
        if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
            return call  # `*args`/`**kwargs`: der Befehl ist nicht zu sehen
        if len(call.args) >= 2:
            return call.args[1]
        return next((k.value for k in call.keywords if k.arg == "command"), None)
    if on_exec and func.attr == "open_shell":
        return next((k.value for k in call.keywords if k.arg == "command"), None)
    return None


def _assignments(function: ast.AST, name: str) -> list[ast.AST]:
    return [
        node.value for node in ast.walk(function)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    ]


def _violations(tree: ast.Module) -> list[str]:
    found: list[str] = []
    seen: set[int] = set()
    constants = {
        t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets
        if isinstance(t, ast.Name) and t.id.startswith("CMD_")
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and _is_exec(node.value):
            found.append(f"Zeile {node.lineno}: Alias auf exec")
    # Von innen nach außen: ein Aufruf in einer verschachtelten Funktion zählt bei der innersten.
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for function in reversed(functions):
        for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
            arg = _command_arg(call)
            if arg is None or id(call) in seen:
                continue
            seen.add(id(call))
            ok = _is_builder_call(arg)
            if isinstance(arg, ast.Name):
                values = _assignments(function, arg.id)
                parameter = arg.id in {a.arg for a in function.args.args} and function.name in _PASS_THROUGH
                changed = any(
                    isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and n.target.id == arg.id
                    for n in ast.walk(function)
                )
                built = bool(values) and all(_is_builder_call(v) for v in values) and not changed
                ok = parameter or (arg.id in constants and not values) or built
            if not ok:
                found.append(f"Zeile {call.lineno}: {ast.unparse(arg)[:80]}")
    executors = {
        id(n) for c in ast.walk(tree) if isinstance(c, ast.ClassDef) and c.name.endswith("Executor") for n in ast.walk(c)
    }
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        owner, name = _callee(call)
        if owner == "commands" and name in _CHANGING and id(call) not in executors:
            found.append(f"Zeile {call.lineno}: ändernder Befehl {name} außerhalb eines Ausführers")
    return found


@pytest.mark.parametrize("path", sorted(SRC.glob("*.py")), ids=lambda p: p.name)
def test_no_call_builds_its_own_command(path):
    assert _violations(ast.parse(path.read_text(encoding="utf-8"))) == []


def test_the_guard_finds_self_built_commands():
    bad = '''
async def f(ctx, host, name, tail):
    await ctx.exec.run(host, f"docker logs {name}")
    await ctx.exec.run(host, "docker stop " + name)
    await ctx.exec.run(host, "docker ps")
    command = f"docker rm {name}"
    await ctx.exec.run(host, command)
    async with ctx.exec.stream(host, "docker logs -f " + name):
        pass
    await ctx.exec.open_shell(host, cols=80, rows=24, command=f"docker exec -it {name} sh")
    await self._exec(host, f"docker inspect {name}", 20)
    await ctx.exec.run(host, commands.container_verb("stop", name))

async def run_it(ctx, host, command):
    await ctx.exec.run(host, command)

async def tricks(self, ctx, host, name, params):
    run = ctx.exec.run
    await run(host, f"docker rm {name}")
    CMD_X = f"docker rm {name}"
    await ctx.exec.run(host, CMD_X)
    await ctx.exec.run(host, commands._docker("rm", "-f", name))
    command = commands.ps_all()
    command += f" {name}"
    await ctx.exec.run(host, command)
    await ctx.exec.run(**params)
'''
    found = _violations(ast.parse(bad))
    assert len(found) == 14, found
    assert "Zeile 18: Alias auf exec" in found
    assert "Zeile 15: command" in found
    assert "Zeile 12: ändernder Befehl container_verb außerhalb eines Ausführers" in found


def test_the_guard_lets_builders_through():
    good = '''
CMD_LIST = "docker ps -q"

class DockerContainerExecutor:
    async def execute(self, ctx, host, name, ids):
        await ctx.exec.run(host, commands.container_verb("stop", name))
        await ctx.exec.run(host, ia.cmd_verify(name))
        command = dt.poll_command(name)
        await ctx.exec.run(host, command)
        await self._exec(host, CMD_LIST, 20)
        await self._exec(host, cmd_inspect_containers(ids), 20)

    async def _exec(self, host, command, timeout_s):
        return await self._ctx.exec.run(host, command, timeout_s=timeout_s)
'''
    assert _violations(ast.parse(good)) == []
