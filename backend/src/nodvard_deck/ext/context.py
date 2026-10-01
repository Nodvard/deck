"""Konkrete Implementierung von `nodvard_sdk.ExtensionContext` (docs/02-EXTENSION-API.md
§2).

Jeder Handle, fuer den docs/02 §2 eine Permission nennt, prueft sie ZUERST -- ueber
`_PermissionChecker`, der dieselbe Suffix-Wildcard-Logik wie die Nutzer-RBAC
wiederverwendet (`core.rbac.has_permission`), nur gegen `granted_permissions` einer
Extension statt gegen Rollen eines Nutzers.

**Bewusster Realitaetsgrad je Handle** (siehe Abnahmeberichte fuer die volle
Begruendung): `ui`, `settings`, `events`, `audit`, `secrets`, `vault_use`,
`capabilities`, `hosts`, `notify`, `scheduler.register_job`, `http`, `spawn`, `api`,
`logger`, `data_dir` sind seit WP-3 ECHT. `exec` (run/open_shell/sftp) ist seit WP-4
ECHT -- der gemeinsame `core/ssh.py`-Layer, den die terminal-Extension nutzt. Seit
WP-5 ist `actions` (register/propose/result) ECHT -- `propose()` geht durch
`core.gate.propose()` (Sperrliste, Anti-Flapping, Autonomie-Entscheidung, Ausfuehrung
bei voller Autonomie), `ctx.exec.run()` prueft zusaetzlich selbst gegen die
Sperrliste (Verteidigung in der Tiefe, siehe `ExecHandle.run()`-Docstring). `ws`,
`connectors.get_client`, `scheduler.trigger` werfen weiterhin `NotImplementedError`
mit einem Verweis auf das WP, das die fehlende Grundlage liefert
(Scheduler-Ausfuehrung/WS-Multiplex-Hub: WP-6) -- die Permission-Pruefung laeuft aber
auch fuer sie zuerst, damit ein Test beweisen kann, dass die Reihenfolge stimmt.
"""

from __future__ import annotations

import ipaddress
import logging
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
    Severity,
    WidgetSpec,
)
from nodvard_sdk.actions import ActionRequest, ActionSpec
from nodvard_sdk.context import SecretHandleRef
from nodvard_sdk.errors import HostUnreachable, PermissionDenied
from sqlalchemy import select

from ..core import rbac, ssh, vault
from ..core.events import get_event_bus
from ..db.base import refresh_relationships
from ..db.session import session_scope
from ..models import Action, Host, HostTag, Setting
from ..services import audit as audit_service
from ..services.hosts import host_to_sdk as _host_to_sdk
from .runtime import ExtensionRuntime, LoadedExtension, SpawnTarget, SupervisedTask

logger = logging.getLogger("nodvard_deck.ext")


class _PermissionChecker:
    def __init__(self, ext_id: str, granted_permissions: list[str]) -> None:
        self.ext_id = ext_id
        self._granted = granted_permissions

    def require(self, permission: str) -> None:
        if not rbac.has_permission(self._granted, permission):
            raise PermissionDenied(self.ext_id, permission)




