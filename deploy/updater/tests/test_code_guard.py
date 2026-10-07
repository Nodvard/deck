"""Code-Waechter fuer den Update-Helfer.

Der Helfer laeuft mit Zugriff auf den Docker-Socket, also mit so viel Macht wie root auf dem Rechner. Darum
prueft dieser Test ueber den Syntaxbaum **jedes** `*.py` im Paket `nodvard_deck_updater` -- auch Module, die
spaeter dazukommen (engine, target, clone, flow, __main__), ohne dass jemand hier etwas eintragen muss. Er ist eine
**Erlaubnisliste**, keine Verbotsliste: was nicht ausdruecklich erlaubt ist, faellt auf.

1. **Nur erlaubte Module** (`ALLOWED_MODULES`, vollstaendige Namen; `sys.stdlib_module_names` und das eigene Paket
   zusaetzlich). Ein neues Modul muss bewusst und mit Begruendung eingetragen werden -- das faellt in der Durchsicht
   auf. Nie erlaubt (`FORBIDDEN_MODULES`, ein Test haelt beide Listen getrennt): Programmstart, Code nachladen oder
   ausfuehren (auch `timeit`, `doctest`, `pydoc`, `pkgutil`), Netz ueber `AF_UNIX` hinaus (auch `logging.handlers`,
   `_socket`), und alles, was Dateien an `os.open` vorbei oeffnet (`io`, `pathlib`, `shutil`, `tempfile`, ...).
   Module bekommen keinen Alias (`import socket as so` wuerde die Pruefung am Namen umgehen).
2. **Dateien nur ueber `os.open`**, immer mit `O_NOFOLLOW` und relativ zu einem Ordner-Deskriptor (`dir_fd=`; nur die
   Wurzel selbst, `O_DIRECTORY`, und `/proc/...` ohne). `O_NOFOLLOW` wirkt nur auf die letzte Pfadkomponente, ein
   absoluter Pfad `/channel/requests/x.json` waere also kein Schutz. Die pfadbasierten `os`-Funktionen (`stat`,
   `unlink`, `rename`, `chmod`, ...) verlangen `dir_fd=` (und `follow_symlinks=False`), `os.scandir`/`os.listdir`
   nur einen Deskriptor (eine Variable `...fd`); `os.fdopen`, `os.system`, `os.exec*`, `os.spawn*`, `os.fork*`,
   `os.kill*`, `os.walk`, `os.chdir` und `os.path.exists`/`realpath`/... sind verboten, ebenso `open()` und
   `eval`/`exec`/`compile`/`__import__`/`breakpoint`/`globals`/`locals`/`vars`/`input`.
3. **Netz nur ueber `AF_UNIX`:** keine andere Adressfamilie (`AF_INET`, `AF_INET6`, ...), `socket.socket()` nur als
   Aufruf mit `socket.AF_UNIX` (der Name `socket.socket` darf nirgends sonst vorkommen: keine Zuweisung an eine
   Variable, keine Basisklasse, kein `SocketType`, kein `from socket import socket`), kein `create_connection`,
   `getaddrinfo`, `HTTPSConnection`, `urlopen`. `HTTPConnection` darf nur als Basisklasse vorkommen; die Unterklasse
   muss `connect()` selbst schreiben, darin `self.sock = ...` aus einem eigenen `socket.socket(AF_UNIX)` setzen
   und darf nie `super()` benutzen (`HTTPConnection.connect` verbindet per TCP).
4. **Umgebung:** gelesen wird nur `os.environ.get(SERVICE_ENV)` (`NODVARD_DECK_UPDATER_SERVICE`), und `SERVICE_ENV`
   ist genau einmal als Text in `policy.py` gesetzt und wird nirgends neu gebunden. Das Repository ist eine Konstante,
   die nur in `policy.py` und nur einmal gesetzt wird; der Text `ghcr.io/nodvard/deck` steht sonst nirgends im Code
   (Docstrings ausgenommen). Es stimmt mit `release.yml` (und, sobald vorhanden, mit `core.updates.OFFICIAL_IMAGE`
   im Dashboard) ueberein. `REPOSITORY` und `SERVICE_ENV` werden weder per Attribut noch per `setattr` noch ueber
   `__dict__`, `sys.modules`, `globals()` o. a. ueberschrieben.
5. **Reflexion:** `getattr`/`setattr`/`delattr` nur mit `self`/`cls` als erstem Argument (und nie als Wert
   weitergereicht, z. B. an `functools.reduce`), keine Attribute wie `__dict__`, `__builtins__`, `__globals__`,
   `__subclasses__`, `f_globals`, `_getframe`, `sys.modules`, `attrgetter`, ...
6. **Keine gefaehrlichen Engine-Pfade im Code:** `/exec`, `/archive`, `/build`, `/commit`, `/images/load`,
   `/volumes`, `/swarm`, `/plugins`, `/session`, `v=1`/`v=true`, `force=1`/`force=true`.

**Was ein Syntaxbaum-Waechter prinzipiell nicht kann:** er sieht keine Typen und keine Laufzeitwerte. Wer es darauf
anlegt (`operator.attrgetter`-artige Umwege, Zeichenketten fuer Attributnamen ueber Formatstrings, ein Objekt, dessen
Methode `open` heisst, Code in einer Datei, die das Paket zur Laufzeit nachlaedt, ...), kommt immer vorbei. Der
Waechter schuetzt vor **Versehen** bei spaeteren Aenderungen (und macht Absicht auffaellig), nicht vor einem
absichtlichen Angreifer im Code-Review. Die tatsaechliche Grenze setzen das Image (nur das Paket, kein pip, kein
Compiler), `read_only`, `cap_drop: ALL`, `network_mode: none` und die Allowlist der Engine-Pfade.

Dazu ein Lauf mit `python -I -S`: das Paket laesst sich ohne `site-packages` importieren, und Umgebungsvariablen
aendern das Repository nicht.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
import textwrap

import pytest
from nodvard_deck_updater import policy
from updater_support import UPDATER_DIR

PACKAGE = "nodvard_deck_updater"
PACKAGE_DIR = UPDATER_DIR / PACKAGE
REPO_ROOT = UPDATER_DIR.parents[1]
MODULES = sorted(PACKAGE_DIR.rglob("*.py"))

ALLOWED_MODULES = frozenset({
    "__future__",
    # Typen und Datenstrukturen
    "typing", "dataclasses", "enum", "collections", "collections.abc", "contextlib", "functools", "itertools",
    # Text und Zahlen: Protokoll-JSON, Muster, isfinite
    "json", "re", "math",
    # Dateien nur ueber os.open + dir_fd, Sperre (fcntl), Zufallsnamen fuer Temp-Dateien (secrets)
    "os", "os.path", "stat", "errno", "fcntl", "secrets",
    # Uhr, SIGTERM, Argumente/Ausgabe, Heartbeat-Thread
    "time", "signal", "sys", "threading",
    # Engine-API ueber AF_UNIX: ein eigener Client, nichts anderes
    "socket", "http.client", "urllib.parse",
})
"""Alles, was das Paket braucht oder fuer `engine`, `target`, `clone`, `flow` und `__main__` brauchen
wird. Bewusst **nicht** dabei: `logging` (JSON-Zeilen laufen ueber `json` und `sys.stdout`; `logging.handlers`/
`.config` und `FileHandler` koennen senden bzw. Code ausfuehren), `uuid`/`hashlib`/`base64` (die Signatur kommt
spaeter mit `cryptography`), `datetime`, `argparse`, `select`, `copy`, `traceback`. Wer eins braucht, traegt es hier
mit Begruendung ein."""

FORBIDDEN_MODULES = frozenset({
    # Programme starten
    "subprocess", "pty", "multiprocessing", "asyncio", "concurrent", "_posixsubprocess",
    # Code nachladen / ausfuehren / deserialisieren
    "importlib", "imp", "runpy", "code", "codeop", "builtins", "pickle", "_pickle", "pickletools", "copyreg",
    "marshal", "shelve", "dbm", "ctypes", "_ctypes", "pdb", "bdb", "trace", "zipimport", "webbrowser",
    "timeit", "cProfile", "profile", "doctest", "pydoc", "pkgutil", "zipapp", "venv", "ensurepip", "sqlite3",
    "unittest", "operator",
    # Netz (ausser AF_UNIX ueber socket/http.client)
    "ssl", "_ssl", "_socket", "ftplib", "smtplib", "poplib", "imaplib", "nntplib", "telnetlib", "socketserver",
    "xmlrpc", "http.server", "http.cookiejar", "urllib.request", "urllib.robotparser", "wsgiref", "mailbox",
    "logging.config", "logging.handlers",
    # Dateien an os.open vorbei (folgen Symlinks)
    "io", "_io", "pathlib", "shutil", "tempfile", "fileinput", "glob", "linecache", "codecs", "gzip", "bz2",
    "lzma", "zipfile", "tarfile", "mmap", "filecmp", "fnmatch",
})
"""Nie erlaubt, auch nicht per Eintrag in `ALLOWED_MODULES` (`test_allowlist_and_forbidden_list_are_disjoint`)."""

FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "breakpoint", "globals", "locals", "vars", "__builtins__",
    "input", "open",
}
FORBIDDEN_ATTR_RE = re.compile(
    r"system|popen|fork|forkpty|vfork|exec[lv]p?e?|spawn[lv]p?e?|posix_spawnp?|startfile|kill|killpg"
    r"|pthread_kill|raise_signal|pidfd_send_signal"
    r"|read_text|read_bytes|write_text|write_bytes|load_module|exec_module"
    # Dateien an os.open vorbei und Pfad-Operationen ohne Deskriptor
    r"|fdopen|chdir|fchdir|chroot|walk|fwalk|makedirs|removedirs|renames|symlink|link|mknod|truncate|access"
    r"|open_code|tmpfile|tmpnam|tempnam|mkstemp|mkdtemp"
    r"|exists|lexists|isfile|isdir|islink|ismount|getsize|getmtime|getatime|getctime|realpath|samefile"
    r"|sameopenfile|samestat"
)
REFLECTION_ATTRS = frozenset({
    "__dict__", "__builtins__", "__globals__", "__code__", "__closure__", "__subclasses__", "__mro__", "__bases__",
    "__loader__", "__spec__", "__self__", "__getattribute__", "__reduce__", "__reduce_ex__", "f_globals", "f_locals", "f_back",
    "f_builtins", "f_code", "_getframe", "_current_frames", "gi_frame", "cr_frame", "ag_frame", "tb_frame",
    "co_code", "attrgetter", "methodcaller", "itemgetter", "modules", "meta_path", "path_hooks",
    "path_importer_cache", "settrace", "setprofile", "addaudithook", "get_type_hints", "ForwardRef", "_eval_type",
    "make_dataclass", "namedtuple", "FileHandler", "WatchedFileHandler",
})
"""Attribute, ueber die man an Modul- und Funktionsinhalte kommt (`__dict__`, `sys.modules`, Frames) oder die Text
als Code auswerten (`get_type_hints`, `make_dataclass`)."""
REFLECTION_CALLS = {"getattr", "setattr", "delattr"}
"""Nur mit `self`/`cls` als erstem Argument, nie als Wert weitergereicht."""
NETWORK_NAMES = {
    "create_connection", "create_server", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr",
    "getnameinfo", "fromfd", "fromshare", "sethostname", "HTTPSConnection", "urlopen",
}
ADDRESS_FAMILY_RE = re.compile(r"AF_[A-Z0-9_]+")
"""Jede Adressfamilie ausser `AF_UNIX` (auch `AF_VSOCK`, `AF_BLUETOOTH`, ...)."""
ENV_NAMES = {"environ", "environb", "getenv", "getenvb", "putenv", "unsetenv"}
ENGINE_FORBIDDEN_RE = re.compile(
    r"/(?:exec|archive|build|commit|volumes|swarm|plugins|session)\b|/images/load\b"
    r"|\bv=(?:1|true)\b|\bforce=(?:1|true)\b",
    re.IGNORECASE,
)
PROTECTED_CONSTANTS = ("REPOSITORY", "SERVICE_ENV")
"""Konstanten, die genau einmal als Text in `policy.py` gesetzt und nie neu gebunden werden."""
SHADOW_PROTECTED = ("os", "socket")
"""Namen, an denen die Regeln haengen: sie duerfen keine Variable und kein Parameter sein."""
PATH_FUNCS_NEED_DIR_FD = {
    "stat", "lstat", "unlink", "remove", "mkdir", "rmdir", "rename", "replace", "chmod", "chown", "utime",
    "readlink",
}
"""`os.<name>(...)` mit Pfad: verlangt `dir_fd=` (bzw. `src_dir_fd=`/`dst_dir_fd=`)."""
PATH_FUNCS_NO_FOLLOW = {"stat", "chmod", "chown", "utime"}
"""Zusaetzlich `follow_symlinks=False`."""
FD_ONLY_FUNCS = {"scandir", "listdir"}
"""Nur mit einem Deskriptor (eine Variable, deren Name auf `fd` endet)."""
OS_FROM_IMPORTS_OK = {"path"}
"""`from os import ...` nur fuer `path`; alles andere ueber `os.<name>`, damit die Regeln es sehen."""


def _flag_definitions(tree: ast.AST) -> dict[str, tuple[bool, bool]]:
    """Modul-Konstanten mit Flags (`X = os.O_RDONLY | os.O_NOFOLLOW | ...`): Name -> (enthaelt O_NOFOLLOW,
    enthaelt O_DIRECTORY)."""
    found: dict[str, tuple[bool, bool]] = {}
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            names = {sub.attr for sub in ast.walk(node.value) if isinstance(sub, ast.Attribute)}
            if names & {"O_RDONLY", "O_WRONLY", "O_RDWR", "O_NOFOLLOW", "O_DIRECTORY"}:
                found[node.targets[0].id] = ("O_NOFOLLOW" in names, "O_DIRECTORY" in names)
    return found


def _package_flag_names() -> tuple[frozenset[str], frozenset[str]]:
    """Namen, die im ganzen Paket **immer** `O_NOFOLLOW` (bzw. `O_DIRECTORY`) enthalten (`DIR_FLAGS`, ...)."""
    nofollow: dict[str, bool] = {}
    directory: dict[str, bool] = {}
    for path in MODULES:
        for name, (has_nofollow, has_dir) in _flag_definitions(ast.parse(path.read_text(encoding="utf-8"))).items():
            nofollow[name] = nofollow.get(name, True) and has_nofollow
            directory[name] = directory.get(name, True) and has_dir
    return (frozenset(name for name, ok in nofollow.items() if ok),
            frozenset(name for name, ok in directory.items() if ok))


def _module_allowed(name: str) -> str | None:
    """Grund, warum der Import `name` verboten ist, sonst `None`."""
    top = name.split(".")[0]
    if top == PACKAGE:
        return None
    if top not in sys.stdlib_module_names:
        return f"nicht aus der Standardbibliothek: {name}"
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        if ".".join(parts[:i]) in FORBIDDEN_MODULES:
            return f"verbotenes Modul: {name}"
    if name not in ALLOWED_MODULES:
        return f"Modul nicht auf der Erlaubnisliste: {name}"
    return None


def _is_submodule(module: str, name: str) -> bool:
    """Ist `module.name` ein Untermodul (`from http import client`) und kein Name im Modul?"""
    try:  # nur fuer Module von der Erlaubnisliste aufgerufen: das Importieren des Elternmoduls ist harmlos
        return importlib.util.find_spec(f"{module}.{name}") is not None
    except Exception:  # noqa: BLE001 - "kein Paket", "nicht gefunden": dann ist es ein Name im Modul
        return False


def _docstring_ids(tree: ast.AST) -> set[int]:
    return {
        id(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    }


def _binds(node: ast.AST, name: str) -> bool:
    """Setzt `node` den Namen `name` (Zuweisung, auch `for`/`with`/`:=`/`del`, Parameter, `import ... as name`)?"""
    if isinstance(node, ast.Name):
        return node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))
    if isinstance(node, ast.arg):
        return node.arg == name
    return isinstance(node, ast.alias) and node.asname == name


def _bindings(tree: ast.AST, name: str) -> list[ast.AST]:
    """Jede Stelle, an der der Name `name` gesetzt wird."""
    return [node for node in ast.walk(tree) if _binds(node, name)]


def _binds_repository(node: ast.AST) -> bool:
    return _binds(node, "REPOSITORY")


def _repository_bindings(tree: ast.AST) -> list[ast.AST]:
    return _bindings(tree, "REPOSITORY")


def _is_module_attr(node: ast.AST, module: str, attr: str) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == attr and isinstance(node.value, ast.Name)
            and node.value.id == module)


def _constant_text(tree: ast.AST, name: str) -> bool:
    """`name` ist genau einmal gebunden, auf Modulebene, als Text mit dem Wert aus `policy`."""
    found = _bindings(tree, name)
    top = [node for node in tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1
           and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name] if isinstance(
        tree, ast.Module) else []
    return (len(found) == 1 and len(top) == 1 and isinstance(top[0].value, ast.Constant)
            and top[0].value.value == getattr(policy, name))


def _service_env_name_ok(tree: ast.AST) -> bool:
    """Der Name `SERVICE_ENV` ist in dieser Datei entweder gar nicht gebunden (kommt per Import aus `policy`) oder
    genau die eine Textzuweisung auf Modulebene (`policy.py`)."""
    return not _bindings(tree, "SERVICE_ENV") or _constant_text(tree, "SERVICE_ENV")


def _is_service_env_get(call: ast.Call, *, name_ok: bool) -> bool:
    """`os.environ.get(SERVICE_ENV[, default])` bzw. mit dem Text `"NODVARD_DECK_UPDATER_SERVICE"`."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "get" and _is_module_attr(func.value, "os", "environ")):
        return False
    if not call.args or len(call.args) > 2 or call.keywords:
        return False
    key = call.args[0]
    if isinstance(key, ast.Constant):
        return key.value == policy.SERVICE_ENV
    return (isinstance(key, ast.Name) and key.id == "SERVICE_ENV" and name_ok) or _is_module_attr(
        key, "policy", "SERVICE_ENV")


