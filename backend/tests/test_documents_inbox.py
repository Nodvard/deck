"""Nextcloud-Ordner als Eingang der Dokumente: neue PDFs und Bilder werden übernommen, nichts doppelt, Übersprungenes
mit Grund. Die Nextcloud ist hier eine nachgebaute Dateiquelle (`FileSourceProvider` der Erweiterung „nextcloud“)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import PurePosixPath

import pytest
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_sdk import FileEntry, FileSourceProvider, Page
from test_ext_documents import (  # noqa: F401
    _auth_header,
    _cleanup_sys_path,
    _create_documents_tables,
    _enable_documents,
    _png_bytes,
)

API = "/api/v1/ext/documents"
T1 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


class _FakeSource:
    source_id = "nextcloud"

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, datetime]] = {}
        self.broken: set[str] = set()
        self.missing_folder = False
        self.reads: list[str] = []
        self.gate: asyncio.Event | None = None

    def put(self, path: str, content: bytes, when: datetime = T1) -> None:
        self.files[path] = (content, when)

    async def list_dir(self, path: PurePosixPath, *, cursor: str | None = None) -> Page[FileEntry]:
        if self.gate is not None:
            await self.gate.wait()
        if self.missing_folder:
            err = RuntimeError("Den Ordner gibt es in der Nextcloud nicht.")
            err.readable = True  # type: ignore[attr-defined]
            raise err
        entries = [FileEntry(name="Unterordner", path=f"{path}/Unterordner", is_dir=True)]
        for p, (content, when) in sorted(self.files.items()):
            if str(PurePosixPath(p).parent) == str(path):
                entries.append(FileEntry(name=PurePosixPath(p).name, path=p, is_dir=False, size=len(content), modified_at=when))
        # Zwei Seiten, damit das Weiterblättern mitgeprüft wird.
        half = len(entries) // 2
        if cursor is None:
            return Page(items=entries[:half], next_cursor="2")
        return Page(items=entries[half:])

    async def open_read(self, path: PurePosixPath, *, offset: int = 0):
        self.reads.append(str(path))
        if str(path) in self.broken:
            raise OSError("Verbindung weg")
        content = self.files[str(path)][0]
        for i in range(0, len(content), 4096):
            yield content[i : i + 4096]


class _FakeProvider:
    """Zählt die Abfragen: In echt baut jede die Verbindung samt Zugriff auf den Tresor (steht im Protokoll)."""

    def __init__(self, source: _FakeSource | None) -> None:
        self.source = source
        self.calls = 0

    async def file_sources(self) -> list[_FakeSource]:
        self.calls += 1
        return [self.source] if self.source else []


def _use_nextcloud(source: _FakeSource | None) -> _FakeProvider:
    capabilities = get_extension_runtime().capabilities
    capabilities.clear_extension("nextcloud")
    provider = _FakeProvider(source)
    capabilities.provide("nextcloud", FileSourceProvider, provider)
    return provider


def _pdf() -> bytes:
    return b"%PDF-1.4\n%fake\n"


async def _setup(client, db_session, test_settings, folder: str | None = "/Scans", source: _FakeSource | None = None):
    headers = _auth_header(await _enable_documents(client, db_session, test_settings))
    source = source if source is not None else _FakeSource()
    _use_nextcloud(source)
    if folder is not None:
        saved = await client.put(
            "/api/v1/extensions/documents/settings", json={"values": {"nextcloud_folder": folder}}, headers=headers,
        )
        assert saved.status_code == 200, saved.text
    return headers, source


async def _wait_for_runs() -> None:
    """Wartet auf laufende Abrufe, ohne währenddessen etwas abzufragen: Test und App teilen sich hier eine
    Datenbankverbindung (StaticPool), eine Abfrage mittendrin könnte das Schreiben des Abrufs zurückrollen."""
    tasks = list(get_extension_runtime().loaded["documents"].instance._inbox_tasks)
    await asyncio.gather(*tasks)


async def _fetch(client, headers) -> dict:
    """Startet den Abruf (läuft im Hintergrund), wartet, bis er fertig ist, und gibt das Ergebnis zurück."""
    started = await client.post(f"{API}/inbox/fetch", headers=headers)
    assert started.status_code == 202, started.text
    await _wait_for_runs()
    status = (await client.get(f"{API}/inbox", headers=headers)).json()
    assert status["running"] is False
    return status["last_run"]


async def _documents(client, headers) -> list[str]:
    return sorted(d["original_filename"] for d in (await client.get(f"{API}/documents", headers=headers)).json())


@pytest.mark.asyncio
async def test_new_files_become_documents_once(client, db_session, test_settings):
    headers, source = await _setup(client, db_session, test_settings, folder=" Scans/ ")
    source.put("/Scans/rechnung.png", _png_bytes("RECHNUNG"))
    source.put("/Scans/brief.pdf", _pdf())
    source.put("/Scans/notiz.txt", b"kein Dokument")
    source.put("/Andere/foto.png", _png_bytes("X"))

    status = (await client.get(f"{API}/inbox", headers=headers)).json()
    assert status["folder"] == "/Scans" and status["nextcloud_ready"] is True and status["last_run"] is None

    run = await _fetch(client, headers)
    assert run["ok"] is True and run["imported"] == 2
    assert await _documents(client, headers) == ["brief.pdf", "rechnung.png"]
    assert (await client.get(f"{API}/inbox", headers=headers)).json()["last_run"]["imported"] == 2

    # Zweiter Abruf: nichts Neues, nichts doppelt, nichts erneut geladen.
    source.reads.clear()
    again = await _fetch(client, headers)
    assert again["imported"] == 0 and source.reads == []
    assert await _documents(client, headers) == ["brief.pdf", "rechnung.png"]

    # Hier gelöscht: kommt nicht wieder.
    doc = next(d for d in (await client.get(f"{API}/documents", headers=headers)).json() if d["original_filename"] == "brief.pdf")
    assert (await client.delete(f"{API}/documents/{doc['id']}", headers=headers)).status_code == 204
    assert (await _fetch(client, headers))["imported"] == 0
    assert await _documents(client, headers) == ["rechnung.png"]

    # In der Nextcloud ersetzt (neue Änderungszeit): kommt als neues Dokument dazu.
    source.put("/Scans/brief.pdf", _pdf() + b"neu", when=T2)
    assert (await _fetch(client, headers))["imported"] == 1
    assert await _documents(client, headers) == ["brief.pdf", "rechnung.png"]


@pytest.mark.asyncio
async def test_skipped_files_are_named_once_and_broken_reads_are_retried(client, db_session, test_settings):
    from nodvard_deck_ext_documents.storage import MAX_DOCUMENT_BYTES

    headers, source = await _setup(client, db_session, test_settings)
    source.put("/Scans/riesig.pdf", b"%PDF-" + b"0" * MAX_DOCUMENT_BYTES)
    source.put("/Scans/webseite.pdf", b"<html>Anmeldung</html>")
    source.put("/Scans/wackelt.png", _png_bytes("W"))
    source.broken.add("/Scans/wackelt.png")

    run = await _fetch(client, headers)
    assert run["imported"] == 0
    assert run["too_large"] == ["riesig.pdf"] and run["wrong_content"] == ["webseite.pdf"] and run["failed"] == ["wackelt.png"]
    assert "/Scans/riesig.pdf" not in source.reads, "die Größe stand schon in der Liste, nicht erst laden"

    source.broken.clear()
    again = await _fetch(client, headers)
    assert again["imported"] == 1 and again["too_large"] == [] and again["wrong_content"] == [] and again["failed"] == []
    assert await _documents(client, headers) == ["wackelt.png"]


@pytest.mark.asyncio
async def test_at_most_a_batch_per_run(client, db_session, test_settings):
    from nodvard_deck_ext_documents.inbox import MAX_FILES_PER_RUN

    headers, source = await _setup(client, db_session, test_settings)
    for i in range(MAX_FILES_PER_RUN + 2):
        source.put(f"/Scans/scan{i:02d}.pdf", _pdf() + str(i).encode())
    first = await _fetch(client, headers)
    assert first["imported"] == MAX_FILES_PER_RUN and first["more_waiting"] is True
    second = await _fetch(client, headers)
    assert second["imported"] == 2 and second["more_waiting"] is False


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_saved_does_not_stop_the_rest(client, db_session, test_settings, monkeypatch):
    import nodvard_deck_ext_documents as documents_module
    from nodvard_deck_ext_documents.models import InboxFile
    from sqlalchemy import select

    headers, source = await _setup(client, db_session, test_settings)
    source.put("/Scans/a-kaputt.pdf", _pdf() + b"KAPUTT", when=T1)
    source.put("/Scans/b-gut.pdf", _pdf(), when=T2)
    original = documents_module.save_document

    def _save(ext_data_dir, document_id, extension, content):
        if b"KAPUTT" in content:
            raise OSError("Platte voll")
        return original(ext_data_dir, document_id, extension, content)

    monkeypatch.setattr(documents_module, "save_document", _save)
    run = await _fetch(client, headers)
    assert run["ok"] is True and run["imported"] == 1 and run["failed"] == ["a-kaputt.pdf"]
    assert await _documents(client, headers) == ["b-gut.pdf"], "kein halbes Dokument von der kaputten Datei"
    remembered = {r.path for r in (await db_session.execute(select(InboxFile))).scalars()}
    assert remembered == {"/Scans/b-gut.pdf"}, "die kaputte kommt beim nächsten Lauf wieder dran"

    monkeypatch.setattr(documents_module, "save_document", original)
    assert (await _fetch(client, headers))["imported"] == 1
    assert await _documents(client, headers) == ["a-kaputt.pdf", "b-gut.pdf"]


@pytest.mark.asyncio
async def test_unreadable_files_do_not_hold_up_new_ones(client, db_session, test_settings):
    from nodvard_deck_ext_documents.inbox import MAX_FILES_PER_RUN

    headers, source = await _setup(client, db_session, test_settings)
    for i in range(3):
        source.put(f"/Scans/alt{i}.pdf", _pdf(), when=T1)
        source.broken.add(f"/Scans/alt{i}.pdf")
    for i in range(MAX_FILES_PER_RUN + 1):
        source.put(f"/Scans/neu{i:02d}.pdf", _pdf() + str(i).encode(), when=T2)
    run = await _fetch(client, headers)
    assert run["failed"] == ["alt0.pdf", "alt1.pdf", "alt2.pdf"]
    assert run["imported"] == MAX_FILES_PER_RUN and run["more_waiting"] is True


@pytest.mark.asyncio
async def test_runs_in_the_background_and_only_once_at_a_time(client, db_session, test_settings):
    headers, source = await _setup(client, db_session, test_settings)
    source.put("/Scans/brief.pdf", _pdf())
    source.gate = asyncio.Event()
    started = await client.post(f"{API}/inbox/fetch", headers=headers)
    assert started.status_code == 202 and started.json()["running"] is True
    second = await client.post(f"{API}/inbox/fetch", headers=headers)
    assert second.status_code == 409 and "läuft schon" in second.json()["detail"]
    source.gate.set()
    await _wait_for_runs()
    status = (await client.get(f"{API}/inbox", headers=headers)).json()
    assert status["running"] is False and status["last_run"]["imported"] == 1


@pytest.mark.asyncio
async def test_status_asks_the_nextcloud_rarely(client, db_session, test_settings, monkeypatch):
    """Der Status fragt die Nextcloud nur mit eingestelltem Ordner und höchstens einmal je Minute: die Seite fragt
    beim Warten auf einen Abruf alle 1,5 s nach, und jede Abfrage wäre ein Zugriff auf den Tresor."""
    import nodvard_deck_ext_documents as documents_module

    headers, _ = await _setup(client, db_session, test_settings, folder=None)
    provider = _use_nextcloud(_FakeSource())
    for _ in range(3):
        status = (await client.get(f"{API}/inbox", headers=headers)).json()
    assert status["nextcloud_ready"] is False and provider.calls == 0, "ohne Ordner kein Blick in die Nextcloud"

    await client.put("/api/v1/extensions/documents/settings", json={"values": {"nextcloud_folder": "/Scans"}}, headers=headers)
    for _ in range(5):
        status = (await client.get(f"{API}/inbox", headers=headers)).json()
    assert status["nextcloud_ready"] is True and provider.calls == 1

    monkeypatch.setattr(documents_module, "INBOX_READY_CACHE_S", 0.0)
    await client.get(f"{API}/inbox", headers=headers)
    assert provider.calls == 2, "nach Ablauf der Frist wird neu gefragt"


@pytest.mark.asyncio
async def test_without_folder_or_nextcloud(client, db_session, test_settings):
    headers, _ = await _setup(client, db_session, test_settings, folder=None)
    status = (await client.get(f"{API}/inbox", headers=headers)).json()
    assert status["folder"] is None and status["last_run"] is None
    res = await client.post(f"{API}/inbox/fetch", headers=headers)
    assert res.status_code == 409 and "Kein Nextcloud-Ordner" in res.json()["detail"]

    await client.put("/api/v1/extensions/documents/settings", json={"values": {"nextcloud_folder": "/a/../b"}}, headers=headers)
    status = (await client.get(f"{API}/inbox", headers=headers)).json()
    assert status["folder"] is None and "„..“" in status["folder_error"]
    assert (await client.post(f"{API}/inbox/fetch", headers=headers)).status_code == 409

    # Ordner eingestellt, aber Nextcloud nicht eingerichtet: ein Satz statt eines Fehlers.
    _use_nextcloud(None)
    await client.put("/api/v1/extensions/documents/settings", json={"values": {"nextcloud_folder": "/Scans"}}, headers=headers)
    assert (await client.get(f"{API}/inbox", headers=headers)).json()["nextcloud_ready"] is False
    run = await _fetch(client, headers)
    assert run["ok"] is False and "nicht eingerichtet" in run["message"]


@pytest.mark.asyncio
async def test_missing_folder_is_a_readable_message(client, db_session, test_settings):
    source = _FakeSource()
    source.missing_folder = True
    headers, _ = await _setup(client, db_session, test_settings, source=source)
    run = await _fetch(client, headers)
    assert run["ok"] is False
    assert run["message"] == "Der Ordner /Scans ließ sich nicht lesen. Den Ordner gibt es in der Nextcloud nicht."


def test_content_check_and_folder_rules():
    from nodvard_deck_ext_documents.inbox import (
        InboxError,
        content_matches,
        normalize_folder,
    )

    assert content_matches(b"%PDF-1.7", "application/pdf")
    assert content_matches(b"\xef\xbb\xbf\r\n%PDF-1.4", "application/pdf"), "Vorspann vor der Kennung ist erlaubt"
    assert not content_matches(b"<html>", "application/pdf")
    assert content_matches(b"\x89PNG\r\n\x1a\n...", "image/png")
    assert content_matches(b"\xff\xd8\xff\xe0", "image/jpeg")
    assert content_matches(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp")
    assert not content_matches(b"RIFF\x00\x00\x00\x00AVI ", "image/webp")

    assert normalize_folder("") is None and normalize_folder("  ") is None and normalize_folder(None) is None
    assert normalize_folder("/") == "/"
    assert normalize_folder("Scans\\Eingang//") == "/Scans/Eingang"
    with pytest.raises(InboxError):
        normalize_folder("/Scans/../etc")
