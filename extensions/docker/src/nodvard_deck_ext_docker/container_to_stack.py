"""Einen einzelnen Container (ohne Compose gestartet) in einen Stack umwandeln: Aus `docker inspect` entsteht eine
Compose-Datei, nur aus bekannten Feldern. Was Deck nicht sicher übersetzen kann, nennt es, statt es still wegzulassen."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Actor, Risk

from .adopt import AdoptedStacks
from .applier import RISK_ORDER
from .capabilities import is_own_container
from .commands import CONTAINER_NAME_RE, VOLUME_NAME_RE
from .editor import StackEditor, risky_settings, sha256
from .image_apply import is_helper_labels, is_self_image
from .image_updates import is_valid_ref
from .new_stack import (
    DockerStackCreateExecutor,
    NewStacks,
    _leftover,
    _sentence,
    _undo,
    new_stack,
)
from .stack_actions import (
    FAILURE_LIMIT,
    DockerStackExecutor,
    _connection_error,
    _lost,
    busy_text,
    cmd_verb,
    failure_line,
)
from .stacks import DockerStacks

CONVERT_ACTION = "docker.container_to_stack"
ENV_NAME = "container.env"
OLD_SUFFIX = "-vor-stack"
# So lehnt Docker ein Umbenennen auf den Namen ab, den der Container schon trägt.
SAME_NAME = "same name as its current name"
READ_TIMEOUT_S = 60
STOP_TIMEOUT_S = 180
DAEMON_FORMAT = "{{.CgroupVersion}} {{.LoggingDriver}} {{.DefaultRuntime}}"
IMAGE_FORMAT = '{"Id":{{json .Id}},"Config":{{json .Config}}}'
_ID_RE = re.compile(r"^[0-9a-f]{64}\Z")
DEFAULT_SHM = 64 * 1024 * 1024
_DURATION_FIELDS = ("Interval", "Timeout", "StartPeriod", "StartInterval")
# In einfachen Anführungszeichen liest Compose den Wert roh; nur „'“, Zeilenumbrüche und ein „\\“ vor dem schließenden „'“ stören.
_SAFE_ENV_VALUE = re.compile(r"^[^'\n\r]*(?<!\\)\Z")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*\Z")
ENV_CHANGED = (
    "Compose liest die Variablen aus container.env anders, als der Container sie hat. Damit der Dienst nicht mit "
    "verfälschten Werten startet, wandelt Deck ihn nicht um."
)

# Felder aus `HostConfig`, die Deck in die Compose-Datei übersetzt, dazu die Schlüssel dort.
_HOST_DIRECT = {
    "CapAdd": "cap_add", "CapDrop": "cap_drop", "SecurityOpt": "security_opt", "GroupAdd": "group_add", "Sysctls": "sysctls",
    "ExtraHosts": "extra_hosts", "Dns": "dns", "DnsSearch": "dns_search", "DnsOptions": "dns_opt", "OomScoreAdj": "oom_score_adj",
    "PidsLimit": "pids_limit", "MemoryReservation": "mem_reservation", "CpuShares": "cpu_shares", "CpusetCpus": "cpuset",
    "OomKillDisable": "oom_kill_disable", "MemorySwappiness": "mem_swappiness",
}
_HOST_HANDLED = {
    *_HOST_DIRECT, "Binds", "Mounts", "PortBindings", "PublishAllPorts", "NetworkMode", "RestartPolicy", "LogConfig", "ConsoleSize",
    "CgroupnsMode", "IpcMode", "ShmSize", "Runtime", "MaskedPaths", "ReadonlyPaths", "Privileged", "ReadonlyRootfs", "Init",
    "Memory", "MemorySwap", "NanoCpus", "Tmpfs", "Devices", "Ulimits", "PidMode", "UTSMode", "UsernsMode", "ContainerIDFile",
    "AutoRemove", "Links", "VolumesFrom", "VolumeDriver",
}
_CONFIG_HANDLED = {
    "Hostname", "Domainname", "User", "AttachStdin", "AttachStdout", "AttachStderr", "Tty", "OpenStdin", "StdinOnce", "Env", "Cmd",
    "Entrypoint", "Image", "Volumes", "WorkingDir", "Labels", "ExposedPorts", "Healthcheck", "StopSignal", "StopTimeout",
    "OnBuild", "Shell", "ArgsEscaped", "MacAddress", "NetworkDisabled",
}


@dataclass
class Converted:
    project: str
    service: str
    compose: str = ""
    env: str | None = None
    """Inhalt der `container.env`; die Werte gehen nie an den Browser."""
    env_names: list[str] = field(default_factory=list)
    env_values: dict[str, str] = field(default_factory=dict, repr=False)
    """Was Compose aus `container.env` lesen muss; nur für die Gegenprobe im Ordner."""
    refused: list[str] = field(default_factory=list)


class _Dumper(yaml.SafeDumper):
    """Text mit „:“ oder wie eine Zahl in Anführungszeichen, damit Compose genau den Text liest (Ports, `1000:1000`)."""

    def represent_str(self, data: str) -> yaml.ScalarNode:
        # Alles, was mit Ziffer, Vorzeichen oder Punkt beginnt, könnte YAML als Zahl lesen (`1e3`, `0o17`, `.5`).
        quote = ":" in data or data[:1] in "0123456789+-." or data.lower() in ("yes", "no", "on", "off", "true", "false", "null", "~")
        return self.represent_scalar("tag:yaml.org,2002:str", data, style='"' if quote else None)


_Dumper.add_representer(str, _Dumper.represent_str)


def _literal(value: Any) -> Any:
    """Jedes `$` in Werten als `$$`: Sonst setzte Compose beim Lesen eigene Variablen ein, etwa in `command`."""
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [_literal(v) for v in value]
    if isinstance(value, dict):
        return {k: _literal(v) for k, v in value.items()}
    return value


def is_default(value: Any) -> bool:
    return value is None or value is False or value == 0 or value == "" or value == [] or value == {}


def stack_name(container: str) -> str:
    """Ein gültiger Projektname aus dem Namen des Containers: klein, nur Buchstaben, Ziffern, „-“ und „_“."""
    name = re.sub(r"[^a-z0-9_-]+", "-", container.lower()).strip("-_")[:63].strip("-_")
    return name or "stack"


def _duration(nanos: int) -> str:
    millis = nanos // 1_000_000
    return f"{millis // 1000}s" if millis % 1000 == 0 else f"{millis}ms"


def _ports(bindings: Any, refused: list[str]) -> list[str]:
    ports = []
    for key, entries in sorted((bindings or {}).items()):
        number, _, proto = key.partition("/")
        suffix = "" if proto in ("", "tcp") else f"/{proto}"
        for entry in entries or [{}]:
            host_ip, host_port = (entry or {}).get("HostIp") or "", (entry or {}).get("HostPort") or ""
            if host_ip in ("0.0.0.0", "::"):
                host_ip = ""
            if ":" in host_ip:
                refused.append(f"Der Port {key} ist an die IPv6-Adresse {host_ip} gebunden.")
                continue
            if host_port:
                ports.append(":".join(p for p in (host_ip, host_port, number) if p) + suffix)
            else:
                # Ohne festen Port am Server bleibt die Adresse: `127.0.0.1::8080` heißt weiter „nur lokal“.
                ports.append(f"{host_ip}::{number}{suffix}" if host_ip else number + suffix)
    return ports


def _mounts(mounts: Any, host_mounts: Any, refused: list[str]) -> tuple[list[str], list[str], dict[str, Any]]:
    """(volumes-Einträge, tmpfs-Einträge, oberste `volumes`) aus `.Mounts`; jedes Volume bleibt das alte (`external`)."""
    entries, tmpfs, top = [], [], {}
    options = {m.get("Target"): m for m in host_mounts or [] if isinstance(m, dict)}
    for mount in mounts or []:
        kind, target = mount.get("Type"), mount.get("Destination")
        extra = options.get(target) or {}
        if any(not is_default(extra.get(k)) for k in ("VolumeOptions", "BindOptions", "TmpfsOptions", "ImageOptions")):
            refused.append(f"Der Mount {target} hat eigene Optionen (--mount …).")
            continue
        if kind == "volume":
            name = str(mount.get("Name") or "")
            if not VOLUME_NAME_RE.match(name) or mount.get("Driver") not in (None, "", "local"):
                refused.append(f"Das Volume unter {target} lässt sich nicht übernehmen ({name or 'ohne Namen'}, Treiber {mount.get('Driver')}).")
                continue
            modes = {m for m in str(mount.get("Mode") or "").split(",") if m}
            if modes - {"z", "rw", "ro", "nocopy"}:
                refused.append(f"Das Volume unter {target} wird mit „{mount.get('Mode')}“ eingebunden; das übernimmt Deck nicht.")
                continue
            flags = ",".join(f for f in ("" if mount.get("RW", True) else "ro", "nocopy" if "nocopy" in modes else "") if f)
            entries.append(f"{name}:{target}" + (f":{flags}" if flags else ""))
            top[name] = {"external": True}
        elif kind == "bind":
            if ":" in str(mount.get("Source") or "") or ":" in str(target or ""):
                refused.append(f"Der Ordner unter {target} hat einen Doppelpunkt im Pfad.")
                continue
            if mount.get("Propagation") not in (None, "", "rprivate"):
                refused.append(f"Der Ordner unter {target} wird mit „{mount.get('Propagation')}“ eingebunden.")
                continue
            label = next((m for m in ("z", "Z") if m in str(mount.get("Mode") or "").split(",")), "")
            flags = ",".join(f for f in ("" if mount.get("RW", True) else "ro", label) if f)
            entries.append(f"{mount.get('Source')}:{target}" + (f":{flags}" if flags else ""))
        elif kind == "tmpfs":
            tmpfs.append(str(target))
        else:
            refused.append(f"Der Mount {target} ist vom Typ {kind}.")
    return entries, tmpfs, top


def _networks(inspect: dict[str, Any], refused: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(Angaben im Dienst, oberste `networks`)."""
    mode = str(inspect.get("HostConfig", {}).get("NetworkMode") or "default")
    attached = (inspect.get("NetworkSettings") or {}).get("Networks") or {}
    if mode in ("default", "bridge", "host", "none"):
        if set(attached) - {"bridge", "host", "none"}:
            refused.append(f"Der Container hängt zusätzlich an weiteren Netzen ({', '.join(sorted(set(attached) - {'bridge'}))}).")
        return {"network_mode": "bridge" if mode == "default" else mode}, {}
    if mode.startswith("container:"):
        refused.append("Der Container teilt das Netz eines anderen Containers (network_mode: container).")
        return {}, {}
    short = str(inspect.get("Id") or "")[:12]
    service: dict[str, Any] = {}
    for name, endpoint in sorted(attached.items()):
        endpoint = endpoint or {}
        entry: dict[str, Any] = {}
        aliases = [a for a in endpoint.get("Aliases") or [] if a != short]
        if aliases:
            entry["aliases"] = aliases
        ipam = endpoint.get("IPAMConfig") or {}
        if ipam.get("IPv4Address"):
            entry["ipv4_address"] = ipam["IPv4Address"]
        if ipam.get("IPv6Address"):
            entry["ipv6_address"] = ipam["IPv6Address"]
        if ipam.get("LinkLocalIPs"):
            refused.append(f"Am Netz {name} hat der Container eigene Link-Local-Adressen.")
        if endpoint.get("Links") or not is_default(endpoint.get("DriverOpts")):
            refused.append(f"Am Netz {name} hängen Links oder eigene Treiber-Optionen.")
        service[name] = entry or None
    return {"networks": service}, {name: {"external": True} for name in service}


