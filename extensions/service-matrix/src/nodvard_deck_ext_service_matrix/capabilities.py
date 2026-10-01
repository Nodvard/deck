"""Erfuellt `nodvard_sdk.capabilities.ServiceCatalog` ueber `docker ps` per SSH
(`ctx.exec.run()`, derselbe gemeinsame Layer wie ueberall sonst, docs/00 D-05) --
kein Docker-Python-SDK, kein zweiter Ausfuehrungsweg.

Ersetzt die von Hand gepflegten Container-/URL-Listen des Vorgaengersystems (die
veralteten: tote Eintraege, Dienste am falschen Host). Hier gibt es keine Liste zu
pflegen -- jeder laufende Container auf einem
getaggten Host erscheint automatisch, verschwindet automatisch wieder, wenn er
gestoppt/entfernt wird.
"""

from __future__ import annotations

import re
import shlex
import socket
from typing import TYPE_CHECKING, Any

from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Risk

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}\Z")
"""Dockers eigene Namensregel (`[a-zA-Z0-9][a-zA-Z0-9_.-]+`). Jeder Containername, der
in ein SSH-Kommando wandert, MUSS hier durch -- sonst waere `container=x; rm -rf /`
ein Weg an der Sperrliste vorbei in eine Shell. `\\Z` statt `$`: ein Zeilenumbruch am
Ende ist KEIN Treffer."""
COMPOSE_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}\Z")
"""Composes eigene Regel fuer Projektnamen -- wandert wie der Containername in ein
SSH-Kommando und muss deshalb hier durch (ebenfalls mit `\\Z`)."""


def _json_pattern(regex: re.Pattern[str]) -> str:
    """Fuer das JSON-Schema (ECMA-Syntax kennt `\\Z` nicht): `\\Z` -> `$`."""
    return regex.pattern.replace("\\Z", "$")


_PS_FORMAT = """'{{.ID}}|{{.Names}}|{{.State}}|{{.Status}}|{{.Image}}|{{.Label "com.docker.compose.project"}}|{{.Ports}}'"""
"""`.ID`: erkennt den Container, in dem Nodvard Deck
selbst laeuft -- Docker setzt dessen Hostnamen standardmaessig auf die kurze ID.
`.Image` wie in Portainer: welches Image hinter einem Container steckt."""

_STATS_FORMAT = "'{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}'"
_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([KMGTP]?i?B)\s*$", re.IGNORECASE)
_SIZE_FACTOR = {
    "B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
    "KIB": 2**10, "MIB": 2**20, "GIB": 2**30, "TIB": 2**40,
}


def parse_size(text: str) -> int | None:
    """"45.3MiB" -> Byte; Dockers eigene Schreibweise (binaer UND dezimal)."""
    match = _SIZE_RE.match(text or "")
    if match is None:
        return None
    factor = _SIZE_FACTOR.get(match.group(2).upper())
    return int(float(match.group(1)) * factor) if factor else None


def parse_stats_line(line: str) -> tuple[str, dict[str, Any]] | None:
    """Eine Zeile `docker stats --no-stream`. Meldet der Host keinen Speicher
    ("0B / 0B" -- live auf dem Raspberry Pi: dort ist die Speicher-Abrechnung der
    cgroups im Kernel standardmaessig aus), ist RAM UNBEKANNT, nicht 0."""
    parts = line.split("|")
    if len(parts) < 4 or not parts[0].strip():
        return None
    try:
        cpu: float | None = float(parts[1].strip().rstrip("%"))
    except ValueError:
        cpu = None
    used_text, _, limit_text = parts[2].partition("/")
    mem_used, mem_limit = parse_size(used_text), parse_size(limit_text)
    if not mem_limit:
        mem_used = mem_limit = None
    return parts[0].strip(), {"cpu_percent": cpu, "mem_used": mem_used, "mem_limit": mem_limit}


def _own_container_id() -> str:
    return socket.gethostname()


def is_own_container(container_id: str) -> bool:
    """Nodvard Deck laeuft oft selbst als Container auf einem ueberwachten Host --
    "Stoppen" darauf wuerde die Oberflaeche abschalten, aus der man ihn wieder
    starten muesste. Vergleich auf die kurze ID (12 Zeichen, Docker-Hostname-Default)."""
    own = _own_container_id()
    if not container_id or len(own) < 12:
        return False
    return container_id.startswith(own) or own.startswith(container_id)

_PORT_RE = re.compile(r"(?:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)->\d+/tcp")
"""Nimmt bewusst nur IPv4-Host-Bindungen (`0.0.0.0:8080->80/tcp`) -- Dockers
`--format '{{.Ports}}'` listet fuer denselben veroeffentlichten Port oft eine IPv6-
Zwillingszeile (`:::8080->80/tcp`), die zum selben Port fuehrt und sonst zu einer
zweiten, redundanten Kachel fuehren wuerde."""


