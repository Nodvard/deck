"""Befehls-Baukasten des Docker-Moduls: dieselben Befehle wie bisher, nur geprüfte Werte, kein Aufruf baut selbst."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "docker"
SRC = EXT_DIR / "src" / "nodvard_deck_ext_docker"


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
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
_CHANGING_ELSEWHERE = {
    "cmd_verb", "cmd_take_down", "cmd_remove_folder", "cmd_stack_pull", "cmd_stack_up", "cmd_tag", "cmd_untag", "cmd_archive", "cmd_stop_services",
    "cmd_start_services", "cmd_pull_backup_image", "cmd_stop_archive", "cmd_read", "cmd_move_up", "cmd_stop_old", "cmd_rename_old",
    "cmd_start_old", "cmd_remove_old", "cmd_login", "cmd_logout", "cmd_restore", "cmd_restart_services",
}
"""Dasselbe für ändernde Bauer außerhalb von `commands.py` (Stack-Aktionen, Löschen und Update von Stacks)."""
_OWNER_WRITES = {"cmd_owner_write", "cmd_owner_mkdir", "cmd_owner_remove", "cmd_owner_rmdir"}
"""Schreiben mit den Rechten des Ordner-Besitzers: nur in `OwnerFiles`; wer deren Schreibmethoden aufruft, prüft der SFTP-Wächter."""
_PASS_THROUGH = {"_exec", "_call"}
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
    """Das Befehls-Argument von `ctx.exec.run/stream(host, cmd)`, `self._exec`/`self._call(host, cmd, ...)` und `open_shell(command=)`."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    on_exec = isinstance(func.value, ast.Attribute) and func.value.attr == "exec"
    if (on_exec and func.attr in ("run", "stream", "run_argv", "stream_argv")) or func.attr in _PASS_THROUGH:
        if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
            return call  # `*args`/`**kwargs`: der Befehl ist nicht zu sehen
        if len(call.args) >= 2:
            return call.args[1]
        return next((k.value for k in call.keywords if k.arg in ("command", "argv")), call)
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
    owner_files = {id(n) for c in ast.walk(tree) if isinstance(c, ast.ClassDef) and c.name == "OwnerFiles" for n in ast.walk(c)}
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        owner, name = _callee(call)
        if ((owner == "commands" and name in _CHANGING) or name in _CHANGING_ELSEWHERE) and id(call) not in executors:
            found.append(f"Zeile {call.lineno}: ändernder Befehl {name} außerhalb eines Ausführers")
        if name in _OWNER_WRITES and id(call) not in owner_files:
            found.append(f"Zeile {call.lineno}: Schreibbefehl {name} außerhalb von OwnerFiles")
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
    await ctx.exec.run_argv(host, ["docker", "rm", name])
    await ctx.exec.run_argv(host, sa.cmd_verb(stack, "docker.stack_down"))

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
    await ctx.exec.run_argv(host, argv=["docker", "rm", name])
'''
    found = _violations(ast.parse(bad))
    assert len(found) == 17, found
    assert "Zeile 29: ['docker', 'rm', name]" in found
    assert any("ändernder Befehl cmd_verb" in f for f in found)
    assert "Zeile 20: Alias auf exec" in found
    assert "Zeile 17: command" in found
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
        await ctx.exec.run_argv(host, sa.cmd_verb(stack, "docker.stack_up"))
        await ctx.exec.run_argv(host, argv=sa.cmd_services(stack))

    async def _exec(self, host, command, timeout_s):
        return await self._ctx.exec.run(host, command, timeout_s=timeout_s)
'''
    assert _violations(ast.parse(good)) == []


# --- Wächter: Schreiben per SFTP nur in den Ausführern ----------------------------------------------------------------