def _is_unix_family(node: ast.AST | None) -> bool:
    return _is_module_attr(node, "socket", "AF_UNIX")


def _foreign_family(name: str) -> bool:
    return ADDRESS_FAMILY_RE.fullmatch(name) is not None and name != "AF_UNIX"


def _name_problem(name: str, *, bare: bool, attr: bool = False) -> str | None:
    """Grund, warum der Bezeichner `name` verboten ist. `bare`: ein einzelner Name (`eval`) oder ein Import-Name,
    nicht der Teil nach einem Punkt (`re.compile`, `store.open` sind in Ordnung). `attr`: ein Attribut oder ein
    importierter Name (dort zusaetzlich die Reflexions-Attribute)."""
    if (bare and name in FORBIDDEN_NAMES) or FORBIDDEN_ATTR_RE.fullmatch(name):
        return f"verbotener Name {name}"
    if attr and name in REFLECTION_ATTRS:
        return f"Reflexion: {name}"
    if name in NETWORK_NAMES or _foreign_family(name):
        return f"Netz ausser AF_UNIX: {name}"
    if name in ENV_NAMES:
        return f"Umgebung: {name}"
    return None


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _flags_problem(call: ast.Call, nofollow: frozenset[str], directory: frozenset[str]) -> str | None:
    """Regeln fuer `os.open(path, flags, ..., dir_fd=...)`."""
    flags = call.args[1] if len(call.args) > 1 else _kw(call, "flags")
    if flags is None:
        return "os.open ohne Flags"
    names = {sub.attr if isinstance(sub, ast.Attribute) else sub.id for sub in ast.walk(flags)
             if isinstance(sub, (ast.Attribute, ast.Name))}
    if "O_NOFOLLOW" not in names and not names & nofollow:
        return "os.open ohne O_NOFOLLOW"
    path = call.args[0] if call.args else _kw(call, "path")
    is_proc = isinstance(path, ast.Constant) and isinstance(path.value, str) and path.value.startswith("/proc/")
    if _kw(call, "dir_fd") is None and "O_DIRECTORY" not in names and not names & directory and not is_proc:
        return "os.open ohne dir_fd (ein Pfad folgt Symlinks in Zwischenordnern)"
    return None


