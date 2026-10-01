"""Dateimanager -- docs/04-API.md §3 ("Dateien"), docs/02-EXTENSION-API.md §4/§7.

Der Kern kennt keine einzige konkrete Quelle (kein SSH, kein WebDAV) -- er kennt nur
`nodvard_sdk.capabilities.FileSourceProvider`/`FileSource` und baut Explorer,
Such-Fan-out und Quelle-zu-Quelle-Transfer generisch darueber. `find_all_sources()`
ist dieselbe Art "uneingeschraenkte Capability-Abfrage, weil Kern-Code" wie
`core.gate.find_executor()`.

**Warum `FileSourceProvider` (eine Liste PRO Aufruf) statt `FileSource` direkt ueber
`ctx.capabilities.provide()` registriert (das Beispiel im Modul-Docstring von
`nodvard_sdk.capabilities`):** eine statische, bei `setup()` eingefrorene Liste passt
fuer eine Extension mit GENAU EINER Quelle (z. B. ein konfigurierter Nextcloud-
Server), aber nicht fuer SSH: jeder Host mit Zugangsdaten ist eine eigene Quelle, und
Hosts werden jederzeit angelegt/entfernt, ohne dass die terminal-Extension neu laedt.
`file_sources()` wird deshalb bei JEDER Anfrage frisch aufgerufen.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from nodvard_sdk import FileEntry, FileSourceCaps, SourceInfo
from nodvard_sdk.capabilities import FileSource, FileSourceProvider
from pydantic import BaseModel, Field

from ...core.ws_hub import get_ws_hub
from ...ext.runtime import get_extension_runtime
from ...services import jobs as jobs_service
from ..deps import CurrentUser, SessionDep, require_permission

logger = logging.getLogger("nodvard_deck.files")

router = APIRouter(tags=["files"])

_PROGRESS_EVERY_BYTES = 1_048_576
"""Fortschritt wird nicht bei JEDEM Chunk ueber WS publiziert (koennte bei kleiner
Chunk-Groesse den Kanal fluten), sondern hoechstens einmal pro Megabyte."""


class FileSourceOut(BaseModel):
    source_id: str
    label: str
    icon: str
    caps: FileSourceCaps


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


async def _resolve_source(source_id: str) -> FileSource:
    for source in await _all_sources():
        if source.source_id == source_id:
            return source
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unbekannte Quelle '{source_id}'.")


async def _call(coro: Any, *, not_found: str | None = None) -> Any:
    """Uebersetzt einen Fehler EINER Quelle (SFTP/WebDAV/...) in eine saubere HTTP-
    Antwort -- eine `FileSource`-Implementierung ist nicht verpflichtet, ihre Fehler
    selbst zu normalisieren (anders als proxmoxs `ProxmoxConnector._request()`, das
    genau EINE Quelle kennt); dieser generische Aufrufer tut es fuer alle."""
    try:
        return await coro
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found or str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - siehe Docstring: jede Quelle normalisiert hier einheitlich
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Quelle antwortete mit einem Fehler: {exc}"
        ) from exc


@router.get("/files/sources", dependencies=[Depends(require_permission("files.read"))])
async def list_sources() -> list[FileSourceOut]:
    sources = await _all_sources()
    return [FileSourceOut(source_id=s.source_id, label=s.label, icon=s.icon, caps=s.caps) for s in sources]


@router.get("/files/{source_id}/info", dependencies=[Depends(require_permission("files.read"))])
async def source_info(source_id: str) -> SourceInfo:
    """`FileSource.info()` ist Vertragspflicht (jede Quelle implementiert sie); dieser
    Endpunkt macht das Quota/Health/Deep-Link-Panel (`SourceInfo`) ueber die API
    abrufbar. Derselbe `_call()`-Fehlerpfad wie jeder andere Quellen-Zugriff hier."""
    source = await _resolve_source(source_id)
    return await _call(source.info())


@router.get("/files/{source_id}/list", dependencies=[Depends(require_permission("files.read"))])
async def list_dir(source_id: str, path: str = "/", cursor: str | None = None) -> Any:
    source = await _resolve_source(source_id)
    page = await _call(source.list_dir(PurePosixPath(path), cursor=cursor))
    return {"items": [e.model_dump(mode="json") for e in page.items], "next_cursor": page.next_cursor}


@router.get("/files/{source_id}/stat", dependencies=[Depends(require_permission("files.read"))])
async def stat_entry(source_id: str, path: str) -> FileEntry:
    source = await _resolve_source(source_id)
    return await _call(source.stat(PurePosixPath(path)), not_found=f"'{path}' nicht gefunden.")


@router.get("/files/{source_id}/download", dependencies=[Depends(require_permission("files.read"))])
async def download_file(source_id: str, path: str, request: Request) -> StreamingResponse:
    source = await _resolve_source(source_id)
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

    return StreamingResponse(
        source.open_read(p, offset=offset),
        status_code=response_status,
        media_type=entry.mime or "application/octet-stream",
        headers=headers,
    )


@router.post("/files/{source_id}/upload", dependencies=[Depends(require_permission("files.write"))])
async def upload_file(source_id: str, path: str, request: Request) -> FileEntry:
    source = await _resolve_source(source_id)
    if not source.caps.write:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Quelle unterstützt kein Schreiben.")
    content_length = request.headers.get("content-length")
    size = int(content_length) if content_length is not None and content_length.isdigit() else None
    return await _call(source.open_write(PurePosixPath(path), request.stream(), size=size))


@router.post("/files/{source_id}/mkdir", dependencies=[Depends(require_permission("files.write"))])
async def mkdir(source_id: str, payload: MkdirIn) -> FileEntry:
    source = await _resolve_source(source_id)
    if not source.caps.mkdir:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Quelle unterstützt kein Anlegen von Ordnern.")
    return await _call(source.mkdir(PurePosixPath(payload.path)))


@router.post("/files/{source_id}/rename", dependencies=[Depends(require_permission("files.write"))])
async def rename(source_id: str, payload: RenameIn) -> FileEntry:
    source = await _resolve_source(source_id)
    if not source.caps.rename:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Quelle unterstützt kein Umbenennen.")
    return await _call(source.rename(PurePosixPath(payload.src), PurePosixPath(payload.dst)))


@router.post("/files/{source_id}/remove", dependencies=[Depends(require_permission("files.write"))])
async def remove(source_id: str, payload: RemoveIn) -> dict[str, bool]:
    source = await _resolve_source(source_id)
    if not source.caps.remove:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Quelle unterstützt kein Löschen.")
    await _call(source.remove(PurePosixPath(payload.path), recursive=payload.recursive))
    return {"removed": True}


@router.get("/files/search", dependencies=[Depends(require_permission("files.read"))])
async def search(q: str, sources: str | None = Query(None)) -> list[SearchHit]:
    wanted = set(sources.split(",")) if sources else None
    all_sources = await _all_sources()
    targets = [s for s in all_sources if (wanted is None or s.source_id in wanted) and s.caps.search]

    async def _search_one(source: FileSource) -> list[SearchHit]:
        try:
            return [SearchHit(source_id=source.source_id, entry=entry) async for entry in source.search(q, root=PurePosixPath("/"))]
        except Exception:  # noqa: BLE001 - eine kaputte Quelle darf die Suche der anderen nicht verhindern
            logger.exception("file_source_search_failed source_id=%s", source.source_id)
            return []

    results = await asyncio.gather(*(_search_one(s) for s in targets))
    return [hit for group in results for hit in group]


async def _run_transfer(run_id: str, source_from: FileSource, path_from: PurePosixPath, source_to: FileSource, path_to: PurePosixPath) -> None:
    """Streamt `open_read(A) -> open_write(B)` serverseitig (docs/04 §3: "ohne dass
    die Datei ueber den Client laeuft"), IMMER in einem eigenen `session_scope()` --
    der Request, der diesen Task gestartet hat, ist laengst zurueckgekehrt, bevor
    dieser Task fertig wird (derselbe Grund, aus dem jeder `ctx.*`-Handle seine
    eigene Session oeffnet, siehe docs/00-DECISIONS.md D-14)."""
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
    except Exception as exc:  # noqa: BLE001 - Ergebnis MUSS immer geschrieben werden, siehe core.gate.execute_action()-Praezedenz
        logger.exception("file_transfer_failed run_id=%s", run_id)
        async with session_scope() as session:
            run = await jobs_service.get_run(session, run_id)
            if run is not None:
                await jobs_service.finish_run(session, run, status="failed", error=str(exc))
        await hub.publish(f"runs.{run_id}", {"status": "failed", "error": str(exc)}, required_permission="files.read")
        return

    async with session_scope() as session:
        run = await jobs_service.get_run(session, run_id)
        if run is not None:
            await jobs_service.finish_run(session, run, status="succeeded", exit_code=0)
    await hub.publish(f"runs.{run_id}", {"status": "succeeded", "bytes": bytes_done}, required_permission="files.read")


@router.post("/files/transfer", dependencies=[Depends(require_permission("files.write"))])
async def transfer(payload: TransferIn, session: SessionDep, user: CurrentUser) -> TransferOut:
    source_from = await _resolve_source(payload.from_.source)
    source_to = await _resolve_source(payload.to.source)
    if not source_to.caps.write:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Zielquelle unterstützt kein Schreiben.")

    run = await jobs_service.create_run(
        session, job_id=None, ext_id=None, trigger="manual",
        actor={"type": "user", "id": user.id, "label": user.username},
    )
    asyncio.ensure_future(
        _run_transfer(run.id, source_from, PurePosixPath(payload.from_.path), source_to, PurePosixPath(payload.to.path))
    )
    return TransferOut(run_id=run.id)
