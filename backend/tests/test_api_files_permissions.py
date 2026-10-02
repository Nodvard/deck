"""Dateimanager: Quellen mit eigener Berechtigung (`required_permission`).

Die SSH-Quelle der terminal-Extension arbeitet mit den Zugangsdaten des Servers. Wer nur
`files.read` hat (Rolle "Nur ansehen"), darf sie deshalb nicht benutzen -- sie verlangt wie
Befehle auf Servern `hosts.execute`. Hier laeuft die ECHTE terminal-Quelle; nur der SFTP-Client
dahinter ist ein Spion, der festhaelt, ob ueberhaupt etwas geoeffnet wurde.
"""

from __future__ import annotations

import asyncio
import sys
import types
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_sdk import FileEntry, FileSourceCaps, Page, SourceInfo
from nodvard_sdk.capabilities import FileSourceProvider

TERMINAL_SRC = Path(__file__).resolve().parents[2] / "extensions" / "terminal" / "src"
PASSWORD = "whatever123"
SSH_SOURCE = "ssh-sftp:pve1"
SSH_FILES = {"/etc/shadow": b"root:geheim:19000::\n"}
BROKEN_PATH = "/tmp/kaputt"


class _Attrs:
    def __init__(self, size: int = 0, permissions: int = 0o100600) -> None:
        self.size = size
        self.type = 1
        self.permissions = permissions
        self.mtime = 0
        self.uid = 0
        self.gid = 0


