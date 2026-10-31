"""Holt für „Stacks aus Git“ einen Stand und liest den Ordner der Compose-Datei, ohne Arbeitskopie, als eigener kurzer
Prozess (`python -I git_fetch.py`) mit Grenzen für Speicher, Rechenzeit und Dateigröße; Auftrag und Antwort sind JSON."""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import signal
import stat as statmod
import sys
import time
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any
from urllib.parse import urljoin, urlsplit

import urllib3
import yaml
from dulwich.client import (
    HTTPUnauthorized,
    Urllib3HttpGitClient,
    default_urllib3_manager,
)
from dulwich.errors import GitProtocolError, HangupException, NotGitRepository
from dulwich.objects import Blob, Commit, Tag, Tree
from dulwich.repo import Repo

FETCHED_REF = b"refs/nodvard/fetched"
LFS_POINTER = b"version https://git-lfs.github.com/spec/"
CONNECT_TIMEOUT_S = 15
READ_TIMEOUT_S = 60
MAX_REDIRECTS = 5
MAX_PROBLEMS = 20
MAX_WARNINGS = 40
_GITLINK = 0o160000
MEMORY = "Holen brauchte mehr Speicher, als Deck dafür hergibt. Ist das Repository ungewöhnlich groß?"
_NAME_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._+@=-][A-Za-z0-9._+@= -]{0,254}\Z")
"""Dieselben Zeichen, die Deck in Pfaden auf dem Server zulässt, ohne Leerzeichen am Rand."""


@dataclass(frozen=True)
class Limits:
    pack_bytes: int = 50 * 1024 * 1024
    refs_bytes: int = 16 * 1024 * 1024
    """Die erste Antwort, bei älteren Servern mit der Liste der Branches und Tags."""
    response_bytes: int = 55 * 1024 * 1024
    files: int = 300
    total_bytes: int = 10 * 1024 * 1024
    depth: int = 8
    entries: int = 5000
    """Einträge in allen Ordnern zusammen, auch in solchen, die mehrfach vorkommen."""
    compose_bytes: int = 256 * 1024
    seconds: float = 120
    memory_bytes: int = 512 * 1024 * 1024


class GitProblem(Exception):
    """Holen oder Lesen ging nicht; der Text ist für Menschen und enthält nie ein Geheimnis."""


class _Broken(GitProblem):
    """Ein Objekt fehlt oder hat die falsche Art."""


class _StoreDamaged(Exception):
    """Der Speicher vom letzten Holen lässt sich nicht lesen."""


class _ForeignOrigin(Exception):
    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.target = target


def name_ok(name: str) -> bool:
    return bool(_NAME_SEGMENT_RE.match(name)) and name not in (".", "..") and name.lower() != ".git" and not name.endswith(" ")


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):g} MB"