def _guess_url(ports: str, address: str, scheme: str) -> str | None:
    match = _PORT_RE.search(ports)
    if match is None:
        return None
    return f"{scheme}://{address}:{match.group(1)}"


def _error_tile(host: Any, message: str) -> dict[str, Any]:
    """Nachtrag (Detailseite): `host` trug hier urspruenglich die
    Fehlermeldung (fuer das Widget, dessen `tile_subtitle="{{ host }}"" davon
    profitierte) -- die neue ServiceMatrixPage.tsx gruppiert Eintraege aber NACH
    `host`, eine Fehlermeldung als "Hostname" haette dort eine eigene, falsche
    Gruppe erzeugt. `host` ist jetzt wieder der echte Host-Anzeigename wie bei
    jedem normalen Eintrag, die Meldung steht ausschliesslich in `status` (das die
    Seite bereits als eigene Spalte zeigt; das Widget zeigt sie nicht extra an --
    hinnehmbar, `tone=danger` bleibt dort trotzdem sichtbar)."""
    return {
        "id": f"{host.id}:__error__",
        "name": f"⚠ {host.display_name or host.name}",
        "host": host.display_name or host.name,
        "state": "error",
        "status": message,
        "tone": "danger",
        "url": None,
    }


def _tone_for_state(state: str) -> str:
    if state == "running":
        return "good"
    if state in ("restarting", "paused"):
        return "warn"
    return "neutral"


class DockerServiceCatalog:
    """`list_services()` liefert lose typisierte Dicts (`ServiceCatalog`-Vertrag,
    docs/02 Paragraph 3, kennt keine strengere Form) -- die Felder sind genau die,
    die `StatusGridView`s Templates (`__init__.py`) referenzieren."""

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def list_services(self) -> list[dict[str, Any]]:
        """Ein Host ohne
        laufenden Docker-Daemon (SSH klappt, `docker ps` scheitert) oder ganz
        unerreichbarer Host lieferte bisher STILLE eine leere Liste -- fachlich
        korrekt fuer "keine Container", ununterscheidbar von "der Host ist kaputt".
        Beide Faelle erzeugen jetzt eine eigene, sichtbare `tone="danger"`-Kachel
        statt zu verschwinden -- ein anderer, erreichbarer Host darf davon
        unberuehrt bleiben (deshalb weiterhin pro Host `continue`, nicht `return`)."""
        settings = await self._ctx.settings.get()
        tag = settings.get("docker_host_tag") or "docker"
        scheme = settings.get("url_scheme") or "http"

        services: list[dict[str, Any]] = []
        for host in await self._ctx.hosts.list(tag=tag):
            try:
                result = await self._ctx.exec.run(host, f"docker ps -a --format {_PS_FORMAT}", timeout_s=10)
            except Exception as exc:  # noqa: BLE001 - ein nicht erreichbarer Host darf die anderen nicht verstecken
                services.append(_error_tile(host, f"Nicht erreichbar: {exc}"))
                continue
            if result.exit_code != 0:
                detail = result.stderr.strip() or f"Exit-Code {result.exit_code}"
                services.append(_error_tile(host, f"docker ps fehlgeschlagen: {detail}"))
                continue

            for line in result.stdout.splitlines():
                # Seit Roadmap Punkt 2 mit Compose-Projekt vor den Ports (7 Felder);
                # 6 Felder = altes Format ohne Projekt, weiterhin gelesen.
                parts = line.split("|", 6)
                if len(parts) < 5:
                    continue
                container_id = parts[0].strip()
                name = parts[1].strip()
                state = parts[2].strip().lower()
                status_str = parts[3].strip()
                image = parts[4].strip()
                project = parts[5].strip() if len(parts) > 6 else ""
                ports = (parts[6] if len(parts) > 6 else parts[5] if len(parts) > 5 else "").strip()
                if not name:
                    continue
                services.append(
                    {
                        "id": f"{host.id}:{name}",
                        "name": name,
                        "host": host.display_name or host.name,
                        # Was die Seite fuer Aktionen
                        # (POST /hosts/{host_id}/actions/container.*) und Logs braucht.
                        "host_id": host.id,
                        "container": name,
                        "is_self": is_own_container(container_id),
                        "image": image,
                        "compose_project": project or None,
                        "state": state,
                        "status": status_str,
                        "tone": _tone_for_state(state),
                        "url": _guess_url(ports, host.address, scheme),
                    }
                )
        return services

    async def container_stats(self) -> dict[str, dict[str, Any]]:
        """CPU/RAM je LAUFENDEM Container (`docker stats --no-stream`, dauert je Host
        ~2-3s, weil Docker eine Messperiode abwartet) -- deshalb ein eigener Aufruf,
        nicht Teil der Liste. Schluessel wie `list_services()`s `id`. Ein Host, der
        nicht antwortet, fehlt einfach (die Liste zeigt seinen Fehler schon)."""
        settings = await self._ctx.settings.get()
        tag = settings.get("docker_host_tag") or "docker"
        stats: dict[str, dict[str, Any]] = {}
        for host in await self._ctx.hosts.list(tag=tag):
            try:
                result = await self._ctx.exec.run(host, f"docker stats --no-stream --format {_STATS_FORMAT}", timeout_s=20)
            except Exception:  # noqa: BLE001 - ein kaputter Host darf die anderen nicht verdecken
                continue
            if result.exit_code != 0:
                continue
            for line in result.stdout.splitlines():
                parsed = parse_stats_line(line)
                if parsed is not None:
                    name, values = parsed
                    stats[f"{host.id}:{name}"] = values
        return stats


