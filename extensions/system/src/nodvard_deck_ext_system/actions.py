"""Dienst neu starten -- die einzige verändernde Aktion der System-Erweiterung.

Läuft durch das Aktions-Gate des Kerns: die Seite schlägt vor (`ctx.actions.propose`),
wer freigeben darf, gibt selbst frei, erst dann ruft der Kern `SystemActionExecutor`.

Der Payload (`host_id` + `unit`) steht zwischen Vorschlag und Ausführung in der
Datenbank und wird deshalb im Executor NOCHMAL streng geprüft. Der Befehl entsteht
nur hier, aus dem geprüften Einheitennamen (mit `shlex.quote`) -- nie aus einem
Text, den jemand mitgeschickt hat.
"""

from __future__ import annotations

import re
import shlex
import socket
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from nodvard_sdk import ActionResult, Actor, DryRunReport, Risk
from nodvard_sdk.actions import REQUEST_WAIT_S, ActionRequest, ActionSpec
from pydantic import BaseModel

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext, Host

SERVICE_RESTART = "system.service_restart"

ACTION_SPECS = [
    ActionSpec(
        action_type=SERVICE_RESTART,
        label="Dienst neu starten",
        description="Startet einen systemd-Dienst auf einem Server neu (systemctl restart). Wichtige Systemdienste sind gesperrt.",
        icon="cpu",
        default_risk=Risk.MEDIUM,
        permissions=["hosts.execute"],
        host_bound=False,
        confirm_text="Der Dienst ist kurz nicht erreichbar. Fortfahren?",
    ),
]

# Erlaubt ist nur, was systemd als Einheitenname kennt (ohne Platzhalter wie * ? [ ]).
# Kein Bindestrich am Anfang, sonst läse `systemctl` den Namen als Option.
UNIT_RE = re.compile(r"[A-Za-z0-9@._:][A-Za-z0-9@._:-]{0,127}\.(?:service|socket|timer)")
MAX_HOST_ID_LEN = 64

# Sperrliste: was sich nicht "mal eben" neu starten lässt, ohne den Server oder den
# Weg dorthin zu kappen. Verglichen wird der Name ohne Endung und ohne Instanz
# ("getty@tty1.service" -> "getty"), klein geschrieben.
DENY_SSH = (frozenset({"ssh", "sshd"}), ("sshd-", "ssh-"))
DENY_SYSTEM = (frozenset({"dbus", "dbus-broker"}), ("systemd-", "dbus-"))
DENY_NETWORK = (frozenset({"networking", "networkmanager", "dhcpcd", "wpa_supplicant"}), ())
# Das Dashboard selbst (systemd-Dienst "lattice…" oder "nodvard-deck…"): auf jedem Server
# gesperrt, weil ein Neustart mitten in der Aktion die Oberfläche abschaltet. Der blanke
# Name "nodvard" ist nur als Tippvariante gesperrt, nicht als Präfix: andere Nodvard-Dienste
# (etwa "nodvard-link") bleiben neu startbar.
DENY_DASHBOARD = (frozenset({"nodvard"}), ("lattice", "nodvard-deck"))
# Server-Markierungen (Tags), die einen Server als Dashboard-Rechner kennzeichnen.
DASHBOARD_HOST_TAGS = ("dashboard", "lattice", "nodvard-deck")
# Ein Neustart von Docker oder containerd nimmt alle Container mit -- auf dem Server,
# auf dem das Dashboard läuft, also auch das Dashboard.
DENY_ON_DASHBOARD_HOST = (frozenset({"docker", "containerd"}), ("docker-", "containerd-"))


def _matches(name: str, rule: tuple[frozenset[str], tuple[str, ...]]) -> bool:
    exact, prefixes = rule
    return name in exact or (bool(prefixes) and name.startswith(prefixes))