class ApiHandle:
    """**Live gefunden beim Bau der ntfy-Extension (WP-6), nicht beim Lesen:** der
    Docstring von `include_router()` behauptete seit WP-3 woertlich, der Kern
    "uebernehme" Authentifizierung/Permission-Pruefung fuer montierte Extension-Routen
    (docs/02-EXTENSION-API.md §2: "kann nicht versehentlich einen ungeschuetzten
    Endpunkt veroeffentlichen"). Das war nie wahr -- `include_router()` haengte den
    Router unveraendert an, ohne jemals eine `Depends(...)`-Pruefung hinzuzufuegen.
    `hello-world`s `/vault-use-and-fail`/`/widgets/hello` liefen seit WP-3
    unauthentifiziert erreichbar, unbemerkt, weil kein Test je einen Request OHNE
    Token gegen eine Extension-Route stellte. Siehe Abnahmebericht fuer die volle
    Einordnung -- dieser Fix macht es Extensions MOEGLICH, eine Permission zu
    verlangen, zwingt aber KEINE bestehende Extension dazu (bewusst
    rueckwaertskompatibel: `permission=None` bleibt das bisherige, unauthentifizierte
    Verhalten)."""

    def __init__(self, loaded: LoadedExtension) -> None:
        self._loaded = loaded

    @property
    def current_actor(self) -> Any:  # noqa: ANN401 - eine FastAPI-Dependency
        """D-15: wer ruft diese Extension-Route auf? Als FastAPI-Dependency deklarieren --
        `actor: Actor = Depends(ctx.api.current_actor)` -- und `actor` als `proposed_by`
        bzw. im Audit-Log verwenden. Liefert `Actor.user(id, username)` des angemeldeten
        Nutzers (401 ohne gueltigen Token, wie jede Kern-Route)."""
        from fastapi import Depends
        from nodvard_sdk import Actor

        from ..api.deps import get_current_user

        # Depends als Default statt Annotated-Typ: dieses Modul nutzt
        # `from __future__ import annotations`, ein lokal importierter Typ waere fuer
        # FastAPIs Signatur-Aufloesung nur ein unbekannter String (-> Query-Parameter).
        async def _current_actor(user: Any = Depends(get_current_user)) -> Actor:  # noqa: B008
            return Actor.user(user.id, user.username)

        return _current_actor

    def include_router(
        self, router: Any, *, prefix: str = "", tags: list[str] | None = None, permission: str | None = None
    ) -> None:
        """Montiert NICHT sofort -- registriert nur (docs/02 §1: setup() darf kein
        I/O machen). Die eigentliche Montage unter /api/v1/ext/<id>/... macht der
        Host, NACHDEM setup() erfolgreich durchgelaufen ist (services/extensions.py).

        `permission`, falls gesetzt, gilt fuer ALLE Routen dieses Routers (kein
        Pro-Route-Feingranulat in dieser Runde) -- reicht fuer den ersten echten
        Bedarf (ntfy: ein einzelner Token-Endpunkt), ist aber kein Ersatz fuer eine
        spaetere, feinere Loesung, falls eine Extension einmal Routen mit
        UNTERSCHIEDLICHEN Berechtigungen im selben Router braucht."""
        if self._loaded.router is None:
            from fastapi import APIRouter

            self._loaded.router = APIRouter()

        dependencies = []
        if permission is not None:
            from fastapi import Depends

            from ..api.deps import require_permission

            dependencies.append(Depends(require_permission(permission)))
        self._loaded.router.include_router(router, prefix=prefix, tags=tags, dependencies=dependencies)


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
        """Reine Bestandsaufnahme -- die eigentliche Navigation ergibt sich aus den
        `nav_section`/`nav_order`-Feldern der registrierten PageSpecs (GET /pages).
        Diese Methode existiert fuer Extensions, die NUR eine Nav-Sektion anlegen
        wollen, ohne selbst eine Seite zu registrieren (z. B. um eine leere Sektion
        vorzubereiten, die eine andere Extension spaeter befuellt)."""
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
        """Nur Capabilities von Extensions, die in `requires` deklariert sind
        (docs/02 §2: 'andere Extensions nur ueber ctx.events und deklarierte
        requires')."""
        return self._runtime.capabilities.query(protocol, allowed_ext_ids=self._allowed)


