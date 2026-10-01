"""Terminal-Sitzungen -- docs/04-API.md §4, docs/00-DECISIONS.md D-05.

Eigener Socket, ABSICHTLICH getrennt vom generischen WS-Multiplex-Hub (`api/v1/ws.py`):
Tastatureingaben sind latenzkritisch, Ausgaben binaer und voluminoes -- beides wuerde
den Multiplex-Steuerkanal verstopfen.

`POST /terminal/sessions` mintet ein einmaliges Ticket (die Autorisierung passiert
HIER, ueber die normale Bearer-Pruefung); der WS-Verbindungsaufbau selbst braucht
keinen zweiten Auth-Schritt, weil das Ticket bereits an den anfragenden Nutzer
gebunden ist (docs/04 §4: "keine Tokens in der URL" -- die Session-ID ist ein
kurzlebiges Einmal-Ticket, kein wiederverwendbares Credential).

Kennt SSH nicht: findet einen `TerminalTarget`-Anbieter ueber die
Capability-Registry (docs/02 §3) -- welche Extension das ist (aktuell die
terminal-Extension mit `core/ssh.py` dahinter), ist hier bewusst unbekannt.

**Terminal-Seite:** `GET /terminal/hosts` sagt der Terminal-Seite,
fuer welche Hosts eine Sitzung ueberhaupt moeglich ist. Und docs/04 §4 versprach
seit jeher `terminal.open`/`terminal.close` mit Dauer im Audit-Log -- geschrieben
wurde das nie. Jetzt schon: eine Shell ist Vollzugriff auf die Maschine.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from nodvard_sdk.capabilities import TerminalTarget
from pydantic import BaseModel

from ...config import Settings, get_settings
from ...core.terminal_sessions import get_terminal_session_registry
from ...db.session import session_scope
from ...ext.runtime import get_extension_runtime
from ...models import Host
from ...services import audit as audit_service
from ...services import hosts as hosts_service
from ...services.hosts import host_to_sdk
from ..deps import CurrentUser, SessionDep, require_permission

router = APIRouter(tags=["terminal"])


class TerminalSessionCreate(BaseModel):
    host_id: str
    cols: int = 80
    rows: int = 24
    user: str | None = None


class TerminalSessionOut(BaseModel):
    session_id: str
    ws_url: str


@router.post("/terminal/sessions", dependencies=[Depends(require_permission("hosts.execute"))])
async def create_terminal_session(
    payload: TerminalSessionCreate, session: SessionDep, user: CurrentUser
) -> TerminalSessionOut:
    host = await session.get(Host, payload.host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")

    ticket = get_terminal_session_registry().create(
        host_id=payload.host_id, user_id=user.id, cols=payload.cols, rows=payload.rows
    )
    return TerminalSessionOut(
        session_id=ticket.session_id, ws_url=f"/api/v1/ws/terminal/{ticket.session_id}"
    )


async def _find_terminal_target(host):  # noqa: ANN001 - nodvard_sdk.types.Host
    runtime = get_extension_runtime()
    for candidate in runtime.capabilities.query(TerminalTarget):
        if await candidate.can_open(host):
            return candidate
    return None


@router.get("/terminal/hosts", dependencies=[Depends(require_permission("hosts.read"))])
async def list_terminal_hosts(session: SessionDep) -> list[str]:
    """IDs aller Hosts, fuer die gerade ein Anbieter eine Terminal-Sitzung oeffnen
    kann (heute: Hosts mit SSH-Zugangsdaten) -- die Terminal-Seite bietet nur diese an,
    statt erst beim Verbinden zu scheitern."""
    if not get_extension_runtime().capabilities.query(TerminalTarget):
        return []
    result: list[str] = []
    for host in await hosts_service.list_hosts(session):
        if await _find_terminal_target(host_to_sdk(host)) is not None:
            result.append(host.id)
    return result


async def _audit(ticket, action: str, outcome: str, **kwargs) -> None:  # noqa: ANN001, ANN003
    try:
        async with session_scope() as db_session:
            await audit_service.log(
                db_session, actor_type="user", actor_id=ticket.user_id, action=action, outcome=outcome,
                target_type="host", target_id=ticket.host_id, **kwargs,
            )
    except Exception:  # noqa: BLE001 - ein Audit-Fehler darf die Sitzung nicht abreissen
        pass


# Private WS-Fehlercodes im Bereich 4000-4999 (RFC 6455 erlaubt 4000-4999 fuer
# Anwendungen). Keine Standard-Codes verfuegbar, die "Ticket unbekannt" o. Ae. meinen.
_WS_UNKNOWN_TICKET = 4404
_WS_UNKNOWN_HOST = 4404
_WS_NO_TARGET = 4501
_WS_OPEN_FAILED = 4500


@router.websocket("/ws/terminal/{session_id}")
async def terminal_ws(
    websocket: WebSocket, session_id: str, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    """`settings` via `Depends()`, NICHT `get_settings()` direkt -- WebSocket-Routen
    unterstuetzen FastAPIs DI genauso wie normale Endpunkte, und nur darueber greift
    die Test-Ueberschreibung (`app.dependency_overrides[get_settings]`, siehe
    conftest.py `client`/`running_app`)."""
    # `accept()` VOR jeder Pruefung, auch vor einer, die sofort ablehnt: `accept()`
    # konsumiert intern erst das ausstehende ASGI-"websocket.connect"-Ereignis via
    # `receive()`, bevor es sendet. `close()` allein tut das NICHT -- ruft man
    # `close()` als allererste Handler-Aktion auf, wartet der ASGI-Server auf ein
    # `receive()`, das nie kommt, und der Verbindungsaufbau haengt unbestimmt lange
    # (live gefunden: genau das lieferte test_unknown_ticket_is_rejected).
    await websocket.accept()

    ticket = get_terminal_session_registry().consume(session_id, ttl_s=settings.terminal_session_ttl_s)
    if ticket is None:
        await websocket.close(code=_WS_UNKNOWN_TICKET)
        return

    async with session_scope() as db_session:
        host = await db_session.get(Host, ticket.host_id)
        sdk_host = host_to_sdk(host) if host is not None else None

    if sdk_host is None:
        await websocket.close(code=_WS_UNKNOWN_HOST)
        return

    target = await _find_terminal_target(sdk_host)
    if target is None:
        await websocket.close(code=_WS_NO_TARGET)
        return

    try:
        term_session = await target.open(sdk_host, user=None, cols=ticket.cols, rows=ticket.rows)
    except Exception as exc:  # noqa: BLE001 - dem Client sichtbar machen, nicht den Server crashen
        await _audit(ticket, "terminal.open", "failure", reason=str(exc)[:500])
        # Der Grund als Text VOR dem Close-Frame -- die Seite
        # zeigt ihn an, statt nur "Verbindung getrennt (4500)".
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(exc)[:300]}))
        except Exception:  # noqa: BLE001
            pass
        await websocket.close(code=_WS_OPEN_FAILED)
        return

    await _audit(ticket, "terminal.open", "success")
    opened_at = time.monotonic()

    async def _pump_host_to_client() -> None:
        try:
            async for chunk in term_session.read():
                await websocket.send_bytes(chunk)
        except Exception:  # noqa: BLE001 - Verbindungsende ist kein Serverfehler
            pass
        finally:
            exit_code = term_session.exit_code
            try:
                await websocket.send_text(json.dumps({"type": "exit", "code": exit_code}))
            except Exception:  # noqa: BLE001
                pass

    async def _pump_client_to_host() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    await term_session.write(data)
                    continue
                text = message.get("text")
                if text is None:
                    continue
                try:
                    control = json.loads(text)
                except ValueError:
                    continue
                control_type = control.get("type")
                if control_type == "resize":
                    await term_session.resize(int(control.get("cols", ticket.cols)), int(control.get("rows", ticket.rows)))
                elif control_type == "signal" and control.get("name") == "SIGINT":
                    await term_session.write(b"\x03")
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass

    reader = asyncio.ensure_future(_pump_host_to_client())
    writer = asyncio.ensure_future(_pump_client_to_host())
    try:
        await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # Live gefunden: `writer` haengt oft in `await websocket.receive()`, das auf
        # `cancel()` nicht immer sofort reagiert (Starlettes ASGI-receive-Wartung
        # scheint eine CancelledError gelegentlich zu verschlucken/verzoegern statt
        # sie umgehend durchzureichen) -- ohne Zeitlimit blieb die Verbindung (und
        # damit ein Test-Server-Shutdown) unbestimmt lange haengen. Ein bewusst
        # grosszuegiges, aber ENDLICHES Limit verhindert das, ohne eine normal
        # schnell endende Sitzung zu beeintraechtigen.
        reader.cancel()
        writer.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(reader, writer, return_exceptions=True), timeout=5.0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(term_session.close(), timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        await _audit(
            ticket, "terminal.close", "success",
            detail={"duration_s": round(time.monotonic() - opened_at, 1), "exit_code": term_session.exit_code},
        )
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - moeglicherweise schon zu
            pass
