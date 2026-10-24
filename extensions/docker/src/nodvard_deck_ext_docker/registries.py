"""Private Registries: Zugangsdaten liegen im Tresor, angemeldet wird je Server mit `docker login --password-stdin`, sodass das
Passwort nie auf der Kommandozeile steht. Gezeigt wird nur, bei welchen Registries ein Server angemeldet ist."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.errors import HostUnreachable, SecretUnavailable
from nodvard_sdk.types import Actor, Risk

from .editor import StackEditor
from .operate import not_full_host
from .stack_actions import _connection_error, failure_line
from .stacks import no_such_file

LOGIN_ACTION = "docker.registry_login"
LOGOUT_ACTION = "docker.registry_logout"
SECRET_PREFIX = "docker-registry-pw:"
LOGIN_TIMEOUT_S = 120
MAX_CONFIG_BYTES = 256 * 1024
STORED_NOTE = (
    "Docker merkt sich die Anmeldung auf dem Server in ~/.docker/config.json des Zugangs, wie bei docker login von Hand; "
    "„Abmelden“ entfernt sie wieder."
)
LOOK = "Den Stand zeigt die Liste nach dem Neuladen."
NOT_LISTED = "Diese Registry ist nicht (mehr) eingetragen. Bitte die Einstellungen prüfen."
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
# Ein Port liegt zwischen 1 und 65535, ohne führende Null.
_ADDRESS_RE = re.compile(
    r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?(:([1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5]))?\Z")
_USER_RE = re.compile(r"^\S{1,255}\Z")
DOCKER_HUB_KEYS = ("https://index.docker.io/v1/", "index.docker.io", "docker.io", "registry-1.docker.io")


@dataclass(frozen=True)
class Registry:
    name: str
    address: str
    username: str

    @property
    def secret_label(self) -> str:
        return f"{SECRET_PREFIX}{self.name}"

    @property
    def login_address(self) -> str:
        """Für Docker Hub nennt man keine Adresse; `docker login` nimmt dann die eigene."""
        return "" if self.address in ("docker.io", "index.docker.io", "registry-1.docker.io") else self.address


def usable(registry: Registry) -> bool:
    return bool(_NAME_RE.match(registry.name) and _ADDRESS_RE.match(registry.address) and _USER_RE.match(registry.username))


def registries(settings: dict[str, Any]) -> list[Registry]:
    """Die gültigen Einträge aus den Einstellungen; ein ungültiger fällt weg, ein doppelter Name mit allen seinen Einträgen."""
    out = []
    for raw in settings.get("registries") or []:
        if not isinstance(raw, dict):
            continue
        entry = Registry(str(raw.get("name") or ""), str(raw.get("address") or ""), str(raw.get("username") or ""))
        if usable(entry):
            out.append(entry)
    # Das Passwort hängt am Namen; bei zwei gleichen Namen wäre offen, zu welcher Adresse es gehört.
    twice = duplicate_names(settings)
    return [entry for entry in out if entry.name not in twice]


def duplicate_names(settings: dict[str, Any]) -> set[str]:
    names = [str(raw.get("name") or "") for raw in settings.get("registries") or [] if isinstance(raw, dict)]
    return {name for name in names if names.count(name) > 1}


def skipped_names(settings: dict[str, Any]) -> list[str]:
    """Namen der Einträge, die nicht wegen eines doppelten Namens, sondern wegen eines ungültigen Felds wegfallen."""
    kept, twice = {entry.name for entry in registries(settings)}, duplicate_names(settings)
    names = [str(raw.get("name") or "") if isinstance(raw, dict) else "" for raw in settings.get("registries") or []]
    return [name for name in names if name not in kept and name not in twice]


def login_spec() -> list[ActionSpec]:
    common: dict[str, Any] = {"permissions": ["hosts.execute"], "host_bound": False}
    return [
        ActionSpec(
            action_type=LOGIN_ACTION, label="Bei einer Registry anmelden", icon="key", default_risk=Risk.MEDIUM,
            description="Meldet Docker auf einem Server bei einer privaten Registry an; das Passwort kommt aus dem Tresor.",
            confirm_text="Docker auf diesem Server darf danach Images aus dieser Registry holen. Fortfahren?", **common,
        ),
        ActionSpec(
            action_type=LOGOUT_ACTION, label="Bei einer Registry abmelden", icon="log-out", default_risk=Risk.LOW,
            description="Entfernt die Anmeldung von Docker auf einem Server bei einer Registry.", **common,
        ),
    ]


def cmd_login(registry: Registry) -> list[str]:
    """Das Passwort kommt über die Standardeingabe, nie als Argument."""
    if not _ADDRESS_RE.match(registry.address) or not _USER_RE.match(registry.username):
        raise ValueError(f"Ungültige Registry: {registry!r}")
    return ["docker", "login", f"--username={registry.username}", "--password-stdin", *([registry.login_address] if registry.login_address else [])]


def cmd_logout(registry: Registry) -> list[str]:
    if not _ADDRESS_RE.match(registry.address):
        raise ValueError(f"Ungültige Registry: {registry!r}")
    return ["docker", "logout", *([registry.login_address] if registry.login_address else [])]


def _hosts(keys: Any) -> set[str]:
    found = set()
    for key in keys if isinstance(keys, dict) else {}:
        host = str(key).removeprefix("https://").removeprefix("http://").split("/", 1)[0]
        found.add("docker.io" if key in DOCKER_HUB_KEYS or host in DOCKER_HUB_KEYS else host)
    return found


def logged_in(config: dict[str, Any]) -> tuple[set[str], set[str], str | None]:
    """(Registries mit Anmeldung in der Datei, Registries mit eigenem Hilfsprogramm, Hilfsprogramm für alle) aus `config.json`;
    geprüft wird nur, ob Einträge da sind, nie ihr Inhalt."""
    store = config.get("credsStore")
    store = store if isinstance(store, str) and store else None
    auths = config.get("auths")
    if isinstance(auths, dict):
        # Ein leerer Eintrag ist ein Rest oder ein Verweis auf das Hilfsprogramm; eine Anmeldung belegt er nicht.
        auths = {key: value for key, value in auths.items()
                 if isinstance(value, dict) and any(value.get(field) for field in ("auth", "identitytoken", "registrytoken"))}
    return _hosts(auths), _hosts(config.get("credHelpers")), store


def _broken(exc: Exception, doing: str, verb: str) -> str:
    """Der Grund, wenn der Aufruf auf dem Server selbst abbricht; ein fertiger Satz des Kerns bleibt, sonst nur die Art."""
    if type(exc) is TimeoutError:
        return f"Nach {LOGIN_TIMEOUT_S // 60} Minuten ohne Ende abgebrochen; docker {verb} kann auf dem Server weiterlaufen. {LOOK}"
    text = str(exc).strip()
    if (getattr(exc, "readable", False) or isinstance(exc, HostUnreachable)) and text:
        return text if text.endswith((".", "!", "?")) else f"{text}."
    if _connection_error(exc):
        return f"Die Verbindung zum Server ist beim {doing} abgebrochen; docker {verb} kann schon durchgelaufen sein. {LOOK}"
    return f"{doing} ist gescheitert ({type(exc).__name__})."


def _failed(result: Any, verb: str) -> str:
    """Der Grund aus der Ausgabe; ohne Ausgabe sagt der Satz, dass offen ist, was auf dem Server geschah."""
    text = result.stderr or result.stdout
    if (text or "").strip():
        return failure_line(text)
    if result.exit_code == -1:
        return f"Die Verbindung endete, bevor docker {verb} fertig war. {LOOK}"
    return f"docker {verb} endete ohne Meldung (Code {result.exit_code}). {LOOK}"


class SecretsUnreadable(Exception):
    """Ob ein Passwort im Tresor liegt, ließ sich nicht lesen."""


class Registries:
    """Welche Registries eingetragen sind und bei welchen ein Server angemeldet ist."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def entries(self) -> list[Registry]:
        return registries(await self._ctx.settings.get())

    async def find(self, name: str) -> Registry | None:
        return next((r for r in await self.entries() if r.name == name), None)

    async def state(self, host: Any) -> dict[str, Any]:
        settings = await self._ctx.settings.get()
        out: dict[str, Any] = {"registries": [], "creds_store": None, "error": None, "warnings": [
            f"Der Name „{name}“ steht mehrmals in den Einstellungen. Deck nutzt keinen dieser Einträge, bis jeder Name nur einmal vorkommt."
            for name in sorted(duplicate_names(settings))
        ] + [
            f"Der Eintrag {f'„{name}“' if name else 'ohne Kurznamen'} ist unvollständig oder hat ein ungültiges Feld; Deck nutzt ihn nicht. "
            "Prüf Kurzname, Adresse und Benutzername in den Einstellungen."
            for name in skipped_names(settings)
        ]}
        try:
            secrets = {entry.name: bool(await self._ctx.secrets.exists(entry.secret_label)) for entry in registries(settings)}
        except Exception as exc:
            raise SecretsUnreadable from exc
        found: set[str] = set()
        helped: set[str] = set()
        connected = False
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                connected = True
                home = str(await sftp.realpath("."))
                try:
                    async with sftp.open(f"{home}/.docker/config.json", "rb") as handle:
                        data = await handle.read(MAX_CONFIG_BYTES + 1)
                except Exception as exc:  # fehlt die Datei, ist niemand angemeldet
                    if not no_such_file(exc):
                        raise
                    data = b"{}"
            if len(data) > MAX_CONFIG_BYTES:
                raise ValueError(f"~/.docker/config.json ist größer als {MAX_CONFIG_BYTES // 1024} KB")
            config = json.loads(data.decode("utf-8", errors="replace"))
            if not isinstance(config, dict):
                raise TypeError("~/.docker/config.json hat ein unbekanntes Format")
            found, helped, out["creds_store"] = logged_in(config)
        except Exception as exc:  # noqa: BLE001 - als Grund zeigen
            what = "Die Anmeldungen von Docker ließen sich nicht lesen" if connected else "Server nicht erreichbar"
            out["error"] = f"{what}: {str(exc) or type(exc).__name__}"
        for entry in registries(settings):
            key = entry.address if entry.login_address else "docker.io"
            # Mit einem Hilfsprogramm sagt die Datei nichts darüber, ob gerade Zugangsdaten hinterlegt sind.
            known = out["error"] is None and out["creds_store"] is None and key not in helped
            out["registries"].append({
                "name": entry.name, "address": entry.address, "username": entry.username,
                "logged_in": (key in found) if known else None,
                "secret_set": secrets[entry.name],
            })
        return out