def _shown(text: str, limit: int = 120) -> str:
    """Text aus dem Repository oder vom Server für eine Meldung: gekürzt und ohne Steuer- oder Richtungszeichen."""
    text = "".join("?" if unicodedata.category(c).startswith("C") else c for c in " ".join(text.split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or (443 if scheme == "https" else 80)


class _SameOrigin:
    """Schickt urllib3-Anfragen nur an die eingetragene Adresse, auch keine Weiterleitung und kein vom Server genanntes
    Pack woandershin, und begrenzt jede Antwort nach Größe und Zeit."""

    def __init__(self, inner: Any, url: str, limits: Limits) -> None:
        self._inner = inner
        self._origin = _origin(url)
        self._limits = limits
        self._deadline = time.monotonic() + limits.seconds
        self.headers = inner.headers

    def _limited(self, resp: Any, limit: int) -> Any:
        read, left = resp.read, limit

        def limited_read(amt: int | None = None, *args: Any, **kwargs: Any) -> bytes:
            nonlocal left
            if time.monotonic() > self._deadline:
                raise GitProblem(f"Holen hat länger als {self._limits.seconds:g} Sekunden gedauert.")
            data = read(left + 1 if amt is None or amt > left + 1 else amt, *args, **kwargs)
            left -= len(data)
            if left < 0:
                raise GitProblem(f"Der Server schickt mehr als {_mb(limit)} auf einmal; so viel holt Deck nicht.")
            return data

        resp.read = limited_read
        return resp

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        for _ in range(MAX_REDIRECTS + 1):
            if _origin(url) != self._origin:
                raise _ForeignOrigin(url)
            resp = self._inner.request(method, url, redirect=False, retries=False, **kwargs)
            location = resp.get_redirect_location()
            if not location:
                return self._limited(resp, self._limits.refs_bytes if method == "GET" else self._limits.response_bytes)
            with contextlib.suppress(Exception):
                resp.close()
            if method != "GET":
                raise GitProblem("Der Server leitet beim Holen auf eine andere Adresse weiter. Trag die Adresse ein, unter der das Repository wirklich liegt.")
            url = urljoin(url, location)
        raise GitProblem("Der Server leitet zu oft weiter.")


class _SmartOnly(Urllib3HttpGitClient):
    """Nur Server mit dem Git-Protokoll über HTTP: Beim alten Abruf einzelner Dateien („dumb“) lädt dulwich alles ohne
    Grenze in den Speicher."""

    @property  # type: ignore[override]
    def dumb(self) -> bool:
        return False

    @dumb.setter
    def dumb(self, value: bool) -> None:
        if value:
            raise GitProblem("Der Server liefert das Repository nur als einzelne Dateien („dumb HTTP“). Das holt Deck nicht; "
                             "nimm die Adresse, die der Git-Server zum Klonen anzeigt.")


def http_client(url: str, credentials: tuple[str, str] | None, limits: Limits) -> tuple[Any, str]:
    """dulwich über HTTPS ohne Einstellungen aus Git-Konfigurationen; der Zugang geht nur an die eingetragene Adresse."""
    pool = _SameOrigin(default_urllib3_manager(None, base_url=url), url, limits)
    username, password = credentials if credentials else (None, None)
    client = _SmartOnly(
        url, pool_manager=pool, username=username, password=password,  # type: ignore[arg-type]
        timeout=urllib3.Timeout(connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S), quiet=True,
    )
    return client, urlsplit(url).path


class _NoHaves:
    """Meldet dem Server keine vorhandenen Commits: Er schickt immer den ganzen Stand des einen Commits."""

    def __init__(self) -> None:
        self.shallow: set[bytes] = set()

    def next(self) -> None:
        return None

    __next__ = next

    def ack(self, sha: bytes) -> None:
        pass

    def nak(self) -> None:
        pass


# Der Proxy zuerst: Ist er nicht erreichbar, steht auch „Connection refused“ im Text.
_NETWORK_TEXTS = (
    ("Unable to connect to proxy", "Der Proxy hat die Verbindung nicht hergestellt. Prüf HTTPS_PROXY in der Umgebung von Deck."),
    ("Tunnel connection failed", "Der Proxy hat die Verbindung nicht hergestellt. Prüf HTTPS_PROXY in der Umgebung von Deck."),
    ("CERTIFICATE_VERIFY_FAILED", ("Das Zertifikat des Servers ist nicht vertrauenswürdig, etwa weil es selbst ausgestellt ist. "
                                   "Deck holt nur von Servern mit einem gültigen Zertifikat.")),
    ("NameResolutionError", "Den Namen des Servers kennt das Netz nicht. Prüf die Adresse."),
    ("Name or service not known", "Den Namen des Servers kennt das Netz nicht. Prüf die Adresse."),
    ("ConnectTimeoutError", "Der Server antwortet nicht. Prüf die Adresse und ob Deck ihn erreichen kann."),
    ("Connection refused", "Der Server nimmt keine Verbindung an. Prüf Adresse und Port."),
    ("Failed to establish a new connection", "Der Server ist nicht erreichbar. Prüf Adresse und Port und ob Deck ihn erreichen kann."),
    ("ReadTimeoutError", "Der Server hat zu lange nicht geantwortet."),
    ("shallow", "Der Server kann nicht nur den neuesten Stand liefern (shallow); solche Server holt Deck noch nicht."),
)
_STATUS_TEXTS = {
    403: "Der Server verweigert den Zugriff. Reicht der Zugang für dieses Repository?",
    429: "Der Server nimmt gerade zu viele Anfragen an. Versuch es später noch einmal.",
}


def _fetch_problem(exc: BaseException, url: str, with_access: bool) -> GitProblem:
    if isinstance(exc, GitProblem):
        return exc
    if isinstance(exc, MemoryError):
        return GitProblem(MEMORY)
    if isinstance(exc, _ForeignOrigin):
        return GitProblem(f"Der Server schickt Deck zu einer anderen Adresse ({_shown(exc.target, 200)}). Trag die Adresse ein, "
                          "unter der das Repository wirklich liegt.")
    if isinstance(exc, HTTPUnauthorized):
        if with_access:
            return GitProblem("Der Server hat den Zugang abgelehnt. Prüf Benutzername und Token in den Einstellungen (Docker, Git-Zugänge).")
        return GitProblem("Das Repository verlangt eine Anmeldung. Trag einen Zugang in den Einstellungen ein (Docker, Git-Zugänge) und wähl ihn hier aus.")
    if isinstance(exc, NotGitRepository):
        return GitProblem(f"Unter {url} gibt es kein Git-Repository, oder es ist privat und braucht einen Zugang.")
    if isinstance(exc, HangupException):
        return GitProblem("Der Server hat die Verbindung beim Holen beendet. Versuch es gleich noch einmal.")
    text = str(exc)
    for marker, human in _NETWORK_TEXTS:
        if marker in text:
            return GitProblem(human)
    if found := re.search(r"unexpected http resp (\d{3})", text):
        code = int(found.group(1))
        return GitProblem(f"Der Server hat mit dem Fehler {code} geantwortet. " + _STATUS_TEXTS.get(
            code, "Eine Störung beim Git-Server; versuch es später noch einmal." if code >= 500 else "Prüf die Adresse."))
    if isinstance(exc, GitProtocolError):
        return GitProblem(f"Der Server hat nicht wie ein Git-Server geantwortet: {_shown(text, 200)}")
    if isinstance(exc, OSError):
        return GitProblem(f"Der Server ist nicht erreichbar: {_shown(text or type(exc).__name__, 200)}")
    return GitProblem(f"Holen ist gescheitert ({type(exc).__name__}).")


def _old_store(store: Path) -> Any | None:
    """Der Speicher vom letzten Holen, wenn er sich öffnen lässt; sonst wird er beim nächsten Tausch ersetzt."""
    if not ((store / "objects" / "pack").is_dir() and (store / "HEAD").is_file()):
        return None
    try:
        return Repo(str(store), bare=True)
    except Exception:  # noqa: BLE001 - ein kaputter Speicher hält nur Geholtes
        return None


def _pull(fresh: Any, old: Any | None, url: str, ref: str, credentials: tuple[str, str] | None,
          client_for: Callable[..., tuple[Any, str]], limits: Limits) -> tuple[bytes, str, bool]:
    """Holt genau den einen Commit von Branch oder Tag (Tiefe 1) in den frischen Speicher, außer der alte hat ihn schon;
    liefert Kennung, Art und ob er neu ist."""
    names = {b"refs/heads/" + ref.encode(): "branch", b"refs/tags/" + ref.encode(): "tag"}
    found: dict[str, Any] = {}
    deadline = time.monotonic() + limits.seconds

    def determine_wants(refs: dict[bytes, bytes], depth: int | None = None) -> list[bytes]:
        for name, kind in names.items():
            if refs.get(name):
                found.update(sha=refs[name], kind=kind)
                break
        else:
            return []
        try:
            found["new"] = old is None or found["sha"] not in old.object_store
        except Exception:  # noqa: BLE001 - ein kaputter alter Speicher wird ersetzt
            found["new"] = True
        return [found["sha"]] if found["new"] else []

    received = 0

    def write(chunk: bytes) -> int:
        nonlocal received
        received += len(chunk)
        if received > limits.pack_bytes:
            raise GitProblem(f"Das Repository ist größer als {_mb(limits.pack_bytes)}; so große holt Deck nicht.")
        if time.monotonic() > deadline:
            raise GitProblem(f"Holen hat länger als {limits.seconds:g} Sekunden gedauert.")
        return spool.write(chunk)

    # Wie dulwich selbst: Ein Pack kann „dünn“ sein und erst mit dem Speicher vollständig werden.
    with SpooledTemporaryFile(max_size=8 * 1024 * 1024, prefix="incoming-", dir=fresh.object_store.path) as spool:
        try:
            client, path = client_for(url, credentials, limits)
            result = client.fetch_pack(path, determine_wants, _NoHaves(), write, depth=1, ref_prefix=list(names))
            if result.object_format and result.object_format != fresh.object_format.name:
                raise GitProblem("Das Repository nutzt SHA-256; solche Repositorys holt Deck noch nicht.")
            if found.get("new") and spool.tell():
                spool.seek(0)
                fresh.object_store.add_thin_pack(spool.read, None)
        except Exception as exc:
            raise _fetch_problem(exc, url, credentials is not None) from exc
    if not found:
        raise GitProblem(f"Im Repository gibt es keinen Branch und kein Tag „{ref}“.")
    return found["sha"], found["kind"], found["new"]


def _object(repo: Any, sha: bytes, kind: type) -> Any:
    try:
        obj = repo[sha]
    except KeyError:
        raise _Broken("Im geholten Stand fehlen Teile. Hol ihn noch einmal.") from None
    if not isinstance(obj, kind):
        raise _Broken("Der geholte Stand ist kaputt: Ein Teil hat eine andere Art, als der Ordner angibt.")
    return obj


def _peel(repo: Any, sha: bytes) -> Any:
    obj = _object(repo, sha, (Commit, Tag))
    for _ in range(10):
        if isinstance(obj, Commit):
            return obj
        obj = _object(repo, obj.object[1], (Commit, Tag))
    raise GitProblem("Der Tag zeigt nicht auf einen Commit.")


def _when(commit: Any) -> str:
    try:
        zone = timezone(timedelta(seconds=commit.author_timezone))
        return datetime.fromtimestamp(commit.author_time, zone).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _subtree(repo: Any, tree: Any, folder: str) -> Any:
    for part in [p for p in folder.split("/") if p]:
        try:
            mode, sha = tree[part.encode()]
        except KeyError:
            raise GitProblem(f"Im Repository gibt es den Ordner „{folder}“ nicht.") from None
        if not statmod.S_ISDIR(mode):
            raise GitProblem(f"„{folder}“ ist im Repository kein Ordner.")
        tree = _object(repo, sha, Tree)
    return tree


def collect(repo: Any, tree: Any, limits: Limits) -> tuple[dict[str, tuple[int, bytes]], list[str]]:
    """Alle Dateien unter `tree` mit Rechten und Inhalt; Verweise, Submodule, LFS und ungewöhnliche Namen als Gründe."""
    files: dict[str, tuple[int, bytes]] = {}
    problems: list[str] = []
    total = entries = 0
    pending: list[tuple[str, Any, int]] = [("", tree, 1)]
    while pending and len(problems) < MAX_PROBLEMS:
        prefix, current, depth = pending.pop()
        for entry in current.iteritems():
            entries += 1
            if entries > limits.entries:
                problems.append(f"Der Ordner hat mehr als {limits.entries} Einträge.")
                return files, problems
            try:
                name = entry.path.decode("utf-8")
            except UnicodeDecodeError:
                problems.append(f"In „{_shown(prefix) or '.'}“ liegt eine Datei, deren Name nicht in UTF-8 geschrieben ist.")
                continue
            rel = prefix + name
            if not name_ok(name):
                problems.append(f"„{_shown(rel)}“: Der Name enthält Zeichen, die Deck nicht auf einen Server schreibt.")
            elif entry.mode == _GITLINK:
                problems.append(f"„{_shown(rel)}“ ist ein Submodul; Submodule holt Deck nicht.")
            elif statmod.S_ISLNK(entry.mode):
                problems.append(f"„{_shown(rel)}“ ist ein Verweis (Symlink); Verweise holt Deck nicht.")
            elif statmod.S_ISDIR(entry.mode):
                if depth >= limits.depth:
                    problems.append(f"„{_shown(rel)}“ liegt tiefer als {limits.depth} Ordner.")
                else:
                    pending.append((rel + "/", _object(repo, entry.sha, Tree), depth + 1))
            elif not statmod.S_ISREG(entry.mode):
                problems.append(f"„{_shown(rel)}“ ist keine normale Datei.")
            else:
                data = _object(repo, entry.sha, Blob).as_raw_string()
                total += len(data)
                if len(files) >= limits.files or total > limits.total_bytes:
                    problems.append(f"Der Ordner hat mehr als {limits.files} Dateien oder mehr als {_mb(limits.total_bytes)}.")
                    return files, problems
                if data.startswith(LFS_POINTER):
                    problems.append(f"„{_shown(rel)}“ liegt in Git LFS; solche Dateien holt Deck nicht.")
                    continue
                files[rel] = (0o755 if entry.mode & 0o111 else 0o644, data)
    return files, problems[:MAX_PROBLEMS]


class _ComposeLoader(yaml.SafeLoader):
    """Liest auch die Compose-Markierungen `!reset` und `!override`, ohne etwas auszuführen."""


def _tagged(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


_ComposeLoader.add_multi_constructor("!", _tagged)


_REMOTE_RE = re.compile(r"^[^/]+:")
"""`git@server:team/stacks.git`: Compose holt so eine Angabe aus dem Netz, auch ohne `://`."""


def _outside(raw: Any) -> str | None:
    """Wohin ein Pfad aus der Compose-Datei zeigt, wenn nicht in den Ordner aus dem Repository, sonst `None`."""
    if not isinstance(raw, str) or not raw:
        return None
    if "$" in raw:
        return f"{_shown(raw)} (enthält Variablen)"
    if raw.startswith(("/", "~")):
        return _shown(raw)
    if "://" in raw or _REMOTE_RE.match(raw):
        return f"{_shown(raw)} (aus dem Netz)"
    normal = posixpath.normpath(raw)
    return _shown(raw) if normal == ".." or normal.startswith("../") else None


def _paths(value: Any, key: str | None = None) -> list[Any]:
    """Pfade aus einer Angabe, die ein Text, eine Liste oder eine Liste von Einträgen mit `key` sein kann."""
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, dict) and key:
            inner = item.get(key)
            out.extend(inner if isinstance(inner, list) else [inner])
        else:
            out.append(item)
    return out


def _bind_source(volume: Any) -> Any:
    if isinstance(volume, dict):
        return volume.get("source") if volume.get("type") == "bind" else None
    if isinstance(volume, str) and ":" in volume:
        source = volume.split(":", 1)[0]
        return source if source.startswith(("/", ".", "~", "$")) else None
    return None


def load_compose(text: bytes) -> Any:
    """Die Compose-Datei als Daten; Markierungen wie `!reset` werden gelesen, nie ausgeführt."""
    return yaml.load(text.decode("utf-8"), Loader=_ComposeLoader)


def compose_warnings(text: bytes) -> tuple[list[str], str | None]:
    """Was die Compose-Datei von außerhalb des Ordners aus dem Repository holt oder auf dem Server baut, als Sätze; dazu
    der Grund, wenn sie sich nicht lesen lässt."""
    try:
        config = load_compose(text)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        return [], f"Die Compose-Datei ist kein gültiges YAML: {_shown(str(exc), 200)}"
    except RecursionError:
        return [], "Die Compose-Datei ist zu tief verschachtelt."
    if not isinstance(config, dict):
        return [], "Die Compose-Datei enthält keine Zuordnung mit services."
    found: list[str] = []
    for raw in _paths(config.get("include") or [], "path"):
        if where := _outside(raw):
            found.append(f"Die Datei bindet eine weitere Compose-Datei von außerhalb des Ordners ein (include: {where}).")
    services = config.get("services")
    for name, svc in sorted(services.items() if isinstance(services, dict) else [], key=lambda kv: str(kv[0])):
        if not isinstance(svc, dict):
            continue
        who = f"„{_shown(str(name))}“"
        extends = svc.get("extends")
        if isinstance(extends, dict) and (where := _outside(extends.get("file"))):
            found.append(f"{who} erbt Einstellungen aus einer Datei außerhalb des Ordners (extends: {where}).")
        outside_env = [w for raw in _paths(svc.get("env_file") or [], "path") if (w := _outside(raw))]
        if outside_env:
            found.append(f"{who} liest Variablen aus Dateien außerhalb des Ordners (env_file: {', '.join(outside_env)}).")
        volumes = svc.get("volumes")
        binds = [w for volume in (volumes if isinstance(volumes, list) else []) if (w := _outside(_bind_source(volume)))]
        if binds:
            found.append(f"{who} bindet Ordner oder Dateien vom Server ein, die nicht aus dem Repository kommen: {', '.join(binds)}.")
        build = svc.get("build")
        if build is not None:
            context = build.get("context", ".") if isinstance(build, dict) else build
            where = _outside(context)
            found.append(f"{who} wird auf dem Server gebaut (build): Dabei läuft Code aus dem Repository auf dem Server."
                         + (f" Gebaut wird von außerhalb des Ordners ({where})." if where else ""))
    for section, label in (("secrets", "Das Geheimnis"), ("configs", "Die Konfiguration")):
        entries = config.get(section)
        for name, entry in sorted(entries.items() if isinstance(entries, dict) else [], key=lambda kv: str(kv[0])):
            if isinstance(entry, dict) and (where := _outside(entry.get("file"))):
                found.append(f"{label} „{_shown(str(name))}“ kommt aus einer Datei außerhalb des Ordners ({where}).")
    return found[:MAX_WARNINGS], None


@dataclass
class Fetched:
    commit: str
    ref_kind: str
    author: str
    when: str
    message: str
    compose: str
    """Name der Compose-Datei im Ordner, der synchronisiert wird."""
    files: dict[str, tuple[int, bytes]]
    """Je Pfad relativ zum Ordner der Compose-Datei die Rechte (0644 oder 0755) und der Inhalt."""
    problems: list[str]
    warnings: list[str] = field(default_factory=list)


def _private(folder: Path) -> None:
    """dulwich setzt Packs selbst auf 0444; im Speicher darf nur Deck lesen, auch bei privaten Repositorys."""
    for path in (folder, *folder.rglob("*")):
        with contextlib.suppress(OSError):
            mode = path.lstat().st_mode
            if not statmod.S_ISLNK(mode) and statmod.S_IMODE(mode) & 0o077:
                os.chmod(path, statmod.S_IMODE(mode) & 0o700)


def _swap(store: Path, fresh_dir: Path) -> None:
    """Der frische Speicher ersetzt den alten; es bleibt immer nur der Stand vom letzten Holen."""
    aside = store.with_name(f"{store.name}.alt-{secrets.token_hex(4)}")
    if store.exists():
        os.replace(store, aside)
    os.replace(fresh_dir, store)
    shutil.rmtree(aside, ignore_errors=True)


def clean_leftovers(store: Path) -> None:
    """Halbe Speicher eines abgebrochenen Holens neben dem Speicher."""
    for path in store.parent.glob(f"{store.name}.*"):
        shutil.rmtree(path, ignore_errors=True)


def _read(repo: Any, sha: bytes, kind: str, path: str, limits: Limits) -> Fetched:
    commit = _peel(repo, sha)
    folder, compose = posixpath.split(path)
    files, problems = collect(repo, _subtree(repo, _object(repo, commit.tree, Tree), folder), limits)
    if compose not in files and not any(line.startswith(f"„{compose}“") for line in problems):
        problems.append(f"Im Repository gibt es die Datei „{path}“ nicht.")
    warnings: list[str] = []
    if compose in files and len(files[compose][1]) <= limits.compose_bytes:
        warnings, unreadable = compose_warnings(files[compose][1])
        if unreadable:
            problems.append(unreadable)
    repo.refs[FETCHED_REF] = commit.id
    author = commit.author.decode("utf-8", "replace").split(" <", 1)[0]
    message = commit.message.decode("utf-8", "replace").strip().splitlines()
    return Fetched(commit.id.decode(), kind, _shown(author), _when(commit), _shown(message[0] if message else "", 200),
                   compose, files, problems[:MAX_PROBLEMS], warnings)


def _fetch_into(url: str, ref: str, path: str, store: Path, credentials: tuple[str, str] | None,
                client_for: Callable[..., tuple[Any, str]], limits: Limits, old: Any | None) -> Fetched:
    fresh_dir = store.with_name(f"{store.name}.neu-{secrets.token_hex(4)}")
    fresh = Repo.init_bare(str(fresh_dir), mkdir=True)
    try:
        sha, kind, new = _pull(fresh, old, url, ref, credentials, client_for, limits)
        try:
            fetched = _read(fresh if new else old, sha, kind, path, limits)
        except (GitProblem, MemoryError) as exc:
            if new or not isinstance(exc, _Broken):
                raise
            raise _StoreDamaged from exc
        except Exception as exc:
            if new:
                raise
            raise _StoreDamaged from exc
    except BaseException:
        fresh.close()
        shutil.rmtree(fresh_dir, ignore_errors=True)
        raise
    finally:
        if old is not None:
            old.close()
    fresh.close()
    if new:
        _private(fresh_dir)
        _swap(store, fresh_dir)
    else:
        shutil.rmtree(fresh_dir, ignore_errors=True)
    return fetched


def fetch(url: str, ref: str, path: str, store: Path, credentials: tuple[str, str] | None = None,
          client_for: Callable[..., tuple[Any, str]] | None = None, limits: Limits | None = None) -> Fetched:
    """Holt den Stand in einen frischen Speicher und liest den Ordner der Compose-Datei direkt aus dem Objektspeicher:
    Filter, Hooks und Einstellungen aus dem Repository greifen so nie."""
    limits = limits or Limits()
    clean_leftovers(store)
    store.parent.mkdir(parents=True, exist_ok=True)
    try:
        return _fetch_into(url, ref, path, store, credentials, client_for or http_client, limits, _old_store(store))
    except _StoreDamaged:
        # Der Stand vom letzten Holen ließ sich nicht lesen: weg damit und gleich einmal ganz neu holen.
        shutil.rmtree(store, ignore_errors=True)
        return _fetch_into(url, ref, path, store, credentials, client_for or http_client, limits, None)


def handle(request: dict[str, Any], client_for: Callable[..., tuple[Any, str]] | None = None) -> dict[str, Any]:
    """Ein Auftrag als Zuordnung, die Antwort ebenso; jeder Fehler wird zu einem Satz."""
    limits = Limits(**request.get("limits") or {})
    credentials = tuple(request["credentials"]) if request.get("credentials") else None
    try:
        fetched = fetch(request["url"], request["ref"], request["path"], Path(request["store"]), credentials,  # type: ignore[arg-type]
                        client_for, limits)
    except GitProblem as exc:
        return {"ok": False, "problem": str(exc)}
    except MemoryError:
        return {"ok": False, "problem": MEMORY}
    except Exception as exc:  # noqa: BLE001 - nie ein 500 ohne Grund; der Name der Ausnahme hilft beim Nachsehen
        return {"ok": False, "problem": f"Holen ist gescheitert ({type(exc).__name__}).", "error": type(exc).__name__}
    out = asdict(fetched)
    out["files"] = [{"path": rel, "mode": f"{mode:o}", "data": base64.b64encode(data).decode()} for rel, (mode, data) in sorted(fetched.files.items())]
    return {"ok": True, **out}


def _confine(limits: Limits) -> None:
    """Grenzen für diesen Prozess; ohne `resource` (Windows) bleibt es bei denen aus dem Auftrag."""
    os.umask(0o077)
    try:
        import resource
    except ImportError:
        return
    # Kein Kern-Abzug: Er enthielte das Token und den ganzen Speicher des Prozesses.
    for name, value in (("RLIMIT_AS", limits.memory_bytes), ("RLIMIT_CPU", int(limits.seconds) + 5),
                        ("RLIMIT_FSIZE", 2 * limits.pack_bytes + 16 * 1024 * 1024), ("RLIMIT_CORE", 0)):
        kind = getattr(resource, name, None)
        if kind is not None:
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(kind, (value, value))


def main() -> int:
    request = json.loads(sys.stdin.buffer.read(1024 * 1024))
    limits = Limits(**request.get("limits") or {})
    _confine(limits)
    # Endet auch dann, wenn Deck selbst hart beendet wurde und ihn nicht mehr abbrechen kann.
    if hasattr(signal, "alarm"):
        signal.alarm(int(limits.seconds) + 15)
    # dulwich warnt bei jeder unbekannten Zeile; ein feindlicher Server könnte so das Protokoll fluten.
    logging.disable(logging.CRITICAL)
    for name in ("https_proxy", "http_proxy", "all_proxy", "no_proxy"):
        if name not in os.environ and name.upper() in os.environ:
            os.environ[name] = os.environ[name.upper()]
    sys.stdout.write(json.dumps(handle(request)))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
