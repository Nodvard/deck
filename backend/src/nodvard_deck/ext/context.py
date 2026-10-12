"""Setzt `nodvard_sdk.ExtensionContext` im Kern um (docs/02-EXTENSION-API.md §2).

Jeder Handle prüft seine Berechtigung zuerst (`_PermissionChecker`, Wildcard-Logik wie bei der Nutzer-RBAC).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import hmac
import http.cookiejar
import ipaddress
import logging
import ssl
import unicodedata
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import asyncssh
import httpx
from nodvard_sdk import (
    ConnectorType,
    DiscoveredHost,
    Event,
    ExecResult,
    ExtensionManifest,
    GateDecision,
    Host as SdkHost,
    HostStatus,
    HostRequirementSpec,
    HostToolSpec,
    JobSpec,
    Notification as SdkNotification,
    NotifyResult,
    PageSpec,
    Risk,
    Severity,
    WidgetSpec,
)
from nodvard_sdk.actions import ActionRequest, ActionSpec
from nodvard_sdk.context import SecretHandleRef
from nodvard_sdk.errors import CertificateMismatch, HostUnreachable, PermissionDenied
from nodvard_sdk.http import sha256_fingerprint
from nodvard_sdk.types import PeerCertificate
from sqlalchemy import select

from ..core import rbac, ssh, vault
from ..core.events import get_event_bus
from ..db.base import refresh_relationships
from ..db.session import session_scope
from ..models import Action, Host, HostTag, NotificationDelivery, Secret, Setting, User
from ..services import audit as audit_service
from ..services.hosts import host_to_sdk as _host_to_sdk
from .runtime import ExtensionRuntime, LoadedExtension, SpawnTarget, SupervisedTask

logger = logging.getLogger("nodvard_deck.ext")

PUBLIC_ROUTES_PERMISSION = "api.public"
"""Manifest-Berechtigung für `ctx.api.include_router(..., public=True)`."""


class _PermissionChecker:
    def __init__(self, ext_id: str, granted_permissions: list[str]) -> None:
        self.ext_id = ext_id
        self._granted = granted_permissions

    def require(self, permission: str) -> None:
        if not rbac.has_permission(self._granted, permission):
            raise PermissionDenied(self.ext_id, permission)


class ApiHandle:
    """Hängt die Routen einer Erweiterung unter `/api/v1/ext/<id>/...` ein.

    Jede Route verlangt eine gültige Anmeldung; öffentlich ist sie nur mit `public=True`."""

    def __init__(self, loaded: LoadedExtension) -> None:
        self._loaded = loaded
        # Router mit Anmeldepflicht; `check_routes` prüft sie nach setup() erneut, weil eine Erweiterung nach
        # include_router() weitere Routen anhängen kann.
        self._guarded: list[Any] = []
        # Adress-Präfixe mit `public=True`, die der Kern beim Einschalten ins Protokoll schreibt.
        self.public_prefixes: list[str] = []

    def check_routes(self) -> None:
        """Wirft `InvalidRegistration`, wenn ein geschützter Router eine Route ohne Anmeldeprüfung hat."""
        unguarded = [desc for router in self._guarded for desc in _unguarded_routes(router)]
        if unguarded:
            from nodvard_sdk.errors import InvalidRegistration

            raise InvalidRegistration(
                f"Extension '{self._loaded.manifest.id}': Diese Routen können keine Anmeldung "
                f"prüfen: {', '.join(unguarded)}. Nur normale FastAPI-Routen (@router.get usw.) "
                "lassen sich schützen. WebSockets, router.add_route() und router.mount() nur mit "
                "include_router(..., public=True) einhängen und selbst absichern."
            )

    @property
    def current_actor(self) -> Any:  # noqa: ANN401 - eine FastAPI-Dependency
        """Der angemeldete Nutzer als `Actor`, per `Depends(ctx.api.current_actor)`; 401 ohne gültigen Token."""
        from fastapi import Depends
        from nodvard_sdk import Actor

        from ..api.deps import get_current_user

        # `Depends` als Default statt `Annotated`: Mit `from __future__ import annotations` wäre ein lokal
        # importierter Typ für FastAPI nur ein String und damit ein Query-Parameter.
        async def _current_actor(user: Any = Depends(get_current_user)) -> Actor:  # noqa: B008
            return Actor.user(user.id, user.username)

        return _current_actor

    def include_router(
        self,
        router: Any,
        *,
        prefix: str = "",
        tags: list[str] | None = None,
        permission: str | None = None,
        public: bool = False,
    ) -> None:
        """Registriert den Router; montiert wird erst nach setup() (docs/02 §1: setup() macht kein I/O).

        Der Zugriff gilt für den ganzen Router: `permission` verlangt Anmeldung und Recht, `public=True` nichts."""
        # Ohne `permission` und `public` genügt eine gültige Anmeldung; sie ist keine Berechtigung, Routen mit Daten
        # setzen `permission=`. `public=True` braucht die Manifest-Berechtigung `api.public` und schließt `permission`
        # aus. Grenze: Der Host prüft nach `setup()` und `on_start()` (`check_routes`) und lehnt WebSocket,
        # `add_route()` und `mount()` ab. Routen, die später (etwa aus einem Hintergrundtask) an einen montierten Router
        # kommen, sieht er nicht mehr, sie müssen sich selbst absichern.
        if public and permission is not None:
            from nodvard_sdk.errors import InvalidRegistration

            raise InvalidRegistration(
                f"Extension '{self._loaded.manifest.id}': include_router(public=True) "
                f"verträgt sich nicht mit permission='{permission}'."
            )
        if public and not rbac.has_permission(self._loaded.granted_permissions, PUBLIC_ROUTES_PERMISSION):
            raise PermissionDenied(self._loaded.manifest.id, PUBLIC_ROUTES_PERMISSION)
        if self._loaded.router is None:
            from fastapi import APIRouter

            self._loaded.router = APIRouter()

        dependencies = []
        if public:
            full_prefix = f"{self._loaded.manifest.api_prefix}{prefix}"
            if full_prefix not in self.public_prefixes:
                self.public_prefixes.append(full_prefix)
            logger.warning(
                "Extension '%s' hängt öffentliche Routen ein (ohne Anmeldung erreichbar): %s",
                self._loaded.manifest.id,
                full_prefix,
            )
        elif permission is not None:
            from fastapi import Depends

            from ..api.deps import require_permission

            dependencies.append(Depends(require_permission(permission)))
        else:
            from fastapi import Depends

            from ..api.deps import get_current_user

            dependencies.append(Depends(get_current_user))
        if not public:
            self._guarded.append(router)
            self.check_routes()
        self._loaded.router.include_router(router, prefix=prefix, tags=tags, dependencies=dependencies)


def _unguarded_routes(router: Any) -> list[str]:
    """Routen eines Routers, an denen eine Router-Abhängigkeit wie `get_current_user` nicht greift."""
    # FastAPI übernimmt Abhängigkeiten nur für `APIRoute`: Starlette-Routen (`add_route`), `mount()` und WebSockets
    # fallen heraus, unbekannte Arten zählen als ungeschützt. Unter-Router werden mitgesucht (je nach FastAPI-Version
    # als Platzhalter mit `original_router` oder schon aufgelöst).
    from fastapi.routing import APIRoute

    found: list[str] = []
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            continue
        nested = getattr(route, "original_router", None)
        if nested is not None:
            found.extend(_unguarded_routes(nested))
            continue
        found.append(f"{type(route).__name__} {getattr(route, 'path', '?') or '/'}")
    return found


class UiHandle:
    def __init__(self, runtime: ExtensionRuntime, ext_id: str) -> None:
        self._runtime = runtime
        self._ext_id = ext_id

    def register_page(self, spec: PageSpec) -> None:
        self._runtime.ui.register_page(self._ext_id, spec)

    def register_widget(self, spec: WidgetSpec) -> None:
        self._runtime.ui.register_widget(self._ext_id, spec)

    def register_host_tool(self, spec: HostToolSpec) -> None:
        self._runtime.ui.register_host_tool(self._ext_id, spec)

    def register_host_requirement(self, spec: HostRequirementSpec) -> None:
        self._runtime.ui.register_host_requirement(self._ext_id, spec)

    def register_nav(self, *, section: str, order: int = 100) -> None:
        """Legt nur eine Nav-Sektion an; sonst ergibt sich die Navigation aus `nav_section`/`nav_order` der Seiten."""
        return None


class CapabilitiesHandle:
    def __init__(self, runtime: ExtensionRuntime, ext_id: str, requires: list[str]) -> None:
        self._runtime = runtime
        self._ext_id = ext_id
        self._allowed = {r.split(">=", 1)[0].split("==", 1)[0].strip() for r in requires}

    def provide(self, implementation: Any) -> None:
        from nodvard_sdk import ALL_CAPABILITIES

        matched = [proto for proto in ALL_CAPABILITIES if isinstance(implementation, proto)]
        if not matched:
            from nodvard_sdk.errors import InvalidRegistration

            raise InvalidRegistration(
                f"Extension '{self._ext_id}': {implementation!r} erfüllt kein bekanntes "
                f"Capability-Protokoll (nodvard_sdk.ALL_CAPABILITIES)."
            )
        for proto in matched:
            self._runtime.capabilities.provide(self._ext_id, proto, implementation)

    async def query(self, protocol: type) -> list[Any]:
        """Nur Capabilities von Extensions, die in `requires` deklariert sind (docs/02 §2).

        Alte Kennungen in `requires` gelten für die umbenannte Erweiterung; aufgelöst wird erst hier."""
        allowed = self._allowed | {self._runtime.canonical(ext_id) for ext_id in self._allowed}
        return self._runtime.capabilities.query(protocol, allowed_ext_ids=allowed)


class HostsHandle:
    def __init__(self, perm: _PermissionChecker, ext_id: str) -> None:
        self._perm = perm
        self._ext_id = ext_id

    async def list(self, *, tag: str | None = None, group: str | None = None) -> list[SdkHost]:
        """Die Server, optional nach `tag` oder `group` gefiltert (delegiert an `services.hosts.list_hosts`)."""
        from ..services import hosts as hosts_service

        self._perm.require("hosts.read")
        async with session_scope() as session:
            hosts = await hosts_service.list_hosts(session, tag=tag, group=group)
            return [_host_to_sdk(h) for h in hosts]

    async def get(self, host_id: str) -> SdkHost | None:
        self._perm.require("hosts.read")
        async with session_scope() as session:
            host = await session.get(Host, host_id)
            return _host_to_sdk(host) if host else None

    async def by_name(self, name: str) -> SdkHost | None:
        self._perm.require("hosts.read")
        async with session_scope() as session:
            result = await session.execute(select(Host).where(Host.name == name))
            host = result.scalar_one_or_none()
            return _host_to_sdk(host) if host else None

    async def upsert_discovered(self, hosts: list[DiscoveredHost]) -> list[SdkHost]:
        self._perm.require("hosts.write")
        out: list[SdkHost] = []
        async with session_scope() as session:
            for dh in hosts:
                result = await session.execute(
                    select(Host).where(
                        Host.provider_ext_id == self._ext_id, Host.provider_ref == dh.provider_ref
                    )
                )
                host = result.scalar_one_or_none()
                if host is None:
                    host = Host(
                        name=dh.name,
                        display_name=dh.display_name or dh.name,
                        address=dh.address,
                        os_family=dh.os_family,
                        kind=dh.kind,
                        status=dh.status.value,
                        provider_ext_id=self._ext_id,
                        provider_ref=dh.provider_ref,
                        host_metadata=dh.metadata,
                    )
                    session.add(host)
                    await session.flush()
                    # `lazy="selectin"` braucht bei einem frisch angelegten Objekt ein explizites Refresh, sonst gibt es
                    # MissingGreenlet beim Zugriff auf `.tags` und `.credentials` (`_host_to_sdk` liest beide).
                    await refresh_relationships(session, host, "tags", "credentials")
                    for tag in dh.tags:
                        host.tags.append(HostTag(tag=tag, managed_by_ext_id=self._ext_id))
                    await session.flush()
                else:
                    host.display_name = dh.display_name or host.display_name
                    # Proxmox kennt die Gast-IP einer VM oder eines LXC nicht; `discover_hosts()` liefert als
                    # Platzhalter die Adresse der Proxmox-API selbst. Ein unbedingtes Überschreiben setzte bei jedem
                    # Abgleich eine von Hand korrigierte Gast-IP auf diesen Platzhalter zurück. Bei `kind="node"` ist
                    # die Adresse die der API und darf sich ändern; eine vom Gast selbst gemeldete Adresse
                    # (`address_verified`) gilt immer.
                    if host.kind not in ("vm", "lxc") or dh.address_verified:
                        if host.address != dh.address:
                            # Wie bei einer Änderung von Hand: Passwörter gehören zum alten Server, eine gepoolte
                            # Verbindung ginge sonst weiter an die alte Adresse. Bei Gästen (Adresse vom Gast selbst,
                            # wechselt per DHCP) bleibt ein Passwort, wenn der Server-Schlüssel schon gemerkt ist: Ein
                            # anderer Server an der neuen Adresse käme nie bis zur Anmeldung.
                            from ..services import hosts as hosts_service

                            old_address = host.address
                            removed = await hosts_service.forget_address_bound_access(
                                session, host, spare_pinned=host.kind in ("vm", "lxc")
                            )
                            if removed:
                                await audit_service.log(
                                    session, actor_type="extension", actor_id=self._ext_id, action="host.updated",
                                    outcome="success", target_type="host", target_id=host.id,
                                    detail={
                                        "changed": {"address": {"from": old_address, "to": dh.address}},
                                        "password_credentials_removed": removed,
                                    },
                                )
                        host.address = dh.address
                    host.status = dh.status.value
                    # Den Beleg der letzten SSH-Anmeldung (vom Kern, nicht vom Anbieter) behalten.
                    kept = {k: v for k, v in (host.host_metadata or {}).items() if k == "login_ok"}
                    host.host_metadata = {**dh.metadata, **kept}
                    # Nur übernehmen, wenn der Anbieter es ausdrücklich meldet,
                    # sonst bliebe eine manuelle Korrektur nicht stehen.
                    if "os_family" in dh.model_fields_set:
                        host.os_family = dh.os_family
                    # Mengenabgleich statt Ersetzen, damit die Beziehung (`cascade="all, delete-orphan"`) verschwundene
                    # Tags entfernt. Nur Tags der eigenen Extension (`managed_by_ext_id`) fliegen raus: Manuelle oder
                    # fremde Tags (etwa "gameserver" auf einem entdeckten Host) bleiben.
                    current = {t.tag: t for t in host.tags}
                    target = set(dh.tags)
                    for tag_str, tag_row in list(current.items()):
                        if tag_row.managed_by_ext_id == self._ext_id and tag_str not in target:
                            host.tags.remove(tag_row)
                    still_present = {t.tag for t in host.tags}
                    for tag_str in target - still_present:
                        host.tags.append(HostTag(tag=tag_str, managed_by_ext_id=self._ext_id))
                    await session.flush()
                out.append(_host_to_sdk(host))
        return out


class ExecHandle:
    """Der gemeinsame SSH-Layer (`core/ssh.py`) für Extensions.

    Zugangsdaten kommen je Aufruf frisch aus dem Tresor, der Klartext lebt nur im Aufrufrahmen (docs/03 §3)."""

    def __init__(self, perm: _PermissionChecker, settings: Any) -> None:
        self._perm = perm
        self._settings = settings

    async def _resolve(self, host: SdkHost) -> tuple[ssh.ConnectionTarget, str, Any]:
        from ..services import hosts as hosts_service

        settings = self._settings
        async with session_scope() as session:
            db_host = await session.get(Host, host.id)
            if db_host is None:
                raise HostUnreachable(f"Host '{host.id}' existiert nicht (mehr).")
            credential = await hosts_service.default_credential(session, host.id)
            if credential is None:
                # Typisch für einen von Hand angelegten Server, der schon getaggt, aber noch ohne SSH-Zugang ist.
                raise HostUnreachable(
                    f"Für „{host.display_name or host.name}“ ist noch kein SSH-Zugang eingerichtet "
                    "(Einstellungen → Server & Zugänge)."
                )
            target = await hosts_service.resolve_connection_target(session, settings, db_host, credential)
            return target, credential.id, settings

    async def _connection(self, host: SdkHost) -> asyncssh.SSHClientConnection:
        from ..services import hosts as hosts_service

        target, credential_id, settings = await self._resolve(host)
        try:
            async with session_scope() as session:
                conn = await ssh.get_ssh_pool().get(
                    session, target, credential_id=credential_id, connect_timeout_s=settings.ssh_connect_timeout_s
                )
                # Eine angemeldete Verbindung ist der Beleg, dass der Zugang klappt (für das Abzeichen „Zugang“).
                # Geschrieben wird nur, wenn der Beleg noch fehlt, und nur für den Zugang, mit dem verbunden wurde.
                db_host = await session.get(Host, host.id)
                credential = await hosts_service.default_credential(session, host.id)
                if (
                    db_host is not None and credential is not None and credential.id == credential_id
                    and hosts_service.login_confirmed_at(db_host, credential) is None
                ):
                    hosts_service.record_login_ok(db_host, credential)
                return conn
        except (ssh.SshAuthError, ssh.HostKeyMismatch):
            # Anmeldung abgelehnt oder anderer Server-Schlüssel: der frühere Beleg gilt nicht mehr. Eigene
            # Sitzung, weil die obige beim Fehler zurückgerollt wird.
            async with session_scope() as session:
                db_host = await session.get(Host, host.id)
                if db_host is not None:
                    hosts_service.clear_login_ok(db_host)
            raise

    async def run(
        self, host: SdkHost, command: str, *, timeout_s: int = 60, user: str | None = None,
        log_command: str | None = None,
    ) -> ExecResult:
        """Für lesende Kommandos ohne Nebenwirkung (docs/02 §2); ändernde Aktionen gehen über `ctx.actions.propose()`.

        `log_command`: maskierte Fassung des Kommandos für die Audit-Zeile, falls `command` Geheimnisse enthält."""
        # Extensions sind vertrauenswürdiger Code, keine Sandbox (docs/01 §7), und `run()` ist nur per Konvention auf
        # lesende Kommandos beschränkt. Deshalb läuft jedes Kommando vor der Verbindung durch die Sperrliste: ohne
        # Action-Zeile (niemand könnte bestätigen), aber mit Audit-Zeile.
        self._perm.require("hosts.execute")
        await self._reject_denied(command, log_command=log_command)

        import time

        conn = await self._connection(host)
        started = time.monotonic()
        exit_code, stdout, stderr = await ssh.run(conn, command, timeout_s=timeout_s)
        return ExecResult(
            exit_code=exit_code, stdout=stdout, stderr=stderr,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _reject_denied(self, command: str, *, log_command: str | None = None) -> None:
        """Sperrlisten-Prüfung für jedes Kommando, das eine Extension selbst verschickt (`run()` und `stream()`)."""
        from ..core import deny_patterns

        matched = deny_patterns.match_deny_patterns(command)
        if matched is None:
            return
        from nodvard_sdk.errors import ActionBlocked

        from ..services import audit as audit_service

        async with session_scope() as session:
            await audit_service.log(
                session, actor_type="extension", actor_id=self._perm.ext_id,
                action="exec.denied", outcome="denied",
                reason=f"Sperrmuster '{matched.id}' getroffen: {matched.description}",
                detail={
                    "command": command if log_command is None else log_command,
                    "rule": f"deny_pattern:{matched.id}",
                },
            )
        raise ActionBlocked(f"deny_pattern:{matched.id}", matched.description)

    def stream(self, host: SdkHost, command: str) -> Any:
        """Wie `run()` (gleiche Berechtigung und Sperrliste, geprüft beim Betreten), aber mit laufender Ausgabe.

        Gibt einen Kontextmanager mit `AsyncIterator[bytes]` zurück; beim Verlassen endet der Prozess (PTY-Hangup)."""
        self._perm.require("hosts.execute")

        @asynccontextmanager
        async def _cm():
            await self._reject_denied(command)
            conn = await self._connection(host)
            async with ssh.open_command_stream(conn, command) as process:

                async def _chunks():
                    while True:
                        data = await process.stdout.read(65536)
                        if not data:
                            return
                        yield data

                yield _chunks()

        return _cm()

    def open_shell(self, host: SdkHost, *, cols: int, rows: int, command: str | None = None) -> Any:
        self._perm.require("hosts.execute")

        if command is not None and not command.strip():
            raise ValueError("open_shell: leeres Kommando (ohne Kommando `command` weglassen).")

        @asynccontextmanager
        async def _cm():
            if command is not None:
                await self._reject_denied(command)
            conn = await self._connection(host)
            async with ssh.open_shell(conn, cols=cols, rows=rows, command=command) as process:
                yield process

        return _cm()

    def sftp(self, host: SdkHost) -> Any:
        self._perm.require("hosts.execute")

        @asynccontextmanager
        async def _cm():
            conn = await self._connection(host)
            sftp_client = await ssh.start_sftp(conn)
            try:
                yield sftp_client
            finally:
                sftp_client.exit()
                await sftp_client.wait_closed()

        return _cm()


async def _is_login_secret(session: Any, secret: Secret) -> bool:
    """Gehört das Geheimnis zur Zwei-Faktor-Anmeldung eines Kontos (`User.totp_secret_id`, Label `totp:<Nutzer>:...`)?

    Auch unbestätigte oder verwaiste Einträge bleiben unberührt (wie `_refuse_login_secret` in `api/v1/secrets.py`)."""
    if secret.label.startswith("totp:"):
        return True
    owner_of = await session.execute(select(User.id).where(User.totp_secret_id == secret.id).limit(1))
    return owner_of.first() is not None


class SecretsHandle:
    """Lesen nach `secrets.read:<label>`; ersetzen und löschen nur eigene Geheimnisse (`SecretNotOwned`).

    Anmelde-Geheimnisse sind tabu, sonst könnte `secrets.read:*` den Authenticator eines Kontos austauschen."""

    def __init__(self, perm: _PermissionChecker, ext_id: str, settings: Any, *, own_ids: Sequence[str] = ()) -> None:
        self._perm = perm
        self._ext_id = ext_id
        self._settings = settings
        # Eigene tragen als `owner_ext_id` die Kennung, die Speicher-Kennung oder eine alte (`legacy_ids`).
        self._own_ids = {ext_id, *own_ids}

    async def _secret(self, session: Any, label: str, *, change: bool) -> Secret | None:
        """Die Zeile zum Label; Anmelde-Geheimnisse werden verweigert, bei `change` (ersetzen, löschen) auch fremde."""
        from nodvard_sdk.errors import SecretNotOwned

        secret = (await session.execute(select(Secret).where(Secret.label == label))).scalar_one_or_none()
        if secret is None:
            return None
        if await _is_login_secret(session, secret):
            logger.warning("Erweiterung %s: Zugriff auf ein Anmelde-Geheimnis abgelehnt (%s)", self._ext_id, label)
            raise SecretNotOwned(self._ext_id, label, login_secret=True)
        if change and secret.owner_ext_id not in self._own_ids:
            logger.warning("Erweiterung %s: fremdes Geheimnis nicht geaendert (%s)", self._ext_id, label)
            raise SecretNotOwned(self._ext_id, label)
        return secret

    async def get_handle(self, label: str) -> SecretHandleRef:
        self._perm.require(f"secrets.read:{label}")
        async with session_scope() as session:
            secret = await self._secret(session, label, change=False)
            if secret is None:
                from nodvard_sdk.errors import SecretUnavailable

                raise SecretUnavailable(f"Secret '{label}' existiert nicht.")
            return vault.SecretHandle(id=secret.id, label=secret.label, kind=secret.kind)

    async def create(self, *, label: str, kind: str, value: str, description: str | None = None) -> SecretHandleRef:
        self._perm.require(f"secrets.read:{label}")
        async with session_scope() as session:
            keyring = vault.load_keyring(self._settings)
            secret = await vault.create_secret(
                session,
                keyring,
                label=label,
                kind=kind,
                plaintext=value,
                owner_ext_id=self._ext_id,
                description=description or "",
            )
            return vault.SecretHandle(id=secret.id, label=secret.label, kind=secret.kind)

    async def exists(self, label: str) -> bool:
        async with session_scope() as session:
            return await vault.get_handle(session, label) is not None

    async def replace(self, *, label: str, value: str) -> SecretHandleRef:
        """Ersetzt den Wert in derselben Transaktion (`vault.replace_secret_value`); scheitert etwas, bleibt der alte.

        Im Protokoll steht wie über die Einstellungsseite nur das Label."""
        self._perm.require(f"secrets.read:{label}")
        async with session_scope() as session:
            secret = await self._secret(session, label, change=True)
            if secret is None:
                from nodvard_sdk.errors import SecretUnavailable

                raise SecretUnavailable(f"Secret '{label}' existiert nicht.")
            handle = vault.SecretHandle(id=secret.id, label=secret.label, kind=secret.kind)
            keyring = vault.load_keyring(self._settings)
            await vault.replace_secret_value(session, keyring, handle.id, value)
            await audit_service.log(
                session, actor_type="extension", actor_id=self._ext_id, action="extension.secret_set",
                outcome="success", target_type="extension", target_id=self._ext_id,
                detail={"label": label, "replaced": True},
            )
            return handle

    async def delete(self, label: str) -> bool:
        self._perm.require(f"secrets.read:{label}")
        async with session_scope() as session:
            secret = await self._secret(session, label, change=True)
            if secret is None:
                return False
            await vault.delete_secret(session, secret.id)
            await session.flush()
            # Wie über die Einstellungsseite: Im Protokoll steht nur das Label, nie der Wert.
            await audit_service.log(
                session, actor_type="extension", actor_id=self._ext_id, action="extension.secret_removed",
                outcome="success", target_type="extension", target_id=self._ext_id, detail={"label": label},
            )
            return True


class SettingsHandle:
    def __init__(self, loaded: LoadedExtension, ext_id: str, *, store_id: str | None = None) -> None:
        self._loaded = loaded
        self._ext_id = ext_id
        # Die Registry-Zeile, in der die Einstellungen liegen (siehe `LoadedExtension.store_id`).
        self._store_id = store_id or ext_id
        self._notifying = False

    def declare(self, schema: dict[str, Any]) -> None:
        self._loaded.settings_schema = schema

    async def get(self) -> dict[str, Any]:
        from ..services.extensions import get_record

        async with session_scope() as session:
            record = await get_record(session, self._store_id)
            return dict(record.settings) if record else {}

    async def set(self, values: dict[str, Any]) -> None:
        """Speichert die Werte ohne Schema-Validierung (`declare()`) und ruft danach `on_settings_changed`."""
        from ..services.extensions import get_record

        async with session_scope() as session:
            record = await get_record(session, self._store_id)
            if record is not None:
                record.settings = values
                await session.flush()

        # Erst nach dem Commit, damit der Hook per get() die neuen Werte liest. Ein set() aus dem Hook löst ihn nicht
        # erneut aus, und ein Fehler im Hook lässt das gespeicherte set() nicht nachträglich scheitern.
        hook = getattr(self._loaded.instance, "on_settings_changed", None)
        if hook is None or self._notifying:
            return
        self._notifying = True
        try:
            await hook(self._loaded.ctx, dict(values))
        except Exception:
            logger.exception("on_settings_changed von %s ist fehlgeschlagen", self._ext_id)
        finally:
            self._notifying = False

    async def core(self, key: str) -> Any:
        async with session_scope() as session:
            result = await session.execute(
                select(Setting).where(Setting.key == key, Setting.scope == "global", Setting.user_id == "")
            )
            row = result.scalar_one_or_none()
            return row.value if row else None


class SchedulerHandle:
    """`register_job()` legt die `jobs`-Zeile an (Upsert über `ext_id` und `spec.id`) und meldet den Handler an."""

    # Anders als im SDK-Protokoll ist `register_job` hier `async`: Die DB-Zeile ist echtes I/O, und ein
    # `ensure_future` im Hintergrund verlöre Fehler und jeden festen Zeitpunkt, ab dem sich der Aufrufer auf die
    # Zeile verlassen kann. Das Protokoll ist trotzdem erfüllt, `@runtime_checkable` prüft keine Signaturen.

    def __init__(
        self, perm: _PermissionChecker, ext_id: str, runtime: ExtensionRuntime, *, store_id: str | None = None
    ) -> None:
        self._perm = perm
        self._ext_id = ext_id
        self._runtime = runtime
        # Jobs hängen an der Speicher-Kennung (`jobs.ext_id`, Handler-Bestand): Nach einer Umbenennung laufen
        # dieselben Jobzeilen samt Verlauf weiter.
        self._store_id = store_id or ext_id

    async def register_job(self, spec: JobSpec) -> None:
        from ..services import jobs as jobs_service
        from ..core.scheduler import get_scheduler_service

        self._perm.require("schedule.register")
        async with session_scope() as session:
            row = await jobs_service.upsert_job(
                session, ext_id=self._store_id, ext_job_key=spec.id, name=spec.name, kind="ext",
                schedule=spec.schedule, params=spec.params, enabled=spec.enabled,
            )
            job_id = row.id

        self._runtime.scheduler.register(self._store_id, spec.id, spec.handler)

        async with session_scope() as session:
            job = await jobs_service.get_job(session, job_id)
            await get_scheduler_service().schedule(job, spec.handler)

    def validate_schedule(self, schedule: str) -> None:
        """Dieselbe Prüfung wie beim Anmelden (`core.cron.cron_trigger`), ohne Nebenwirkung."""
        from ..core.cron import cron_trigger

        cron_trigger(schedule)

    async def trigger(self, job_id: str, **params: Any) -> str:
        """`job_id` ist `spec.id`, der Schlüssel der Extension, nicht die generierte `Job.id` (die sie nie sieht)."""
        from ..services import jobs as jobs_service
        from ..core.scheduler import get_scheduler_service

        from nodvard_sdk.errors import NodvardError

        self._perm.require("schedule.register")
        handler = self._runtime.scheduler.get(self._store_id, job_id)
        if handler is None:
            raise NodvardError(f"Job '{job_id}' ist für Extension '{self._ext_id}' nicht registriert.")

        async with session_scope() as session:
            job = await jobs_service.get_job_by_key(session, ext_id=self._store_id, ext_job_key=job_id)
        if job is None:
            raise NodvardError(f"Job '{job_id}' existiert nicht (mehr) in der DB.")

        return await get_scheduler_service().trigger_now(job, handler, params=params or None)


class EventsHandle:
    def __init__(self, ext_id: str) -> None:
        self._ext_id = ext_id
        self._subscriptions: list[tuple[str, Callable[[Event], Awaitable[None]]]] = []

    async def publish(self, event: Event) -> None:
        await get_event_bus().publish(event)

    def subscribe(self, pattern: str, handler: Callable[[Event], Awaitable[None]]) -> None:
        get_event_bus().subscribe(pattern, handler)
        self._subscriptions.append((pattern, handler))

    def unsubscribe_all(self) -> None:
        """Beim Entladen, sonst feuern die Handler der alten Instanz nach dem nächsten setup() doppelt."""
        bus = get_event_bus()
        for pattern, handler in self._subscriptions:
            bus.unsubscribe(pattern, handler)
        self._subscriptions.clear()


class NotifyHandle:
    def __init__(self, perm: _PermissionChecker, ext_id: str) -> None:
        self._perm = perm
        self._ext_id = ext_id

    async def send(self, notification: SdkNotification, *, raise_on_failure: bool = False) -> NotifyResult:
        from ..services import notifications as notifications_service

        self._perm.require("notify.send")
        async with session_scope() as session:
            row, suppressed = await notifications_service.deliver(
                session,
                title=notification.title,
                body=notification.body,
                severity=notification.severity.value,
                source_ext_id=self._ext_id,
                correlation_id=notification.correlation_id,
                payload=notification.payload,
                raise_on_failure=raise_on_failure,
            )
            # `deliver()` hat die Zustellprotokolle gerade geschrieben; ein ausgefallener oder nicht eingerichteter
            # Kanal steht dort als "failed" und darf nicht als "gesendet" durchgehen.
            statuses = (
                await session.execute(
                    select(NotificationDelivery.status).where(NotificationDelivery.notification_id == row.id)
                )
            ).scalars().all()
        delivered = any(s == "sent" for s in statuses)
        # "Übersprungen" nur, wenn das der ganze Grund ist: Ist daneben ein Kanal ausgefallen, wäre
        # `skipped=True` eine Entwarnung ("liegt nur unter der Mindest-Stufe"), die den Ausfall versteckt.
        skipped = not delivered and any(s == "skipped" for s in statuses) and not any(s == "failed" for s in statuses)
        return NotifyResult(notification_id=row.id, suppressed=suppressed, delivered=delivered, skipped=skipped)

    async def would_suppress(self, *, host_id: str | None = None, host_ids: Sequence[str] | None = None) -> bool:
        """Nur lesen: dieselbe Prüfung wie in `send()`, ohne Verlaufseintrag."""
        from ..services import notifications as notifications_service

        self._perm.require("notify.send")
        payload: dict[str, Any] = {}
        if host_id is not None:
            payload["host_id"] = host_id
        if host_ids is not None:
            # Unverändert weitergeben: Ob die Liste taugt, entscheidet dieselbe Prüfung wie in send().
            payload["host_ids"] = host_ids
        async with session_scope() as session:
            return await notifications_service.would_suppress(session, payload)


# Namensräume im Protokoll, die nur der Kern beschreibt, weil er solche Einträge wieder liest: `mfa.failed` und
# `auth.totp_check_failed` holen beim Start die Sperre nach falschen Zwei-Faktor-Codes zurück (ein gefälschter Eintrag
# sperrte den Owner bis zu 24 Stunden aus), `login.failed`/`login.locked` bestimmen, welche Namen das Protokoll
# verbirgt, `system.update.*` hält fest, ob ein Update-Ergebnis schon protokolliert ist, und `user.*` ist die Spur der
# Kontenverwaltung. `AuditHandle.log()` lehnt diese Namen ab.
CORE_AUDIT_PREFIXES = ("mfa.", "auth.", "login.", "system.", "user.")


class AuditHandle:
    def __init__(self, perm: _PermissionChecker, ext_id: str) -> None:
        self._perm = perm
        self._ext_id = ext_id

    async def log(
        self,
        *,
        action: str,
        outcome: str,
        target_type: str | None = None,
        target_id: str | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        actor: Any | None = None,  # noqa: ANN401 - nodvard_sdk.Actor
    ) -> None:
        """Schreibt einen Eintrag; mit `actor` (`current_actor`) steht der Mensch darin, die Extension in `detail.via`.

        Namen aus `CORE_AUDIT_PREFIXES` sind dem Kern vorbehalten (`ValueError`)."""
        self._perm.require("audit.write")
        # Groß-/Kleinschreibung und Leerzeichen zählen nicht: Die Abfrage des Kerns unterscheidet sie teils nicht.
        if str(action).strip().casefold().startswith(CORE_AUDIT_PREFIXES):
            raise ValueError(
                f"Die Aktion '{str(action)[:60]}' ist dem Kern vorbehalten (Namen mit "
                f"{', '.join(repr(p) for p in CORE_AUDIT_PREFIXES)} am Anfang). Eine Erweiterung schreibt "
                "Einträge unter eigenem Namen, etwa '<kennung>.<ereignis>'."
            )
        actor_type, actor_id = "extension", self._ext_id
        if actor is not None and getattr(actor, "type", None) is not None:
            actor_type = getattr(actor.type, "value", str(actor.type))
            actor_id = actor.id
            detail = {**(detail or {}), "via": self._ext_id}
        async with session_scope() as session:
            await audit_service.log(
                session,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                outcome=outcome,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
                detail=detail,
                correlation_id=correlation_id,
            )


class WsHandle:
    def __init__(self, ext_id: str, legacy_ids: Callable[[], Sequence[str]] | None = None) -> None:
        self._ext_id = ext_id
        # Alte Kennungen (`legacy_ids`), bei jedem Senden nach dem letzten Scan gefragt
        # (`ExtensionRuntime.legacy_ids_of`).
        self._legacy_ids = legacy_ids

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        """Sendet auf `ext.<Kennung>.<Kanal>` und nach einer Umbenennung auch auf den Kanälen der alten Kennungen.

        Eine noch offene Seite mit altem Katalog hat ihren Kanal unter der alten Kennung abonniert."""
        from ..core.ws_hub import get_ws_hub

        hub = get_ws_hub()
        for ext_id in (self._ext_id, *(self._legacy_ids() if self._legacy_ids else ())):
            await hub.publish(f"ext.{ext_id}.{channel}", payload)


class ConnectorsHandle:
    def __init__(self, ext_id: str, *, own_ids: Sequence[str] = ()) -> None:
        self._ext_id = ext_id
        # Instanzen liegen unter der Kennung, mit der sie angelegt wurden, nach einer Umbenennung auch unter den alten.
        self._own_ids = sorted({ext_id, *own_ids})
        self._types: dict[str, ConnectorType] = {}

    def register_type(self, connector_type: ConnectorType) -> None:
        self._types[connector_type.id] = connector_type

    async def instances(self, type_id: str | None = None) -> list[Any]:
        from ..models import ConnectorInstance

        async with session_scope() as session:
            stmt = select(ConnectorInstance).where(ConnectorInstance.ext_id.in_(self._own_ids))
            if type_id:
                stmt = stmt.where(ConnectorInstance.type_id == type_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_client(self, instance_id: str) -> Any:
        raise NotImplementedError(
            "ctx.connectors.get_client() braucht die $secret-Referenz-Auflösung über "
            "den Vault -- noch nicht gebaut, erster Bedarf voraussichtlich mit einer "
            "späteren KI-/Connector-Extension."
        )


class ActionsHandle:
    """`propose()` prüft die Berechtigungen der Extension (Schritte 1+2 in docs/01 §4); das Gate kennt nur Aktionen."""

    def __init__(
        self, perm: _PermissionChecker, ext_id: str, runtime: ExtensionRuntime, settings: Any,
        *, own_ids: Sequence[str] = (),
    ) -> None:
        self._perm = perm
        self._ext_id = ext_id
        self._runtime = runtime
        self._settings = settings
        # Kennungen, unter denen diese Erweiterung Aktionen vorgeschlagen hat: die heutige und nach einer Umbenennung
        # die alten; neue Aktionen tragen die heutige.
        self._own_ids = sorted({ext_id, *own_ids})

    def register(self, spec: ActionSpec) -> None:
        self._runtime.actions.register(self._ext_id, spec)

    async def propose(self, request: ActionRequest, *, wait_s: float | None = None) -> GateDecision:
        """`wait_s` begrenzt bei Vollautonomie das Warten auf das Ergebnis; `None` wartet bis zum Ende."""
        from ..core import gate as gate_service

        entry = self._runtime.actions.get(request.action_type)
        spec = entry[1] if entry is not None else None
        if spec is not None:
            for perm in spec.permissions:
                self._perm.require(perm)
        else:
            self._perm.require("hosts.execute")
        if request.standing_approval is not None:
            # Ohne Klick nur, wenn das Manifest es ausdrücklich anmeldet; die Freigabe selbst prüft danach das Gate.
            self._perm.require(gate_service.STANDING_APPROVAL_PERMISSION)

        async with session_scope() as session:
            return await gate_service.propose(
                session,
                ext_id=self._ext_id,
                request=request,
                command_field=spec.command_field if spec is not None else None,
                wait_s=wait_s,
            )

    async def check_standing_approval(self, granted_by_user_id: str, *, risk: Risk) -> str | None:
        """Gälte eine Dauerfreigabe von `granted_by_user_id` heute für die Stufe `risk`? `None` = ja, sonst der Grund.

        Lesend (`core.gate.standing_approval_problem`); braucht dieselbe Berechtigung wie `propose()` mit Freigabe."""
        from ..core import gate as gate_service

        self._perm.require(gate_service.STANDING_APPROVAL_PERMISSION)
        async with session_scope() as session:
            found = await gate_service.standing_approval_problem(
                session, granted_by_user_id=granted_by_user_id, risk=risk
            )
        return None if found is None else found[0]

    async def list(self, *, correlation_id: str | None = None, limit: int = 20) -> list[Action]:
        """Die jüngsten Aktionen dieser Extension, optional zu einer `correlation_id`; wie `result()` nie fremde."""
        from sqlalchemy import select

        stmt = select(Action).where(Action.ext_id.in_(self._own_ids))
        if correlation_id is not None:
            stmt = stmt.where(Action.correlation_id == correlation_id)
        stmt = stmt.order_by(Action.created_at.desc()).limit(max(1, min(limit, 200)))
        async with session_scope() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def waiting_count(self) -> int:
        """Zahl der Aktionen mit Status `proposed` aller Erweiterungen (wie `GET /overview`); braucht `hosts.read`."""
        from sqlalchemy import func

        self._perm.require("hosts.read")
        async with session_scope() as session:
            stmt = select(func.count()).select_from(Action).where(Action.status == "proposed")
            return int((await session.execute(stmt)).scalar_one())

    async def proposer_labels(self, rows: list[Action]) -> dict[str, str]:
        """`{action_id: Name des Vorschlagenden}`, aufgelöst nur für Zeilen dieser Extension anhand der Datenbank."""
        # Von `rows` zählt nur die `id`: Mit einer selbstgebauten Zeile ließe sich sonst jede Nutzer-ID in einen Namen
        # umsetzen. Wer den Namen sieht, prüft der Kern hier nicht (er kennt den Aufrufer nicht): Die Route der
        # Extension muss selbst abgesichert sein.
        from sqlalchemy import select

        from ..services.actor_labels import load_actor_labels, raw_actor

        out = {row.id: raw_actor(row.proposed_by_type, row.proposed_by_id) for row in rows}
        if not out:
            return {}
        stmt = select(Action).where(Action.id.in_(list(out)), Action.ext_id.in_(self._own_ids))
        async with session_scope() as session:
            own = list((await session.execute(stmt)).scalars().all())
            labels = await load_actor_labels(session, own, viewer=None)
        out.update({row.id: labels.proposed_by(row) for row in own})
        return out

    async def result(self, action_id: str) -> Action | None:
        """Nur eigene Aktionen; ein erratenes fremdes `action_id` wäre ein Informationsleck."""
        async with session_scope() as session:
            row = await session.get(Action, action_id)
            if row is None or row.ext_id not in self._own_ids:
                return None
            return row


class WebSocketRedirectRefused(ConnectionError):
    """Der Server hat beim WebSocket-Handshake mit einer Weiterleitung (3xx) geantwortet, der nie gefolgt wird.

    Der Text ist ein fertiger deutscher Satz (`readable`) und kommt unverändert beim Aufrufer an."""

    readable = True

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(
            f"Der Server hat die Verbindung auf eine andere Adresse umgeleitet (HTTP {status_code}). "
            "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
            "Prüfe die eingetragene Adresse des Servers."
        )


@functools.lru_cache(maxsize=1)
def _websocket_connect_class() -> type:
    """`websockets.asyncio.client.connect` ohne Weiterleitungen, lazy geladen wie `websockets` sonst auch."""
    # Die Bibliothek folgt 3xx bis zu zehnmal, prüft aber nur die Start-Adresse gegen `_require_target_allowed`: Ein
    # Server könnte die Verbindung so auf eine Adresse lenken, die die Erweiterung nicht erreichen dürfte.
    # `process_redirect()` ist dafür die vorgesehene Stelle; eine zurückgegebene Ausnahme wird statt eines zweiten
    # Verbindungsaufbaus ausgelöst (die erste Verbindung ist dann schon abgebrochen).
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    class _ConnectWithoutRedirects(connect):
        def process_redirect(self, exc: Exception) -> Exception | str:
            if isinstance(exc, InvalidStatus) and 300 <= exc.response.status_code < 400:
                return WebSocketRedirectRefused(exc.response.status_code)
            return exc

    return _ConnectWithoutRedirects


def _direct_transport(*, verify: bool | ssl.SSLContext = True) -> httpx.AsyncHTTPTransport:
    """Transport ohne Proxy aus der Umgebung (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`)."""
    # httpx liest Proxys nur, wenn der Client den Transport selbst anlegt. `trust_env=False` am Client wäre falsch: Es
    # schaltet auch `SSL_CERT_FILE`/`SSL_CERT_DIR` ab, und ein Betreiber mit eigener Zertifizierungsstelle verlöre sie.
    # Sonst gilt, was `httpx.AsyncClient()` selbst angelegt hätte; Weiterleitungen folgt der Client weiter nicht.
    return httpx.AsyncHTTPTransport(verify=verify)


def _format_sha256(digest: bytes) -> str:
    return ":".join(f"{b:02X}" for b in digest)


def _pin_digest(value: str) -> bytes:
    """`pinned_sha256` als 32 Bytes; `ValueError` mit deutschem Satz, wenn es kein SHA-256-Fingerabdruck ist."""
    return bytes.fromhex(sha256_fingerprint(value).replace(":", ""))


def _pinned_sslobject_class(expected: bytes) -> type[ssl.SSLObject]:
    """`ssl.SSLObject`, das nach dem Handshake den Fingerabdruck des Server-Zertifikats mit `expected` vergleicht."""
    # asyncio (`websockets`) und anyio (`httpx`/`httpcore`) bauen ihre TLS-Verbindung über `SSLContext.wrap_bio()`, das
    # `SSLContext.sslobject_class` liefert (laut Python-Doku für Unterklassen gedacht). Beide rufen `do_handshake()` vor
    # dem ersten Anwendungsbyte: Passt das Zertifikat nicht, scheitert der Handshake (`CertificateMismatch`, ein
    # `ssl.SSLError`) und nichts von der Anfrage verlässt die Maschine. `write()` verweigert zusätzlich alles, solange
    # kein passendes Zertifikat gesehen wurde, falls eine Schicht je ohne eigenen Handshake-Aufruf schriebe.

    class _PinnedSSLObject(ssl.SSLObject):
        _nodvard_pin_ok = False

        def do_handshake(self) -> None:
            super().do_handshake()
            der = self.getpeercert(binary_form=True)
            actual = hashlib.sha256(der).digest() if der else b""
            if not der or not hmac.compare_digest(actual, expected):
                raise CertificateMismatch(_format_sha256(expected), _format_sha256(actual) if der else "")
            self._nodvard_pin_ok = True

        def write(self, data: Any) -> int:
            if not self._nodvard_pin_ok:
                raise CertificateMismatch(_format_sha256(expected), "")
            return super().write(data)

    return _PinnedSSLObject


def _pinned_ssl_context(expected: bytes) -> ssl.SSLContext:
    """TLS-Kontext, der genau ein Zertifikat annimmt (`_pinned_sslobject_class`).

    Keine Prüfung über Zertifizierungsstellen und Rechnernamen; ein selbstsigniertes Zertifikat fiele dort durch."""
    # Ein einfacher `ssl.SSLContext` ohne Unterklasse: anyio ruft `wrap_bio()` nur für genau diese Klasse direkt auf.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.sslobject_class = _pinned_sslobject_class(expected)
    return context


def _unverified_ssl_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


_CERT_TEXT_LIMIT = 200
_CERT_MAX_NAMES = 10


def _cert_text(value: Any, limit: int = _CERT_TEXT_LIMIT) -> str | None:
    """Einzeilig, ohne Steuer- und Formatzeichen, gekürzt: Die Felder kommen ungeprüft vom Server."""
    if not isinstance(value, str):
        return None
    text = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in value)
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe_certificate(der: bytes, *, trusted: bool) -> PeerCertificate:
    """Fingerabdruck immer; Inhaber, Aussteller, Ablauf und Namen nur, soweit sich das Zertifikat lesen lässt."""
    sha256 = _format_sha256(hashlib.sha256(der).digest())
    subject = issuer = not_after = None
    names: list[str] = []
    try:
        from cryptography import x509

        cert = x509.load_der_x509_certificate(der)
        subject = _cert_text(cert.subject.rfc4514_string())
        issuer = _cert_text(cert.issuer.rfc4514_string())
        not_after = cert.not_valid_after_utc.isoformat()
        try:
            alt = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:
            alt = None
        if alt is not None:
            for entry in alt:
                value = getattr(entry, "value", None)
                text = _cert_text(str(value), 100) if value is not None else None
                if text and len(names) < _CERT_MAX_NAMES:
                    names.append(text)
    except Exception:  # noqa: BLE001, S110 - nur Anzeige; ein ungewöhnliches Zertifikat bleibt beim Fingerabdruck
        pass
    return PeerCertificate(
        sha256=sha256, trusted=trusted, subject=subject, issuer=issuer, not_after=not_after, alt_names=tuple(names)
    )


async def _read_peer_certificate(host: str, port: int, context: ssl.SSLContext, timeout_s: float) -> bytes:
    """Nur der TLS-Handshake: Verbindung auf, Zertifikat lesen, Verbindung zu. Es geht keine Anfrage hinaus."""

    async def _handshake() -> bytes:
        _reader, writer = await asyncio.open_connection(
            host, port, ssl=context, server_hostname=host, ssl_handshake_timeout=timeout_s
        )
        try:
            ssl_object = writer.get_extra_info("ssl_object")
            der = ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        if not der:
            raise ssl.SSLError("Der Server hat kein Zertifikat gezeigt.")
        return der

    return await asyncio.wait_for(_handshake(), timeout=timeout_s)


_MAX_PINNED_CLIENTS = 8
"""So viele Clients mit gepinntem Zertifikat hält ein Handle offen (je Fingerabdruck einer); der älteste geht zu."""


class _NoCookies(http.cookiejar.DefaultCookiePolicy):
    """Kein Cookie wird gemerkt oder mitgeschickt."""

    # Ein gepinnter Client ist für Aufrufe mit etwas Geheimem da (etwa eine einmalige Anmeldung). Merkte er ein
    # `Set-Cookie` (z. B. eine Sitzung), ginge es mit jeder späteren Anfrage an denselben Fingerabdruck mit, auch von
    # anderen Stellen der Erweiterung. Wer ein Cookie braucht, setzt den Kopf selbst je Anfrage.

    def set_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:
        return False

    def return_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:
        return False


class HttpHandle:
    """Ausgehende HTTP-Aufrufe einer Extension, geprüft gegen ihre `net.outbound`-Berechtigungen."""

    def __init__(self, perm: _PermissionChecker, granted_permissions: list[str]) -> None:
        self._perm = perm
        self._cidrs = [
            p.split(":", 1)[1] if ":" in p else None
            for p in granted_permissions
            if p == "net.outbound" or p.startswith("net.outbound:")
        ]
        # Eigener Transport ohne Proxy aus der Umgebung: Die Zielprüfung sieht nur die URL, und ein Proxy bekäme
        # `Authorization` und das Ziel, ohne dass die Erweiterung dafür freigegeben wurde.
        self._client = httpx.AsyncClient(transport=_direct_transport())
        self._insecure_client: httpx.AsyncClient | None = None
        self._pinned_clients: OrderedDict[bytes, httpx.AsyncClient] = OrderedDict()
        self._closing: set[asyncio.Future[Any]] = set()

    def _require_target_allowed(self, url: str) -> None:
        if not self._cidrs:
            self._perm.require("net.outbound")
            return

        host = urlsplit(url).hostname
        try:
            ip = ipaddress.ip_address(host) if host else None
        except ValueError:
            ip = None

        if ip is None:
            return  # DNS-Name wird nicht aufgelöst: Eine net.outbound-Berechtigung reicht (oben geprüft)

        for cidr in self._cidrs:
            if cidr is None or ip in ipaddress.ip_network(cidr, strict=False):
                return
        raise PermissionDenied(self._perm.ext_id, f"net.outbound:{host}")

    def _client_for(self, *, insecure_tls: bool, pinned_sha256: str | None = None, url: str = "") -> httpx.AsyncClient:
        if pinned_sha256 is not None:
            return self._pinned_client(url, pinned_sha256, insecure_tls=insecure_tls)
        if not insecure_tls:
            return self._client
        # httpx erlaubt `verify=` nur bei der Konstruktion, daher ein zweiter, lazy angelegter Client (etwa für das
        # selbstsignierte Standardzertifikat von Proxmox). Die eigene Berechtigung macht das im Manifest sichtbar und
        # bestätigungspflichtig: `net.outbound` allein erlaubt nicht, die Zertifikatsprüfung abzuschalten.
        self._perm.require("net.outbound.insecure_tls")
        if self._insecure_client is None:
            self._insecure_client = httpx.AsyncClient(transport=_direct_transport(verify=False))
        return self._insecure_client

    @staticmethod
    def _check_pin_use(url: str, *, insecure_tls: bool, schemes: tuple[str, ...]) -> None:
        if insecure_tls:
            raise ValueError(
                "pinned_sha256 und insecure_tls schliessen sich aus: mit einem gepinnten Zertifikat wird genau "
                "dieses geprueft, insecure_tls wuerde jede Pruefung abschalten."
            )
        if urlsplit(url).scheme.lower() not in schemes:
            raise ValueError("pinned_sha256 geht nur mit einer verschluesselten Adresse (https:// bzw. wss://).")

    def _pinned_client(self, url: str, pinned_sha256: str, *, insecure_tls: bool) -> httpx.AsyncClient:
        """Client, der nur das Zertifikat mit diesem Fingerabdruck annimmt; die ältesten gehen bei zu vielen zu."""
        # Für Ziele mit selbstsigniertem Zertifikat, an die etwas wirklich Geheimes geht (etwa ein einmal benutztes
        # Administrator-Passwort): Der Vergleich läuft im TLS-Handshake vor dem ersten Byte der Anfrage, bei Abweichung
        # gibt es `CertificateMismatch`. Je Fingerabdruck ein eigener Client, damit keine Pool-Verbindung eines anderen
        # Fingerabdrucks wiederverwendet wird. Weil die Prüfung über Zertifizierungsstellen entfällt, braucht es wie
        # `insecure_tls` die Berechtigung `net.outbound.insecure_tls`.
        self._check_pin_use(url, insecure_tls=insecure_tls, schemes=("https",))
        expected = _pin_digest(pinned_sha256)
        self._perm.require("net.outbound.insecure_tls")
        client = self._pinned_clients.get(expected)
        if client is not None:
            self._pinned_clients.move_to_end(expected)
            return client
        client = httpx.AsyncClient(
            transport=_direct_transport(verify=_pinned_ssl_context(expected)),
            cookies=http.cookiejar.CookieJar(policy=_NoCookies()),
        )
        self._pinned_clients[expected] = client
        while len(self._pinned_clients) > _MAX_PINNED_CLIENTS:
            _old_key, old = self._pinned_clients.popitem(last=False)
            closing = asyncio.ensure_future(old.aclose())
            self._closing.add(closing)
            closing.add_done_callback(self._closing.discard)
        return client

    @staticmethod
    def _refuse_redirects(kwargs: dict[str, Any]) -> None:
        """Lehnt `follow_redirects=True` mit `ValueError` ab, bevor etwas gesendet wird (auch mit `insecure_tls`)."""
        # `_require_target_allowed()` sieht nur die Adresse des Aufrufs: Folgte httpx einer 3xx-Antwort, ginge die
        # nächste Anfrage samt `Authorization` an ein ungeprüftes Ziel, und ein Server könnte die Erweiterung so aus
        # ihren `net.outbound:<cidr>`-Bereichen lenken. 3xx-Antworten kommen unverändert zurück (wie bei `websocket()`).
        value = kwargs.get("follow_redirects", False)
        if value is httpx.USE_CLIENT_DEFAULT or not value:
            return
        raise ValueError(
            "ctx.http folgt keinen Weiterleitungen, follow_redirects=True ist nicht erlaubt: das Ziel einer "
            "Weiterleitung würde nie gegen die erlaubten Adressen (net.outbound) geprüft. Eine Antwort mit "
            "Status 3xx kommt unverändert zurück; die Erweiterung fragt die neue Adresse selbst ab."
        )

    async def get(self, url: str, *, insecure_tls: bool = False, pinned_sha256: str | None = None, **kwargs: Any) -> Any:
        self._require_target_allowed(url)
        self._refuse_redirects(kwargs)
        return await self._client_for(insecure_tls=insecure_tls, pinned_sha256=pinned_sha256, url=url).get(url, **kwargs)

    async def post(self, url: str, *, insecure_tls: bool = False, pinned_sha256: str | None = None, **kwargs: Any) -> Any:
        self._require_target_allowed(url)
        self._refuse_redirects(kwargs)
        return await self._client_for(insecure_tls=insecure_tls, pinned_sha256=pinned_sha256, url=url).post(url, **kwargs)

    async def request(
        self, method: str, url: str, *, insecure_tls: bool = False, pinned_sha256: str | None = None, **kwargs: Any
    ) -> Any:
        self._require_target_allowed(url)
        self._refuse_redirects(kwargs)
        client = self._client_for(insecure_tls=insecure_tls, pinned_sha256=pinned_sha256, url=url)
        return await client.request(method, url, **kwargs)

    async def peer_certificate(self, url: str, *, timeout_s: float = 10.0) -> PeerCertificate:
        """Liest das Zertifikat des Ziels ohne Anfrage: erst streng, sonst ohne Prüfung nur zum Lesen.

        Gleiche Zertifizierungsstellen wie `self._client` (`httpx.create_ssl_context()`), direkt und ohne Proxy."""
        self._require_target_allowed(url)
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("https", "wss"):
            raise ValueError("Ein Zertifikat gibt es nur bei verschluesselten Adressen (https:// oder wss://).")
        host = parts.hostname
        if not host:
            raise ValueError("Die Adresse nennt keinen Rechner.")
        port = parts.port or 443
        try:
            der = await _read_peer_certificate(host, port, httpx.create_ssl_context(), timeout_s)
            trusted = True
        except ssl.SSLCertVerificationError:
            der = await _read_peer_certificate(host, port, _unverified_ssl_context(), timeout_s)
            trusted = False
        return _describe_certificate(der, trusted=trusted)

    def stream(  # noqa: ANN201 - httpx.AsyncClient.stream()s eigener Typ
        self, method: str, url: str, *, insecure_tls: bool = False, pinned_sha256: str | None = None, **kwargs: Any
    ):
        """Streamt die Antwort statt sie ganz zu lesen (Wrapper um `AsyncClient.stream()`), etwa für große Dateien."""
        self._require_target_allowed(url)
        self._refuse_redirects(kwargs)
        return self._client_for(insecure_tls=insecure_tls, pinned_sha256=pinned_sha256, url=url).stream(method, url, **kwargs)

    def websocket(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        subprotocols: list[str] | None = None,
        insecure_tls: bool = False,
        open_timeout_s: float = 10.0,
        pinned_sha256: str | None = None,
    ):  # noqa: ANN201 - AsyncContextManager[websockets ClientConnection]
        """Ausgehender WebSocket mit denselben Prüfungen wie die HTTP-Methoden, beim Aufruf und vor dem Verbinden.

        Ziel (`net.outbound`/CIDR), dann `insecure_tls` oder `pinned_sha256` nur mit `net.outbound.insecure_tls`."""
        self._require_target_allowed(url)
        ssl_context: ssl.SSLContext | None = None
        # Gepinnt (nur `wss://`): Der TLS-Handshake scheitert mit `CertificateMismatch` vor den Kopfzeilen.
        if pinned_sha256 is not None:
            self._check_pin_use(url, insecure_tls=insecure_tls, schemes=("wss",))
            expected = _pin_digest(pinned_sha256)
            self._perm.require("net.outbound.insecure_tls")
            ssl_context = _pinned_ssl_context(expected)
        elif url.startswith("wss://"):
            if insecure_tls:
                self._perm.require("net.outbound.insecure_tls")
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            else:
                ssl_context = ssl.create_default_context()

        # `max_size=None`: Ein Bildschirm-Update ist ein einzelner großer Frame, das Standardlimit (1 MiB) bräche eine
        # Konsole mit hoher Auflösung mitten in der Sitzung ab. `compression=None`: RFB-Rahmen sind schon kodiert,
        # permessage-deflate kostet nur CPU. `proxy=None`: Seit websockets 15 nutzt `connect()` sonst `HTTP_PROXY`,
        # `WS_PROXY` und Co., und die Verbindung samt Zugangs-Header ginge an eine Stelle, die die Erweiterung nie
        # freigegeben bekam (deshalb `websockets>=15` in `backend/pyproject.toml`; ältere Versionen scheiterten mit
        # `TypeError`). Antwortet der Server beim Handshake mit 3xx, wirft `async with` `WebSocketRedirectRefused`.
        return _websocket_connect_class()(
            url,
            additional_headers=headers,
            subprotocols=subprotocols,  # type: ignore[arg-type]
            ssl=ssl_context,
            open_timeout=open_timeout_s,
            max_size=None,
            compression=None,
            proxy=None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._insecure_client is not None:
            await self._insecure_client.aclose()
        while self._pinned_clients:
            _key, client = self._pinned_clients.popitem(last=False)
            await client.aclose()
        if self._closing:
            await asyncio.gather(*self._closing, return_exceptions=True)


class DbHandle:
    """`ctx.db`: eine echte Session (`session_scope()` wie im Kern) und die Präfix-Prüfung für Tabellen."""

    # `declare_tables()` ist eine Vertrauens-, keine Sandbox-Grenze: Einzelne Queries werden nicht durchleuchtet
    # (docs/02 §3: Extensions sind vertrauenswürdiger Code).

    def __init__(self, ext_id: str, table_prefix: str | tuple[str, ...]) -> None:
        """Ein Präfix oder mehrere (`ExtensionManifest.table_prefixes`; nach einer Umbenennung auch die alten)."""
        self._ext_id = ext_id
        self._table_prefix = table_prefix

    def session(self):  # noqa: ANN201
        return session_scope()

    def declare_tables(self, metadata: Any) -> None:
        """Prüft die `MetaData` der Extension gegen `ext_<id>_`; bei einer Abweichung wirft sie `TablePrefixViolation`.

        Der Fehler läuft über den Fehlerpfad von `setup()` und verhindert das Laden."""
        from .tables import validate_table_prefix

        validate_table_prefix(self._ext_id, metadata.tables.keys(), expected_prefix=self._table_prefix)


@dataclass
class Context:
    ext_id: str
    version: str
    data_dir: PurePosixPath
    api: ApiHandle
    ui: UiHandle
    connectors: ConnectorsHandle
    capabilities: CapabilitiesHandle
    actions: ActionsHandle
    hosts: HostsHandle
    exec: ExecHandle
    secrets: SecretsHandle
    settings: SettingsHandle
    scheduler: SchedulerHandle
    events: EventsHandle
    notify: NotifyHandle
    audit: AuditHandle
    ws: WsHandle
    db: DbHandle
    http: HttpHandle
    logger: Any
    _loaded: LoadedExtension
    _app_settings: Any
    """Die beim Laden aktiven `Settings`, nicht `config.get_settings()` (das ignorierte die des laufenden Hosts)."""

    def vault_use(self, handle: SecretHandleRef):  # noqa: ANN201
        @asynccontextmanager
        async def _cm():
            keyring = vault.load_keyring(self._app_settings)
            async with session_scope() as session:
                async with vault.vault_use(
                    session, keyring, handle, actor_type="extension", actor_id=self.ext_id
                ) as value:
                    yield value

        return _cm()

    def spawn(
        self,
        coro: SpawnTarget,
        *,
        name: str,
        restart: bool = True,
        restart_delay_s: float = 5.0,
    ) -> None:
        task = SupervisedTask(
            ext_id=self.ext_id, name=name, target=coro, restart=restart, restart_delay_s=restart_delay_s
        )
        task.start()
        self._loaded.tasks.append(task)

    def notify_sync(self, title: str, body: str, severity: Severity = Severity.INFO) -> None:
        import asyncio

        asyncio.ensure_future(
            self.notify.send(SdkNotification(title=title, body=body, severity=severity))
        )


def build_context(
    runtime: ExtensionRuntime,
    loaded: LoadedExtension,
    manifest: ExtensionManifest,
    granted_permissions: list[str],
    ext_data_dir: Path,
    settings: Any,
) -> Context:
    """Einstellungen und Jobs hängen an der Speicher-Kennung `loaded.store_id`, alles andere an `manifest.id`."""
    store = loaded.store_id or manifest.id
    perm = _PermissionChecker(manifest.id, granted_permissions)
    ext_data_dir.mkdir(parents=True, exist_ok=True)
    return Context(
        ext_id=manifest.id,
        version=manifest.version,
        data_dir=PurePosixPath(str(ext_data_dir)),
        api=ApiHandle(loaded),
        ui=UiHandle(runtime, manifest.id),
        connectors=ConnectorsHandle(manifest.id, own_ids=[store, *manifest.legacy_ids]),
        capabilities=CapabilitiesHandle(runtime, manifest.id, manifest.requires),
        actions=ActionsHandle(perm, manifest.id, runtime, settings, own_ids=[store, *manifest.legacy_ids]),
        hosts=HostsHandle(perm, manifest.id),
        exec=ExecHandle(perm, settings),
        secrets=SecretsHandle(perm, manifest.id, settings, own_ids=[store, *manifest.legacy_ids]),
        settings=SettingsHandle(loaded, manifest.id, store_id=store),
        scheduler=SchedulerHandle(perm, manifest.id, runtime, store_id=store),
        events=EventsHandle(manifest.id),
        notify=NotifyHandle(perm, manifest.id),
        audit=AuditHandle(perm, manifest.id),
        ws=WsHandle(manifest.id, lambda: runtime.legacy_ids_of(manifest.id)),
        db=DbHandle(manifest.id, manifest.table_prefixes),
        http=HttpHandle(perm, granted_permissions),
        logger=logging.getLogger(f"nodvard_deck.ext.{manifest.id}"),
        _loaded=loaded,
        _app_settings=settings,
    )