_VERBS = {"container.start": "start", "container.stop": "stop", "container.restart": "restart"}
_VERB_LABEL = {"start": "starten", "stop": "stoppen", "restart": "neu starten"}
_PARAMS_SCHEMA = {
    "type": "object",
    "properties": {"container": {"type": "string", "pattern": _json_pattern(CONTAINER_NAME_RE)}},
    "required": ["container"],
}

def container_action_specs(tag: str = "docker") -> list[ActionSpec]:
    """Nur fuer Docker-Hosts (`host_tags`) -- der Tag ist einstellbar, deshalb eine
    Funktion: bei geaenderter Einstellung meldet die Extension die Specs neu an."""
    return [
        ActionSpec(
            action_type="container.start", label="Container starten", icon="play",
            description="Startet einen gestoppten Docker-Container auf diesem Host.",
            default_risk=Risk.LOW, permissions=["hosts.execute"], host_bound=True, params_schema=_PARAMS_SCHEMA,
            host_tags=[tag],
        ),
        ActionSpec(
            action_type="container.stop", label="Container stoppen", icon="square",
            description="Stoppt einen laufenden Docker-Container auf diesem Host.",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, params_schema=_PARAMS_SCHEMA,
            confirm_text="Der Dienst ist danach nicht mehr erreichbar, bis er wieder gestartet wird. Fortfahren?",
            host_tags=[tag],
        ),
        ActionSpec(
            action_type="container.restart", label="Container neu starten", icon="rotate-cw",
            description="Startet einen Docker-Container auf diesem Host neu.",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, params_schema=_PARAMS_SCHEMA,
            host_tags=[tag],
        ),
        # Roadmap Punkt 2 (Portainer-Ersatz): Speicher aufraeumen. Feste Befehle, kein
        # Nutzertext -- deshalb kein command_field.
        ActionSpec(
            action_type="docker.prune_images", label="Verwaiste Images entfernen", icon="trash-2",
            description="Entfernt Images ohne Namen, die kein Container mehr nutzt (docker image prune).",
            default_risk=Risk.LOW, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
        ),
        ActionSpec(
            action_type="docker.prune_unused_images", label="Ungenutzte Images entfernen", icon="trash-2",
            description="Entfernt alle Images, die kein Container nutzt -- auch benannte (docker image prune -a).",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            confirm_text="Entfernt auch benannte Images ohne Container. Werden sie wieder gebraucht, lädt Docker sie neu herunter. Fortfahren?",
        ),
        ActionSpec(
            action_type="docker.stack_restart", label="Stack neu starten", icon="rotate-cw",
            description="Startet alle Container eines Docker-Compose-Projekts neu (docker compose -p ... restart).",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            params_schema={"type": "object", "properties": {"project": {"type": "string", "pattern": _json_pattern(COMPOSE_PROJECT_RE)}}, "required": ["project"]},
            confirm_text="Alle Container des Stacks starten neu -- die Dienste sind kurz nicht erreichbar. Fortfahren?",
        ),
        ActionSpec(
            action_type="docker.prune_build_cache", label="Build-Cache leeren", icon="eraser",
            description="Leert den Zwischenspeicher von docker build (docker builder prune).",
            default_risk=Risk.MEDIUM, permissions=["hosts.execute"], host_bound=True, host_tags=[tag],
            confirm_text="Der nächste Build auf diesem Host dauert dann deutlich länger. Fortfahren?",
        ),
    ]


CONTAINER_ACTION_SPECS = container_action_specs()
_PRUNE_COMMANDS = {
    "docker.prune_images": "docker image prune -f",
    "docker.prune_unused_images": "docker image prune -a -f",
    "docker.prune_build_cache": "docker builder prune -f",
}


