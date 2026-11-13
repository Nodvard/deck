"""Compose-Dateien übernommener Stacks bearbeiten: prüfen, ohne etwas zu speichern, und speichern nur über das Gate, mit
der alten Fassung im Verlauf."""

from __future__ import annotations

from http import HTTPStatus
import asyncio
import contextlib
import difflib
import hashlib
import json
import os
import posixpath
import re
import secrets
import stat as statmod
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Response, status
from nodvard_sdk import REQUEST_WAIT_S
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Actor, Risk
from nodvard_sdk.errors import error_message
from pydantic import BaseModel, ConfigDict, Field

from .applier import RISK_ORDER
from .env_values import cmd_with_env, env_stdin
from .image_apply import PROJECT_RE
from .stack_actions import IN_FOLDER, NO_FOLDER_EXIT, DockerStackExecutor, busy_text
from .stacks import (
    FILE_SUFFIXES,
    MAX_FILE_BYTES,
    Stack,
    compose_argv,
    first_reason,
    no_such_file,
)

SAVE_ACTION = "docker.stack_save"
CHECK_TIMEOUT_S = 60
REALPATH_TIMEOUT_S = 20
HISTORY_KEEP = 20
DRAFT_TTL_S = 24 * 3600
MAX_DRAFTS = 50
_ID_RE = re.compile(r"^[0-9a-f]{32}\Z")
_ENTRY_RE = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}\Z")
_SFTP_OP_UNSUPPORTED = 8
_RISKY_CAPS = frozenset({"ALL", "SYS_ADMIN", "SYS_MODULE", "SYS_RAWIO", "SYS_PTRACE", "SYS_BOOT", "DAC_READ_SEARCH", "NET_ADMIN", "BPF"})
_UNCONFINED = frozenset({"seccomp:unconfined", "apparmor:unconfined", "label:disable", "systempaths:unconfined"})
_SENSITIVE_DIRS = (
    "/etc", "/root", "/boot", "/dev", "/proc", "/sys", "/run", "/var/run", "/var/lib/docker", "/var/lib/containers", "/usr", "/bin",
    "/sbin", "/lib", "/lib64",
)
_HARMLESS = ("/dev/dri", "/dev/net/tun", "/etc/localtime", "/etc/timezone")
"""Üblich und ohne Zugriff auf Dateien oder Prozesse des Servers: Grafikkarte (Jellyfin), VPN-Gerät, Zeitzone."""
_HARMLESS_DEVICES = ("/dev/ttyUSB", "/dev/ttyACM", "/dev/serial/")
"""Funk- und Zigbee-Sticks, etwa für Home Assistant."""
_SOCKETS = ("docker.sock", "containerd.sock", "podman.sock")
_SHARED = {
    "network_mode": "das Netz", "pid": "die Prozesse", "ipc": "den gemeinsamen Speicher", "userns_mode": "die Benutzerkennungen",
    "uts": "den Rechnernamen", "cgroup": "die Steuergruppen",
}
_SHARED_KEYS = frozenset(_SHARED)
_SHARES_WITH_OTHERS = ("network_mode", "pid", "ipc")
_DATA_ROOTS = ("/srv", "/mnt", "/media", "/data")
"""Ordner, in denen Dienste Nutzdaten ablegen dürfen, ohne dass das als riskant gilt; dazu kommt der Ordner des Stacks."""
_SAFE_SERVICE_KEYS = frozenset({
    "image", "command", "entrypoint", "environment", "env_file", "ports", "expose", "restart", "depends_on", "networks", "labels",
    "healthcheck", "container_name", "hostname", "domainname", "user", "working_dir", "deploy", "profiles", "stop_grace_period",
    "stop_signal", "tty", "stdin_open", "read_only", "tmpfs", "dns", "dns_search", "dns_opt", "extra_hosts", "cap_drop", "mem_limit",
    "mem_reservation", "memswap_limit", "cpus", "cpu_shares", "cpu_quota", "cpu_period", "cpuset", "shm_size", "logging", "init",
    "platform", "pull_policy", "sysctls", "ulimits", "links", "external_links", "mac_address", "group_add", "oom_score_adj",
    "pids_limit", "scale", "attach", "secrets", "configs", "gpus",
})
"""Bekannt harmlos. Alles, was hier und in den eigens geprüften Schlüsseln fehlt, gilt als riskant."""
_CHECKED_SERVICE_KEYS = frozenset({
    "privileged", "cap_add", "security_opt", "volumes", "devices", "volumes_from", "device_cgroup_rules", "provider", "build",
    "post_start", "pre_stop", *_SHARED_KEYS,
})
_SAFE_BUILD_KEYS = frozenset({"context", "dockerfile", "dockerfile_inline", "args", "target", "labels", "cache_from", "pull", "platforms", "no_cache"})
_SAFE_TOP_KEYS = frozenset({"version", "name", "services", "volumes", "networks", "secrets", "configs"})
_SAFE_SECURITY_OPT = frozenset({"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"})
_SAFE_VOLUME_TYPES = frozenset({"nfs", "nfs4", "cifs", "tmpfs"})
VARIABLE_PATH = "Pfad mit Variable"
OUTSIDE_DATA = "außerhalb der Datenordner"
_SAFE_NETWORK_DRIVERS = frozenset({"bridge", "overlay"})
_PRIVATE_MODE = 0o600
_SFTP_PERMISSION_DENIED = 3

STALE = "Die Datei wurde inzwischen auf dem Server geändert. Lade sie neu und trage deine Änderung noch einmal ein."
NOT_A_USER = "Eine Compose-Datei speichert nur ein Mensch über die Docker-Seite."
RISK_TOO_LOW = "Die Datei enthält riskante Einstellungen; dafür braucht es eine Freigabe mit hohem Risiko. Bitte neu speichern."


def save_action_spec() -> ActionSpec:
    return ActionSpec(
        action_type=SAVE_ACTION, label="Compose-Datei speichern", icon="save", default_risk=Risk.MEDIUM,
        description="Schreibt eine geprüfte Compose-Datei eines übernommenen Stacks auf den Server; die alte Fassung kommt in den Verlauf.",
        permissions=["hosts.execute"],
        # host_bound=False: Inhalt und Risikostufe legt immer die Docker-Seite fest, nie ein Formular auf der Server-Seite.
        host_bound=False,
        confirm_text="Die Compose-Datei auf dem Server wird ersetzt. Die alte Fassung bleibt im Verlauf. Fortfahren?",
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cmd_check(stack: Stack, index: int) -> list[str]:
    """Im Ordner des Stacks `docker compose config` mit allen Profilen; die Datei Nummer `index` kommt über die
    Standardeingabe, damit Compose den Entwurf liest und nicht die Datei auf dem Server."""
    return ["sh", "-c", IN_FOLDER, "sh", stack.working_dir,
            *compose_argv(stack, "--profile", "*", "config", "--format", "json", stdin_index=index)]


def cmd_realpath(paths: list[str]) -> list[str]:
    """Das tatsächliche Ziel jedes Ordners auf dem Server, auch wenn er noch fehlt."""
    return ["realpath", "-m", "--", *paths]


def _host_path(raw: str) -> str:
    """Absoluter Pfad ohne `..` und doppelte Schrägstriche; `//` ist für Docker dasselbe wie `/`."""
    return "/" + posixpath.normpath(raw).lstrip("/")


def _under(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root + "/") for root in roots)


