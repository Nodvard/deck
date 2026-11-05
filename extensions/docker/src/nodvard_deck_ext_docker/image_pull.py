"""Ein Image über die Freigabe von seiner Registry holen (docker pull), ohne einen Container anzufassen."""

from __future__ import annotations

from typing import Any

from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Risk

from .capabilities import _json_pattern
from .image_updates import (
    IMAGE_REF_MAX_LEN,
    IMAGE_REF_RE,
    classify_registry_error,
    is_valid_ref,
)
from .operate import still_full
from .resources import DockerResources

PULL = "docker.image_pull"
TIMEOUT_S = 1800

_SCHEMA = {
    "type": "object",
    "properties": {"image": {"type": "string", "maxLength": IMAGE_REF_MAX_LEN, "pattern": _json_pattern(IMAGE_REF_RE)}},
    "required": ["image"],
}


def image_pull_specs(tag: str = "docker") -> list[ActionSpec]:
    """Nur für Server mit dem Docker-Tag; im Modus „Nur bedienen“ lehnt der Ausführer ab."""
    return [
        ActionSpec(
            action_type=PULL, label="Image holen", icon="download",
            description="Lädt ein Image von seiner Registry herunter (docker pull). Laufende Container bleiben, wie sie sind.",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            params_schema=_SCHEMA,
            confirm_text="Docker lädt das Image herunter. Das braucht Platz auf dem Server, laufende Container ändern sich nicht. "
            "Fortfahren?",
        ),
    ]


def cmd_pull(image: str) -> list[str]:
    if not is_valid_ref(image):
        raise ValueError("Der Name des Images ist ungültig, etwa nginx:1.27 oder ghcr.io/besitzer/name:1.0.")
    return ["docker", "pull", "-q", image]


_PULL_TEXT = {
    "rate_limit": "Die Registry lässt gerade keine weiteren Abrufe zu (Abrufgrenze, bei Docker Hub etwa für Abrufe ohne "
    "Anmeldung). Später noch einmal versuchen oder unter Registries anmelden.",
    "auth": "Die Registry gibt das Image nicht heraus: Es ist privat, der Name ist falsch geschrieben, oder die Anmeldung "
    "fehlt. Schreibweise prüfen oder unter Registries anmelden.",
    "not_found": "Diesen Tag gibt es bei der Registry nicht. Schreibweise und Tag prüfen.",
    "network": "Die Registry ist vom Server aus nicht erreichbar. Internet und DNS des Servers prüfen.",
}


def _reason(stderr: str, exit_code: int) -> str:
    """Warum `docker pull` scheiterte, als Satz mit nächstem Schritt."""
    low = (stderr or "").lower()
    if "cannot connect to the docker daemon" in low or "permission denied while trying to connect" in low:
        return "Docker läuft auf dem Server nicht, oder der Zugang darf Docker nicht benutzen (Gruppe docker)."
    if "http response to https client" in low or "x509" in low or "certificate" in low:
        return ("Die Registry spricht nur HTTP oder hat kein gültiges Zertifikat. Auf dem Server bei Docker unter "
                "„insecure-registries“ eintragen oder ein Zertifikat einrichten.")
    if "invalid reference format" in low:
        return "Docker versteht den Namen nicht. Eine eigene Registry braucht einen Punkt oder Port, etwa registry.lan:5000/app."
    if "no space left" in low:
        return "Auf dem Server ist kein Platz mehr frei. Unter „Docker-Speicher“ ungenutzte Images entfernen und noch einmal versuchen."
    if "no matching manifest" in low:
        return "Das Image gibt es nicht für die Bauart dieses Servers (etwa arm64 auf einem Raspberry Pi). Ein anderes Image oder einen anderen Tag wählen."
    problem = classify_registry_error(stderr, exit_code)
    if problem.kind in _PULL_TEXT:
        return _PULL_TEXT[problem.kind]
    line = next((ln.strip() for ln in (stderr or "").splitlines() if ln.strip()), "")
    return f"Docker meldet: {line[:300] or f'Exit-Code {exit_code}'}"


class DockerImagePullExecutor:
    """Holt ein Image, nur nach dem Gate; die Referenz wird hier noch einmal geprüft."""

    action_types = frozenset({PULL})

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
        image = str((req.payload or {}).get("image") or "")
        try:
            argv = cmd_pull(image)
        except ValueError as exc:
            return ActionResult(success=False, error=str(exc))
        try:
            result = await self._ctx.exec.run_argv(host, argv, timeout_s=TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - als Grund zeigen
            if type(exc) is TimeoutError:
                return ActionResult(success=False, error=f"Nach {TIMEOUT_S // 60} Minuten ohne Ende abgebrochen. Docker kann auf dem "
                                    "Server weiter laden; ob das Image da ist, zeigt der Reiter Images nach dem Neuladen.")
            why = str(exc).strip() or type(exc).__name__
            return ActionResult(success=False, error=f"Server nicht erreichbar: {why}{'' if why.endswith(('.', '!', '?')) else '.'} "
                                "Ob Docker das Image trotzdem geholt hat, zeigt der Reiter Images nach dem Neuladen.")
        finally:
            self._resources.forget(host.id)
        if result.exit_code != 0:
            return ActionResult(success=False, exit_code=result.exit_code, error=_reason(result.stderr, result.exit_code),
                                duration_ms=result.duration_ms)
        return ActionResult(success=True, exit_code=0, output=f"Image „{image}“ geholt.", duration_ms=result.duration_ms)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return DryRunReport(would_change=True, summary=f"Würde das Image „{(req.payload or {}).get('image') or '?'}“ holen.")