class HostsHandle:
    def __init__(self, perm: _PermissionChecker, ext_id: str) -> None:
        self._perm = perm
        self._ext_id = ext_id

    async def list(self, *, tag: str | None = None, group: str | None = None) -> list[SdkHost]:
        """Live gefunden (WP-10-Vorarbeit, scripts-Extension): `group` war seit WP-3
        im Protokoll deklariert, wurde in der eigenen, hier dupliziert gehaltenen
        Query aber nie ausgewertet -- ein Aufruf mit `group=...` lieferte still ALLE
        Hosts statt der gefilterten Teilmenge, kein Fehler, nur ein falsches Ergebnis.
        `HostGroup` (models/infra.py) tragt bereits im Docstring "Zielgruppen fuer
        Skripte" -- genau das, was diese Extension jetzt braucht. Delegiert deshalb an
        `services.hosts.list_hosts()`, das den Join auf `host_group_members` schon
        korrekt implementiert (siehe test_services_hosts.py), statt die Query ein
        zweites Mal von Hand zu pflegen."""
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
                    # docs/00-DECISIONS.md D-12: lazy="selectin" auf einem frisch
                    # angelegten Objekt braucht ein explizites Refresh, sonst
                    # MissingGreenlet beim naechsten Zugriff auf `.tags`. "credentials"
                    # dazugekommen mit dem has_credential-Nachtrag (_host_to_sdk()
                    # liest jetzt auch das) -- derselbe Grund, dieselbe Regel.
                    await refresh_relationships(session, host, "tags", "credentials")
                    for tag in dh.tags:
                        host.tags.append(HostTag(tag=tag, managed_by_ext_id=self._ext_id))
                    await session.flush()
                else:
                    host.display_name = dh.display_name or host.display_name
                    # Live gefunden (WP-12-Nachtrag, echte Windows-VM gegen einen
                    # echten Proxmox-Knoten): Proxmox' eigene API kennt die tatsaechliche
                    # Gast-IP einer VM/eines LXC nicht (kein qemu-guest-agent-Abruf) --
                    # `discover_hosts()` liefert dafuer ehrlich nur `connector.host` (die
                    # Proxmox-API-Adresse SELBST) als Platzhalter. Ein unbedingtes
                    # `host.address = dh.address` liess jeden 5-Minuten-Resync eine
                    # manuell auf die echte Gast-IP korrigierte Adresse wieder mit diesem
                    # Platzhalter ueberschreiben -- beobachtet an einer Windows-VM
                    # (Gast-IP z. B. 192.168.2.46, Proxmox-Platzhalter 192.168.2.41), was
                    # gameservers Join-Code-Abruf per SSH strukturell unmoeglich machte
                    # (SSH landete auf dem Proxmox-Host, nicht auf dem Windows-Gast). Fuer
                    # `kind="node"` bleibt das alte Verhalten: die Node-eigene Adresse IST
                    # die Proxmox-API-Adresse und darf sich legitim aendern.
                    #
                    # Nachtrag (beobachtet: die Terminal-Liste zeigte zwei VMs mit
                    # der Knoten-IP): meldet der Anbieter eine vom GAST selbst stammende
                    # Adresse (`address_verified`, z. B. per Gast-Agent), ist das die
                    # Wahrheit und wird uebernommen -- ein Platzhalter weiterhin nie.
                    if host.kind not in ("vm", "lxc") or dh.address_verified:
                        if host.address != dh.address:
                            # Eine gepoolte SSH-Verbindung ginge sonst weiter an die alte Adresse.
                            from ..services.hosts import drop_pooled_connections

                            await drop_pooled_connections(session, host.id)
                        host.address = dh.address
                    host.status = dh.status.value
                    host.host_metadata = dh.metadata
                    # Live gefunden (Server-Seiten): eine Windows-VM stand als
                    # "linux" in Nodvard Deck -- os_family wurde nur beim ANLEGEN gesetzt, und
                    # nur mit dem Standardwert. Jetzt: uebernehmen, wenn der Anbieter es
                    # WIRKLICH meldet (explizit gesetzt); sonst bliebe eine manuelle
                    # Korrektur nicht stehen.
                    if "os_family" in dh.model_fields_set:
                        host.os_family = dh.os_family
                    # `DiscoveredHost.tags` wurde hier bisher stillschweigend verworfen
                    # (live gefunden beim Bau der proxmox-Extension, WP-8) -- weder beim
                    # Anlegen noch beim erneuten Abgleich landete je ein Tag in der DB.
                    # Set-Abgleich statt Ersetzen, damit die cascade="all, delete-orphan"-
                    # Beziehung (models/infra.py) verschwundene Tags korrekt entfernt.
                    #
                    # Live gefunden (WP-12): NUR Tags entfernen, die DIESE Extension
                    # selbst zuvor gesetzt hat (`managed_by_ext_id`, siehe HostTag-
                    # Docstring) -- sonst loescht ein Discovery-Zyklus einen manuell
                    # oder von einer anderen Extension gesetzten Tag (z. B. gameserver
                    # taggt einen proxmox-entdeckten Host mit "gameserver").
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
    """Der gemeinsame SSH-Layer aus `core/ssh.py`, docs/00 D-05 zufolge geteilt von
    Terminal, Skript-Ausfuehrung und KI-Remediation -- `ctx.exec` ist die Stelle,
    an der Extensions ihn erreichen. Loest die Zugangsdaten je Aufruf frisch ueber
    den Vault auf (`services.hosts.resolve_connection_target`); der Klartext lebt
    nur kurzlebig im Aufrufrahmen (docs/03 §3, Invariante 2)."""

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
                # Typisch fuer einen von Hand angelegten Server, der schon getaggt, aber noch ohne SSH-Zugang ist.
                raise HostUnreachable(
                    f"Für „{host.display_name or host.name}“ ist noch kein SSH-Zugang eingerichtet "
                    "(Einstellungen → Server & Zugänge)."
                )
            target = await hosts_service.resolve_connection_target(session, settings, db_host, credential)
            return target, credential.id, settings

    async def _connection(self, host: SdkHost) -> asyncssh.SSHClientConnection:
        target, credential_id, settings = await self._resolve(host)
        async with session_scope() as session:
            return await ssh.get_ssh_pool().get(
                session, target, credential_id=credential_id, connect_timeout_s=settings.ssh_connect_timeout_s
            )

    async def run(
        self, host: SdkHost, command: str, *, timeout_s: int = 60, user: str | None = None,
        log_command: str | None = None,
    ) -> ExecResult:
        """Nur fuer *lesende* Kommandos ohne Nebenwirkung (docs/02 §2) -- Aendernde
        Aktionen gehen ueber `ctx.actions.propose()`, niemals hierueber.

        **Verteidigung in der Tiefe (WP-5):** Extensions sind vertrauenswuerdiger
        Code, keine Sandbox (docs/01 §7) -- `ctx.exec.run()` ist deshalb technisch
        NICHT auf lesende Kommandos beschraenkt, nur per Konvention. Damit ein Fehler
        in einer Extension (oder ein Missverstaendnis der obigen Konvention) nicht
        unbemerkt an der Sperrliste vorbei ein `rm -rf /` verschickt, laeuft JEDER
        `ctx.exec.run()`-Befehl trotzdem durch dieselbe `core.deny_patterns`-Pruefung
        wie ein `ctx.actions.propose()`-Vorschlag, BEVOR er die Verbindung erreicht --
        ohne Action-Zeile (das ist kein Gate-Vorschlag, den jemand bestaetigen
        koennte), aber mit Audit-Zeile, damit ein blockierter Versuch sichtbar
        bleibt.

        `log_command`: Enthaelt `command` Geheimnisse (z. B. ein Skript mit Tresor-Wert),
        steht in der Audit-Zeile eines Sperrlisten-Treffers stattdessen diese maskierte
        Fassung."""
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
        """Sperrlisten-Pruefung fuer JEDES Kommando, das eine Extension selbst
        verschickt (`run()` UND `stream()`) -- siehe `run()`-Docstring."""
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
        """Live-Logs (Container-Verwaltung): wie `run()` nur fuer LESENDE
        Kommandos, aber die Ausgabe kommt laufend als Byte-Stuecke statt erst am Ende
        -- `docker logs -f` endet nie von selbst. Liefert einen Kontextmanager, der
        einen `AsyncIterator[bytes]` liefert; beim Verlassen wird der entfernte Prozess
        beendet (PTY-Hangup, siehe `core.ssh.open_command_stream()`). Dieselbe
        Berechtigung und dieselbe Sperrliste wie `run()`, geprueft beim Betreten --
        bevor irgendeine Verbindung entsteht."""
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

    def open_shell(self, host: SdkHost, *, cols: int, rows: int) -> Any:
        self._perm.require("hosts.execute")

        @asynccontextmanager
        async def _cm():
            conn = await self._connection(host)
            async with ssh.open_shell(conn, cols=cols, rows=rows) as process:
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