class _SftpFile:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def __await__(self):
        # asyncssh: `open()` laesst sich awaiten und als Kontextmanager nutzen.
        async def _self():
            return self

        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def seek(self, offset: int) -> None:
        self._pos = offset

    async def read(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    async def write(self, chunk: bytes) -> None:
        self._data += chunk

    async def stat(self) -> _Attrs:
        return _Attrs(len(self._data))

    async def chown(self, uid: int, gid: int) -> None:
        return None

    async def chmod(self, mode: int) -> None:
        return None


class _Entry:
    def __init__(self, name: str, size: int) -> None:
        self.filename = name
        self.attrs = _Attrs(size)


class _SpySftp:
    """Spion: `touched` bekommt jeden Zugriff auf den Server."""

    def __init__(self, touched: list[str]) -> None:
        self.touched = touched

    async def stat(self, path: str) -> _Attrs:
        self.touched.append(f"stat {path}")
        return _Attrs(len(SSH_FILES.get(path, b"")))

    async def lstat(self, path: str) -> _Attrs:
        self.touched.append(f"lstat {path}")
        return _Attrs(0)

    async def readdir(self, path: str) -> list[_Entry]:
        self.touched.append(f"readdir {path}")
        return [_Entry("shadow", len(SSH_FILES["/etc/shadow"]))]

    async def realpath(self, path: str) -> str:
        return path

    async def posix_rename(self, src: str, dst: str) -> None:
        self.touched.append(f"rename {src} {dst}")

    def open(self, path: str, mode: str, attrs: _Attrs | None = None) -> _SftpFile:
        self.touched.append(f"open {path} {mode}")
        # Auch die Temp-Datei, ueber die das Ziel geschrieben wird (`.kaputt.nodvard-tmp-...`).
        if path == BROKEN_PATH or path.startswith("/tmp/.kaputt."):
            raise OSError("Verbindung abgebrochen")
        return _SftpFile(SSH_FILES.get(path, b"") if "r" in mode else b"")

    async def remove(self, path: str) -> None:
        self.touched.append(f"remove {path}")

    async def rename(self, src: str, dst: str) -> None:
        self.touched.append(f"rename {src} {dst}")

    async def mkdir(self, path: str) -> None:
        self.touched.append(f"mkdir {path}")


class _FakeExec:
    def __init__(self, touched: list[str]) -> None:
        self._touched = touched

    def sftp(self, host):
        @asynccontextmanager
        async def _cm():
            yield _SpySftp(self._touched)

        return _cm()


class _FakeHosts:
    async def list(self):
        return [types.SimpleNamespace(id="pve1", name="pve1", display_name="pve1", has_credential=True)]

    async def get(self, host_id: str):
        return types.SimpleNamespace(id=host_id)


class _OldSource:
    """Aeltere Quelle ohne `required_permission` -- muss unveraendert laufen."""

    source_id = "alt"
    label = "Alte Quelle"
    icon = "file"
    caps = FileSourceCaps(write=True, rename=True, remove=True, mkdir=True, search=True, range_read=True)

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {"/notes.txt": b"hallo welt"}

    async def stat(self, path: PurePosixPath) -> FileEntry:
        if str(path) not in self.files:
            raise FileNotFoundError(str(path))
        return FileEntry(
            name=path.name, path=str(path), is_dir=False, size=len(self.files[str(path)]),
            modified_at=datetime.now(timezone.utc), mime="text/plain",
        )

    async def list_dir(self, path: PurePosixPath, *, cursor: str | None = None) -> Page[FileEntry]:
        return Page(items=[
            FileEntry(name=PurePosixPath(p).name, path=p, is_dir=False, size=len(c), modified_at=None, mime=None)
            for p, c in self.files.items()
        ])

    async def open_read(self, path: PurePosixPath, *, offset: int = 0):
        yield self.files[str(path)][offset:]

    async def open_write(self, path: PurePosixPath, stream, *, size: int | None = None) -> FileEntry:
        self.files[str(path)] = b"".join([c async for c in stream])
        return FileEntry(name=path.name, path=str(path), is_dir=False, size=len(self.files[str(path)]), modified_at=None, mime=None)

    async def mkdir(self, path: PurePosixPath) -> FileEntry:
        return FileEntry(name=path.name, path=str(path), is_dir=True, size=None, modified_at=None, mime=None)

    async def remove(self, path: PurePosixPath, *, recursive: bool = False) -> None:
        self.files.pop(str(path), None)

    async def rename(self, src: PurePosixPath, dst: PurePosixPath) -> FileEntry:
        self.files[str(dst)] = self.files.pop(str(src))
        return FileEntry(name=dst.name, path=str(dst), is_dir=False, size=0, modified_at=None, mime=None)

    async def search(self, query: str, *, root: PurePosixPath):
        for p, c in self.files.items():
            if query in p:
                yield FileEntry(name=PurePosixPath(p).name, path=p, is_dir=False, size=len(c), modified_at=None, mime=None)

    async def info(self) -> SourceInfo:
        return SourceInfo(healthy=True)


class _Provider:
    def __init__(self, sources: list) -> None:
        self._sources = sources

    async def file_sources(self) -> list:
        return self._sources


@pytest.fixture(autouse=True)
def _clean_runtime():
    reset_extension_runtime()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_terminal"):
            del sys.modules[name]


@pytest.fixture
def touched() -> list[str]:
    return []


@pytest.fixture
def old_source() -> _OldSource:
    return _OldSource()


@pytest.fixture
def setup_sources(touched, old_source):
    """Registriert die echte terminal-Quelle (SFTP-Client gespiegelt) und eine alte Quelle."""
    sys.path.insert(0, str(TERMINAL_SRC))
    from nodvard_deck_ext_terminal import _TerminalFileSourceProvider

    ctx = types.SimpleNamespace(hosts=_FakeHosts(), exec=_FakeExec(touched))
    caps = get_extension_runtime().capabilities
    caps.provide("terminal", FileSourceProvider, _TerminalFileSourceProvider(ctx))
    caps.provide("alt-ext", FileSourceProvider, _Provider([old_source]))


async def _token(client, db_session, username: str, *, role: str | None = None, permissions: tuple[str, ...] = ()):
    from nodvard_deck.core import security
    from nodvard_deck.models import Role, RolePermission, User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password(PASSWORD), is_active=True)
    if role is not None:
        user.roles.append(roles[role])
    if permissions:
        custom = Role(name=f"rolle-{username}", permissions=[RolePermission(permission=p) for p in permissions])
        db_session.add(custom)
        user.roles.append(custom)
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _owner(client):
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _viewer(client, db_session):
    return await _token(client, db_session, "gast", role="viewer")