def _sensitive(path: str) -> bool:
    """Ein Systemordner, etwas darin oder ein Ordner, der einen enthält (wie `/var` mit `/var/lib/docker`)."""
    if _under(path, _HARMLESS):
        return False
    return path == "/" or _under(path, _SENSITIVE_DIRS) or any(root.startswith(path + "/") for root in _SENSITIVE_DIRS)


def _path_warning(who: str, raw: Any, verb: str = "bindet", *, read_only: bool = False) -> str | None:
    """Ein Satz, wenn ein Pfad vom Server Docker selbst oder einen Systemordner in den Container bringt."""
    if not isinstance(raw, str) or not raw.startswith("/"):
        return None
    path = _host_path(raw)
    if path.endswith(_SOCKETS):
        return f"{who} bekommt Zugriff auf {posixpath.basename(path)}; wer den Container steuert, steuert damit den Server."
    if not _sensitive(path):
        return None
    if verb != "bindet":
        return f"{who} {verb} {path} vom Server."
    # Lesend oder schreibend steht im Satz, damit das Freigeben von Schreibrechten als neue Warnung zählt.
    return f"{who} bindet {path} vom Server ein{' (nur lesend)' if read_only else ''}."


def _roots(extra: tuple[str, ...]) -> tuple[str, ...]:
    return (*_DATA_ROOTS, *(_host_path(r) for r in extra if r.startswith("/") and _host_path(r) != "/"))


def _outside_warning(who: str, raw: Any, verb: str, roots: tuple[str, ...], resolved: dict[str, str] | None, *, read_only: bool = False) -> str | None:
    """Ein Pfad gilt nur als unbedenklich, wenn er und sein tatsächliches Ziel auf dem Server in einem Datenordner liegen."""
    if not isinstance(raw, str) or not raw.startswith("/"):
        return None
    path = _host_path(raw)
    real = _host_path(resolved[path]) if resolved and path in resolved else path
    for candidate in (path, real):
        if line := _path_warning(who, candidate, verb, read_only=read_only):
            return line
    if _under(path, _HARMLESS) and _under(real, _HARMLESS):
        return None
    ro = " (nur lesend)" if read_only else ""
    if real != path and not _under(real, roots):
        return f"{who} {verb} {path}, das auf dem Server auf {real} zeigt, {OUTSIDE_DATA}{ro}."
    if not _under(path, roots):
        return f"{who} {verb} {path} vom Server, {OUTSIDE_DATA}{ro}."
    return None


def _device(raw: Any) -> str | None:
    source = raw.get("source") if isinstance(raw, dict) else raw.split(":", 1)[0] if isinstance(raw, str) else None
    return source if isinstance(source, str) and source else None


def _shares_with_others(key: str, value: Any, own: set[str]) -> bool:
    """`container:` teilt mit einem fremden Container, `service:` nur, wenn der Dienst nicht in dieser Datei steht."""
    if key not in _SHARES_WITH_OTHERS or not isinstance(value, str):
        return False
    return value.startswith("container:") or (value.startswith("service:") and value.removeprefix("service:") not in own)


def _service_findings(name: str, svc: dict[str, Any], roots: tuple[str, ...], resolved: dict[str, str] | None, own: set[str]) -> list[str]:
    found: list[str] = []
    who = f"„{name}“"
    for key in sorted(set(svc) - _SAFE_SERVICE_KEYS - _CHECKED_SERVICE_KEYS):
        if not key.startswith("x-"):
            found.append(f"{who} nutzt die unbekannte Einstellung „{key}“; was nicht als harmlos bekannt ist, gilt als riskant.")
    if svc.get("privileged") is True:
        found.append(f"{who} bekommt alle Rechte des Servers (privileged).")
    for key, what in _SHARED.items():
        if svc.get(key) == "host":
            found.append(f"{who} teilt {what} mit dem Server ({key}: host).")
        elif _shares_with_others(key, svc.get(key), own):
            found.append(f"{who} teilt {what} mit einem anderen Container ({key}: {svc[key]}).")
    caps = {str(c).upper().removeprefix("CAP_") for c in svc.get("cap_add") or [] if isinstance(c, str)}
    if caps & _RISKY_CAPS:
        found.append(f"{who} bekommt besondere Rechte: {', '.join(sorted(caps & _RISKY_CAPS))} (cap_add).")
    for opt in svc.get("security_opt") or []:
        if not isinstance(opt, str):
            continue
        if opt.replace("=", ":", 1) in _UNCONFINED:
            found.append(f"{who} schaltet einen Schutz ab ({opt}).")
        elif opt not in _SAFE_SECURITY_OPT:
            found.append(f"{who} ändert eine Schutzeinstellung ({opt}).")
    for hook in (*(svc.get("post_start") or []), *(svc.get("pre_stop") or [])):
        if isinstance(hook, dict) and hook.get("privileged") is True:
            found.append(f"{who} führt einen Befehl mit allen Rechten des Servers aus (post_start oder pre_stop mit privileged).")
    build = svc.get("build")
    if isinstance(build, dict):
        for key in sorted(set(build) - _SAFE_BUILD_KEYS):
            found.append(f"{who} baut sein Image mit der Einstellung „{key}“ (build); die gilt als riskant.")
        for key in ("context", "dockerfile"):
            if line := _outside_warning(who, build.get(key), "baut sein Image aus", roots, resolved):
                found.append(line)
    for volume in svc.get("volumes") or []:
        if not isinstance(volume, dict) or volume.get("type") != "bind":
            continue
        if line := _outside_warning(who, volume.get("source"), "bindet", roots, resolved, read_only=volume.get("read_only") is True):
            found.append(line)
    for device in svc.get("devices") or []:
        source = _device(device)
        if source and source.startswith("/"):
            path = _host_path(source)
            if not _under(path, _HARMLESS) and not path.startswith(_HARMLESS_DEVICES):
                found.append(f"{who} bekommt das Gerät {path} des Servers (devices).")
    if svc.get("volumes_from"):
        found.append(f"{who} übernimmt alle Ordner anderer Container (volumes_from).")
    if svc.get("device_cgroup_rules"):
        found.append(f"{who} bekommt Zugriff auf Geräte des Servers (device_cgroup_rules).")
    provider = svc.get("provider")
    if isinstance(provider, dict) and provider.get("type"):
        found.append(f"{who} startet beim Anwenden ein Programm direkt auf dem Server, außerhalb jedes Containers (provider: {provider['type']}).")
    return found