class DockerRegistryExecutor:
    """Meldet Docker auf einem Server an oder ab; das Passwort liest nur dieser Ausführer aus dem Tresor."""

    action_types = frozenset({LOGIN_ACTION, LOGOUT_ACTION})

    def __init__(self, ctx: Any, registries_: Registries) -> None:
        self._ctx = ctx
        self._registries = registries_

    async def execute(self, req: ActionRequest) -> ActionResult:
        payload = req.payload or {}
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return ActionResult(success=False, error="Bei einer Registry meldet nur ein Mensch über die Docker-Seite an.")
        registry = await self._registries.find(str(payload.get("registry") or ""))
        if registry is None or not usable(registry):
            return ActionResult(success=False, error=NOT_LISTED)
        if "username" not in payload:
            return ActionResult(success=False, error=(
                "Dieser Vorschlag stammt aus einer älteren Version von Nodvard Deck und nennt den Benutzernamen nicht. Schlag es bitte "
                "noch einmal vor."))
        if (registry.address, registry.username) != (payload.get("address"), payload.get("username")):
            return ActionResult(success=False, error=(
                f"Adresse oder Benutzername von „{registry.name}“ haben sich seit dem Vorschlag geändert. "
                "Freigegeben wird nur, was vorgeschlagen war; schlag es mit den neuen Angaben noch einmal vor."))
        host = await self._ctx.hosts.get(req.host_ref) if req.host_ref else None
        if host is None:
            return ActionResult(success=False, error="Server nicht gefunden.")
        if why := not_full_host(host, await self._ctx.settings.get(), "Dieser Server ist kein Docker-Server (Markierung fehlt)."):
            return ActionResult(success=False, error=why)
        if req.action_type == LOGOUT_ACTION:
            return await self._logout(host, registry)
        return await self._login(host, registry)

    async def _login(self, host: Any, registry: Registry) -> ActionResult:
        try:
            argv = cmd_login(registry)
        except ValueError:
            return ActionResult(success=False, error=NOT_LISTED)
        try:
            handle = await self._ctx.secrets.get_handle(registry.secret_label)
            async with self._ctx.vault_use(handle) as secret:
                password = str(secret or "")
                if not password:
                    raise LookupError
                try:
                    result = await self._ctx.exec.run_argv(host, argv, stdin=password.encode() + b"\n", timeout_s=LOGIN_TIMEOUT_S)
                except Exception as exc:  # noqa: BLE001 - zurückgeben, nicht werfen: unten stehen nur Fehler des Tresors
                    return ActionResult(success=False, error=f"Anmelden bei {registry.address} ist gescheitert: {_broken(exc, 'Anmelden', 'login')}")
        except (LookupError, SecretUnavailable):
            return ActionResult(success=False, error=f"Für „{registry.name}“ ist kein Passwort im Tresor. Trag es unter Einstellungen → Docker ein.")
        except Exception as exc:  # noqa: BLE001 - nie den Text der Ausnahme weitergeben, er könnte das Geheimnis betreffen
            return ActionResult(success=False, error=f"Das Passwort für „{registry.name}“ ließ sich nicht aus dem Tresor lesen ({type(exc).__name__}).")
        if result.exit_code != 0:
            return ActionResult(success=False, exit_code=result.exit_code, error=f"Anmelden bei {registry.address} ist gescheitert: {_failed(result, 'login')}")
        return ActionResult(success=True, exit_code=0, output=f"Docker auf diesem Server ist bei {registry.address} angemeldet. {STORED_NOTE}")

    async def _logout(self, host: Any, registry: Registry) -> ActionResult:
        try:
            argv = cmd_logout(registry)
        except ValueError:
            return ActionResult(success=False, error=NOT_LISTED)
        try:
            result = await self._ctx.exec.run_argv(host, argv, timeout_s=LOGIN_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            return ActionResult(success=False, error=f"Abmelden bei {registry.address} ist gescheitert: {_broken(exc, 'Abmelden', 'logout')}")
        if result.exit_code != 0:
            return ActionResult(success=False, exit_code=result.exit_code, error=f"Abmelden bei {registry.address} ist gescheitert: {_failed(result, 'logout')}")
        return ActionResult(success=True, exit_code=0, output=f"Docker auf diesem Server ist bei {registry.address} abgemeldet.")

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        verb = "anmelden" if req.action_type == LOGIN_ACTION else "abmelden"
        return DryRunReport(would_change=True, summary=f"Würde Docker bei {payload.get('address', '?')} {verb}.")


def registry_routes(registries_: Registries, editor: StackEditor, docker_host: Any, current_actor: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/hosts/{host_id}/registries")
    async def registry_state(host_id: str) -> dict:
        """Die eingetragenen Registries und ob dieser Server bei ihnen angemeldet ist; nie Zugangsdaten."""
        host = await docker_host(host_id)
        try:
            return await registries_.state(host)
        except SecretsUnreadable:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="Ob die Passwörter im Tresor liegen, ließ sich gerade nicht lesen. Versuch es gleich noch einmal.") from None

    async def _propose(host_id: str, name: str, actor: Actor, action_type: str) -> dict:
        if not _NAME_RE.match(name):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Name.")
        host = await docker_host(host_id)
        registry = await registries_.find(name)
        if registry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diese Registry ist nicht eingetragen.")
        verb = "anmelden" if action_type == LOGIN_ACTION else "abmelden"
        risk = Risk.MEDIUM if action_type == LOGIN_ACTION else Risk.LOW
        decision = await editor.propose(ActionRequest(
            action_type=action_type, host_ref=host.id, risk=risk, proposed_by=actor,
            reason=f"Docker bei {registry.address} {verb} (als {registry.username}).",
            payload={"registry": registry.name, "address": registry.address, "username": registry.username},
        ))
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value, "detail": decision.detail}

    @router.post("/hosts/{host_id}/registries/{name}/login", status_code=status.HTTP_202_ACCEPTED)
    async def login(host_id: str, name: str, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Anmelden vor (Gate-Aktion `docker.registry_login`)."""
        return await _propose(host_id, name, actor, LOGIN_ACTION)

    @router.post("/hosts/{host_id}/registries/{name}/logout", status_code=status.HTTP_202_ACCEPTED)
    async def logout(host_id: str, name: str, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Abmelden vor (Gate-Aktion `docker.registry_logout`)."""
        return await _propose(host_id, name, actor, LOGOUT_ACTION)

    return router