def _env(container_env: list[str], image_env: list[str], refused: list[str]) -> tuple[str | None, dict[str, str]]:
    own = [line for line in container_env or [] if line not in set(image_env or [])]
    lines, values = [], {}
    for line in own:
        name, _, value = line.partition("=")
        if not _ENV_NAME_RE.match(name):
            refused.append("Eine Variable hat einen Namen, den Compose in einer Env-Datei nicht annimmt (erlaubt sind Buchstaben, "
                           "Ziffern, „_“, „.“ und „-“).")
            continue
        if not _SAFE_ENV_VALUE.match(value):
            refused.append(f"Die Variable {name} hat einen Wert, den eine Env-Datei nicht sicher fasst (Zeilenumbruch, „'“ oder „\\“ am Ende).")
            continue
        lines.append(f"{name}='{value}'")
        values[name] = value
    return ("\n".join(lines) + "\n" if lines else None), values


def convert(inspect: dict[str, Any], image: dict[str, Any], *, cgroup_v2: bool, log_driver: str, runtime: str) -> Converted:
    """Die Compose-Datei für diesen Container; `image` ist die Config seines Images, die Angaben des Dienstes zum Vergleich."""
    name = str(inspect.get("Name") or "").lstrip("/")
    out = Converted(project=stack_name(name), service=stack_name(name))
    refused = out.refused
    config, host = inspect.get("Config") or {}, inspect.get("HostConfig") or {}
    labels = config.get("Labels") or {}
    if any(key.startswith("com.docker.compose.") for key in labels):
        refused.append("Der Container gehört schon zu einem Compose-Projekt.")
    for key, value in host.items():
        if key not in _HOST_HANDLED and not is_default(value):
            refused.append(f"Die Einstellung {key} übersetzt Deck noch nicht.")
    for key, value in config.items():
        if key not in _CONFIG_HANDLED and not is_default(value):
            refused.append(f"Die Einstellung {key} übersetzt Deck noch nicht.")
    for key, why in (("AutoRemove", "Der Container löscht sich beim Stoppen selbst (--rm)."), ("Links", "Der Container nutzt alte Links (--link)."),
                     ("VolumesFrom", "Der Container übernimmt Volumes eines anderen (--volumes-from)."), ("PublishAllPorts", "Alle Ports werden zufällig veröffentlicht (-P)."),
                     ("VolumeDriver", "Der Container nutzt einen eigenen Volume-Treiber.")):
        if not is_default(host.get(key)):
            refused.append(why)
    if config.get("NetworkDisabled"):
        refused.append("Das Netz des Containers ist abgeschaltet.")
    if config.get("MacAddress"):
        refused.append("Der Container hat eine feste MAC-Adresse.")
    if any(str(opt).startswith("seccomp={") or str(opt).startswith("seccomp:{") for opt in host.get("SecurityOpt") or []):
        refused.append("Der Container hat ein eigenes Seccomp-Profil; Compose erwartet dafür eine Datei.")
    image_ref = str(config.get("Image") or "")
    if not image_ref or image_ref.startswith("sha256:"):
        refused.append("Der Container wurde über die ID seines Images gestartet, nicht über einen Namen.")

    service: dict[str, Any] = {"image": image_ref, "container_name": name}
    restart = host.get("RestartPolicy") or {}
    if restart.get("Name") == "on-failure":
        service["restart"] = f"on-failure:{restart['MaximumRetryCount']}" if restart.get("MaximumRetryCount") else "on-failure"
    elif restart.get("Name") in ("always", "unless-stopped"):
        service["restart"] = restart["Name"]
    # Mit `uts: host` ist der Rechnername der des Servers; Compose lehnt `hostname` dann ab.
    if config.get("Hostname") and config["Hostname"] != str(inspect.get("Id") or "")[:12] and host.get("UTSMode") != "host":
        service["hostname"] = config["Hostname"]
    for key, target in (("Domainname", "domainname"), ("Tty", "tty"), ("OpenStdin", "stdin_open")):
        if config.get(key):
            service[target] = config[key]
    for key, target in (("User", "user"), ("WorkingDir", "working_dir")):
        if (config.get(key) or "") != (image.get(key) or ""):
            service[target] = config.get(key) or ""
    entrypoint_changed = (config.get("Entrypoint") or None) != (image.get("Entrypoint") or None)
    if entrypoint_changed:
        if not config.get("Entrypoint"):
            refused.append("Der Startbefehl des Images ist abgeschaltet (--entrypoint \"\").")
        service["entrypoint"] = config.get("Entrypoint")
    if config.get("Cmd") and (entrypoint_changed or config.get("Cmd") != image.get("Cmd")):
        service["command"] = config["Cmd"]
    env, out.env_values = _env(config.get("Env") or [], image.get("Env") or [], refused)
    out.env_names = list(out.env_values)
    if env:
        out.env = env
        service["env_file"] = [ENV_NAME]
    own_labels = {k: v for k, v in labels.items() if (image.get("Labels") or {}).get(k) != v}
    if own_labels:
        service["labels"] = own_labels
    if (config.get("StopSignal") or "SIGTERM") != (image.get("StopSignal") or "SIGTERM"):
        service["stop_signal"] = config["StopSignal"]
    if config.get("StopTimeout") is not None:
        service["stop_grace_period"] = f"{config['StopTimeout']}s"
    health = config.get("Healthcheck")
    if health and health != image.get("Healthcheck"):
        if health.get("Test") == ["NONE"]:
            service["healthcheck"] = {"disable": True}
        else:
            check: dict[str, Any] = {"test": health.get("Test")}
            check.update({k.lower().replace("startperiod", "start_period").replace("startinterval", "start_interval"): _duration(health[k])
                          for k in _DURATION_FIELDS if health.get(k)})
            if health.get("Retries"):
                check["retries"] = health["Retries"]
            service["healthcheck"] = check

    published = {key for key in (host.get("PortBindings") or {})}
    if ports := _ports(host.get("PortBindings"), refused):
        service["ports"] = ports
    expose = sorted(set(config.get("ExposedPorts") or {}) - set(image.get("ExposedPorts") or {}) - published)
    if expose:
        service["expose"] = [p.removesuffix("/tcp") for p in expose]
    volumes, tmpfs, top_volumes = _mounts(inspect.get("Mounts"), host.get("Mounts"), refused)
    if volumes:
        service["volumes"] = volumes
    tmpfs_options = host.get("Tmpfs") or {}
    tmpfs = sorted({*tmpfs, *tmpfs_options})
    if tmpfs:
        service["tmpfs"] = [f"{path}:{tmpfs_options[path]}" if tmpfs_options.get(path) else path for path in tmpfs]
    network, top_networks = _networks(inspect, refused)
    service.update(network)

    log = host.get("LogConfig") or {}
    if log.get("Type") and (log["Type"] != log_driver or log.get("Config")):
        service["logging"] = {"driver": log["Type"], **({"options": log["Config"]} if log.get("Config") else {})}
    if host.get("CgroupnsMode") and host["CgroupnsMode"] != ("private" if cgroup_v2 else "host"):
        service["cgroup"] = host["CgroupnsMode"]
    ipc = host.get("IpcMode") or ""
    if ipc == "host":
        service["ipc"] = "host"
    elif ipc not in ("", "private", "shareable"):
        refused.append(f"Der Container teilt den Speicherbereich (IpcMode {ipc}).")
    if host.get("ShmSize") and host["ShmSize"] != DEFAULT_SHM:
        service["shm_size"] = host["ShmSize"]
    if host.get("Runtime") and host["Runtime"] != runtime:
        service["runtime"] = host["Runtime"]
    if not host.get("Privileged") and (not host.get("MaskedPaths") or not host.get("ReadonlyPaths")):
        refused.append("Der Schutz der Systempfade ist abgeschaltet (systempaths=unconfined).")
    for key, target in (("Privileged", "privileged"), ("ReadonlyRootfs", "read_only"), ("Init", "init")):
        if host.get(key):
            service[target] = True
    for key, target in _HOST_DIRECT.items():
        if not is_default(host.get(key)):
            service[target] = host[key]
    if host.get("Memory"):
        service["mem_limit"] = host["Memory"]
        if host.get("MemorySwap") not in (None, 0, 2 * host["Memory"]):
            service["memswap_limit"] = host["MemorySwap"]
    elif host.get("MemorySwap") not in (None, 0):
        service["memswap_limit"] = host["MemorySwap"]
    if host.get("NanoCpus"):
        service["cpus"] = host["NanoCpus"] / 1e9
    if host.get("Devices"):
        service["devices"] = [f"{d['PathOnHost']}:{d['PathInContainer']}:{d.get('CgroupPermissions') or 'rwm'}" for d in host["Devices"]]
    if host.get("Ulimits"):
        service["ulimits"] = {u["Name"]: {"soft": u["Soft"], "hard": u["Hard"]} for u in host["Ulimits"]}
    for key, target in (("PidMode", "pid"), ("UTSMode", "uts"), ("UsernsMode", "userns_mode")):
        value = host.get(key) or ""
        if value == "host":
            service[target] = "host"
        elif value:
            refused.append(f"Die Einstellung {key} ({value}) übersetzt Deck noch nicht.")

    document: dict[str, Any] = {"services": {out.service: _literal(service)}}
    if top_volumes:
        document["volumes"] = top_volumes
    if top_networks:
        document["networks"] = top_networks
    out.compose = yaml.dump(document, Dumper=_Dumper, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000)
    return out


