"""Eine kleine Engine zum Durchspielen: Container, Images und Registry im Speicher, mit eigener Uhr.

`LiveWorld` erweitert die Welt der Fake-Engine (`fake_engine.World`) um die aendernden Aufrufe, so wie der
Docker-Dienst sie beantwortet: `create` fuehrt den Body mit der Config des Images zusammen (Env, Labels, Healthcheck,
Ports, Volumes, Cmd, Entrypoint, Standardmasken) und haengt Volumes und Binds ein, `connect` haengt Netze an, `start`
und `stop` aendern den Zustand, `rename`, `update`, `remove`, Tags, Pull nach Digest und die Aufloesung des beweglichen
Tags in der Registry (`/distribution`). Wie sich ein gestarteter Container verhaelt, bestimmt sein Image (`Behavior`):
gesund nach einer Weile, erst lange `unhealthy`, beendet, in einer Neustart-Schleife, mit der Notseite (503) oder nie
gesund -- jeweils abhaengig von der monotonen Zeit auf der Uhr der Tests (`Clock`), die auch der Ablauf benutzt.

Images verwaltet sie wie einer der beiden Stores des Docker-Dienstes (`image_store`): wie der alte Graphdriver-Store
(`RepoDigests` bleiben stehen) oder wie der containerd-Store, Standard einer neuen Installation von Docker 29
(`RepoDigests` aus den Namen abgeleitet, `derive_repo_digests`). `restart_daemon` spielt einen Neustart des
Docker-Dienstes nach (Restart-Policies, von Hand gestoppte Container; auch der Helfer startet dabei neu),
`restart_container` den Neustart eines einzelnen Containers (etwa des Helfers nach einem Absturz), `end_later` einen
Stopp, der nach einer Fehlerantwort weiterlaeuft.

**Invarianten bei jedem Aufruf** (Verstoesse in `violations`, die Tests verlangen eine leere Liste):

* nie zwei laufende Container an derselben Quelle von `/app/data` (One-off-Container wie `docker compose run` fuer eine
  Sicherung zaehlen nicht: die startet der Nutzer, nicht der Helfer);
* jeder Container, der startet, hat unter `/app/data` dieselbe Quelle wie das Ziel am Anfang (ein Klon mit einem
  neuen, leeren Volume faellt so auf);
* kein Volume wird je entfernt (`DELETE /containers/{id}` laesst Volumes stehen; `v=1` lehnt schon die Fake-Engine ab).

Endpunkte ausserhalb der Allowlist, die Form der aendernden Aufrufe (nie `v=1`, nie `force`, `/update` nur mit der
Restart-Policy) und die Bindung an das Journal auf der Platte prueft die Fake-Engine selbst (`form_problem`,
`JournalGuard.from_state_dir`).

`Lab` setzt das zusammen: die Welt einer Aufnahme als durchgespielte Engine, ein echtes `/state` und den Ablauf mit
der Uhr der Tests. Fuer die Wiederaufnahme kann der Ablauf an einer gezaehlten Stelle "abstuerzen" (`Points`, `Crash`:
vor und nach jedem Schreiben in `/state` ueber den `checkpoint` des Ablaufs, nach jedem aendernden Engine-Aufruf ueber
`CrashingEngine`), und `Lab.restart_helper` startet den Helfer neu: neue Sperre, neuer Client ohne ausgehandelte
Version, neuer Ablauf -- die Engine und `/state` bleiben, wie sie sind.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from fake_engine import FakeEngine, JournalGuard, Request, Response, World
from nodvard_deck_updater import channel
from nodvard_deck_updater.engine import PREVIOUS_REPOSITORY, Engine
from nodvard_deck_updater.flow import HEALTHY, Flow, Outcome, judge
from nodvard_deck_updater.policy import REPOSITORY, VERSION_LABEL
from nodvard_deck_updater.policy import Request as HelperRequest
from nodvard_deck_updater.state import JOURNAL_NAME, Journal, State, StateStore
from updater_support import NOW

DATA_PATH = "/app/data"
LABEL_ONEOFF = "com.docker.compose.oneoff"
DEFAULT_MASKED_PATHS = [
    "/proc/acpi", "/proc/asound", "/proc/interrupts", "/proc/kcore", "/proc/keys", "/proc/latency_stats",
    "/proc/sched_debug", "/proc/scsi", "/proc/timer_list", "/proc/timer_stats", "/sys/devices/virtual/powercap",
    "/sys/firmware", "/sys/devices/system/cpu/thermal_throttle",
]
"""Standardmasken eines neueren Docker-Dienstes: alles, was die Aufnahmen zeigen, und ein Eintrag mehr."""
DEFAULT_READONLY_PATHS = ["/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys", "/proc/sysrq-trigger"]
NEVER = "0001-01-01T00:00:00Z"
HOST_UP_S = 30 * 86400
"""So lange laeuft der Rechner in `Lab` schon, wenn ein Test nichts anderes sagt (`Lab.boot`)."""
HELPER_UP_S = 86400
"""So lange laeuft der Helfer in `Lab` schon (`State.StartedAt` seines Containers liegt so weit vor `NOW`)."""
RESCUE_OUTPUT = "urllib.error.HTTPError: HTTP Error 503: Service Unavailable"
BOOT_OUTPUT = "urllib.error.URLError: <urlopen error [Errno 111] Connection refused>"
CHECK_INTERVAL_S = 10.0


class Clock:
    """Uhr der Tests: `time()` ist die Wanduhr, `monotonic()` die monotone Uhr (fuer Fristen und Warten im Ablauf und
    fuer die Laufzeit der Container in der Welt). `sleep()` stellt beide nur weiter.

    `t` vorstellen heisst: so viel Zeit vergeht (beide Uhren laufen weiter). `t` zurueckstellen und `jump()` aendern nur
    die Wanduhr -- so wie NTP sie stellt, etwa kurz nach dem Hochfahren eines Raspberry Pi ohne Echtzeituhr."""

    def __init__(self, start: float = NOW) -> None:
        self._wall = float(start)
        self.m = 1000.0
        """Die monotone Uhr: eigener Nullpunkt, laeuft nie rueckwaerts."""
        self.slept = 0.0

    @property
    def t(self) -> float:
        return self._wall

    @t.setter
    def t(self, value: float) -> None:
        passed = float(value) - self._wall
        self._wall = float(value)
        if passed > 0:
            self.m += passed

    def jump(self, seconds: float) -> None:
        """Nur die Wanduhr springt; die monotone Uhr und die Container merken davon nichts."""
        self._wall += seconds

    def time(self) -> float:
        return self._wall

    def monotonic(self) -> float:
        return self.m

    def sleep(self, seconds: float) -> None:
        self._wall += seconds
        self.m += seconds
        self.slept += seconds


@dataclass
class Behavior:
    """Wie sich ein Container dieses Images nach dem Start verhaelt (Sekunden seit dem Start).

    * `healthy_after`: ab dann `healthy` (`None`: nie).
    * `unhealthy_from`: ab dann `unhealthy` (bis `healthy_after`); vorher `starting`.
    * `exit_after`: ab dann beendet (Exit-Code 1).
    * `restart_after`: ab dann eine Neustart-Schleife (`Restarting`, `RestartCount` steigt alle 10 s).
    * `rescue_after`: ab dann zeigt der Healthcheck die Notseite (503); gesund wird er dann nie.
    """

    healthy_after: float | None = 30.0
    unhealthy_from: float | None = None
    exit_after: float | None = None
    restart_after: float | None = None
    rescue_after: float | None = None

    def state(self, elapsed: float) -> tuple[dict[str, Any], int]:
        """`State` und `RestartCount` nach `elapsed` Sekunden."""
        state: dict[str, Any] = {
            "Status": "running", "Running": True, "Paused": False, "Restarting": False, "OOMKilled": False,
            "Dead": False, "Pid": 4242, "ExitCode": 0, "Error": "", "StartedAt": "2026-10-02T08:00:00Z",
            "FinishedAt": NEVER,
        }
        restarts = 0
        if self.exit_after is not None and elapsed >= self.exit_after:
            state.update(Status="exited", Running=False, Pid=0, ExitCode=1, FinishedAt="2026-10-02T08:01:00Z")
        elif self.restart_after is not None and elapsed >= self.restart_after:
            since = elapsed - self.restart_after
            restarts = 1 + int(since // 10)
            if since % 10 < 3:
                state.update(Status="restarting", Restarting=True)
        log = []
        checks = int(elapsed // CHECK_INTERVAL_S)
        for number in range(max(1, checks - 4), checks + 1):
            at = number * CHECK_INTERVAL_S
            if self.rescue_after is not None and at >= self.rescue_after:
                output, code = RESCUE_OUTPUT, 1
            elif self._healthy_at(at):
                output, code = "", 0
            else:
                output, code = BOOT_OUTPUT, 1
            log.append({"Start": "2026-10-02T08:00:00Z", "End": "2026-10-02T08:00:01Z", "ExitCode": code,
                        "Output": output})
        if checks < 1:
            log = []
        if self._healthy_at(elapsed) and (self.rescue_after is None or elapsed < self.rescue_after):
            status = "healthy"
        elif (self.unhealthy_from is not None and elapsed >= self.unhealthy_from) or (
                self.rescue_after is not None and elapsed >= self.rescue_after):
            status = "unhealthy"
        else:
            status = "starting"
        if not state["Running"]:
            status = "unhealthy"
        state["Health"] = {"Status": status, "FailingStreak": 0 if status == "healthy" else 1, "Log": log}
        return state, restarts

    def _healthy_at(self, at: float) -> bool:
        return self.healthy_after is not None and at >= self.healthy_after


def docker_time(wall: float) -> str:
    """Ein Zeitpunkt der Wanduhr so, wie der Docker-Dienst ihn in `State.StartedAt` nennt (RFC 3339 mit bis zu neun
    Nachkommastellen, UTC, ohne Nullen am Ende)."""
    seconds = int(wall // 1)
    nanos = round((wall - seconds) * 1e9)
    text = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    fraction = f"{nanos:09d}".rstrip("0")
    return text + ("." + fraction if fraction else "") + "Z"


def exited_state() -> dict[str, Any]:
    return {"Status": "exited", "Running": False, "Paused": False, "Restarting": False, "OOMKilled": False,
            "Dead": False, "Pid": 0, "ExitCode": 0, "Error": "", "StartedAt": "2026-10-02T07:00:00Z",
            "FinishedAt": "2026-10-02T08:00:00Z"}


def created_state() -> dict[str, Any]:
    return {"Status": "created", "Running": False, "Paused": False, "Restarting": False, "OOMKilled": False,
            "Dead": False, "Pid": 0, "ExitCode": 0, "Error": "", "StartedAt": NEVER, "FinishedAt": NEVER}


IMAGE_STORES = ("graphdriver", "containerd")
"""Die beiden Image-Stores des Docker-Dienstes, die `LiveWorld` nachbildet (siehe `LiveWorld.derive_repo_digests`)."""


def _name_repository(name: str) -> str:
    """Das Repository eines Image-Namens: `repo:tag` bzw. `repo@sha256:...` -> `repo` (ein Port im Registry-Namen
    bleibt)."""
    if "@" in name:
        return name.split("@", 1)[0]
    head, _, last = name.rpartition("/")
    return (head + "/" if head else "") + last.split(":", 1)[0]


def _volume_source(name: str) -> str:
    return f"/var/lib/docker/volumes/{name}/_data"


def _merge_env(user: list[str], image: list[str]) -> list[str]:
    keys = {entry.split("=", 1)[0] for entry in user}
    return list(user) + [entry for entry in image if entry.split("=", 1)[0] not in keys]


class LiveWorld(World):
    """Die Welt einer Fixture plus Registry, Uhr und die aendernden Aufrufe (siehe Kopf des Moduls)."""

    def __init__(self, base: World, clock: Clock, *, image_store: str = "graphdriver") -> None:
        super().__init__(api=base.api, version=base.version, containers=base.containers, images=base.images,
                         networks=base.networks)
        if image_store not in IMAGE_STORES:
            raise ValueError(image_store)
        self.clock = clock
        self.image_store = image_store
        self.targets: dict[str, str] = {}
        """containerd-Store: Image-ID -> Digest, auf den seine Namen zeigen (wie in der Registry)."""
        self.digested: dict[str, set[str]] = {}
        """containerd-Store: Image-ID -> Namen `<repository>@<digest>`, die ein Pull nach Digest angelegt hat."""
        self.stopped_by_hand: set[str] = set()
        """Container, die per `stop` endeten (oder nach `end_later`): `unless-stopped` startet sie nicht wieder."""
        self.registry: dict[str, dict[str, Any]] = {}
        """Digest -> Inspect des Images, wie es nach dem Ziehen aussieht."""
        self.tags: dict[str, str] = {}
        """Bewegliches Tag in der Registry -> Digest."""
        self.behaviors: dict[str, Behavior] = {}
        """Image-ID -> Verhalten eines Containers mit diesem Image (sonst `Behavior()`)."""
        self.started_at: dict[str, float] = {}
        """Container-ID -> Start auf der monotonen Uhr (die Laufzeit merkt nichts vom Stellen der Wanduhr)."""
        self.started_wall: dict[str, float] = {}
        """Container-ID -> Start auf der Wanduhr, wie sie in dem Moment stand (`State.StartedAt`; spaeteres Stellen der
        Uhr aendert ihn nicht mehr)."""
        self.ends_at: dict[str, float] = {}
        """Container-ID -> Zeitpunkt (monotone Uhr), zu dem ein laufender Container von selbst endet (`end_later`)."""
        self.violations: list[str] = []
        self.mutations: list[tuple[str, str]] = []
        """(Art, ID bzw. Name) jedes erfolgreichen aendernden Aufrufs in der Reihenfolge."""
        self.volumes_removed: list[str] = []
        self.data_origin: tuple[Any, Any] | None = None
        self._counter = 0

    # --- Aufbau fuer Tests ---------------------------------------------------

    def remember_data_origin(self, container: dict[str, Any]) -> None:
        self.data_origin = self.mount_origin(container, DATA_PATH)

    def publish(self, digest: str, image: dict[str, Any], *, tag: str | None = "latest") -> None:
        """Legt ein Image in die Registry (unter `digest`) und laesst das bewegliche Tag `tag` darauf zeigen."""
        self.registry[digest] = image
        if tag is not None:
            self.tags[tag] = digest

    def tagged(self, ref: str) -> str | None:
        """Image-ID, auf die das lokale Tag `ref` zeigt."""
        found = [image_id for image_id, image in self.images.items() if ref in (image.get("RepoTags") or [])]
        assert len(found) <= 1, ref
        return found[0] if found else None

    def with_labels(self, **labels: str) -> list[dict[str, Any]]:
        return [c for c in self.containers.values()
                if all((c.get("Config") or {}).get("Labels", {}).get(k.replace("__", ".")) == v
                       for k, v in labels.items())]

    def running(self) -> list[dict[str, Any]]:
        self.refresh()
        return [c for c in self.containers.values() if (c.get("State") or {}).get("Running") is True]

    # --- Zustand -------------------------------------------------------------

    @staticmethod
    def mount_origin(container: dict[str, Any], destination: str) -> tuple[Any, Any] | None:
        for mount in container.get("Mounts") or []:
            if mount.get("Destination") == destination:
                kind = mount.get("Type")
                return kind, mount.get("Name") if kind == "volume" else mount.get("Source")
        return None

    def end_later(self, container_id: str, after: float) -> None:
        """Der laufende Container endet erst `after` Sekunden spaeter -- so wie der Docker-Dienst einen Container nach
        einer Fehlerantwort auf `stop` (oder einer verlorenen Antwort) weiter stoppt. Er gilt dann als von Hand
        gestoppt."""
        self.ends_at[container_id] = self.clock.m + after
        self.stopped_by_hand.add(container_id)

    def restart_container(self, container_id: str) -> None:
        """Nur dieser Container startet neu, jetzt (etwa der Helfer nach einem Absturz ueber seine Restart-Policy):
        `State.StartedAt` ist die Wanduhr in diesem Moment, `RestartCount` beginnt bei 0."""
        self.refresh()
        self.ends_at.pop(container_id, None)
        self.started_at[container_id] = self.clock.m
        self.started_wall[container_id] = self.clock.t
        self.refresh()

    def restart_daemon(self) -> None:
        """Der Docker-Dienst (oder der ganze Rechner) startet neu: Jeder Container endet. Danach startet der Dienst die
        mit der Restart-Policy `always` und die mit `unless-stopped`, die nicht von Hand gestoppt waren, mit
        `RestartCount` 0 -- nie die mit `no` (und `on-failure` nur nach einem Fehler, hier nie) und nie einen, der nur
        angelegt und noch nie gestartet war."""
        self.refresh()
        self.ends_at.clear()
        for container_id, container in self.containers.items():
            self.started_at.pop(container_id, None)
            self.started_wall.pop(container_id, None)
            if (container.get("State") or {}).get("Status") in ("running", "restarting", "paused"):
                container["State"] = exited_state()
        for container_id, container in self.containers.items():
            name = ((container.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")
            if (container.get("State") or {}).get("StartedAt") == NEVER:
                continue
            if name == "always" or (name == "unless-stopped" and container_id not in self.stopped_by_hand):
                self.started_at[container_id] = self.clock.m
                self.started_wall[container_id] = self.clock.t
        self.refresh()

    def refresh(self) -> None:
        now = self.clock.m
        for container_id, at in list(self.ends_at.items()):
            if now >= at:
                del self.ends_at[container_id]
                self.started_at.pop(container_id, None)
                self.started_wall.pop(container_id, None)
                if container_id in self.containers:
                    self.containers[container_id]["State"] = exited_state()
        for container_id, started in self.started_at.items():
            container = self.containers.get(container_id)
            if container is None:
                continue
            behavior = self.behaviors.get(container.get("Image"), Behavior())
            container["State"], container["RestartCount"] = behavior.state(now - started)
            wall = self.started_wall.get(container_id, self.clock.t - (now - started))
            container["State"]["StartedAt"] = docker_time(wall)

    def _check_invariants(self, where: str) -> None:
        by_origin: dict[Any, list[str]] = {}
        for container in self.containers.values():
            state = container.get("State") or {}
            labels = (container.get("Config") or {}).get("Labels") or {}
            if labels.get(LABEL_ONEOFF) == "True":
                continue
            if state.get("Running") is True or state.get("Restarting") is True:
                origin = self.mount_origin(container, DATA_PATH)
                if origin is not None:
                    by_origin.setdefault(origin, []).append(container["Name"])
        for origin, names in by_origin.items():
            if len(names) > 1:
                self.violations.append(f"zwei laufende Container an {origin}: {sorted(names)} ({where})")

    # --- Antworten -----------------------------------------------------------

    def answer(self, request: Request) -> Response:
        self.refresh()
        try:
            response = self._answer(request)
        finally:
            self.refresh()
            self._check_invariants(f"{request.method} {request.path}")
        return response

    def _answer(self, request: Request) -> Response:
        method, path = request.method, request.path
        query = {key: values[0] for key, values in request.query.items()}
        if method == "GET":
            match = re.fullmatch(r"/distribution/(.+):([^:/]+)/json", path)
            if match:
                digest = self.tags.get(match[2]) if match[1] == REPOSITORY else None
                if digest is None:
                    return Response(status=404, body={"message": "manifest unknown"})
                return Response(body={
                    "Descriptor": {"mediaType": "application/vnd.oci.image.index.v1+json", "digest": digest,
                                   "size": 856},
                    "Platforms": [{"architecture": "amd64", "os": "linux"}],
                })
            return super().answer(request)
        if (method, path) == ("POST", "/containers/create"):
            return self._create(query["name"], request.body)
        if (method, path) == ("POST", "/images/create"):
            return self._pull(query["fromImage"], query["tag"])
        match = re.fullmatch(r"/containers/([0-9a-f]{64})/(start|stop|rename|update)", path)
        if method == "POST" and match:
            return getattr(self, "_" + match[2])(match[1], query, request.body)
        match = re.fullmatch(r"/containers/([0-9a-f]{64})", path)
        if method == "DELETE" and match:
            return self._remove(match[1])
        match = re.fullmatch(r"/networks/([0-9a-f]{64})/connect", path)
        if method == "POST" and match:
            return self._connect(match[1], request.body)
        match = re.fullmatch(r"/images/(sha256:[0-9a-f]{64})/tag", path)
        if method == "POST" and match:
            return self._tag(match[1], query["repo"], query["tag"])
        match = re.fullmatch(r"/images/(nodvard-deck-previous:[0-9.]+)", path)
        if method == "DELETE" and match:
            return self._untag(match[1])
        return Response(status=501, body={"message": "not implemented in LiveWorld"})

    def _mutated(self, kind: str, ref: str) -> None:
        self.mutations.append((kind, ref))

    # --- Container ----------------------------------------------------------

    def _new_id(self) -> str:
        self._counter += 1
        return hashlib.sha256(f"live-{self._counter}".encode()).hexdigest()

    def resolve(self, text: str) -> str | None:
        """Image-ID zum Tag-Text (`repo` ohne Tag = `repo:latest`)."""
        ref = text if ":" in text.rsplit("/", 1)[-1] else text + ":latest"
        return self.tagged(ref)

    def _create(self, name: str, body: dict[str, Any]) -> Response:
        if any(c.get("Name") == "/" + name for c in self.containers.values()):
            return Response(status=409, body={"message": f'Conflict. The container name "/{name}" is already in use'})
        image_id = self.resolve(body.get("Image") or "")
        if image_id is None:
            return Response(status=404, body={"message": "No such image"})
        image_config = self.images[image_id].get("Config") or {}
        container_id = self._new_id()
        host = copy.deepcopy(body.get("HostConfig") or {})
        if host.get("Privileged") is not True:
            host.setdefault("MaskedPaths", list(DEFAULT_MASKED_PATHS))
            host.setdefault("ReadonlyPaths", list(DEFAULT_READONLY_PATHS))
        networks = {}
        for key, endpoint in ((body.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}).items():
            networks[key] = self._endpoint(endpoint)
        self.containers[container_id] = {
            "Id": container_id, "Name": "/" + name, "Image": image_id, "Created": "2026-10-02T08:00:00Z",
            "Path": "/entrypoint.sh", "Args": [], "Config": self._config(body, image_config, container_id),
            "HostConfig": host, "Mounts": self._mounts(host, image_config), "RestartCount": 0,
            "NetworkSettings": {"Networks": networks}, "State": created_state(),
        }
        self._mutated("create", container_id)
        return Response(status=201, body={"Id": container_id, "Warnings": []})

    @staticmethod
    def _config(body: dict[str, Any], image: dict[str, Any], container_id: str) -> dict[str, Any]:
        """So fuehrt der Docker-Dienst Body und Image-Config zusammen."""
        config = {key: copy.deepcopy(value) for key, value in body.items()
                  if key not in ("HostConfig", "NetworkingConfig")}
        config.setdefault("Hostname", container_id[:12])
        for key in ("Domainname", "User"):
            config.setdefault(key, image.get(key) or "")
        for key in ("AttachStdin", "AttachStdout", "AttachStderr", "Tty", "OpenStdin", "StdinOnce"):
            config.setdefault(key, False)
        config["Env"] = _merge_env(body.get("Env") or [], image.get("Env") or [])
        config["Cmd"] = copy.deepcopy(body["Cmd"] if "Cmd" in body else image.get("Cmd"))
        config["Entrypoint"] = copy.deepcopy(image.get("Entrypoint"))
        config["Labels"] = {**(image.get("Labels") or {}), **(body.get("Labels") or {})}
        for key in ("ExposedPorts", "Volumes"):
            merged = {**(image.get(key) or {}), **(body.get(key) or {})}
            if merged:
                config[key] = merged
        health = {**(image.get("Healthcheck") or {}), **(body.get("Healthcheck") or {})}
        if health:
            config["Healthcheck"] = health
        for key in ("WorkingDir", "StopSignal"):
            value = body.get(key) or image.get(key)
            if value:
                config[key] = value
        return config

    def _mounts(self, host: dict[str, Any], image: dict[str, Any]) -> list[dict[str, Any]]:
        mounts: list[dict[str, Any]] = []
        covered: set[str] = set()
        for bind in host.get("Binds") or []:
            parts = bind.split(":")
            source, destination = parts[0], parts[1]
            options = parts[2] if len(parts) > 2 else ""
            rw = "ro" not in options.split(",")
            if source.startswith("/"):
                mounts.append({"Type": "bind", "Source": source, "Destination": destination, "Mode": options,
                               "RW": rw, "Propagation": "rprivate"})
            else:
                mounts.append({"Type": "volume", "Name": source, "Source": _volume_source(source),
                               "Destination": destination, "Driver": "local", "Mode": options, "RW": rw,
                               "Propagation": ""})
            covered.add(destination)
        for entry in host.get("Mounts") or []:
            kind, destination = entry.get("Type"), entry.get("Target")
            rw = entry.get("ReadOnly") is not True
            if kind == "volume":
                name = entry.get("Source") or self._new_id()  # ohne Quelle: ein neues, leeres Volume
                mounts.append({"Type": "volume", "Name": name, "Source": _volume_source(name),
                               "Destination": destination, "Driver": "local", "Mode": "z", "RW": rw,
                               "Propagation": ""})
            else:
                mounts.append({"Type": kind, "Source": entry.get("Source"), "Destination": destination, "Mode": "",
                               "RW": rw, "Propagation": "rprivate"})
            covered.add(destination)
        covered |= set(host.get("Tmpfs") or {})
        for destination in image.get("Volumes") or {}:
            if destination not in covered:
                name = self._new_id()
                mounts.append({"Type": "volume", "Name": name, "Source": _volume_source(name),
                               "Destination": destination, "Driver": "local", "Mode": "", "RW": True,
                               "Propagation": ""})
        return mounts

    @staticmethod
    def _endpoint(config: dict[str, Any]) -> dict[str, Any]:
        return {"IPAMConfig": copy.deepcopy(config.get("IPAMConfig")), "Links": copy.deepcopy(config.get("Links")),
                "Aliases": copy.deepcopy(config.get("Aliases")), "DriverOpts": copy.deepcopy(config.get("DriverOpts")),
                "GwPriority": config.get("GwPriority", 0), "NetworkID": "", "EndpointID": "", "Gateway": "",
                "IPAddress": "", "IPPrefixLen": 0, "IPv6Gateway": "", "GlobalIPv6Address": "",
                "GlobalIPv6PrefixLen": 0, "MacAddress": "", "DNSNames": None}

    def _connect(self, network_id: str, body: dict[str, Any]) -> Response:
        network = self.networks.get(network_id)
        container = self.containers.get(body.get("Container"))
        if network is None or container is None:
            return Response(status=404, body={"message": "No such network or container"})
        networks = container["NetworkSettings"]["Networks"]
        if network["Name"] in networks:
            return Response(status=403, body={"message": "endpoint already exists in network"})
        networks[network["Name"]] = self._endpoint(body.get("EndpointConfig") or {})
        self._mutated("connect", network_id)
        return Response(status=200, body={})

    def _start(self, container_id: str, _query: dict[str, str], _body: Any) -> Response:
        container = self.containers.get(container_id)
        if container is None:
            return Response(status=404, body={"message": "No such container"})
        if (container.get("State") or {}).get("Running") is True:
            return Response(status=304)
        origin = self.mount_origin(container, DATA_PATH)
        if self.data_origin is not None and origin != self.data_origin:
            self.violations.append(f"Start mit anderer Quelle fuer {DATA_PATH}: {origin} statt {self.data_origin}")
        by_name = {net["Name"]: net_id for net_id, net in self.networks.items()}
        for key, endpoint in container["NetworkSettings"]["Networks"].items():
            endpoint["NetworkID"] = by_name.get(key, endpoint.get("NetworkID") or "")
        self.started_at[container_id] = self.clock.m
        self.started_wall[container_id] = self.clock.t
        self.ends_at.pop(container_id, None)
        self.stopped_by_hand.discard(container_id)
        self._mutated("start", container_id)
        return Response(status=204)

    def _stop(self, container_id: str, _query: dict[str, str], _body: Any) -> Response:
        container = self.containers.get(container_id)
        if container is None:
            return Response(status=404, body={"message": "No such container"})
        state = container.get("State") or {}
        if state.get("Running") is not True and state.get("Restarting") is not True:
            return Response(status=304)
        self.started_at.pop(container_id, None)
        self.started_wall.pop(container_id, None)
        self.ends_at.pop(container_id, None)
        self.stopped_by_hand.add(container_id)
        container["State"] = exited_state()
        self._mutated("stop", container_id)
        return Response(status=204)

    def _rename(self, container_id: str, query: dict[str, str], _body: Any) -> Response:
        container = self.containers.get(container_id)
        if container is None:
            return Response(status=404, body={"message": "No such container"})
        wanted = "/" + query["name"]
        if container.get("Name") == wanted:
            # Wie der Docker-Dienst: ein Umbenennen auf den aktuellen Namen ist ein ungueltiger Parameter.
            return Response(status=400, body={"message": "Renaming a container with the same name as its current name"})
        if any(c.get("Name") == wanted for cid, c in self.containers.items() if cid != container_id):
            return Response(status=409, body={"message": "Conflict. The name is already in use"})
        container["Name"] = wanted
        self._mutated("rename", query["name"])
        return Response(status=204)

    def _update(self, container_id: str, _query: dict[str, str], body: dict[str, Any]) -> Response:
        container = self.containers.get(container_id)
        if container is None:
            return Response(status=404, body={"message": "No such container"})
        container["HostConfig"]["RestartPolicy"] = copy.deepcopy(body["RestartPolicy"])
        self._mutated("update", body["RestartPolicy"]["Name"])
        return Response(status=200, body={"Warnings": []})

    def _remove(self, container_id: str) -> Response:
        container = self.containers.get(container_id)
        if container is None:
            return Response(status=404, body={"message": "No such container"})
        if (container.get("State") or {}).get("Running") is True:
            return Response(status=409, body={"message": "cannot remove a running container"})
        del self.containers[container_id]
        self.started_at.pop(container_id, None)
        self.started_wall.pop(container_id, None)
        self._mutated("remove", container_id)
        return Response(status=204)

    # --- Images ----------------------------------------------------------------

    def _pull(self, repository: str, digest: str) -> Response:
        image = self.registry.get(digest) if repository == REPOSITORY else None
        if image is None:
            return Response(status=404, body={"message": "manifest unknown"})
        image_id = image["Id"]
        local = self.images.setdefault(image_id, copy.deepcopy(image))
        ref = f"{repository}@{digest}"
        local.setdefault("RepoDigests", [])
        local.setdefault("RepoTags", [])
        if self.image_store == "containerd":
            self.targets[image_id] = digest
            self.digested.setdefault(image_id, set()).add(ref)
            self.derive_repo_digests()
        elif ref not in local["RepoDigests"]:
            local["RepoDigests"].append(ref)
        self._mutated("pull", digest)
        lines = [{"status": f"Pulling from {repository}", "id": digest[7:19]}, {"status": "Download complete"},
                 {"status": f"Digest: {digest}"}]
        return Response(chunks=[json.dumps(line).encode() + b"\r\n" for line in lines])

    def _tag(self, image_id: str, repo: str, tag: str) -> Response:
        if image_id not in self.images:
            return Response(status=404, body={"message": "No such image"})
        ref = f"{repo}:{tag}"
        for image in self.images.values():
            if ref in (image.get("RepoTags") or []):
                image["RepoTags"].remove(ref)
        self.images[image_id].setdefault("RepoTags", []).append(ref)
        self.derive_repo_digests()
        self._mutated("tag", ref)
        return Response(status=201)

    def _untag(self, ref: str) -> Response:
        image_id = self.tagged(ref)
        if image_id is None:
            return Response(status=404, body={"message": f"No such image: {ref}"})
        self.images[image_id]["RepoTags"].remove(ref)
        self.derive_repo_digests()
        self._mutated("untag", ref)
        return Response(status=200, body=[{"Untagged": ref}])

    def derive_repo_digests(self) -> None:
        """containerd-Store (Standard bei einer neuen Installation von Docker 29): `RepoDigests` sind keine eigenen
        Eintraege, sondern werden aus den Namen eines Images abgeleitet -- je Name `<repository>@<Digest des Ziels>`.
        Haengt das bewegliche Tag auf ein anderes Image um, verliert das alte so seinen Digest im Repository, wenn es
        keinen anderen Namen dort hat. Im alten Store (Graphdriver) bleibt `RepoDigests` stehen: dann nichts tun."""
        if self.image_store != "containerd":
            return
        for image_id, image in self.images.items():
            target = self.targets.get(image_id)
            names = list(image.get("RepoTags") or []) + sorted(self.digested.get(image_id, ()))
            image["RepoDigests"] = sorted({_name_repository(name) + "@" + target for name in names}) if target else []

    def prune_all(self) -> list[str]:
        """Wie `docker image prune -a`: jedes Image, das kein Container benutzt, ist weg (auch mit Tags)."""
        used = {c.get("Image") for c in self.containers.values()}
        gone = [image_id for image_id in self.images if image_id not in used]
        for image_id in gone:
            del self.images[image_id]
            self.digested.pop(image_id, None)
        return gone


# ---------------------------------------------------------------------------
# Absturz an einer gezaehlten Stelle
# ---------------------------------------------------------------------------

SECRET = "GEHEIM-FLOW-123"
LATEST = REPOSITORY + ":latest"
MUTATING_CALLS = ("pull", "tag_image", "rename_container", "create_container", "connect_network",
                  "set_restart_policy", "stop_container", "start_container", "remove_container", "remove_protect_tag")
"""Die aendernden Methoden des Clients: nach jeder kann der Helfer abstuerzen (die Engine hat den Aufruf dann schon
ausgefuehrt, nur die Antwort kommt beim Helfer nicht mehr an)."""


class Crash(BaseException):
    """Der Helfer endet genau hier (wie bei `kill -9` oder einem Stromausfall). Eine `BaseException`, damit kein
    `except Exception` im Ablauf sie auffaengt."""


class Points:
    """Zaehlt die Stellen, an denen der Helfer abstuerzen kann (`seen`, in der Reihenfolge), und stuerzt an der Stelle
    mit der Nummer `crash_at` ab. Stellen: `before:<was>`/`after:<was>` um jedes Schreiben in `/state` (`checkpoint`
    des Ablaufs) und `call:<Methode>` nach jedem aendernden Engine-Aufruf."""

    def __init__(self, crash_at: int | None = None, *, crash_on: str | None = None) -> None:
        self.crash_at = crash_at
        self.crash_on = crash_on
        """Statt einer Nummer: an der ersten Stelle mit diesem Namen abstuerzen."""
        self.seen: list[str] = []

    def hit(self, label: str) -> None:
        self.seen.append(label)
        if (self.crash_at is not None and len(self.seen) - 1 == self.crash_at) or label == self.crash_on:
            self.crash_on = None
            raise Crash(label)

    def checkpoint(self, when: str, what: str) -> None:
        self.hit(f"{when}:{what}")


def _after_call(name: str):
    method = getattr(Engine, name)

    def call(self, *args, **kwargs):
        result = method(self, *args, **kwargs)
        self.points.hit(f"call:{name}")
        return result

    call.__name__ = name
    return call


class CrashingEngine(Engine):
    """Der echte Client, der nach jedem aendernden Aufruf an `points` meldet (und dort abstuerzen kann)."""

    def __init__(self, socket_path: str, points: Points) -> None:
        super().__init__(socket_path)
        self.points = points


for _name in MUTATING_CALLS:
    setattr(CrashingEngine, _name, _after_call(_name))


# ---------------------------------------------------------------------------
# Labor: Welt, Engine, /state, Ablauf
# ---------------------------------------------------------------------------


def _hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Lab:
    """Eine aufgezeichnete Welt als durchgespielte Engine, ein echtes `/state` und der Ablauf mit eigener Uhr.

    `image_store="containerd"`: die Welt leitet `RepoDigests` wie der containerd-Store aus den Namen ab, und das
    Dashboard ist wie mit `docker compose pull` installiert -- sein Image hat nur den Namen des beweglichen Tags."""

    def __init__(self, tmp_path, uid, fixture: str = "docker29-api154", *, points: Points | None = None,
                 image_store: str = "graphdriver") -> None:
        self.uid = uid
        self.clock = Clock()
        self.world = LiveWorld(World.from_fixture(fixture), self.clock, image_store=image_store)
        self.helper = self.world.by_service("updater")
        self.helper["State"]["StartedAt"] = docker_time(self.clock.t - HELPER_UP_S)  # die Aufnahme liegt nach `NOW`
        self.booted: float | None = self.clock.m - HOST_UP_S
        """Wann der Rechner hochgefahren ist (monotone Uhr der Tests); `None`: die Laufzeit ist unbekannt."""
        self.target = self.world.by_service("nodvard-deck")
        self.target["Config"]["Env"].append("NODVARD_DECK_JWT_SECRET=" + SECRET)
        self.old_id = self.target["Id"]
        self.name = self.target["Name"][1:]
        self.old_image = self.target["Image"]
        self.restart = copy.deepcopy(self.target["HostConfig"]["RestartPolicy"])
        self.labels = {key: value for key, value in self.target["Config"]["Labels"].items()
                       if key in ("com.docker.compose.project", "com.docker.compose.service")}
        self.world.remember_data_origin(self.target)
        old = self.world.images[self.old_image]
        self.old_digest = next(ref.split("@", 1)[1] for ref in old["RepoDigests"] if ref.startswith(REPOSITORY + "@"))
        self.world.registry[self.old_digest] = {**copy.deepcopy(old), "RepoTags": [], "RepoDigests": []}
        if image_store == "containerd":
            old["RepoTags"] = [self.target["Config"]["Image"]]
            self.world.targets[self.old_image] = self.old_digest
            self.world.derive_repo_digests()
            assert old["RepoDigests"] == [f"{REPOSITORY}@{self.old_digest}"]
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir(mode=0o700)
        self.store = StateStore(self.state_dir, expected_uid=uid)
        self.store.open()
        self.fake = FakeEngine(self.world, guard=JournalGuard.from_state_dir(self.state_dir, self.helper["Id"]))
        self.fake.start()
        self.steps: list[str | None] = []
        self.step_at: dict[str | None, float] = {}
        self.logs: list[dict] = []
        self._releases = 0
        self._start_helper(points)

    def _start_helper(self, points: Points | None) -> None:
        self.points = points
        self.engine = Engine(self.fake.path) if points is None else CrashingEngine(self.fake.path, points)
        self.flow = Flow(engine=self.engine, store=self.store, own_id=self.helper["Id"], service="nodvard-deck",
                         clock=self.clock.time, monotonic=self.clock.monotonic, sleep=self.clock.sleep,
                         uptime=self.uptime, on_step=self._on_step, logger=self._log,
                         checkpoint=None if points is None else points.checkpoint)

    def uptime(self) -> float | None:
        """Wie lange der Rechner schon laeuft (wie `flow.boot_uptime`, auf der monotonen Uhr der Tests)."""
        return None if self.booted is None else self.clock.m - self.booted

    def boot(self) -> None:
        """Der Rechner ist gerade hochgefahren (nach einem Stromausfall): seine Laufzeit beginnt bei 0."""
        self.booted = self.clock.m

    def restart_helper(self, points: Points | None = None) -> None:
        """Der Helfer startet neu (nach einem Absturz): die Sperre von `/state` faellt mit dem Prozess, der neue nimmt
        sie, mit einem neuen Client (noch nichts ausgehandelt) und einem neuen Ablauf. Engine und `/state` bleiben."""
        self.store.close()
        self.store = StateStore(self.state_dir, expected_uid=self.uid)
        self.store.open()
        self._start_helper(points)

    def _on_step(self, journal: Journal | None) -> None:
        step = None if journal is None else journal.step
        if journal is not None and journal.undo:
            step = "undo"  # der angekuendigte Rueckbau steht im selben Schritt, nur mit `undo`
        if not self.steps or self.steps[-1] != step:
            self.steps.append(step)
        self.step_at.setdefault(step, self.clock.t)

    def _log(self, event, **fields):
        self.logs.append({"event": event, **fields})

    def close(self) -> None:
        self.fake.stop()
        self.store.close()

    def assert_clean(self) -> None:
        assert self.fake.violations == []
        assert self.world.violations == []
        assert SECRET not in json.dumps(self.logs)

    # --- Aufbau ---------------------------------------------------------------

    def release(self, version: str, *, behavior: Behavior | None = None, tag: str | None = "latest",
                label: str | None = None, **changes) -> tuple[str, str]:
        """Eine neue Version in der Registry: Image-ID und Digest. `label` ueberschreibt das Versions-Label (`""`: ohne
        Label), `changes` weitere Felder des Image-Inspects."""
        self._releases += 1
        image = copy.deepcopy(self.world.images[self.old_image])
        image_id = "sha256:" + _hex(f"image-{version}-{self._releases}")
        digest = "sha256:" + _hex(f"digest-{version}-{self._releases}")
        image.update(Id=image_id, RepoTags=[], RepoDigests=[])
        labels = image["Config"].setdefault("Labels", {})
        if label == "":
            labels.pop(VERSION_LABEL, None)
        else:
            labels[VERSION_LABEL] = label or version
        image["Config"]["Env"] = list(image["Config"].get("Env") or []) + [f"DECK_IMAGE_ONLY={version}"]
        image.update(changes)
        if behavior is not None:
            self.world.behaviors[image_id] = behavior
        self.world.publish(digest, image, tag=tag)
        return image_id, digest

    def request(self, action: str = "update", version: str = "0.7.1") -> HelperRequest:
        return HelperRequest(v=1, id=str(uuid.uuid4()), action=action, version=version, created_at=int(self.clock.t))

    def state(self) -> State:
        return self.store.load_state(self.clock.t)

    def run(self, action: str = "update", version: str = "0.7.1") -> Outcome:
        self.steps.clear()
        self.step_at.clear()
        outcome = self.flow.run(self.request(action, version), self.state())
        if outcome.outcome is not None:
            status_with([outcome.result(self.clock.t)])
        return outcome

    # --- Blick auf die Welt ---------------------------------------------------

    def decks(self) -> list[dict]:
        """Alle Container des Dienstes (ohne One-off)."""
        self.world.refresh()
        return [c for c in self.world.containers.values()
                if all(c["Config"]["Labels"].get(k) == v for k, v in self.labels.items())
                and c["Config"]["Labels"].get("com.docker.compose.oneoff") != "True"]

    def only_deck(self) -> dict:
        decks = self.decks()
        assert len(decks) == 1, [d["Name"] for d in decks]
        return decks[0]

    def journal_left(self) -> bool:
        return (self.state_dir / JOURNAL_NAME).exists()

    def protect(self, version: str) -> str | None:
        return self.world.tagged(f"{PREVIOUS_REPOSITORY}:{version}")

    def mutations(self, kind: str | None = None) -> list[tuple[str, str]]:
        return [m for m in self.world.mutations if kind is None or m[0] == kind]

    def assert_healthy(self, container: dict) -> None:
        assert judge(container, 0) == HEALTHY, container["State"]

    def assert_back_to_old(self) -> None:
        """Der alte Container laeuft wieder unter seinem Namen, mit seiner Restart-Policy, gesund; der neue ist weg,
        das bewegliche Tag zeigt wieder auf das alte Image, das Schutz-Tag dieses Vorgangs ist entfernt."""
        deck = self.only_deck()
        assert deck["Id"] == self.old_id and deck["Name"] == "/" + self.name
        assert deck["HostConfig"]["RestartPolicy"] == self.restart
        self.assert_healthy(deck)
        assert self.world.tagged(LATEST) == self.old_image
        assert self.protect("0.7.0") is None
        assert not self.journal_left()


def status_with(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Ein Status mit diesen Ergebnissen, geprueft wie `status.json` (nur feste Codes, die auch das Dashboard kennt)."""
    doc = {"proto": 1, "helper_version": "0.7.0", "request_versions": [1], "seq": 1, "heartbeat_at": 1,
           "state": "idle", "ready": False, "reason": None, "target": None, "busy": None, "previous": None,
           "results": results}
    channel.validate_status(doc)
    return doc