REFUSAL_SSH = "Der SSH-Dienst wird hier nicht neu gestartet – darüber erreicht das Dashboard den Server. Geht dabei etwas schief, ist er ausgesperrt."
REFUSAL_SYSTEM = "Systemdienste (systemd-…, dbus) werden hier nicht neu gestartet – das kann den ganzen Server lahmlegen."
REFUSAL_NETWORK = "Netzwerkdienste werden hier nicht neu gestartet – die Verbindung zum Server könnte abreißen."
REFUSAL_DASHBOARD = "Das Dashboard startet sich nicht selbst neu – dabei würde diese Seite mitten in der Aktion wegbrechen."
REFUSAL_DASHBOARD_HOST = "Auf diesem Server läuft das Dashboard selbst. Ein Neustart von Docker würde auch das Dashboard beenden."
REFUSAL_UNKNOWN_HOST = (
    "Konnte nicht prüfen, ob auf diesem Server das Dashboard läuft (Docker-Rechte fehlen oder Docker antwortet nicht) – "
    "Docker wird deshalb nicht neu gestartet. Tipp: dem Dashboard-Server den Tag „dashboard“ geben."
)

NO_ROOT = "@@noroot"
NO_ROOT_MESSAGE = (
    "Auf diesem Server fehlen die Rechte: das Dashboard ist dort nicht als root angemeldet, und „sudo“ ohne "
    "Passwort ist nicht eingerichtet. Bitte für den Dashboard-Benutzer einrichten und noch einmal versuchen."
)


class PayloadError(ValueError):
    pass


def _base_name(unit: str) -> str:
    return unit.rsplit(".", 1)[0].split("@", 1)[0].lower()


def validate_unit(value: Any) -> str:
    """Nur ganz normale Dienst-/Socket-/Timer-Namen. `fullmatch`, damit auch ein
    angehängter Zeilenumbruch durchfällt."""
    if not isinstance(value, str) or UNIT_RE.fullmatch(value) is None:
        raise PayloadError("Ungültiger Dienstname – erlaubt sind nur Namen wie „nginx.service“.")
    return value


def deny_reason(unit: str, *, dashboard_host: bool | None) -> str | None:
    """Warum `unit` nicht neu gestartet wird -- deutscher Text, oder `None`.

    `dashboard_host`: läuft das Dashboard auf diesem Server? `None` = nicht
    feststellbar (dann bleibt Docker vorsichtshalber gesperrt)."""
    name = _base_name(unit)
    for rule, text in ((DENY_SSH, REFUSAL_SSH), (DENY_SYSTEM, REFUSAL_SYSTEM), (DENY_NETWORK, REFUSAL_NETWORK), (DENY_DASHBOARD, REFUSAL_DASHBOARD)):
        if _matches(name, rule):
            return text
    if _matches(name, DENY_ON_DASHBOARD_HOST):
        if dashboard_host is None:
            return REFUSAL_UNKNOWN_HOST
        if dashboard_host:
            return REFUSAL_DASHBOARD_HOST
    return None


def is_restartable(unit: str, *, dashboard_host: bool | None = False) -> bool:
    """Darf `unit` grundsätzlich neu gestartet werden? Rein nach Namen, ohne Rückfrage beim
    Server -- für die Knöpfe der Seite. Ohne Angabe gilt Docker als möglich; wer schon weiß,
    ob auf dem Server das Dashboard läuft (`is_dashboard_host`), reicht das durch."""
    try:
        validate_unit(unit)
    except PayloadError:
        return False
    return deny_reason(unit, dashboard_host=dashboard_host) is None


def needs_dashboard_check(unit: str) -> bool:
    return _matches(_base_name(unit), DENY_ON_DASHBOARD_HOST)


def validate_payload(payload: Any) -> tuple[str, str]:
    """`{"host_id": ..., "unit": ...}` -- genau diese beiden Felder, sonst nichts."""
    if not isinstance(payload, dict):
        raise PayloadError("Ungültige Aktion: Daten fehlen.")
    extra = sorted(set(payload) - {"host_id", "unit"})
    if extra:
        raise PayloadError(f"Ungültige Aktion: unerwartete Angaben ({', '.join(extra)}).")
    host_id = payload.get("host_id")
    if not isinstance(host_id, str) or not 1 <= len(host_id) <= MAX_HOST_ID_LEN or not host_id.isprintable() or host_id != host_id.strip():
        raise PayloadError("Ungültige Aktion: der Server fehlt.")
    return host_id, validate_unit(payload.get("unit"))