def convert_action_spec() -> ActionSpec:
    return ActionSpec(
        action_type=CONVERT_ACTION, label="Container in einen Stack umwandeln", icon="layers", default_risk=Risk.HIGH,
        description="Schreibt für einen ohne Compose gestarteten Container eine Compose-Datei in einen eigenen Ordner und startet "
        "ihn daraus neu; die Volumes und Ordner bleiben dieselben.",
        permissions=["hosts.execute"],
        host_bound=False,
        confirm_text="Der Container wird gestoppt und aus der neuen Compose-Datei neu erstellt. Klappt das, löscht Deck den alten "
        "Container; was nur in ihm lag und nicht in einem Volume oder Ordner, ist dann weg. Klappt es nicht, startet der alte wieder. "
        "Fortfahren?",
    )


def _container(target: str) -> str:
    if not (_ID_RE.match(target) or CONTAINER_NAME_RE.match(target)):
        raise ValueError(f"Ungültiger Container: {target!r}")
    return target


def cmd_inspect(target: str) -> list[str]:
    return ["docker", "container", "inspect", _container(target)]


def cmd_image_config(ref: str) -> list[str]:
    """ID und Config des Images, auf das der Name jetzt zeigt."""
    if not is_valid_ref(ref):
        raise ValueError(f"Ungültiger Image-Name: {ref!r}")
    return ["docker", "image", "inspect", "--format", IMAGE_FORMAT, ref]