def _os_call_problem(func: str, call: ast.Call) -> str | None:
    """Regeln fuer `os.<func>(...)` mit Pfad: Deskriptor-Bezug."""
    if func in PATH_FUNCS_NEED_DIR_FD and not any(
            kw.arg in {"dir_fd", "src_dir_fd", "dst_dir_fd"} for kw in call.keywords):
        return f"os.{func} ohne dir_fd (Pfade folgen Symlinks)"
    if func in PATH_FUNCS_NO_FOLLOW:
        value = _kw(call, "follow_symlinks")
        if not (isinstance(value, ast.Constant) and value.value is False):
            return f"os.{func} ohne follow_symlinks=False"
    if func in FD_ONLY_FUNCS:
        arg = call.args[0] if call.args else None
        if not (isinstance(arg, ast.Name) and arg.id.endswith("fd")):
            return f"os.{func} nur mit einem Ordner-Deskriptor (Variable `...fd`), nicht mit einem Pfad"
    return None


def _reflection_call_ok(call: ast.Call) -> bool:
    """`getattr(self, ...)` / `setattr(cls, ...)`: nur auf dem eigenen Objekt."""
    return bool(call.args) and isinstance(call.args[0], ast.Name) and call.args[0].id in {"self", "cls"}


def _http_connection_problems(node: ast.ClassDef, filename: str) -> list[str]:
    """Eine Unterklasse von `HTTPConnection` verbindet selbst ueber `AF_UNIX`."""
    problems = []
    where = f"{filename}:{node.lineno}"
    connect = next((item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "connect"), None)
    if connect is None:
        return [f"{where}: HTTPConnection-Unterklasse ohne eigenes connect()"]
    if any(isinstance(sub, ast.Name) and sub.id == "super" for sub in ast.walk(node)):
        problems.append(f"{where}: HTTPConnection-Unterklasse benutzt super() (HTTPConnection.connect verbindet per TCP)")
    makes_socket = any(isinstance(sub, ast.Call) and _is_module_attr(sub.func, "socket", "socket")
                       for sub in ast.walk(connect))
    sets_sock = any(
        isinstance(sub, ast.Assign) and any(
            isinstance(target, ast.Attribute) and target.attr == "sock" and isinstance(target.value, ast.Name)
            and target.value.id == "self" for target in sub.targets)
        for sub in ast.walk(connect))
    if not (makes_socket and sets_sock):
        problems.append(f"{where}: connect() setzt self.sock nicht aus einem eigenen socket.socket(AF_UNIX)")
    return problems


def violations(source: str, filename: str = "<test>", *,
               flag_names: tuple[frozenset[str], frozenset[str]] | None = None) -> list[str]:
    """Alle Verstoesse in `source` (leer = sauber). `flag_names`: Namen aus dem Paket, die `O_NOFOLLOW` bzw.
    `O_DIRECTORY` enthalten (Vorgabe: nur die in `source` selbst definierten)."""
    tree = ast.parse(source, filename=filename)
    local = _flag_definitions(tree)
    nofollow = frozenset(name for name, (has, _) in local.items() if has) | (flag_names[0] if flag_names else frozenset())
    directory = frozenset(name for name, (_, has) in local.items() if has) | (flag_names[1] if flag_names else frozenset())
    found: list[str] = []
    docstrings = _docstring_ids(tree)
    service_env_name_ok = _service_env_name_ok(tree)
    allowed_env: set[int] = set()
    base_classes: set[int] = set()  # `class X(http.client.HTTPConnection)`: dort (und nur dort) erlaubt
    socket_calls: set[int] = set()  # `socket.socket(...)` als Aufruf: nur so erlaubt (mit AF_UNIX)
    reflection_ok: set[int] = set()  # `getattr(self, ...)`: die Namen in genau diesen Aufrufen
    os_open_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _is_service_env_get(node, name_ok=service_env_name_ok):
                allowed_env.add(id(node.func.value))  # das `os.environ` in genau diesem Aufruf
            if _is_module_attr(node.func, "socket", "socket"):
                socket_calls.add(id(node.func))
            if isinstance(node.func, ast.Name) and node.func.id in REFLECTION_CALLS and _reflection_call_ok(node):
                reflection_ok.add(id(node.func))
            if _is_module_attr(node.func, "os", "open"):
                os_open_calls.append(node)
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_classes.add(id(base))
                if (getattr(base, "attr", None) or getattr(base, "id", None)) == "HTTPConnection":
                    found.extend(_http_connection_problems(node, filename))
    for call in os_open_calls:
        if reason := _flags_problem(call, nofollow, directory):
            found.append(f"{filename}:{call.lineno}: {reason}")

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        where = f"{filename}:{line}"
        for shadowed in SHADOW_PROTECTED:
            if _binds(node, shadowed):
                found.append(f"{where}: {shadowed} wird neu gebunden (die Regeln haengen an dem Namen)")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if reason := _module_allowed(alias.name):
                    found.append(f"{where}: {reason}")
                if alias.asname is not None and alias.name.split(".")[0] != PACKAGE:
                    found.append(f"{where}: Modul mit Alias importiert ({alias.name} as {alias.asname})")
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0:
                continue
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    found.append(f"{where}: Stern-Import aus {module}")
                    continue
                target = f"{module}.{alias.name}"
                if target in ALLOWED_MODULES:
                    reason = None  # `from http import client` ist `import http.client`
                elif _module_allowed(module) is None and module.split(".")[0] != PACKAGE \
                        and _is_submodule(module, alias.name):
                    reason = _module_allowed(target)  # `from logging import handlers` ist ein anderes Modul
                else:
                    reason = _module_allowed(module)
                if reason:
                    found.append(f"{where}: {reason}")
                elif reason := _name_problem(alias.name, bare=True, attr=True):
                    found.append(f"{where}: {reason} (aus {module})")
                elif module == "os" and alias.name not in OS_FROM_IMPORTS_OK:
                    found.append(f"{where}: from os import {alias.name} (nur os.<name>, damit die Regeln es sehen)")
                elif module == "socket" and alias.name in {"socket", "SocketType"}:
                    found.append(f"{where}: from socket import {alias.name} (nur socket.socket(AF_UNIX))")
                if alias.asname is not None and module.split(".")[0] != PACKAGE:
                    found.append(f"{where}: Name mit Alias importiert ({alias.name} as {alias.asname})")
        elif isinstance(node, ast.Name):
            if reason := _name_problem(node.id, bare=True):
                found.append(f"{where}: {reason}")
            elif node.id in REFLECTION_CALLS and id(node) not in reflection_ok:
                found.append(f"{where}: {node.id} nur als Aufruf auf self/cls")
            elif node.id == "HTTPConnection" and id(node) not in base_classes:
                found.append(f"{where}: HTTPConnection nur als Basisklasse (sonst TCP)")
        elif isinstance(node, ast.Attribute):
            if node.attr in ENV_NAMES and id(node) in allowed_env:
                continue
            if reason := _name_problem(node.attr, bare=False, attr=True):
                found.append(f"{where}: {reason}")
            elif node.attr == "HTTPConnection" and id(node) not in base_classes:
                found.append(f"{where}: HTTPConnection nur als Basisklasse (sonst TCP)")
            elif node.attr in PROTECTED_CONSTANTS and isinstance(node.ctx, (ast.Store, ast.Del)):
                found.append(f"{where}: {node.attr} wird ueberschrieben")
            elif node.attr in {"socket", "SocketType"} and isinstance(node.value, ast.Name) \
                    and node.value.id == "socket" and id(node) not in socket_calls:
                found.append(f"{where}: socket.{node.attr} nur als Aufruf mit AF_UNIX (kein Alias, keine Basisklasse)")
        elif isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id in REFLECTION_CALLS | {"hasattr"} and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
                attr = node.args[1].value
                if _name_problem(attr, bare=True, attr=True) or attr in PROTECTED_CONSTANTS:
                    found.append(f"{where}: {func.id}(..., {attr!r})")
            if _is_module_attr(func, "socket", "socket") or _is_module_attr(func, "socket", "SocketType"):
                family = node.args[0] if node.args else _kw(node, "family")
                if not _is_unix_family(family):
                    found.append(f"{where}: socket() ohne ausdruecklich socket.AF_UNIX")
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "os"
                    and (reason := _os_call_problem(func.attr, node))):
                found.append(f"{where}: {reason}")
            for kw in node.keywords:
                if kw.arg in {"filename", "filemode"}:
                    found.append(f"{where}: Schluesselwort {kw.arg}= (Datei an os.open vorbei)")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for protected in PROTECTED_CONSTANTS:
                if protected in node.names:
                    found.append(f"{where}: {protected} als global/nonlocal")
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
              and (match := ENGINE_FORBIDDEN_RE.search(node.value))):
            found.append(f"{where}: verbotener Engine-Pfad {match.group(0)!r}")
    return found