_SFTP_READING = {
    "stat", "lstat", "listdir", "readdir", "scandir", "exists", "lexists", "isdir", "isfile", "islink", "realpath", "readlink",
    "getcwd", "glob", "statvfs", "getsize", "getatime", "getmtime",
}
"""Erlaubnisliste: alles andere auf einem SFTP-Zugang zählt als Schreiben, auch Methoden, die es heute noch nicht gibt."""
_FILE_CLASSES = {"SftpFiles", "OwnerFiles"}
"""Schreiben Dateien im Auftrag der Ausführer über `self._sftp` oder feste Befehle; ihre Schreibmethoden heißen wie `_IO_WRITES`."""
_IO_WRITES = {"write", "mkdir", "remove", "rmdir"}
"""Schreibmethoden auf `io` bzw. `….io` (ein `SftpFiles` oder `OwnerFiles`): zählen wie Schreiben per SFTP."""


def _sftp_names(tree: ast.AST) -> set[str]:
    """`sftp` und jeder Name aus `async with ….sftp(…) as <Name>`."""
    names = {"sftp"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                call = item.context_expr
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "sftp"
                        and isinstance(item.optional_vars, ast.Name)):
                    names.add(item.optional_vars.id)
    return names


def _sftp_writes(node: ast.AST, names: set[str] | None = None) -> list[ast.Call]:
    """Aufrufe auf einem SFTP-Zugang, die nicht nur lesen; `open` nur mit `r`/`b` im Modus gilt als lesend."""
    names = names if names is not None else _sftp_names(node)
    found = []
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        func = call.func
        if not isinstance(func, ast.Attribute):
            continue
        receiver = func.value
        if (isinstance(receiver, ast.Name) and receiver.id == "io") or (isinstance(receiver, ast.Attribute) and receiver.attr == "io"):
            if func.attr in _IO_WRITES:
                found.append(call)
            continue
        if not ((isinstance(receiver, ast.Name) and receiver.id in names) or (isinstance(receiver, ast.Attribute) and receiver.attr == "_sftp")):
            continue
        if func.attr == "open":
            mode = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg in ("pflags_or_mode", "mode")), None)
            if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and set(mode.value) <= {"r", "b"}):
                found.append(call)
        elif func.attr not in _SFTP_READING:
            found.append(call)
    return found


