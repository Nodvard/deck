"""Image-Updates prüfen, rein lesend: hat die Registry unter demselben Tag eine neuere Fassung als der Container nutzt?

Verglichen werden nur Prüfsummen (Digests), nichts wird geladen, neu erstellt oder gestartet. Einspielen: `applier.py`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nodvard_sdk import Notification, Severity

from .capabilities import CONTAINER_NAME_RE

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

log = logging.getLogger("nodvard_deck.ext.service-matrix")

# Antworten der Registry gelten 6 Stunden je Host (schont das Abruflimit von Docker Hub); der lokale Stand wird bei jeder
# Prüfung frisch gelesen, ein Neustart von Nodvard Deck leert den Speicher.
CACHE_TTL_S = 6 * 3600
MATRIX_PATH = "/ext/service-matrix/matrix"
STATE_FILE = "image-updates-state.json"
DEFAULT_CRON = "30 6 * * *"
JOB_ID = "image-updates"
_NEVER = "0 0 31 2 *"  # 31. Februar: der Job existiert, läuft aber nie (wie bei Shield)

MAX_PARALLEL_HOSTS = 3
LOCAL_TIMEOUT_S = 20
REMOTE_TIMEOUT_S = 45

CURRENT, UPDATE, UNKNOWN, LOCAL = "current", "update", "unknown", "local"
LOCAL_REASON = "Selbst gebautes Image – es gibt dafür keine Registry, Updates kommen über den eigenen Build (beim Dashboard: Deploy)."
"""`LOCAL`: Container mit selbst gebautem Image. Ein klares Ergebnis wie "aktuell": kein Problem, keine Meldung, und die
Registry wird dadurch nicht gesperrt."""
TRANSIENT = frozenset({"rate_limit", "network"})
"""Registry-Probleme, die vorbeigehen: dann bleibt die letzte Antwort der Registry stehen (auch bei "Registry neu
abfragen"), und bis zum Ende der Prüfung wird dieselbe Registry nicht weiter gefragt."""
# Docker Hub antwortet mit 401/denied auch auf ein Repository, das es nicht gibt, und jedes echte offizielle Image ist
# öffentlich: bei einem offiziellen Hub-Namen (`is_official_hub_repo`) heißt `auth` darum "selbst gebaut" (etwa per
# `docker load` aufs Gerät gebracht; mit dem containerd-Image-Speicher hat es dann trotzdem RepoDigests). Bewusst nicht
# `not_found`: ein entfernter Tag eines echten Images (`nginx:1.27`) heißt "manifest unknown" und bleibt "nicht prüfbar".
SELF_BUILT_HINTS = frozenset({"auth"})
"""Registry-Antworten, die bei einem offiziellen Hub-Namen "selbst gebaut" heißen. `auth` ist nicht vorbeigehend und
sperrt die Registry darum nicht (nicht in `TRANSIENT`)."""

# --- Namen prüfen ----------------------------------------------------------------------

_NAME_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_DOMAIN_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_DOMAIN = rf"{_DOMAIN_LABEL}(?:\.{_DOMAIN_LABEL})*(?::[0-9]{{1,5}})?"
_TAG = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"
_DIGEST = r"sha256:[a-f0-9]{64}"
IMAGE_REF_RE = re.compile(
    rf"^(?:{_DOMAIN}/)?{_NAME_COMPONENT}(?:/{_NAME_COMPONENT})*(?::{_TAG})?(?:@{_DIGEST})?\Z"
)
"""`[registry[:port]/]pfad[:tag][@sha256:...]` nach Dockers Referenzgrammatik. Beginnt
immer mit Buchstabe/Ziffer (nie mit `-`: kein Optionsschmuggel) und kennt weder
Leerzeichen noch Shell-Zeichen. `\\Z` statt `$`: ein Zeilenumbruch am Ende ist kein Treffer."""
IMAGE_REF_MAX_LEN = 255
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
_ID_LIKE_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{12,64}\Z")
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}\Z")
_DIGEST_RE = re.compile(rf"^{_DIGEST}\Z")
_HUB_HOSTS = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})


def is_valid_ref(ref: str) -> bool:
    return len(ref) <= IMAGE_REF_MAX_LEN and IMAGE_REF_RE.match(ref) is not None


def split_ref(ref: str) -> tuple[str, str, str | None]:
    """`registry/pfad:tag@digest` -> (Name ohne Tag, Tag, Digest). Der Doppelpunkt eines Tags steht hinter dem letzten
    Schrägstrich (davor wäre es ein Registry-Port)."""
    name, _, digest = ref.partition("@")
    colon = name.rfind(":")
    if colon > name.rfind("/"):
        return name[:colon], name[colon + 1:], digest or None
    return name, "", digest or None