async def _files_only(client, db_session):
    """Darf Dateien lesen und schreiben, aber keine Befehle auf Servern ausfuehren."""
    return await _token(client, db_session, "ablage", permissions=("files.read", "files.write"))


async def _audit_actions(db_session) -> list:
    from nodvard_deck.services import audit as audit_service

    return await audit_service.list_entries(db_session, limit=100)


async def _user_id(db_session, username: str) -> str:
    from nodvard_deck.models import User
    from sqlalchemy import select

    return (await db_session.execute(select(User.id).where(User.username == username))).scalar_one()


async def _file_entries(db_session) -> dict:
    """Protokolleintraege der Dateiaktionen, nach Aktion (jede kommt in den Tests nur einmal vor)."""
    entries = [e for e in await _audit_actions(db_session) if e.action.startswith("files.")]
    by_action = {e.action: e for e in entries}
    assert len(by_action) == len(entries), [e.action for e in entries]
    return by_action


async def _wait_for_run(db_session, run_id: str):
    """Wartet, bis die Kopie im Hintergrund fertig ist (der Lauf ist nicht mehr `running`)."""
    from nodvard_deck.models import JobRun

    run = await db_session.get(JobRun, run_id)
    for _ in range(250):
        await db_session.refresh(run)
        if run.status != "running":
            return run
        await asyncio.sleep(0.02)
    raise AssertionError(f"Lauf {run_id} wurde nicht fertig")


async def _transfer(client, headers, source: str, path: str, target: str, target_path: str):
    return await client.post(
        "/api/v1/files/transfer",
        json={"from": {"source": source, "path": path}, "to": {"source": target, "path": target_path}},
        headers=headers,
    )


# --- Nutzer ohne hosts.execute: 403, und es wird nichts geoeffnet --------------------------------


@pytest.mark.asyncio
async def test_viewer_cannot_download_from_ssh_source(client, db_session, setup_sources, touched):
    headers = await _viewer(client, db_session)
    res = await client.get(f"/api/v1/files/{SSH_SOURCE}/download?path=/etc/shadow", headers=headers)
    assert res.status_code == 403, res.text
    assert "hosts.execute" in res.json()["detail"]
    assert b"geheim" not in res.content
    assert touched == []


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["info", "list?path=/etc", "stat?path=/etc/shadow"])
async def test_viewer_cannot_read_other_ssh_endpoints(client, db_session, setup_sources, touched, endpoint):
    headers = await _viewer(client, db_session)
    res = await client.get(f"/api/v1/files/{SSH_SOURCE}/{endpoint}", headers=headers)
    assert res.status_code == 403, res.text
    assert touched == []


@pytest.mark.asyncio
async def test_viewer_cannot_search_ssh_source(client, db_session, setup_sources, touched):
    headers = await _viewer(client, db_session)
    res = await client.get(f"/api/v1/files/search?q=shadow&sources={SSH_SOURCE}", headers=headers)
    assert res.status_code == 200
    assert res.json() == []
    assert touched == []
    # Ohne Quellenangabe: kein Treffer vom Server, die alte Quelle bleibt durchsuchbar.
    res = await client.get("/api/v1/files/search?q=notes", headers=headers)
    assert [h["source_id"] for h in res.json()] == ["alt"]
    assert touched == []


@pytest.mark.asyncio
async def test_viewer_does_not_see_ssh_sources_but_keeps_the_others(client, db_session, setup_sources):
    headers = await _viewer(client, db_session)
    res = await client.get("/api/v1/files/sources", headers=headers)
    assert res.status_code == 200
    ids = [s["source_id"] for s in res.json()]
    assert ids == ["alt"]


