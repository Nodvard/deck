"""Netze anlegen und Container mit einem Netz verbinden oder davon trennen, nur über die Freigabe und mit festen Argumenten."""

from __future__ import annotations

import re
from typing import Any

from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Risk

from .capabilities import _json_pattern, is_own_container
from .commands import CONTAINER_NAME_RE, NETWORK_ID_RE
from .operate import still_full
from .resources import BUILTIN_NETWORKS, DockerResources

NETWORK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}\Z")
RESERVED_NAMES = BUILTIN_NETWORKS | {"default"}
"""Diese Namen hält Docker für seine eigenen Netze frei."""
FULL_ID_RE = re.compile(r"^[0-9a-f]{64}\Z")
TIMEOUT_S = 60

CREATE = "docker.network_create"
CONNECT = "docker.network_connect"
DISCONNECT = "docker.network_disconnect"


_CREATE_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string", "pattern": _json_pattern(NETWORK_NAME_RE)}, "internal": {"type": "boolean"}},
    "required": ["name"],
}
_LINK_SCHEMA = {
    "type": "object",
    "properties": {
        "network": {"type": "string", "pattern": _json_pattern(NETWORK_ID_RE)},
        "container": {"type": "string", "pattern": _json_pattern(CONTAINER_NAME_RE)},
    },
    "required": ["network", "container"],
}


def network_action_specs(tag: str = "docker") -> list[ActionSpec]:
    """Nur für Server mit dem Docker-Tag; im Modus „Nur bedienen“ lehnt der Ausführer ab."""
    return [
        ActionSpec(
            action_type=CREATE, label="Netz anlegen", icon="network",
            description="Legt ein Bridge-Netz an (docker network create), auf Wunsch nur intern ohne Zugang nach außen.",
            default_risk=Risk.LOW, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            params_schema=_CREATE_SCHEMA,
            confirm_text="Docker legt ein neues Netz an. Container hängen erst daran, wenn du sie verbindest. Fortfahren?",
        ),
        ActionSpec(
            action_type=CONNECT, label="Container mit Netz verbinden", icon="link",
            description="Verbindet einen Container mit einem Netz (docker network connect).",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            params_schema=_LINK_SCHEMA,
            confirm_text="Der Container kann danach die anderen Container in diesem Netz erreichen und sie ihn. Erstellt Compose "
            "ihn neu, ist die Verbindung wieder weg. Fortfahren?",
        ),
        ActionSpec(
            action_type=DISCONNECT, label="Container vom Netz trennen", icon="unlink",
            description="Trennt einen Container von einem Netz (docker network disconnect, ohne -f).",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            params_schema=_LINK_SCHEMA,
            confirm_text="Dienste, die den Container über dieses Netz erreichen, finden ihn danach nicht mehr. Fortfahren?",
        ),
    ]


def cmd_create(name: str, internal: bool) -> list[str]:
    if not NETWORK_NAME_RE.match(name) or name in RESERVED_NAMES:
        raise ValueError("Der Name des Netzes ist ungültig: Buchstaben, Ziffern, „_“, „.“ und „-“, am Anfang ein Buchstabe "
                         "oder eine Ziffer, nicht bridge, host, none oder default.")
    return ["docker", "network", "create", "--driver", "bridge", *(["--internal"] if internal else []), name]


def _link(verb: str, network_id: str, container_id: str) -> list[str]:
    if not NETWORK_ID_RE.match(network_id):
        raise ValueError("Ungültige Netz-ID.")
    if not FULL_ID_RE.match(container_id):
        raise ValueError("Ungültige Container-ID.")
    return ["docker", "network", verb, network_id, container_id]


def cmd_connect(network_id: str, container_id: str) -> list[str]:
    return _link("connect", network_id, container_id)


def cmd_disconnect(network_id: str, container_id: str) -> list[str]:
    return _link("disconnect", network_id, container_id)


def cmd_container(container: str) -> list[str]:
    """Volle ID und Netzmodus; der Befehl danach nennt die ID, damit ihn ein inzwischen umbenannter Container nicht trifft."""
    if not CONTAINER_NAME_RE.match(container):
        raise ValueError("Ungültiger Containername.")
    return ["docker", "inspect", "--type", "container", "--format", "{{.Id}}|{{.HostConfig.NetworkMode}}", container]


_REFUSALS: dict[str, tuple[tuple[str, str], ...]] = {
    CREATE: (("already exists", "Ein Netz mit diesem Namen gibt es schon."),),
    CONNECT: (
        ("already exists", "Der Container hängt schon an diesem Netz."),
        ("host network", "Am Netz host hängt ein Container ganz oder gar nicht; das legt man beim Anlegen des Containers fest."),
        ("private (none) mode", "Der Container läuft ohne Netz (network_mode: none) und lässt sich mit keinem Netz verbinden."),
        ("sharing network namespace",
         "Der Container nutzt das Netz seines Servers oder eines anderen Containers und lässt sich mit keinem weiteren Netz verbinden."),
        ("not manually attachable", "An dieses Netz lassen sich Container nicht von Hand hängen, etwa an ingress oder ein Swarm-Netz."),
    ),
    DISCONNECT: (
        ("is not connected", "Der Container hängt nicht an diesem Netz."),
        ("host network", "Vom Netz host lässt sich ein Container nicht trennen; das legt man beim Anlegen des Containers fest."),
    ),
}
_GONE = (("no such container", "Den Container gibt es nicht (mehr)."), ("not found", "Das Netz oder der Container ist nicht (mehr) da."))


