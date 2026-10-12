"""Dokumentenarchiv (Paperless-ngx-artig): Upload, Texterkennung, automatische Tags nach Stichwort, Volltextsuche.

Eigene Tabellen mit eigenem Alembic-Zweig (`models.py`); die Dateien liegen auf der Platte (`storage.py`)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from nodvard_deck.db.base import utcnow
from nodvard_sdk import (
    Badge,
    Event,
    ExtensionContext,
    GridSize,
    HealthReport,
    ListItem,
    ListView,
    NodvardExtension,
    PageSpec,
    Refresh,
    WidgetSpec,
    max_body_bytes,
)
from pydantic import BaseModel
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from .inbox import InboxError, fetch_inbox, find_source, normalize_folder
from .models import Base, Document, DocumentItemLink, DocumentTag, Tag
from .ocr import extract_text
from .storage import (
    ALLOWED_CONTENT_TYPES,
    MAX_DOCUMENT_BYTES,
    delete_document_file,
    document_path,
    save_document,
)

logger = logging.getLogger(__name__)

# So viele Kennungen je Nachfrage beim Inventar (dort gilt dieselbe Grenze).
ITEM_CHECK_BATCH = 500

# So lange gilt die Antwort auf „Ist die Nextcloud eingerichtet?“ (siehe `_nextcloud_ready`).
INBOX_READY_CACHE_S = 60.0


class TagIn(BaseModel):
    name: str
    match_keyword: str | None = None


class TagOut(BaseModel):
    id: str
    name: str
    match_keyword: str | None
    created_at: datetime


class _RenameIn(BaseModel):
    original_filename: str


class DocumentOut(BaseModel):
    id: str
    original_filename: str
    content_type: str
    size_bytes: int
    page_count: int | None
    ocr_text: str | None
    ocr_status: str
    ocr_error: str | None
    created_at: datetime
    updated_at: datetime
    tags: list[TagOut]
    # Gegenstände im Inventar, zu denen das Dokument gehört (Rechnung, Handbuch).
    item_ids: list[str] = []


class InboxRunOut(BaseModel):
    """Ergebnis eines Abrufs aus dem Nextcloud-Ordner (Knopf oder alle 15 Minuten)."""

    at: datetime
    ok: bool
    message: str | None = None
    imported: int = 0
    too_large: list[str] = []
    wrong_content: list[str] = []
    failed: list[str] = []
    more_waiting: bool = False


class InboxStatusOut(BaseModel):
    folder: str | None
    folder_error: str | None = None
    # Nur geprüft, wenn ein Ordner eingestellt ist (sonst `false`), und höchstens einmal je Minute.
    nextcloud_ready: bool
    running: bool
    last_run: InboxRunOut | None = None
    # Wann zuletzt nachgesehen wurde, auch ohne Neues (dann bleibt `last_run` beim letzten Ergebnis mit Inhalt).
    last_checked_at: datetime | None = None


class _InboxJobSpec:
    """Holt alle 15 Minuten neue Dateien aus dem Nextcloud-Ordner (inbox.py). Ohne eingestellten Ordner nichts."""

    id = "inbox"
    name = "Nextcloud-Ordner abholen"
    schedule = "*/15 * * * *"
    enabled = True

    def __init__(self, run: Any) -> None:
        self._run = run
        self.params: dict[str, Any] = {}

    async def handler(self, **_: Any) -> dict[str, Any]:
        return await self._run()


# Kennung eines Gegenstands im Inventar (wie dort vergeben). Mehr wird nicht geprüft: die Dokumente kennen das
# Inventar nicht, die Seite bietet nur vorhandene Gegenstände an.
_ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _check_item_id(item_id: str) -> str:
    if not _ITEM_ID.fullmatch(item_id):
        raise HTTPException(status_code=422, detail="Ungültige Kennung eines Gegenstands.")
    return item_id


def _tag_out(row: Tag) -> TagOut:
    return TagOut(id=row.id, name=row.name, match_keyword=row.match_keyword, created_at=row.created_at)


# Die Dateien kommen von Nutzern. Wird die Adresse direkt geöffnet, soll weder ein Skript laufen
# noch der Browser den Typ erraten. Die Vorschau im Dashboard lädt die Datei als Blob und ist
# davon nicht betroffen.
_UNTRUSTED_FILE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}


def _sanitize_for_header(value: str) -> str:
    """Entfernt CR/LF und Anführungszeichen aus dem frei gewählten Dateinamen (Upload-Parameter `filename`), bevor er
    in `Content-Disposition` kommt: verhindert Header-Injection und Response-Splitting."""
    return value.replace("\r", "").replace("\n", "").replace('"', "'")


def _content_disposition(filename: str) -> str:
    """`Content-Disposition` für beliebige Dateinamen: `filename=` nur als ASCII-Ersatz, der echte Name UTF-8-kodiert
    in `filename*=` (RFC 6266), den alle aktuellen Browser nehmen."""
    # Starlette kodiert Header als latin-1: "Rechnung 49€.pdf", "Vertrag – Kopie.pdf" oder ein schmales Leerzeichen
    # (U+202F, macOS-Screenshots) ließen Vorschau und Download sonst mit HTTP 500 scheitern. Der Ersatz behält
    # Umlaute ohne Punkte, alles andere wird "_".
    safe_name = _sanitize_for_header(filename)
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch != "\\" else "_"
        for ch in unicodedata.normalize("NFKD", safe_name)
        if not unicodedata.combining(ch)
    )
    return f"inline; filename=\"{fallback}\"; filename*=UTF-8''{quote(safe_name, safe='')}"


def _run_ocr(content: bytes, content_type: str) -> tuple[str | None, int | None, str, str | None]:
    """Synchrone Hülle um `ocr.extract_text()`, nur über `asyncio.to_thread()` aufrufen (siehe ocr.py).

    Ein OCR-Fehler darf den Upload nicht scheitern lassen: das Dokument bleibt gespeichert, nur ohne durchsuchbaren Text."""
    try:
        text, page_count = extract_text(content, content_type)
        return text, page_count, "done", None
    except Exception as exc:  # noqa: BLE001 - OCR-Fehler-Isolation, siehe oben
        return None, None, "error", str(exc)


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        # Der Kern prüft die Tabellennamen auf den Präfix `ext_documents_` und weist die Erweiterung sonst ab.
        ctx.db.declare_tables(Base.metadata)

        async def _tags_for(session: Any, document_id: str) -> list[TagOut]:
            rows = (
                await session.execute(
                    select(Tag)
                    .join(DocumentTag, DocumentTag.tag_id == Tag.id)
                    .where(DocumentTag.document_id == document_id)
                    .order_by(Tag.name)
                )
            ).scalars().all()
            return [_tag_out(r) for r in rows]

        async def _item_ids_for(session: Any, document_id: str) -> list[str]:
            rows = await session.execute(
                select(DocumentItemLink.item_id).where(DocumentItemLink.document_id == document_id)
                .order_by(DocumentItemLink.created_at)
            )
            return list(rows.scalars().all())

        async def _document_out(session: Any, row: Document) -> DocumentOut:
            return DocumentOut(
                id=row.id, original_filename=row.original_filename, content_type=row.content_type,
                size_bytes=row.size_bytes, page_count=row.page_count, ocr_text=row.ocr_text,
                ocr_status=row.ocr_status, ocr_error=row.ocr_error, created_at=row.created_at,
                updated_at=row.updated_at, tags=await _tags_for(session, row.id),
                item_ids=await _item_ids_for(session, row.id),
            )

        async def _auto_tag(session: Any, document_id: str, ocr_text: str) -> None:
            lowered = ocr_text.lower()
            candidates = (
                await session.execute(select(Tag).where(Tag.match_keyword.is_not(None)))
            ).scalars().all()
            for tag in candidates:
                if tag.match_keyword and tag.match_keyword.lower() in lowered:
                    session.add(DocumentTag(document_id=document_id, tag_id=tag.id))

        async def _store_document(
            content: bytes, content_type: str, filename: str, item_id: str | None = None,
            on_saved: Callable[[Any, str], Awaitable[None]] | None = None,
        ) -> DocumentOut:
            """Legt ein Dokument an: Texterkennung, Datei auf die Platte, Stichwörter. Für das Hochladen und
            den Nextcloud-Ordner (`inbox.py`) derselbe Weg; `on_saved(session, id)` schreibt dort in derselben
            Transaktion mit, welche Datei übernommen ist."""
            extension = ALLOWED_CONTENT_TYPES[content_type]
            # OCR vor dem Öffnen der Datenbank-Session (bei einem mehrseitigen PDF mehrere Sekunden) und per
            # `asyncio.to_thread`, weil `pytesseract` blockierend einen Subprozess startet. So bleibt keine
            # Transaktion über einen langen, sitzungsunabhängigen Aufruf offen.
            ocr_text, page_count, ocr_status_, ocr_error = await asyncio.to_thread(_run_ocr, content, content_type)

            async with ctx.db.session() as session:
                row = Document(
                    filename="", original_filename=filename, content_type=content_type, size_bytes=len(content),
                    page_count=page_count, ocr_text=ocr_text, ocr_status=ocr_status_, ocr_error=ocr_error,
                )
                session.add(row)
                await session.flush()
                row.filename = save_document(ctx.data_dir, row.id, extension, content)
                await session.flush()

                if ocr_text:
                    await _auto_tag(session, row.id, ocr_text)
                    await session.flush()
                if item_id:
                    session.add(DocumentItemLink(document_id=row.id, item_id=item_id))
                    await session.flush()
                if on_saved is not None:
                    await on_saved(session, row.id)
                    await session.flush()

                return await _document_out(session, row)

        read_router = APIRouter()
        write_router = APIRouter()

        # --- Tags ---------------------------------------------------------
        @read_router.get("/tags")
        async def list_tags() -> list[TagOut]:
            async with ctx.db.session() as session:
                rows = (await session.execute(select(Tag).order_by(Tag.name))).scalars().all()
                return [_tag_out(r) for r in rows]

        @write_router.post("/tags", status_code=status.HTTP_201_CREATED)
        async def create_tag(payload: TagIn) -> TagOut:
            async with ctx.db.session() as session:
                existing = (
                    await session.execute(select(Tag).where(Tag.name == payload.name))
                ).scalar_one_or_none()
                if existing is not None:
                    raise HTTPException(status_code=409, detail=f"Tag '{payload.name}' existiert bereits.")
                row = Tag(name=payload.name, match_keyword=payload.match_keyword or None)
                session.add(row)
                await session.flush()
                return _tag_out(row)

        @write_router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_tag(tag_id: str) -> None:
            async with ctx.db.session() as session:
                row = await session.get(Tag, tag_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Tag nicht gefunden.")
                await session.delete(row)

        # --- Dokumente ------------------------------------------------------
        @read_router.get("/documents")
        async def list_documents(q: str | None = None, tag_id: str | None = None, item_id: str | None = None) -> list[DocumentOut]:
            async with ctx.db.session() as session:
                stmt = select(Document).order_by(Document.created_at.desc())
                if item_id:
                    stmt = stmt.where(
                        Document.id.in_(select(DocumentItemLink.document_id).where(DocumentItemLink.item_id == item_id))
                    )
                if q:
                    stmt = stmt.where(
                        or_(Document.original_filename.ilike(f"%{q}%"), Document.ocr_text.ilike(f"%{q}%"))
                    )
                if tag_id:
                    stmt = stmt.where(
                        Document.id.in_(select(DocumentTag.document_id).where(DocumentTag.tag_id == tag_id))
                    )
                rows = (await session.execute(stmt)).scalars().all()
                return [await _document_out(session, r) for r in rows]

        @write_router.post("/documents", status_code=status.HTTP_201_CREATED)
        @max_body_bytes(MAX_DOCUMENT_BYTES)
        async def upload_document(filename: str, request: Request, item_id: str | None = None) -> DocumentOut:
            """Rohkörper-Upload wie `POST /branding/logo`; der Dateiname kommt als Query-Parameter `filename`.

            `item_id` (optional): gleich mit diesem Gegenstand im Inventar verknüpfen, etwa die Rechnung am Gerät."""
            if item_id is not None:
                _check_item_id(item_id)
            content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
            if content_type not in ALLOWED_CONTENT_TYPES:
                raise HTTPException(status_code=415, detail=f"Nicht unterstützter Dokumenttyp: {content_type or '(keiner)'}")

            # Erst die Angabe im Kopf prüfen, dann die tatsächliche Größe: sie kann fehlen oder falsch sein.
            content_length = request.headers.get("content-length")
            if content_length is not None and content_length.isdigit() and int(content_length) > MAX_DOCUMENT_BYTES:
                raise HTTPException(status_code=413, detail=f"Dokument zu gross (max. {MAX_DOCUMENT_BYTES // 1024 // 1024} MB).")
            content = await request.body()
            if len(content) > MAX_DOCUMENT_BYTES:
                raise HTTPException(status_code=413, detail=f"Dokument zu gross (max. {MAX_DOCUMENT_BYTES // 1024 // 1024} MB).")

            return await _store_document(content, content_type, filename, item_id)

        @read_router.get("/documents/{document_id}")
        async def get_document(document_id: str) -> DocumentOut:
            async with ctx.db.session() as session:
                row = await session.get(Document, document_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Dokument nicht gefunden.")
                return await _document_out(session, row)

        @read_router.get("/documents/{document_id}/download")
        async def download_document(document_id: str) -> Response:
            async with ctx.db.session() as session:
                row = await session.get(Document, document_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Dokument nicht gefunden.")
                path = document_path(ctx.data_dir, row.filename)
                if not path.is_file():
                    raise HTTPException(status_code=404, detail="Dokumentdatei fehlt auf der Platte.")
                return Response(
                    content=path.read_bytes(), media_type=row.content_type,
                    headers={
                        "Content-Disposition": _content_disposition(row.original_filename),
                        **_UNTRUSTED_FILE_HEADERS,
                    },
                )

        @write_router.patch("/documents/{document_id}")
        async def rename_document(document_id: str, payload: _RenameIn) -> DocumentOut:
            """Anzeigename ändern (z. B. "scan_0042.pdf" -> "Stromrechnung 2026.pdf"); die Datei auf der Platte
            behält ihren internen Namen."""
            # Der Name wird nur angezeigt und in Header eingebaut, nie als Pfad benutzt; trotzdem keine Pfadtrenner,
            # Zeilenumbrüche oder NUL.
            name = payload.original_filename.strip()
            if not name or len(name) > 300 or any(ch in name for ch in "/\\\r\n\x00"):
                raise HTTPException(status_code=422, detail="Ungültiger Dateiname.")
            async with ctx.db.session() as session:
                row = await session.get(Document, document_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Dokument nicht gefunden.")
                row.original_filename = name
                await session.flush()
                return await _document_out(session, row)

        @write_router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def delete_document(document_id: str) -> None:
            async with ctx.db.session() as session:
                row = await session.get(Document, document_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Dokument nicht gefunden.")
                # Die Tag-Zeilen kaskadieren über den Fremdschlüssel (ondelete="CASCADE"), die Datei auf der Platte
                # nicht: sie muss explizit weg. Die Zuordnungen zu Gegenständen ebenfalls ausdrücklich, weil die
                # Kaskade ohne `PRAGMA foreign_keys` (etwa in Tests) nicht griffe.
                await session.execute(delete(DocumentItemLink).where(DocumentItemLink.document_id == document_id))
                delete_document_file(ctx.data_dir, row.filename)
                await session.delete(row)

        @write_router.post("/documents/{document_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def attach_tag(document_id: str, tag_id: str) -> None:
            async with ctx.db.session() as session:
                if await session.get(Document, document_id) is None:
                    raise HTTPException(status_code=404, detail="Dokument nicht gefunden.")
                if await session.get(Tag, tag_id) is None:
                    raise HTTPException(status_code=404, detail="Tag nicht gefunden.")
                existing = await session.get(DocumentTag, (document_id, tag_id))
                if existing is None:
                    session.add(DocumentTag(document_id=document_id, tag_id=tag_id))

        @write_router.delete("/documents/{document_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def detach_tag(document_id: str, tag_id: str) -> None:
            async with ctx.db.session() as session:
                row = await session.get(DocumentTag, (document_id, tag_id))
                if row is not None:
                    await session.delete(row)

        @write_router.post("/documents/{document_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def link_item(document_id: str, item_id: str) -> None:
            """Dokument mit einem Gegenstand im Inventar verknüpfen (doppelt verknüpfen schadet nicht)."""
            _check_item_id(item_id)
            try:
                async with ctx.db.session() as session:
                    if await session.get(Document, document_id) is None:
                        raise HTTPException(status_code=404, detail="Dokument nicht gefunden.")
                    if await session.get(DocumentItemLink, (document_id, item_id)) is None:
                        session.add(DocumentItemLink(document_id=document_id, item_id=item_id))
            except IntegrityError:
                # Zwei Klicks gleichzeitig: die andere Anfrage hat die Zuordnung schon angelegt.
                return

        @write_router.delete("/documents/{document_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
        async def unlink_item(document_id: str, item_id: str) -> None:
            async with ctx.db.session() as session:
                row = await session.get(DocumentItemLink, (document_id, item_id))
                if row is not None:
                    await session.delete(row)

        # --- Nextcloud-Ordner als Eingang (inbox.py) ---------------------------
        inbox_lock = asyncio.Lock()
        # Nur im Speicher: nach einem Neustart steht hier wieder nichts, bis zum nächsten Abruf.
        inbox_state: dict[str, Any] = {"last_run": None, "last_checked_at": None}

        inbox_tasks: set[asyncio.Task[Any]] = set()
        inbox_ready: dict[str, Any] = {"value": None, "at": 0.0}

        async def _nextcloud_ready() -> bool:
            """Ob die Nextcloud eingerichtet ist. Die Abfrage baut die Verbindung samt Zugriff auf den Tresor, und
            jeder solche Zugriff landet im Protokoll; darum höchstens einmal je `INBOX_READY_CACHE_S` (die Seite
            fragt beim Warten auf einen Abruf alle paar Sekunden nach)."""
            now = time.monotonic()
            if inbox_ready["value"] is not None and now - inbox_ready["at"] < INBOX_READY_CACHE_S:
                return bool(inbox_ready["value"])
            try:
                ready = await find_source(ctx) is not None
            except Exception:
                logger.warning("Nextcloud-Quelle ließ sich nicht abfragen", exc_info=True)
                ready = False
            inbox_ready.update(value=ready, at=now)
            return ready
        self._inbox_tasks = inbox_tasks  # laufende Abrufe; Tests warten darauf

        async def _run_inbox(folder: str, *, manual: bool) -> InboxRunOut:
            """`manual`: auf Knopfdruck, das Ergebnis wird immer gezeigt. Ein Lauf nach Zeitplan ohne Neues
            verdrängt das letzte Ergebnis nicht, sonst wäre ein Hinweis auf eine übersprungene Datei nach
            15 Minuten weg; er setzt nur „zuletzt nachgesehen“."""

            async def _store(content: bytes, content_type: str, filename: str, mark: Any) -> str:
                return (await _store_document(content, content_type, filename, on_saved=mark)).id

            notable = True
            try:
                result = await fetch_inbox(ctx, folder, _store)
                run = InboxRunOut(at=utcnow(), ok=True, **result.as_dict())
                notable = result.notable
            except InboxError as exc:
                run = InboxRunOut(at=utcnow(), ok=False, message=str(exc))
            except Exception:
                logger.exception("Abruf aus dem Nextcloud-Ordner gescheitert")
                run = InboxRunOut(
                    at=utcnow(), ok=False, message="Der Abruf ist unerwartet gescheitert. Einzelheiten stehen im Protokoll des Servers.",
                )
            inbox_state["last_checked_at"] = run.at
            if manual or notable or inbox_state["last_run"] is None:
                inbox_state["last_run"] = run
            return run

        async def _inbox_job() -> dict[str, Any]:
            try:
                folder = normalize_folder((await ctx.settings.get()).get("nextcloud_folder"))
            except InboxError as exc:
                return {"skipped": str(exc)}
            if folder is None:
                return {"skipped": "Kein Nextcloud-Ordner eingestellt."}
            if inbox_lock.locked():
                return {"skipped": "Der Abruf läuft schon."}
            async with inbox_lock:
                run = await _run_inbox(folder, manual=False)
            return {"imported": run.imported, "ok": run.ok, "message": run.message}

        @read_router.get("/inbox")
        async def inbox_status() -> InboxStatusOut:
            folder: str | None = None
            folder_error: str | None = None
            try:
                folder = normalize_folder((await ctx.settings.get()).get("nextcloud_folder"))
            except InboxError as exc:
                folder_error = str(exc)
            # Ohne Ordner ist der Abruf aus: dann gar nicht erst bei der Nextcloud nachsehen.
            ready = await _nextcloud_ready() if folder else False
            return InboxStatusOut(
                folder=folder, folder_error=folder_error, nextcloud_ready=ready, running=inbox_lock.locked(),
                last_run=inbox_state["last_run"], last_checked_at=inbox_state["last_checked_at"],
            )

        @write_router.post("/inbox/fetch", status_code=status.HTTP_202_ACCEPTED)
        async def inbox_fetch() -> InboxStatusOut:
            """Startet den Abruf im Hintergrund (die Texterkennung vieler PDFs kann Minuten dauern) und gibt den
            Stand zurück; fertig ist er, wenn `running` wieder `false` ist, das Ergebnis steht dann in `last_run`."""
            try:
                folder = normalize_folder((await ctx.settings.get()).get("nextcloud_folder"))
            except InboxError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if folder is None:
                raise HTTPException(status_code=409, detail="Kein Nextcloud-Ordner eingestellt (Einstellungen der Dokumente).")
            if inbox_lock.locked():
                raise HTTPException(status_code=409, detail="Der Abruf läuft schon. Bitte gleich noch einmal nachsehen.")
            await inbox_lock.acquire()

            async def _run_locked() -> None:
                try:
                    await _run_inbox(folder, manual=True)
                finally:
                    inbox_lock.release()

            task = asyncio.create_task(_run_locked())
            inbox_tasks.add(task)
            task.add_done_callback(inbox_tasks.discard)
            return await inbox_status()

        await ctx.scheduler.register_job(_InboxJobSpec(_inbox_job))

        ctx.api.include_router(read_router, permission="documents.read")
        ctx.api.include_router(write_router, permission="documents.write")

        # --- Widget: Dokumente ohne Tag --------------------------------------
        widget_router = APIRouter()

        @widget_router.get("/widgets/untagged")
        async def untagged_widget_data() -> dict[str, Any]:
            async with ctx.db.session() as session:
                tagged_ids = select(DocumentTag.document_id)
                rows = (
                    await session.execute(
                        select(Document)
                        .where(Document.id.not_in(tagged_ids))
                        .order_by(Document.created_at.desc())
                        .limit(20)
                    )
                ).scalars().all()
                return {
                    "data": [
                        {"id": r.id, "name": r.original_filename, "status": "ohne Tag", "tone": "warn"}
                        for r in rows
                    ],
                    "meta": {},
                }

        # Mit Berechtigung einhängen: ohne wäre der Router öffentlich (siehe `ApiHandle.include_router()`), und die
        # Namen wären ohne Login lesbar.
        ctx.api.include_router(widget_router, permission="documents.read")

        ctx.ui.register_page(
            PageSpec(
                id="documents",
                path="/documents",
                title="Dokumente",
                icon="file-text",
                nav_section="Dokumente",
                nav_order=10,
                component="DocumentsPage",
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="untagged",
                title="Dokumente ohne Tag",
                icon="tag",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=300),
                data_endpoint="widgets/untagged",
                permissions=["documents.read"],
                view=ListView(
                    item=ListItem(title="{{ name }}", badge=Badge(text="{{ status }}", tone="{{ tone }}")),
                    empty_text="Alle Dokumente sind getaggt",
                ),
            )
        )

        # Wird ein Gegenstand im Inventar gelöscht, gehören seine Dokumente zu nichts mehr: Verknüpfung weg, die
        # Dokumente selbst bleiben (eine Rechnung braucht man vielleicht noch).
        async def _on_item_deleted(event: Event) -> None:
            item_id = str(event.payload.get("item_id") or "")
            if not _ITEM_ID.fullmatch(item_id):
                return
            async with ctx.db.session() as session:
                removed = await session.execute(delete(DocumentItemLink).where(DocumentItemLink.item_id == item_id))
            if removed.rowcount:
                # Unumkehrbar, darum wenigstens im Protokoll des Servers: wann, welcher Gegenstand, wie viele.
                logger.info("Gegenstand %s im Inventar gelöscht: %d Zuordnung(en) entfernt", item_id, removed.rowcount)

        ctx.events.subscribe("inventory.item_deleted", _on_item_deleted)

        # Waren die Dokumente aus, während im Inventar ein Gegenstand gelöscht wurde, blieb die Zuordnung stehen.
        # Beim Start (und wenn das Inventar später startet) darum nachfragen: Das Inventar meldet jeden Gegenstand,
        # den es nicht mehr gibt, wie beim Löschen, und `_on_item_deleted` räumt auf. Ist das Inventar aus, passiert
        # nichts; die Seite zeigt solche Zuordnungen als „Gegenstand nicht mehr da“.
        async def _request_item_check(_event: Event | None = None) -> None:
            # Nur Aufräumen: scheitert es (etwa weil die Datenbank noch nicht bereit ist), darf das den Start der
            # Dokumente nie kippen. Dann eben beim nächsten Start.
            try:
                async with ctx.db.session() as session:
                    ids = list((await session.execute(select(DocumentItemLink.item_id).distinct())).scalars())
                for start in range(0, len(ids), ITEM_CHECK_BATCH):
                    await ctx.events.publish(
                        Event(name="inventory.items_check_requested", payload={"item_ids": ids[start:start + ITEM_CHECK_BATCH]})
                    )
            except Exception:
                logger.warning("Nachfrage beim Inventar nach gelöschten Gegenständen gescheitert", exc_info=True)

        ctx.events.subscribe("inventory.started", _request_item_check)
        self._request_item_check = _request_item_check

    async def on_start(self, ctx: ExtensionContext) -> None:
        await self._request_item_check()

    async def on_stop(self, ctx: ExtensionContext) -> None:
        # Ein laufender Abruf endet mit der Erweiterung; sonst liefe er nach erneutem Einschalten neben einem neuen.
        for task in list(getattr(self, "_inbox_tasks", ())):
            task.cancel()

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        async with ctx.db.session() as session:
            rows = (await session.execute(select(Document))).scalars().all()
            errors = [r for r in rows if r.ocr_status == "error"]
            message = f"{len(rows)} Dokument(e) erfasst"
            if errors:
                message += f", {len(errors)} mit OCR-Fehler"
            return HealthReport(healthy=True, message=message)
