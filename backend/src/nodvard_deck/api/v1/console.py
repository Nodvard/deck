"""Grafische Konsole (Bildschirm einer VM/eines Containers).

Dasselbe Muster wie `terminal.py` (Autorisierung beim `POST` ueber die normale
Bearer-Pruefung, danach ein kurzlebiges Einmal-Ticket im WS-Pfad), mit einem
Unterschied: die Sitzung wird schon beim `POST` GEOEFFNET, weil das Einmal-Kennwort
fuer die RFB-Authentifizierung erst dabei entsteht und der Browser es vor dem
WS-Aufbau braucht (Begruendung: `core/console_sessions.py`).

Kennt keinen Hypervisor: findet einen `ConsoleTarget`-Anbieter ueber die
Capability-Registry (docs/02 §3) und pumpt danach nur Bytes. Der Kern versteht das
Bildschirmprotokoll selbst nicht -- er reicht `protocol`/`password` an den Browser
durch, der Rest ist Sache von Client und Anbieter.

Jede geoeffnete (oder gescheiterte) Konsole landet im Audit-Log: ein Bildschirm ist
gleichwertig mit physischem Tastaturzugriff auf die Maschine.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from nodvard_sdk.capabilities import ConsoleTarget
from pydantic import BaseModel

from ...config import Settings, get_settings
from ...core.console_sessions import get_console_session_store
from ...db import refresh_relationships
from ...ext.runtime import get_extension_runtime
from ...models import Host
from ...services import audit as audit_service
from ...services import hosts as hosts_service
from ...services.hosts import host_to_sdk
from ..deps import CurrentUser, SessionDep, require_permission

router = APIRouter(tags=["console"])


class ConsoleSessionCreate(BaseModel):
    host_id: str


class ConsoleSessionOut(BaseModel):
    session_id: str
    ws_url: str
    protocol: str
    password: str | None
    """Einmal-Kennwort fuer die protokolleigene Authentifizierung (RFB) -- gilt nur
    fuer genau diese, bereits geoeffnete Sitzung, siehe Modul-Docstring."""


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
    """IDs aller Hosts, fuer die gerade ein Anbieter eine Konsole liefern kann -- damit
    Oberflaechen den Knopf nur dort zeigen, wo er auch funktioniert."""
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
) -> ConsoleSessionOut:
    host = await session.get(Host, payload.host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    # D-12: ein Host, der in DIESER Session schon als frisch angelegtes Objekt lebt,
    # hat `credentials` nicht geladen -- `host_to_sdk()` liest es synchron.
    await refresh_relationships(session, host, "tags", "credentials")
    sdk_host = host_to_sdk(host)

    target = await _find_console_target(sdk_host)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Für diesen Host ist keine Konsole verfügbar.",
        )

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
        host_id=host.id, user_id=user.id, session=console, ttl_s=settings.terminal_session_ttl_s
    )
    return ConsoleSessionOut(
        session_id=pending.session_id,
        ws_url=f"/api/v1/ws/console/{pending.session_id}",
        protocol=console.protocol,
        password=console.password,
    )


_WS_UNKNOWN_TICKET = 4404


@router.websocket("/ws/console/{session_id}")
async def console_ws(
    websocket: WebSocket, session_id: str, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    # noVNC fragt je nach Version das Subprotokoll "binary" an. Ein Server, der ein
    # angefragtes Subprotokoll nicht bestaetigt, laesst Browser den Handshake
    # verwerfen -- deshalb spiegeln, wenn angefragt, sonst ohne.
    requested = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol="binary" if "binary" in requested else None)

    pending = get_console_session_store().take(session_id, ttl_s=settings.terminal_session_ttl_s)
    if pending is None:
        await websocket.close(code=_WS_UNKNOWN_TICKET)
        return
    console = pending.session

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
    try:
        await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # Dieselbe Begrenzung wie in terminal.py (dort live gefunden): ein in
        # `websocket.receive()` haengender Writer reagiert nicht immer sofort auf
        # `cancel()` -- ohne Zeitlimit bliebe die Verbindung unbestimmt offen.
        reader.cancel()
        writer.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(reader, writer, return_exceptions=True), timeout=5.0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(console.close(), timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - moeglicherweise schon zu
            pass