@pytest.mark.asyncio
async def test_files_only_user_cannot_use_ssh_source_for_any_action(client, db_session, setup_sources, touched):
    headers = await _files_only(client, db_session)

    checks = [
        await client.get(f"/api/v1/files/{SSH_SOURCE}/download?path=/etc/shadow", headers=headers),
        await client.post(f"/api/v1/files/{SSH_SOURCE}/upload?path=/tmp/x", content=b"x", headers=headers),
        await client.post(f"/api/v1/files/{SSH_SOURCE}/mkdir", json={"path": "/tmp/neu"}, headers=headers),
        await client.post(f"/api/v1/files/{SSH_SOURCE}/rename", json={"src": "/tmp/a", "dst": "/tmp/b"}, headers=headers),
        await client.post(f"/api/v1/files/{SSH_SOURCE}/remove", json={"path": "/tmp/a"}, headers=headers),
    ]
    assert [r.status_code for r in checks] == [403] * 5, [r.text for r in checks]
    assert touched == []
    sources = await client.get("/api/v1/files/sources", headers=headers)
    assert [s["source_id"] for s in sources.json()] == ["alt"]


@pytest.mark.asyncio
async def test_transfer_checks_both_sides(client, db_session, setup_sources, touched, old_source):
    headers = await _files_only(client, db_session)

    from_ssh = await client.post(
        "/api/v1/files/transfer",
        json={"from": {"source": SSH_SOURCE, "path": "/etc/shadow"}, "to": {"source": "alt", "path": "/kopie"}},
        headers=headers,
    )
    to_ssh = await client.post(
        "/api/v1/files/transfer",
        json={"from": {"source": "alt", "path": "/notes.txt"}, "to": {"source": SSH_SOURCE, "path": "/root/x"}},
        headers=headers,
    )
    assert from_ssh.status_code == 403, from_ssh.text
    assert to_ssh.status_code == 403, to_ssh.text
    assert touched == []
    assert "/kopie" not in old_source.files


