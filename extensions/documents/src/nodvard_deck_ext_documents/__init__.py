"""documents-Extension -- Paperless-ngx-artiges Dokumentenarchiv: Upload, OCR
ueber die Tesseract-BIBLIOTHEK (pytesseract, siehe ocr.py -- ausdruecklich
NICHT der separate Paperless-ngx-Container), automatisches Verschlagworten
anhand des erkannten Texts, Volltextsuche.

Zweite Extension mit echten eigenen Datenbank-Tabellen nach `inventory`
(docs/02-EXTENSION-API.md §7: eigener Alembic-Branch) -- `models.py`
deklariert eine eigene `Base`/`MetaData`, `migrations/versions/
9f1e2d3c4b5a_create_documents_tables.py` ist ein eigener, unabhaengiger
Alembic-Kopf. `ctx.db.declare_tables(Base.metadata)` unten validiert den
Pflicht-Praefix `ext_documents_`.

Dokument-BYTES liegen auf der Platte unter `ctx.data_dir/documents/`
(storage.py), nicht in der Datenbank -- dasselbe Muster wie
`nodvard_deck_ext_inventory.images`."""

from __future__ import annotations

import asyncio
import unicodedata
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from nodvard_sdk import (
    Badge,
    ExtensionContext,
    GridSize,
    HealthReport,
    ListItem,
    ListView,
    NodvardExtension,
    PageSpec,
    Refresh,
    WidgetSpec,
)
from pydantic import BaseModel
from sqlalchemy import or_, select

from .models import Base, Document, DocumentTag, Tag
from .ocr import extract_text
from .storage import ALLOWED_CONTENT_TYPES, MAX_DOCUMENT_BYTES, delete_document_file, document_path, save_document


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


def _tag_out(row: Tag) -> TagOut:
    return TagOut(id=row.id, name=row.name, match_keyword=row.match_keyword, created_at=row.created_at)


def _sanitize_for_header(value: str) -> str:
    """Verhindert Header-Injection/Response-Splitting ueber einen
    Original-Dateinamen, der vom Client frei gewaehlt wird (Upload-Query-Param
    `filename`) -- CR/LF UND Anfuehrungszeichen raus, bevor der Wert in einen
    `Content-Disposition`-Header eingebettet wird."""
    return value.replace("\r", "").replace("\n", "").replace('"', "'")


def _content_disposition(filename: str) -> str:
    """`Content-Disposition` fuer beliebige Dateinamen: Starlette kodiert
    Header als latin-1, "Rechnung 49€.pdf", "Vertrag – Kopie.pdf" oder ein schmales
    Leerzeichen (U+202F, macOS-Screenshots) liessen Vorschau/Download mit HTTP 500
    scheitern. Deshalb `filename=` nur mit ASCII-Ersatz (Umlaute ohne Punkte, alles
    andere "_") plus den echten Namen UTF-8-kodiert in `filename*=` (RFC 6266) --
    den nehmen alle aktuellen Browser."""
    safe_name = _sanitize_for_header(filename)
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch != "\\" else "_"
        for ch in unicodedata.normalize("NFKD", safe_name)
        if not unicodedata.combining(ch)
    )
    return f"inline; filename=\"{fallback}\"; filename*=UTF-8''{quote(safe_name, safe='')}"


