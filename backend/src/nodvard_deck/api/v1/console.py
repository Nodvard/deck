"""Grafische Konsole (Bildschirm einer VM oder eines Containers), im Ablauf wie `terminal.py`. Die Sitzung wird
schon beim `POST` geöffnet, weil das Einmal-Kennwort für die RFB-Authentifizierung erst dabei entsteht
(`core/console_sessions.py`)."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from nodvard_sdk.capabilities import ConsoleTarget
from pydantic import BaseModel

from ...config import Settings, get_settings
from ...core.console_sessions import get_console_session_store
from ...db import refresh_relationships
from ...db.session import session_scope
from ...ext.runtime import get_extension_runtime
from ...models import Host
from ...services import audit as audit_service
from ...services import hosts as hosts_service
from ...services import session_guard
from ...services.hosts import host_to_sdk
from ..deps import CurrentSessionId, CurrentUser, SessionDep, require_permission

router = APIRouter(tags=["console"])


class ConsoleSessionCreate(BaseModel):
    host_id: str


class ConsoleSessionOut(BaseModel):
    session_id: str
    ws_url: str
    protocol: str
    password: str | None
    """Einmal-Kennwort für die protokolleigene Authentifizierung (RFB), gilt nur für diese geöffnete Sitzung."""


# Der Kern kennt weder Hypervisor noch Bildschirmprotokoll: der Anbieter kommt aus der Capability-Registry,
# `protocol` und `password` gehen unverändert an den Browser, danach werden nur Bytes gepumpt.
async def _find_console_target(host: Any) -> Any | None:
    for candidate in get_extension_runtime().capabilities.query(ConsoleTarget):
        try:
            if await candidate.console_available(host):
                return candidate
        except Exception:  # noqa: BLE001 - ein kaputter Anbieter darf andere nicht verdecken
            continue
    return None


@router.get("/console/hosts", dependencies=[Depends(require_permission("hosts.read"))])
async def list_console_hosts(session: SessionDep) -> list[str]:
    """IDs aller Hosts, für die gerade ein Anbieter eine Konsole liefern kann; nur dort zeigt die Oberfläche den Knopf."""
    targets = get_extension_runtime().capabilities.query(ConsoleTarget)
    if not targets:
        return []
    result: list[str] = []
    for host in await hosts_service.list_hosts(session):
        if await _find_console_target(host_to_sdk(host)) is not None:
            result.append(host.id)
    return result


@router.post("/console/sessions", dependencies=[Depends(require_permission("hosts.execute"))])
async def create_console_session(
    payload: ConsoleSessionCreate,
    session: SessionDep,
    user: CurrentUser,
    settings: Annotated[Settings, Depends(get_settings)],
    login_id: CurrentSessionId,
) -> ConsoleSessionOut:
    host = await session.get(Host, payload.host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    # Ein Host, der in dieser Session als frisch angelegtes Objekt lebt, hat `credentials` nicht geladen,
    # `host_to_sdk()` liest es synchron.
    await refresh_relationships(session, host, "tags", "credentials")
    sdk_host = host_to_sdk(host)

    target = await _find_console_target(sdk_host)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Für diesen Host ist keine Konsole verfügbar.",
        )

    # Geöffnet wie gescheitert kommt jede Konsole ins Protokoll: ein Bildschirm ist wie physischer Tastaturzugriff.
    try:
        console = await target.open_console(sdk_host)
    except Exception as exc:  # noqa: BLE001 - dem Nutzer sichtbar machen, nicht als 500 verstecken
        await audit_service.log(
            session, actor_type="user", actor_id=user.id, action="console.open", outcome="failure",
            target_type="host", target_id=host.id, reason=str(exc)[:500],
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Konsole konnte nicht geöffnet werden: {exc}"
        ) from exc

    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="console.open", outcome="success",
        target_type="host", target_id=host.id, detail={"protocol": console.protocol},
    )
    pending = get_console_session_store().add(
        host_id=host.id, user_id=user.id, session=console, ttl_s=settings.terminal_session_ttl_s,
        stamp=session_guard.credential_stamp(user), login_id=login_id,
    )
    return ConsoleSessionOut(
        session_id=pending.session_id,
        ws_url=f"/api/v1/ws/console/{pending.session_id}",
        protocol=console.protocol,
        password=console.password,
    )


_WS_UNKNOWN_TICKET = 4404
_WS_ACCESS_ENDED = 4401
"""Konto deaktiviert, Berechtigung entzogen, Passwort geändert oder abgemeldet."""


@router.websocket("/ws/console/{session_id}")
async def console_ws(
    websocket: WebSocket, session_id: str, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    # noVNC fragt je nach Version das Subprotokoll "binary" an. Bestätigt der Server ein angefragtes Subprotokoll
    # nicht, verwirft der Browser den Handshake, deshalb spiegeln, wenn angefragt, sonst ohne.
    requested = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol="binary" if "binary" in requested else None)

    pending = get_console_session_store().take(session_id, ttl_s=settings.terminal_session_ttl_s)
    if pending is None:
        await websocket.close(code=_WS_UNKNOWN_TICKET)
        return
    console = pending.session

    watch = session_guard.SessionWatch(user_id=pending.user_id, stamp=pending.stamp, login_id=pending.login_id)

    async def _access_ended() -> str | None:
        async with session_scope() as db_session:
            return await watch.ended_reason(db_session, permission="hosts.execute")

    stop_watchdog = asyncio.Event()

    async def _watchdog() -> bool:
        """Beendet die Konsole, wenn Konto, Berechtigung oder Anmeldung nicht mehr gelten (die Prüfung beim Aufbau ist
        bis zu 30 s alt). `True` = Zugriff beendet, `False` = anders gestoppt."""
        # Wird nie abgebrochen, sondern über `stop_watchdog` gestoppt: eine mitten in der Datenbankabfrage
        # abgebrochene Aufgabe risse deren Verbindung mit.
        interval = max(0.05, settings.terminal_recheck_interval_s)
        while True:
            try:
                if await _access_ended() is not None:
                    return True
            except Exception:  # noqa: BLE001 - ein Datenbank-Hänger beendet keine laufende Konsole
                pass
            try:
                await asyncio.wait_for(stop_watchdog.wait(), timeout=interval)
                return False
            except asyncio.TimeoutError:
                pass

    async def _pump_host_to_client() -> None:
        try:
            async for chunk in console.read():
                await websocket.send_bytes(chunk)
        except Exception:  # noqa: BLE001 - Verbindungsende ist kein Serverfehler
            pass

    async def _pump_client_to_host() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    await console.write(data)
        except (WebSocketDisconnect, Exception):  # noqa: BLE001
            pass

    reader = asyncio.ensure_future(_pump_host_to_client())
    writer = asyncio.ensure_future(_pump_client_to_host())
    watchdog = asyncio.ensure_future(_watchdog())
    ended = False
    try:
        await asyncio.wait({reader, writer, watchdog}, return_when=asyncio.FIRST_COMPLETED)
        ended = watchdog.done() and not watchdog.cancelled() and watchdog.exception() is None and watchdog.result()
    finally:
        # Dieselbe Begrenzung wie in terminal.py: ein in `websocket.receive()` hängender Writer reagiert nicht immer
        # sofort auf `cancel()`, ohne Zeitlimit bliebe die Verbindung unbestimmt offen.
        reader.cancel()
        writer.cancel()
        stop_watchdog.set()
        try:
            await asyncio.wait_for(asyncio.gather(reader, writer, watchdog, return_exceptions=True), timeout=5.0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(console.close(), timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            await websocket.close(code=_WS_ACCESS_ENDED if ended else 1000)
        except Exception:  # noqa: BLE001 - möglicherweise schon zu
            pass