# --- Nutzer mit hosts.execute duerfen weiter ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["owner", "operator", "admin"])
async def test_users_with_server_rights_keep_access(client, db_session, setup_sources, touched, old_source, who):
    """Wer Befehle auf Servern ausfuehren darf (Owner, Operator, Admin), kann die SSH-Quelle weiter
    ganz benutzen: lesen, schreiben, suchen und zwischen Quellen kopieren."""
    headers = await _owner(client) if who == "owner" else await _token(client, db_session, f"{who}1", role=who)
    base = f"/api/v1/files/{SSH_SOURCE}"

    sources = await client.get("/api/v1/files/sources", headers=headers)
    assert {s["source_id"] for s in sources.json()} == {SSH_SOURCE, "alt"}

    info = await client.get(f"{base}/info", headers=headers)
    assert info.status_code == 200, info.text
    stat = await client.get(f"{base}/stat?path=/etc/shadow", headers=headers)
    assert stat.status_code == 200, stat.text
    assert stat.json()["path"] == "/etc/shadow"
    listing = await client.get(f"{base}/list?path=/etc", headers=headers)
    assert listing.status_code == 200, listing.text

    res = await client.get(f"{base}/download?path=/etc/shadow", headers=headers)
    assert res.status_code == 200, res.text
    assert res.content == SSH_FILES["/etc/shadow"]

    found = await client.get(f"/api/v1/files/search?q=shadow&sources={SSH_SOURCE}", headers=headers)
    assert found.status_code == 200
    assert any(h["source_id"] == SSH_SOURCE for h in found.json())

    writes = [
        await client.post(f"{base}/upload?path=/tmp/x", content=b"neu", headers=headers),
        await client.post(f"{base}/mkdir", json={"path": "/tmp/neu"}, headers=headers),
        await client.post(f"{base}/rename", json={"src": "/tmp/a", "dst": "/tmp/b"}, headers=headers),
        await client.post(f"{base}/remove", json={"path": "/tmp/b"}, headers=headers),
    ]
    assert [r.status_code for r in writes] == [200] * 4, [r.text for r in writes]
    assert {"mkdir /tmp/neu", "rename /tmp/a /tmp/b", "remove /tmp/b"} <= set(touched)
    # Hochladen schreibt ueber eine Temp-Datei im Zielordner und ersetzt dann das Ziel.
    assert any(t.startswith("open /tmp/.x.nodvard-tmp-") for t in touched), touched
    assert any(t.startswith("rename /tmp/.x.nodvard-tmp-") and t.endswith(" /tmp/x") for t in touched), touched

    # Nacheinander: die Test-Datenbank teilt EINE Verbindung zwischen Anfrage und Hintergrundtask.
    # Schliesst der Task der ersten Kopie seine Sitzung, waehrend die zweite Anfrage ihren Lauf
    # anlegt, rollt das den noch nicht gespeicherten Lauf zurueck (im Betrieb hat jede Sitzung
    # ihre eigene Verbindung).
    for args in (("alt", "/notes.txt", SSH_SOURCE, "/tmp/ziel"), (SSH_SOURCE, "/etc/shadow", "alt", "/kopie")):
        res = await _transfer(client, headers, *args)
        assert res.status_code == 200, res.text
        assert (await _wait_for_run(db_session, res.json()["run_id"])).status == "succeeded"
    assert any(t.startswith("rename /tmp/.ziel.nodvard-tmp-") and t.endswith(" /tmp/ziel") for t in touched), touched
    assert old_source.files["/kopie"] == SSH_FILES["/etc/shadow"]


# --- Aeltere Quelle ohne Attribut -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_old_source_without_attribute_works_for_viewer(client, db_session, setup_sources, old_source):
    assert not hasattr(old_source, "required_permission")
    headers = await _viewer(client, db_session)

    listing = await client.get("/api/v1/files/alt/list?path=/", headers=headers)
    assert listing.status_code == 200
    download = await client.get("/api/v1/files/alt/download?path=/notes.txt", headers=headers)
    assert download.status_code == 200
    assert download.content == b"hallo welt"

    # Unprotokolliert wie bisher: kein Eintrag fuer Quellen ohne eigene Berechtigung.
    assert [e for e in await _audit_actions(db_session) if e.action.startswith("files.")] == []


@pytest.mark.asyncio
async def test_unreadable_required_permission_locks_the_source(client, db_session, old_source):
    old_source.required_permission = ""
    get_extension_runtime().capabilities.provide("alt-ext", FileSourceProvider, _Provider([old_source]))
    owner = await _owner(client)
    viewer = await _viewer(client, db_session)
    res = await client.get("/api/v1/files/alt/list?path=/", headers=viewer)
    assert res.status_code == 403

    res = await client.get("/api/v1/files/alt/list?path=/", headers=owner)
    assert res.status_code == 200