def _volume_findings(name: str, entry: dict[str, Any], project: Any, roots: tuple[str, ...], resolved: dict[str, str] | None, flag_external: bool) -> list[str]:
    label = f"Das Volume „{name}“"
    if entry.get("external"):
        if not flag_external:
            return []
        return [f"{label} ist ein vorhandenes Volume des Servers, dessen Inhalt diese Datei nicht festlegt (external)."]
    found: list[str] = []
    served = entry.get("name")
    if isinstance(project, str) and isinstance(served, str) and not served.startswith(f"{project}_"):
        found.append(f"{label} heißt auf dem Server „{served}“ und gehört damit nicht zu diesem Stack.")
    if entry.get("driver") not in (None, "local"):
        found.append(f"{label} nutzt den Treiber „{entry['driver']}“, der auf dem Server eigene Programme ausführt.")
    opts = entry.get("driver_opts") if isinstance(entry.get("driver_opts"), dict) else {}
    raw, kind = opts.get("device"), opts.get("type")
    if isinstance(raw, str) and _host_path(raw).startswith("/dev/"):
        return [*found, f"{label} bindet das Gerät {_host_path(raw)} des Servers ein."]
    if kind == "overlay":
        return [*found, f"{label} legt Ordner des Servers übereinander (overlay)."]
    # Mit `o: bind` gilt der Dateisystemtyp nicht, das Volume ist dann ein Ordner des Servers.
    binds = "bind" in str(opts.get("o", "")) or kind == "none"
    if kind in _SAFE_VOLUME_TYPES and not binds:
        return found
    if kind not in (None, "none") and not binds:
        return [*found, f"{label} bindet ein Dateisystem des Servers ein (type: {kind})."]
    if binds and (line := _outside_warning(label, raw, "bindet", roots, resolved)):
        found.append(line)
    return found


def bind_sources(config: dict[str, Any], roots: tuple[str, ...] = ()) -> list[str]:
    """Absolute Ordner des Servers, die in der Datei vorkommen und in einem Datenordner liegen; sie lassen sich auf dem Server auflösen."""
    allowed, found = _roots(roots), set()
    services = config.get("services")
    for svc in services.values() if isinstance(services, dict) else []:
        for volume in (svc.get("volumes") or []) if isinstance(svc, dict) else []:
            if isinstance(volume, dict) and volume.get("type") == "bind" and isinstance(volume.get("source"), str):
                found.add(volume["source"])
        build = svc.get("build") if isinstance(svc, dict) else None
        for key in ("context", "dockerfile"):
            if isinstance(build, dict) and isinstance(build.get(key), str):
                found.add(build[key])
    for section in ("volumes", "secrets", "configs"):
        entries = config.get(section)
        for entry in entries.values() if isinstance(entries, dict) else []:
            if not isinstance(entry, dict):
                continue
            opts = entry.get("driver_opts")
            source = opts.get("device") if section == "volumes" and isinstance(opts, dict) else entry.get("file")
            if isinstance(source, str):
                found.add(source)
    paths = {_host_path(raw) for raw in found if raw.startswith("/")}
    return sorted(path for path in paths if _under(path, allowed))


class Finding(NamedTuple):
    """Eine Warnung samt dem, woran sie hängt: Art und Name des Eintrags in der Compose-Datei."""

    kind: str
    name: str
    text: str


def risky_findings(
    config: dict[str, Any], *, flag_external: bool = True, roots: tuple[str, ...] = (), resolved: dict[str, str] | None = None,
) -> list[Finding]:
    """Einstellungen, mit denen ein Container den Server übernehmen oder Schutz abschalten kann, mit ihrem Träger.

    Riskant ist alles, was nicht als harmlos bekannt ist: unbekannte Schlüssel und Ordner außerhalb der Datenordner (`roots` und
    die festen Datenordner). `resolved` ordnet Ordner dem tatsächlichen Ziel auf dem Server zu (Verweise aufgelöst)."""
    found: list[Finding] = []
    allowed = _roots(roots)
    for key in sorted(set(config) - _SAFE_TOP_KEYS):
        if not str(key).startswith("x-"):
            found.append(Finding("top", str(key), f"Die Datei nutzt die unbekannte Einstellung „{key}“; was nicht als harmlos bekannt ist, gilt als riskant."))
    services = config.get("services")
    for name, svc in sorted(services.items() if isinstance(services, dict) else []):
        if isinstance(svc, dict):
            found.extend(Finding("service", name, line) for line in _service_findings(name, svc, allowed, resolved, set(services)))
    volumes = config.get("volumes")
    for name, entry in sorted(volumes.items() if isinstance(volumes, dict) else []):
        if isinstance(entry, dict):
            found.extend(Finding("volume", name, line) for line in _volume_findings(name, entry, config.get("name"), allowed, resolved, flag_external))
    # Geheimnisse und Konfigurationen lesen Dateien des Servers.
    for section, kind, label in (("secrets", "secret", "Das Geheimnis"), ("configs", "config", "Die Konfiguration")):
        entries = config.get(section)
        for name, entry in sorted(entries.items() if isinstance(entries, dict) else []):
            if isinstance(entry, dict) and (line := _outside_warning(f"{label} „{name}“", entry.get("file"), "liest", allowed, resolved)):
                found.append(Finding(kind, name, line))
    networks = config.get("networks")
    for name, entry in sorted(networks.items() if isinstance(networks, dict) else []):
        if not isinstance(entry, dict):
            continue
        label = f"Das Netz „{name}“"
        if entry.get("external"):
            if flag_external:
                found.append(Finding("network", name, f"{label} ist ein vorhandenes Netz des Servers, das diese Datei nicht festlegt (external)."))
        elif entry.get("name") == "host" or entry.get("driver") == "host":
            found.append(Finding("network", name, f"{label} ist das Netz des Servers selbst (host)."))
        elif entry.get("driver") not in (None, *_SAFE_NETWORK_DRIVERS):
            found.append(Finding("network", name, f"{label} nutzt den Treiber „{entry['driver']}“, der sich direkt ins Netz des Servers hängt."))
    return found


def risky_settings(
    config: dict[str, Any], *, flag_external: bool = True, roots: tuple[str, ...] = (), resolved: dict[str, str] | None = None,
) -> list[str]:
    """Wie `risky_findings`, nur die Sätze für Menschen."""
    return [f.text for f in risky_findings(config, flag_external=flag_external, roots=roots, resolved=resolved)]


# Abschnitt der Datei, in dem ein Dienst einen Eintrag der obersten Ebene benutzt.
_USES = {"volume": "volumes", "secret": "secrets", "config": "configs", "network": "networks"}