# ---------------------------------------------------------------------------
# Das Paket selbst
# ---------------------------------------------------------------------------


PACKAGE_FLAGS = _package_flag_names()


def test_guard_sees_the_whole_package():
    names = {path.relative_to(PACKAGE_DIR).as_posix() for path in MODULES}
    assert {"__init__.py", "policy.py", "channel.py", "state.py", "engine.py", "target.py", "clone.py",
            "__main__.py"} <= names
    assert {"DIR_FLAGS", "READ_FLAGS", "_CREATE_FLAGS"} <= PACKAGE_FLAGS[0], "Flags mit O_NOFOLLOW sind erkannt"
    assert PACKAGE_FLAGS[1] == {"DIR_FLAGS"}, "nur die Ordner-Flags enthalten O_DIRECTORY"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.relative_to(PACKAGE_DIR).as_posix())
def test_module_follows_the_rules(path):
    assert violations(path.read_text(encoding="utf-8"), path.name, flag_names=PACKAGE_FLAGS) == []


def test_allowlist_and_forbidden_list_are_disjoint():
    # Wer ein Modul auf die Erlaubnisliste setzt, setzt es nicht gleichzeitig auf die Verbotsliste (und umgekehrt):
    # `subprocess` & Co. lassen sich nicht "mal eben" erlauben.
    assert ALLOWED_MODULES <= set(sys.stdlib_module_names) | {"os.path", "collections.abc", "http.client",
                                                              "urllib.parse"}
    for name in ALLOWED_MODULES:
        parts = name.split(".")
        assert not any(".".join(parts[:i]) in FORBIDDEN_MODULES for i in range(1, len(parts) + 1)), name
    assert {"subprocess", "ctypes", "importlib", "io", "pathlib", "shutil", "tempfile", "socketserver",
            "logging.handlers"} <= FORBIDDEN_MODULES


def test_the_package_only_imports_modules_from_the_allowlist():
    imported = set()
    for path in MODULES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module)
    assert imported - {name for name in imported if name.split(".")[0] == PACKAGE} <= ALLOWED_MODULES


@pytest.mark.parametrize("name", PROTECTED_CONSTANTS)
def test_protected_constants_are_set_once_as_a_literal_in_policy(name):
    bindings = []
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bindings += [(path.name, getattr(node, "lineno", 0)) for node in _bindings(tree, name)]
    assert len(bindings) == 1, bindings
    assert bindings[0][0] == "policy.py"
    policy_tree = ast.parse((PACKAGE_DIR / "policy.py").read_text(encoding="utf-8"))
    top_level = [
        node for node in policy_tree.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    ]
    assert len(top_level) == 1, "genau eine Zuweisung, auf Modulebene (nicht in einer Funktion)"
    value = top_level[0].value
    assert isinstance(value, ast.Constant) and value.value == getattr(policy, name)
    assert _constant_text(policy_tree, name)


def test_service_name_is_the_only_environment_setting():
    assert policy.SERVICE_ENV == "NODVARD_DECK_UPDATER_SERVICE"
    assert policy.service_name(None) == policy.DEFAULT_SERVICE == "nodvard-deck"


def test_repository_text_appears_only_in_the_constant():
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_ids(tree)
        hits = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
            and "ghcr.io/nodvard" in node.value
        ]
        expected = 1 if path.name == "policy.py" else 0
        assert len(hits) == expected, (path.name, hits)


def test_repository_binding_detector_sees_every_form():
    for source in ("REPOSITORY = 'x'", "REPOSITORY: str = 'x'", "REPOSITORY += 'x'", "for REPOSITORY in y: pass",
                   "with a as REPOSITORY: pass", "(REPOSITORY := 'x')", "def f(REPOSITORY): pass",
                   "import os as REPOSITORY", "del REPOSITORY"):
        assert _repository_bindings(ast.parse(source)), source
    assert not _repository_bindings(ast.parse("x = REPOSITORY\nf(repository=REPOSITORY)\npolicy.REPOSITORY"))


def test_production_code_never_overrides_the_expected_owner():
    # `expected_uid` (Besitzer von /channel und /state) ist ein Parameter nur fuer Tests ohne root. Im Betrieb gilt
    # die Vorgabe 0: kein Modul des Pakets reicht ihn weiter, und er ist nur ueber den Konstruktor zu aendern.
    hits = []
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits += [
            (path.name, node.lineno) for node in ast.walk(tree)
            if isinstance(node, ast.keyword) and node.arg == "expected_uid"
        ]
    assert hits == []


def test_status_json_is_only_ever_written():
    # Der Helfer entscheidet nur aus /state und der Engine: `status.json` ist das, was das Dashboard sehen
    # darf, nie eine Eingabe. Es wird nur in `Channel.write_status` benutzt (und als Konstante benannt).
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_ids(tree)
        allowed: set[int] = set()
        for node in ast.walk(tree):
            names_status = (isinstance(node, ast.Assign) and len(node.targets) == 1
                            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "STATUS_NAME")
            if names_status or (isinstance(node, ast.FunctionDef) and node.name == "write_status"):
                allowed |= {id(sub) for sub in ast.walk(node)}
        for node in ast.walk(tree):
            uses = (
                (isinstance(node, ast.Name) and node.id == "STATUS_NAME")
                or (isinstance(node, ast.Attribute) and node.attr == "STATUS_NAME")
                or (isinstance(node, ast.Constant) and isinstance(node.value, str) and "status.json" in node.value
                    and id(node) not in docstrings)
            )
            if uses:
                assert path.name == "channel.py" and id(node) in allowed, (path.name, node.lineno)


def test_repository_matches_the_release_workflow():
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    match = re.search(r"^\s*IMAGE_NAME:\s*(\S+)\s*$", text, re.MULTILINE)
    assert match and match.group(1) == policy.REPOSITORY