def registry_of(repo: str) -> str:
    first, sep, _ = repo.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        return "docker.io" if first in _HUB_HOSTS else first
    return "docker.io"


def canonical_repo(repo: str) -> str:
    """Docker Hub in einer Schreibweise (`docker.io/library/nginx` == `nginx`); RepoDigests nennen die Images in ihrer
    kurzen Form."""
    first, sep, rest = repo.partition("/")
    if sep and first in _HUB_HOSTS:
        repo = rest
    repo = repo.removeprefix("library/")
    return repo


def is_official_hub_repo(repo: str) -> bool:
    """Docker Hub ohne Namespace und ohne eigene Registry, also ein offizielles `library/`-Image
    (`lattice`, `library/lattice`, `docker.io/library/lattice`). Nach `canonical_repo` bleibt dann kein Schrägstrich
    übrig; `someuser/lattice`, `ghcr.io/x` und `registry.local:5000/x` behalten ihren."""
    return "/" not in canonical_repo(repo)


@dataclass(frozen=True)
class RefInfo:
    ref: str
    repo: str = ""
    registry: str = ""
    query: str = ""
    """So wird die Registry gefragt -- immer mit Tag (ohne Angabe gilt `latest`)."""
    problem: str | None = None
    """Grund für "nicht prüfbar", schon vor jedem Befehl klar."""


def analyze_ref(ref: str) -> RefInfo:
    ref = (ref or "").strip()
    if not ref or ref == "<none>":
        return RefInfo(ref, problem="Image ohne Namen")
    if _ID_LIKE_RE.match(ref):
        return RefInfo(ref, problem="Container nutzt das Image nur per ID -- ohne Namen gibt es nichts zu vergleichen")
    if not is_valid_ref(ref):
        return RefInfo(ref, problem="Image-Name hat ein ungewöhnliches Format")
    repo, tag, digest = split_ref(ref)
    if digest:
        return RefInfo(ref, problem="Per Digest (sha256) festgelegt -- bleibt absichtlich unverändert")
    return RefInfo(ref, repo=repo, registry=registry_of(repo), query=f"{repo}:{tag or 'latest'}")


# --- Befehle bauen ---------------------------------------------------------------------

# Jeder Image-Name, der in ein SSH-Kommando wandert, geht vorher durch `IMAGE_REF_RE` und `shlex.quote`: ein Name wie
# `x; rm -rf /` kommt nie bis zur Shell. Die Befehlsbauer prüfen selbst, nicht der Aufrufer.
_q = shlex.quote

CMD_LIST_IDS = "docker ps -q"
CMD_PLATFORM = "docker version --format " + _q("{{.Server.Os}}/{{.Server.Arch}}")
_CONTAINER_FORMAT = "{{.Name}}|{{.Config.Image}}|{{.Image}}"
_LOCAL_FORMAT = '{"repo_digests": {{json .RepoDigests}}, "id": {{json .Id}}}'
_IMAGETOOLS_FORMAT = "{{json .Manifest.Digest}}"


def _checked_ref(ref: str) -> str:
    if not is_valid_ref(ref):
        raise ValueError(f"Ungültiger Image-Name: {ref!r}")
    return _q(ref)


def cmd_inspect_containers(ids: list[str]) -> str:
    for container_id in ids:
        if not _CONTAINER_ID_RE.match(container_id):
            raise ValueError(f"Ungültige Container-ID: {container_id!r}")
    return f"docker container inspect --format {_q(_CONTAINER_FORMAT)} " + " ".join(_q(i) for i in ids)


def cmd_inspect_named(names: list[str]) -> str:
    """Wie `cmd_inspect_containers`, aber nach Container-Namen (auch gestoppte)."""
    for name in names:
        if not CONTAINER_NAME_RE.match(name):
            raise ValueError(f"Ungültiger Containername: {name!r}")
    return f"docker container inspect --format {_q(_CONTAINER_FORMAT)} " + " ".join(_q(n) for n in names)


def cmd_local_inspect(target: str) -> str:
    """`target`: die Image-ID des Containers (sha256:...) oder ein gültiger Image-Name."""
    quoted = _q(target) if _IMAGE_ID_RE.match(target) else _checked_ref(target)
    return f"docker image inspect --format {_q(_LOCAL_FORMAT)} {quoted}"


def cmd_remote_imagetools(query: str) -> str:
    return f"docker buildx imagetools inspect {_checked_ref(query)} --format {_q(_IMAGETOOLS_FORMAT)}"


def cmd_remote_manifest(query: str) -> str:
    return f"docker manifest inspect -v {_checked_ref(query)}"


# --- Ausgaben lesen --------------------------------------------------------------------