def _users(config: dict[str, Any], kind: str, name: str) -> dict[str, Any]:
    """Die Dienste, die den Eintrag benutzen."""
    services = config.get("services")
    users: dict[str, Any] = {}
    for svc_name, svc in (services.items() if isinstance(services, dict) else []):
        used = svc.get(_USES[kind]) if isinstance(svc, dict) else None
        if isinstance(used, dict):
            hit = name in used
        else:
            hit = any(name == (item.get("source") if isinstance(item, dict) else item) for item in used or [])
        if hit:
            users[svc_name] = {"service": svc, "uses": _used_entries(config, svc)}
    return users


def _used_entries(config: dict[str, Any], svc: Any) -> dict[str, Any]:
    """Die Einträge der obersten Ebene (Volumes, Geheimnisse, Konfigurationen, Netze), die der Dienst benennt, samt Inhalt."""
    used: dict[str, Any] = {}
    for section in _USES.values():
        names = svc.get(section) if isinstance(svc, dict) else None
        if isinstance(names, dict):
            wanted = list(names)
        else:
            wanted = [item.get("source") if isinstance(item, dict) else item for item in names or []]
        top = config.get(section)
        used[section] = {n: top.get(n) for n in wanted if isinstance(n, str) and isinstance(top, dict)}
    return used


def _carrier(config: dict[str, Any], finding: Finding) -> str:
    """Alles, woran die Warnung hängt: der ganze Dienst, bei Volume, Geheimnis, Konfiguration und Netz der Eintrag samt Benutzern."""
    kind, name = finding.kind, finding.name
    if kind == "service":
        svc = (config.get("services") or {}).get(name)
        part: Any = {"service": svc, "uses": _used_entries(config, svc)}
    elif kind == "top":
        part = config.get(name)
    elif kind in _USES:
        part = {"entry": (config.get(_USES[kind]) or {}).get(name), "users": _users(config, kind, name)}
    else:
        return ""
    return json.dumps(part, sort_keys=True, default=str)


@dataclass(frozen=True)
class Checked:
    error: str | None = None
    """Compose lehnt die Datei ab."""
    unverified: str | None = None
    """Die Prüfung lief nicht (Server, Zeitlimit, Ordner); über die Datei sagt das nichts."""
    warnings: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = field(default=(), compare=False, repr=False)
    services: tuple[str, ...] = ()
    config: dict[str, Any] | None = field(default=None, compare=False, repr=False)
    """Die Datei, wie Compose sie aufgelöst hat (Pfade absolut, Variablen eingesetzt); nur zum Prüfen, nie für die Antwort."""

    @property
    def problem(self) -> str | None:
        if self.error is not None:
            return f"Compose lehnt die Datei ab: {self.error}"
        if self.unverified is not None:
            return f"Die Datei ließ sich nicht prüfen: {self.unverified}"
        return None

    def as_response(self) -> dict[str, Any]:
        return {"ok": self.problem is None, "error": self.error, "unverified": self.unverified, "warnings": list(self.warnings),
                "services": list(self.services)}


def parse_check(exit_code: int, stdout: str, stderr: str, name: str, *, roots: tuple[str, ...] = ()) -> Checked:
    """Antwort von `cmd_check`; in Fehlermeldungen steht statt „-“ der Name der Datei."""
    if exit_code == NO_FOLDER_EXIT:
        return Checked(unverified="Den Ordner des Stacks gibt es auf dem Server nicht mehr, oder er ist nicht lesbar.")
    if exit_code != 0:
        reason = first_reason((stderr or stdout or "").splitlines())
        return Checked(re.sub(r"(^|\s)-:", rf"\g<1>{name}:", reason))
    try:
        config = json.loads(stdout)
    except ValueError:
        config = None
    if not isinstance(config, dict):
        return Checked(unverified="Compose hat keine lesbare Antwort geliefert.")
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    return _checked(risky_findings(config, roots=roots), services, config)


def _checked(findings: list[Finding], services: Any, config: dict[str, Any]) -> Checked:
    return Checked(warnings=tuple(f.text for f in findings), findings=tuple(findings), services=tuple(sorted(services)), config=config)


def new_warnings(checked: Checked, before: Checked) -> list[str]:
    """Die Warnungen, die die Datei auf dem Server noch nicht hatte; ließ sie sich nicht prüfen, zählen alle.

    Bekannt ist eine Warnung nur, wenn alles unverändert ist, woran sie hängt (siehe `_carrier`)."""
    if before.problem is not None:
        return list(checked.warnings)
    if before.config is None or checked.config is None:
        return [line for line in checked.warnings if line not in set(before.warnings)]
    known = {(f, _carrier(before.config, f)) for f in before.findings}
    fresh = [f.text for f in checked.findings if (f, _carrier(checked.config, f)) not in known]
    # Hinweise ohne Träger (Ordner nicht aufgelöst) sagen nichts über die alte Fassung aus und zählen immer.
    return fresh + [line for line in checked.warnings if line not in {f.text for f in checked.findings}]


def changed_lines(old: bytes, new: bytes) -> str:
    """„+3 −1 Zeilen“: was der Freigebende über den Umfang erfährt, ohne dass Inhalt im Protokoll steht."""
    added = removed = 0
    for line in difflib.unified_diff(old.decode("utf-8", "replace").splitlines(), new.decode("utf-8", "replace").splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return f"+{added} −{removed} Zeilen"


def _stack_key(host_id: str, project: str) -> str:
    return hashlib.sha256(f"{host_id}\0{project}".encode()).hexdigest()[:32]


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        # Die halbe Kopie trägt den Inhalt und fiele sonst durch jedes Aufräumen.
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _mtime(path: Path) -> float | None:
    """Änderungszeit oder `None`, wenn die Datei inzwischen weg ist (zwei Speichern räumen gleichzeitig auf)."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


class Drafts:
    """Entwürfe zwischen Vorschlag und Freigabe; der Inhalt steht so nie in der Aktion und nie im Protokoll."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, host_id: str, project: str, path: str, content: bytes) -> str:
        self._root.mkdir(parents=True, exist_ok=True)
        self._prune()
        draft_id = secrets.token_hex(16)
        meta = {"host_id": host_id, "project": project, "path": path, "sha256": sha256(content), "created": time.time()}
        try:
            _write_private(self._root / f"{draft_id}.yaml", content)
            _write_private(self._root / f"{draft_id}.json", json.dumps(meta).encode())
        except OSError:
            self.drop(draft_id)
            raise
        return draft_id

    def get(self, draft_id: str) -> tuple[dict[str, Any], bytes] | None:
        if not _ID_RE.match(draft_id):
            return None
        try:
            meta = json.loads((self._root / f"{draft_id}.json").read_text(encoding="utf-8"))
            content = (self._root / f"{draft_id}.yaml").read_bytes()
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict) or meta.get("sha256") != sha256(content):
            return None
        return meta, content

    def drop(self, draft_id: str) -> None:
        if _ID_RE.match(draft_id):
            for suffix in (".json", ".yaml"):
                with contextlib.suppress(OSError):
                    (self._root / f"{draft_id}{suffix}").unlink()

    def _prune(self) -> None:
        metas = sorted(((meta, mtime) for meta in self._root.glob("*.json") if (mtime := _mtime(meta)) is not None), key=lambda m: m[1])
        cutoff = time.time() - DRAFT_TTL_S
        for index, (meta, mtime) in enumerate(metas):
            if mtime < cutoff or len(metas) - index >= MAX_DRAFTS:
                self.drop(meta.stem)
        # Erst nach einer Minute: Sonst träfe es einen Entwurf, dessen Beschreibung gerade noch geschrieben wird.
        for content in self._root.glob("*.yaml"):
            mtime = _mtime(content)
            if mtime is not None and mtime < time.time() - 60 and not content.with_suffix(".json").exists():
                self.drop(content.stem)