def cmd_daemon() -> list[str]:
    return ["docker", "info", "--format", DAEMON_FORMAT]


def cmd_stop_old(container_id: str, seconds: int) -> list[str]:
    if not _ID_RE.match(container_id) or not 0 <= seconds <= 3600:
        raise ValueError(f"Ungültiges Stoppen: {container_id!r}")
    return ["docker", "stop", "-t", str(seconds), container_id]


def cmd_rename_old(container_id: str, name: str) -> list[str]:
    if not _ID_RE.match(container_id) or not CONTAINER_NAME_RE.match(name):
        raise ValueError(f"Ungültiges Umbenennen: {container_id!r} -> {name!r}")
    return ["docker", "rename", container_id, name]


def cmd_start_old(container_id: str) -> list[str]:
    return ["docker", "start", _container(container_id)]


def cmd_remove_old(container_id: str) -> list[str]:
    """Ohne `-v`: Die Volumes des alten Containers nutzt der neue weiter."""
    if not _ID_RE.match(container_id):
        raise ValueError(f"Ungültiger Container: {container_id!r}")
    return ["docker", "rm", container_id]


@dataclass
class Facts:
    inspect: dict[str, Any]
    converted: Converted


class ContainerToStack:
    """Liest einen Container und schlägt die Compose-Datei vor; umgewandelt wird nur im Ausführer."""

    def __init__(self, ctx: Any, new: NewStacks, adopted: AdoptedStacks, stacks: DockerStacks) -> None:
        self._ctx = ctx
        self.new = new
        self._adopted = adopted
        self.stacks = stacks

    async def _exec(self, host: Any, argv: list[str], what: str) -> Any:
        try:
            return await self._ctx.exec.run_argv(host, argv, timeout_s=READ_TIMEOUT_S)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            raise LookupError(f"{what} ließ sich nicht lesen: {_lost(exc)}") from exc

    @staticmethod
    def _json(result: Any, what: str) -> Any:
        if result.exit_code != 0:
            raise LookupError(f"{what} ließ sich nicht lesen: {failure_line(result.stderr or result.stdout)}")
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise LookupError(f"{what} ließ sich nicht lesen.") from exc

    async def facts(self, host: Any, container: str) -> Facts:
        """Inspect und Übersetzung; `LookupError` mit einem Satz für Menschen, wenn etwas fehlt."""
        found = self._json(await self._exec(host, cmd_inspect(container), "Der Container"), "Der Container")
        inspect = found[0] if isinstance(found, list) and found and isinstance(found[0], dict) else None
        if inspect is None:
            raise LookupError("Den Container gibt es nicht mehr.")
        config = inspect.get("Config") or {}
        image_ref = str(config.get("Image") or "")
        what = "Das Image des Containers"
        image = self._json(await self._exec(host, cmd_image_config(image_ref), what), what) if is_valid_ref(image_ref) else {}
        image = image if isinstance(image, dict) else {}
        daemon = (await self._exec(host, cmd_daemon(), "Docker")).stdout.split()
        if len(daemon) != 3:
            raise LookupError("Docker ließ sich nicht befragen.")
        image_config = image.get("Config") if isinstance(image.get("Config"), dict) else {}
        converted = convert(inspect, image_config, cgroup_v2=daemon[0] == "2", log_driver=daemon[1], runtime=daemon[2])
        state = inspect.get("State") or {}
        if is_own_container(str(inspect.get("Id") or "")) or is_self_image(image_ref):
            converted.refused.insert(0, "Das ist Nodvard Deck selbst.")
        elif is_helper_labels(config.get("Labels")):
            converted.refused.insert(0, "Das ist der Update-Helfer von Nodvard Deck.")
        if str(inspect.get("Name") or "").lstrip("/") != container:
            converted.refused.append("Gib den Namen des Containers an, nicht seine ID.")
        elif not CONTAINER_NAME_RE.match(container + OLD_SUFFIX):
            converted.refused.append("Der Name des Containers ist zu lang, um ihn für den Rückweg umzubenennen.")
        if image and image.get("Id") != inspect.get("Image"):
            # Sonst liefe der Dienst nach dem Umwandeln still mit einem anderen Image, etwa nach einem `docker pull`.
            converted.refused.append(f"Der Name {image_ref} zeigt inzwischen auf ein anderes Image als das, mit dem der Container "
                                     "läuft. Wandle ihn um, wenn er mit dem aktuellen Image läuft.")
        if not state.get("Running"):
            converted.refused.append("Der Container läuft nicht. Starte ihn zuerst, damit sich danach prüfen lässt, ob er wieder läuft.")
        return Facts(inspect, converted)

    async def plan(self, host: Any, container: str) -> dict[str, Any]:
        """Was entstünde: Ordner, Compose-Datei (ohne Werte der Variablen), Namen der Variablen, Gründe dagegen, Warnungen."""
        try:
            facts = await self.facts(host, container)
        except LookupError as exc:
            return {"container": container, "id": None, "project": None, "folder": None, "compose": None, "env_names": [],
                    "refused": [str(exc)], "warnings": []}
        converted = facts.converted
        root = await self.new.root(host.id)
        refused = list(converted.refused)
        if not refused and (conflict := await self.new.conflict(host, converted.project, root)) is not None:
            refused.append(conflict)
        return {
            "container": container, "id": facts.inspect.get("Id"), "project": converted.project,
            "folder": new_stack(converted.project, root).working_dir, "compose": converted.compose, "env_names": converted.env_names,
            "refused": refused, "warnings": risky_settings(_long_binds(yaml.safe_load(converted.compose) or {})),
        }