def _sftp_violations(trees: dict[str, ast.Module]) -> list[str]:
    """Ein schreibender SFTP-Aufruf steht in einem Ausführer oder in einer Funktion des Moduls, die nur Ausführer (oder
    andere solche Funktionen) aufrufen."""
    executors: set[int] = set()
    writers: dict[tuple[str, str], ast.AST] = {}
    for name, tree in trees.items():
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef) and (n.name.endswith("Executor") or n.name in _FILE_CLASSES)):
            executors.update(id(n) for n in ast.walk(cls))
        names = _sftp_names(tree)
        for fn in (n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            if _sftp_writes(fn, names):
                writers[(name, fn.name)] = fn
    allowed = set(executors)
    for fn in writers.values():
        allowed.update(id(n) for n in ast.walk(fn))
    found: list[str] = []
    for name, tree in trees.items():
        found += [f"{name} Zeile {call.lineno}: sftp.{call.func.attr} außerhalb eines Ausführers" for call in _sftp_writes(tree) if id(call) not in allowed]
    # Jede schreibende Hilfsfunktion wird nur aufgerufen, und zwar aus Ausführern oder anderen schreibenden Hilfsfunktionen:
    # nicht dekoriert (etwa als Route), nicht als Wert weitergereicht und nicht ungenutzt.
    calls: dict[tuple[str, str], int] = {key: 0 for key in writers}
    for name, tree in trees.items():
        called = {id(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call)}
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            owner, callee = _callee(call)
            key = (f"{owner}.py" if owner else name, callee)
            if key in writers:
                calls[key] += 1
                if id(call) not in allowed:
                    found.append(f"{name} Zeile {call.lineno}: {callee} schreibt per SFTP und wird außerhalb eines Ausführers aufgerufen")
        for ref in ast.walk(tree):
            target = (name, ref.id) if isinstance(ref, ast.Name) else (f"{ref.value.id}.py", ref.attr) if (
                isinstance(ref, ast.Attribute) and isinstance(ref.value, ast.Name)) else None
            if target in writers and id(ref) not in called and isinstance(getattr(ref, "ctx", None), ast.Load):
                found.append(f"{name} Zeile {ref.lineno}: {target[1]} schreibt per SFTP und wird als Wert weitergereicht")
    for (name, fn_name), fn in writers.items():
        if fn.decorator_list:
            found.append(f"{name} Zeile {fn.lineno}: {fn_name} schreibt per SFTP und ist dekoriert")
        elif not calls[(name, fn_name)]:
            found.append(f"{name} Zeile {fn.lineno}: {fn_name} schreibt per SFTP und wird von keinem Ausführer aufgerufen")
    return found


_IO_MAKERS = {"owner_files", "SftpFiles", "OwnerFiles"}
"""Liefern einen Schreibzugang; die beiden Klassen baut nur `owner_files`."""


def _is_io(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "io") or (isinstance(node, ast.Attribute) and node.attr == "io")


def _io_violations(tree: ast.Module) -> list[str]:
    """Ein Schreibzugang heißt überall `io` oder `….io`; sonst sähe `_sftp_writes` seine Schreibaufrufe nicht."""
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    placed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                same = isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple) and len(target.elts) == len(node.value.elts)
                for t, v in zip(target.elts, node.value.elts) if same else [(target, node.value)]:
                    if _is_io(t):
                        placed.add(id(v.value if isinstance(v, ast.Await) else v))
    if "owner_files" in functions:
        placed.update(id(r.value) for r in ast.walk(functions["owner_files"]) if isinstance(r, ast.Return) and r.value is not None)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node)[1] in _IO_MAKERS and id(node) not in placed:
            found.append(f"Zeile {node.lineno}: {_callee(node)[1]} ohne Zuweisung an io")
        if not (_is_io(node) and isinstance(node.ctx, ast.Load)) or id(node) in placed:
            continue
        parent = parents.get(id(node))
        if isinstance(parent, ast.Attribute):
            called = parents.get(id(parent))
            if parent.attr in _IO_WRITES and not (isinstance(called, ast.Call) and called.func is parent):
                found.append(f"Zeile {node.lineno}: io.{parent.attr} als Wert")
            continue
        call, key = (parents.get(id(parent)), parent.arg) if isinstance(parent, ast.keyword) else (parent, None)
        if isinstance(call, ast.Call) and (key is not None or node in call.args) and _callee(call)[0] is None:
            fn = functions.get(_callee(call)[1])
            params = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)] if fn else []
            name = key if key is not None else (params[call.args.index(node)] if call.args.index(node) < len(params) else None)
            if fn is not None and name == "io" and "io" in params:
                continue
        alias = isinstance(parent, (ast.Assign, ast.AnnAssign)) or isinstance(parents.get(id(parent)), (ast.Assign, ast.AnnAssign))
        found.append(f"Zeile {node.lineno}: Alias auf io" if alias else f"Zeile {node.lineno}: io weitergereicht, ohne dass es dort io heißt")
    return found


def test_writers_go_by_the_name_io_everywhere():
    for path in sorted(SRC.glob("*.py")):
        assert _io_violations(ast.parse(path.read_text(encoding="utf-8"))) == [], path.name


def test_the_file_classes_have_only_the_known_methods():
    tree = ast.parse((SRC / "git_apply.py").read_text(encoding="utf-8"))
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in _FILE_CLASSES):
        public = {f.name for f in cls.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and not f.name.startswith("_")}
        assert public == {"read"} | _IO_WRITES, f"{cls.name}: neue Methode lesend? Sonst in _IO_WRITES aufnehmen"