def _module_strings(tree: ast.Module) -> dict[str, str]:
    """Einfache Modul-Konstanten (`X = "..."` und `X = f"{A}/{B}"` aus solchen) auswerten, ohne zu importieren."""
    values: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            values[node.targets[0].id] = value.value
        elif isinstance(value, ast.JoinedStr):
            parts = []
            for part in value.values:
                if isinstance(part, ast.Constant):
                    parts.append(str(part.value))
                elif (isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
                      and part.value.id in values):
                    parts.append(values[part.value.id])
                else:
                    break
            else:
                values[node.targets[0].id] = "".join(parts)
    return values


def test_repository_matches_the_dashboard_constant_once_it_exists():
    path = REPO_ROOT / "backend" / "src" / "nodvard_deck" / "core" / "updates.py"
    if not path.exists():
        pytest.skip("core/updates.py (Update-Suche) ist noch nicht in diesem Stand")
    values = _module_strings(ast.parse(path.read_text(encoding="utf-8")))
    assert values.get("OFFICIAL_IMAGE") == policy.REPOSITORY


def test_official_compose_file_uses_a_tag_the_helper_supports():
    text = (REPO_ROOT / "deploy" / "compose.standalone.yml").read_text(encoding="utf-8")
    images = re.findall(r"^\s*image:\s*(\S+)\s*$", text, re.MULTILINE)
    assert images and all(policy.floating_tag(image) == "latest" for image in images)