# --- Protokoll ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ssh_source_actions_are_written_to_the_audit_log(client, db_session, setup_sources):
    """Jede Dateiaktion eines Operators steht mit seiner Kennung im Protokoll -- auch Ordner anlegen,
    Suchen und Kopieren. Nur Quelle und Pfad, nie Inhalte oder Suchbegriff."""
    headers = await _token(client, db_session, "bedienung", role="operator")
    operator_id = await _user_id(db_session, "bedienung")
    base = f"/api/v1/files/{SSH_SOURCE}"

    await client.get(f"{base}/download?path=/etc/shadow", headers=headers)
    await client.post(f"{base}/upload?path=/tmp/x", content=b"inhalt-geheim", headers=headers)
    await client.post(f"{base}/mkdir", json={"path": "/tmp/neu"}, headers=headers)
    await client.post(f"{base}/rename", json={"src": "/tmp/a", "dst": "/tmp/b"}, headers=headers)
    await client.post(f"{base}/remove", json={"path": "/tmp/b"}, headers=headers)
    await client.get(f"/api/v1/files/search?q=suchwort-geheim&sources={SSH_SOURCE}", headers=headers)
    copy = await _transfer(client, headers, "alt", "/notes.txt", SSH_SOURCE, "/tmp/ziel")
    assert copy.status_code == 200, copy.text
    # Auf das Ende des Laufs warten (der Abschlusseintrag steht dann schon), damit der
    # Hintergrundtask nicht mehr in die Datenbank schreibt, waehrend der Test aufraeumt.
    assert (await _wait_for_run(db_session, copy.json()["run_id"])).status == "succeeded"

    entries = await _file_entries(db_session)
    assert set(entries) == {
        "files.download", "files.upload", "files.mkdir", "files.rename", "files.remove", "files.search",
        "files.transfer_start", "files.transfer",
    }
    for entry in entries.values():
        assert entry.actor_type == "user"
        assert entry.actor_id == operator_id
        assert entry.target_type == "file_source"
        assert entry.target_id == SSH_SOURCE
        assert entry.outcome == "success"
        assert "geheim" not in str(entry.detail)
        assert "hallo" not in str(entry.detail)
    assert entries["files.download"].detail == {"path": "/etc/shadow"}
    assert entries["files.upload"].detail == {"path": "/tmp/x"}
    assert entries["files.mkdir"].detail == {"path": "/tmp/neu"}
    assert entries["files.rename"].detail == {"from": "/tmp/a", "to": "/tmp/b"}
    assert entries["files.remove"].detail == {"path": "/tmp/b", "recursive": False}
    assert entries["files.search"].detail == {}


@pytest.mark.asyncio
async def test_search_is_logged_per_protected_source_without_the_query(client, db_session, setup_sources):
    """Die Suche liest Dateiinhalte auf dem Server: sie steht im Protokoll, der Suchbegriff nicht.
    Die alte Quelle ohne eigene Berechtigung bleibt unprotokolliert."""
    headers = await _owner(client)

    res = await client.get("/api/v1/files/search?q=passwort-suchwort", headers=headers)
    assert res.status_code == 200

    entries = [e for e in await _audit_actions(db_session) if e.action.startswith("files.")]
    assert [(e.action, e.outcome, e.target_type, e.target_id, e.detail) for e in entries] == [
        ("files.search", "success", "file_source", SSH_SOURCE, {})
    ]
    assert "suchwort" not in str(entries[0].detail)


@pytest.mark.asyncio
async def test_search_without_server_right_leaves_no_entry(client, db_session, setup_sources, touched):
    headers = await _viewer(client, db_session)
    res = await client.get(f"/api/v1/files/search?q=shadow&sources={SSH_SOURCE}", headers=headers)
    assert res.status_code == 200
    assert res.json() == []
    assert [e for e in await _audit_actions(db_session) if e.action.startswith("files.")] == []
    assert touched == []


@pytest.mark.asyncio
async def test_transfer_is_logged_at_start_and_at_the_end_with_the_run_id(client, db_session, setup_sources):
    headers = await _token(client, db_session, "bedienung", role="operator")
    operator_id = await _user_id(db_session, "bedienung")

    res = await _transfer(client, headers, SSH_SOURCE, "/etc/shadow", "alt", "/kopie")
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]
    assert (await _wait_for_run(db_session, run_id)).status == "succeeded"

    entries = await _file_entries(db_session)
    assert set(entries) == {"files.transfer_start", "files.transfer"}
    expected = {
        "run_id": run_id, "from_source": SSH_SOURCE, "from_path": "/etc/shadow", "to_source": "alt", "to_path": "/kopie",
    }
    assert entries["files.transfer_start"].detail == expected
    assert entries["files.transfer"].detail == {**expected, "bytes": len(SSH_FILES["/etc/shadow"])}
    for entry in entries.values():
        assert (entry.actor_id, entry.outcome, entry.target_id) == (operator_id, "success", SSH_SOURCE)
    assert "geheim" not in str([e.detail for e in entries.values()])