def _plain(stderr: str, action: str) -> str:
    """Dockers englische Ablehnung als Satz mit Grund; unbekannte bleiben, wie Docker sie schreibt."""
    text = next((line.strip() for line in (stderr or "").splitlines() if line.strip()), "")
    lowered = text.lower()
    return next((plain for marker, plain in (*_REFUSALS.get(action, ()), *_GONE) if marker in lowered), text[:300])


class DockerNetworkExecutor:
    """Führt die drei Netz-Aktionen aus, nur nach dem Gate; jeder Wert wird hier noch einmal geprüft."""

    action_types = frozenset({CREATE, CONNECT, DISCONNECT})

    def __init__(self, ctx: Any, resources: DockerResources) -> None:
        self._ctx = ctx
        self._resources = resources

    async def execute(self, req: ActionRequest) -> ActionResult:
        if req.action_type not in self.action_types:
            return ActionResult(success=False, error=f"Unbekannte Aktion '{req.action_type}'.")
        host = await self._ctx.hosts.get(req.host_ref) if req.host_ref else None
        if host is None:
            return ActionResult(success=False, error="Server nicht gefunden.")
        if why := await still_full(self._ctx, host):
            return ActionResult(success=False, error=why)
        payload = req.payload or {}
        network, container = str(payload.get("network") or ""), str(payload.get("container") or "")
        try:
            if req.action_type == CREATE:
                name = str(payload.get("name") or "")
                argv = cmd_create(name, payload.get("internal") is True)
                done = f"Netz „{name}“ angelegt{' (nur intern)' if payload.get('internal') is True else ''}."
            else:
                if not NETWORK_ID_RE.match(network):
                    raise ValueError("Ungültige Netz-ID.")
                container_id, why = await self._container(host, container, req.action_type)
                if why:
                    return ActionResult(success=False, error=why)
                if req.action_type == CONNECT:
                    argv, done = cmd_connect(network, container_id), f"„{container}“ mit dem Netz verbunden."
                else:
                    argv, done = cmd_disconnect(network, container_id), f"„{container}“ vom Netz getrennt."
        except ValueError as exc:
            return ActionResult(success=False, error=str(exc))
        try:
            result = await self._ctx.exec.run_argv(host, argv, timeout_s=TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - als Grund zeigen
            return ActionResult(success=False, error=f"Server nicht erreichbar: {str(exc) or type(exc).__name__}. Ob Docker "
                                "die Änderung noch ausgeführt hat, zeigt der Reiter Netze nach dem Neuladen.")
        finally:
            self._resources.forget(host.id)
        if result.exit_code != 0:
            return ActionResult(success=False, exit_code=result.exit_code, error=_plain(result.stderr, req.action_type) or None,
                                duration_ms=result.duration_ms)
        return ActionResult(success=True, exit_code=0, output=done, duration_ms=result.duration_ms)

    async def _container(self, host: Any, container: str, action: str) -> tuple[str, str | None]:
        """Volle ID des Containers oder der Grund, warum seine Netze unverändert bleiben."""
        argv = cmd_container(container)
        try:
            found = await self._ctx.exec.run_argv(host, argv, timeout_s=TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - als Grund zeigen
            return "", f"Server nicht erreichbar: {str(exc) or type(exc).__name__}. Nichts geändert."
        if found.exit_code != 0:
            return "", _plain(found.stderr, action) or "Den Container gibt es nicht (mehr)."
        container_id, _, mode = found.stdout.strip().partition("|")
        if not FULL_ID_RE.match(container_id):
            return "", "Die Angaben zum Container ließen sich nicht lesen. Nichts geändert."
        if is_own_container(container_id):
            # Ein neues Netz kann auch Ausgang und Ports der Oberfläche umlegen, nicht nur das Trennen schadet.
            return "", "Das ist der Container von Nodvard Deck selbst. Seine Netze ändert das Modul nicht, damit die Oberfläche erreichbar bleibt."
        if mode == "none" or mode == "host" or mode.startswith(("container:", "service:")):
            return "", ("Der Container nutzt kein eigenes Netz (network_mode: "
                        f"{mode.split(':', 1)[0]}). Seine Netze legt man beim Anlegen des Containers fest.")
        return container_id, None

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        summary = {
            CREATE: f"Würde das Netz „{payload.get('name') or '?'}“ anlegen.",
            CONNECT: f"Würde „{payload.get('container') or '?'}“ mit dem Netz {str(payload.get('network') or '?')[:12]} verbinden.",
            DISCONNECT: f"Würde „{payload.get('container') or '?'}“ vom Netz {str(payload.get('network') or '?')[:12]} trennen.",
        }.get(req.action_type, "?")
        return DryRunReport(would_change=True, summary=summary)