def test_package_imports_without_site_packages_and_ignores_the_environment():
    modules = sorted(
        ".".join((PACKAGE, *path.relative_to(PACKAGE_DIR).with_suffix("").parts)).removesuffix(".__init__")
        for path in MODULES if path.name != "__main__.py"
    )
    script = textwrap.dedent(f"""
        import importlib, sys
        sys.path.insert(0, {str(UPDATER_DIR)!r})
        for name in {modules!r}:
            importlib.import_module(name)
        third_party = sorted(
            n for n in sys.modules
            if n.split(".")[0] not in sys.stdlib_module_names and n.split(".")[0] not in ({PACKAGE!r}, "__main__")
        )
        from nodvard_deck_updater import policy
        print(policy.REPOSITORY, third_party)
    """)
    env = {**os.environ, "NODVARD_DECK_UPDATER_REPOSITORY": "evil.example/x", "REPOSITORY": "evil.example/x",
           "NODVARD_DECK_REPOSITORY": "evil.example/x", "IMAGE": "evil.example/x"}
    out = subprocess.run([sys.executable, "-I", "-S", "-c", script], capture_output=True, text=True, env=env,
                         timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ghcr.io/nodvard/deck []"


def test_dashboard_image_never_contains_the_channel_path():
    # Ein Image, das `/app/updater` schon mitbringt, gaebe der Wurzel des Kanals beim ersten Einhaengen des
    # Volumes (Copy-up) den Besitzer uid 1000 -- der Helfer richtet den Kanal selbst ein, das Dashboard-Image
    # kennt den Pfad nicht. Das Dashboard bekommt ihn nur ueber das Volume in der Compose-Datei.
    for name in ("Dockerfile", "entrypoint.sh"):
        assert "/app/updater" not in (REPO_ROOT / "deploy" / name).read_text(encoding="utf-8"), name
    assert _final_stage_problems((REPO_ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")) == []


def _final_stage_problems(dockerfile: str) -> list[str]:
    """Anweisungen der letzten Stufe, die etwas vom Helfer ins Dashboard-Image bringen koennten.

    Nur die letzte Stufe wird zum Image. Die Bau-Stufe des Frontends darf die Test-Vektoren des Helfers lesen (tsc
    prueft den Test der Helfer-Texte mit); die letzte Stufe holt sich von dort nur das fertige Frontend und die
    Erweiterungen."""
    instructions, pending = [], ""
    for line in dockerfile.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        keyword, _, rest = (pending + line).strip().partition(" ")
        pending = ""
        if keyword:
            instructions.append((keyword.upper(), rest.strip()))
    final = instructions[max(i for i, (kw, _rest) in enumerate(instructions) if kw == "FROM"):]
    problems = [f"{kw} {rest}" for kw, rest in final if kw in {"COPY", "ADD", "VOLUME", "RUN"}
                and "updater" in rest.lower()]
    problems += [f"{kw} {rest}" for kw, rest in final if kw == "COPY" and rest.startswith("--from=")
                 and not rest.split()[-2].startswith(("/src/extensions", "/src/frontend/dist"))]
    return problems


def test_the_image_guard_reads_only_the_final_stage():
    """Gegenprobe: In der Bau-Stufe darf der Helfer vorkommen, in der letzten Stufe nicht -- auch nicht ueber eine
    fortgesetzte Zeile oder ein `--from=`, das mehr als das fertige Frontend holt. Kommentare zaehlen nicht."""
    build = "FROM node AS b\nCOPY deploy/updater/tests/vectors/ x/\nFROM python AS runtime\n# COPY deploy/updater/ x/\n"
    assert _final_stage_problems(build + "COPY --from=b /src/frontend/dist ./frontend/dist\n") == []
    assert len(_final_stage_problems(build + "COPY a \\\n  deploy/updater/ /x/\n")) == 1
    assert len(_final_stage_problems(build + "ADD deploy/Updater.tar /x/\n")) == 1
    assert _final_stage_problems(build + "COPY --from=b /src /app\n") == ["COPY --from=b /src /app"]


def test_ci_runs_the_updater_tests():
    # Eigener Aufruf (nicht in `pytest backend/tests` eingehaengt): der Helfer ist ein eigenes Programm mit nur der
    # Standardbibliothek und wird ohne Installation und ohne Backend-conftest getestet.
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s*- run: python -m pytest deploy/updater/tests\s*$", text, re.MULTILINE)


def test_ci_also_runs_the_needs_root_tests_as_root():
    # Der Runner ist nicht root: ohne diesen Schritt werden die Tests mit Dateien eines anderen Besitzers (Betriebsbild:
    # Helfer uid 0, Dashboard uid 1000) dort nie ausgefuehrt. `sudo` ohne Passwort gibt es auf GitHub-Runnern; `-E` und
    # `env "PATH=$PATH"` nehmen dasselbe Python und dieselben Pakete mit.
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    steps = re.findall(r"^\s*- run: (sudo .*)$", text, re.MULTILINE)
    root_steps = [step for step in steps if "deploy/updater/tests" in step]
    assert len(root_steps) == 1, steps
    step = root_steps[0]
    assert re.match(r'sudo -E env "PATH=\$PATH" ', step)
    assert "python -m pytest" in step and re.search(r"-m needs_root\b", step)
    assert "-p no:cacheprovider" in step, "kein root-eigener Cache im Checkout"
    assert "PYTHONDONTWRITEBYTECODE=1" in step, "kein root-eigener Bytecode im Checkout"
    # Der Marker muss es geben (sonst waehlt `-m needs_root` nichts aus und der Schritt wuerde still nichts tun).
    count = sum(1 for path in UPDATER_DIR.joinpath("tests").glob("test_*.py")
                for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("@needs_root"))
    assert count >= 5, count


# ---------------------------------------------------------------------------
# Der Waechter selbst (damit er nicht still nichts prueft)
# ---------------------------------------------------------------------------

BAD = {
    "subprocess": "import subprocess",
    "subprocess aus Paket": "from subprocess import run",
    "os.system": "import os\nos.system('ls')",
    "from os import system": "from os import system",
    "os.execv": "import os\nos.execv('/bin/sh', [])",
    "os.spawnlp": "import os\nos.spawnlp(0, 'sh')",
    "os.posix_spawn": "import os\nos.posix_spawn('/bin/sh', [], {})",
    "os.fork": "import os\nos.fork()",
    "os.popen": "import os\nos.popen('ls')",
    "os.kill": "import os\nos.kill(1, 9)",
    "getattr os system": "import os\ngetattr(os, 'system')('ls')",
    "eval": "eval('1')",
    "exec": "exec('x = 1')",
    "compile": "compile('1', 'x', 'eval')",
    "__import__": "__import__('subprocess')",
    "importlib": "import importlib",
    "importlib import_module": "from importlib import import_module",
    "importlib.util": "import importlib.util",
    "os.execvpe": "import os\nos.execvpe('sh', [], {})",
    "os.spawnve": "import os\nos.spawnve(0, 'sh', [], {})",
    "__import__ ueber getattr": "getattr(__builtins__, '__import__')('os')",
    "runpy": "import runpy",
    "code": "import code",
    "pickle": "import pickle",
    "marshal": "from marshal import loads",
    "ctypes": "import ctypes",
    "pty": "import pty",
    "multiprocessing": "import multiprocessing",
    "asyncio": "import asyncio",
    "concurrent.futures": "from concurrent import futures",
    "ssl": "import ssl",
    "urllib.request": "from urllib import request",
    "urllib.request direkt": "import urllib.request",
    "http.server": "import http.server",
    "Fremdpaket": "import requests",
    "Fremdpaket cryptography": "from cryptography.hazmat import backends",
    "Stern-Import": "from os import *",
    "AF_INET": "import socket\nsocket.socket(socket.AF_INET)",
    "AF_INET6 importiert": "from socket import AF_INET6",
    "AF_VSOCK": "import socket\nx = socket.AF_VSOCK",
    "socket() ohne Familie (= TCP)": "import socket\nsocket.socket()",
    "socket() mit anderer Familie": "import socket\nsocket.socket(family=socket.AF_PACKET)",
    "socket aus from-Import": "from socket import socket\nsocket()",
    "socket.fromfd": "import socket\nsocket.fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)",
    "HTTPConnection direkt": "import http.client\nhttp.client.HTTPConnection('localhost')",
    "HTTPConnection aus from-Import": "from http.client import HTTPConnection\nHTTPConnection('x')",
    "HTTPConnection ohne connect()": (
        "import http.client\nclass C(http.client.HTTPConnection):\n    def request(self): pass"),
    "create_connection": "import socket\nsocket.create_connection(('x', 1))",
    "HTTPSConnection": "import http.client\nhttp.client.HTTPSConnection('x')",
    "os.getenv": "import os\nos.getenv('X')",
    "os.environ[...]": "import os\nos.environ['NODVARD_DECK_UPDATER_SERVICE']",
    "environ.get anderer Schluessel": "import os\nos.environ.get('NODVARD_DECK_UPDATER_REPOSITORY')",
    "environ weitergereicht": "import os\nf(os.environ)",
    "from os import environ": "from os import environ",
    "putenv": "import os\nos.putenv('A', 'b')",
    "open()": "open('/channel/status.json')",
    "Path.read_text": "from pathlib import Path\nPath('x').read_text()",
    "REPOSITORY ueberschreiben": "import policy\npolicy.REPOSITORY = 'evil'",
    "REPOSITORY global": "def f():\n    global REPOSITORY\n",
    "setattr REPOSITORY": "setattr(policy, 'REPOSITORY', 'evil')",
    "Engine /exec": "PATH = '/containers/{id}/exec'",
    "Engine /archive": "path = f'/containers/{cid}/archive'",
    "Engine /build": "x = '/build?t=x'",
    "Engine v=1": "q = 'v=1&force=0'",
    "Engine force=true": "q = 'force=true'",
    "Engine /volumes": "x = '/volumes/prune'",
}


@pytest.mark.parametrize("source", list(BAD.values()), ids=list(BAD))
def test_guard_catches(source):
    assert violations(source), source


# Jede Regel hat ihren eigenen Fall mit dem erwarteten Grund: so faellt auf, wenn eine Regel entfernt wird, auch wenn
# ein anderer Fall denselben Quelltext noch aus anderem Grund bemaengelt.
CONNECT = "import http.client, socket\nclass C(http.client.HTTPConnection):\n    def connect(self):\n"
BAD_WHY = {
    # --- Erlaubnisliste statt Verbotsliste (Funde 8, 11)
    "timeit fuehrt Text aus": ("import timeit\ntimeit.timeit('__import__(\"os\")')", "verbotenes Modul: timeit"),
    "cProfile fuehrt Text aus": ("import cProfile\ncProfile.run('1')", "verbotenes Modul: cProfile"),
    "profile fuehrt Text aus": ("import profile", "verbotenes Modul: profile"),
    "doctest fuehrt Text aus": ("import doctest\ndoctest.run_docstring_examples(f, {})", "verbotenes Modul: doctest"),
    "pydoc laedt nach Namen": ("import pydoc\npydoc.locate('subprocess').run(['ls'])", "verbotenes Modul: pydoc"),
    "pkgutil laedt nach Namen": ("import pkgutil\npkgutil.resolve_name('subprocess:run')(['ls'])",
                                 "verbotenes Modul: pkgutil"),
    "logging.config fuehrt Code aus": ("import logging.config\nlogging.config.listen(9030)",
                                       "verbotenes Modul: logging.config"),
    "logging.handlers sendet per UDP": ("from logging.handlers import SysLogHandler\nSysLogHandler()",
                                        "verbotenes Modul: logging.handlers"),
    "logging nicht auf der Liste": ("import logging", "Modul nicht auf der Erlaubnisliste: logging"),
    "zipapp": ("import zipapp", "verbotenes Modul: zipapp"),
    "venv": ("import venv", "verbotenes Modul: venv"),
    "ensurepip": ("import ensurepip\nensurepip.bootstrap()", "verbotenes Modul: ensurepip"),
    "sqlite3 laedt Erweiterungen": ("import sqlite3", "verbotenes Modul: sqlite3"),
    "unittest.mock": ("from unittest import mock", "verbotenes Modul: unittest"),
    "uuid nicht auf der Liste": ("import uuid", "Modul nicht auf der Erlaubnisliste: uuid"),
    "hashlib nicht auf der Liste": ("from hashlib import sha256", "Modul nicht auf der Erlaubnisliste: hashlib"),
    "datetime nicht auf der Liste": ("import datetime", "Modul nicht auf der Erlaubnisliste: datetime"),
    "json.tool ist ein anderes Modul": ("import json.tool", "Modul nicht auf der Erlaubnisliste: json.tool"),
    "json.tool aus from-Import": ("from json import tool", "Modul nicht auf der Erlaubnisliste: json.tool"),
    "http.server aus from-Import": ("from http import server", "Modul nicht auf der Erlaubnisliste"),
    "weiteres Paket der Standardbibliothek": ("import xml.etree", "Modul nicht auf der Erlaubnisliste: xml.etree"),
    "_socket (Standard AF_INET)": ("import _socket\n_socket.socket()", "verbotenes Modul: _socket"),
    # --- Aliase
    "socket als Modul-Alias": ("import socket as so\nso.socket()", "Modul mit Alias importiert"),
    "socket-Klasse als Alias": ("from socket import socket as S\nS()", "from socket import socket"),
    "json als Alias": ("import json as j", "Modul mit Alias importiert"),
    "Name aus Modul als Alias": ("from re import compile as c", "Name mit Alias importiert"),
    "os ueberdeckt": ("os = None", "os wird neu gebunden"),
    "os als Parameter": ("def f(os): pass", "os wird neu gebunden"),
    "socket ueberdeckt": ("socket = object()", "socket wird neu gebunden"),
    "socket.SocketType()": ("import socket\nsocket.SocketType()", "socket.SocketType nur als Aufruf"),
    "socket.socket einer Variablen zugewiesen": ("import socket\nmk = socket.socket\nmk()",
                                                 "socket.socket nur als Aufruf"),
    "socket.socket als Basisklasse": ("import socket\nclass S(socket.socket): pass", "socket.socket nur als Aufruf"),
    "from socket import SocketType": ("from socket import SocketType", "from socket import SocketType"),
    # --- Dateien nur ueber os.open mit O_NOFOLLOW und dir_fd (Funde 9, 16)
    "io.open": ("import io\nio.open('/channel/requests/x.json').read()", "verbotenes Modul: io"),
    "Path.open": ("from pathlib import Path\nPath('/channel/requests/x.json').open()", "verbotenes Modul: pathlib"),
    "shutil.copy": ("import shutil\nshutil.copy('/channel/x', '/state/x')", "verbotenes Modul: shutil"),
    "shutil.rmtree": ("import shutil\nshutil.rmtree('/channel/requests')", "verbotenes Modul: shutil"),
    "tempfile": ("import tempfile\ntempfile.NamedTemporaryFile(dir='/channel')", "verbotenes Modul: tempfile"),
    "fileinput": ("import fileinput", "verbotenes Modul: fileinput"),
    "codecs.open": ("import codecs\ncodecs.open('/channel/x')", "verbotenes Modul: codecs"),
    "glob": ("import glob", "verbotenes Modul: glob"),
    "os.fdopen": ("import os\nos.fdopen(3)", "verbotener Name fdopen"),
    "os.open ohne O_NOFOLLOW": ("import os\nos.open('x', os.O_RDONLY, dir_fd=3)", "os.open ohne O_NOFOLLOW"),
    "os.open mit fremdem Flag-Namen": ("import os\nos.open('x', FLAGS, dir_fd=3)", "os.open ohne O_NOFOLLOW"),
    "os.open mit Flag-Name ohne O_NOFOLLOW": ("import os\nFLAGS = os.O_RDONLY\nos.open('x', FLAGS, dir_fd=3)",
                                              "os.open ohne O_NOFOLLOW"),
    "os.open ohne Flags": ("import os\nos.open('x')", "os.open ohne Flags"),
    "os.open mit absolutem Pfad": ("import os\nos.open('/channel/requests/x.json', os.O_RDONLY | os.O_NOFOLLOW)",
                                   "os.open ohne dir_fd"),
    "os.stat mit Pfad": ("import os\nos.stat('/channel/requests')", "os.stat ohne dir_fd"),
    "os.stat folgt Links": ("import os\nos.stat('x', dir_fd=3)", "os.stat ohne follow_symlinks=False"),
    "os.lstat mit Pfad": ("import os\nos.lstat('/channel/requests')", "os.lstat ohne dir_fd"),
    "os.chmod mit Pfad": ("import os\nos.chmod('/channel/requests', 0o1777)", "os.chmod ohne dir_fd"),
    "os.chown mit Pfad": ("import os\nos.chown('/state', 0, 0)", "os.chown ohne dir_fd"),
    "os.unlink mit Pfad": ("import os\nos.unlink('/channel/status.json')", "os.unlink ohne dir_fd"),
    "os.remove mit Pfad": ("import os\nos.remove('/channel/status.json')", "os.remove ohne dir_fd"),
    "os.rename mit Pfaden": ("import os\nos.rename('/a', '/b')", "os.rename ohne dir_fd"),
    "os.replace mit Pfaden": ("import os\nos.replace('/a', '/b')", "os.replace ohne dir_fd"),
    "os.mkdir mit Pfad": ("import os\nos.mkdir('/channel/requests')", "os.mkdir ohne dir_fd"),
    "os.rmdir mit Pfad": ("import os\nos.rmdir('/channel/requests')", "os.rmdir ohne dir_fd"),
    "os.readlink mit Pfad": ("import os\nos.readlink('/channel/x')", "os.readlink ohne dir_fd"),
    "os.listdir mit Pfad": ("import os\nos.listdir('/channel')", "os.listdir nur mit einem Ordner-Deskriptor"),
    "os.scandir mit Pfad": ("import os\nos.scandir(self._path)", "os.scandir nur mit einem Ordner-Deskriptor"),
    "os.scandir ohne Argument": ("import os\nos.scandir()", "os.scandir nur mit einem Ordner-Deskriptor"),
    "os.walk": ("import os\nos.walk('/channel')", "verbotener Name walk"),
    "os.chdir": ("import os\nos.chdir('/channel')", "verbotener Name chdir"),
    "os.symlink": ("import os\nos.symlink('/state', '/channel/x')", "verbotener Name symlink"),
    "os.path.exists": ("import os\nos.path.exists('/channel/x')", "verbotener Name exists"),
    "os.path.realpath": ("import os\nos.path.realpath('/channel/x')", "verbotener Name realpath"),
    "from os import stat": ("from os import stat", "from os import stat"),
    "Datei per Schluesselwort": ("f(filename='/state/log')", "Schluesselwort filename="),
    # --- HTTPConnection
    "connect ruft super().connect()": (CONNECT + "        super().connect()", "benutzt super()"),
    "connect ruft super(C, self).connect()": (
        CONNECT + "        self.timeout = 5\n        super(C, self).connect()", "benutzt super()"),
    "connect ruft HTTPConnection.connect": (
        CONNECT + "        http.client.HTTPConnection.connect(self)", "HTTPConnection nur als Basisklasse"),
    "connect legt keinen eigenen Socket an": (
        CONNECT + "        self.sock = other", "setzt self.sock nicht aus einem eigenen socket.socket"),
    "connect legt Socket an, setzt self.sock nicht": (
        CONNECT + "        socket.socket(socket.AF_UNIX)", "setzt self.sock nicht aus einem eigenen socket.socket"),
    "connect mit TCP-Socket": (
        CONNECT + "        self.sock = socket.socket()", "socket() ohne ausdruecklich socket.AF_UNIX"),
    # --- Schutz von REPOSITORY und SERVICE_ENV
    "REPOSITORY ueber __dict__": ("import policy\npolicy.__dict__['REPOSITORY'] = 'evil'", "Reflexion: __dict__"),
    "REPOSITORY ueber setattr mit Variable": ("k = 'REPO' + 'SITORY'\nsetattr(policy, k, 'evil')",
                                              "setattr nur als Aufruf auf self/cls"),
    "REPOSITORY ueber setattr mit Text": ("setattr(policy, 'REPOSITORY', 'evil')", "setattr nur als Aufruf auf self/cls"),
    "REPOSITORY ueber setattr auf self": ("setattr(self, 'REPOSITORY', 'evil')", "setattr(..., 'REPOSITORY')"),
    "getattr mit zusammengesetztem Namen": ("import os\ngetattr(os, 'sys' + 'tem')('ls')",
                                            "getattr nur als Aufruf auf self/cls"),
    "getattr als Wert weitergereicht": ("import functools\nfunctools.reduce(getattr, ['system'], os)",
                                        "getattr nur als Aufruf auf self/cls"),
    "hasattr mit verbotenem Namen": ("import os\nhasattr(os, 'system')", "hasattr(..., 'system')"),
    "sys.modules": ("import sys\nsys.modules['builtins'].__import__('subprocess')", "Reflexion: modules"),
    "sys._getframe": ("import sys\nsys._getframe().f_globals['REPOSITORY'] = 'x'", "Reflexion: _getframe"),
    "Frame-Globals": ("def f(frame):\n    return frame.f_globals", "Reflexion: f_globals"),
    "object.__subclasses__": ("object.__subclasses__()", "Reflexion: __subclasses__"),
    "builtins ueber print.__self__": ("print.__self__.eval('1')", "Reflexion: __self__"),
    "__globals__": ("def f(g):\n    return g.__globals__", "Reflexion: __globals__"),
    "get_type_hints wertet Text aus": ("import typing\ntyping.get_type_hints(f)", "Reflexion: get_type_hints"),
    "import von modules": ("from sys import modules", "Reflexion: modules"),
    "SERVICE_ENV neu gebunden": (
        "import os\nSERVICE_ENV = 'NODVARD_DECK_UPDATER_REPOSITORY'\nos.environ.get(SERVICE_ENV)", "Umgebung: environ"),
    "SERVICE_ENV als Parameter": (
        "import os\ndef f(SERVICE_ENV):\n    return os.environ.get(SERVICE_ENV)", "Umgebung: environ"),
    "SERVICE_ENV ueberschreiben": ("policy.SERVICE_ENV = 'NODVARD_DECK_UPDATER_REPOSITORY'",
                                   "SERVICE_ENV wird ueberschrieben"),
    "SERVICE_ENV global": ("def f():\n    global SERVICE_ENV\n", "SERVICE_ENV als global/nonlocal"),
}


@pytest.mark.parametrize(("source", "why"), list(BAD_WHY.values()), ids=list(BAD_WHY))
def test_guard_catches_each_rule_for_its_own_reason(source, why):
    found = violations(source)
    assert any(why in line for line in found), (why, found)


def test_guard_allows_what_the_helper_needs():
    source = textwrap.dedent("""
        \"\"\"Docstrings duerfen /exec und v=1 und ghcr.io/nodvard/deck erwaehnen.\"\"\"
        from __future__ import annotations
        import errno, fcntl, json, math, os, re, secrets, signal, socket, stat, sys, threading, time
        import http.client
        import os.path
        from collections.abc import Iterable
        from dataclasses import dataclass, field
        from http import client
        from os import path
        from typing import Any, Self
        from urllib import parse
        from urllib.parse import quote
        from . import policy
        from .policy import Refusal, SERVICE_ENV
        service = os.environ.get(SERVICE_ENV, "nodvard-deck")
        other = os.environ.get("NODVARD_DECK_UPDATER_SERVICE")
        via_policy = os.environ.get(policy.SERVICE_ENV)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        root_fd = os.open("/channel", DIR_FLAGS)
        fd = os.open("x", READ_FLAGS, dir_fd=root_fd)
        other_fd = os.open("y", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
        mountinfo = os.open("/proc/self/mountinfo", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        st = os.stat("x", dir_fd=root_fd, follow_symlinks=False)
        os.unlink("x", dir_fd=root_fd)
        os.rmdir("d", dir_fd=root_fd)
        os.mkdir("d", 0o700, dir_fd=root_fd)
        os.replace("a", "b", src_dir_fd=root_fd, dst_dir_fd=root_fd)
        with os.scandir(root_fd) as entries:
            names = [entry.name for entry in entries]
        joined = os.path.join("a", "b")
        pair = re.compile("x")
        signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
        threading.Thread(target=print, daemon=True)
        sys.stdout.write(json.dumps({"x": math.isfinite(1.0)}))

        class UnixHTTPConnection(http.client.HTTPConnection):
            def connect(self):
                sock = socket.socket(family=socket.AF_UNIX)
                sock.connect("/var/run/docker.sock")
                self.sock = sock
        path = f"/containers/{cid}/json"
        query = "v=0&force=0"
        class Box:
            def get(self, key):
                return getattr(self, key)
            def put(self, key, value):
                setattr(self, key, value)
        def f(store):
            store.open()
            store.exists_later = hasattr(store, "until")
    """)
    assert violations(source) == [], violations(source)


def test_guard_still_flags_the_package_patterns_when_a_rule_is_broken_on_purpose():
    # Gegenprobe zum Test oben: derselbe Quelltext mit je einer kleinen Aenderung faellt auf (der Waechter hat also
    # nicht einfach nur "alles erlaubt").
    good = "import os\nDIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW\nroot = os.open('/channel', DIR_FLAGS)\n"
    assert violations(good) == []
    assert violations(good.replace(" | os.O_NOFOLLOW", ""))
    assert violations(good.replace(" | os.O_DIRECTORY", ""))


# ---------------------------------------------------------------------------
# Engine-Pfade nur in engine.py und nur aus der Allowlist
# ---------------------------------------------------------------------------

ENGINE_PATH_RE = re.compile(r"/(?:_ping|version|containers|images|networks|distribution|exec|build|commit|volumes"
                            r"|swarm|plugins|session|info|events|system|auth|secrets|configs|services|tasks|nodes)\b")
"""Texte, die wie ein Pfad der Engine-API aussehen (auch die, die es im Helfer gar nicht geben darf)."""
NETWORK_MODULES = {"socket", "http.client", "http"}


def engine_path_problems(source: str, filename: str, allowed: set[str]) -> list[str]:
    """Jeder Text im Code (ohne Docstrings), der mit einem Engine-Pfad beginnt, muss genau ein Muster aus `allowed`
    sein (fuer engine.py: die Tabelle `ENDPOINTS`; fuer alle anderen Module: nichts)."""
    tree = ast.parse(source, filename=filename)
    docstrings = _docstring_ids(tree)
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                and ENGINE_PATH_RE.match(node.value) and node.value not in allowed):
            found.append(f"{filename}:{node.lineno}: Engine-Pfad ausserhalb der Allowlist: {node.value!r}")
    return found


def _endpoint_patterns() -> set[str]:
    from nodvard_deck_updater import engine
    return {pattern for _method, pattern in engine.ENDPOINTS.values()}


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.relative_to(PACKAGE_DIR).as_posix())
def test_engine_paths_only_in_engine_and_only_from_the_table(path):
    allowed = _endpoint_patterns() if path.name == "engine.py" else set()
    assert engine_path_problems(path.read_text(encoding="utf-8"), path.name, allowed) == []


def test_endpoint_table_contains_nothing_forbidden():
    from nodvard_deck_updater import engine
    for method, pattern in engine.ENDPOINTS.values():
        assert method in {"GET", "POST", "DELETE"}
        assert not ENGINE_FORBIDDEN_RE.search(pattern), pattern
        assert pattern.startswith("/") and "?" not in pattern
    assert ("POST", "/containers/{id}/exec") not in engine.ENDPOINTS.values()


@pytest.mark.parametrize(("source", "allowed"), [
    ("x = '/containers/{id}/logs'", {"/containers/{id}/json"}),   # nicht in der Tabelle
    ("x = '/containers/{id}/json'", set()),                       # in einem anderen Modul
    ("x = '/info'", set()),
    ("x = '/events?since=1'", set()),
    ("x = f'/containers/{cid}/json'", set()),                     # f-String: der feste Anfang zaehlt
    ("x = '/images/' + ref", set()),
])
def test_engine_path_guard_catches(source, allowed):
    assert engine_path_problems(source, "<test>", allowed)


def test_engine_path_guard_allows_ordinary_paths():
    source = "a = '/app/updater'\nb = '/channel'\nc = '/proc/self/mountinfo'\nd = '/var/run/docker.sock'\n" \
             "e = '/state'\nf = 'containers/'\n\"\"\"Docstring /containers/{id}/exec\"\"\"\n"
    assert engine_path_problems(source, "<test>", set()) == []


def network_use(source: str) -> tuple[set[str], list[int]]:
    """Importierte Module und die Zeilen, die die Interna des Engine-Clients benutzen (`_request`, `_UnixConnection`)."""
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
    uses_internals = [node.lineno for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute) and node.attr in {"_request", "_UnixConnection"}
                      or isinstance(node, ast.Name) and node.id == "_UnixConnection"]
    return imported, uses_internals