def parse_containers(text: str) -> list[tuple[str, str, str]]:
    """`Name|Image-Name|Image-ID` je Zeile -> [(name, ref, image_id)], sortiert nach Name."""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3 or not parts[0].strip("/"):
            continue
        rows.append((parts[0].lstrip("/"), parts[1].strip(), parts[2].strip()))
    return sorted(rows)


@dataclass(frozen=True)
class LocalImage:
    repo_digests: tuple[str, ...]
    image_id: str | None


def parse_local_image(text: str) -> LocalImage | None:
    try:
        data = json.loads(text.strip())
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("repo_digests") or []  # ältere Docker-Versionen: null
    digests = tuple(str(d) for d in raw if isinstance(d, str)) if isinstance(raw, list) else ()
    image_id = data.get("id")
    return LocalImage(digests, image_id if isinstance(image_id, str) and image_id else None)


def digests_for_repo(repo_digests: tuple[str, ...], repo: str) -> set[str]:
    """Nur die RepoDigests, die zu diesem Namen gehören: dasselbe Image kann unter mehreren Namen/Registries geladen
    sein, jeder mit eigener Prüfsumme."""
    wanted = canonical_repo(repo)
    found: set[str] = set()
    for entry in repo_digests:
        name, _, digest = entry.partition("@")
        if _DIGEST_RE.match(digest) and canonical_repo(name) == wanted:
            found.add(digest)
    return found


@dataclass(frozen=True)
class RemoteAnswer:
    digest: str
    """Zum Anzeigen und Merken (Meldung nur bei einer neuen Version)."""
    matches: frozenset[str]
    """Alles, womit ein lokales Image als "gleich" gilt."""
    source: str  # "imagetools" | "manifest"
    fetched_at: float = 0.0


def parse_imagetools_digest(text: str) -> str | None:
    """`docker buildx imagetools inspect <name> --format '{{json .Manifest.Digest}}'`
    -> `"sha256:..."` (ein JSON-Text)."""
    try:
        value = json.loads(text.strip())
    except ValueError:
        return None
    return value if isinstance(value, str) and _DIGEST_RE.match(value) else None


