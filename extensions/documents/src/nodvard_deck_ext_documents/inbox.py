"""Nextcloud-Ordner als Eingang: neue PDFs und Bilder aus einem eingestellten Ordner werden Dokumente.

Der Abruf läuft alle 15 Minuten (Job `inbox`) und auf Knopfdruck (`POST /inbox/fetch`); in der Nextcloud wird nur gelesen."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from nodvard_deck.db.base import utcnow
from nodvard_sdk import FileEntry, FileSourceProvider
from sqlalchemy import select

from .models import InboxFile
from .storage import MAX_DOCUMENT_BYTES

logger = logging.getLogger(__name__)

# Die Dateien kommen über die Dateiquelle der Nextcloud-Erweiterung (`FileSource`, `requires = ["nextcloud"]`);
# ohne sie oder ohne eingestellten Ordner passiert nichts.
SOURCE_ID = "nextcloud"
# Nur der Ordner selbst, keine Unterordner; höchstens so viele Dateien je Lauf, der Rest kommt beim nächsten. Dateien, die
# sich nicht lesen oder speichern ließen, kommen beim nächsten Lauf wieder dran und zählen nicht gegen diese Grenze
# (sonst hielten sie neue Dateien auf); dafür begrenzt MAX_ATTEMPTS_PER_RUN die Versuche.
MAX_FILES_PER_RUN = 20
MAX_ATTEMPTS_PER_RUN = 3 * MAX_FILES_PER_RUN
# Höchstens so viele Seiten beim Auflisten des Ordners.
MAX_PAGES = 100

# Nur PDF, PNG, JPEG und WebP (Größe bis `MAX_DOCUMENT_BYTES`); der Inhalt muss zur Endung passen (`content_matches`).
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# (Session, Kennung des neuen Dokuments): merkt die Datei in derselben Transaktion wie das Dokument.
MarkSaved = Callable[[Any, str], Awaitable[None]]

# (Inhalt, Dateityp, Dateiname, mark) -> Kennung des neuen Dokuments; derselbe Weg wie beim Hochladen.
StoreDocument = Callable[[bytes, str, str, MarkSaved], Awaitable[str]]


class InboxError(Exception):
    """Der Abruf geht gar nicht (Nextcloud fehlt, Ordner ungültig oder nicht lesbar). Der Text wird gezeigt."""


@dataclass
class InboxResult:
    imported: list[str] = field(default_factory=list)
    too_large: list[str] = field(default_factory=list)
    wrong_content: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    more_waiting: bool = False

    @property
    def notable(self) -> bool:
        """Gab es etwas zu berichten? Ein Lauf ohne alles soll das letzte Ergebnis nicht verdrängen."""
        return bool(self.imported or self.too_large or self.wrong_content or self.failed or self.more_waiting)

    def as_dict(self) -> dict[str, Any]:
        return {
            "imported": len(self.imported),
            "too_large": self.too_large,
            "wrong_content": self.wrong_content,
            "failed": self.failed,
            "more_waiting": self.more_waiting,
        }


def normalize_folder(value: object) -> str | None:
    """Der eingestellte Ordner als `/a/b`, oder None (aus). `..` und leere Teile gibt es nicht."""
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return None
    parts = [p for p in text.split("/") if p]
    if not parts:
        return "/"
    if any(p in (".", "..") for p in parts):  # der Abruf bleibt im eingestellten Ordner
        raise InboxError("Der Nextcloud-Ordner darf kein „..“ enthalten.")
    return "/" + "/".join(parts)


def content_matches(content: bytes, content_type: str) -> bool:
    if content_type == "application/pdf":
        # Die Kennung darf laut PDF-Norm irgendwo in den ersten 1024 Bytes stehen (manche Scanner setzen etwas davor).
        return b"%PDF-" in content[:1024]
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/webp":
        return content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def _same_time(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return a is b
    return int(a.timestamp()) == int(b.timestamp())


async def find_source(ctx: Any) -> Any | None:
    """Die Dateiquelle der Nextcloud, oder None, wenn die Erweiterung aus oder nicht eingerichtet ist."""
    for provider in await ctx.capabilities.query(FileSourceProvider):
        for source in await provider.file_sources():
            if getattr(source, "source_id", None) == SOURCE_ID:
                return source
    return None


async def _read_limited(source: Any, path: PurePosixPath) -> bytes | None:
    """Liest die Datei; None, wenn sie größer als erlaubt ist (dann wird nicht weiter geladen und die Verbindung
    gleich geschlossen)."""
    chunks: list[bytes] = []
    total = 0
    async with contextlib.aclosing(source.open_read(path)) as stream:
        async for chunk in stream:
            total += len(chunk)
            if total > MAX_DOCUMENT_BYTES:
                return None
            chunks.append(chunk)
    return b"".join(chunks)


async def _list_files(source: Any, folder: str) -> list[FileEntry]:
    entries: list[FileEntry] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        page = await source.list_dir(PurePosixPath(folder), cursor=cursor)
        entries.extend(e for e in page.items if not e.is_dir)
        if not page.next_cursor or page.next_cursor == cursor:
            break
        cursor = page.next_cursor
    return entries


async def _upsert(session: Any, entry: FileEntry, document_id: str | None) -> None:
    row = await session.get(InboxFile, (SOURCE_ID, entry.path))
    if row is None:
        row = InboxFile(source_id=SOURCE_ID, path=entry.path)
        session.add(row)
    row.size_bytes = entry.size
    row.modified_at = entry.modified_at
    row.document_id = document_id
    row.imported_at = utcnow()


async def _remember_skipped(ctx: Any, entry: FileEntry) -> None:
    """Übersprungene Datei (zu groß, falscher Inhalt) merken: gemeldet wird sie einmal, beim nächsten Lauf nicht
    wieder. Wird sie in der Nextcloud ersetzt, gilt sie wieder als neu."""
    async with ctx.db.session() as session:
        await _upsert(session, entry, None)


async def fetch_inbox(ctx: Any, folder: str, store: StoreDocument) -> InboxResult:
    source = await find_source(ctx)
    if source is None:
        raise InboxError("Nextcloud ist nicht eingerichtet. Bitte zuerst die Erweiterung „Nextcloud“ einrichten.")
    try:
        entries = await _list_files(source, folder)
    except Exception as exc:
        # Fehler der Nextcloud-Quelle sind lesbare Sätze (Ordner fehlt, Anmeldung falsch); alles andere bleibt kurz.
        if getattr(exc, "readable", False):
            reason = str(exc)
        else:
            logger.warning("Nextcloud-Ordner %s ließ sich nicht lesen", folder, exc_info=True)
            reason = "Die Nextcloud hat nicht geantwortet."
        raise InboxError(f"Der Ordner {folder} ließ sich nicht lesen. {reason}") from exc

    candidates = sorted(
        (e for e in entries if PurePosixPath(e.name).suffix.lower() in CONTENT_TYPES),
        key=lambda e: (e.modified_at is None, e.modified_at.timestamp() if e.modified_at else 0.0, e.name),
    )
    async with ctx.db.session() as session:
        known = {
            row.path: row
            for row in (await session.execute(select(InboxFile).where(InboxFile.source_id == SOURCE_ID))).scalars()
        }
    # Neu ist eine Datei, wenn es ihren Pfad in `InboxFile` noch nicht gibt oder sich Größe oder Änderungszeit geändert haben.
    new = [
        e for e in candidates
        if e.path not in known
        or known[e.path].size_bytes != e.size
        or not _same_time(known[e.path].modified_at, e.modified_at)
    ]

    result = InboxResult()
    handled = attempts = 0
    for index, entry in enumerate(new):
        if handled >= MAX_FILES_PER_RUN or attempts >= MAX_ATTEMPTS_PER_RUN:
            result.more_waiting = index < len(new)
            break
        attempts += 1
        content_type = CONTENT_TYPES[PurePosixPath(entry.name).suffix.lower()]
        if entry.size is not None and entry.size > MAX_DOCUMENT_BYTES:
            result.too_large.append(entry.name)
            await _remember_skipped(ctx, entry)
            handled += 1
            continue
        try:
            content = await _read_limited(source, PurePosixPath(entry.path))
        except Exception:
            logger.warning("Datei %s aus der Nextcloud ließ sich nicht lesen", entry.path, exc_info=True)
            result.failed.append(entry.name)
            continue
        if content is None:
            result.too_large.append(entry.name)
            await _remember_skipped(ctx, entry)
            handled += 1
            continue
        if not content_matches(content, content_type):  # etwa eine `.pdf`, die in Wahrheit eine Webseite ist
            result.wrong_content.append(entry.name)
            await _remember_skipped(ctx, entry)
            handled += 1
            continue

        # Dokument und Merkeintrag entstehen in einer Transaktion (`store` ruft `mark` in seiner Session auf): bricht der
        # Lauf dazwischen ab, gibt es weder ein Dokument ohne Eintrag (käme doppelt) noch einen Eintrag ohne Dokument.
        async def _mark(session: Any, document_id: str, entry: FileEntry = entry) -> None:
            await _upsert(session, entry, document_id)

        try:
            await store(content, content_type, entry.name, _mark)
        except Exception:
            logger.warning("Datei %s aus der Nextcloud ließ sich nicht speichern", entry.path, exc_info=True)
            result.failed.append(entry.name)
            continue
        result.imported.append(entry.name)
        handled += 1
    return result
