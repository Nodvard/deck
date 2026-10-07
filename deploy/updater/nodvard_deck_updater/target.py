"""Das Ziel finden und vorpruefen.

Die Vorpruefung laeuft beim Start, danach regelmaessig und vor jeder Aktion. Sie **liest nur** (nur `GET` an die
Engine, keine Registry) und meldet `ready` bzw. den ersten Ablehnungsgrund als festen Code. So sieht der Nutzer
eine Fehlkonfiguration, bevor er klickt.

* **Eigene ID** aus `/proc/self/mountinfo`: Docker bindet `hostname`, `hosts` und `resolv.conf` aus
  `.../containers/<64 hex>/` ein. Nicht `HOSTNAME` (das ist nur die Kurz-ID und per `hostname:` frei waehlbar).
* **Projekt** aus den eigenen Labels (Self-Inspect). Ohne Compose-Labels: `not_compose`.
* **Ziel:** Filter `project=P`, `service=S`, `oneoff=False` (lokal nachgeprueft, eine Engine, die Filter
  ignoriert, fuehrt nicht zum falschen Ziel), ohne die eigene ID. **Genau ein** Treffer, der laeuft, nicht neu startet
  und `healthy` ist. Swarm-Labels -> `swarm`. Ab hier gilt nur die volle ID.
* **Mit Journal** (`exclude` nicht leer: der alte und der neue Container eines laufenden Vorgangs) gibt es **nie ein
  Ziel**: Ein Container mit den Ziel-Labels, der nicht im Journal steht, gilt als von aussen dazugekommen
  (`external_change`), und ohne weiteren bleibt es bei `no_target`. `Preflight.target` darf dann nicht benutzt
  werden; der Ablauf prueft Container ueber ihre IDs aus dem Journal.
  **Ausnahme Schritt `creating`:** Die ID des neuen Containers steht erst nach `created` im Journal. Ein Container
  `<name>` im Zustand `created`, nie gestartet, mit dem neuen Image und den gleichen Compose-Labels gehoert dem
  Helfer und erscheint hier trotzdem als `external_change`. Mit Journal sagt `reason` darum **nichts**
  ueber fremde Container aus: Wer ein Journal wiederaufnimmt, listet die Container mit den Ziel-Labels selbst und
  gleicht sie mit den Journal-IDs (und in `creating` mit dem Zustand des Containers) ab.
* **Zustand und Socket:** das Ziel bindet weder den Engine-Socket noch das `/state`-Volume des Helfers ein
  (auch nicht ueber einen Elternordner wie `/var/run` oder `/`), sonst `unsafe_target`. Es hat den Kanal des Helfers
  unter `/app/updater` (`channel_missing_in_target`).
* **Image und Tag:** `Config.Image` ist das Repository mit beweglichem Tag; die Engine nennt fuer das laufende Image
  einen Digest im Repository (`RepoDigests`, fuer den Rueckweg-Slot), es traegt das Versions-Label und ist mindestens
  `MIN_VERSION`. Die Herkunft beweist der Digest nur beim alten Store der Engine: Beim containerd-Store (Standard ab
  Docker 29) leitet die Engine ihn aus den Namen ab, dann besteht auch ein selbst gebautes, als ghcr getaggtes Image
  diese Pruefung. Unterscheiden koennte das die Registry, die wertet die Vorpruefung aber nicht aus (Folgen siehe
  `policy.require_registry_digest`).
* **Klonbarkeit:** der `create`-Body wird schon hier probeweise gebaut (`clone.build`), damit
  `custom_entrypoint`, `unknown_field`, `network_unclear`, `macvlan` und `auto_remove` vor dem Klick auffallen.
* **Umgebung:** kein anderer Container haengt per `NetworkMode`, `PidMode` oder `IpcMode` = `container:<ziel>` am Ziel
  (`dependent_containers`; die Liste der Container zeigt nur `NetworkMode`, darum wird jeder andere Container
  gelesen), `<name>-previous` ist frei (`name_taken`).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import clone, policy
from .engine import SOCKET_PATH, Engine, EngineError
from .policy import Refusal
from .state import CONTAINER_NAME_RE, PREVIOUS_SUFFIX, RESTART_POLICIES

LABEL_PROJECT = "com.docker.compose.project"
LABEL_SERVICE = "com.docker.compose.service"
LABEL_ONEOFF = "com.docker.compose.oneoff"
SWARM_PREFIX = "com.docker.swarm."
CHANNEL_IN_TARGET = "/app/updater"
CHANNEL_IN_HELPER = "/channel"
STATE_IN_HELPER = "/state"
MOUNTINFO_MAX_BYTES = 1024 * 1024

PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}", re.ASCII)
_MOUNT_ROOT_RE = re.compile(r"(?:\A|.*/)containers/([0-9a-f]{64})/(?:hostname|hosts|resolv\.conf)", re.ASCII)
_SELF_MOUNT_POINTS = frozenset({"/etc/hostname", "/etc/hosts", "/etc/resolv.conf"})
NEVER_STARTED = "0001-01-01T00:00:00Z"
"""`State.StartedAt` eines Containers, der nie gestartet wurde."""
_DOCKER_TIME_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})",
    re.ASCII)


# ---------------------------------------------------------------------------
# Eigene Container-ID
# ---------------------------------------------------------------------------


def parse_mountinfo(data: bytes) -> str:
    """Eigene volle Container-ID aus dem Inhalt von `/proc/self/mountinfo` (reine Funktion).

    Gesucht werden die Eintraege mit Einhaengepunkt `/etc/hostname`, `/etc/hosts` oder `/etc/resolv.conf`, deren
    Wurzel (Feld 4) auf `containers/<64 hex>/<datei>` endet. Alle muessen dieselbe ID nennen; keine oder mehrere
    verschiedene -> `self_unknown`."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MOUNTINFO_MAX_BYTES:
        raise Refusal(policy.SELF_UNKNOWN)
    found = set()
    for line in bytes(data).decode("utf-8", "replace").splitlines():
        fields = line.split(" ")
        if len(fields) < 5 or fields[4] not in _SELF_MOUNT_POINTS:
            continue
        match = _MOUNT_ROOT_RE.fullmatch(fields[3])
        if match:
            found.add(match[1])
    if len(found) != 1:
        raise Refusal(policy.SELF_UNKNOWN)
    return found.pop()


