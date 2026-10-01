"""Erfuellt `nodvard_sdk.capabilities.FileSource`/`FileSourceProvider` gegen die
Nextcloud-WebDAV-API.

Genau EINE Quelle pro konfigurierter Nextcloud-Instanz (`source_id="nextcloud"`) --
mehrere Server-Instanzen sind derzeit nicht vorgesehen (dieselbe
Scope-Entscheidung wie bei proxmox in Phase 1)."""

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
"""Echte Volltextsuche in DateiINHALTEN statt nur -namen.
WebDAV liefert `mime` nicht zuverlaessig fuer jede
Datei (haengt vom Server ab) -- diese Allowlist ist der Fallback, wenn `mime` fehlt
oder nicht eindeutig text-artig ist. Bewusst per Extension dupliziert (identisch in
terminals `__init__.py`) statt in die SDK zu wandern: bislang genau zwei Verwender,
keine dritte Extension braucht das noch -- dasselbe Muster wie D-15 (erst beim dritten
unabhaengigen Fall generalisieren)."""

_MAX_CONTENT_BYTES_PER_FILE = 64 * 1024
_MAX_CONTENT_READS = 50


def _looks_like_text(name: str, mime: str | None) -> bool:
    if mime:
        if mime.startswith("text/"):
            return True
        if mime in ("application/json", "application/xml", "application/javascript", "application/x-yaml"):
            return True
        if not mime.startswith("application/octet-stream"):
            # Ein bekannter, aber eindeutig binaerer Mime-Typ (image/*, video/*, ...)
            # -- der Extensions-Fallback unten wuerde sonst z.B. "foo.svg" trotz
            # image/svg+xml erneut versuchen, das ist bereits durch die
            # text/-Pruefung oben abgedeckt, alles andere hier ist echt binaer.
            return False
    return PurePosixPath(name).suffix.lower() in _TEXT_EXTENSIONS


class NextcloudFileSource:
    """Erfuellt `nodvard_sdk.capabilities.FileSource`.

    **`caps.search=True` (Nachtrag):** WebDAV hat kein portables Volltextsuch-
    Primitiv, und Nextclouds eigene `SEARCH`-Erweiterung waere ein weiterer,
    Nextcloud-spezifischer Sonderpfad (Sonderformat, eigenes XML) -- deshalb baut
    `search()` stattdessen auf dem bereits vorhandenen, generischen
    `list_children()` (PROPFIND Depth:1) auf: eine echte, aber bewusst begrenzte
    Verzeichnis-Wanderung ab `root` (siehe `_MAX_RESULTS`/`_MAX_DIRS_VISITED`
    dort) statt eines Endlosscans auf sehr grossen Baeumen.

    **Volltextsuche in Dateiinhalten:** urspruenglich nur der Dateiname.
    `search()` liest jetzt
    zusaetzlich den Anfang text-artiger Dateien (`_looks_like_text`,
    `_MAX_CONTENT_BYTES_PER_FILE`/`_MAX_CONTENT_READS` als zweiter Deckel neben den
    beiden oben). Dieselbe Erweiterung gilt jetzt auch fuer terminals
    `_SshFileSource.search()` (WP-4/WP-11) -- vorher bewusst nur Dateiname, siehe
    dortigen Docstring fuer die identische, dort dupliziert gehaltene Logik."""

    _MAX_RESULTS = 200
    _MAX_DIRS_VISITED = 500
    _MAX_CONCURRENCY = 8

    def __init__(self, ctx: "ExtensionContext", connector: NextcloudConnector) -> None:
        self.source_id = SOURCE_ID
        self.label = "Nextcloud"
        self.icon = "cloud"
        self.caps = FileSourceCaps(write=True, rename=True, remove=True, mkdir=True, search=True, range_read=True)
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
        """`recursive` ist hier nur informativ -- WebDAV `DELETE` auf eine Collection
        entfernt IMMER alles darunter, es gibt keinen Nicht-rekursiven Modus im
        Protokoll. Ein `recursive=False`-Aufruf auf ein nicht-leeres Verzeichnis
        entfernt es trotzdem vollstaendig, statt (wie ein lokales `rmdir()`) mit
        einem Fehler abzulehnen -- ehrlich benannter Unterschied, keine Nachbildung
        auf Anwendungsebene in dieser Runde."""
        await self._connector.remove(str(path))

    async def rename(self, src: PurePosixPath, dst: PurePosixPath) -> FileEntry:
        await self._connector.rename(str(src), str(dst))
        return await self.stat(dst)

    async def _list_children_or_empty(self, path: PurePosixPath) -> list[WebDavEntry]:
        try:
            return await self._connector.list_children(str(path))
        except WebDavError:
            # Ein einzelnes nicht erreichbares Unterverzeichnis (z. B. durch eine
            # Zugriffsbeschraenkung) bricht die Suche NICHT komplett ab -- dieselbe
            # Haltung wie `api/v1/files.py::search()`s "eine kaputte Quelle darf die
            # Suche der anderen nicht verhindern", hier eine Ebene tiefer fuer ein
            # einzelnes Unterverzeichnis angewendet.
            return []

    async def _content_matches(self, path: str, needle: str) -> bool:
        """Liest hoechstens `_MAX_CONTENT_BYTES_PER_FILE` (nicht die ganze Datei --
        ein Treffer im ersten Ausschnitt reicht, und ein einzelnes riesiges Log darf
        die Suche nicht dominieren). `errors="ignore"` statt eines Abbruchs bei einer
        Datei, die trotz `_looks_like_text` doch nicht valides UTF-8 ist (z. B. eine
        Textdatei mit Latin-1-Resten) -- ein bewusst grosszuegiger Best-Effort, kein
        Volltextindex."""
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
        """Breitensuche ab `root`, gegen Dateiname UND -inhalt. Ein
        Treffer ist Name ODER Inhalt; Inhalt wird nur gelesen, wenn der Name schon
        nicht traf, `_looks_like_text()` zustimmt UND `_MAX_CONTENT_READS` fuer
        diesen Aufruf noch nicht erschoepft ist (Bandbreiten-Deckel zusaetzlich zu
        `_MAX_RESULTS`/`_MAX_DIRS_VISITED`) -- danach laeuft die Namenssuche fuer den
        Rest des Baums unveraendert weiter, nur ohne weitere Inhalts-Reads.

        **`_MAX_CONCURRENCY` (Nachtrag, live gegen die echte Nextcloud gefunden):**
        eine rein serielle Breitensuche (ein PROPFIND nach dem anderen) brauchte
        gegen einen echten, nicht-trivialen Baum (Foto-Backup-Ordner mit vielen
        Dateien) 16-17 Sekunden pro Anfrage -- jeder Netzwerk-Umlauf einzeln
        nacheinander. Jetzt PRO EBENE bis zu `_MAX_CONCURRENCY` Verzeichnisse
        gleichzeitig abgefragt, statt eines nach dem anderen -- gleiche Grenzen,
        gleiches Ergebnis, nur parallel statt seriell."""
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
    """Erfuellt `nodvard_sdk.capabilities.FileSourceProvider`. Liefert die eine
    konfigurierte Quelle nur, wenn `base_url`/`username`/das Secret tatsaechlich
    gesetzt sind -- unkonfiguriert erscheint die Extension NICHT im Dateimanager,
    statt mit einer garantiert fehlschlagenden Quelle aufzutauchen."""

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def file_sources(self) -> list[NextcloudFileSource]:
        from .config import build_connector

        connector = await build_connector(self._ctx)
        if connector is None:
            return []
        return [NextcloudFileSource(self._ctx, connector)]