class DockerMaintenanceExecutor:
    """Aufraeumen (`docker.prune_*`) -- nur nach dem Gate, feste Befehle."""

    action_types = frozenset(_PRUNE_COMMANDS) | {"docker.stack_restart"}

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def execute(self, req: ActionRequest) -> ActionResult:
        if req.action_type == "docker.stack_restart":
            project = str(req.payload.get("project") or "")
            if not COMPOSE_PROJECT_RE.match(project):
                return ActionResult(success=False, error=f"Ungültiger Projektname: {project!r}")
            host = await self._ctx.hosts.get(req.host_ref) if req.host_ref else None
            if host is None:
                return ActionResult(success=False, error="Host nicht gefunden.")
            # Ohne Compose-Datei: Compose v2+ findet die Container ueber ihre Labels
            # (live geprueft mit Compose 5.5.1 auf dem Pi).
            result = await self._ctx.exec.run(host, f"docker compose -p {shlex.quote(project)} restart", timeout_s=300)
            return ActionResult(
                success=result.exit_code == 0, exit_code=result.exit_code,
                output=f"Stack '{project}' neu gestartet." if result.exit_code == 0 else None,
                error=result.stderr.strip() or None, duration_ms=result.duration_ms,
            )
        command = _PRUNE_COMMANDS.get(req.action_type)
        if command is None:
            return ActionResult(success=False, error=f"Unbekannte Aktion '{req.action_type}'.")
        if not req.host_ref:
            return ActionResult(success=False, error="Aktion ohne Host-Referenz.")
        host = await self._ctx.hosts.get(req.host_ref)
        if host is None:
            return ActionResult(success=False, error=f"Host '{req.host_ref}' nicht gefunden.")
        result = await self._ctx.exec.run(host, command, timeout_s=600)
        # Docker schliesst mit "Total reclaimed space: 1.2GB" -- das ist die Nachricht.
        reclaimed = next((line for line in reversed(result.stdout.splitlines()) if "reclaimed" in line.lower()), None)
        return ActionResult(
            success=result.exit_code == 0,
            exit_code=result.exit_code,
            output=(f"Freigegeben: {reclaimed.split(':', 1)[1].strip()}" if reclaimed and ":" in reclaimed else result.stdout.strip() or None),
            error=result.stderr.strip() or None,
            duration_ms=result.duration_ms,
        )

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return DryRunReport(would_change=True, summary=f"Würde ausführen: {_PRUNE_COMMANDS.get(req.action_type, '?')}")


class DockerContainerExecutor:
    """Erfuellt `nodvard_sdk.capabilities.ActionExecutor` fuer Start/Stop/Neustart
    einzelner Container (damit braucht es fuer den Alltag weder Portainer noch eine
    Konsole auf dem Docker-Host). Laeuft wie jede Aktion NUR nach dem Gate -- die
    Seite loest ueber `POST /hosts/{id}/actions/container.*` aus, der Nutzer steht
    damit als Ausloeser im Journal, nicht die Extension.

    Der Befehl entsteht hier aus einem festen Verb und einem gegen Dockers
    Namensregel geprueften Namen -- kein freier Befehlstext im Payload, deshalb auch
    kein `command_field`."""

    action_types = frozenset(_VERBS)

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def execute(self, req: ActionRequest) -> ActionResult:
        verb = _VERBS.get(req.action_type)
        if verb is None:
            return ActionResult(success=False, error=f"Unbekannte Aktion '{req.action_type}'.")
        name = str(req.payload.get("container") or "")
        if not CONTAINER_NAME_RE.match(name):
            return ActionResult(success=False, error=f"Ungültiger Containername: {name!r}")
        if not req.host_ref:
            return ActionResult(success=False, error="Aktion ohne Host-Referenz.")
        host = await self._ctx.hosts.get(req.host_ref)
        if host is None:
            return ActionResult(success=False, error=f"Host '{req.host_ref}' nicht gefunden.")

        if verb == "stop":
            inspected = await self._ctx.exec.run(host, f"docker inspect -f '{{{{.Id}}}}' {name}", timeout_s=15)
            if inspected.exit_code == 0 and is_own_container(inspected.stdout.strip()):
                return ActionResult(
                    success=False,
                    error=f"'{name}' ist der Container, in dem Nodvard Deck selbst läuft -- Stoppen würde "
                    "diese Oberfläche abschalten. Neustarten geht, Stoppen nur direkt auf dem Host.",
                )

        result = await self._ctx.exec.run(host, f"docker {verb} {name}", timeout_s=120)
        return ActionResult(
            success=result.exit_code == 0,
            exit_code=result.exit_code,
            output=result.stdout or None,
            error=result.stderr.strip() or None,
            duration_ms=result.duration_ms,
        )

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        verb = _VERBS.get(req.action_type, "?")
        name = req.payload.get("container", "?")
        return DryRunReport(would_change=True, summary=f"Würde Container '{name}' {_VERB_LABEL.get(verb, verb)}.")
