"""Dateimanager (docs/04-API.md §3 „Dateien“, docs/02-EXTENSION-API.md §4/§7)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from nodvard_sdk import FileEntry, FilePlace, FileSourceCaps, SourceInfo, max_body_bytes
from nodvard_sdk.capabilities import FilePlaceProvider, FileSource, FileSourceProvider
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from ...config import get_settings
from ...core.error_text import describe_connection_error, is_expected_connection_error
from ...core.ws_hub import get_ws_hub
from ...ext.runtime import get_extension_runtime
from ...models import Host, User
from ...services import audit as audit_service
from ...services import jobs as jobs_service
from ...services.auth import user_has_permission
from ..body_limit import NO_LIMIT, format_bytes, too_large_message
from ..deps import CurrentUser, SessionDep, require_permission

logger = logging.getLogger("nodvard_deck.files")

router = APIRouter(tags=["files"])

# Der Kern kennt keine konkrete Quelle (kein SSH, kein WebDAV), nur `FileSourceProvider`/`FileSource`; `file_sources()`
# wird bei JEDER Anfrage frisch aufgerufen, weil Hosts jederzeit dazukommen oder wegfallen.
#
# `files.read`/`files.write` erlauben nur den Dateimanager. Jede Quelle verlangt zusätzlich ihr `required_permission`
# (SSH: `hosts.execute`, Nextcloud: `files.nextcloud`), das `_resolve_source()` gegen den NUTZER prüft; `/files/sources`
# und `/files/search` blenden sie sonst aus. Ohne Angabe nur Owner und admin, offene Quellen nennen `files.read`.
# Protokolliert werden für jede Quelle Herunterladen, Suchen (ohne Begriff), Schreibaktionen und Kopieren zwischen
# Quellen, nur Quelle und Pfad, nie Inhalte; verweigerte Zugriffe als `files.access`.

# So lange darf jeder Anbieter für `GET /files/places` höchstens brauchen; ein hängender kostet nur seine eigenen Orte.
PLACES_TIMEOUT_S = 6.0
# Mehr Orte zeigt die Seite nicht (zuerst die der zuerst angemeldeten Anbieter).
MAX_PLACES = 30
_MAX_PLACE_PATH = 1024

# Fortschritt höchstens einmal pro Megabyte statt bei jedem Chunk, sonst flutet er bei kleinen Chunks den Kanal.
_PROGRESS_EVERY_BYTES = 1_048_576


class FileSourceOut(BaseModel):
    source_id: str
    label: str
    icon: str
    caps: FileSourceCaps
    # Server hinter der Quelle (SSH-Quellen), damit die Seite Status und Betriebssystem zeigen kann; sonst None.
    host_id: str | None = None


class FilePlaceOut(BaseModel):
    label: str
    path: str
    icon: str = "folder"
    hint: str | None = None


class MkdirIn(BaseModel):
    path: str


class RenameIn(BaseModel):
    src: str
    dst: str


class RemoveIn(BaseModel):
    path: str
    recursive: bool = False


class TransferEndpoint(BaseModel):
    source: str
    path: str


class TransferIn(BaseModel):
    model_config = {"populate_by_name": True}

    from_: TransferEndpoint = Field(alias="from")
    to: TransferEndpoint


class TransferOut(BaseModel):
    run_id: str


class SearchHit(BaseModel):
    source_id: str
    entry: FileEntry


async def _all_sources() -> list[FileSource]:
    runtime = get_extension_runtime()
    providers = runtime.capabilities.query(FileSourceProvider, allowed_ext_ids=None)
    sources: list[FileSource] = []
    for provider in providers:
        try:
            sources.extend(await provider.file_sources())
        except Exception:  # noqa: BLE001 - eine kaputte Quelle darf die anderen nicht verstecken
            logger.exception("file_source_provider_failed provider=%r", provider)
    return sources


# Für eine Quelle ohne lesbare `required_permission`: `*` haben nur der Owner und die Rolle admin, im Zweifel also zu.
_LOCKED_PERMISSION = "*"


def _required_permission(source: FileSource) -> str:
    """Das Recht, das der Nutzer für die Quelle selbst braucht; fehlt die Angabe oder ist sie unlesbar, `*`."""
    # Kein Pflichtmitglied des `FileSource`-Protokolls: `@runtime_checkable` wiese sonst jede ältere Quelle ab.
    permission = getattr(source, "required_permission", None)
    return permission if isinstance(permission, str) and permission else _LOCKED_PERMISSION


def _may_use(user: User, source: FileSource) -> bool:
    return user_has_permission(user, _required_permission(source))


async def _audit(
    session: AsyncSession, actor_id: str, source: FileSource, action: str, *, outcome: str = "success",
    detail: dict[str, Any] | None = None,
) -> None:
    """Protokolliert einen Zugriff auf eine Quelle, nur Quelle und Pfad, nie Dateiinhalte."""
    await audit_service.log(
        session, actor_type="user", actor_id=actor_id, action=action, outcome=outcome,
        target_type="file_source", target_id=source.source_id, detail=detail,
    )


async def _audited(
    session: AsyncSession, user: User, source: FileSource, action: str, detail: dict[str, Any], coro: Any
) -> Any:
    """Führt eine schreibende Dateiaktion aus und protokolliert sie auch bei Misserfolg (ein abgebrochener Upload hat
    die Zieldatei schon geleert). Der Commit vor dem Weiterwerfen verhindert, dass `session_scope()` die Zeile
    zurückrollt."""
    try:
        result = await _call(coro)
    except HTTPException as exc:
        await _audit(session, user.id, source, action, outcome="failure", detail={**detail, "status": exc.status_code})
        await session.commit()
        raise
    await _audit(session, user.id, source, action, detail=detail)
    return result


async def _resolve_source(source_id: str, user: User, session: AsyncSession) -> FileSource:
    """Löst die Quelle auf und prüft ihre `required_permission` gegen den Nutzer; jeder Endpunkt geht hier durch."""
    for source in await _all_sources():
        if source.source_id != source_id:
            continue
        if not _may_use(user, source):
            await _audit(session, user.id, source, "files.access", outcome="denied", detail={"reason": "permission"})
            await session.commit()
            permission = _required_permission(source)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Diese Quelle dürfen nur Admins benutzen." if permission == _LOCKED_PERMISSION
                else f"Für diese Quelle brauchst du die Berechtigung '{permission}'.",
            )
        return source
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unbekannte Quelle '{source_id}'.")


async def _call(coro: Any, *, not_found: str | None = None) -> Any:
    """Übersetzt einen Fehler EINER Quelle (SFTP/WebDAV/...) in eine saubere HTTP-Antwort; eine `FileSource` muss ihre
    Fehler nicht selbst normalisieren, das tut dieser generische Aufrufer für alle."""
    try:
        return await coro
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found or str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - siehe Docstring: jede Quelle normalisiert hier einheitlich
        # `str(exc)` ist bei Zeitüberschreitungen leer. Ein Fehler der Quelle (Server aus, falsche Zugangsdaten, Ordner
        # fehlt) ist kein Programmfehler: eine Zeile, nie ein Traceback (`deploy_pi.sh` wertet "Traceback" im Protokoll
        # als gescheiterten Start). Die Person bekommt den Grund als 502 zurück.
        reason = describe_connection_error(exc)
        expected = is_expected_connection_error(exc)
        # Ins Protokoll darf der volle Text (nur Admins lesen es), in die Antwort kommt nur `reason`.
        logger.log(
            logging.INFO if expected else logging.WARNING,
            "file_source_failed type=%s reason=%s",
            type(exc).__name__,
            reason if expected else (str(exc).strip() or reason),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Zugriff auf die Quelle fehlgeschlagen: {reason}"
        ) from exc


# Marke für "die Quelle hat nichts geliefert" (leere Datei), unterscheidbar von einem leeren Teil.
_STREAM_END = object()


async def _close_stream(stream: Any) -> None:
    """Schließt den Strom einer Quelle (`aclose()` eines Async-Generators gibt Dateien und Verbindungen frei).
    Mehrfaches Schließen ist harmlos; ein Fehler dabei stört eine fertige oder abgebrochene Antwort nicht."""
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:  # die Antwort ist schon gelaufen, mehr als ein Vermerk ist nicht nötig
        logger.debug("file_source_close_failed", exc_info=True)


async def _start_stream(source: FileSource, path: PurePosixPath, offset: int) -> tuple[Any, Any]:
    """Öffnet `open_read()` und holt den ersten Teil. Gibt (Strom, erster Teil) zurück, der erste Teil ist `_STREAM_END`
    bei einer leeren Datei. Scheitert das, ist der Strom geschlossen und der Fehler geht weiter (der Aufrufer übersetzt
    ihn mit `_call()`)."""
    stream = aiter(source.open_read(path, offset=offset))
    try:
        return stream, await anext(stream, _STREAM_END)
    except BaseException:
        await _close_stream(stream)
        raise


async def _primed_stream(stream: Any, first: Any) -> Any:
    """Der schon geholte erste Teil, danach der Rest des Stroms; der Strom wird am Ende immer geschlossen."""
    try:
        if first is not _STREAM_END:
            yield first
        async for chunk in stream:
            yield chunk
    finally:
        await _close_stream(stream)


@router.get("/files/sources", dependencies=[Depends(require_permission("files.read"))])
async def list_sources(user: CurrentUser) -> list[FileSourceOut]:
    sources = [s for s in await _all_sources() if _may_use(user, s)]
    return [
        FileSourceOut(source_id=s.source_id, label=s.label, icon=s.icon, caps=s.caps, host_id=_source_host_id(s))
        for s in sources
    ]


@router.get(
    "/files/places",
    dependencies=[Depends(require_permission("files.read")), Depends(require_permission("hosts.read"))],
)
async def list_places(host_id: str, user: CurrentUser, session: SessionDep) -> list[FilePlaceOut]:
    """Wichtige Orte eines Servers (Abkürzungen im Dateimanager): gesammelt bei allen `FilePlaceProvider`, gleichzeitig,
    mit Zeitgrenze je Anbieter (ein Anbieter, der scheitert oder zu lange braucht, kostet nur seine Orte). Entdoppelt
    nach Pfad, nur absolute Pfade, höchstens `MAX_PLACES`; leer ohne Recht auf eine Dateiquelle dieses Servers."""
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    # Ohne Dateiquelle des Servers führten die Abkürzungen ins Leere und verrieten Ordner, die der Nutzer nicht sehen
    # darf.
    if not any(_source_host_id(s) == host.id and _may_use(user, s) for s in await _all_sources()):
        return []
    providers = get_extension_runtime().capabilities.query(FilePlaceProvider, allowed_ext_ids=None)
    batches = await asyncio.gather(*(_places_of(provider, host.id) for provider in providers))
    places: list[FilePlaceOut] = []
    seen: set[str] = set()
    for batch in batches:
        for raw in batch:
            place = _clean_place(raw)
            if place is None or place.path in seen:
                continue
            seen.add(place.path)
            places.append(place)
    return places[:MAX_PLACES]


async def _places_of(provider: FilePlaceProvider, host_id: str) -> list[Any]:
    try:
        found = await asyncio.wait_for(provider.places_for_host(host_id), timeout=PLACES_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - ein Anbieter darf die anderen nicht verstecken
        # Ohne Traceback: das Protokoll gilt beim Ausliefern als Fehlerzeichen, und jede Server-Seite fragt neu.
        logger.warning("file_places_provider_failed provider=%r host=%s reason=%s", provider, host_id, str(exc) or type(exc).__name__)
        return []
    return list(found) if isinstance(found, list | tuple) else []


def _clean_place(raw: Any) -> FilePlaceOut | None:
    """Nimmt einen Ort eines Anbieters in die Antwort auf, oder `None`: kein brauchbarer Ort, Pfad nicht absolut (oder
    mit `..`/NUL), Beschriftung leer. Der Pfad wird geglättet (`//`, `/./`, Schrägstrich am Ende)."""
    try:
        place = FilePlace.model_validate(raw)
    except ValueError:
        return None
    label = place.label.strip()
    if not label or not place.path.startswith("/") or "\x00" in place.path or len(place.path) > _MAX_PLACE_PATH:
        return None
    parts = [part for part in place.path.split("/") if part and part != "."]
    if ".." in parts:
        return None
    hint = (place.hint or "").strip()
    return FilePlaceOut(
        label=label[:60], path="/" + "/".join(parts), icon=(place.icon or "").strip()[:40] or "folder", hint=hint[:160] or None
    )


def _source_host_id(source: object) -> str | None:
    """Optionales Attribut `host_id` einer Quelle (SDK, `FileSource`); nur ein nicht leerer String zählt."""
    value = getattr(source, "host_id", None)
    return value if isinstance(value, str) and value else None


@router.get("/files/{source_id}/info", dependencies=[Depends(require_permission("files.read"))])
async def source_info(source_id: str, user: CurrentUser, session: SessionDep) -> SourceInfo:
    """Quota, Health und Deep-Link der Quelle (`SourceInfo`, `FileSource.info()`); gleicher Fehlerpfad (`_call()`) wie
    jeder Quellen-Zugriff."""
    source = await _resolve_source(source_id, user, session)
    return await _call(source.info())


@router.get("/files/{source_id}/list", dependencies=[Depends(require_permission("files.read"))])
async def list_dir(
    source_id: str, user: CurrentUser, session: SessionDep, path: str = "/", cursor: str | None = None
) -> Any:
    source = await _resolve_source(source_id, user, session)
    page = await _call(source.list_dir(PurePosixPath(path), cursor=cursor))
    return {"items": [e.model_dump(mode="json") for e in page.items], "next_cursor": page.next_cursor}


@router.get("/files/{source_id}/stat", dependencies=[Depends(require_permission("files.read"))])
async def stat_entry(source_id: str, path: str, user: CurrentUser, session: SessionDep) -> FileEntry:
    source = await _resolve_source(source_id, user, session)
    return await _call(source.stat(PurePosixPath(path)), not_found=f"'{path}' nicht gefunden.")


@router.get("/files/{source_id}/download", dependencies=[Depends(require_permission("files.read"))])
async def download_file(
    source_id: str, path: str, request: Request, user: CurrentUser, session: SessionDep
) -> StreamingResponse:
    source = await _resolve_source(source_id, user, session)
    p = PurePosixPath(path)
    entry = await _call(source.stat(p), not_found=f"'{path}' nicht gefunden.")

    offset = 0
    response_status = status.HTTP_200_OK
    headers: dict[str, str] = {"Accept-Ranges": "bytes"}
    range_header = request.headers.get("range")
    if range_header and source.caps.range_read and entry.size is not None:
        try:
            start_str = range_header.removeprefix("bytes=").split("-", 1)[0]
            offset = int(start_str) if start_str else 0
        except ValueError:
            offset = 0
        else:
            response_status = status.HTTP_206_PARTIAL_CONTENT
            headers["Content-Range"] = f"bytes {offset}-{entry.size - 1}/{entry.size}"

    # Den ersten Teil holen, bevor die Antwort gebaut wird: Starlette schickt den Kopf (200/206) sofort beim Start des
    # Stroms. Scheitert die Quelle gleich am Anfang (Nextcloud antwortet mit einer Weiterleitung oder 404, der Server
    # ist weg), wäre das sonst ein 200 mit leerem oder abgebrochenem Körper statt eines Fehlers.
    try:
        stream, first = await _call(_start_stream(source, p, offset), not_found=f"'{path}' nicht gefunden.")
    except HTTPException as exc:
        await _audit(
            session, user.id, source, "files.download", outcome="failure",
            detail={"path": path, "status": exc.status_code},
        )
        await session.commit()
        raise
    try:
        await _audit(session, user.id, source, "files.download", detail={"path": path})
    except BaseException:
        # Ohne Antwort schließt sonst niemand den schon geöffneten Strom.
        await _close_stream(stream)
        raise

    return StreamingResponse(
        _primed_stream(stream, first),
        status_code=response_status,
        media_type=entry.mime or "application/octet-stream",
        headers=headers,
        background=BackgroundTask(_close_stream, stream),
    )


async def _free_bytes(source: FileSource, path: PurePosixPath) -> int | None:
    """Freier Platz dort, wohin `path` geschrieben würde, falls die Quelle ihn meldet (`free_bytes`, optional)."""
    method = getattr(source, "free_bytes", None)
    if method is None:
        return None
    try:
        free = await method(path)
    except Exception:  # noqa: BLE001 - nicht prüfbar heißt: die Quelle meldet selbst, wenn der Platz nicht reicht
        logger.info("file_source_free_bytes_failed source_id=%s", source.source_id)
        return None
    return free if isinstance(free, int) and not isinstance(free, bool) and free >= 0 else None


def _upload_limit() -> int:
    """Grenze für `upload_file`: `files_max_upload_bytes`, 0 heißt keine (der Körper geht als Strom an die Quelle und
    liegt nie ganz im Speicher)."""
    return get_settings().files_max_upload_bytes or NO_LIMIT


@router.post("/files/{source_id}/upload", dependencies=[Depends(require_permission("files.write"))])
@max_body_bytes(_upload_limit)
async def upload_file(source_id: str, path: str, request: Request, user: CurrentUser, session: SessionDep) -> FileEntry:
    source = await _resolve_source(source_id, user, session)
    if not source.caps.write:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Quelle unterstützt kein Schreiben.")
    content_length = request.headers.get("content-length")
    size = int(content_length) if content_length is not None and content_length.isdigit() else None
    # Die Grenze schon HIER prüfen: Die Middleware greift erst beim Lesen des ersten Körperteils, bis dahin kann die
    # Quelle schon etwas angelegt haben (SFTP: Temp-Datei; ohne Zwischendatei wird das Ziel auf 0 Byte gekürzt).
    # `Connection: close` wie bei der Middleware, sonst liest uvicorn den Rest (womöglich viele GB) für die nächste
    # Anfrage noch ein.
    limit = _upload_limit()
    if size is not None and size > limit:
        raise HTTPException(status_code=413, detail=too_large_message(limit), headers={"Connection": "close"})
    free = await _free_bytes(source, PurePosixPath(path))
    if size is not None and free is not None and size > free:
        raise HTTPException(
            status_code=507,
            detail=f"Auf dem Ziel ist nicht genug Platz frei ({format_bytes(free)} frei, die Datei hat {format_bytes(size)}).",
            headers={"Connection": "close"},
        )
    return await _audited(
        session, user, source, "files.upload", {"path": path},
        source.open_write(PurePosixPath(path), request.stream(), size=size),
    )


@router.post("/files/{source_id}/mkdir", dependencies=[Depends(require_permission("files.write"))])
async def mkdir(source_id: str, payload: MkdirIn, user: CurrentUser, session: SessionDep) -> FileEntry:
    source = await _resolve_source(source_id, user, session)
    if not source.caps.mkdir:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Quelle unterstützt kein Anlegen von Ordnern.")
    return await _audited(session, user, source, "files.mkdir", {"path": payload.path}, source.mkdir(PurePosixPath(payload.path)))


@router.post("/files/{source_id}/rename", dependencies=[Depends(require_permission("files.write"))])
async def rename(source_id: str, payload: RenameIn, user: CurrentUser, session: SessionDep) -> FileEntry:
    source = await _resolve_source(source_id, user, session)
    if not source.caps.rename:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Quelle unterstützt kein Umbenennen.")
    return await _audited(
        session, user, source, "files.rename", {"from": payload.src, "to": payload.dst},
        source.rename(PurePosixPath(payload.src), PurePosixPath(payload.dst)),
    )


@router.post("/files/{source_id}/remove", dependencies=[Depends(require_permission("files.write"))])
async def remove(source_id: str, payload: RemoveIn, user: CurrentUser, session: SessionDep) -> dict[str, bool]:
    source = await _resolve_source(source_id, user, session)
    if not source.caps.remove:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Quelle unterstützt kein Löschen.")
    await _audited(
        session, user, source, "files.remove", {"path": payload.path, "recursive": payload.recursive},
        source.remove(PurePosixPath(payload.path), recursive=payload.recursive),
    )
    return {"removed": True}


@router.get("/files/search", dependencies=[Depends(require_permission("files.read"))])
async def search(user: CurrentUser, session: SessionDep, q: str, sources: str | None = Query(None)) -> list[SearchHit]:
    wanted = set(sources.split(",")) if sources else None
    all_sources = await _all_sources()
    targets = [
        s for s in all_sources
        if (wanted is None or s.source_id in wanted) and s.caps.search and _may_use(user, s)
    ]
    # Die Suche liest Dateiinhalte auf dem Server: je durchsuchter Quelle ein Eintrag (ohne Suchbegriff). Sofort
    # speichern, damit die Datenbank während der oft langen Suche nicht gesperrt bleibt.
    for source in targets:
        await _audit(session, user.id, source, "files.search")
    if targets:
        await session.commit()

    async def _search_one(source: FileSource) -> list[SearchHit]:
        try:
            return [SearchHit(source_id=source.source_id, entry=entry) async for entry in source.search(q, root=PurePosixPath("/"))]
        except Exception as exc:  # noqa: BLE001 - eine kaputte Quelle darf die Suche der anderen nicht verhindern
            if is_expected_connection_error(exc):  # Server aus: eine Zeile, kein Traceback
                logger.info("file_source_search_failed source_id=%s reason=%s", source.source_id, describe_connection_error(exc))
            else:
                logger.exception("file_source_search_failed source_id=%s", source.source_id)
            return []

    results = await asyncio.gather(*(_search_one(s) for s in targets))
    return [hit for group in results for hit in group]


_IDENTITY_STAT_KEYS = ("size", "mtime", "uid", "gid", "mode")


async def _identity(source: FileSource, path: PurePosixPath) -> dict[str, Any] | None:
    """Optionale `file_identity()` einer Quelle (kein Pflichtmitglied des Protokolls, wie `required_permission`). Ohne
    sie oder mit einem Fehler `None`: im Zweifel gilt eine Datei als eine andere."""
    method = getattr(source, "file_identity", None)
    if method is None:
        return None
    try:
        identity = await method(path)
    except Exception:  # noqa: BLE001 - nicht prüfbar heißt nicht "gleich"
        logger.warning("file_identity_failed source_id=%s", source.source_id, exc_info=True)
        return None
    return identity if isinstance(identity, dict) else None


async def _is_same_file(source_from: FileSource, path_from: PurePosixPath, source_to: FileSource, path_to: PurePosixPath) -> bool:
    """Meinen Quelle und Ziel dieselbe Datei? Gleiche Quelle: gleicher oder gleich aufgelöster Pfad (Link, `..`). Zwei
    Quellen: gleicher aufgelöster Pfad, nicht verschiedener Rechner (`machine`) und gleiche Größe, Zeit, Besitzer,
    Rechte. Im Zweifel nein."""
    # Ein falsches "gleich" verhindert eine erlaubte Kopie, ein falsches "verschieden" kostet nichts (das Ziel wird erst
    # fertig geschrieben, dann ersetzt). Nicht erkennbar: ein unter zwei Pfaden eingehängter Ordner; beim Verschieben
    # auf den zweiten Pfad löscht das Entfernen der Quelle die Datei (SFTP liefert keine Inode-Nummer).
    same_source = source_from.source_id == source_to.source_id
    if same_source and path_from == path_to:
        return True
    identity_from = await _identity(source_from, path_from)
    identity_to = await _identity(source_to, path_to)
    if not identity_from or not identity_to:
        return False
    resolved_from, resolved_to = identity_from.get("path"), identity_to.get("path")
    if not resolved_from or resolved_from != resolved_to:
        return False
    if same_source:
        return True
    # Kennen beide ihren Rechner und die Kennungen unterscheiden sich, sind es zwei Dateien (auch bei per rsync gleich
    # gehaltenen). Geklonte Rechner teilen oft dieselbe `/etc/machine-id`, die Kennung allein macht /etc/x auf dem Klon
    # nicht zur selben Datei.
    machine_from, machine_to = identity_from.get("machine"), identity_to.get("machine")
    if machine_from and machine_to and machine_from != machine_to:
        return False
    return all(
        identity_from.get(key) is not None and identity_from.get(key) == identity_to.get(key)
        for key in _IDENTITY_STAT_KEYS
    )


async def _audit_transfer_result(
    actor_id: str, sources: tuple[FileSource, ...], outcome: str, detail: dict[str, Any]
) -> None:
    """Abschlusseintrag des Kopierens in einer eigenen Sitzung (die des Requests ist beim Ende längst zu). Scheitert das
    Protokollieren, bleibt es bei einer Zeile im Log; der Lauf bekommt trotzdem sein Ergebnis."""
    from ...db.session import session_scope

    try:
        async with session_scope() as session:
            for gated in {s.source_id: s for s in sources}.values():
                await _audit(session, actor_id, gated, "files.transfer", outcome=outcome, detail=detail)
    except Exception:  # noqa: BLE001 - siehe Docstring
        logger.exception("file_transfer_audit_failed run_id=%s", detail.get("run_id"))


async def _run_transfer(
    run_id: str, source_from: FileSource, path_from: PurePosixPath, source_to: FileSource, path_to: PurePosixPath,
    *, actor_id: str, audit_detail: dict[str, Any],
) -> None:
    """Streamt `open_read(A) -> open_write(B)` serverseitig, ohne dass die Datei über den Client läuft (docs/04 §3),
    IMMER in einem eigenen `session_scope()`: der Request, der den Task gestartet hat, ist längst zurückgekehrt."""
    from ...db.session import session_scope

    hub = get_ws_hub()
    bytes_done = 0
    last_published = 0

    async def _counted_stream():
        nonlocal bytes_done, last_published
        async for chunk in source_from.open_read(path_from):
            bytes_done += len(chunk)
            if bytes_done - last_published >= _PROGRESS_EVERY_BYTES:
                last_published = bytes_done
                await hub.publish(f"runs.{run_id}", {"status": "running", "bytes": bytes_done}, required_permission="files.read")
            yield chunk

    try:
        await source_to.open_write(path_to, _counted_stream(), size=None)
    except Exception as exc:  # noqa: BLE001 - Ergebnis MUSS immer geschrieben werden, siehe core.gate.execute_action()-Präzedenz
        reason = describe_connection_error(exc)
        if is_expected_connection_error(exc):  # Server aus: eine Zeile, kein Traceback
            logger.info("file_transfer_failed run_id=%s reason=%s", run_id, reason)
        else:
            logger.exception("file_transfer_failed run_id=%s", run_id)
        # Zuerst ins Protokoll: das Ziel kann schon abgeschnitten sein (`bytes` zeigt, wie weit es kam).
        await _audit_transfer_result(
            actor_id, (source_from, source_to), "failure", {**audit_detail, "bytes": bytes_done}
        )
        async with session_scope() as session:
            run = await jobs_service.get_run(session, run_id)
            if run is not None:
                await jobs_service.finish_run(session, run, status="failed", error=reason)
        await hub.publish(f"runs.{run_id}", {"status": "failed", "error": reason}, required_permission="files.read")
        return

    await _audit_transfer_result(actor_id, (source_from, source_to), "success", {**audit_detail, "bytes": bytes_done})
    async with session_scope() as session:
        run = await jobs_service.get_run(session, run_id)
        if run is not None:
            await jobs_service.finish_run(session, run, status="succeeded", exit_code=0)
    await hub.publish(f"runs.{run_id}", {"status": "succeeded", "bytes": bytes_done}, required_permission="files.read")


@router.post("/files/transfer", dependencies=[Depends(require_permission("files.write"))])
async def transfer(payload: TransferIn, session: SessionDep, user: CurrentUser) -> TransferOut:
    source_from = await _resolve_source(payload.from_.source, user, session)
    source_to = await _resolve_source(payload.to.source, user, session)
    if not source_to.caps.write:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Zielquelle unterstützt kein Schreiben.")
    path_from, path_to = PurePosixPath(payload.from_.path), PurePosixPath(payload.to.path)
    if await _is_same_file(source_from, path_from, source_to, path_to):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Quelle und Ziel sind dieselbe Datei.")
    run = await jobs_service.create_run(
        session, job_id=None, ext_id=None, trigger="manual",
        actor={"type": "user", "id": user.id, "label": user.username},
    )
    transfer_detail = {
        "run_id": run.id,
        "from_source": payload.from_.source, "from_path": payload.from_.path,
        "to_source": payload.to.source, "to_path": payload.to.path,
    }
    # Hier steht nur, dass das Kopieren BEGONNEN hat; ob es klappte, steht im Abschlusseintrag `files.transfer` (gleiche
    # `run_id`). Fehlt der, ist die Kopie unterbrochen worden.
    for gated in {s.source_id: s for s in (source_from, source_to)}.values():
        await _audit(session, user.id, gated, "files.transfer_start", detail=transfer_detail)
    # Lauf und Starteinträge müssen gespeichert sein, bevor der Hintergrundtask losläuft: Scheitert die Kopie sofort,
    # sucht er den Lauf in einer eigenen Sitzung und schreibt dort ins Protokoll.
    await session.commit()
    asyncio.ensure_future(
        _run_transfer(run.id, source_from, path_from, source_to, path_to, actor_id=user.id, audit_detail=transfer_detail)
    )
    return TransferOut(run_id=run.id)
