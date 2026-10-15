"""Images, Volumes und Netze eines Docker-Servers: gelesen aus `docker system df -v` und `docker network ls`, gelöscht
nur über die Freigabe."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Risk

from . import commands
from .capabilities import _json_pattern, parse_size

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

CACHE_TTL_S = 15.0
"""Die drei Listen teilen sich einen Abruf; wer zwischen den Reitern wechselt, löst keinen neuen aus."""
DF_TIMEOUT_S = 60
NETWORKS_TIMEOUT_S = 20
BUILTIN_NETWORKS = frozenset({"bridge", "host", "none"})
_ANONYMOUS_LABEL = "com.docker.volume.anonymous"
_PROJECT_LABEL = "com.docker.compose.project"
_ANONYMOUS_NAME = re.compile(r"^[0-9a-f]{64}\Z")


class ResourceProblem(RuntimeError):
    """Docker hat nicht geantwortet oder etwas Unlesbares geliefert; der Text ist für Menschen."""


@dataclass(frozen=True)
class Snapshot:
    images: list[dict[str, Any]]
    volumes: list[dict[str, Any]]
    networks: list[dict[str, Any]]
    network_error: str | None = None


def _labels(text: str) -> dict[str, str]:
    """`a=1,b=2` aus der Listen-Ausgabe; ein Wert mit Komma lässt sich so nicht trennen und bleibt abgeschnitten."""
    pairs = (item.partition("=") for item in (text or "").split(",") if item)
    return {key: value for key, _, value in pairs if key}


def _split(text: str) -> list[str]:
    return [part for part in (text or "").split(",") if part]


def _listed(name: str, joined: str) -> bool:
    """Steht `name` in der Komma-Liste? Ein Netzname darf selbst Kommas enthalten, deshalb nicht trennen."""
    return bool(joined) and f",{name}," in f",{joined},"


def _count(text: str) -> int | None:
    """Zahl der Nutzer; `None`, wenn Docker keine nennt (`N/A`, `-1`), das zählt als benutzt."""
    return int(text) if (text or "").isdigit() else None


def _used(count: int | None, used_by: list[str]) -> bool:
    return count is None or count > 0 or bool(used_by)


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _image_users(refs: list[str], image_id: str, containers: list[dict[str, Any]]) -> list[str]:
    """Container, deren Image einer der Namen oder die ID dieses Images ist (`docker ps` nennt, womit er erstellt wurde)."""
    names = set(refs) | {r.removesuffix(":latest") for r in refs if r.endswith(":latest")}
    short = image_id.removeprefix("sha256:")[:12]
    return sorted(
        str(c.get("Names") or "") for c in containers
        if c.get("Image") in names or (short and str(c.get("Image") or "").removeprefix("sha256:").startswith(short))
    )


def _rollback_copy(names: list[str]) -> bool:
    """Der Rückweg von Nodvard Deck nach einem misslungenen Update (`deploy_pi.sh` bzw. das Schutz-Tag des Update-Helfers)."""
    for name in names:
        repo, sep, tag = name.rpartition(":")
        if not sep or "/" in tag:
            repo, tag = name, ""
        repo = repo.removeprefix("docker.io/").removeprefix("library/")
        if (repo == "nodvard-deck" and tag == "previous") or repo == "nodvard-deck-previous":
            return True
    return False


def parse_images(df: dict[str, Any]) -> list[dict[str, Any]]:
    containers = _rows(df.get("Containers"))
    grouped: dict[str, dict[str, Any]] = {}
    for row in _rows(df.get("Images")):
        image_id = str(row.get("ID") or "")
        if not image_id:
            continue
        entry = grouped.setdefault(image_id, {
            "id": image_id, "short_id": image_id.removeprefix("sha256:")[:12], "names": [],
            "size": row.get("Size") or None, "size_bytes": parse_size(row.get("Size") or ""),
            "unique_size_bytes": parse_size(row.get("UniqueSize") or ""),
            "created_at": row.get("CreatedAt") or None, "containers": _count(row.get("Containers") or ""),
        })
        repo, tag = row.get("Repository") or "", row.get("Tag") or ""
        if repo and repo != "<none>":
            entry["names"].append(f"{repo}:{tag}" if tag and tag != "<none>" else repo)
    images = []
    for entry in grouped.values():
        entry["names"] = sorted(set(entry["names"]))
        entry["used_by"] = _image_users(entry["names"], entry["id"], containers)
        entry["in_use"] = _used(entry["containers"], entry["used_by"])
        entry["dangling"] = not entry["names"]
        entry["rollback"] = _rollback_copy(entry["names"])
        # Mit mehreren Namen lehnt `docker image rm <ID>` ohne `-f` ab.
        entry["removable"] = (
            not entry["in_use"] and not entry["rollback"] and len(entry["names"]) <= 1
            and bool(commands.IMAGE_ID_RE.match(entry["id"]))
        )
        images.append(entry)
    return sorted(images, key=lambda i: (i["names"][0] if i["names"] else "~", i["id"]))


def parse_volumes(df: dict[str, Any]) -> list[dict[str, Any]]:
    users: dict[str, list[str]] = {}
    for container in _rows(df.get("Containers")):
        for name in _split(container.get("Mounts") or ""):
            users.setdefault(name, []).append(str(container.get("Names") or ""))
    volumes = []
    for row in _rows(df.get("Volumes")):
        name = str(row.get("Name") or "")
        if not commands.VOLUME_NAME_RE.match(name):
            continue
        labels = _labels(row.get("Labels") or "")
        used_by = sorted(users.get(name, []))
        in_use = _used(_count(row.get("Links") or ""), used_by)
        volumes.append({
            "name": name, "driver": row.get("Driver") or None, "scope": row.get("Scope") or None,
            "size": row.get("Size") or None, "size_bytes": parse_size(row.get("Size") or ""),
            "anonymous": _ANONYMOUS_LABEL in labels and bool(_ANONYMOUS_NAME.match(name)),
            "compose_project": labels.get(_PROJECT_LABEL) or None,
            "used_by": used_by, "in_use": in_use, "removable": not in_use,
        })
    return sorted(volumes, key=lambda v: (v["anonymous"], v["name"]))


def parse_networks(text: str, df: dict[str, Any]) -> list[dict[str, Any]]:
    containers = _rows(df.get("Containers"))
    networks = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        network_id, name = str(row.get("ID") or ""), str(row.get("Name") or "")
        if not commands.NETWORK_ID_RE.match(network_id) or not name:
            continue
        labels = _labels(row.get("Labels") or "")
        used_by = sorted(str(c.get("Names") or "") for c in containers if _listed(name, str(c.get("Networks") or "")))
        builtin = name in BUILTIN_NETWORKS
        networks.append({
            "id": network_id, "short_id": network_id[:12], "name": name, "driver": row.get("Driver") or None,
            "scope": row.get("Scope") or None, "internal": row.get("Internal") == "true",
            "builtin": builtin, "compose_project": labels.get(_PROJECT_LABEL) or None,
            "created_at": row.get("CreatedAt") or None, "used_by": used_by, "in_use": bool(used_by),
            "removable": not used_by and not builtin,
        })
    return sorted(networks, key=lambda n: (not n["builtin"], n["name"]))


def _first_line(text: str) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


class DockerResources:
    """Ein Abruf je Server für alle drei Listen, kurz zwischengespeichert."""

    def __init__(self, ctx: ExtensionContext, clock: Callable[[], float] = time.monotonic) -> None:
        self._ctx = ctx
        self._clock = clock
        self._cache: dict[str, tuple[float, Snapshot]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def snapshot(self, host: Any) -> Snapshot:
        lock = self._locks.setdefault(host.id, asyncio.Lock())
        async with lock:
            cached = self._cache.get(host.id)
            if cached is not None and self._clock() - cached[0] < CACHE_TTL_S:
                return cached[1]
            snapshot = await self._read(host)
            self._cache[host.id] = (self._clock(), snapshot)
            return snapshot

    async def _read(self, host: Any) -> Snapshot:
        try:
            df = await self._ctx.exec.run(host, commands.system_df_verbose(), timeout_s=DF_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - als Grund zeigen, nicht als 500
            raise ResourceProblem(f"Server nicht erreichbar: {str(exc) or type(exc).__name__}") from exc
        if df.exit_code != 0:
            raise ResourceProblem(_first_line(df.stderr) or f"docker system df endete mit {df.exit_code}.")
        try:
            data = json.loads(df.stdout)
        except ValueError as exc:
            raise ResourceProblem("Docker lieferte eine unlesbare Antwort (docker system df).") from exc
        if not isinstance(data, dict):
            raise ResourceProblem("Docker lieferte eine unlesbare Antwort (docker system df).")
        network_error = None
        networks: list[dict[str, Any]] = []
        try:
            listed = await self._ctx.exec.run(host, commands.networks(), timeout_s=NETWORKS_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - die anderen Listen bleiben brauchbar
            network_error = f"Server nicht erreichbar: {str(exc) or type(exc).__name__}"
        else:
            if listed.exit_code == 0:
                networks = parse_networks(listed.stdout, data)
            else:
                network_error = _first_line(listed.stderr) or f"docker network ls endete mit {listed.exit_code}."
        return Snapshot(parse_images(data), parse_volumes(data), networks, network_error)

    def forget(self, host_id: str) -> None:
        self._cache.pop(host_id, None)


_IMAGE_SCHEMA = {
    "type": "object", "properties": {"image": {"type": "string", "pattern": _json_pattern(commands.IMAGE_ID_RE)}},
    "required": ["image"],
}
_VOLUME_SCHEMA = {
    "type": "object", "properties": {"volume": {"type": "string", "pattern": _json_pattern(commands.VOLUME_NAME_RE)}},
    "required": ["volume"],
}
_NETWORK_SCHEMA = {
    "type": "object", "properties": {"network": {"type": "string", "pattern": _json_pattern(commands.NETWORK_ID_RE)}},
    "required": ["network"],
}


def resource_action_specs(tag: str = "docker") -> list[ActionSpec]:
    """Wie die Container-Aktionen nur für Server mit dem Docker-Tag."""
    return [
        ActionSpec(
            action_type="docker.image_remove", label="Image löschen", icon="trash-2",
            description="Löscht ein Image, das kein Container nutzt (docker image rm, ohne -f).",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            params_schema=_IMAGE_SCHEMA,
            confirm_text="Das Image wird gelöscht. Braucht es später ein Container, lädt Docker es neu herunter; "
            "ein selbst gebautes muss neu gebaut werden. Fortfahren?",
        ),
        ActionSpec(
            action_type="docker.volume_remove", label="Volume löschen", icon="trash-2",
            description="Löscht ein Volume, das kein Container nutzt (docker volume rm, ohne -f).",
            default_risk=Risk.HIGH, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            params_schema=_VOLUME_SCHEMA,
            confirm_text="Die Daten darin sind dann weg, das lässt sich nicht rückgängig machen. Fortfahren?",
        ),
        ActionSpec(
            action_type="docker.network_remove", label="Netz löschen", icon="trash-2",
            description="Löscht ein Netz, an dem kein Container hängt (docker network rm).",
            default_risk=Risk.LOW, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            params_schema=_NETWORK_SCHEMA,
            confirm_text="Das Netz wird gelöscht. Ein Compose-Projekt legt es beim nächsten Start wieder an. Fortfahren?",
        ),
    ]


_REFUSALS = (
    ("being used by", "Ein Container nutzt das Image noch (auch ein gestoppter zählt). Erst den Container löschen."),
    ("must be forced", "Das Image hat mehrere Namen oder wird noch gebraucht. Docker löscht es so nicht."),
    ("volume is in use", "Ein Container nutzt das Volume noch (auch ein gestoppter zählt). Erst den Container löschen."),
    ("active endpoints", "An dem Netz hängen noch Container."),
    ("pre-defined network", "Die eingebauten Netze von Docker lassen sich nicht löschen."),
    ("no such", "Nicht (mehr) vorhanden."),
    ("not found", "Nicht (mehr) vorhanden."),
)


def _plain(stderr: str) -> str:
    """Dockers englische Ablehnung als Satz mit Grund; unbekannte bleiben, wie Docker sie schreibt."""
    text = _first_line(stderr)
    lowered = text.lower()
    return next((plain for marker, plain in _REFUSALS if marker in lowered), text)


class DockerResourceExecutor:
    """Löscht ein Image, Volume oder Netz, nur nach dem Gate; jeder Wert wird hier noch einmal geprüft."""

    action_types = frozenset({"docker.image_remove", "docker.volume_remove", "docker.network_remove"})

    def __init__(self, ctx: ExtensionContext, resources: DockerResources) -> None:
        self._ctx = ctx
        self._resources = resources

    async def execute(self, req: ActionRequest) -> ActionResult:
        if req.action_type not in self.action_types:
            return ActionResult(success=False, error=f"Unbekannte Aktion '{req.action_type}'.")
        host = await self._ctx.hosts.get(req.host_ref) if req.host_ref else None
        if host is None:
            return ActionResult(success=False, error="Server nicht gefunden.")
        try:
            if req.action_type == "docker.image_remove":
                target = str(req.payload.get("image") or "")
                command = commands.image_remove(target)
                what = f"Image {target.removeprefix('sha256:')[:12]}"
            elif req.action_type == "docker.volume_remove":
                target = str(req.payload.get("volume") or "")
                command = commands.volume_remove(target)
                what = f"Volume {target}"
            else:
                target = str(req.payload.get("network") or "")
                command = commands.network_remove(target)
                what = f"Netz {target[:12]}"
        except ValueError as exc:
            return ActionResult(success=False, error=str(exc))
        result = await self._ctx.exec.run(host, command, timeout_s=120)
        self._resources.forget(host.id)
        if req.action_type == "docker.network_remove" and result.exit_code == commands.NETWORK_IN_USE:
            users = ", ".join(line.strip() for line in result.stdout.splitlines() if line.strip())
            return ActionResult(
                success=False, exit_code=result.exit_code, duration_ms=result.duration_ms,
                error=f"An dem Netz hängen noch Container, auch gestoppte zählen: {users}. Nichts gelöscht.",
            )
        if result.exit_code != 0:
            return ActionResult(
                success=False, exit_code=result.exit_code, error=_plain(result.stderr) or None, duration_ms=result.duration_ms,
            )
        if req.action_type == "docker.network_remove" and _first_line(result.stdout):
            what = f"Netz {_first_line(result.stdout)}"
        return ActionResult(success=True, exit_code=0, output=f"{what} gelöscht.", duration_ms=result.duration_ms)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        what = {
            "docker.image_remove": f"Image {str(req.payload.get('image') or '?').removeprefix('sha256:')[:12]}",
            "docker.volume_remove": f"Volume {req.payload.get('volume') or '?'}",
            "docker.network_remove": f"Netz {str(req.payload.get('network') or '?')[:12]}",
        }.get(req.action_type, "?")
        return DryRunReport(would_change=True, summary=f"Würde {what} löschen, nur wenn es nichts mehr nutzt.")