def read_mountinfo() -> bytes:
    """Liest `/proc/self/mountinfo` (hoechstens `MOUNTINFO_MAX_BYTES`). Fehler -> `self_unknown`."""
    try:
        fd = os.open("/proc/self/mountinfo", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOCTTY)
    except OSError:
        raise Refusal(policy.SELF_UNKNOWN) from None
    chunks: list[bytes] = []
    size = 0
    try:
        while size <= MOUNTINFO_MAX_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        raise Refusal(policy.SELF_UNKNOWN) from None
    finally:
        os.close(fd)
    if size > MOUNTINFO_MAX_BYTES:
        raise Refusal(policy.SELF_UNKNOWN)
    return b"".join(chunks)


def own_container_id() -> str:
    return parse_mountinfo(read_mountinfo())


# ---------------------------------------------------------------------------
# Zeitpunkte des Docker-Dienstes
# ---------------------------------------------------------------------------


def docker_time(value: object) -> float | None:
    """Ein Zeitpunkt des Docker-Dienstes (RFC 3339, bis zu neun Nachkommastellen, etwa `State.StartedAt`) als
    Sekunden seit 1970 (UTC). `None` fuer alles andere, auch fuer den Nullwert eines nie gestarteten Containers."""
    if not isinstance(value, str) or value == NEVER_STARTED:
        return None
    match = _DOCKER_TIME_RE.fullmatch(value)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.group(1, 2, 3, 4, 5, 6))
    if not (1970 <= year <= 9999 and 1 <= month <= 12 and 1 <= day <= 31 and hour <= 23 and minute <= 59
            and second <= 60):
        return None
    zone = match[8]
    offset = 0 if zone == "Z" else (1 if zone[0] == "+" else -1) * (int(zone[1:3]) * 3600 + int(zone[4:6]) * 60)
    fraction = int(match[7]) / 10 ** len(match[7]) if match[7] else 0.0
    return _days_since_1970(year, month, day) * 86400 + hour * 3600 + minute * 60 + second + fraction - offset