def _long_binds(document: dict[str, Any]) -> dict[str, Any]:
    """Ordner in der langen Schreibweise, wie `risky_settings` sie aus `compose config` kennt."""
    for service in (document.get("services") or {}).values():
        volumes = service.get("volumes") if isinstance(service, dict) else None
        if not volumes:
            continue
        service["volumes"] = [
            {"type": "bind", "source": parts[0], "target": parts[1], "read_only": "ro" in (parts[2].split(",") if len(parts) > 2 else [])}
            if isinstance(entry, str) and entry.startswith("/") and len(parts := entry.split(":")) >= 2 else entry
            for entry in volumes
        ]
    return document


class _Unclear(str):
    """Ein Grund ohne Antwort vom Server: Ob der Befehl dort lief, ist offen."""


class DockerContainerToStackExecutor:
    """Legt Ordner und Dateien an, stoppt und benennt den alten Container um, startet den Stack und prüft; sonst zurück."""

    action_types = frozenset({CONVERT_ACTION})

    def __init__(self, ctx: Any, convert: ContainerToStack, create: DockerStackCreateExecutor, adopted: AdoptedStacks,
                 actions: DockerStackExecutor) -> None:
        self._ctx = ctx
        self._convert = convert
        self._create = create
        self._adopted = adopted
        self._actions = actions

    async def execute(self, req: ActionRequest) -> ActionResult:
        payload = req.payload or {}
        container = str(payload.get("container") or "")
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return ActionResult(success=False, error="Einen Container wandelt nur ein Mensch über die Docker-Seite um.")
        if RISK_ORDER.index(req.risk) < RISK_ORDER.index(Risk.HIGH):
            return ActionResult(success=False, error="Das Umwandeln braucht eine Freigabe mit hohem Risiko.")
        if not CONTAINER_NAME_RE.match(container):
            return ActionResult(success=False, error=f"Ungültiger Container: {container!r}")
        host = await self._ctx.hosts.get(req.host_ref) if req.host_ref else None
        if host is None:
            return ActionResult(success=False, error="Server nicht gefunden.")
        if ((await self._ctx.settings.get()).get("docker_host_tag") or "docker") not in host.tags:
            return ActionResult(success=False, error="Kein Docker-Host.")
        project = stack_name(container)
        with self._actions.locks.hold(host.id, project, "Umwandeln in einen Stack") as busy:
            if busy is not None:
                return ActionResult(success=False, error=busy_text(busy))
            return await self._run(req, host, container, payload)

    async def _exec(self, host: Any, argv: list[str], timeout_s: int, converted: Converted | None = None) -> str | None:
        try:
            result = await self._ctx.exec.run_argv(host, argv, timeout_s=timeout_s)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            return _Unclear(_lost(exc))
        if result.exit_code == 0:
            return None
        output = result.stderr or result.stdout
        if converted is None:
            return failure_line(output)
        return _scrub(failure_line(output, limit=None), converted)[:FAILURE_LIMIT]

    async def _run(self, req: ActionRequest, host: Any, container: str, payload: dict[str, Any]) -> ActionResult:
        started = time.monotonic()
        plan = await self._convert.plan(host, container)
        if plan["refused"]:
            return ActionResult(success=False, error=" ".join(_sentence(r) for r in plan["refused"]) + " Geändert wurde nichts.")
        compose = sha256((plan["compose"] or "").encode())
        if (plan["id"], plan["folder"], compose) != (payload.get("id"), payload.get("folder"), payload.get("compose_sha256")):
            return ActionResult(success=False, error="Der Container oder der Ordner hat sich inzwischen geändert. Bitte noch einmal umwandeln.")
        try:
            facts = await self._convert.facts(host, container)
        except LookupError as exc:
            return ActionResult(success=False, error=f"{_sentence(str(exc))} Geändert wurde nichts.")
        converted, old_id = facts.converted, str(plan["id"])
        if converted.refused or facts.inspect.get("Id") != plan["id"] or sha256(converted.compose.encode()) != compose:
            return ActionResult(success=False, error="Der Container oder der Ordner hat sich inzwischen geändert. Bitte noch einmal umwandeln.")
        root = await self._convert.new.root(host.id)
        target = new_stack(converted.project, root)
        env_path = f"{target.working_dir}/{ENV_NAME}"
        extra = ((env_path, converted.env.encode()),) if converted.env else ()
        created: list[tuple[str, str]] = []
        async with self._ctx.exec.sftp(host) as sftp:
            try:
                problem = await self._create.lay_out(req, sftp, host, target, root, converted.compose.encode(), created, extra,
                                                     vet=lambda config: _env_vet(config, converted))
                if problem is not None:
                    problem = _scrub(problem, converted)
            except BaseException:
                await asyncio.shield(_undo(sftp, created))
                raise
            if problem is not None:
                left = await asyncio.shield(_undo(sftp, created))
                return ActionResult(success=False, error=_sentence(problem) + _leftover(left) + " Am Container hat sich nichts geändert.")
        # Ab hier ist der alte Container angefasst: Ein Abbruch (Neustart von Deck, Notbremse) führt erst zurück.
        step = "stop"
        try:
            timeout = int((facts.inspect.get("Config") or {}).get("StopTimeout") or 10)
            if (problem := await self._exec(host, cmd_stop_old(old_id, min(timeout, 3600)), STOP_TIMEOUT_S + timeout)) is not None:
                back = await self._resume(host, old_id, container, renamed=False, word="weiter")
                return await self._undo_files(host, created, f"Der Container ließ sich nicht stoppen: {problem}. {back}")
            if (problem := await self._exec(host, cmd_rename_old(old_id, f"{container}{OLD_SUFFIX}"), READ_TIMEOUT_S)) is not None:
                back = await self._resume(host, old_id, container, renamed=isinstance(problem, _Unclear), word="wieder")
                return await self._undo_files(host, created, f"Der Container ließ sich nicht umbenennen: {problem}. {back}")
            step = "up"
            verdict = None
            problem = await self._exec(host, cmd_verb(target, "docker.stack_up"), STOP_TIMEOUT_S * 5, converted)
            if problem is None:
                verdict = await self._check(host, target)
            if problem is not None or verdict is None or not verdict.ok:
                reason = problem or (verdict.text if verdict else "Der neue Container ließ sich nicht prüfen.")
                step = "back"
                return await asyncio.shield(self._give_up(host, target, old_id, container, created, reason, converted))
            step = "done"
        except BaseException:
            if step in ("stop", "up"):
                await asyncio.shield(self._abandon(host, target, old_id, container, created, up=step == "up", converted=converted))
            raise
        removed = await self._exec(host, cmd_remove_old(old_id), STOP_TIMEOUT_S)
        note = "" if removed is None else f" Der alte, gestoppte Container „{container}{OLD_SUFFIX}“ ließ sich nicht löschen ({removed}); lösche ihn im Reiter Container."
        try:
            await self._adopted.add(host.id, target)
        except Exception as exc:  # noqa: BLE001 - der Stack läuft, nur das Merken fehlt
            return ActionResult(success=False, exit_code=0, output=f"Stack „{target.project}“ läuft aus {target.working_dir}.", error=(
                f"Nodvard Deck konnte sich den Stack nicht merken ({exc}). Übernimm ihn im Reiter Stacks.{note}"
            ))
        return ActionResult(success=True, exit_code=0, duration_ms=int((time.monotonic() - started) * 1000), output=(
            f"„{container}“ läuft jetzt als Stack „{target.project}“ aus {target.working_dir}. {verdict.text}{note}"
        ))

    async def _give_up(
        self, host: Any, target: Any, old_id: str, name: str, created: list[tuple[str, str]], reason: str, converted: Converted,
    ) -> ActionResult:
        back, down_ok = await self._back(host, target, old_id, name, renamed=True, converted=converted)
        return await self._undo_files(host, created, f"Der Stack ist nicht sauber angelaufen: {_sentence(reason)} {back}", keep=not down_ok)

    async def _resume(self, host: Any, old_id: str, name: str, *, renamed: bool, word: str) -> str:
        """Startet den alten Container wieder; nach einem Umbenennen ohne Antwort gibt es ihm vorher seinen Namen zurück."""
        problems = []
        if renamed and (named := await self._exec(host, cmd_rename_old(old_id, name), READ_TIMEOUT_S)) is not None and SAME_NAME not in named:
            problems.append(named)
        if (started := await self._exec(host, cmd_start_old(old_id), STOP_TIMEOUT_S)) is not None:
            problems.append(started)
        if not problems:
            return f"Er läuft {word} wie bisher."
        also = f"; er kann auch „{name}{OLD_SUFFIX}“ heißen" if renamed else ""
        return f"Ob er {word} wie bisher läuft, ließ sich nicht feststellen ({'; '.join(dict.fromkeys(problems))}). Sieh im Reiter Container nach{also}."

    async def _abandon(
        self, host: Any, target: Any, old_id: str, name: str, created: list[tuple[str, str]], *, up: bool, converted: Converted,
    ) -> None:
        """Nach einem Abbruch: den alten Container wiederherstellen und, wenn der neue Stack sicher weg ist, die Dateien."""
        down_ok = True
        if up:
            _, down_ok = await self._back(host, target, old_id, name, renamed=True, converted=converted)
        else:
            # Ob Stoppen oder Umbenennen schon durch war, ist offen; beides zurück schadet nicht.
            await self._exec(host, cmd_rename_old(old_id, name), READ_TIMEOUT_S)
            await self._exec(host, cmd_start_old(old_id), STOP_TIMEOUT_S)
        if down_ok:
            with contextlib.suppress(Exception):
                async with self._ctx.exec.sftp(host) as sftp:
                    await _undo(sftp, created)

    async def _check(self, host: Any, target: Any) -> Any:
        from . import after_check as ac  # spät: after_check braucht capabilities

        try:
            found = [s for s in await self._convert.stacks.stacks(host, target.project) if s.project == target.project]
        except Exception:  # noqa: BLE001 - dann gilt die Prüfung als nicht möglich
            return None
        refs = [m.id for s in found for m in s.members]
        if not refs:
            return None
        verdict, _ = await ac.check_running(self._ctx, host, refs)
        return verdict

    async def _back(self, host: Any, target: Any, old_id: str, name: str, *, renamed: bool, converted: Converted) -> tuple[str, bool]:
        """Den neuen Stack herunterfahren, dem alten Container seinen Namen zurückgeben und ihn starten; dazu, ob der neue
        Stack sicher herunter ist."""
        down = await self._exec(host, cmd_verb(target, "docker.stack_down"), STOP_TIMEOUT_S * 2, converted)
        problems = [p for p in (
            down,
            await self._exec(host, cmd_rename_old(old_id, name), READ_TIMEOUT_S) if renamed else None,
            await self._exec(host, cmd_start_old(old_id), STOP_TIMEOUT_S),
        ) if p]
        if not problems:
            return "Der alte Container läuft wieder wie vorher.", True
        return (f"Zurück auf den alten Container ging nicht ganz ({'; '.join(dict.fromkeys(problems))}). Er heißt vielleicht noch „{name}{OLD_SUFFIX}“; "
                "sieh im Reiter Container nach."), down is None

    async def _undo_files(self, host: Any, created: list[tuple[str, str]], error: str, *, keep: bool = False) -> ActionResult:
        if keep:
            # Der neue Stack läuft womöglich noch aus diesem Ordner; ohne Datei ließe er sich nicht mehr herunterfahren.
            folder = next((path for kind, path in created if kind == "dir"), None)
            return ActionResult(success=False, exit_code=0, error=_sentence(error) + (
                f" Der Ordner {folder} bleibt, bis der neue Stack sicher herunter ist." if folder else ""))
        async with self._ctx.exec.sftp(host) as sftp:
            left = await asyncio.shield(_undo(sftp, created))
        return ActionResult(success=False, exit_code=0, error=_sentence(error) + _leftover(left))

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        return DryRunReport(would_change=True, summary=(
            f"Würde den Container „{payload.get('container', '?')}“ in einen Stack in {payload.get('folder', '?')} umwandeln."
        ))


