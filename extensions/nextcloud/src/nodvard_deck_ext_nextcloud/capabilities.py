"""`FileSource` und `FileSourceProvider` für Nextcloud über WebDAV: genau eine Quelle (`source_id="nextcloud"`),
mehrere Nextclouds sind nicht vorgesehen."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from nodvard_sdk import FileEntry, FileSourceCaps, Page, SourceInfo

from .connector import NextcloudConnector, WebDavEntry, WebDavError

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

SOURCE_ID = "nextcloud"


def _to_file_entry(entry: WebDavEntry) -> FileEntry:
    return FileEntry(
        name=entry.name, path=entry.path, is_dir=entry.is_dir, size=entry.size,
        modified_at=entry.modified_at, mime=entry.mime,
    )


_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".h", ".cpp",
    ".sh", ".bash", ".ps1", ".sql", ".html", ".htm", ".css", ".xml", ".svg",
}
"""WebDAV liefert `mime` nicht für jede Datei zuverlässig; diese Endungen sind dann der Fallback für die
Inhaltssuche. Die Terminal-Erweiterung führt bewusst eine eigene Kopie."""

_MAX_CONTENT_BYTES_PER_FILE = 64 * 1024
_MAX_CONTENT_READS = 50


def _looks_like_text(name: str, mime: str | None) -> bool:
    if mime:
        if mime.startswith("text/"):
            return True
        if mime in ("application/json", "application/xml", "application/javascript", "application/x-yaml"):
            return True
        if not mime.startswith("application/octet-stream"):
            # Ein bekannter Mime-Typ ohne text/ ist binär, auch wenn die Endung nach Text aussieht (`.svg`).
            return False
    return PurePosixPath(name).suffix.lower() in _TEXT_EXTENSIONS


class NextcloudFileSource:
    """Erfüllt `FileSource` über WebDAV; die Suche wandert begrenzt durch die Ordner, weil WebDAV keine portable
    Volltextsuche hat."""

    _MAX_RESULTS = 200
    _MAX_DIRS_VISITED = 500
    _MAX_CONCURRENCY = 8

    def __init__(self, ctx: "ExtensionContext", connector: NextcloudConnector) -> None:
        self.source_id = SOURCE_ID
        self.label = "Nextcloud"
        self.icon = "cloud"
        self.caps = FileSourceCaps(write=True, rename=True, remove=True, mkdir=True, search=True, range_read=True)
        # Die Quelle arbeitet mit dem Nextcloud-Konto aus den Einstellungen: `files.read` allein reicht dafür nicht.
        self.required_permission = "files.nextcloud"
        self._ctx = ctx
        self._connector = connector

    async def stat(self, path: PurePosixPath) -> FileEntry:
        return _to_file_entry(await self._connector.stat_one(str(path)))

    async def list_dir(self, path: PurePosixPath, *, cursor: str | None = None) -> Page[FileEntry]:
        entries = await self._connector.list_children(str(path))
        return Page(items=[_to_file_entry(e) for e in entries])

    async def open_read(self, path: PurePosixPath, *, offset: int = 0) -> AsyncIterator[bytes]:
        async with self._connector.open_read(str(path), offset=offset) as response:
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk

    async def open_write(self, path: PurePosixPath, stream: AsyncIterator[bytes], *, size: int | None = None) -> FileEntry:
        await self._connector.upload(str(path), stream)
        return await self.stat(path)

    async def mkdir(self, path: PurePosixPath) -> FileEntry:
        await self._connector.mkdir(str(path))
        return await self.stat(path)

    async def remove(self, path: PurePosixPath, *, recursive: bool = False) -> None:
        """WebDAV `DELETE` löscht einen Ordner immer samt Inhalt, auch bei `recursive=False`."""
        await self._connector.remove(str(path))

    async def rename(self, src: PurePosixPath, dst: PurePosixPath) -> FileEntry:
        await self._connector.rename(str(src), str(dst))
        return await self.stat(dst)

    async def _list_children_or_empty(self, path: PurePosixPath) -> list[WebDavEntry]:
        try:
            return await self._connector.list_children(str(path))
        except WebDavError:
            # Ein Unterordner, der nicht antwortet oder gesperrt ist, fehlt im Ergebnis, statt die Suche abzubrechen.
            return []

    async def _content_matches(self, path: str, needle: str) -> bool:
        """Sucht nur in den ersten `_MAX_CONTENT_BYTES_PER_FILE` Bytes, damit ein großes Log die Suche nicht bremst;
        nicht lesbares UTF-8 wird übergangen statt abzubrechen."""
        read = 0
        buf = bytearray()
        try:
            async for chunk in self.open_read(PurePosixPath(path)):
                buf.extend(chunk)
                read += len(chunk)
                if read >= _MAX_CONTENT_BYTES_PER_FILE:
                    break
        except WebDavError:
            return False
        return needle in buf.decode("utf-8", errors="ignore").lower()

    async def search(self, query: str, *, root: PurePosixPath) -> AsyncIterator[FileEntry]:  # noqa: ANN201
        """Breitensuche ab `root` nach Name oder Inhalt, je Runde bis zu `_MAX_CONCURRENCY` Ordner gleichzeitig, weil
        jeder Ordner einen eigenen Netz-Umlauf kostet. Inhalte nur von Text-Dateien und höchstens
        `_MAX_CONTENT_READS` je Aufruf."""
        needle = query.lower()
        frontier: list[PurePosixPath] = [root]
        visited_dirs = 0
        results = 0
        content_reads_used = 0
        while frontier and results < self._MAX_RESULTS and visited_dirs < self._MAX_DIRS_VISITED:
            budget = self._MAX_DIRS_VISITED - visited_dirs
            batch, frontier = frontier[: min(self._MAX_CONCURRENCY, budget)], frontier[min(self._MAX_CONCURRENCY, budget) :]
            listings = await asyncio.gather(*(self._list_children_or_empty(p) for p in batch))
            visited_dirs += len(batch)
            for children in listings:
                for child in children:
                    matched = needle in child.name.lower()
                    if (
                        not matched
                        and not child.is_dir
                        and content_reads_used < _MAX_CONTENT_READS
                        and _looks_like_text(child.name, child.mime)
                    ):
                        content_reads_used += 1
                        matched = await self._content_matches(child.path, needle)
                    if matched:
                        yield _to_file_entry(child)
                        results += 1
                        if results >= self._MAX_RESULTS:
                            return
                    if child.is_dir:
                        frontier.append(PurePosixPath(child.path))

    async def info(self) -> SourceInfo:
        try:
            await self._connector.stat_one("/")
        except WebDavError as exc:
            return SourceInfo(healthy=False, message=str(exc))
        return SourceInfo(healthy=True)


class NextcloudFileSourceProvider:
    """Erfüllt `FileSourceProvider`; ohne Adresse, Benutzer oder Geheimnis fehlt die Quelle im Dateimanager, statt
    dort aufzutauchen und garantiert zu scheitern."""

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def file_sources(self) -> list[NextcloudFileSource]:
        from .config import build_connector

        connector = await build_connector(self._ctx)
        if connector is None:
            return []
        return [NextcloudFileSource(self._ctx, connector)]