def _days_since_1970(year: int, month: int, day: int) -> int:
    """Tage seit dem 1. 1. 1970 im gregorianischen Kalender (Jahre ab Maerz gezaehlt, dann faellt der Schalttag ans
    Ende)."""
    year -= month <= 2
    era = year // 400
    year_of_era = year - era * 400
    day_of_year = (153 * ((month + 9) % 12) + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146097 + day_of_era - 719468


def started_at(container: Mapping[str, Any]) -> float | None:
    """`State.StartedAt` eines Inspects als Sekunden seit 1970; `None`, wenn er fehlt, ungueltig ist oder der Container
    nie gestartet wurde."""
    state = container.get("State")
    return docker_time(state.get("StartedAt")) if isinstance(state, dict) else None


# ---------------------------------------------------------------------------
# Ergebnis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MountRef:
    """Herkunft eines Mounts: `kind` (`volume`/`bind`/...), `name` (bei Volumes), `source` (Pfad auf dem Rechner)."""

    kind: str
    name: str | None
    source: str | None

    def same_as(self, mount: Mapping[str, Any]) -> bool:
        if mount.get("Type") != self.kind:
            return False
        if self.kind == "volume":
            return bool(self.name) and mount.get("Name") == self.name
        return bool(self.source) and mount.get("Source") == self.source

    def origin(self) -> tuple[str, str | None]:
        """`(Type, Name bzw. Source)` wie in `clone.mount_map` -- fuer `clone.verify(..., channel=...)`."""
        return self.kind, self.name if self.kind == "volume" else self.source


@dataclass(frozen=True)
class SelfInfo:
    """Der Helfer selbst: ID, Compose-Projekt, sein Kanal, sein Zustand und der Socket auf dem Rechner. `started_at`:
    wann der Docker-Dienst seinen Container zuletzt gestartet hat (`State.StartedAt`, Wanduhr des Rechners in diesem
    Moment; `None`, wenn unbekannt)."""

    id: str
    project: str
    channel: MountRef | None
    state: MountRef | None
    socket_source: str | None
    started_at: float | None = None


@dataclass(frozen=True)
class Target:
    """Das gepruefte Ziel. `container`/`image` sind die Inspects (Ziel und **laufendes** Image), `plan` der
    probeweise gebaute `create`-Body (mit dem alten Image als "neuem")."""

    id: str
    name: str
    image_id: str
    tag_text: str
    floating_tag: str
    version: str
    repo_digests: tuple[str, ...]
    restart_policy: tuple[str, int]
    network_drivers: dict[str, str]
    container: dict[str, Any] = field(repr=False)
    image: dict[str, Any] = field(repr=False)
    plan: clone.Plan = field(repr=False)


@dataclass(frozen=True)
class Preflight:
    """Ergebnis der Vorpruefung. `reason` ist ein Code aus `policy.CODES` (oder `None`, wenn `ready`), `detail`
    ein fester Bezeichner nur fuers Log. `current_version`, `floating_tag` und `pinned` sind auch bei einer
    Ablehnung gefuellt, soweit sie bekannt sind (fuer `status.target`)."""

    ready: bool
    reason: str | None
    detail: str | None = None
    target: Target | None = None
    current_version: str | None = None
    floating_tag: str | None = None
    pinned: bool = False
    known: bool = False
    """Ein Ziel wurde gefunden (sonst gibt es kein `status.target`)."""

    def status_target(self) -> dict[str, Any] | None:
        if not self.known:
            return None
        return {"current_version": self.current_version, "floating_tag": self.floating_tag, "pinned": self.pinned}


@dataclass
class _Info:
    current_version: str | None = None
    floating_tag: str | None = None
    pinned: bool = False
    known: bool = False


# ---------------------------------------------------------------------------
# Vorpruefung
# ---------------------------------------------------------------------------


def preflight(
    engine: Engine,
    *,
    self_id: str | None,
    service: str,
    exclude: Iterable[str] = (),
    repository: str = policy.REPOSITORY,
) -> Preflight:
    """Die ganze Vorpruefung. Wirft nie eine `Refusal` oder `EngineError`, sondern gibt sie als
    `Preflight(ready=False, reason=...)` zurueck. `self_id` ist die eigene ID (`None`: unbekannt), `exclude` die IDs
    im Journal (der alte bzw. neue Container eines laufenden Vorgangs). Ist `exclude` nicht leer, gibt es nie ein
    Ziel (`target` ist `None`): ein weiterer Container mit den Ziel-Labels ist `external_change`, sonst `no_target`.
    Das gilt auch fuer den eigenen, gerade angelegten Container im Schritt `creating` (seine ID steht noch nicht im
    Journal); `reason` ist mit Journal deshalb keine Pruefung auf fremde Container.

    Auch jede andere Ausnahme beim Auswerten der Antworten wird zu "nicht bereit" (`engine_unsupported`, Detail
    `unexpected`): sonst bliebe das letzte, womoeglich bereite Ergebnis stehen (fail-closed). Nur ein ungueltiger
    Dienstname ist ein Programmierfehler (`ValueError`)."""
    if not policy.SERVICE_RE.fullmatch(service):
        raise ValueError("service")
    info = _Info()
    try:
        target = _check(engine, info, self_id=self_id, service=service, exclude=frozenset(exclude),
                        repository=repository)
    except Refusal as exc:
        return Preflight(ready=False, reason=exc.code, detail=exc.detail, **vars_of(info))
    except EngineError as exc:
        return Preflight(ready=False, reason=exc.code, detail=exc.reason, **vars_of(info))
    except Exception:  # noqa: BLE001 - unverstandene Antwort oder Fehler im Code: nie "bereit"
        return Preflight(ready=False, reason=policy.ENGINE_UNSUPPORTED, detail="unexpected", **vars_of(info))
    return Preflight(ready=True, reason=None, target=target, **vars_of(info))


def vars_of(info: _Info) -> dict[str, Any]:
    return {"current_version": info.current_version, "floating_tag": info.floating_tag, "pinned": info.pinned,
            "known": info.known}


def _check(engine: Engine, info: _Info, *, self_id: str | None, service: str, exclude: frozenset[str],
           repository: str) -> Target:
    engine.negotiate()
    me = inspect_self(engine, self_id)
    labels = {LABEL_PROJECT: me.project, LABEL_SERVICE: service, LABEL_ONEOFF: "False"}
    candidates = engine.list_containers([f"{key}={value}" for key, value in labels.items()])
    matching = []
    for item in candidates:
        item_labels = item.get("Labels")
        if not isinstance(item_labels, dict) or any(item_labels.get(k) != v for k, v in labels.items()):
            continue
        item_id = item.get("Id")
        if not policy.is_container_id(item_id):
            raise EngineError("bad_response")
        if item_id == me.id or item_id in exclude:
            continue
        if _has_swarm_labels(item_labels):
            raise Refusal(policy.SWARM)
        matching.append(item_id)
    if exclude:
        # Mit Journal steht jeder Container mit den Ziel-Labels in `exclude`, ausser dem eigenen, im Schritt
        # `creating` gerade angelegten (seine ID ist noch nicht im Journal; der Ablauf erkennt ihn am Zustand). Ein
        # weiterer ist nicht "das Ziel" -- ohne Journal hiesse dieselbe Lage `multiple_targets`.
        raise Refusal(policy.EXTERNAL_CHANGE if matching else policy.NO_TARGET)
    if not matching:
        raise Refusal(policy.NO_TARGET)
    if len(matching) > 1:
        raise Refusal(policy.MULTIPLE_TARGETS)
    target_id = matching[0]
    container = _inspect(engine.inspect_container, target_id, policy.NO_TARGET)
    if container.get("Id") != target_id:
        raise EngineError("bad_response")
    config = container.get("Config")
    host = container.get("HostConfig")
    if not isinstance(config, dict) or not isinstance(host, dict):
        raise Refusal(policy.UNKNOWN_FIELD, "config")
    target_labels = config.get("Labels")
    if not isinstance(target_labels, dict) or any(target_labels.get(k) != v for k, v in labels.items()):
        raise Refusal(policy.NO_TARGET)  # zwischen Liste und Inspect ausgetauscht
    if _has_swarm_labels(target_labels):
        raise Refusal(policy.SWARM)
    name = container.get("Name")
    if not isinstance(name, str) or not name.startswith("/") or not CONTAINER_NAME_RE.fullmatch(name[1:]):
        raise Refusal(policy.UNKNOWN_FIELD, "name")
    name = name[1:]
    info.known = True

    tag_text = config.get("Image")
    image_id = container.get("Image")
    floating = _floating_tag(engine, info, tag_text, image_id, repository)
    check_mounts(container, me)
    image = _inspect(engine.inspect_image, image_id, policy.IMAGE_CONFIG_MISSING)
    ic = clone.image_config(image)
    repo_digests = policy.require_registry_digest(image.get("RepoDigests"), repository=repository)
    version = policy.image_version(ic.get("Labels"))
    info.current_version = version
    policy.check_target_version(version)

    if host.get("AutoRemove") is True:
        raise Refusal(policy.AUTO_REMOVE)
    restart = _restart_policy(host)
    drivers = network_drivers(engine, container)
    plan = clone.build(container, image, new_image_id=image_id, api_version=engine.api_version or (1, 41),
                       network_drivers=drivers)

    others = engine.list_containers()
    check_neighbours(engine, others, target_id=target_id, name=name)
    check_running(container)
    return Target(
        id=target_id, name=name, image_id=image_id, tag_text=tag_text, floating_tag=floating, version=version,
        repo_digests=tuple(repo_digests), restart_policy=restart, network_drivers=drivers, container=container,
        image=image, plan=plan,
    )


def _inspect(call: Any, ref: str, not_found_code: str) -> dict[str, Any]:
    try:
        return call(ref)
    except EngineError as exc:
        if exc.not_found:
            raise Refusal(not_found_code) from None
        raise


def inspect_self(engine: Engine, self_id: str | None) -> SelfInfo:
    """Self-Inspect: Projekt aus den eigenen Labels, eigene Mounts fuer Kanal, Zustand und Socket."""
    if not policy.is_container_id(self_id):
        raise Refusal(policy.SELF_UNKNOWN)
    me = _inspect(engine.inspect_container, self_id, policy.SELF_UNKNOWN)  # type: ignore[arg-type]
    if me.get("Id") != self_id:
        raise Refusal(policy.SELF_UNKNOWN)
    config = me.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    project = labels.get(LABEL_PROJECT) if isinstance(labels, dict) else None
    if not isinstance(project, str) or not project:
        raise Refusal(policy.NOT_COMPOSE)
    if not PROJECT_RE.fullmatch(project):
        raise Refusal(policy.NOT_COMPOSE, "project_name")
    mounts = [m for m in me.get("Mounts") or [] if isinstance(m, dict)] if isinstance(me.get("Mounts"), list) else []
    return SelfInfo(
        id=self_id, project=project,  # type: ignore[arg-type]
        channel=_mount_at(mounts, CHANNEL_IN_HELPER),
        state=_mount_at(mounts, STATE_IN_HELPER),
        socket_source=host_path(mounts, SOCKET_PATH),
        started_at=started_at(me),
    )


def _mount_at(mounts: list[dict[str, Any]], destination: str) -> MountRef | None:
    for mount in mounts:
        if _clean(mount.get("Destination")) == destination:
            kind = mount.get("Type")
            name = mount.get("Name")
            source = mount.get("Source")
            return MountRef(kind=kind if isinstance(kind, str) else "", name=name if isinstance(name, str) else None,
                            source=source if isinstance(source, str) and source else None)
    return None


def host_path(mounts: list[dict[str, Any]], path: str) -> str | None:
    """Pfad auf dem Rechner zu `path` im Helfer (laengster passender Mount), sonst `None`."""
    best: tuple[int, str] | None = None
    for mount in mounts:
        destination, source = _clean(mount.get("Destination")), mount.get("Source")
        if not destination or not isinstance(source, str) or not source:
            continue
        inside = path == destination or destination == "/" or path.startswith(destination + "/")
        if inside and (best is None or len(destination) > best[0]):
            rest = path if destination == "/" else path[len(destination):]
            best = (len(destination), source.rstrip("/") + rest if rest else source)
    return None if best is None else best[1]


def _clean(path: object) -> str:
    if not isinstance(path, str) or not path:
        return ""
    return path.rstrip("/") or "/"


def _canon(path: str) -> str:
    """`/var/run` ist auf ueblichen Systemen ein Link auf `/run`: beide Schreibweisen gelten als gleich."""
    path = _clean(path)
    if path == "/var/run" or path.startswith("/var/run/"):
        path = "/run" + path[len("/var/run"):]
    return path


def _covers(outer: str, inner: str) -> bool:
    """Sieht ein Mount mit Quelle `outer` den Pfad `inner` (gleich oder Elternordner)?"""
    outer, inner = _canon(outer), _canon(inner)
    return outer == inner or outer == "/" or inner.startswith(outer + "/")


def check_mounts(container: Mapping[str, Any], me: SelfInfo) -> None:
    """Kein Engine-Socket und kein `/state` im Ziel (`unsafe_target`), der Kanal des Helfers unter
    `/app/updater` (`channel_missing_in_target`)."""
    if me.socket_source is None:
        raise Refusal(policy.UNSAFE_TARGET, "socket_unknown")  # ohne den Pfad laesst sich das nicht pruefen
    sensitive = [me.socket_source]
    if me.state is not None and me.state.source:
        sensitive.append(me.state.source)
    mounts = container.get("Mounts")
    if mounts is not None and not isinstance(mounts, list):
        raise Refusal(policy.UNSAFE_TARGET, "mounts")
    channel_ok = False
    for mount in mounts or []:
        if not isinstance(mount, dict):
            raise Refusal(policy.UNSAFE_TARGET, "mounts")
        source = mount.get("Source")
        destination = _clean(mount.get("Destination"))
        if me.state is not None and me.state.kind == "volume" and mount.get("Type") == "volume" \
                and mount.get("Name") == me.state.name:
            raise Refusal(policy.UNSAFE_TARGET, "state_volume")
        if isinstance(source, str) and source:
            if any(_covers(source, path) for path in sensitive):
                raise Refusal(policy.UNSAFE_TARGET, "host_path")
            if _clean(source).endswith("/docker.sock"):
                raise Refusal(policy.UNSAFE_TARGET, "socket")
        if destination.endswith("/docker.sock"):
            raise Refusal(policy.UNSAFE_TARGET, "socket")
        if destination == CHANNEL_IN_TARGET and me.channel is not None and me.channel.same_as(mount):
            channel_ok = True
    if not channel_ok:
        raise Refusal(policy.CHANNEL_MISSING_IN_TARGET)


def _floating_tag(engine: Engine, info: _Info, tag_text: Any, image_id: Any, repository: str) -> str:
    """Bei `pinned_version` wird die laufende Version noch gelesen (fuer den Status), dann abgelehnt."""
    if not policy.is_image_id(image_id):
        raise Refusal(policy.IMAGE_CONFIG_MISSING)
    try:
        tag = policy.floating_tag(tag_text, repository=repository)
    except Refusal as exc:
        if exc.code == policy.PINNED_VERSION:
            info.pinned = True
            try:
                info.current_version = policy.image_version(clone.image_config(engine.inspect_image(image_id))
                                                            .get("Labels"))
            except (Refusal, EngineError):
                pass
        raise
    info.floating_tag = tag
    return tag


def _has_swarm_labels(labels: Mapping[str, Any]) -> bool:
    return any(isinstance(key, str) and key.startswith(SWARM_PREFIX) for key in labels)


def _restart_policy(host: Mapping[str, Any]) -> tuple[str, int]:
    restart = host.get("RestartPolicy") or {}
    name = restart.get("Name", "") if isinstance(restart, dict) else None
    count = restart.get("MaximumRetryCount", 0) if isinstance(restart, dict) else None
    if name not in RESTART_POLICIES or type(count) is not int or not 0 <= count <= 1_000_000:
        raise Refusal(policy.UNKNOWN_FIELD, "restart_policy")
    return name, count  # type: ignore[return-value]


def network_drivers(engine: Engine, container: Mapping[str, Any]) -> dict[str, str]:
    """`NetworkID -> Driver` fuer jedes Netz des Ziels (`GET /networks/{id}`; 404 -> `network_unclear`)."""
    host = container.get("HostConfig") or {}
    if not clone.has_own_network(clone.network_mode(host)):
        return {}
    settings = container.get("NetworkSettings")
    networks = settings.get("Networks") if isinstance(settings, dict) else None
    if not isinstance(networks, dict) or not networks:
        raise Refusal(policy.NETWORK_UNCLEAR)
    drivers: dict[str, str] = {}
    for endpoint in networks.values():
        network_id = endpoint.get("NetworkID") if isinstance(endpoint, dict) else None
        if not policy.is_container_id(network_id):
            raise Refusal(policy.NETWORK_UNCLEAR)
        network = _inspect(engine.inspect_network, network_id, policy.NETWORK_UNCLEAR)
        driver = network.get("Driver")
        if network.get("Id") != network_id or not isinstance(driver, str):
            raise Refusal(policy.NETWORK_UNCLEAR)
        drivers[network_id] = driver
    return drivers


_NAMESPACE_FIELDS = (("NetworkMode", "network_mode"), ("PidMode", "pid_mode"), ("IpcMode", "ipc_mode"))
_HEX_RE = re.compile(r"[0-9a-f]+", re.ASCII)


def points_at(ref: str, *, target_id: str, name: str, other_names: frozenset[str] = frozenset()) -> bool:
    """Meint `container:<ref>` das Ziel? Der Docker-Dienst loest `<ref>` in dieser Reihenfolge auf: volle ID, Name
    (auch mit fuehrendem `/`), erst dann ein beliebig kurzes, eindeutiges ID-Praefix. Ein hexadezimaler `ref`, mit
    dem die ID des Ziels beginnt, gilt darum als Verweis -- auch wenn er nicht eindeutig waere (fail-closed ist hier
    billig) --, ausser er ist der Name eines **anderen** Containers (`other_names`, ohne fuehrendes `/`): Dann
    loest der Docker-Dienst ihn als diesen Namen auf, und der Verweis zielt nicht aufs Ziel."""
    if ref in (target_id, name, "/" + name):
        return True
    if ref.lstrip("/") in other_names:
        return False
    return _HEX_RE.fullmatch(ref) is not None and target_id.startswith(ref)


def _other_names(containers: list[dict[str, Any]], target_id: str) -> frozenset[str]:
    """Die Namen aller Container ausser dem Ziel (auch der eigene Helfer: Docker loest dessen Namen genauso vor einem
    ID-Praefix auf). Nur echte Namen (`/name`), keine Link-Aliase der Form `/a/b`."""
    names: set[str] = set()
    for item in containers:
        if item.get("Id") == target_id:
            continue
        item_names = item.get("Names")
        for entry in item_names if isinstance(item_names, list) else []:
            if isinstance(entry, str) and entry.startswith("/") and "/" not in entry[1:]:
                names.add(entry[1:])
    return frozenset(names)


def check_neighbours(engine: Engine, containers: list[dict[str, Any]], *, target_id: str, name: str) -> None:
    """`dependent_containers`: ein anderer Container teilt Netz, PID- oder IPC-Namensraum des Ziels
    (`NetworkMode`/`PidMode`/`IpcMode` = `container:<ziel>`). Stoppt oder entfernt der Ablauf das Ziel, verlieren sie
    ihn mit. Die Liste der Container zeigt nur `NetworkMode`, darum wird jeder andere Container gelesen (ausser dem
    Ziel); ein inzwischen verschwundener (404) wird uebersprungen, jeder andere Fehler fuehrt zur Ablehnung (kein
    "bereit" ohne die Pruefung).

    Auch der **Helfer selbst** wird geprueft: zeigt er auf das Ziel (falsch konfiguriert, z. B. `PidMode`), stuerbe er
    beim Stoppen des Ziels mit und liesse sich nicht wieder starten. Das ist ein Konfigurationsfehler und darf die
    Vorpruefung fail-closed ablehnen.
    `name_taken`: `<name>-previous` ist schon vergeben (oder zu lang)."""
    previous = name + PREVIOUS_SUFFIX
    if not CONTAINER_NAME_RE.fullmatch(previous):
        raise Refusal(policy.NAME_TAKEN)
    other_names = _other_names(containers, target_id)
    for item in containers:
        item_id = item.get("Id")
        if not policy.is_container_id(item_id):
            raise EngineError("bad_response")
        if item_id == target_id:
            continue
        try:
            other = engine.inspect_container(item_id)
        except EngineError as exc:
            if exc.not_found:
                continue
            raise
        host = other.get("HostConfig")
        if other.get("Id") != item_id or not isinstance(host, dict):
            raise EngineError("bad_response")
        for key, detail in _NAMESPACE_FIELDS:
            mode = host.get(key)
            if isinstance(mode, str) and mode.startswith("container:") \
                    and points_at(mode[len("container:"):], target_id=target_id, name=name,
                                  other_names=other_names):
                raise Refusal(policy.DEPENDENT_CONTAINERS, detail)
    for item in containers:
        names = item.get("Names")
        if isinstance(names, list) and "/" + previous in names:
            raise Refusal(policy.NAME_TAKEN)


def check_running(container: Mapping[str, Any]) -> None:
    """Healthcheck vorhanden (`no_healthcheck`), Ziel laeuft (`target_not_running`) und ist gesund
    (`target_unhealthy`: ein jetzt ungesundes Ziel fiele nach 15 min unnoetig zurueck)."""
    config = container.get("Config") or {}
    health_config = config.get("Healthcheck") if isinstance(config, dict) else None
    test = health_config.get("Test") if isinstance(health_config, dict) else None
    if not isinstance(test, list) or not test or test[0] == "NONE":
        raise Refusal(policy.NO_HEALTHCHECK)
    state = container.get("State")
    if not isinstance(state, dict):
        raise Refusal(policy.TARGET_NOT_RUNNING)
    if (state.get("Running") is not True or state.get("Restarting") is not False or state.get("Paused") is True
            or state.get("Status") != "running"):
        raise Refusal(policy.TARGET_NOT_RUNNING)
    health = state.get("Health")
    if not isinstance(health, dict):
        raise Refusal(policy.NO_HEALTHCHECK)
    if health.get("Status") != "healthy":
        raise Refusal(policy.TARGET_UNHEALTHY)