class History:
    """Die letzten Fassungen der Compose-Dateien eines Stacks, je Stack höchstens `HISTORY_KEEP`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def add(self, host_id: str, project: str, path: str, content: bytes) -> str:
        folder = self._root / _stack_key(host_id, project)
        folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        # Mit Mikrosekunden, damit die Namen auch bei mehreren Fassungen in einer Sekunde nach Zeit sortieren.
        entry_id = f"{now:%Y%m%dT%H%M%S%f}Z-{secrets.token_hex(4)}"
        record = {"path": path, "saved_at": now.isoformat(), "sha256": sha256(content),
                  "content": content.decode("utf-8", "replace")}
        _write_private(folder / f"{entry_id}.json", json.dumps(record).encode())
        return entry_id

    def drop(self, host_id: str, project: str, entry_id: str) -> None:
        if _ENTRY_RE.match(entry_id):
            with contextlib.suppress(OSError):
                (self._root / _stack_key(host_id, project) / f"{entry_id}.json").unlink()

    def prune(self, host_id: str, project: str) -> None:
        """Erst nach einem gelungenen Speichern: Fehlversuche verdrängen so keine echten alten Fassungen."""
        for old in sorted((self._root / _stack_key(host_id, project)).glob("*.json"))[:-HISTORY_KEEP]:
            with contextlib.suppress(OSError):
                old.unlink()

    def entries(self, host_id: str, project: str) -> list[dict[str, Any]]:
        out = []
        for file in sorted((self._root / _stack_key(host_id, project)).glob("*.json"), reverse=True):
            record = self._load(file)
            if record is not None:
                out.append({"id": file.stem, "path": record["path"], "saved_at": record["saved_at"], "size": len(record["content"].encode())})
        return out

    def entry(self, host_id: str, project: str, entry_id: str) -> dict[str, Any] | None:
        if not _ENTRY_RE.match(entry_id):
            return None
        record = self._load(self._root / _stack_key(host_id, project) / f"{entry_id}.json")
        return None if record is None else {"id": entry_id, **record}

    @staticmethod
    def _load(file: Path) -> dict[str, Any] | None:
        try:
            record = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or not all(isinstance(record.get(k), str) for k in ("path", "saved_at", "content")):
            return None
        return record


class StackEditor:
    """Prüfen, Lesen und Vorschlagen für die Routen; geschrieben wird nur im Ausführer."""

    def __init__(self, ctx: Any, actions: DockerStackExecutor, data_dir: Path) -> None:
        self._ctx = ctx
        self._actions = actions
        self.drafts = Drafts(data_dir / "editor" / "drafts")
        self.history = History(data_dir / "editor" / "history")

    async def target(self, host_id: str, project: str, path: str) -> tuple[Any, Stack, int]:
        """Host, Stack und Nummer der Datei; nur für übernommene Stacks und ihre eigenen Dateien."""
        found = await self._actions.target(host_id, project)
        if isinstance(found, str):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=found)
        host, stack = found
        if path not in (stack.files or ()):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Diese Datei gehört nicht zum Stack.")
        return host, stack, stack.files.index(path)

    async def check(self, host: Any, stack: Stack, index: int, content: bytes, env: dict[str, str] | None = None) -> Checked:
        try:
            result = await self._ctx.exec.run_argv(
                host, cmd_with_env(cmd_check(stack, index), env), stdin=env_stdin(env, content, saved=True), timeout_s=CHECK_TIMEOUT_S,
            )
        except OSError as exc:
            if type(exc) is TimeoutError:
                return Checked(unverified=f"Compose hat nicht innerhalb von {CHECK_TIMEOUT_S} Sekunden geantwortet.")
            return Checked(unverified=f"Server nicht erreichbar: {str(exc) or type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001 - nicht geprüft, nicht abgelehnt
            return Checked(unverified=f"Die Prüfung ist abgebrochen: {str(exc) or type(exc).__name__}")
        checked = parse_check(result.exit_code, result.stdout, result.stderr, posixpath.basename(stack.files[index]), roots=(stack.working_dir,))
        return await self.with_real_targets(host, checked, (stack.working_dir,))

    async def with_real_targets(self, host: Any, checked: Checked, roots: tuple[str, ...]) -> Checked:
        """Löst Ordner in Datenordnern auf dem Server auf (Verweise) und prüft das Ziel; gelingt das nicht, ist das selbst eine Warnung."""
        sources = bind_sources(checked.config, roots) if checked.config is not None else []
        if not sources:
            return checked
        try:
            done = await self._ctx.exec.run_argv(host, cmd_realpath(sources), timeout_s=REALPATH_TIMEOUT_S)
            lines = done.stdout.splitlines()
            resolved = dict(zip(sources, lines, strict=True)) if done.exit_code == 0 else None
        except Exception:  # noqa: BLE001 - nicht aufgelöst heißt Warnung, nie Durchlass
            resolved = None
        if resolved is None:
            note = f"Wohin diese Ordner auf dem Server wirklich zeigen, ließ sich nicht klären: {', '.join(sources)}."
            return replace(checked, warnings=(*checked.warnings, note))
        return _checked(risky_findings(checked.config, roots=roots, resolved=resolved), checked.services, checked.config)

    async def actions_result(self, action_id: str) -> Any:
        return await self._ctx.actions.result(action_id)

    async def may_decide(self, actor: Actor, action_id: str) -> bool:
        return await self._ctx.actions.may_decide(actor, action_id)

    async def propose(self, request: ActionRequest) -> Any:
        return await self._ctx.actions.propose(request, wait_s=REQUEST_WAIT_S)

    async def current(self, host: Any, path: str) -> bytes:
        """Die Datei, wie sie gerade auf dem Server liegt; ein Verweis (Symlink) wird nicht bearbeitet."""
        async with self._ctx.exec.sftp(host) as sftp:
            return (await _read_plain(sftp, path))[1]


class EditProblem(Exception):
    """Die Datei lässt sich nicht bearbeiten; der Text ist für Menschen."""


class Stale(EditProblem):
    def __init__(self) -> None:
        super().__init__(STALE)


async def _read_plain(sftp: Any, path: str) -> tuple[Any, bytes]:
    """Attribute und Inhalt einer normalen Datei; Verweise, Pipes, Geräte, zu große und nicht UTF-8-Dateien nicht."""
    try:
        attrs = await sftp.lstat(path)
    except Exception as exc:
        if no_such_file(exc):
            raise EditProblem("Die Datei gibt es auf dem Server nicht mehr.") from exc
        raise
    if attrs.permissions is not None and statmod.S_ISLNK(attrs.permissions):
        raise EditProblem("Die Datei ist ein Verweis auf eine andere Datei. Bearbeite sie dort, wo sie wirklich liegt.")
    if attrs.permissions is not None and not statmod.S_ISREG(attrs.permissions):
        raise EditProblem("Das ist keine normale Datei.")
    if (attrs.size or 0) > MAX_FILE_BYTES:
        raise EditProblem(f"Die Datei ist größer als {MAX_FILE_BYTES // 1024} KB.")
    async with sftp.open(path, "rb") as handle:
        data = await handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise EditProblem(f"Die Datei ist größer als {MAX_FILE_BYTES // 1024} KB.")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EditProblem("Die Datei ist nicht als UTF-8 gespeichert; Nodvard Deck bearbeitet sie nicht, damit keine Zeichen verloren gehen.") from exc
    return attrs, data


async def _unchanged(sftp: Any, path: str, base: str) -> None:
    """Kurz vor dem Ersetzen noch einmal: Hat jemand die Datei inzwischen geändert, gewinnt seine Fassung."""
    if sha256((await _read_plain(sftp, path))[1]) != base:
        raise Stale()


def _temp_path(path: str, kind: str) -> str:
    folder, name = posixpath.split(path)
    return posixpath.join(folder, f".{name[:100]}.nodvard-{kind}-{secrets.token_hex(6)}")


async def _take_over_owner(handle: Any, old: Any) -> bool:
    """Besitzer und Gruppe der alten Datei auf die neue übertragen, nur über den offenen Griff (Pfade folgen Links);
    `True`, wenn die neue Datei sie danach wirklich trägt."""
    if old.uid is None or old.gid is None:
        return True
    with contextlib.suppress(Exception):  # ohne root nicht erlaubt, das Ergebnis zählt
        await handle.chown(old.uid, old.gid)
    try:
        now = await handle.stat()
    except Exception:  # noqa: BLE001 - nicht prüfbar heißt nicht übertragen
        return False
    return (getattr(now, "uid", None), getattr(now, "gid", None)) == (old.uid, old.gid)


async def _replace(sftp: Any, tmp: str, path: str) -> None:
    """Ersetzt in einem Schritt; kennt der Server das nicht, wird die alte Datei beiseitegelegt und bei einem Fehler zurückgeholt."""
    try:
        await sftp.posix_rename(tmp, path)
        return
    except Exception as exc:  # nur „nicht unterstützt“ hat einen zweiten Weg
        if getattr(exc, "code", None) != _SFTP_OP_UNSUPPORTED:
            raise
    backup = _temp_path(path, "alt")
    await sftp.rename(path, backup)
    try:
        await sftp.rename(tmp, path)
    except BaseException:
        with contextlib.suppress(Exception):
            await sftp.rename(backup, path)
        raise
    with contextlib.suppress(Exception):
        await sftp.remove(backup)


async def _write_in_place(sftp: Any, path: str, content: bytes, base: str) -> None:
    """Direkt in die Datei, wenn sich Besitzer oder Ordner nicht anders halten lassen; der Inhalt liegt schon ganz vor."""
    await _unchanged(sftp, path, base)
    async with sftp.open(path, "wb") as handle:
        await handle.write(content)


async def _copy_mode(handle: Any, old: Any) -> str | None:
    """Die Rechte der alten Datei über den Griff; klappt das nicht, bleibt die neue bei 0600, und der Satz sagt es."""
    if old.permissions is None:
        return None
    wanted = statmod.S_IMODE(old.permissions)
    with contextlib.suppress(Exception):
        await handle.chmod(wanted)
        if statmod.S_IMODE((await handle.stat()).permissions) == wanted:
            return None
    return "Die Rechte der alten Datei ließen sich nicht übernehmen; die neue kann nur der Zugang von Nodvard Deck lesen."


async def _write(sftp: Any, path: str, old: Any, content: bytes, base: str) -> str | None:
    """Schreibt eine Datei daneben, die bis zum Ersetzen nur der Zugang lesen kann, übernimmt Besitzer und Rechte über den
    Griff und ersetzt dann in einem Schritt; so liest Compose nie eine halbe Datei. Liefert einen Hinweis oder `None`."""
    tmp = _temp_path(path, "neu")
    try:
        handle = await sftp.open(tmp, "xb", type(old)(permissions=_PRIVATE_MODE))
    except Exception as exc:
        # Datei beschreibbar, Ordner nicht: dann direkt hinein.
        if getattr(exc, "code", None) != _SFTP_PERMISSION_DENIED:
            raise
        await _write_in_place(sftp, path, content, base)
        return None
    keeps_owner, note = True, None
    try:
        async with handle:
            keeps_owner = await _take_over_owner(handle, old)
            if keeps_owner:
                await handle.write(content)
                note = await _copy_mode(handle, old)
        if keeps_owner:
            await _unchanged(sftp, path, base)
            await _replace(sftp, tmp, path)
    except BaseException:
        with contextlib.suppress(Exception):
            await sftp.remove(tmp)
        raise
    if not keeps_owner:
        # Ohne root ginge die Datei beim Ersetzen an den Zugang über; wer sie vorher schreiben durfte, verlöre das Recht.
        with contextlib.suppress(Exception):
            await sftp.remove(tmp)
        await _write_in_place(sftp, path, content, base)
    return note


async def _read_back(sftp: Any, path: str) -> bytes:
    async with sftp.open(path, "rb") as handle:
        return bytes(await handle.read(MAX_FILE_BYTES + 1))


AFTER_FAILURE = {
    "untouched": "",
    "restored": " Die alte Fassung steht wieder auf dem Server.",
    "unknown": " Die Datei auf dem Server ist jetzt vielleicht unvollständig. Die alte Fassung steht im Verlauf: Lade sie dort in "
    "den Editor und speichere sie erneut.",
}


async def _after_failure(sftp: Any, path: str, old: bytes, content: bytes) -> str:
    """Was nach einem abgebrochenen Schreiben auf dem Server steht: unverändert, schon gespeichert, wiederhergestellt oder
    unklar; eine halbe Datei bekommt dabei die alte Fassung zurück."""
    try:
        now = await _read_back(sftp, path)
    except Exception:  # noqa: BLE001 - Verbindung weg: Ob etwas geschrieben wurde, ist unklar
        return "unknown"
    if now == old:
        return "untouched"
    if now == content:
        return "saved"
    with contextlib.suppress(Exception):
        async with sftp.open(path, "wb") as handle:
            await handle.write(old)
        if await _read_back(sftp, path) == old:
            return "restored"
    return "unknown"


def _describe(exc: BaseException) -> str:
    if getattr(exc, "code", None) == _SFTP_PERMISSION_DENIED:
        return "Der Benutzer, mit dem Nodvard Deck sich anmeldet, darf diese Datei nicht schreiben."
    return str(exc) or type(exc).__name__


class DockerStackSaveExecutor:
    """Speichert einen Entwurf, wenn die Datei auf dem Server noch die ist, die der Nutzer bearbeitet hat."""

    action_types = frozenset({SAVE_ACTION})

    def __init__(self, ctx: Any, editor: StackEditor, actions: DockerStackExecutor) -> None:
        self._ctx = ctx
        self._editor = editor
        self._actions = actions

    async def execute(self, req: ActionRequest) -> ActionResult:
        payload = req.payload or {}
        draft_id = str(payload.get("draft") or "")
        try:
            return await self._execute(req, draft_id)
        finally:
            # Ein Vorschlag läuft nur einmal; sonst bliebe der Inhalt bis zum nächsten Aufräumen im Datenordner liegen.
            self._editor.drafts.drop(draft_id)

    async def _execute(self, req: ActionRequest, draft_id: str) -> ActionResult:
        payload = req.payload or {}
        project, path = str(payload.get("project") or ""), str(payload.get("path") or "")
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return ActionResult(success=False, error=NOT_A_USER)
        found = await self._actions.target(req.host_ref, project)
        if isinstance(found, str):
            return ActionResult(success=False, error=found)
        host, stack = found
        draft = self._editor.drafts.get(draft_id)
        if draft is None or (draft[0].get("host_id"), draft[0].get("project"), draft[0].get("path"), draft[0].get("sha256")) != (
            host.id, project, path, payload.get("sha256"),
        ):
            return ActionResult(success=False, error="Der Entwurf ist abgelaufen oder passt nicht. Bitte noch einmal speichern.")
        if path not in (stack.files or ()):
            return ActionResult(success=False, error="Diese Datei gehört nicht mehr zum Stack.")
        with self._actions.locks.hold(host.id, project, "Speichern") as busy:
            if busy is not None:
                return ActionResult(success=False, error=busy_text(busy))
            return await self._save(req, host, stack, path, draft[1], bool(payload.get("apply")))

    async def _save(self, req: ActionRequest, host: Any, stack: Stack, path: str, content: bytes, apply: bool) -> ActionResult:
        started = time.monotonic()
        index, base = stack.files.index(path), str((req.payload or {}).get("base_sha256") or "")
        checked = await self._editor.check(host, stack, index, content)
        if checked.problem is not None:
            return ActionResult(success=False, error=checked.problem)
        history = self._editor.history
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                try:
                    old_attrs, old = await _read_plain(sftp, path)
                except EditProblem:
                    raise
                except Exception as exc:  # noqa: BLE001 - gelesen, nicht geschrieben
                    return ActionResult(success=False, error=f"Die Datei ließ sich nicht lesen: {str(exc) or type(exc).__name__}. Gespeichert wurde nichts.")
                if sha256(old) != base:
                    raise Stale()
                before = await self._editor.check(host, stack, index, old)
                if new_warnings(checked, before) and RISK_ORDER.index(req.risk) < RISK_ORDER.index(Risk.HIGH):
                    return ActionResult(success=False, error=RISK_TOO_LOW)
                try:
                    entry = await asyncio.to_thread(history.add, host.id, stack.project, path, old)
                except OSError:
                    return ActionResult(success=False, error="Die alte Fassung ließ sich nicht sichern, deshalb wurde nichts geändert.")
                try:
                    note = await _write(sftp, path, old_attrs, content, base)
                except Stale:
                    history.drop(host.id, stack.project, entry)
                    raise
                except Exception as exc:  # noqa: BLE001 - erst nachsehen, was auf dem Server steht
                    state = await _after_failure(sftp, path, old, content)
                    if state != "saved":
                        if state == "untouched":
                            history.drop(host.id, stack.project, entry)
                        return ActionResult(success=False, error=f"Die Datei ließ sich nicht schreiben: {_describe(exc)}{AFTER_FAILURE[state]}")
                    note = None
        except EditProblem as exc:
            return ActionResult(success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            return ActionResult(success=False, error=f"Die Datei ließ sich nicht schreiben: {_describe(exc)}")
        history.prune(host.id, stack.project)
        saved = f"„{posixpath.basename(path)}“ von Stack „{stack.project}“ gespeichert. Die alte Fassung steht im Verlauf." + (f" {note}" if note else "")
        duration_ms = int((time.monotonic() - started) * 1000)
        # `saved` sagt der Oberfläche, dass die Datei auf dem Server steht, auch wenn das Anwenden danach scheitert.
        if not apply:
            return ActionResult(success=True, exit_code=0, output=saved, duration_ms=duration_ms, detail={"saved": True})
        try:
            applied = await self._actions.run(host, stack, "docker.stack_up")
        except Exception as exc:  # noqa: BLE001 - die Datei steht schon auf dem Server, das muss die Oberfläche erfahren
            return ActionResult(
                success=False, output=saved, duration_ms=duration_ms, detail={"saved": True},
                error=f"Gespeichert, aber das Anwenden ist gescheitert: {_describe(exc)}",
            )
        output = "\n".join(part for part in (saved, applied.output) if part)
        detail = {**(applied.detail or {}), "saved": True}
        if applied.success:
            return ActionResult(success=True, exit_code=0, output=output, duration_ms=duration_ms + (applied.duration_ms or 0), detail=detail)
        return ActionResult(
            success=False, exit_code=applied.exit_code, output=output, duration_ms=duration_ms + (applied.duration_ms or 0),
            error=f"Gespeichert, aber das Anwenden ist gescheitert: {applied.error}", detail=detail,
        )

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        then = " und den Stack danach anwenden" if payload.get("apply") else ""
        return DryRunReport(
            would_change=True,
            summary=f"Würde „{posixpath.basename(str(payload.get('path') or '?'))}“ von Stack „{payload.get('project', '?')}“ ersetzen "
            f"({payload.get('changes', '?')}){then}.",
        )


class _CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(max_length=1024)
    content: str = Field(max_length=MAX_FILE_BYTES)


class _SaveIn(_CheckIn):
    base_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    apply: bool = False


def editor_routes(editor: StackEditor, docker_host: Any, current_actor: Any) -> APIRouter:
    router = APIRouter()

    async def _target(host_id: str, project: str, path: str) -> tuple[Any, Stack, int]:
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        if not path.lower().endswith(FILE_SUFFIXES):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nur Compose-Dateien mit der Endung .yml oder .yaml.")
        await docker_host(host_id)
        return await editor.target(host_id, project, path)

    def _bytes(content: str) -> bytes:
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"Die Datei ist größer als {MAX_FILE_BYTES // 1024} KB.")
        return data

    @router.post(
        "/hosts/{host_id}/stacks/{project}/check",
        responses={code: {"description": HTTPStatus(code).phrase, "content": {"application/json": {}}}
                   for code in (400, 409, 413,)},
    )
    async def check_file(host_id: str, project: str, body: _CheckIn) -> dict:
        """Prüft einen Entwurf mit Compose selbst, ohne etwas zu speichern; dazu riskante Einstellungen."""
        host, stack, index = await _target(host_id, project, body.path)
        return (await editor.check(host, stack, index, _bytes(body.content))).as_response()

    @router.post(
        "/hosts/{host_id}/stacks/{project}/save", status_code=status.HTTP_202_ACCEPTED,
        responses={code: {"description": HTTPStatus(code).phrase, "content": {"application/json": {}}}
                   for code in (400, 409, 413, 502, 503,)},
    )
    async def save_file(host_id: str, project: str, body: _SaveIn, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Speichern vor (Gate-Aktion `docker.stack_save`), mit hohem Risiko bei riskanten Einstellungen."""
        host, stack, index = await _target(host_id, project, body.path)
        content = _bytes(body.content)
        checked = await editor.check(host, stack, index, content)
        if checked.problem is not None:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY if checked.error is not None else status.HTTP_502_BAD_GATEWAY
            raise HTTPException(status_code=code, detail=checked.problem)
        try:
            old = await editor.current(host, body.path)
        except EditProblem as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except Exception as exc:  # als Grund zeigen, nicht als 500
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=error_message(exc, "Datei nicht lesbar.")) from None
        if sha256(old) != body.base_sha256:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=STALE)
        if old == content:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Die Datei ist unverändert, es gibt nichts zu speichern.")
        # Hohes Risiko nur für riskante Einstellungen, die die Datei auf dem Server noch nicht hat.
        fresh = new_warnings(checked, await editor.check(host, stack, index, old))
        risk = Risk.HIGH if fresh else Risk.MEDIUM
        try:
            draft = await asyncio.to_thread(editor.drafts.put, host.id, project, body.path, content)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Der Entwurf ließ sich nicht ablegen ({exc.strerror or type(exc).__name__}). Ist der Datenordner von Nodvard Deck "
                "voll oder schreibgeschützt?",
            ) from exc
        name, changes = posixpath.basename(body.path), changed_lines(old, content)
        reason = f"Compose-Datei „{name}“ von Stack „{project}“ speichern ({changes})" + (" und den Stack anwenden" if body.apply else "") + "."
        if fresh:
            reason += " Neu und riskant: " + " ".join(fresh)
        decision = await editor.propose(
            ActionRequest(
                action_type=SAVE_ACTION, host_ref=host.id, risk=risk, proposed_by=actor, reason=reason,
                payload={
                    "project": project, "path": body.path, "draft": draft, "sha256": sha256(content), "base_sha256": body.base_sha256,
                    "apply": body.apply, "changes": changes, "warnings": fresh,
                },
            ),
        )
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value, "detail": decision.detail}

    @router.get(
        "/hosts/{host_id}/stacks/{project}/save-proposals/{action_id}",
        responses={code: {"description": HTTPStatus(code).phrase, "content": {"application/json": {}}}
                   for code in (400, 403, 404, 409, 502,)},
    )
    async def save_proposal(host_id: str, project: str, action_id: str, response: Response, actor: Actor = Depends(current_actor)) -> dict:
        """Der vorgeschlagene Inhalt und der Unterschied zur Datei auf dem Server; nur für den Vorschlagenden und wer freigeben darf."""
        response.headers["Cache-Control"] = "no-store"
        await docker_host(host_id)
        action = await editor.actions_result(action_id)
        payload = (action.payload or {}) if action is not None else {}
        if action is None or action.action_type != SAVE_ACTION or action.status != "proposed" or payload.get("project") != project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diesen Vorschlag gibt es nicht (mehr).")
        own = (action.proposed_by_type, action.proposed_by_id) == ("user", str(actor.id))
        if not own and not await editor.may_decide(actor, action_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Den Inhalt sieht nur, wer den Vorschlag gemacht hat oder ihn freigeben darf.")
        host, _stack, _index = await _target(host_id, project, str(payload.get("path") or ""))
        draft = editor.drafts.get(str(payload.get("draft") or ""))
        if draft is None or (draft[0].get("host_id"), draft[0].get("project"), draft[0].get("path"), draft[0].get("sha256")) != (
            host.id, project, payload.get("path"), payload.get("sha256"),
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Entwurf zu diesem Vorschlag ist nicht mehr da.")
        try:
            old = await editor.current(host, str(payload["path"]))
        except EditProblem as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except Exception as exc:  # als Grund zeigen, nicht als 500
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=error_message(exc, "Datei nicht lesbar.")) from None
        new = draft[1]
        diff = difflib.unified_diff(
            old.decode("utf-8", "replace").splitlines(), new.decode("utf-8", "replace").splitlines(), "Server", "Vorschlag", lineterm="",
        )
        return {"content": new.decode("utf-8", "replace"), "diff": "\n".join(diff)}

    @router.get(
        "/hosts/{host_id}/stacks/{project}/history",
        responses={code: {"description": HTTPStatus(code).phrase, "content": {"application/json": {}}}
                   for code in (400,)},
    )
    async def history(host_id: str, project: str) -> dict:
        """Die gespeicherten alten Fassungen, neueste zuerst, ohne Inhalt."""
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        host = await docker_host(host_id)
        return {"entries": editor.history.entries(host.id, project)}

    @router.get(
        "/hosts/{host_id}/stacks/{project}/history/{entry_id}",
        responses={code: {"description": HTTPStatus(code).phrase, "content": {"application/json": {}}}
                   for code in (400, 404,)},
    )
    async def history_entry(host_id: str, project: str, entry_id: str) -> dict:
        """Eine alte Fassung mit Inhalt, etwa um sie in den Editor zu laden."""
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        host = await docker_host(host_id)
        entry = editor.history.entry(host.id, project, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diese Fassung gibt es nicht (mehr).")
        return entry

    return router