def test_network_and_engine_internals_only_in_engine():
    for path in MODULES:
        imported, uses_internals = network_use(path.read_text(encoding="utf-8"))
        if path.name == "engine.py":
            assert {"socket", "http.client"} <= imported
            continue
        assert not imported & NETWORK_MODULES, (path.name, imported & NETWORK_MODULES)
        assert uses_internals == [], (path.name, uses_internals)


@pytest.mark.parametrize("source", [
    "import socket", "from socket import AF_UNIX", "import http.client", "from http import client",
    "engine._request('GET', '/_ping')", "from .engine import _UnixConnection\nc = _UnixConnection('x')",
    "self._engine._UnixConnection",
])
def test_network_guard_catches(source):
    # Negativtest zur Regel oben: jede dieser Zeilen ausserhalb von engine.py faellt auf.
    imported, uses_internals = network_use(source)
    assert imported & NETWORK_MODULES or uses_internals


def test_network_guard_allows_the_public_engine_api():
    imported, uses_internals = network_use("from .engine import Engine\nEngine().inspect_container('x')\n")
    assert not imported & NETWORK_MODULES and uses_internals == []


# ---------------------------------------------------------------------------
# Das Paket beschreibt sich selbst
# ---------------------------------------------------------------------------

INTERNAL_REFERENCE_RE = re.compile("|".join([
    "Bau" + "plan", "Be" + "fund", "Test" + "strategie", r"Ab" + r"schnitt [0-9]", r"\bFu" + r"nd [0-9]",
    r"\bPR ?[0-9]", r"\bM[0-9]{1,2}\b", r"\bSch" + r"ritt [0-9]", r"\b2c-[0-9]", r"docs/[0-9]{2}", r"\bEbene [0-9]",
    r"\w \([0-9]{1,2}(?:\.[0-9]{1,2})?(?:/[0-9]{1,2}(?:\.[0-9]{1,2})?)*\)(?=[.:,;\s)])",  # z. B. "je Endpunkt (6)."
    # Verweis auf eine Abschnittsnummer, z. B. "der Status nach 1.5"; eine Version wie "nach 0.7.1" bleibt
    r"\bnach [0-9]{1,2}\.[0-9]{1,2}\b(?!\.[0-9])",
]))
"""Verweise auf Planungsunterlagen, die nicht im Repository liegen (Nummern von Massnahmen, Abschnitten, Paketen)."""