def parse_manifest_inspect(text: str, platform: str) -> RemoteAnswer | None:
    """`docker manifest inspect -v <name>`: bei mehreren Plattformen eine Liste, sonst ein Objekt. Nur die Einträge der
    Plattform des Hosts (`linux/arm64`) zählen. Als "gleich" gilt die Prüfsumme des Manifests und die der
    Image-Konfiguration; die ist bei klassischem Image-Speicher die lokale Image-ID (`.Id`)."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    entries = data if isinstance(data, list) else [data]
    want_os, _, want_arch = platform.partition("/")
    matches: set[str] = set()
    first: str | None = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        descriptor = entry.get("Descriptor") or {}
        plat = descriptor.get("platform") or {}
        if plat and (plat.get("os") != want_os or plat.get("architecture") != want_arch):
            continue
        digest = descriptor.get("digest")
        if isinstance(digest, str) and _DIGEST_RE.match(digest):
            matches.add(digest)
            first = first or digest
        for key in ("SchemaV2Manifest", "OCIManifest"):
            config = (entry.get(key) or {}).get("config") or {}
            config_digest = config.get("digest")
            if isinstance(config_digest, str) and _DIGEST_RE.match(config_digest):
                matches.add(config_digest)
    if not matches:
        return None
    return RemoteAnswer(first or min(matches), frozenset(matches), "manifest")


_NO_IMAGETOOLS_RE = re.compile(r"is not a docker command|unknown command|unknown (?:shorthand )?flag", re.IGNORECASE)


@dataclass(frozen=True)
class RegistryProblem:
    kind: str  # rate_limit | credentials | auth | not_found | network | error
    message: str


def classify_registry_error(stderr: str, exit_code: int = 1) -> RegistryProblem:
    """Fehlertext der Registry -> verständlicher Grund. Die Reihenfolge zählt: Docker Hubs Abruflimit (`toomanyrequests`)
    darf nicht als "Anmeldung" enden, ein fehlendes Anmelde-Hilfsprogramm (`docker-credential-...: executable file not
    found`) weder als "Anmeldung verweigert" noch als "Image nicht gefunden"."""
    text = (stderr or "").strip()
    low = text.lower()
    if "toomanyrequests" in low or "too many requests" in low or "rate limit" in low or re.search(r"\b429\b", low):
        return RegistryProblem("rate_limit", "Abruflimit der Registry erreicht (Docker Hub erlaubt nur wenige anonyme Abfragen) -- später erneut versuchen")
    if re.search(r"docker-credential|error getting credentials|credential helper|credentials store", low):
        return RegistryProblem("credentials", "Die gespeicherten Anmeldedaten des Hosts sind nicht lesbar (docker-credential-Hilfsprogramm fehlt oder schlägt fehl) -- auf dem Host prüfen")
    # Ein fehlendes Programm oder ein fehlender Dateizugriff ist keine Antwort der Registry.
    low_registry = re.sub(r"permission denied|(?:executable file|command) not found", "", low)
    if any(k in low_registry for k in ("unauthorized", "denied", "authentication required", "requires authentication")) or re.search(r"\b40[13]\b", low):
        return RegistryProblem("auth", "Registry verweigert den Zugriff (privates oder dort nicht vorhandenes Image) -- ggf. auf dem Host per docker login anmelden")
    if any(k in low_registry for k in ("manifest unknown", "not found", "no such manifest")) or re.search(r"\b404\b", low):
        return RegistryProblem("not_found", "Image bei der Registry nicht gefunden")
    if any(k in low for k in (
        "no such host", "timeout", "timed out", "connection refused", "network is unreachable", "dial tcp",
        "tls handshake", "temporary failure in name resolution", "no route to host",
    )):
        return RegistryProblem("network", "Registry vom Host aus nicht erreichbar")
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return RegistryProblem("error", f"Abfrage fehlgeschlagen: {line[:160] or f'Exit-Code {exit_code}'}")


# --- Ergebnis je Container -------------------------------------------------------------


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def _result(
    ref: str, status: str, *, reason: str | None = None, remote: RemoteAnswer | None = None, stale: bool = False,
) -> dict[str, Any]:
    return {
        "image": ref,
        "status": status,
        "reason": reason,
        "remote_digest": remote.digest if remote else None,
        "registry_at": _iso(remote.fetched_at) if remote and remote.fetched_at else None,
        # Die Registry hat diesmal nicht geantwortet -- `remote_digest` ist die letzte Antwort.
        "stale": stale,
    }


def _failed(what: str, result: Any) -> str:
    detail = result.stderr.strip() or f"Exit-Code {result.exit_code}"
    return f"{what} fehlgeschlagen: {detail}"


class HostProblem(Exception):
    """Der Host selbst antwortet nicht (SSH weg, Docker-Daemon aus): die ganze Prüfung dieses Hosts bricht ab, statt jedes
    Image einzeln in denselben Fehler laufen zu lassen."""


@dataclass
class HostState:
    checked_at: float | None
    """`None`, solange es noch nie einen erfolgreichen Durchlauf gab (nur ein Fehler)."""
    results: dict[str, dict[str, Any]]
    """Container-Name -> Ergebnis (`_result`)."""
    remote: dict[str, RemoteAnswer] = field(default_factory=dict)
    """Image-Name (mit Tag) -> Antwort der Registry, wiederverwendet bis `CACHE_TTL_S`."""
    error: str | None = None


@dataclass
class _Run:
    """Zustand einer Prüfung eines Hosts."""

    host: Any
    now: float
    imagetools: bool | None = None
    platform: str | None = None
    local: dict[str, LocalImage | str] = field(default_factory=dict)
    remote: dict[str, RemoteAnswer] = field(default_factory=dict)
    fallback: dict[str, RemoteAnswer] = field(default_factory=dict)
    """Letzte (noch gültige) Antworten der Registry, auch bei `force`, damit ein Abruflimit oder eine Netzstörung sie
    nicht löscht."""
    kept: dict[str, RemoteAnswer] = field(default_factory=dict)
    """Davon die, die diesmal gezeigt wurden, weil die Registry nicht antwortete."""
    blocked: dict[str, RegistryProblem] = field(default_factory=dict)
    """Registry -> vorübergehendes Problem (`TRANSIENT`): in diesem Lauf nicht weiter fragen (jede Frage an eine
    unerreichbare Registry kostet bis zu `REMOTE_TIMEOUT_S`)."""


def _write_atomic(path: Path, text: str) -> None:
    """Erst in eine Nachbardatei, dann umbenennen: ein Absturz mitten im Schreiben lässt nie eine halbe Datei zurück
    (sonst begänne der Stand bei {} und alles würde neu gemeldet)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class ImageUpdateService:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._hosts: dict[str, HostState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._checking: set[str] = set()
        self._tasks: set[asyncio.Task[Any]] = set()  # Referenz halten, sonst räumt der GC sie weg
        self._clock = time.time  # Test-Einhänger
        self.apply_hints: Callable[[Any, list[str]], Awaitable[dict[str, dict[str, Any]]]] | None = None
        """Haken für `applier.py`: bekommt den Host und die Namen der Container mit Update und liefert je Name
        `{"mode": "compose"|"none", ...}`; das steht dann als `apply` im Ergebnis."""

    # --- Lesen ---------------------------------------------------------------------

    async def _tag(self) -> str:
        return (await self._ctx.settings.get()).get("docker_host_tag") or "docker"

    async def docker_hosts(self) -> list[Any]:
        hosts = list(await self._ctx.hosts.list(tag=await self._tag()))
        self._prune({h.id for h in hosts})
        return hosts

    def _prune(self, current: set[str]) -> None:
        """Hosts, die keine Docker-Hosts mehr sind (Markierung weg, Host gelöscht), aus dem Stand nehmen, sonst zählen sie
        bis zum Neustart in der Zusammenfassung mit. Wer weiter Docker-Host ist, behält seinen Stand, auch wenn er nicht
        antwortet. Eine laufende Prüfung (Lock oder `_checking`) bleibt unberührt."""
        for host_id in set(self._hosts) - current - self._checking:
            del self._hosts[host_id]
        for host_id, lock in list(self._locks.items()):
            if host_id not in current and host_id not in self._checking and not lock.locked():
                del self._locks[host_id]

    def snapshot(self) -> dict[str, Any]:
        """Stand für die Seite. Schlüssel wie `list_services()` (`host_id:container`)."""
        data: dict[str, Any] = {}
        hosts: dict[str, Any] = {}
        for host_id in sorted(set(self._hosts) | self._checking):
            state = self._hosts.get(host_id)
            hosts[host_id] = {
                "checking": host_id in self._checking,
                "checked_at": _iso(state.checked_at) if state and state.checked_at is not None else None,
                "error": state.error if state else None,
            }
            for name, result in (state.results if state else {}).items():
                data[f"{host_id}:{name}"] = result
        return {"data": data, "hosts": hosts, "cache_ttl_s": CACHE_TTL_S}

    # --- Prüfen --------------------------------------------------------------------

    def start(self, hosts: list[Any], *, force: bool = False) -> list[str]:
        """Prüfung im Hintergrund (ein Klick soll nicht eine Minute auf die Antwort warten). Hosts, die schon geprüft
        werden, bleiben dabei. Gibt die IDs zurück, die neu gestartet wurden."""
        todo = [h for h in hosts if h.id not in self._checking]
        self._checking.update(h.id for h in todo)  # sofort sichtbar: "prüft ..."
        if todo:
            task = asyncio.ensure_future(self.check_hosts(todo, force=force))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return [h.id for h in todo]

    async def check_hosts(self, hosts: list[Any], *, force: bool = False) -> None:
        sem = asyncio.Semaphore(MAX_PARALLEL_HOSTS)

        async def one(host: Any) -> None:
            async with sem:
                await self.check_host(host, force=force)

        await asyncio.gather(*(one(h) for h in hosts))

    async def check_host(self, host: Any, *, force: bool = False) -> None:
        # Ein Host wird nie zweimal gleichzeitig geprüft; wer ein zweites Mal kommt (z. B. der Tagesjob während eines
        # Klicks), wartet und prüft danach selbst frisch.
        lock = self._locks.setdefault(host.id, asyncio.Lock())
        async with lock:
            self._checking.add(host.id)
            try:
                await self._check_locked(host, force)
            except HostProblem as exc:
                self._record_error(host.id, str(exc))
            except Exception as exc:
                log.exception("image_updates_check_failed host=%s", host.id)
                self._record_error(host.id, f"Prüfung fehlgeschlagen: {exc}")
            finally:
                self._checking.discard(host.id)

    def _record_error(self, host_id: str, message: str) -> None:
        # Der letzte gute Stand bleibt sichtbar; nur die Meldung kommt dazu. Gab es noch keinen, bleibt `checked_at` leer:
        # ein Fehler ist keine Prüfung.
        state = self._hosts.get(host_id) or HostState(checked_at=None, results={})
        state.error = message
        self._hosts[host_id] = state

    async def _exec(self, host: Any, command: str, timeout_s: int) -> Any:
        try:
            return await self._ctx.exec.run(host, command, timeout_s=timeout_s)
        except Exception as exc:
            raise HostProblem(f"Nicht erreichbar: {exc}") from exc

    # Ablauf je Host: `docker ps -q` und `docker container inspect` liefern Name, Image-Name (wie angelegt) und die ID des
    # Images, das der Container wirklich nutzt. Je Image folgt lokal `docker image inspect` (RepoDigests und ID), je
    # Image-Name bei der Registry `docker buildx imagetools inspect` (liest nur den Manifest-Kopf) oder, wenn buildx fehlt,
    # `docker manifest inspect -v` (dort zählt die Plattform des Hosts).
    async def _check_locked(self, host: Any, force: bool) -> None:
        now = self._clock()
        previous = self._hosts.get(host.id)
        run = _Run(host=host, now=now)
        if previous:
            run.fallback = {k: a for k, a in previous.remote.items() if now - a.fetched_at < CACHE_TTL_S}
            if not force:
                run.remote = dict(run.fallback)

        listed = await self._exec(host, CMD_LIST_IDS, LOCAL_TIMEOUT_S)
        if listed.exit_code != 0:
            raise HostProblem(_failed("docker ps", listed))
        ids = listed.stdout.split()
        containers: list[tuple[str, str, str]] = []
        if ids:
            inspected = await self._exec(host, cmd_inspect_containers(ids), LOCAL_TIMEOUT_S)
            # Ein Container, der zwischen `ps` und `inspect` verschwindet, lässt den Exit-Code auf 1 springen, die
            # übrigen Zeilen sind trotzdem da.
            if inspected.exit_code != 0 and not inspected.stdout.strip():
                raise HostProblem(_failed("docker inspect", inspected))
            containers = parse_containers(inspected.stdout)

        results = {name: await self._check_container(run, ref, image_id) for name, ref, image_id in containers}
        await self._add_hints(host, results)
        # Nur Antworten behalten, die zu heutigen Containern gehören (sonst wüchse der Speicher).
        wanted = {analyze_ref(ref).query for _, ref, _ in containers}
        # Antworten, die diesmal nicht erneuerbar waren, bleiben mit ihrem Alter stehen, bis ihre Zeit um ist.
        remote = {q: a for q, a in (run.kept | run.remote).items() if q in wanted}
        # Frisch von der Registry geholt -> jetzt; wiederverwendet -> behält sein Alter.
        self._hosts[host.id] = HostState(checked_at=now, results=results, remote=remote)

    async def _add_hints(self, host: Any, results: dict[str, dict[str, Any]]) -> None:
        """Ein zusätzlicher lesender Aufruf je Host, nur wenn es Container mit Update gibt. Ein Fehler dabei ändert nichts
        am Ergebnis der Prüfung (dann gibt es kein `apply`)."""
        if self.apply_hints is None:
            return
        names = sorted(n for n, r in results.items() if r["status"] == UPDATE)
        if not names:
            return
        try:
            hints = await self.apply_hints(host, names)
        except Exception:  # noqa: BLE001 - die Prüfung selbst ist wichtiger als der Hinweis
            log.exception("image_updates_apply_hints_failed host=%s", host.id)
            return
        for name, hint in hints.items():
            if name in results:
                results[name]["apply"] = hint

    def result_for(self, host_id: str, container: str) -> dict[str, Any] | None:
        state = self._hosts.get(host_id)
        return state.results.get(container) if state else None

    async def recheck_container(self, host: Any, name: str) -> dict[str, Any] | None:
        """Nach einem Update: nur diesen Container neu bewerten; `None`, wenn er nicht mehr da ist (sein Eintrag fällt weg).
        Lokal wird frisch gelesen, die Registry nur gefragt, wenn ihre letzte Antwort älter als `CACHE_TTL_S` ist (sonst ist
        der Container sofort "aktuell", ohne das Abruflimit zu verbrauchen). `checked_at` bleibt unangetastet."""
        lock = self._locks.setdefault(host.id, asyncio.Lock())
        async with lock:
            now = self._clock()
            state = self._hosts.get(host.id)
            run = _Run(host=host, now=now)
            if state:
                run.fallback = {k: a for k, a in state.remote.items() if now - a.fetched_at < CACHE_TTL_S}
                run.remote = dict(run.fallback)  # bewusst ohne `force`
            found = await self._exec(host, cmd_inspect_named([name]), LOCAL_TIMEOUT_S)
            rows = parse_containers(found.stdout) if found.exit_code == 0 else []
            if not rows:
                if state:
                    state.results.pop(name, None)
                return None
            _, ref, image_id = rows[0]
            result = await self._check_container(run, ref, image_id)
            results = {name: result}
            await self._add_hints(host, results)
            state = self._hosts.get(host.id) or HostState(checked_at=None, results={})
            state.results[name] = result
            state.remote.update(run.kept | run.remote)
            self._hosts[host.id] = state
            return result

    async def _check_container(self, run: _Run, ref: str, image_id: str) -> dict[str, Any]:
        info = analyze_ref(ref)
        if info.problem:
            return _result(ref, UNKNOWN, reason=info.problem)

        # Lokal: das Image, das der Container wirklich nutzt (die ID), nicht das, worauf der Name heute zeigt: nach einem
        # Pull ohne Neustart zeigt der Name schon aufs neue.
        target = image_id if _IMAGE_ID_RE.match(image_id) else ref
        if target not in run.local:
            run.local[target] = await self._inspect_local(run, target)
        local = run.local[target]
        if isinstance(local, str):
            return _result(ref, UNKNOWN, reason=local)
        repo_digests = digests_for_repo(local.repo_digests, info.repo)
        if not repo_digests:
            if not local.repo_digests:
                return _result(ref, LOCAL, reason=LOCAL_REASON)  # nie aus einer Registry geladen
            return _result(ref, UNKNOWN, reason="Image stammt aus einer anderen Registry, als der Name angibt")

        answer = run.remote.get(info.query)
        if answer is None:
            got = await self._registry_answer(run, info)
            if isinstance(got, RegistryProblem):
                if got.kind in SELF_BUILT_HINTS and is_official_hub_repo(info.repo):
                    # Offizielle Images sind öffentlich: verweigert Docker Hub den Zugriff (401), ist das Image selbst
                    # gebaut. Eine klare Antwort, die Registry wird nicht gesperrt.
                    return _result(ref, LOCAL, reason=LOCAL_REASON)
                # Vorbeigehendes Problem (Abruflimit, Registry nicht erreichbar): die letzte Antwort der Registry gilt
                # weiter, gekennzeichnet, statt eines guten Stands ein "nicht prüfbar" zu machen.
                old = run.fallback.get(info.query) if got.kind in TRANSIENT else None
                if old is None:
                    return _result(ref, UNKNOWN, reason=got.message)
                run.kept[info.query] = old
                return self._compare(ref, repo_digests, local, old, reason=f"{got.message}. Angezeigt wird die letzte Antwort der Registry.", stale=True)
            answer = got
        return self._compare(ref, repo_digests, local, answer)

    async def _registry_answer(self, run: _Run, info: RefInfo) -> RemoteAnswer | RegistryProblem:
        """Fragt die Registry, außer sie hat in diesem Lauf schon vorbeigehend versagt."""
        blocked = run.blocked.get(info.registry)
        if blocked is not None:
            return blocked
        fetched = await self._ask_registry(run, info)
        if isinstance(fetched, RegistryProblem):
            if fetched.kind in TRANSIENT:
                run.blocked[info.registry] = fetched  # keine weiteren Fragen an dieselbe Registry
            return fetched
        run.remote[info.query] = fetched
        return fetched

    @staticmethod
    def _compare(ref: str, repo_digests: set[str], local: LocalImage, answer: RemoteAnswer, *, reason: str | None = None, stale: bool = False) -> dict[str, Any]:
        # Verglichen wird die Prüfsumme des Tags (bei Multi-Arch-Images die des Index). Ändert die Registry nur andere
        # Plattformen desselben Tags, heißt das "Update verfügbar", obwohl das eigene Image gleich bleibt; ein Pull zöge dann
        # nur die Prüfsumme nach. (`manifest inspect -v` vergleicht dagegen die Plattform des Hosts.)
        same = bool((repo_digests | ({local.image_id} if local.image_id else set())) & answer.matches)
        return _result(ref, CURRENT if same else UPDATE, reason=reason, remote=answer, stale=stale)

    async def _inspect_local(self, run: _Run, target: str) -> LocalImage | str:
        result = await self._exec(run.host, cmd_local_inspect(target), LOCAL_TIMEOUT_S)
        if result.exit_code != 0:
            return "Image lokal nicht lesbar"
        return parse_local_image(result.stdout) or "Antwort von docker image inspect nicht lesbar"

    async def _ask_registry(self, run: _Run, info: RefInfo) -> RemoteAnswer | RegistryProblem:
        if run.imagetools is not False:
            try:
                result = await self._exec(run.host, cmd_remote_imagetools(info.query), REMOTE_TIMEOUT_S)
            except HostProblem as exc:
                return self._slow_registry_or_raise(exc)
            if result.exit_code == 0:
                digest = parse_imagetools_digest(result.stdout)
                if digest is None:
                    return RegistryProblem("error", "Unerwartete Antwort von docker buildx imagetools")
                run.imagetools = True
                return RemoteAnswer(digest, frozenset({digest}), "imagetools", run.now)
            if not _NO_IMAGETOOLS_RE.search(result.stderr):
                return classify_registry_error(result.stderr, result.exit_code)
            run.imagetools = False  # kein buildx auf diesem Host -> Rückfall

        if run.platform is None:
            found = await self._exec(run.host, CMD_PLATFORM, LOCAL_TIMEOUT_S)
            plat = found.stdout.strip()
            if found.exit_code != 0 or "/" not in plat:
                return RegistryProblem("error", "Plattform des Hosts nicht ermittelbar (docker version)")
            run.platform = plat
        try:
            result = await self._exec(run.host, cmd_remote_manifest(info.query), REMOTE_TIMEOUT_S)
        except HostProblem as exc:
            return self._slow_registry_or_raise(exc)
        if result.exit_code != 0:
            return classify_registry_error(result.stderr, result.exit_code)
        answer = parse_manifest_inspect(result.stdout, run.platform)
        if answer is None:
            return RegistryProblem("error", f"Die Registry kennt dieses Image nicht für {run.platform}")
        return RemoteAnswer(answer.digest, answer.matches, answer.source, run.now)

    @staticmethod
    def _slow_registry_or_raise(exc: HostProblem) -> RegistryProblem:
        """Eine Zeitüberschreitung heißt hier "die Registry antwortet nicht": nur dieses Image ist betroffen. Jeder andere
        Fehler (SSH weg) betrifft den Host."""
        if isinstance(exc.__cause__, TimeoutError):
            return RegistryProblem("network", "Registry antwortet nicht (Zeitüberschreitung)")
        raise exc

    # --- Tagesjob + Meldung --------------------------------------------------------

    async def scheduled_check(self) -> dict[str, Any]:
        hosts = await self.docker_hosts()
        await self.check_hosts(hosts, force=True)
        return await self._notify(hosts)

    async def _notify(self, hosts: list[Any]) -> dict[str, Any]:
        """Eine Meldung, und nur wenn seit der letzten Meldung eine neue Version dazugekommen ist (Container + Digest).
        Schrumpft die Liste, weil jemand aktualisiert hat, gibt es keine Nachricht; der neue Stand wird trotzdem gemerkt,
        damit die nächste Version desselben Containers wieder gemeldet wird."""
        path = Path(str(self._ctx.data_dir)) / STATE_FILE
        try:
            stored = json.loads(path.read_text(encoding="utf-8")).get("notified", {})
        except (OSError, ValueError, AttributeError):
            stored = {}
        previous = {h: set(keys) for h, keys in stored.items() if isinstance(keys, list)} if isinstance(stored, dict) else {}

        current: dict[str, set[str]] = {}
        lines: list[tuple[str, str, str]] = []
        for host in hosts:
            state = self._hosts.get(host.id)
            # Ein Host, der diesmal nicht antwortete, behält seinen alten Stand (sonst käme beim nächsten Mal alles neu).
            if state is None or state.error:
                continue
            found = {n: r for n, r in state.results.items() if r["status"] == UPDATE}
            # Ebenso Container, die "nicht prüfbar" waren (Abruflimit, Registry weg): nur ein klares Ergebnis (aktuell,
            # andere Version, selbst gebaut, Container weg) vergisst die Meldung.
            unsure = {n for n, r in state.results.items() if r["status"] == UNKNOWN}
            held = {key for key in previous.get(host.id, set()) if key.rsplit("|", 1)[0] in unsure}
            current[host.id] = held | {f"{n}|{r['remote_digest']}" for n, r in found.items()}
            lines.extend((host.display_name or host.name, n, r["image"]) for n, r in sorted(found.items()))

        has_new = any(keys - previous.get(host_id, set()) for host_id, keys in current.items())
        if has_new:
            body = "\n".join(f"- {host}: {name} ({image})" for host, name, image in lines)
            await self._ctx.notify.send(Notification(
                title=f"Image-Updates: {len(lines)} Container mit neuer Version",
                body=f"{body}\nEs wurde nichts geändert.",
                severity=Severity.INFO,
                payload={"path": MATRIX_PATH, "tags": ["package"]},
            ))
        known = {h.id for h in hosts}
        merged = {h: sorted(keys) for h, keys in previous.items() if h in known and h not in current}
        merged.update({h: sorted(keys) for h, keys in current.items()})
        _write_atomic(path, json.dumps({"notified": merged}, indent=1, sort_keys=True))
        return {"hosts": len(hosts), "updates": len(lines), "notified": has_new}


class ImageUpdateJobSpec:
    """Täglicher Lauf (Einstellung `image_updates_enabled`, Standard: aus)."""

    id = JOB_ID
    name = "Image-Updates prüfen"

    def __init__(self, service: ImageUpdateService, *, enabled: bool, schedule: str) -> None:
        self._service = service
        self.enabled = enabled
        self.schedule = schedule if enabled else _NEVER
        self.params: dict[str, Any] = {}

    async def handler(self, **_: Any) -> dict[str, Any]:
        return await self._service.scheduled_check()


async def register_job(ctx: ExtensionContext, service: ImageUpdateService, settings: dict[str, Any]) -> None:
    """Bei jedem Start und nach jeder Einstellungsänderung (`register_job` ist ein Upsert)."""
    enabled = bool(settings.get("image_updates_enabled", False))
    schedule = (settings.get("image_updates_cron") or "").strip() or DEFAULT_CRON
    try:
        await ctx.scheduler.register_job(ImageUpdateJobSpec(service, enabled=enabled, schedule=schedule))
    except Exception:
        log.exception("image_updates_register_job_failed schedule=%r", schedule)
        await ctx.scheduler.register_job(ImageUpdateJobSpec(service, enabled=enabled, schedule=DEFAULT_CRON))