def _env_vet(config: dict[str, Any], converted: Converted) -> str | None:
    """Liest Compose im neuen Ordner genau die Werte aus `container.env`? Der Grund nennt keinen Wert."""
    if not converted.env:
        return None
    service = ((config.get("services") or {}).get(converted.service) or {}) if isinstance(config, dict) else {}
    environment = service.get("environment") if isinstance(service, dict) else None
    if isinstance(environment, list):
        environment = dict(line.partition("=")[::2] for line in environment if isinstance(line, str))
    if not isinstance(environment, dict) or set(environment) != set(converted.env_values):
        return ENV_CHANGED
    # `compose config` schreibt jedes `$` als `$$`, damit sich die Ausgabe wieder lesen lässt; im Container steht `$`.
    same = all(environment[name] in (value, value.replace("$", "$$")) for name, value in converted.env_values.items())
    return None if same else ENV_CHANGED


def _scrub(text: str, converted: Converted) -> str:
    """Eine Meldung von Compose ohne die Werte der Variablen: Sie geht in das Ergebnis der Aktion und damit an den Browser."""
    if any(_shows(text, value) for value in converted.env_values.values() if len(value) >= 3):
        return f"Compose meldet einen Fehler, der Werte aus {ENV_NAME} enthält; Deck zeigt ihn deshalb nicht."
    return text