class SecretsHandle:
    def __init__(self, perm: _PermissionChecker, ext_id: str, settings: Any) -> None:
        self._perm = perm
        self._ext_id = ext_id
        self._settings = settings

    async def get_handle(self, label: str) -> SecretHandleRef:
        self._perm.require(f"secrets.read:{label}")
        async with session_scope() as session:
            handle = await vault.get_handle(session, label)
            if handle is None:
                from nodvard_sdk.errors import SecretUnavailable

                raise SecretUnavailable(f"Secret '{label}' existiert nicht.")
            return handle

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


class SettingsHandle:
    def __init__(self, loaded: LoadedExtension, ext_id: str) -> None:
        self._loaded = loaded
        self._ext_id = ext_id
        self._notifying = False

    def declare(self, schema: dict[str, Any]) -> None:
        self._loaded.settings_schema = schema

    async def get(self) -> dict[str, Any]:
        from ..models import ExtensionRecord

        async with session_scope() as session:
            record = await session.get(ExtensionRecord, self._ext_id)
            return dict(record.settings) if record else {}

    async def set(self, values: dict[str, Any]) -> None:
        """**Ehrlich abgegrenzt:** keine JSON-Schema-Validierung gegen `declare()` in
        dieser Runde -- das braucht eine `jsonschema`-Abhaengigkeit, die noch niemand
        sonst im Projekt zieht. Persistiert unvalidiert; die Validierung nachzuruesten
        ist risikofrei moeglich (reiner Zusatz-Check vor dem Schreiben)."""
        from ..models import ExtensionRecord

        async with session_scope() as session:
            record = await session.get(ExtensionRecord, self._ext_id)
            if record is not None:
                record.settings = values
                await session.flush()

        # Gefunden beim Bau der Server-Seiten: das SDK versprach den Hook
        # `on_settings_changed`, der Kern rief ihn aber nie auf. Erst NACH dem Commit,
        # damit der Hook per get() schon die neuen Werte liest. Ein set() aus dem Hook
        # heraus loest ihn nicht erneut aus (keine Endlosschleife); ein Fehler im Hook
        # darf das bereits gespeicherte set() nicht nachtraeglich scheitern lassen.
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
    """Seit WP-6 echt: `register_job()` legt/aktualisiert die `jobs`-Zeile (Upsert
    ueber `(ext_id, spec.id)`, docs/00 D-08 -- ein erneutes Enable dupliziert den
    Job nicht mehr) UND meldet den Handler beim Kern-Scheduler an, der ihn bei
    Faelligkeit wirklich ausfuehrt.

    **Dokumentierte Abweichung vom SDK-Protokoll:** `SchedulerHandle.register_job`
    ist dort als SYNCHRONE Methode deklariert, legt aber eine DB-Zeile an -- ein
    echtes I/O. Sync mit `asyncio.ensure_future()` im Hintergrund "loesen" waere die
    naheliegende Abkuerzung, hat aber zwei echte Maengel: (1) eine Exception
    verschwindet spurlos, (2) kein deterministischer Zeitpunkt, zu dem der Aufrufer
    sich auf die Zeile verlassen kann -- exakt das Muster, das WP-1 beim
    MissingGreenlet-Fund schon einmal teuer gemacht hat. `@runtime_checkable` prueft
    nur Attributnamen, keine Signaturen (siehe `ext/runtime.py` `SupervisedTask` fuer
    dieselbe Abwaegung bei `ctx.spawn`) -- deshalb ist `async def` hier
    protokollkonform. hello-world ruft entsprechend `await ctx.scheduler.
    register_job(...)` auf."""

    def __init__(self, perm: _PermissionChecker, ext_id: str, runtime: ExtensionRuntime) -> None:
        self._perm = perm
        self._ext_id = ext_id
        self._runtime = runtime

    async def register_job(self, spec: JobSpec) -> None:
        from ..services import jobs as jobs_service
        from ..core.scheduler import get_scheduler_service

        self._perm.require("schedule.register")
        async with session_scope() as session:
            row = await jobs_service.upsert_job(
                session, ext_id=self._ext_id, ext_job_key=spec.id, name=spec.name, kind="ext",
                schedule=spec.schedule, params=spec.params, enabled=spec.enabled,
            )
            job_id = row.id

        self._runtime.scheduler.register(self._ext_id, spec.id, spec.handler)

        async with session_scope() as session:
            job = await jobs_service.get_job(session, job_id)
            await get_scheduler_service().schedule(job, spec.handler)

    async def trigger(self, job_id: str, **params: Any) -> str:
        """`job_id` ist `spec.id` (der von der Extension selbst gewaehlte Schluessel,
        siehe `Job.ext_job_key`-Docstring), NICHT die server-generierte `Job.id` --
        eine Extension bekommt letztere nie zu sehen (`register_job()` liefert laut
        SDK-Vertrag nichts zurueck)."""
        from ..services import jobs as jobs_service
        from ..core.scheduler import get_scheduler_service

        from nodvard_sdk.errors import NodvardError

        self._perm.require("schedule.register")
        handler = self._runtime.scheduler.get(self._ext_id, job_id)
        if handler is None:
            raise NodvardError(f"Job '{job_id}' ist für Extension '{self._ext_id}' nicht registriert.")

        async with session_scope() as session:
            job = await jobs_service.get_job_by_key(session, ext_id=self._ext_id, ext_job_key=job_id)
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
        """Beim Entladen der Extension: sonst feuern die Handler der alten Instanz nach
        dem naechsten setup() zusammen mit denen der neuen (doppelte Reaktionen)."""
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
        return NotifyResult(notification_id=row.id, suppressed=suppressed)

    async def would_suppress(self, *, host_id: str | None = None, host_ids: Sequence[str] | None = None) -> bool:
        """Nur lesen: dieselbe Pruefung wie in `send()`, ohne Verlaufseintrag."""
        from ..services import notifications as notifications_service

        self._perm.require("notify.send")
        payload: dict[str, Any] = {}
        if host_id is not None:
            payload["host_id"] = host_id
        if host_ids is not None:
            # Unveraendert weitergeben: ob die Liste taugt, entscheidet dieselbe Pruefung
            # wie in send() (ein einzelner String etwa ist keine Liste, also nie still).
            payload["host_ids"] = host_ids
        async with session_scope() as session:
            return await notifications_service.would_suppress(session, payload)


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
        """`actor` (D-15): der Mensch hinter dem Klick, aus `ctx.api.current_actor`. Dann
        steht ER im Audit-Log, die Extension nur als `detail.via` -- sonst wie bisher
        die Extension selbst."""
        self._perm.require("audit.write")
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
    def __init__(self, ext_id: str) -> None:
        self._ext_id = ext_id

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        from ..core.ws_hub import get_ws_hub

        await get_ws_hub().publish(f"ext.{self._ext_id}.{channel}", payload)


