"""Kern-Dateimanager -- `api/v1/files.py` (docs/04-API.md Paragraph 3).
Gegen eine FAKE `FileSource`/`FileSourceProvider`
getestet (kein SSH/WebDAV noetig) -- die echten Quellen sind in
`test_ext_terminal.py`/`test_ext_nextcloud.py` gegen echte Server bewiesen; hier geht
es um den generischen Fan-out/Fehler-Uebersetzung/Transfer-Code selbst, der ALLEN
Quellen gemeinsam ist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath

import pytest
from nodvard_sdk import FileEntry, FileSourceCaps, Page, SourceInfo
from nodvard_sdk.capabilities import FileSourceProvider

from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime


class _FakeFileSource:
    def __init__(self, source_id: str = "fake", *, caps: FileSourceCaps | None = None) -> None:
        self.source_id = source_id
        self.label = "Fake-Quelle"
        self.icon = "file"
        self.caps = caps or FileSourceCaps(write=True, rename=True, remove=True, mkdir=True, search=True, range_read=True)
        self.files: dict[str, bytes] = {"/notes.txt": b"hallo welt"}
        self.calls: list[tuple] = []

    async def stat(self, path: PurePosixPath) -> FileEntry:
        p = str(path)
        if p not in self.files:
            raise FileNotFoundError(p)
        return FileEntry(name=path.name, path=p, is_dir=False, size=len(self.files[p]), modified_at=datetime.now(timezone.utc), mime="text/plain")

    async def list_dir(self, path: PurePosixPath, *, cursor: str | None = None) -> Page[FileEntry]:
        items = [
            FileEntry(name=PurePosixPath(p).name, path=p, is_dir=False, size=len(c), modified_at=None, mime="text/plain")
            for p, c in self.files.items()
        ]
        return Page(items=items)

    async def open_read(self, path: PurePosixPath, *, offset: int = 0):
        content = self.files[str(path)]
        yield content[offset:]

    async def open_write(self, path: PurePosixPath, stream, *, size: int | None = None) -> FileEntry:
        chunks = [chunk async for chunk in stream]
        data = b"".join(chunks)
        self.files[str(path)] = data
        self.calls.append(("write", str(path), data))
        return FileEntry(name=path.name, path=str(path), is_dir=False, size=len(data), modified_at=None, mime=None)

    async def mkdir(self, path: PurePosixPath) -> FileEntry:
        self.calls.append(("mkdir", str(path)))
        return FileEntry(name=path.name, path=str(path), is_dir=True, size=None, modified_at=None, mime=None)

    async def remove(self, path: PurePosixPath, *, recursive: bool = False) -> None:
        self.files.pop(str(path), None)
        self.calls.append(("remove", str(path), recursive))

    async def rename(self, src: PurePosixPath, dst: PurePosixPath) -> FileEntry:
        self.files[str(dst)] = self.files.pop(str(src))
        self.calls.append(("rename", str(src), str(dst)))
        return FileEntry(name=dst.name, path=str(dst), is_dir=False, size=len(self.files[str(dst)]), modified_at=None, mime=None)

    async def search(self, query: str, *, root: PurePosixPath):
        for p, c in self.files.items():
            if query in p:
                yield FileEntry(name=PurePosixPath(p).name, path=p, is_dir=False, size=len(c), modified_at=None, mime=None)

    async def info(self) -> SourceInfo:
        return SourceInfo(healthy=True)


class _BrokenFileSource(_FakeFileSource):
    async def stat(self, path: PurePosixPath) -> FileEntry:
        raise ConnectionError("Server nicht erreichbar")


class _FakeProvider:
    def __init__(self, sources: list) -> None:
        self._sources = sources

    async def file_sources(self) -> list:
        return self._sources


def _register(*sources) -> None:
    get_extension_runtime().capabilities.provide("fake-ext", FileSourceProvider, _FakeProvider(list(sources)))


@pytest.fixture(autouse=True)
def _reset_runtime():
    reset_extension_runtime()
    yield
    reset_extension_runtime()


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_sources_aggregates_across_providers(client, db_session):
    _register(_FakeFileSource("a"), _FakeFileSource("b"))
    token = await _bootstrap_owner(client)

    res = await client.get("/api/v1/files/sources", headers=_auth_header(token))
    assert res.status_code == 200, res.text
    assert {s["source_id"] for s in res.json()} == {"a", "b"}


@pytest.mark.asyncio
async def test_list_stat_download_roundtrip(client, db_session):
    _register(_FakeFileSource("fake"))
    token = await _bootstrap_owner(client)
    headers = _auth_header(token)

    listing = await client.get("/api/v1/files/fake/list?path=/", headers=headers)
    assert listing.status_code == 200
    assert {i["name"] for i in listing.json()["items"]} == {"notes.txt"}

    stat = await client.get("/api/v1/files/fake/stat?path=/notes.txt", headers=headers)
    assert stat.status_code == 200
    assert stat.json()["size"] == len(b"hallo welt")

    download = await client.get("/api/v1/files/fake/download?path=/notes.txt", headers=headers)
    assert download.status_code == 200
    assert download.content == b"hallo welt"


@pytest.mark.asyncio
async def test_download_honors_range_header_when_source_supports_it(client, db_session):
    source = _FakeFileSource("fake")
    _register(source)
    token = await _bootstrap_owner(client)

    res = await client.get(
        "/api/v1/files/fake/download?path=/notes.txt", headers={**_auth_header(token), "Range": "bytes=6-"}
    )
    assert res.status_code == 206, res.text
    assert res.content == b"welt"
    assert res.headers["content-range"] == "bytes 6-9/10"


@pytest.mark.asyncio
async def test_unknown_source_returns_404(client, db_session):
    _register(_FakeFileSource("fake"))
    token = await _bootstrap_owner(client)

    res = await client.get("/api/v1/files/does-not-exist/list?path=/", headers=_auth_header(token))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_source_info_returns_the_sources_health(client, db_session):
    """`FileSource.info()` war
    Vertragspflicht, aber nie ueber die API erreichbar -- Quota/Health-Panel
    blieb totes Gepaeck."""
    _register(_FakeFileSource("fake"))
    token = await _bootstrap_owner(client)

    res = await client.get("/api/v1/files/fake/info", headers=_auth_header(token))
    assert res.status_code == 200, res.text
    assert res.json()["healthy"] is True


@pytest.mark.asyncio
async def test_source_info_for_a_broken_source_reports_unhealthy_not_a_502(client, db_session):
    """Anders als stat()/list_dir(): eine kaputte Quelle soll HIER als sichtbares
    `healthy=False` zurueckkommen (das Info-Panel IST der Fehlerindikator), nicht
    als generischer 502 -- deshalb eine eigene FileSource, deren info() selbst
    einen SourceInfo mit healthy=False liefert, statt zu werfen."""

    class _UnhealthySource(_FakeFileSource):
        async def info(self) -> SourceInfo:
            return SourceInfo(healthy=False, message="Verbindung abgelehnt")

    _register(_UnhealthySource("flaky"))
    token = await _bootstrap_owner(client)

    res = await client.get("/api/v1/files/flaky/info", headers=_auth_header(token))
    assert res.status_code == 200, res.text
    assert res.json()["healthy"] is False
    assert res.json()["message"] == "Verbindung abgelehnt"


@pytest.mark.asyncio
async def test_source_info_for_unknown_source_returns_404(client, db_session):
    token = await _bootstrap_owner(client)
    res = await client.get("/api/v1/files/does-not-exist/info", headers=_auth_header(token))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_source_exception_is_translated_to_a_clean_502(client, db_session):
    """`_call()` normalisiert JEDEN Fehler einer Quelle -- eine rohe
    `ConnectionError` (wie sie SFTP/WebDAV realistisch werfen koennten) darf nie als
    500 mit Stacktrace beim Client ankommen."""
    _register(_BrokenFileSource("broken"))
    token = await _bootstrap_owner(client)

    res = await client.get("/api/v1/files/broken/stat?path=/notes.txt", headers=_auth_header(token))
    assert res.status_code == 502
    assert "nicht erreichbar" in res.json()["detail"]


@pytest.mark.asyncio
async def test_upload_mkdir_rename_remove_roundtrip(client, db_session):
    source = _FakeFileSource("fake")
    _register(source)
    token = await _bootstrap_owner(client)
    headers = _auth_header(token)

    uploaded = await client.post("/api/v1/files/fake/upload?path=/new.txt", headers=headers, content=b"neu")
    assert uploaded.status_code == 200, uploaded.text
    assert source.files["/new.txt"] == b"neu"

    made = await client.post("/api/v1/files/fake/mkdir", json={"path": "/sub"}, headers=headers)
    assert made.status_code == 200, made.text
    assert ("mkdir", "/sub") in source.calls

    renamed = await client.post("/api/v1/files/fake/rename", json={"src": "/new.txt", "dst": "/renamed.txt"}, headers=headers)
    assert renamed.status_code == 200, renamed.text
    assert "/renamed.txt" in source.files

    removed = await client.post("/api/v1/files/fake/remove", json={"path": "/renamed.txt"}, headers=headers)
    assert removed.status_code == 200, removed.text
    assert "/renamed.txt" not in source.files


@pytest.mark.asyncio
async def test_write_op_rejected_when_source_lacks_the_capability(client, db_session):
    read_only = _FakeFileSource("ro", caps=FileSourceCaps(write=False))
    _register(read_only)
    token = await _bootstrap_owner(client)

    res = await client.post("/api/v1/files/ro/upload?path=/x.txt", headers=_auth_header(token), content=b"x")
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_search_fans_out_and_skips_sources_without_search_cap(client, db_session):
    searchable = _FakeFileSource("a")
    searchable.files = {"/report.txt": b"x", "/other.txt": b"y"}
    not_searchable = _FakeFileSource("b", caps=FileSourceCaps(search=False))
    not_searchable.files = {"/report.txt": b"z"}
    _register(searchable, not_searchable)
    token = await _bootstrap_owner(client)

    res = await client.get("/api/v1/files/search?q=report", headers=_auth_header(token))
    assert res.status_code == 200, res.text
    hits = res.json()
    assert {h["source_id"] for h in hits} == {"a"}
    assert hits[0]["entry"]["path"] == "/report.txt"


@pytest.mark.asyncio
async def test_transfer_streams_between_two_distinct_sources_and_creates_a_job_run(client, db_session):
    import asyncio

    src = _FakeFileSource("src")
    dst = _FakeFileSource("dst")
    dst.files = {}
    _register(src, dst)
    token = await _bootstrap_owner(client)

    res = await client.post(
        "/api/v1/files/transfer",
        json={"from": {"source": "src", "path": "/notes.txt"}, "to": {"source": "dst", "path": "/copy.txt"}},
        headers=_auth_header(token),
    )
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]

    for _ in range(100):
        if "/copy.txt" in dst.files:
            break
        await asyncio.sleep(0.02)
    assert dst.files["/copy.txt"] == b"hallo welt"

    from nodvard_deck.models import JobRun

    run = await db_session.get(JobRun, run_id)
    for _ in range(100):
        await db_session.refresh(run)
        if run.status != "running":
            break
        await asyncio.sleep(0.02)
    assert run.status == "succeeded"
    assert run.job_id is None
    assert run.actor["id"] is not None


@pytest.mark.asyncio
async def test_transfer_marks_run_as_failed_when_the_target_write_raises(client, db_session):
    import asyncio

    class _BrokenWriteSource(_FakeFileSource):
        async def open_write(self, path, stream, *, size=None):
            raise ConnectionError("Ziel abgelehnt")

    src = _FakeFileSource("src")
    dst = _BrokenWriteSource("dst")
    _register(src, dst)
    token = await _bootstrap_owner(client)

    res = await client.post(
        "/api/v1/files/transfer",
        json={"from": {"source": "src", "path": "/notes.txt"}, "to": {"source": "dst", "path": "/copy.txt"}},
        headers=_auth_header(token),
    )
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]

    from nodvard_deck.models import JobRun

    run = await db_session.get(JobRun, run_id)
    for _ in range(100):
        await db_session.refresh(run)
        if run.status != "running":
            break
        await asyncio.sleep(0.02)
    assert run.status == "failed"
    assert "Ziel abgelehnt" in (run.error or "")


@pytest.mark.asyncio
async def test_files_endpoints_require_authentication(client, db_session):
    _register(_FakeFileSource("fake"))
    unauthenticated = await client.get("/api/v1/files/sources")
    assert unauthenticated.status_code == 401