def _shows(text: str, value: str) -> bool:
    """Ob die Meldung den Wert zeigt: lange Werte überall, kurze nur als eigenes Wort (wie „bar“, nicht in „lesbar“)."""
    if len(value) >= 8 and value in text:
        return True
    if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text):
        return True
    # Eine gekürzte Meldung kann mit dem Anfang eines Werts enden, höchstens so lang wie die Kürzung.
    for n in range(min(len(value) - 1, len(text), FAILURE_LIMIT), 3, -1):
        if text.endswith(value[:n]) and (n == len(text) or not (text[-n - 1].isalnum() or text[-n - 1] == "_")):
            return True
    return False


def convert_routes(convert_: ContainerToStack, editor: StackEditor, docker_host: Any, current_actor: Any) -> APIRouter:
    router = APIRouter()

    async def _plan(host_id: str, container: str) -> tuple[Any, dict[str, Any]]:
        if not CONTAINER_NAME_RE.match(container):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Container-Name.")
        host = await docker_host(host_id)
        return host, await convert_.plan(host, container)

    @router.get("/hosts/{host_id}/containers/{container}/to-stack")
    async def convert_plan(host_id: str, container: str) -> dict:
        """Die Compose-Datei, die aus dem Container entstünde, ohne Werte der Variablen; dazu Gründe dagegen und Warnungen."""
        return (await _plan(host_id, container))[1]

    @router.post("/hosts/{host_id}/containers/{container}/to-stack", status_code=status.HTTP_202_ACCEPTED)
    async def convert_container(host_id: str, container: str, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Umwandeln vor (Gate-Aktion `docker.container_to_stack`, immer mit hohem Risiko)."""
        host, plan = await _plan(host_id, container)
        if plan["refused"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=" ".join(_sentence(r) for r in plan["refused"]))
        reason = (f"Container „{container}“ in den Stack „{plan['project']}“ umwandeln ({plan['folder']}): stoppen, aus der neuen "
                  "Compose-Datei neu erstellen und danach den alten Container löschen; Volumes und Ordner bleiben.")
        if plan["warnings"]:
            reason += " Riskant: " + " ".join(plan["warnings"])
        decision = await editor.propose(ActionRequest(
            action_type=CONVERT_ACTION, host_ref=host.id, risk=Risk.HIGH, proposed_by=actor, reason=reason,
            payload={"container": container, "id": plan["id"], "folder": plan["folder"], "compose_sha256": sha256(plan["compose"].encode())},
        ))
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": Risk.HIGH.value, "detail": decision.detail}

    return router