class ConnectorsHandle:
    def __init__(self, ext_id: str) -> None:
        self._ext_id = ext_id
        self._types: dict[str, ConnectorType] = {}

    def register_type(self, connector_type: ConnectorType) -> None:
        self._types[connector_type.id] = connector_type

    async def instances(self, type_id: str | None = None) -> list[Any]:
        from ..models import ConnectorInstance

        async with session_scope() as session:
            stmt = select(ConnectorInstance).where(ConnectorInstance.ext_id == self._ext_id)
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
    """`propose()` geht seit WP-5 durch `core.gate.propose()` -- die Permission-
    Pruefung hier (Schritte 1+2 im docs/01-§4-Diagramm) bleibt Sache dieses Handles,
    weil nur es die GRANTED_PERMISSIONS der jeweiligen Extension kennt; das Gate selbst
    kennt keine Extensions, nur Aktionen (Schritte 3-6)."""

    def __init__(self, perm: _PermissionChecker, ext_id: str, runtime: ExtensionRuntime, settings: Any) -> None:
        self._perm = perm
        self._ext_id = ext_id
        self._runtime = runtime
        self._settings = settings

    def register(self, spec: ActionSpec) -> None:
        self._runtime.actions.register(self._ext_id, spec)

    async def propose(self, request: ActionRequest, *, wait_s: float | None = None) -> GateDecision:
        """`wait_s` begrenzt bei Vollautonomie das Warten auf das Ergebnis;
        None = bis zum Ende, wie bisher."""
        from ..core import gate as gate_service

        entry = self._runtime.actions.get(request.action_type)
        spec = entry[1] if entry is not None else None
        if spec is not None:
            for perm in spec.permissions:
                self._perm.require(perm)
        else:
            self._perm.require("hosts.execute")

        async with session_scope() as session:
            return await gate_service.propose(
                session,
                ext_id=self._ext_id,
                request=request,
                command_field=spec.command_field if spec is not None else None,
                wait_s=wait_s,
            )

    async def list(self, *, correlation_id: str | None = None, limit: int = 20) -> list[Action]:
        """Die juengsten Aktionen DIESER Extension, optional nur zu einer
        `correlation_id` (z. B. alle Laeufe eines Skripts) -- wie `result()` nie fremde."""
        from sqlalchemy import select

        stmt = select(Action).where(Action.ext_id == self._ext_id)
        if correlation_id is not None:
            stmt = stmt.where(Action.correlation_id == correlation_id)
        stmt = stmt.order_by(Action.created_at.desc()).limit(max(1, min(limit, 200)))
        async with session_scope() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def proposer_labels(self, rows: list[Action]) -> dict[str, str]:
        """`{action_id: Name des Vorschlagenden}` -- mit je einer Abfrage fuer
        Nutzer und Erweiterungen. Aufgeloest werden nur Zeilen DIESER Extension, und zwar
        anhand der Datenbank: von `rows` zaehlt nur die `id`, eine selbstgebaute Zeile mit
        fremder Nutzer-ID bekommt den rohen Wert zurueck -- sonst liesse sich mit ihr jede
        Nutzer-ID in einen Namen umsetzen.

        Wer den Namen sieht, prueft der Kern hier NICHT (die Extension kennt den Aufrufer
        nicht): die Route der Extension muss selbst abgesichert sein. Die Skripte-Seite
        verlangt `hosts.execute`, also mindestens Bediener."""
        from sqlalchemy import select

        from ..services.actor_labels import load_actor_labels, raw_actor

        out = {row.id: raw_actor(row.proposed_by_type, row.proposed_by_id) for row in rows}
        if not out:
            return {}
        stmt = select(Action).where(Action.id.in_(list(out)), Action.ext_id == self._ext_id)
        async with session_scope() as session:
            own = list((await session.execute(stmt)).scalars().all())
            labels = await load_actor_labels(session, own, viewer=None)
        out.update({row.id: labels.proposed_by(row) for row in own})
        return out

    async def result(self, action_id: str) -> Action | None:
        """Nur Aktionen, die DIESE Extension selbst vorgeschlagen hat -- ein fremdes
        `action_id` zu erraten und dessen Payload/Ergebnis einzusehen waere ein
        Informationsleck ueber `ctx.actions` hinaus (docs/02 §2 kennt keine
        Cross-Extension-Sicht ausser ueber `requires`+Capabilities)."""
        async with session_scope() as session:
            row = await session.get(Action, action_id)
            if row is None or row.ext_id != self._ext_id:
                return None
            return row