def test_the_io_guard_finds_detours():
    found = _io_violations(ast.parse('''
async def owner_files(ctx, host, sftp, attrs):
    return SftpFiles(sftp, type(attrs))

async def helper(fs, path):
    await fs.write(path, 0o644, b"")

async def proper(sftp, io, path):
    await io.mkdir(path)

class ApplyExecutor:
    async def execute(self, plan, sftp, host, path):
        fs = plan.io
        w = plan.io.write
        await helper(plan.io, path)
        await proper(sftp, plan.io, path)
        await proper(sftp, io=plan.io, path=path)
        await (await owner_files(None, host, sftp, None)).write(path, 0o644, b"")
        files = OwnerFiles(None, host, None)
        plan.io = await owner_files(None, host, sftp, None)
        ok, io = True, plan.io
        return [plan.io]
'''))
    assert sorted(found) == [
        "Zeile 13: Alias auf io",
        "Zeile 14: io.write als Wert",
        "Zeile 15: io weitergereicht, ohne dass es dort io heißt",
        "Zeile 18: owner_files ohne Zuweisung an io",
        "Zeile 19: OwnerFiles ohne Zuweisung an io",
        "Zeile 22: io weitergereicht, ohne dass es dort io heißt",
    ], found


def test_sftp_writes_only_in_executors():
    trees = {path.name: ast.parse(path.read_text(encoding="utf-8")) for path in sorted(SRC.glob("*.py"))}
    assert _sftp_violations(trees) == []


def test_the_sftp_guard_finds_writes_elsewhere():
    bad = ast.parse('''
async def helper(sftp, path):
    await sftp.posix_rename(path, path + ".x")

class Routes:
    async def save(self, sftp, path):
        await helper(sftp, path)
        async with sftp.open(path, "wb") as f:
            pass
        await sftp.chmod(path, 0o777)

class SaveExecutor:
    async def execute(self, sftp, path):
        await helper(sftp, path)
        async with sftp.open(path, "rb") as f:
            pass
        await sftp.lstat(path)

async def sneaky(ctx, host, path):
    async with ctx.exec.sftp(host) as conn:
        await conn.unlink(path)
        await conn.put("lokal", path)
        await conn.stat(path)

@router.post("/x")
async def route(sftp, path):
    await sftp.remove(path)

callbacks = [helper]
''')
    found = _sftp_violations({"bad.py": bad})
    assert found == [
        "bad.py Zeile 8: sftp.open außerhalb eines Ausführers",
        "bad.py Zeile 10: sftp.chmod außerhalb eines Ausführers",
        "bad.py Zeile 7: helper schreibt per SFTP und wird außerhalb eines Ausführers aufgerufen",
        "bad.py Zeile 29: helper schreibt per SFTP und wird als Wert weitergereicht",
        "bad.py Zeile 19: sneaky schreibt per SFTP und wird von keinem Ausführer aufgerufen",
        "bad.py Zeile 26: route schreibt per SFTP und ist dekoriert",
    ], found


def test_the_guards_know_the_writers_of_stacks_from_git():
    commands = ast.parse('''
class OwnerFiles:
    async def write(self, path):
        await self._call(self._host, cmd_owner_write(self.owner, path, 0o644, path))

class Routes:
    async def save(self, host, owner, stack, path):
        await self._call(host, cmd_owner_remove(owner, path))
        await self._ctx.exec.run_argv(host, cmd_restart_services(stack, ["web"]))
''')
    assert set(_violations(commands)) == {
        "Zeile 8: Schreibbefehl cmd_owner_remove außerhalb von OwnerFiles",
        "Zeile 9: ändernder Befehl cmd_restart_services außerhalb eines Ausführers",
    }
    files = ast.parse('''
class SftpFiles:
    async def remove(self, path):
        await self._sftp.remove(path)

class Helper:
    async def drop(self, path):
        await self._sftp.remove(path)

async def make(io, path):
    await io.mkdir(path)

class Routes:
    async def save(self, plan, path):
        await plan.io.write(path, 0o644, b"")
        await make(plan.io, path)
        await plan.io.read(path, 10)

class ApplyExecutor:
    async def execute(self, io, path):
        await make(io, path)
        await io.write(path, 0o644, b"")
''')
    assert set(_sftp_violations({"bad.py": files})) == {
        "bad.py Zeile 8: sftp.remove außerhalb eines Ausführers",
        "bad.py Zeile 15: sftp.write außerhalb eines Ausführers",
        "bad.py Zeile 16: make schreibt per SFTP und wird außerhalb eines Ausführers aufgerufen",
    }
