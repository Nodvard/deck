"""Klonen des Ziels: Inspect -> Body fuer `create` + Liste fuer `connect`, und die Nachkontrolle. Nur reine
Funktionen: keine Ein-/Ausgabe, die Eingaben werden nie veraendert.

Das Inspect eines Containers ist eine Mischung aus Nutzerangaben (Compose-Datei) und Standards des **alten**
Images, denn der Docker-Dienst fuehrt beides beim Anlegen zusammen (Container-Werte gewinnen). Ein naiver Klon
schriebe die alten Standards fest (`PYTHON_VERSION`, das Versions-Label, der alte Healthcheck ...). Darum:

* **Config nur ueber eine Allowlist**: Image-Werte werden "herausgerechnet" (weggelassen, wenn gleich dem Wert
  in `IC`, der Config des alten Images); dann gilt beim Anlegen der Standard des **neuen** Images.
  `Entrypoint` wird **nie** gesendet: gleich `IC.Entrypoint` (und nicht leer) -> weglassen, sonst
  `custom_entrypoint` (ein `[]` schaltete `/entrypoint.sh` und damit `boot` samt Vorher-Kopie ab).
* **HostConfig** wird unveraendert uebernommen, bis auf wenige bereinigte Felder; `AutoRemove` -> Ablehnung.
  `MaskedPaths`/`ReadonlyPaths` bleiben weg, damit der Docker-Dienst seine (neueste) Standardliste setzt -- ausser sie
  sind beim alten Container ausdruecklich leer (`[]`, so sendet es `systempaths=unconfined`): dann geht das `[]`
  mit, sonst bekaeme der Klon stillschweigend Masken, die der alte nicht hatte. Die Nachkontrolle verlangt, dass der
  Klon mindestens die Pfade des alten maskiert.
* **Veroeffentlichte Ports** (`HostConfig.PortBindings`) bleiben in `Config.ExposedPorts`, auch wenn das alte Image sie
  schon mit `EXPOSE` erklaert (so schickt es auch Compose). Der Docker-Dienst veroeffentlicht einen Port nur, wenn er
  dort steht; liesse der Klon ihn weg, haengt der Zugang davon ab, ob das **neue** Image ihn noch erklaert.
  **Anonyme Volumes** (nur in `.Mounts`, etwa `VOLUME /app/data` ohne eigenes Volume) werden als `Mounts`
  angehaengt, und anonyme Eintraege in `HostConfig.Mounts` (Compose: `volumes: [/pfad]`, ohne `Source`) bekommen den
  Namen des alten Volumes -- sonst bekaeme der Klon ein neues, leeres Volume.
* **Netze**: genau ein Endpunkt beim `create` (das Primaernetz aus `NetworkMode`), alle weiteren per
  `connect` vor dem Start -- ein Codepfad fuer API 1.41 bis 1.54. Aliases ohne die Kurz-ID des alten Containers.
* **Unbekanntes Feld mit Nicht-Standardwert -> `unknown_field`** (fail-closed: eine neuere Engine-Version mit einem
  neuen Feld fuehrt zu einer Ablehnung, nie zu einem stillen Verlust).
* **Nachkontrolle** (`verify`): der neue Container wird gegen den alten gehalten; jede nicht erklaerte
  Abweichung ist `clone_mismatch`.

"Standardwert" heisst: `null`, `false`, `0`, `""`, `[]`, `{}` (oberste Ebene; ein Dict mit Eintraegen ist nie
Standard, `Tmpfs: {"/tmp": ""}` traegt die Bedeutung im Schluessel).
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import policy
from .policy import Refusal

COMPOSE_PREFIX = "com.docker.compose."
COMPOSE_IMAGE_LABEL = "com.docker.compose.image"

CONFIG_COPY = ("Domainname", "AttachStdin", "AttachStdout", "AttachStderr", "Tty", "OpenStdin", "StdinOnce",
               "StopTimeout")
"""Config-Felder, die unveraendert uebernommen werden."""
CONFIG_SUBTRACT = ("User", "WorkingDir", "StopSignal")
"""Config-Felder, die herausgerechnet werden (weglassen, wenn gleich dem Wert im alten Image)."""
CONFIG_DROP = ("MacAddress", "OnBuild", "ArgsEscaped", "Shell")
"""Config-Felder, die wegfallen (Felder aus dem Image-Bau bzw. veraltet). `NetworkDisabled` ist eigens behandelt."""
CONFIG_SPECIAL = ("Image", "Hostname", "Env", "Entrypoint", "Cmd", "Healthcheck", "ExposedPorts", "Volumes", "Labels",
                  "NetworkDisabled")
HEALTH_FIELDS = ("Test", "Interval", "Timeout", "StartPeriod", "StartInterval", "Retries")

HOST_COPY = (
    # Speicher
    "Binds", "Mounts", "VolumeDriver", "VolumesFrom", "Tmpfs", "StorageOpt",
    # Netz
    "PortBindings", "PublishAllPorts", "NetworkMode", "Dns", "DnsOptions", "DnsSearch", "ExtraHosts",
    # Start und Protokoll
    "RestartPolicy", "LogConfig", "Init", "Runtime", "ConsoleSize", "Annotations",
    # Rechte
    "CapAdd", "CapDrop", "SecurityOpt", "Privileged", "ReadonlyRootfs", "GroupAdd",
    # Geraete
    "Devices", "DeviceRequests", "DeviceCgroupRules", "Ulimits", "Sysctls", "ShmSize", "PidsLimit", "OomScoreAdj",
    "OomKillDisable",
    # Namensraeume und cgroups
    "IpcMode", "PidMode", "UTSMode", "UsernsMode", "CgroupnsMode", "Cgroup", "CgroupParent", "Isolation",
    # Ressourcen
    "Memory", "MemoryReservation", "MemorySwap", "MemorySwappiness",
    "NanoCpus", "CpuShares", "CpuPeriod", "CpuQuota", "CpuRealtimePeriod", "CpuRealtimeRuntime", "CpusetCpus",
    "CpusetMems", "CpuCount", "CpuPercent",
    "BlkioWeight", "BlkioWeightDevice", "BlkioDeviceReadBps", "BlkioDeviceWriteBps", "BlkioDeviceReadIOps",
    "BlkioDeviceWriteIOps", "IOMaximumBandwidth", "IOMaximumIOps",
)
"""HostConfig-Felder, die unveraendert uebernommen werden."""
HOST_DROP = ("KernelMemory", "KernelMemoryTCP", "ContainerIDFile")
"""HostConfig-Felder, die wegfallen (entfernt bzw. nur Ausgabe)."""
HOST_PATHS = ("MaskedPaths", "ReadonlyPaths")
"""Weglassen, damit der Daemon seine Standardliste setzt; nur ein ausdruecklich leeres Feld (`[]`) eines nicht
privilegierten Containers wird mitgeschickt. Die Nachkontrolle vergleicht sie eigens (`_paths_kept`)."""
HOST_SPECIAL = ("Links", "AutoRemove")

CREATE_CONFIG_KEYS = frozenset({*CONFIG_SPECIAL, *CONFIG_COPY, *CONFIG_SUBTRACT, "HostConfig", "NetworkingConfig"}
                               - {"Entrypoint", "NetworkDisabled"})
"""Die Schluessel, die `build` im `create`-Body senden kann (oberste Ebene). Der Engine-Client laesst im Body nur
diese in genau dieser Schreibweise zu: der Docker-Dienst ordnet JSON-Schluessel ohne Beachtung der Gross- und
Kleinschreibung zu (bei Doppelten gewinnt der letzte), eine Pruefung auf einzelne Namen liesse `entrypoint` oder ein
zweites `image` durch."""
CREATE_HOST_KEYS = frozenset({*HOST_COPY, *HOST_PATHS, "Links"})
"""Dasselbe fuer `HostConfig`."""

ENDPOINT_COPY = ("IPAMConfig", "Aliases", "Links", "DriverOpts", "GwPriority")
ENDPOINT_DROP = ("NetworkID", "EndpointID", "Gateway", "IPAddress", "IPPrefixLen", "IPv6Gateway",
                 "GlobalIPv6Address", "GlobalIPv6PrefixLen", "MacAddress", "DNSNames")
NO_NETWORK_MODES = ("host", "none")
UNSUPPORTED_DRIVERS = ("macvlan", "ipvlan")
GW_PRIORITY_API = (1, 48)
DATA_PATH = "/app/data"
CHANNEL_PATH = "/app/updater"
REQUIRED_MOUNTS = (DATA_PATH, CHANNEL_PATH)
"""Diese Ziele muessen beim alten und beim neuen Container gleich eingehaengt sein (Daten und Kanal)."""

_DETAIL_RE = re.compile(r"(?<!^)(?=[A-Z])")


@dataclass(frozen=True)
class Plan:
    """Ergebnis von `build`: Name, `create`-Body (`Config` + `HostConfig` + `NetworkingConfig` mit hoechstens einem
    Endpunkt) und die weiteren Netze fuer `connect` (`(NetworkID, EndpointConfig)`, vor dem Start).

    `body` und `connects` stehen **nicht** in `repr`: der Body enthaelt die Umgebungswerte des Containers (also
    Geheimnisse) im Klartext, und ein `Plan` in einer Fehlermeldung oder einer Protokollzeile soll sie nicht
    weitergeben."""

    name: str
    body: dict[str, Any] = dataclasses.field(repr=False)
    connects: tuple[tuple[str, dict[str, Any]], ...] = dataclasses.field(repr=False)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def is_default(value: Any) -> bool:
    """Standardwert (siehe Kopf der Datei): `null`, `false`, `0`, `""`, `[]`, `{}` -- nur auf oberster Ebene. Ein Dict
    mit Eintraegen ist nie Standard, auch wenn die Werte leer sind (`Tmpfs: {"/tmp": ""}`, `ExposedPorts`)."""
    if value is None or value is False or (type(value) in (int, float) and value == 0):
        return True
    return value == "" or value == [] or value == {}


def norm(value: Any) -> Any:
    """Fuer Vergleiche: ein Standardwert (siehe `is_default`) wird `None`, alles andere bleibt, wie es ist."""
    return None if is_default(value) else value


def struct_norm(value: Any) -> Any:
    """Fuer Strukturen mit festen Feldern (`IPAMConfig`, Mount-Eintraege): Felder mit Standardwert fallen weg (auch
    verschachtelt), so dass `{"IPv4Address": "1.2.3.4", "LinkLocalIPs": null}` gleich `{"IPv4Address": "1.2.3.4"}`
    ist. Nicht fuer Maps mit frei gewaehlten Schluesseln benutzen (dort traegt schon der Schluessel die Bedeutung)."""
    if isinstance(value, dict):
        out = {key: struct_norm(item) for key, item in value.items()}
        out = {key: item for key, item in out.items() if not is_default(item)}
        return out or None
    if isinstance(value, list):
        return [struct_norm(item) for item in value] or None
    return norm(value)


def _copy(value: Any) -> Any:
    """Tiefe Kopie ueber JSON (die Werte stammen aus JSON; so teilt der Body nie Objekte mit der Eingabe)."""
    return json.loads(json.dumps(value))


def _detail(field: str) -> str:
    """Feldname als fester Bezeichner (`CODE_RE`) fuer das Log: `NetworkDisabled` -> `network_disabled`."""
    text = _DETAIL_RE.sub("_", field).lower()
    return re.sub(r"[^a-z_]", "", text)[:40] or "field"


def _unknown(field: str) -> Refusal:
    return Refusal(policy.UNKNOWN_FIELD, _detail(field))


def _as_dict(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _unknown(field)
    return value


def _map_keys(value: Any) -> set[str]:
    """Die Schluessel einer Map (`PortBindings`, `ExposedPorts`); alles andere ist leer (die Felder selbst prueft
    `build` bzw. die Vergleiche der Nachkontrolle)."""
    return {key for key in value if isinstance(key, str)} if isinstance(value, dict) else set()


def _str_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _unknown(field)
    return value


def short_id(container_id: str) -> str:
    return container_id[:12]


def bind_destination(bind: str) -> str | None:
    """Zielpfad aus einem `Binds`-Eintrag (`quelle:ziel[:optionen]`, auch `C:\\pfad:/ziel`, auch nur `/ziel`)."""
    parts = bind.split(":")
    if len(parts) == 1:
        return parts[0] if parts[0].startswith("/") else None
    for part in parts[1:]:
        if part.startswith("/"):
            return part
    return None


def _clean_path(path: str) -> str:
    return path.rstrip("/") or "/"


def network_mode(host: Mapping[str, Any]) -> str:
    mode = host.get("NetworkMode")
    return mode if isinstance(mode, str) else ""


def has_own_network(mode: str) -> bool:
    """`False` fuer `host`, `none` und `container:*` (dann keine `NetworkingConfig`, kein `connect`)."""
    return mode not in NO_NETWORK_MODES and not mode.startswith("container:")


def image_config(image: Mapping[str, Any]) -> dict[str, Any]:
    """`Config` aus dem Inspect eines Images; fehlt sie (containerd-Store), ist das `image_config_missing`."""
    config = image.get("Config") if isinstance(image, Mapping) else None
    if not isinstance(config, dict):
        raise Refusal(policy.IMAGE_CONFIG_MISSING)
    return config


# ---------------------------------------------------------------------------
# Bauen
# ---------------------------------------------------------------------------


def build(
    container: Mapping[str, Any],
    old_image: Mapping[str, Any],
    *,
    new_image_id: str,
    api_version: tuple[int, int],
    network_drivers: Mapping[str, str],
) -> Plan:
    """Body fuer `POST /containers/create?name=<name>` und die Liste fuer `connect`.

    * `container`: Inspect des alten Containers (`A`).
    * `old_image`: Inspect des **alten** Images (`GET /images/{A.Image}/json`); daraus `IC`.
    * `new_image_id`: Image-ID des neuen Images (fuer `com.docker.compose.image`).
    * `api_version`: die ausgehandelte API-Version (`GwPriority` erst ab 1.48).
    * `network_drivers`: `NetworkID -> Driver` fuer jedes Netz des Containers (aus `GET /networks/{id}`).

    Wirft `Refusal`: `custom_entrypoint`, `auto_remove`, `unknown_field`, `network_unclear`, `macvlan`,
    `image_config_missing`.
    """
    if not policy.is_image_id(new_image_id):
        raise ValueError("new_image_id")
    ic = image_config(old_image)
    config = _as_dict(container.get("Config"), "Config")
    host = _as_dict(container.get("HostConfig"), "HostConfig")
    container_id = container.get("Id")
    if not policy.is_container_id(container_id):
        raise _unknown("Id")
    name = container.get("Name")
    if not isinstance(name, str) or not name.startswith("/"):
        raise _unknown("Name")
    mode = network_mode(host)
    body = _build_config(config, ic, container_id=container_id, new_image_id=new_image_id, host=host, mode=mode)
    body["HostConfig"] = _build_host(host, container.get("Mounts"))
    connects: list[tuple[str, dict[str, Any]]] = []
    if has_own_network(mode):
        primary_key, endpoints = _build_networks(container, mode, container_id, api_version, network_drivers)
        body["NetworkingConfig"] = {"EndpointsConfig": {primary_key: endpoints.pop(primary_key)[1]}}
        connects = [endpoints[key] for key in sorted(endpoints)]
    return Plan(name=name[1:], body=body, connects=tuple(connects))


def _build_config(config: dict[str, Any], ic: dict[str, Any], *, container_id: str, new_image_id: str,
                  host: Mapping[str, Any], mode: str) -> dict[str, Any]:
    body: dict[str, Any] = {}
    image_text = config.get("Image")
    if not isinstance(image_text, str) or not image_text:
        raise _unknown("Image")
    body["Image"] = image_text  # unveraenderter Tag-Text

    # Entrypoint und Cmd: Entrypoint wird nie gesendet.
    entrypoint = _str_list(config.get("Entrypoint"), "Entrypoint")
    image_entrypoint = _str_list(ic.get("Entrypoint"), "Entrypoint")
    if not entrypoint or entrypoint != image_entrypoint:
        # Leer (auch wenn das Image keinen hat) oder abweichend: `boot` und die Vorher-Kopie liefen nicht.
        raise Refusal(policy.CUSTOM_ENTRYPOINT)
    cmd = _str_list(config.get("Cmd"), "Cmd")
    image_cmd = _str_list(ic.get("Cmd"), "Cmd")
    if not cmd and image_cmd:
        # Leeres Cmd neben einem Cmd im Image: nur mit eigenem Entrypoint moeglich -- laesst sich ohne Entrypoint
        # nicht nachbauen (ein leeres Cmd erbt wieder das des Images).
        raise Refusal(policy.CUSTOM_ENTRYPOINT)
    if cmd != image_cmd:
        body["Cmd"] = list(cmd)

    hostname = config.get("Hostname")
    if hostname is not None and not isinstance(hostname, str):
        raise _unknown("Hostname")
    uts = host.get("UTSMode")
    if (hostname and hostname != short_id(container_id) and mode != "host" and not mode.startswith("container:")
            and is_default(uts)):
        body["Hostname"] = hostname

    for field in CONFIG_COPY:
        if not is_default(config.get(field)):
            body[field] = _copy(config[field])
    for field in CONFIG_SUBTRACT:
        value = config.get(field)
        if value is not None and not isinstance(value, str):
            raise _unknown(field)
        if value and value != (ic.get(field) or ""):
            body[field] = value

    image_env = set(_str_list(ic.get("Env"), "Env"))
    env = [entry for entry in _str_list(config.get("Env"), "Env") if entry not in image_env]
    if env:
        body["Env"] = env

    health = _build_healthcheck(config.get("Healthcheck"), ic.get("Healthcheck"))
    if health:
        body["Healthcheck"] = health

    # Veroeffentlichte Ports bleiben in `ExposedPorts`, auch wenn das alte Image sie schon erklaert: der Daemon
    # veroeffentlicht einen Eintrag aus `PortBindings` nur, wenn der Port dort steht (Docker vor 29), und das
    # Einsetzen aus dem Image geschieht erst beim Anlegen -- aus dem **neuen** Image.
    published = _map_keys(host.get("PortBindings"))
    for field in ("ExposedPorts", "Volumes"):
        image_keys = set(_as_dict(ic.get(field), field))
        keep = published if field == "ExposedPorts" else set()
        rest = {key: _copy(value) for key, value in _as_dict(config.get(field), field).items()
                if key not in image_keys or key in keep}
        if rest:
            body[field] = rest

    labels = _build_labels(config.get("Labels"), ic.get("Labels"), new_image_id)
    if labels:
        body["Labels"] = labels

    if config.get("NetworkDisabled") is True:
        raise _unknown("NetworkDisabled")
    for field, value in config.items():
        if field in CONFIG_SPECIAL or field in CONFIG_COPY or field in CONFIG_SUBTRACT or field in CONFIG_DROP:
            continue
        if not is_default(value):
            raise _unknown(field)
    return body


def _build_healthcheck(health: Any, image_health: Any) -> dict[str, Any]:
    """Je Feld herausrechnen; der Daemon ergaenzt fehlende Felder aus dem neuen Image."""
    health = _as_dict(health, "Healthcheck")
    image_health = _as_dict(image_health, "Healthcheck")
    out: dict[str, Any] = {}
    for field, value in health.items():
        if field not in HEALTH_FIELDS:
            if not is_default(value):
                raise _unknown("Healthcheck")
            continue
        if is_default(value) or norm(value) == norm(image_health.get(field)):
            continue
        out[field] = _copy(value)
    return out


def _build_labels(labels: Any, image_labels: Any, new_image_id: str) -> dict[str, str]:
    labels = _as_dict(labels, "Labels")
    image_labels = _as_dict(image_labels, "Labels")
    out: dict[str, str] = {}
    for key, value in labels.items():
        if not isinstance(value, str):
            raise _unknown("Labels")
        if key == COMPOSE_IMAGE_LABEL:
            out[key] = new_image_id
        elif key.startswith(COMPOSE_PREFIX):
            out[key] = value
        elif key in image_labels and image_labels[key] == value:
            continue
        else:
            out[key] = value
    return out


def _build_host(host: dict[str, Any], mounts: Any) -> dict[str, Any]:
    if host.get("AutoRemove") is True:
        raise Refusal(policy.AUTO_REMOVE)
    out: dict[str, Any] = {}
    for field, value in host.items():
        if field in HOST_COPY:
            if not is_default(value):
                out[field] = _copy(value)
        elif field in HOST_DROP:
            continue
        elif field in HOST_PATHS:
            # Nur ein ausdruecklich leeres Feld (`systempaths=unconfined`) geht mit; sonst setzt der Daemon seine Liste.
            if value == [] and host.get("Privileged") is not True:
                out[field] = []
        elif field == "Links":
            links = _links(value)
            if links:
                out["Links"] = links
        elif field == "AutoRemove":
            if not is_default(value):
                raise Refusal(policy.AUTO_REMOVE)
        elif not is_default(value):
            raise _unknown(field)
    anonymous = anonymous_volumes(host, mounts)
    if "Mounts" in out:
        out["Mounts"] = _keep_anonymous_sources(out["Mounts"], mounts)
    if anonymous:
        if not is_default(host.get("VolumesFrom")):
            # Volumes aus `VolumesFrom` stehen ebenfalls nur in `.Mounts`: sie liessen sich von anonymen nicht
            # unterscheiden, und doppelt eingehaengt scheitert `create`.
            raise _unknown("VolumesFrom")
        out["Mounts"] = list(out.get("Mounts") or []) + anonymous
    return out


def _links(value: Any) -> list[str]:
    """`/ziel:/dieser/alias` (Inspect) -> `ziel:alias` (create)."""
    out = []
    for entry in _str_list(value, "Links"):
        source, sep, alias = entry.partition(":")
        if not sep or not source.startswith("/") or not alias.startswith("/"):
            raise _unknown("Links")
        target, alias_name = source[1:], alias.rsplit("/", 1)[-1]
        if not target or not alias_name or "/" in target:
            raise _unknown("Links")
        out.append(f"{target}:{alias_name}")
    return out


def _keep_anonymous_sources(host_mounts: list[Any], mounts: Any) -> list[Any]:
    """`HostConfig.Mounts`-Eintraege `Type=volume` **ohne** `Source` sind anonyme Volumes (Compose legt so ein
    `volumes: [/pfad]` an, live geprueft mit Compose 5.1 und Docker 29.3). Unveraendert gesendet bekaeme der Klon ein
    neues, leeres Volume. Darum bekommt der Eintrag als `Source` den Namen des Volumes, das der alte Container dort
    eingehaengt hat (`.Mounts`); alle anderen Angaben (etwa `VolumeOptions.Subpath`) bleiben. Fehlt es dort:
    `unknown_field`."""
    names: dict[str, str] = {}
    for mount in mounts if isinstance(mounts, list) else []:
        if isinstance(mount, dict) and mount.get("Type") == "volume" and isinstance(mount.get("Destination"), str) \
                and isinstance(mount.get("Name"), str) and mount["Name"]:
            names[_clean_path(mount["Destination"])] = mount["Name"]
    out = []
    for entry in host_mounts:
        if isinstance(entry, dict) and entry.get("Type") == "volume" and is_default(entry.get("Source")):
            name = names.get(_clean_path(entry["Target"])) if isinstance(entry.get("Target"), str) else None
            if name is None:
                raise _unknown("Mounts")
            entry = {**entry, "Source": name}
        out.append(entry)
    return out


def anonymous_volumes(host: Mapping[str, Any], mounts: Any) -> list[dict[str, Any]]:
    """Volumes, die nur in `.Mounts` stehen (weder in `Binds` noch in `HostConfig.Mounts`): als
    `{"Type": "volume", "Source": Name, "Target": Destination, "ReadOnly": !RW}`.

    Ein `Binds`-Eintrag nur mit Zielpfad (`/pfad`) ist ebenfalls ein anonymes Volume (live geprueft); er liesse sich nur
    mit einem anderen `Binds`-Text nachbauen, den die Nachkontrolle nicht vom alten unterscheiden koennte -- darum
    `unknown_field` (vorher, statt erst nach `create` als `clone_mismatch`)."""
    covered = set()
    for bind in _str_list(host.get("Binds"), "Binds"):
        destination = bind_destination(bind)
        if destination is None or ":" not in bind:
            raise _unknown("Binds")
        covered.add(_clean_path(destination))
    host_mounts = host.get("Mounts")
    if host_mounts is not None and not isinstance(host_mounts, list):
        raise _unknown("Mounts")
    for mount in host_mounts or []:
        target = mount.get("Target") if isinstance(mount, dict) else None
        if not isinstance(target, str):
            raise _unknown("Mounts")
        covered.add(_clean_path(target))
    for destination in _as_dict(host.get("Tmpfs"), "Tmpfs"):
        covered.add(_clean_path(destination))
    if mounts is not None and not isinstance(mounts, list):
        raise _unknown("Mounts")
    out = []
    for mount in mounts or []:
        if not isinstance(mount, dict):
            raise _unknown("Mounts")
        if mount.get("Type") != "volume":
            continue
        destination, name, rw = mount.get("Destination"), mount.get("Name"), mount.get("RW")
        if not isinstance(destination, str) or not isinstance(name, str) or not name or type(rw) is not bool:
            raise _unknown("Mounts")
        if _clean_path(destination) in covered:
            continue
        out.append({"Type": "volume", "Source": name, "Target": destination, "ReadOnly": not rw})
        covered.add(_clean_path(destination))
    return out


def _build_networks(container: Mapping[str, Any], mode: str, container_id: str, api_version: tuple[int, int],
                    drivers: Mapping[str, str]) -> tuple[str, dict[str, tuple[str, dict[str, Any]]]]:
    """Gibt den Schluessel fuer `EndpointsConfig` beim `create` zurueck und je Netz `(NetworkID, EndpointConfig)`."""
    settings = _as_dict(container.get("NetworkSettings"), "NetworkSettings")
    networks = settings.get("Networks")
    if not isinstance(networks, dict) or not networks:
        raise Refusal(policy.NETWORK_UNCLEAR)
    wanted = "bridge" if mode in ("", "default") else mode
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    primary = []
    seen: set[str] = set()
    for key, endpoint in networks.items():
        if not isinstance(endpoint, dict):
            raise Refusal(policy.NETWORK_UNCLEAR)
        network_id = endpoint.get("NetworkID")
        if not policy.is_container_id(network_id) or network_id not in drivers or network_id in seen:
            raise Refusal(policy.NETWORK_UNCLEAR)
        seen.add(network_id)
        if drivers[network_id] in UNSUPPORTED_DRIVERS:
            raise Refusal(policy.MACVLAN)
        if key == wanted or network_id == wanted:
            primary.append(key)
        out[key] = (network_id, _build_endpoint(endpoint, key, container_id, api_version))
    if len(primary) != 1:
        raise Refusal(policy.NETWORK_UNCLEAR)
    create_key = "bridge" if mode in ("", "default") else mode
    if create_key != primary[0]:
        out[create_key] = out.pop(primary[0])
    return create_key, out


def _build_endpoint(endpoint: dict[str, Any], key: str, container_id: str,
                    api_version: tuple[int, int]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, value in endpoint.items():
        if field in ENDPOINT_DROP:
            continue
        if field not in ENDPOINT_COPY:
            if not is_default(value):
                raise _unknown(field)
            continue
        if field == "Aliases":
            aliases = [a for a in _str_list(value, "Aliases") if a not in (short_id(container_id), container_id)]
            if aliases and key != "bridge":
                out["Aliases"] = aliases
        elif field == "GwPriority":
            if not is_default(value):
                if type(value) is not int or api_version < GW_PRIORITY_API:
                    raise _unknown("GwPriority")
                out["GwPriority"] = value
        elif not is_default(value):
            if field != "IPAMConfig":
                out[field] = _copy(value)
            elif struct_norm(value) is not None:
                out[field] = _copy(struct_norm(value))
    return out


# ---------------------------------------------------------------------------
# Nachkontrolle
# ---------------------------------------------------------------------------


def verify(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    plan: Plan,
    *,
    new_image: Mapping[str, Any],
    new_image_id: str,
    channel: tuple[str, str | None] | None = None,
) -> None:
    """Haelt den neuen Container (Inspect nach `create` und `connect`) gegen den alten. Erlaubt sind nur die
    erwarteten Unterschiede: Image-ID, Werte aus dem neuen Image, `com.docker.compose.image`, Hostname (Kurz-ID)
    und die Kurz-IDs in den Aliases. Sonst `Refusal(clone_mismatch, <feld>)`. Dazu gehoert: der neue Container
    maskiert mindestens die Pfade des alten (`MaskedPaths`/`ReadonlyPaths`, nicht privilegierte Container), und jeder
    Port, den der alte erklaert und veroeffentlicht hat, ist auch beim neuen erklaert (`ExposedPorts`).

    `/app/data` und `/app/updater` muessen eingehaengt sein (beim alten und damit gleich beim neuen); mit `channel`
    (`(Type, Name bzw. Source)` des Kanals beim Helfer unter `/channel`, siehe `target.MountRef.origin`) muss
    `/app/updater` genau dieser Kanal sein."""
    def mismatch(field: str) -> Refusal:
        return Refusal(policy.CLONE_MISMATCH, _detail(field))

    if new.get("Image") != new_image_id:
        raise mismatch("Image")
    if new.get("Name") != old.get("Name"):
        raise mismatch("Name")
    if not same_mounts(old, new, image_config(new_image)):
        raise mismatch("Mounts")
    new_mounts = mount_map(new)
    if any(path not in mount_map(old) for path in REQUIRED_MOUNTS):
        raise mismatch("Mounts")
    if channel is not None and new_mounts[CHANNEL_PATH][:2] != tuple(channel):
        raise mismatch("Mounts")
    old_host = _as_dict(old.get("HostConfig"), "HostConfig")
    new_host = _as_dict(new.get("HostConfig"), "HostConfig")
    ids = {short_id(str(old.get("Id"))), str(old.get("Id")), short_id(str(new.get("Id"))), str(new.get("Id"))}
    if not same_networks(old, new, ids):
        raise mismatch("Networks")
    for field in HOST_COPY:
        if field == "Mounts":
            if struct_norm(new_host.get("Mounts")) != struct_norm(plan.body["HostConfig"].get("Mounts")):
                raise mismatch(field)
        elif norm(new_host.get(field)) != norm(old_host.get(field)):
            raise mismatch(field)
    if norm(new_host.get("Links")) != norm(old_host.get("Links")):
        raise mismatch("Links")
    if new_host.get("AutoRemove") is True:
        raise mismatch("AutoRemove")
    for field in set(old_host) | set(new_host):
        if field in HOST_COPY or field in HOST_DROP or field in HOST_PATHS or field in HOST_SPECIAL:
            continue
        if norm(new_host.get(field)) != norm(old_host.get(field)):
            raise mismatch(field)
    if old_host.get("Privileged") is not True:
        for field in HOST_PATHS:
            if not _paths_kept(old_host.get(field), new_host.get(field)):
                raise mismatch(field)
    old_config, new_config = _as_dict(old.get("Config"), "Config"), _as_dict(new.get("Config"), "Config")
    # Ein Port, den der alte erklaert **und** veroeffentlicht hat, muss auch beim neuen erklaert sein: der Daemon
    # (vor Version 29) veroeffentlicht ihn sonst nicht, und das Dashboard waere von aussen nicht mehr erreichbar.
    published = _map_keys(old_host.get("PortBindings")) & _map_keys(old_config.get("ExposedPorts"))
    if not published <= _map_keys(new_config.get("ExposedPorts")):
        raise mismatch("ExposedPorts")
    _verify_config(old_config, new_config, plan.body, image_config(new_image), mismatch)


def _paths_kept(old: Any, new: Any) -> bool:
    """`MaskedPaths`/`ReadonlyPaths`: der neue Container maskiert mindestens, was der alte maskiert.

    * alt eine Liste: neu eine Liste, die alle Eintraege des alten enthaelt (eine laengere Standardliste eines
      neueren Daemons ist in Ordnung). War sie beim alten ausdruecklich leer (`[]`, `systempaths=unconfined`), muss
      sie auch beim neuen genau `[]` sein: `null` hiesse beim neuen die Standardliste (stillschweigend strenger).
    * alt `null`: der Daemon setzt zur Laufzeit seine Standardliste (nicht "keine Masken"). Neu darf `null` oder eine
      nicht leere Liste sein, ein `[]` waere ein Klon ohne Masken.
    * alles andere als `null` oder Liste von Strings wird abgelehnt."""
    def as_list(value: Any) -> list[str] | None:
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return value
        return None

    if old is None:
        return new is None or bool(as_list(new))
    old_list, new_list = as_list(old), as_list(new)
    if old_list is None or new_list is None:
        return False
    if not old_list:
        return not new_list
    return set(old_list) <= set(new_list)


def _verify_config(old: dict[str, Any], new: dict[str, Any], body: dict[str, Any], nic: dict[str, Any],
                   mismatch: Any) -> None:
    if new.get("Image") != old.get("Image"):
        raise mismatch("Image")
    if "Hostname" in body and new.get("Hostname") != body["Hostname"]:
        raise mismatch("Hostname")
    if sorted(_str_list(new.get("Env"), "Env")) != sorted(merged_env(body.get("Env") or [],
                                                                      _str_list(nic.get("Env"), "Env"))):
        raise mismatch("Env")
    if _as_dict(new.get("Labels"), "Labels") != {**_as_dict(nic.get("Labels"), "Labels"), **(body.get("Labels") or {})}:
        raise mismatch("Labels")
    if _str_list(new.get("Entrypoint"), "Entrypoint") != _str_list(nic.get("Entrypoint"), "Entrypoint"):
        raise mismatch("Entrypoint")
    expected_cmd = body["Cmd"] if "Cmd" in body else _str_list(nic.get("Cmd"), "Cmd")
    if _str_list(new.get("Cmd"), "Cmd") != expected_cmd:
        raise mismatch("Cmd")
    for field in CONFIG_SUBTRACT:
        if (new.get(field) or "") != (body.get(field) or nic.get(field) or ""):
            raise mismatch(field)
    for field in ("ExposedPorts", "Volumes"):
        expected = set(_as_dict(nic.get(field), field)) | set(body.get(field) or {})
        if set(_as_dict(new.get(field), field)) != expected:
            raise mismatch(field)
    health = body.get("Healthcheck") or {}
    image_health = _as_dict(nic.get("Healthcheck"), "Healthcheck")
    expected_health = {field: health.get(field, image_health.get(field)) for field in HEALTH_FIELDS}
    new_health = _as_dict(new.get("Healthcheck"), "Healthcheck")
    if struct_norm({f: new_health.get(f) for f in HEALTH_FIELDS}) != struct_norm(expected_health):
        raise mismatch("Healthcheck")
    for field in CONFIG_COPY:
        if norm(new.get(field)) != norm(old.get(field)):
            raise mismatch(field)
    for field in set(old) | set(new):
        if field in CONFIG_SPECIAL or field in CONFIG_COPY or field in CONFIG_SUBTRACT or field in CONFIG_DROP:
            continue
        if norm(new.get(field)) != norm(old.get(field)):
            raise mismatch(field)
    if new.get("NetworkDisabled") is True:
        raise mismatch("NetworkDisabled")


def merged_env(user: list[str], image: list[str]) -> list[str]:
    """Wie der Docker-Dienst Env zusammenfuehrt: die Nutzereintraege, dazu die des Images, deren Schluessel der
    Nutzer nicht setzt."""
    keys = {entry.split("=", 1)[0] for entry in user}
    return list(user) + [entry for entry in image if entry.split("=", 1)[0] not in keys]


def mount_map(container: Mapping[str, Any]) -> dict[str, tuple[Any, ...]]:
    """`Destination -> (Type, Name bzw. Source, RW)` aus `.Mounts`."""
    out: dict[str, tuple[Any, ...]] = {}
    mounts = container.get("Mounts")
    for mount in mounts if isinstance(mounts, list) else []:
        if not isinstance(mount, dict):
            continue
        kind = mount.get("Type")
        origin = mount.get("Name") if kind == "volume" else mount.get("Source")
        out[_clean_path(str(mount.get("Destination")))] = (kind, origin, mount.get("RW"))
    return out


def same_mounts(old: Mapping[str, Any], new: Mapping[str, Any], nic: Mapping[str, Any]) -> bool:
    """Jeder Mount des alten Containers ist im neuen genau so da (Ziel -> Typ, Name bzw. Quelle, RW). Zusaetzlich
    erlaubt sind nur neue anonyme Volumes an Pfaden, die das **neue** Image als `VOLUME` erklaert (ein Wert aus dem
    neuen Image)."""
    old_map, new_map = mount_map(old), mount_map(new)
    image_volumes = {_clean_path(path) for path in _as_dict(nic.get("Volumes"), "Volumes")}
    for destination, origin in old_map.items():
        if new_map.get(destination) != origin:
            return False
    extra = set(new_map) - set(old_map)
    return all(destination in image_volumes and new_map[destination][0] == "volume" for destination in extra)


def network_map(container: Mapping[str, Any], ids: set[str]) -> dict[str, tuple[Any, ...]]:
    """`Netzname -> (NetworkID oder None, IPAMConfig, Aliases ohne die IDs)` aus `NetworkSettings.Networks`.

    Nach Name, nicht nach `NetworkID`: ein angelegter, noch nicht gestarteter Container meldet `NetworkID: ""` (die
    Engine loest das Netz erst beim Start auf; live geprueft mit Docker 29.3, API 1.43 und 1.54). Die ID wird
    verglichen, wo beide Seiten eine haben (`same_networks`)."""
    out: dict[str, tuple[Any, ...]] = {}
    settings = container.get("NetworkSettings")
    networks = settings.get("Networks") if isinstance(settings, dict) else None
    for name, endpoint in networks.items() if isinstance(networks, dict) else []:
        if not isinstance(endpoint, dict):
            continue
        aliases = endpoint.get("Aliases")
        cleaned = sorted({a for a in aliases if isinstance(a, str) and a not in ids}) if isinstance(aliases, list) else []
        network_id = endpoint.get("NetworkID")
        out[str(name)] = (network_id if policy.is_container_id(network_id) else None,
                          struct_norm(endpoint.get("IPAMConfig")), cleaned)
    return out


def same_networks(old: Mapping[str, Any], new: Mapping[str, Any], ids: set[str]) -> bool:
    """Gleiche Netze (nach Name), je Netz gleiche feste Adressen und Aliases; die `NetworkID` muss gleich sein,
    sobald beide Seiten eine melden."""
    old_map, new_map = network_map(old, ids), network_map(new, ids)
    if set(old_map) != set(new_map):
        return False
    for name, (old_id, old_ipam, old_aliases) in old_map.items():
        new_id, new_ipam, new_aliases = new_map[name]
        if (old_ipam, old_aliases) != (new_ipam, new_aliases):
            return False
        if old_id is not None and new_id is not None and old_id != new_id:
            return False
    return True