def as_root(command: str) -> str:
    """Führt `command` als root aus: direkt, wenn schon root, sonst über `sudo -n`
    (ohne Passwortabfrage). Geht beides nicht, bricht es mit NO_ROOT ab -- ein
    Neustart als normaler Benutzer hätte ohnehin keine Wirkung."""
    inner = shlex.quote(command)
    return (
        f"if [ \"$(id -u)\" -eq 0 ]; then sh -c {inner}; "
        f"elif sudo -n true 2>/dev/null; then sudo -n sh -c {inner}; "
        f"else echo '{NO_ROOT}'; exit 126; fi"
    )


def restart_command(unit: str) -> str:
    """Neu starten, Rückgabewert festhalten, danach den Zustand nennen. `--` schützt
    zusätzlich davor, dass ein Name als Option gelesen wird."""
    quoted = shlex.quote(validate_unit(unit))
    return f"systemctl restart -- {quoted}; echo \"@@rc=$?\"; systemctl is-active -- {quoted}"


def own_container_probe(container_id: str) -> str:
    """Läuft auf dem Ziel-Server ein Container mit unserer Kennung? Das Dashboard läuft
    als Docker-Container, dessen Hostname die kurze Container-ID ist.

    Drei Antworten: `@@own` (gefunden), `@@other` (Docker antwortet, den Container gibt es
    dort nicht) und `@@unknown` -- keine Rechte, Docker-Dienst nicht erreichbar oder kein
    docker-Programm. Nur "gibt es nicht" darf als "anderer Server" gelten: ein abgestürzter
    dockerd lässt die Container unter containerd weiterlaufen."""
    quoted = shlex.quote(container_id)
    inspect = f"docker inspect --format '{{{{.Id}}}}' {quoted}"
    missing = "*'No such object'*|*'No such container'*|*'No such image, container'*"
    return (
        f'o=$({inspect} 2>&1) && echo @@own || case "$o" in {missing}) echo @@other;; '
        f'*) o=$(sudo -n {inspect} 2>&1) && echo @@own || case "$o" in {missing}) echo @@other;; '
        f"*) echo @@unknown;; esac;; esac"
    )


async def is_dashboard_host(ctx: ExtensionContext, host: Host) -> bool | None:
    """Läuft das Dashboard auf `host`? `True`/`False`, `None` wenn nicht feststellbar."""
    if any(tag in DASHBOARD_HOST_TAGS for tag in host.tags):
        return True
    own = socket.gethostname().strip()
    if not own or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", own):
        return None
    try:
        result = await ctx.exec.run(host, own_container_probe(own), timeout_s=20)
    except Exception:  # noqa: BLE001 - nicht erreichbar heisst: unbekannt, nicht "nein"
        return None
    if "@@own" in result.stdout:
        return True
    if "@@other" in result.stdout:
        return False
    return None


async def refusal_for(ctx: ExtensionContext, host: Host, unit: str) -> str | None:
    dashboard = await is_dashboard_host(ctx, host) if needs_dashboard_check(unit) else False
    return deny_reason(unit, dashboard_host=dashboard)


def host_name(host: Host) -> str:
    return host.display_name or host.name


def parse_restart_output(stdout: str) -> tuple[int | None, str]:
    """`@@rc=<n>` und die Zeile von `systemctl is-active` danach."""
    rc: int | None = None
    state = ""
    seen_rc = False
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if line.startswith("@@rc="):
            rc = int(line[5:]) if line[5:].isdigit() else None
            seen_rc = True
        elif seen_rc and line:
            state = line
    return rc, state