def test_code_and_tests_refer_to_no_internal_planning_documents():
    # Das Paket liegt im oeffentlichen Repository: Kommentare, Docstrings, Testnamen und Vektoren muessen ohne
    # Unterlagen verstaendlich sein, die der Leser nicht hat. Aufnahmen unter `fixtures/` sind Daten der Engine.
    files = [*UPDATER_DIR.joinpath("nodvard_deck_updater").rglob("*.py"), *UPDATER_DIR.joinpath("tools").rglob("*.py"),
             *UPDATER_DIR.joinpath("tests").glob("*.py"), *UPDATER_DIR.joinpath("tests", "vectors").glob("*.json")]
    assert len(files) > 15
    found = [f"{path.relative_to(UPDATER_DIR).as_posix()}:{number}: {match.group(0)}"
             for path in files if path.name != os.path.basename(__file__)
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             for match in INTERNAL_REFERENCE_RE.finditer(line)]
    assert found == []


@pytest.mark.parametrize("text", [
    "siehe Bau" + "plan 2c-2", "(M10)", "Fu" + "nd 14", "Be" + "fund: x", "Ab" + "schnitt 6", "PR 3", "PR5", "2c-1",
    "Test" + "strategie", "Sch" + "ritt 3", "docs/05-X.md", "Abfrage je Endpunkt (6).", "der Engine (1.4): x",
    "Ebene 5", "Schritte des Journals (5.1/5.5) in ihrer Reihenfolge", "Der Status nach 1.5 (nur feste Codes).",
    "der Engine (12.10): x", "Journal (5.1/5.5/6).", "status.json nach 1.5", "wie nach 2.3.",
])
def test_internal_reference_pattern_catches(text):
    assert INTERNAL_REFERENCE_RE.search(text)


@pytest.mark.parametrize("text", [
    "der Abschnitt `files`", "oberste Ebene", "M", "Modul M", "PRIORITY", "Schritt `creating`", "range(5)",
    "os._exit(3)  # Absturz", "tief verschachtelt (2000)", "time.sleep(0.2)", "math.isfinite(1.0)",
    "nach dem Neustart", "Zeitstempel nach 2024", "Schritte des Journals in ihrer Reihenfolge",
    "Update von 0.7.0 nach 0.7.1", "nach 0.7.1-rc.1",
])
def test_internal_reference_pattern_leaves_normal_text_alone(text):
    assert not INTERNAL_REFERENCE_RE.search(text)