class HttpHandle:
    """CIDR-Pruefung nur fuer literale IP-Ziele (kein DNS-Resolving vor dem Check in
    dieser Runde, siehe Abnahmebericht) -- ein DNS-Name braucht mindestens eine
    beliebige `net.outbound`/`net.outbound:<cidr>`-Berechtigung, ein literales
    IP-Ziel muss zusaetzlich in eine der gewaehrten CIDR-Bereiche fallen.

    **`insecure_tls`, WP-8-Blocker-Nachtrag:** `httpx.AsyncClient` erlaubt `verify=`
    nur bei der Konstruktion, nicht pro Anfrage (geprueft gegen httpx 0.28: `request()`
    hat kein `verify`-Keyword) -- EIN geteilter Client fuer alle Aufrufe einer
    Extension kann Zertifikatspruefung deshalb nicht selektiv abschalten. Proxmox VE
    liefert standardmaessig ein selbstsigniertes Zertifikat aus (der konkrete Anlass:
    pve1 im echten Homelab) -- ohne diesen Ausweg schlaegt JEDE Verbindung zu einem
    solchen Host mit einem Zertifikatsfehler fehl, WP-8s proxmox-Extension war damit
    gegen einen echten Proxmox-Host nie einsatzbereit. Loesung: ein ZWEITER,
    lazy angelegter Client mit `verify=False`, nur fuer Aufrufe mit
    `insecure_tls=True` -- der Normalfall (kein Kwarg) bleibt unveraendert streng.
    Eigene Berechtigung `net.outbound.insecure_tls` (nicht automatisch mit
    `net.outbound` mitgewaehrt) macht das im Manifest sichtbar und
    bestaetigungspflichtig, statt es an `net.outbound` huckepack zu haengen -- ein
    Admin, der `net.outbound` erlaubt, erlaubt damit nicht automatisch, dass die
    Extension sich gegen jede MITM-Manipulation innerhalb dieses Bereichs blind
    macht. Kein Pro-Host-CA-Pinning in dieser Runde (nur an/aus) -- fuer den
    tatsaechlichen Bedarf (ein einzelnes selbstsigniertes Standardzertifikat je
    Connector-Instanz) reicht das; siehe docs/00-DECISIONS.md."""

    def __init__(self, perm: _PermissionChecker, granted_permissions: list[str]) -> None:
        self._perm = perm
        self._cidrs = [
            p.split(":", 1)[1] if ":" in p else None
            for p in granted_permissions
            if p == "net.outbound" or p.startswith("net.outbound:")
        ]
        self._client = httpx.AsyncClient()
        self._insecure_client: httpx.AsyncClient | None = None

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
            return  # DNS-Name: mindestens eine net.outbound-Berechtigung reicht (oben geprueft)

        for cidr in self._cidrs:
            if cidr is None or ip in ipaddress.ip_network(cidr, strict=False):
                return
        raise PermissionDenied(self._perm.ext_id, f"net.outbound:{host}")

    def _client_for(self, *, insecure_tls: bool) -> httpx.AsyncClient:
        if not insecure_tls:
            return self._client
        self._perm.require("net.outbound.insecure_tls")
        if self._insecure_client is None:
            self._insecure_client = httpx.AsyncClient(verify=False)
        return self._insecure_client

    async def get(self, url: str, *, insecure_tls: bool = False, **kwargs: Any) -> Any:
        self._require_target_allowed(url)
        return await self._client_for(insecure_tls=insecure_tls).get(url, **kwargs)

    async def post(self, url: str, *, insecure_tls: bool = False, **kwargs: Any) -> Any:
        self._require_target_allowed(url)
        return await self._client_for(insecure_tls=insecure_tls).post(url, **kwargs)

    async def request(self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any) -> Any:
        self._require_target_allowed(url)
        return await self._client_for(insecure_tls=insecure_tls).request(method, url, **kwargs)

    def stream(self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any):  # noqa: ANN201 - httpx.AsyncClient.stream()s eigener Typ
        """Fehlte bisher komplett (WP-9 Nachtrag: `AIProvider.stream()` musste
        deshalb das VOLLSTAENDIGE Ergebnis als einen Chunk liefern) -- die
        nextcloud-Extension (WP-11) braucht echtes Antwort-Streaming fuer
        `FileSource.open_read()` gegen potenziell grosse Dateien, ohne sie
        vollstaendig in den Prozessspeicher zu laden. `request()`/`get()`/`post()`
        oben lesen den GESAMTEN Body, bevor sie zurueckkehren -- fuer eine
        Chat-Antwort tolerierbar (WP-9), fuer eine mehrere GB grosse Datei nicht.
        Zweiter unabhaengiger Bedarf fuer dasselbe fehlende Primitiv; anders als bei
        D-13/D-14/D-15 (dort: strukturelle Aenderungen mit unklarem "richtigen"
        Umfang, deshalb bis zum dritten Fall zurueckgestellt) ist die Form hier von
        Anfang an eindeutig -- ein duenner Wrapper um `httpx.AsyncClient.stream()`,
        genau wie `get`/`post`/`request` es fuer die nicht-streamende Variante schon
        sind -- deshalb sofort ergaenzt statt zurueckgestellt."""
        self._require_target_allowed(url)
        return self._client_for(insecure_tls=insecure_tls).stream(method, url, **kwargs)

    def websocket(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        subprotocols: list[str] | None = None,
        insecure_tls: bool = False,
        open_timeout_s: float = 10.0,
    ):  # noqa: ANN201 - AsyncContextManager[websockets ClientConnection]
        """Ausgehender WebSocket (fuer die Konsole: eine Hypervisor-Konsole ist nur
        ueber einen WebSocket erreichbar, httpx kann keine WebSockets). Dieselben
        Pruefungen wie die HTTP-Methoden, in derselben Reihenfolge und VOR jedem
        Verbindungsaufbau: Zielpruefung (`net.outbound`/CIDR), dann `insecure_tls`
        nur mit `net.outbound.insecure_tls`. Die Pruefungen laufen beim Aufruf, nicht
        erst beim `async with` -- ein verbotenes Ziel scheitert damit sofort.

        `max_size=None`: ein Bildschirm-Update ist ein einzelner grosser Frame, das
        websockets-Default (1 MiB) wuerde eine Konsole mit hoher Aufloesung mitten in
        der Sitzung abbrechen. `compression=None`: RFB-Rahmen sind bereits kodiert,
        permessage-deflate kostet nur CPU (auf dem Pi spuerbar)."""
        import ssl

        from websockets.asyncio.client import connect

        self._require_target_allowed(url)
        ssl_context: ssl.SSLContext | None = None
        if url.startswith("wss://"):
            if insecure_tls:
                self._perm.require("net.outbound.insecure_tls")
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            else:
                ssl_context = ssl.create_default_context()

        return connect(
            url,
            additional_headers=headers,
            subprotocols=subprotocols,  # type: ignore[arg-type]
            ssl=ssl_context,
            open_timeout=open_timeout_s,
            max_size=None,
            compression=None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._insecure_client is not None:
            await self._insecure_client.aclose()


class DbHandle:
    """Liefert eine echte Session (`ctx.db.session()`, dieselbe `session_scope()` wie
    im Kern) UND, seit der inventory-Extension (docs/02-EXTENSION-API.md §7: "eigener
    Alembic-Branch" -- inventory ist die ERSTE Extension mit echten eigenen
    Tabellen), einen echten Torwaechter: `declare_tables(metadata)` prueft die
    `MetaData` einer Extension EINMAL beim `setup()`-Aufruf gegen ihren
    Pflicht-Praefix (`ext.tables.validate_table_prefix()`, vorher nur synthetisch
    getestet, nie von echtem Code aufgerufen). **Weiterhin bewusst abgegrenzt:**
    kein Statement-Introspektions-Schutz zur Laufzeit (jede einzelne Query
    durchleuchten waere ein eigener, hier nicht gebauter Aufwand) -- `declare_tables()`
    ist eine Vertrauens-, keine Sandbox-Grenze, genau wie `ctx.exec` fuer
    Extension-Code generell (docs/02 §3: Extensions sind vertrauenswuerdiger Code)."""

    def __init__(self, ext_id: str, table_prefix: str) -> None:
        self._ext_id = ext_id
        self._table_prefix = table_prefix

    def session(self):  # noqa: ANN201
        return session_scope()

    def declare_tables(self, metadata: Any) -> None:
        """`metadata` ist eine SQLAlchemy `MetaData` (typischerweise `Base.metadata`
        der Extension). Wirft `ext.tables.TablePrefixViolation`, wenn irgendeine
        deklarierte Tabelle nicht mit `ext_<id>_` beginnt -- laesst die Extension
        NICHT laden (wird in `services/extensions.py` beim `setup()`-Aufruf sichtbar,
        derselbe Fehler-Isolations-Pfad wie jeder andere `setup()`-Fehler)."""
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
    """Die `Settings`-Instanz, die beim Laden dieser Extension aktiv war (siehe
    `services.extensions.enable_extension()`) -- NICHT `config.get_settings()`
    direkt. Live gefunden: ein Handle, das intern `get_settings()` aufruft, ignoriert
    stillschweigend, mit welchen Settings der Host gerade laeuft (in Tests: die echten
    Projektpfade statt der isolierten Test-Settings -- derselbe Fallstrick wie der
    `get_settings()`-Singleton aus WP-1, nur eine Ebene tiefer)."""

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
    perm = _PermissionChecker(manifest.id, granted_permissions)
    ext_data_dir.mkdir(parents=True, exist_ok=True)
    return Context(
        ext_id=manifest.id,
        version=manifest.version,
        data_dir=PurePosixPath(str(ext_data_dir)),
        api=ApiHandle(loaded),
        ui=UiHandle(runtime, manifest.id),
        connectors=ConnectorsHandle(manifest.id),
        capabilities=CapabilitiesHandle(runtime, manifest.id, manifest.requires),
        actions=ActionsHandle(perm, manifest.id, runtime, settings),
        hosts=HostsHandle(perm, manifest.id),
        exec=ExecHandle(perm, settings),
        secrets=SecretsHandle(perm, manifest.id, settings),
        settings=SettingsHandle(loaded, manifest.id),
        scheduler=SchedulerHandle(perm, manifest.id, runtime),
        events=EventsHandle(manifest.id),
        notify=NotifyHandle(perm, manifest.id),
        audit=AuditHandle(perm, manifest.id),
        ws=WsHandle(manifest.id),
        db=DbHandle(manifest.id, manifest.table_prefix),
        http=HttpHandle(perm, granted_permissions),
        logger=logging.getLogger(f"nodvard_deck.ext.{manifest.id}"),
        _loaded=loaded,
        _app_settings=settings,
    )