class SystemActionExecutor:
    """Erfüllt `nodvard_sdk.capabilities.ActionExecutor`."""

    action_types = frozenset({SERVICE_RESTART})

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def _resolve(self, req: ActionRequest) -> tuple[Host, str]:
        host_id, unit = validate_payload(req.payload)
        if req.host_ref and req.host_ref != host_id:
            raise PayloadError("Ungültige Aktion: der Server passt nicht zum Vorschlag.")
        host = await self._ctx.hosts.get(host_id)
        if host is None:
            raise PayloadError("Server nicht gefunden.")
        if host.os_family != "linux":
            raise PayloadError("Dienste lassen sich nur auf Linux-Servern neu starten.")
        refusal = await refusal_for(self._ctx, host, unit)
        if refusal:
            raise PayloadError(refusal)
        return host, unit

    async def execute(self, req: ActionRequest) -> ActionResult:
        if req.action_type != SERVICE_RESTART:
            return ActionResult(success=False, error=f"Unbekannte Aktion '{req.action_type}'.")
        try:
            host, unit = await self._resolve(req)
        except PayloadError as exc:
            return ActionResult(success=False, error=str(exc))
        where = host_name(host)
        result = await self._ctx.exec.run(host, as_root(restart_command(unit)), timeout_s=90)
        if NO_ROOT in (result.stdout or ""):
            return ActionResult(success=False, exit_code=result.exit_code, error=NO_ROOT_MESSAGE)
        rc, state = parse_restart_output(result.stdout)
        detail = {"host_id": host.id, "unit": unit, "state": state or None}
        if rc != 0:
            reason = (result.stderr or "").strip()[-500:] or f"systemctl meldet Fehler {rc if rc is not None else '(keine Antwort)'}"
            return ActionResult(success=False, exit_code=result.exit_code, error=f"{unit} auf {where} ließ sich nicht neu starten: {reason}", detail=detail)
        if state == "failed":
            return ActionResult(success=False, exit_code=result.exit_code, error=f"{unit} auf {where} wurde neu gestartet, ist aber gleich wieder ausgefallen (Zustand: failed). Das Protokoll des Dienstes zeigt den Grund.", detail=detail)
        if state in ("active", "activating", "reloading"):
            text = f"{unit} auf {where} neu gestartet – Zustand jetzt: {state}."
        else:
            text = f"{unit} auf {where} neu gestartet – Zustand jetzt: {state or 'unbekannt'} (bei Diensten, die nur einmal etwas erledigen, ist das normal)."
        return ActionResult(success=True, exit_code=result.exit_code, output=text, detail=detail)

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        if req.action_type != SERVICE_RESTART:
            return None
        try:
            host, unit = await self._resolve(req)
        except PayloadError as exc:
            return DryRunReport(would_change=False, summary=str(exc))
        return DryRunReport(would_change=True, summary=f"Würde {unit} auf {host_name(host)} neu starten.")


class _RestartIn(BaseModel):
    unit: str


def build_action_router(ctx: ExtensionContext) -> APIRouter:
    """Die eine Route der Seite: prüft vorab dasselbe wie der Executor (damit Nutzer
    die Ablehnung sofort sehen) und schlägt dann ans Gate vor."""
    router = APIRouter()

    @router.post("/hosts/{host_id}/services/restart")
    async def restart_service(
        payload: _RestartIn,
        host_id: str = Path(min_length=1, max_length=MAX_HOST_ID_LEN),
        actor: Actor = Depends(ctx.api.current_actor),  # noqa: B008
    ) -> dict[str, Any]:
        host = await ctx.hosts.get(host_id)
        if host is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
        if host.os_family != "linux":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Dienste lassen sich nur auf Linux-Servern neu starten.")
        try:
            unit = validate_unit(payload.unit)
        except PayloadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        refusal = await refusal_for(ctx, host, unit)
        if refusal:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal)
        decision = await ctx.actions.propose(
            ActionRequest(
                action_type=SERVICE_RESTART,
                payload={"host_id": host.id, "unit": unit},
                host_ref=host.id,
                risk=Risk.MEDIUM,
                proposed_by=actor,
                reason=f"Dienst {unit} auf {host_name(host)} über die System-Seite neu gestartet.",
                correlation_id="system:restart",  # höchstens 36 Zeichen; Server und Dienst stehen im Payload
            ),
            wait_s=REQUEST_WAIT_S,
        )
        out: dict[str, Any] = {"action_id": decision.action_id, "status": decision.status.value, "risk": Risk.MEDIUM.value, "detail": decision.detail}
        if decision.status.value in ("succeeded", "failed"):
            row = await ctx.actions.result(decision.action_id)
            out["result"] = row.result if row is not None else None
        return out

    return router