def _run_ocr(content: bytes, content_type: str) -> tuple[str | None, int | None, str, str | None]:
    """Synchrone Huelle um `ocr.extract_text()` -- IMMER ueber
    `asyncio.to_thread()` aufrufen (siehe ocr.py-Docstring). Ein OCR-Fehler
    (Tesseract nicht installiert, kaputtes PDF, fehlendes Sprachpaket auch im
    Fallback) darf den Upload nicht scheitern lassen -- das Dokument bleibt
    gespeichert und sichtbar, nur ohne durchsuchbaren Text."""
    try:
        text, page_count = extract_text(content, content_type)
        return text, page_count, "done", None
    except Exception as exc:  # noqa: BLE001 - OCR-Fehler-Isolation, siehe oben
        return None, None, "error", str(exc)


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
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

        async def _document_out(session: Any, row: Document) -> DocumentOut:
            return DocumentOut(
                id=row.id, original_filename=row.original_filename, content_type=row.content_type,
                size_bytes=row.size_bytes, page_count=row.page_count, ocr_text=row.ocr_text,
                ocr_status=row.ocr_status, ocr_error=row.ocr_error, created_at=row.created_at,
                updated_at=row.updated_at, tags=await _tags_for(session, row.id),
            )

        async def _auto_tag(session: Any, document_id: str, ocr_text: str) -> None:
            lowered = ocr_text.lower()
            candidates = (
                await session.execute(select(Tag).where(Tag.match_keyword.is_not(None)))
            ).scalars().all()
            for tag in candidates:
                if tag.match_keyword and tag.match_keyword.lower() in lowered:
                    session.add(DocumentTag(document_id=document_id, tag_id=tag.id))

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
        async def list_documents(q: str | None = None, tag_id: str | None = None) -> list[DocumentOut]:
            async with ctx.db.session() as session:
                stmt = select(Document).order_by(Document.created_at.desc())
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
        async def upload_document(filename: str, request: Request) -> DocumentOut:
            """Rohkoerper-Upload wie `POST /branding/logo` bzw.
            `nodvard_deck_ext_inventory`s Bild-Upload -- derselbe Grund: keine
            zusaetzliche `multipart/form-data`-Konvention im Projekt. Der
            Original-Dateiname kommt als Query-Param (wie `path` bei
            `POST /files/{source_id}/upload`), nicht aus einem Formularfeld."""
            content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
            extension = ALLOWED_CONTENT_TYPES.get(content_type)
            if extension is None:
                raise HTTPException(status_code=415, detail=f"Nicht unterstützter Dokumenttyp: {content_type or '(keiner)'}")

            content_length = request.headers.get("content-length")
            if content_length is not None and content_length.isdigit() and int(content_length) > MAX_DOCUMENT_BYTES:
                raise HTTPException(status_code=413, detail=f"Dokument zu gross (max. {MAX_DOCUMENT_BYTES // 1024 // 1024} MB).")
            content = await request.body()
            if len(content) > MAX_DOCUMENT_BYTES:
                raise HTTPException(status_code=413, detail=f"Dokument zu gross (max. {MAX_DOCUMENT_BYTES // 1024 // 1024} MB).")

            # OCR VOR dem Oeffnen einer DB-Session laufen lassen (kann bei einem
            # mehrseitigen PDF mehrere Sekunden dauern) -- ueber `asyncio.to_thread`,
            # weil `pytesseract` blockierend einen Subprocess aufruft, siehe ocr.py.
            # Denselben Fehler NICHT wiederholen, den WP-8 in
            # services/extensions.py schon einmal gefunden hat: keine offene
            # Session/Transaktion ueber einen langlaufenden, session-unabhaengigen
            # Aufruf offenhalten.
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

                return await _document_out(session, row)

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
                    headers={"Content-Disposition": _content_disposition(row.original_filename)},
                )

        @write_router.patch("/documents/{document_id}")
        async def rename_document(document_id: str, payload: _RenameIn) -> DocumentOut:
            """Anzeigename aendern (z. B. "scan_0042.pdf" -> "Stromrechnung 2026.pdf") --
            die Datei auf der Platte behaelt ihren internen Namen."""
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
                # DocumentTag-Zeilen kaskadieren ueber die FK (ondelete="CASCADE"),
                # die Datei auf der Platte nicht -- muss explizit weg, wie bei
                # ItemImage in nodvard_deck_ext_inventory.
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

        # Sicherheits-Nachtrag: hing ohne Berechtigung (= oeffentlich, siehe
        # ApiHandle.include_router()) -- Namen waren ohne Login lesbar.
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

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        async with ctx.db.session() as session:
            rows = (await session.execute(select(Document))).scalars().all()
            errors = [r for r in rows if r.ocr_status == "error"]
            message = f"{len(rows)} Dokument(e) erfasst"
            if errors:
                message += f", {len(errors)} mit OCR-Fehler"
            return HealthReport(healthy=True, message=message)