@pytest.mark.asyncio
async def test_failed_transfer_is_logged_as_failure(client, db_session, setup_sources):
    """Scheitert die Kopie im Hintergrund (das Ziel kann schon abgeschnitten sein), steht das im
    Protokoll -- nicht nur im Lauf."""
    headers = await _owner(client)

    res = await _transfer(client, headers, "alt", "/notes.txt", SSH_SOURCE, BROKEN_PATH)
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]
    assert (await _wait_for_run(db_session, run_id)).status == "failed"

    entries = await _file_entries(db_session)
    assert set(entries) == {"files.transfer_start", "files.transfer"}
    assert entries["files.transfer_start"].outcome == "success"
    result = entries["files.transfer"]
    assert result.outcome == "failure"
    assert result.target_id == SSH_SOURCE
    assert result.detail["run_id"] == run_id
    assert result.detail["to_path"] == BROKEN_PATH
    assert "hallo" not in str(result.detail)


@pytest.mark.asyncio
async def test_transfer_between_two_protected_sources_is_logged_once_per_source(client, db_session, setup_sources):
    """Beide Seiten sind geschuetzt: je Quelle ein Start- und ein Abschlusseintrag."""
    from nodvard_deck.ext.runtime import get_extension_runtime

    class _SecondSource(_OldSource):
        source_id = "zweit"
        required_permission = "hosts.execute"

    second = _SecondSource()
    second.files = {}
    get_extension_runtime().capabilities.provide("zweit-ext", FileSourceProvider, _Provider([second]))
    headers = await _owner(client)

    res = await _transfer(client, headers, SSH_SOURCE, "/etc/shadow", "zweit", "/kopie")
    assert res.status_code == 200, res.text
    assert (await _wait_for_run(db_session, res.json()["run_id"])).status == "succeeded"

    entries = [e for e in await _audit_actions(db_session) if e.action.startswith("files.")]
    assert sorted((e.action, e.target_id) for e in entries) == sorted([
        ("files.transfer_start", SSH_SOURCE), ("files.transfer_start", "zweit"),
        ("files.transfer", SSH_SOURCE), ("files.transfer", "zweit"),
    ])


@pytest.mark.asyncio
async def test_denied_access_is_logged_and_nothing_is_opened(client, db_session, setup_sources, touched):
    headers = await _viewer(client, db_session)
    res = await client.get(f"/api/v1/files/{SSH_SOURCE}/download?path=/etc/shadow", headers=headers)
    assert res.status_code == 403

    entries = [e for e in await _audit_actions(db_session) if e.action.startswith("files.")]
    assert [(e.action, e.outcome, e.target_id) for e in entries] == [("files.access", "denied", SSH_SOURCE)]
    assert touched == []


@pytest.mark.asyncio
async def test_failed_write_on_ssh_source_is_still_logged(client, db_session, setup_sources):
    """Ein abgebrochener Upload kann die Zieldatei schon geleert haben -- er gehoert ins Protokoll."""
    headers = await _owner(client)
    res = await client.post(f"/api/v1/files/{SSH_SOURCE}/upload?path={BROKEN_PATH}", content=b"inhalt-geheim", headers=headers)
    assert res.status_code == 502, res.text

    entries = [e for e in await _audit_actions(db_session) if e.action.startswith("files.")]
    assert [(e.action, e.outcome, e.target_id) for e in entries] == [("files.upload", "failure", SSH_SOURCE)]
    assert entries[0].detail == {"path": BROKEN_PATH, "status": 502}
