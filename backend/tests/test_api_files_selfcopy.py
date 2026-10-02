"""Dateimanager: eine Datei auf sich selbst kopieren oder verschieben darf sie nie leeren.

Hier laeuft die ECHTE terminal-Quelle gegen einen echten lokalen SFTP-Server (Fixture
`local_ssh_server`). Frueher schnitt `open_write` das Ziel sofort auf 0 Byte ab, noch bevor die
Quelle gelesen wurde -- bei einem Link oder zwei Eintraegen fuer denselben Rechner war die Datei
danach leer, und "Verschieben" loeschte zusaetzlich das Original.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path, PurePosixPath

import pytest
from nodvard_deck.api.v1.files import _is_same_file
from nodvard_deck.ext.context import build_context
from nodvard_deck.ext.runtime import (
    ExtensionRuntime,
    LoadedExtension,
    get_extension_runtime,
    reset_extension_runtime,
)
from nodvard_deck.models import JobRun
from nodvard_deck.services import hosts as hosts_service
from nodvard_sdk import ExtensionManifest
from nodvard_sdk.capabilities import FileSourceProvider

TERMINAL_SRC = Path(__file__).resolve().parents[2] / "extensions" / "terminal" / "src"
CONTENT = b"wichtiger Inhalt " * 6000  # ~100 KB, mehrere Pakete


@pytest.fixture(autouse=True)
def _clean():
    reset_extension_runtime()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_terminal"):
            del sys.modules[name]


async def _terminal_ctx(tmp_path, settings):
    sys.path.insert(0, str(TERMINAL_SRC))
    import nodvard_deck_ext_terminal

    perms = ["hosts.read", "hosts.execute"]
    manifest = ExtensionManifest(
        id="terminal", name="Terminal", version="0.1.0", api_version="0.1",
        entrypoint="nodvard_deck_ext_terminal:Extension", permissions=perms,
    )
    loaded = LoadedExtension(manifest=manifest, instance=None, ctx=None, granted_permissions=perms)
    ctx = build_context(ExtensionRuntime(), loaded, manifest, perms, tmp_path / "data", settings)
    loaded.ctx = ctx
    extension = nodvard_deck_ext_terminal.Extension()
    await extension.setup(ctx)
    return extension


async def _make_host(db_session, settings, server, name):
    address, port, username, password, _root = server
    host = await hosts_service.create_host(db_session, name=name, address=address)
    await hosts_service.add_credential(
        db_session, settings, host_id=host.id, kind="ssh_password", username=username, port=port, secret_value=password
    )
    await db_session.commit()
    return host


async def _sources(db_session, settings, server, tmp_path, count=1):
    extension = await _terminal_ctx(tmp_path, settings)
    return [extension.file_source_for((await _make_host(db_session, settings, server, f"pve{i}")).id) for i in range(count)]


def _stream(*chunks: bytes, then: Exception | None = None):
    async def gen():
        for chunk in chunks:
            yield chunk
        if then is not None:
            raise then

    return gen()


def _temp_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if "nodvard-tmp" in p.name or "nodvard-old" in p.name]


# --- open_write: erst fertig schreiben, dann ersetzen -----------------------------------------------


@pytest.mark.asyncio
async def test_open_write_replaces_the_target_and_leaves_no_temp_file(local_ssh_server, db_session, tmp_path, test_settings):
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "a.txt").write_bytes(b"alt")

    entry = await source.open_write(PurePosixPath("a.txt"), _stream(b"neu-", b"inhalt"))
    assert (root / "a.txt").read_bytes() == b"neu-inhalt"
    assert entry.size == len(b"neu-inhalt")

    await source.open_write(PurePosixPath("frisch.txt"), _stream(b"x"))
    assert (root / "frisch.txt").read_bytes() == b"x"
    assert _temp_files(root) == []


@pytest.mark.asyncio
async def test_failed_read_keeps_the_target_and_removes_the_temp_file(local_ssh_server, db_session, tmp_path, test_settings):
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "ziel.txt").write_bytes(b"bleibt")

    with pytest.raises(ConnectionError):
        await source.open_write(PurePosixPath("ziel.txt"), _stream(b"halb", then=ConnectionError("abgebrochen")))

    assert (root / "ziel.txt").read_bytes() == b"bleibt"
    assert _temp_files(root) == []


@pytest.mark.asyncio
async def test_cancelled_write_removes_the_temp_file(local_ssh_server, db_session, tmp_path, test_settings):
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "ziel.txt").write_bytes(b"bleibt")

    async def never_ending():
        yield b"anfang"
        await asyncio.sleep(60)
        yield b"nie"

    task = asyncio.ensure_future(source.open_write(PurePosixPath("ziel.txt"), never_ending()))
    for _ in range(100):
        if _temp_files(root):
            break
        await asyncio.sleep(0.02)
    assert _temp_files(root)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (root / "ziel.txt").read_bytes() == b"bleibt"
    assert _temp_files(root) == []


@pytest.mark.asyncio
async def test_open_write_keeps_the_permissions_of_the_existing_file(local_ssh_server, db_session, tmp_path, test_settings):
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "geheim.conf").write_bytes(b"alt")
    os.chmod(root / "geheim.conf", 0o640)

    await source.open_write(PurePosixPath("geheim.conf"), _stream(b"neu"))

    assert (root / "geheim.conf").read_bytes() == b"neu"
    assert stat.S_IMODE((root / "geheim.conf").stat().st_mode) == 0o640


@pytest.mark.asyncio
async def test_copying_a_file_onto_itself_through_a_link_keeps_the_content(local_ssh_server, db_session, tmp_path, test_settings):
    """Der Fall von frueher: `alias -> .`, gelesen wird `wichtig.txt`, geschrieben `alias/wichtig.txt`."""
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "wichtig.txt").write_bytes(CONTENT)
    (root / "alias").symlink_to(".")

    await source.open_write(PurePosixPath("alias/wichtig.txt"), source.open_read(PurePosixPath("wichtig.txt")))

    assert (root / "wichtig.txt").read_bytes() == CONTENT
    assert _temp_files(root) == []


@pytest.mark.asyncio
async def test_writing_to_a_file_link_updates_the_real_file_and_keeps_the_link(local_ssh_server, db_session, tmp_path, test_settings):
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "echt.txt").write_bytes(b"alt")
    (root / "kurz.txt").symlink_to("echt.txt")

    await source.open_write(PurePosixPath("kurz.txt"), _stream(b"neu"))

    assert (root / "kurz.txt").is_symlink()
    assert (root / "echt.txt").read_bytes() == b"neu"


@pytest.mark.asyncio
async def test_open_write_without_posix_rename_still_replaces_and_can_roll_back(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    import asyncssh

    async def unsupported(self, old, new):  # noqa: ANN001
        raise asyncssh.SFTPOpUnsupported("posix-rename nicht unterstuetzt")

    monkeypatch.setattr(asyncssh.SFTPClient, "posix_rename", unsupported)
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "a.txt").write_bytes(b"alt")

    await source.open_write(PurePosixPath("a.txt"), _stream(b"neu"))
    assert (root / "a.txt").read_bytes() == b"neu"
    assert _temp_files(root) == []

    # Scheitert das Einsetzen der neuen Datei, kommt die alte zurueck.
    real_rename = asyncssh.SFTPClient.rename

    async def flaky(self, old, new):  # noqa: ANN001
        if ".nodvard-tmp-" in str(old):
            raise asyncssh.SFTPFailure("Platte voll")
        return await real_rename(self, old, new)

    monkeypatch.setattr(asyncssh.SFTPClient, "rename", flaky)
    with pytest.raises(asyncssh.SFTPFailure):
        await source.open_write(PurePosixPath("a.txt"), _stream(b"anders"))
    assert (root / "a.txt").read_bytes() == b"neu"
    assert _temp_files(root) == []


@pytest.mark.asyncio
async def test_copying_attributes_never_follows_a_link_swapped_in_for_the_temp_file(
    local_ssh_server, db_session, tmp_path, test_settings
):
    """Wer im Zielordner schreiben darf, tauscht die Temp-Datei waehrend des Schreibens gegen einen
    Link auf eine fremde Datei. Rechte und Besitzer gehen trotzdem nur an die eigene neue Datei
    (ueber den offenen Dateigriff), nie an das Linkziel -- als root waere das sonst ein Weg, fremde
    Dateien zu uebernehmen."""
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    victim = tmp_path / "fremd.txt"
    victim.write_bytes(b"fremd")
    os.chmod(victim, 0o600)
    (root / "ziel.conf").write_bytes(b"alt")
    os.chmod(root / "ziel.conf", 0o646)

    async def swapping():
        yield b"neu"
        (temp,) = _temp_files(root)
        temp.unlink()
        temp.symlink_to(victim)
        yield b"-rest"

    await source.open_write(PurePosixPath("ziel.conf"), swapping())

    assert stat.S_IMODE(victim.stat().st_mode) == 0o600
    assert victim.read_bytes() == b"fremd"


@pytest.mark.asyncio
async def test_temp_file_for_an_existing_target_is_private_while_it_is_written(
    local_ssh_server, db_session, tmp_path, test_settings
):
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "geheim.key").write_bytes(b"alt")
    os.chmod(root / "geheim.key", 0o640)
    seen: list[int] = []

    async def watching():
        yield b"neu"
        seen.extend(stat.S_IMODE(p.stat().st_mode) for p in _temp_files(root))
        yield b"!"

    await source.open_write(PurePosixPath("geheim.key"), watching())

    assert seen == [0o600]
    assert (root / "geheim.key").read_bytes() == b"neu!"
    assert stat.S_IMODE((root / "geheim.key").stat().st_mode) == 0o640


@pytest.mark.asyncio
async def test_a_pipe_is_written_into_and_never_swapped_for_a_regular_file(local_ssh_server, db_session, tmp_path, test_settings):
    """Pipe oder Geraet (etwa /dev/null): hineinschreiben wie frueher, nie gegen eine normale Datei
    austauschen. (Dieser Test-Server schreibt mit Versatz, bei einer Pipe scheitert das Schreiben
    darum wie bei OpenSSH -- die Pipe muss trotzdem bleiben.)"""
    import asyncssh

    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    pipe = root / "rohr"
    os.mkfifo(pipe)
    reader = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)  # sonst blockiert das Oeffnen zum Schreiben
    try:
        with pytest.raises(asyncssh.SFTPFailure):
            await source.open_write(PurePosixPath("rohr"), _stream(b"daten"))
    finally:
        os.close(reader)

    assert stat.S_ISFIFO(os.lstat(pipe).st_mode)
    assert _temp_files(root) == []


@pytest.mark.asyncio
async def test_without_write_access_to_the_folder_an_existing_file_is_written_in_place(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    """Darf der Zugang die Datei schreiben, aber im Ordner nichts anlegen (etwa eine
    gruppen-schreibbare Datei in /etc), klappt das Speichern weiter wie frueher. Scheitert die Quelle
    gleich zu Beginn, bleibt das Ziel trotzdem unberuehrt."""
    import asyncssh
    from asyncssh.misc import async_context_manager

    real_open = asyncssh.SFTPClient.open

    @async_context_manager
    async def no_new_files(self, path, *args, **kwargs):  # noqa: ANN001
        if "nodvard-tmp" in str(path):
            raise asyncssh.SFTPPermissionDenied("Ordner nicht beschreibbar")
        return await real_open(self, path, *args, **kwargs)

    monkeypatch.setattr(asyncssh.SFTPClient, "open", no_new_files)
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "app.conf").write_bytes(b"alt")

    with pytest.raises(ConnectionError):
        await source.open_write(PurePosixPath("app.conf"), _stream(then=ConnectionError("Quelle weg")))
    assert (root / "app.conf").read_bytes() == b"alt"

    await source.open_write(PurePosixPath("app.conf"), _stream(b"neu-", b"inhalt"))
    assert (root / "app.conf").read_bytes() == b"neu-inhalt"

    with pytest.raises(asyncssh.SFTPPermissionDenied):
        await source.open_write(PurePosixPath("neu.conf"), _stream(b"x"))
    assert not (root / "neu.conf").exists()


@pytest.mark.asyncio
async def test_file_identity_resolves_links_and_ignores_missing_files(local_ssh_server, db_session, tmp_path, test_settings):
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "wichtig.txt").write_bytes(b"x")
    (root / "alias").symlink_to(".")

    direct = await source.file_identity(PurePosixPath("wichtig.txt"))
    linked = await source.file_identity(PurePosixPath("alias/wichtig.txt"))
    assert direct is not None and direct == linked
    assert direct["size"] == 1
    assert await source.file_identity(PurePosixPath("gibt-es-nicht")) is None


# --- Transfer ueber die API ----------------------------------------------------------------------------


async def _owner_headers(client):
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


class _Provider:
    def __init__(self, sources) -> None:
        self._sources = sources

    async def file_sources(self):
        return self._sources


async def _setup_api(client, db_session, tmp_path, test_settings, server, count):
    sources = await _sources(db_session, test_settings, server, tmp_path, count=count)
    get_extension_runtime().capabilities.provide("terminal", FileSourceProvider, _Provider(sources))
    return [s.source_id for s in sources], await _owner_headers(client)


async def _finished(db_session, run_id: str) -> JobRun:
    run = await db_session.get(JobRun, run_id)
    for _ in range(200):
        await db_session.refresh(run)
        if run.status != "running":
            break
        await asyncio.sleep(0.02)
    return run


def _transfer(client, headers, source_from, path_from, source_to, path_to):
    return client.post(
        "/api/v1/files/transfer",
        json={"from": {"source": source_from, "path": path_from}, "to": {"source": source_to, "path": path_to}},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_transfer_onto_itself_through_a_link_is_refused(client, db_session, tmp_path, test_settings, local_ssh_server):
    (sid,), headers = await _setup_api(client, db_session, tmp_path, test_settings, local_ssh_server, 1)
    root = local_ssh_server[4]
    (root / "wichtig.txt").write_bytes(CONTENT)
    (root / "alias").symlink_to(".")
    (root / "ordner").mkdir()

    for target in ("/alias/wichtig.txt", "/ordner/../wichtig.txt", "/wichtig.txt", "/wichtig.txt/"):
        res = await _transfer(client, headers, sid, "/wichtig.txt", sid, target)
        assert res.status_code == 409, (target, res.text)
        assert res.json()["detail"] == "Quelle und Ziel sind dieselbe Datei."

    assert (root / "wichtig.txt").read_bytes() == CONTENT
    assert _temp_files(root) == []


@pytest.mark.asyncio
async def test_transfer_between_two_entries_for_the_same_machine_is_refused(
    client, db_session, tmp_path, test_settings, local_ssh_server
):
    (first, second), headers = await _setup_api(client, db_session, tmp_path, test_settings, local_ssh_server, 2)
    root = local_ssh_server[4]
    (root / "wichtig.txt").write_bytes(CONTENT)

    res = await _transfer(client, headers, first, "/wichtig.txt", second, "/wichtig.txt")

    assert res.status_code == 409, res.text
    assert res.json()["detail"] == "Quelle und Ziel sind dieselbe Datei."
    assert (root / "wichtig.txt").read_bytes() == CONTENT


@pytest.mark.asyncio
async def test_normal_copies_still_work_and_replace_an_existing_target(client, db_session, tmp_path, test_settings, local_ssh_server):
    (first, second), headers = await _setup_api(client, db_session, tmp_path, test_settings, local_ssh_server, 2)
    root = local_ssh_server[4]
    (root / "quelle.txt").write_bytes(CONTENT)
    (root / "ziel.txt").write_bytes(b"alt")

    same = await _transfer(client, headers, first, "/quelle.txt", first, "/kopie.txt")
    assert same.status_code == 200, same.text
    assert (await _finished(db_session, same.json()["run_id"])).status == "succeeded"
    assert (root / "kopie.txt").read_bytes() == CONTENT

    across = await _transfer(client, headers, first, "/quelle.txt", second, "/ziel.txt")
    assert across.status_code == 200, across.text
    assert (await _finished(db_session, across.json()["run_id"])).status == "succeeded"
    assert (root / "ziel.txt").read_bytes() == CONTENT
    assert _temp_files(root) == []


@pytest.mark.asyncio
async def test_transfer_with_unreadable_source_leaves_the_target_untouched(client, db_session, tmp_path, test_settings, local_ssh_server):
    (sid,), headers = await _setup_api(client, db_session, tmp_path, test_settings, local_ssh_server, 1)
    root = local_ssh_server[4]
    (root / "ziel.txt").write_bytes(b"bleibt")

    res = await _transfer(client, headers, sid, "/gibt-es-nicht.txt", sid, "/ziel.txt")
    assert res.status_code == 200, res.text
    run = await _finished(db_session, res.json()["run_id"])

    assert run.status == "failed"
    assert (root / "ziel.txt").read_bytes() == b"bleibt"
    assert _temp_files(root) == []


# --- Gleichheit ueber zwei Quellen: nur bei echter Uebereinstimmung ---------------------------------


class _IdentitySource:
    def __init__(self, source_id: str, identity) -> None:
        self.source_id = source_id
        self._identity = identity

    async def file_identity(self, path):  # noqa: ANN001
        if isinstance(self._identity, Exception):
            raise self._identity
        return self._identity


class _PlainSource:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


def _identity(**overrides):
    base = {"path": "/etc/hosts", "size": 10, "mtime": 5, "uid": 0, "gid": 0, "mode": 0o100644}
    return {**base, **overrides}


@pytest.mark.asyncio
async def test_same_file_across_sources_needs_identical_metadata():
    p = PurePosixPath("/etc/hosts")
    a = _IdentitySource("a", _identity())
    assert await _is_same_file(a, p, _IdentitySource("b", _identity()), p) is True
    # anderer Rechner mit gleichem Pfad, aber anderer Datei: durchlassen
    assert await _is_same_file(a, p, _IdentitySource("b", _identity(size=11)), p) is False
    assert await _is_same_file(a, p, _IdentitySource("b", _identity(mtime=6)), p) is False
    assert await _is_same_file(a, p, _IdentitySource("b", _identity(mtime=None)), p) is False
    assert await _is_same_file(_IdentitySource("a", _identity(mtime=None)), p, _IdentitySource("b", _identity(mtime=None)), p) is False
    # im Zweifel durchlassen: keine Angabe, Fehler, andere Datei
    assert await _is_same_file(a, p, _PlainSource("b"), p) is False
    assert await _is_same_file(a, p, _IdentitySource("b", RuntimeError("weg")), p) is False
    assert await _is_same_file(a, p, _IdentitySource("b", None), p) is False
    assert await _is_same_file(a, p, _IdentitySource("b", _identity(path="/etc/anders")), p) is False


@pytest.mark.asyncio
async def test_same_source_is_decided_by_the_resolved_path_alone():
    p, q = PurePosixPath("/a"), PurePosixPath("/b")
    same = _IdentitySource("a", _identity(path="/real"))
    assert await _is_same_file(same, p, same, q) is True
    assert await _is_same_file(same, p, same, p) is True
    assert await _is_same_file(_PlainSource("a"), p, _PlainSource("a"), q) is False
    assert await _is_same_file(_PlainSource("a"), p, _PlainSource("a"), p) is True


@pytest.mark.asyncio
async def test_two_machines_with_a_synced_file_are_not_the_same_file():
    """Per `rsync -a` gleich gehaltene Konfiguration: gleicher Pfad, gleiche Groesse, Zeit, Besitzer
    und Rechte -- aber zwei Rechner. Kennen beide Seiten ihren Rechner, ist das kein "dieselbe Datei"."""
    p = PurePosixPath("/etc/hosts")
    first = _IdentitySource("a", _identity(machine="1" * 32))
    assert await _is_same_file(first, p, _IdentitySource("b", _identity(machine="2" * 32)), p) is False
    assert await _is_same_file(first, p, _IdentitySource("b", _identity(machine="1" * 32)), p) is True
    # derselbe Rechner unter zwei Eintraegen, aber verschiedene Dateien
    assert await _is_same_file(first, p, _IdentitySource("b", _identity(machine="1" * 32, path="/etc/anders")), p) is False
    # nur eine Seite kennt ihren Rechner: wie bisher ueber die Dateiwerte
    assert await _is_same_file(first, p, _IdentitySource("b", _identity()), p) is True
    assert await _is_same_file(first, p, _IdentitySource("b", _identity(size=11)), p) is False


@pytest.mark.asyncio
async def test_file_identity_names_the_machine_when_it_can(local_ssh_server, db_session, tmp_path, test_settings):
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "wichtig.txt").write_bytes(b"x")
    assert (await source.file_identity(PurePosixPath("wichtig.txt")))["machine"] is None

    (root / "etc").mkdir()
    (root / "etc" / "machine-id").write_text("0123456789abcdef0123456789abcdef\n")
    assert (await source.file_identity(PurePosixPath("wichtig.txt")))["machine"] == "0123456789abcdef0123456789abcdef"

    (root / "etc" / "machine-id").write_text("uninitialized\n")
    assert (await source.file_identity(PurePosixPath("wichtig.txt")))["machine"] is None


@pytest.mark.asyncio
async def test_transfer_between_two_entries_for_the_same_machine_is_refused_by_machine_id(
    client, db_session, tmp_path, test_settings, local_ssh_server
):
    (first, second), headers = await _setup_api(client, db_session, tmp_path, test_settings, local_ssh_server, 2)
    root = local_ssh_server[4]
    (root / "etc").mkdir()
    (root / "etc" / "machine-id").write_text("0123456789abcdef0123456789abcdef\n")
    (root / "wichtig.txt").write_bytes(CONTENT)

    res = await _transfer(client, headers, first, "/wichtig.txt", second, "/wichtig.txt")

    assert res.status_code == 409, res.text
    assert (root / "wichtig.txt").read_bytes() == CONTENT


# --- Rueckfall "direkt ins Ziel": erst die ganze Quelle lesen, Besitzer nicht verlieren -------------


def _deny_new_files_in_folder(monkeypatch) -> None:  # noqa: ANN001
    """Der Zugang darf die Datei schreiben, im Ordner aber nichts anlegen."""
    import asyncssh
    from asyncssh.misc import async_context_manager

    real_open = asyncssh.SFTPClient.open

    @async_context_manager
    async def no_new_files(self, path, *args, **kwargs):  # noqa: ANN001
        if "nodvard-tmp" in str(path):
            raise asyncssh.SFTPPermissionDenied("Ordner nicht beschreibbar")
        return await real_open(self, path, *args, **kwargs)

    monkeypatch.setattr(asyncssh.SFTPClient, "open", no_new_files)


def _pretend_other_owner(monkeypatch, name: str, *, field: str = "uid") -> None:  # noqa: ANN001
    """Die Datei `name` gehoert (laut Server) einem anderen Benutzer bzw. einer anderen Gruppe, und
    der Zugang darf den Besitzer nicht aendern -- wie ein SSH-Benutzer ohne root. (Echte fremde
    Besitzer lassen sich nur als root anlegen, die Tests sollen auch ohne laufen.)"""
    import asyncssh

    real_stat = asyncssh.SFTPClient.stat

    async def other_owner(self, path, *args, **kwargs):  # noqa: ANN001
        attrs = await real_stat(self, path, *args, **kwargs)
        if str(path).endswith(name):
            setattr(attrs, field, getattr(attrs, field) + 4242)
        return attrs

    async def not_root(self, *args, **kwargs):  # noqa: ANN001
        raise asyncssh.SFTPPermissionDenied("nur root darf den Besitzer aendern")

    monkeypatch.setattr(asyncssh.SFTPClient, "stat", other_owner)
    monkeypatch.setattr(asyncssh.SFTPClientFile, "chown", not_root)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["uid", "gid"])
async def test_without_the_right_to_change_the_owner_the_file_is_written_in_place(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, field
):
    """Ohne root laesst sich der Besitzer einer Temp-Datei nicht auf den der alten Datei setzen.
    Ersetzen gaebe die Datei dann dem SSH-Benutzer (ein Webserver-Benutzer verlaere sein
    Schreibrecht): stattdessen wird in die bestehende Datei geschrieben, sie bleibt dieselbe."""
    _pretend_other_owner(monkeypatch, "app.conf", field=field)
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "app.conf").write_bytes(b"alt")
    os.chmod(root / "app.conf", 0o664)
    inode = (root / "app.conf").stat().st_ino

    await source.open_write(PurePosixPath("app.conf"), _stream(b"neu-", b"inhalt"))

    assert (root / "app.conf").read_bytes() == b"neu-inhalt"
    assert (root / "app.conf").stat().st_ino == inode
    assert stat.S_IMODE((root / "app.conf").stat().st_mode) == 0o664
    assert _temp_files(root) == []


@pytest.mark.asyncio
async def test_owner_is_taken_over_before_anything_is_written(local_ssh_server, db_session, tmp_path, test_settings, monkeypatch):
    """Passt der Besitzer, wird die Datei ersetzt (neue Datei, Besitzer schon gesetzt). Der
    Besitzerwechsel kommt gleich nach dem Anlegen: scheitert er, soll noch nichts geschrieben sein."""
    import asyncssh

    events: list[str] = []
    real_chown, real_write = asyncssh.SFTPClientFile.chown, asyncssh.SFTPClientFile.write

    async def chown(self, *args, **kwargs):  # noqa: ANN001
        events.append("chown")
        return await real_chown(self, *args, **kwargs)

    async def write(self, *args, **kwargs):  # noqa: ANN001
        events.append("write")
        return await real_write(self, *args, **kwargs)

    monkeypatch.setattr(asyncssh.SFTPClientFile, "chown", chown)
    monkeypatch.setattr(asyncssh.SFTPClientFile, "write", write)
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "app.conf").write_bytes(b"alt")
    inode = (root / "app.conf").stat().st_ino

    await source.open_write(PurePosixPath("app.conf"), _stream(b"neu-", b"inhalt"))

    assert events[0] == "chown"
    assert "write" in events
    assert (root / "app.conf").read_bytes() == b"neu-inhalt"
    assert (root / "app.conf").stat().st_ino != inode  # ersetzt, nicht hineingeschrieben


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["folder", "owner"])
async def test_in_place_fallback_reads_the_whole_source_before_touching_the_target(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, reason
):
    """Bricht die Quelle mittendrin ab, bleibt das Ziel unberuehrt -- auch im Rueckfall, in dem direkt
    ins Ziel geschrieben wird (Ordner nicht beschreibbar bzw. Besitzer nicht haltbar)."""
    if reason == "folder":
        _deny_new_files_in_folder(monkeypatch)
    else:
        _pretend_other_owner(monkeypatch, "app.conf")
    (source,) = await _sources(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "app.conf").write_bytes(b"alt")

    with pytest.raises(ConnectionError):
        await source.open_write(PurePosixPath("app.conf"), _stream(b"halb", b"-halb", then=ConnectionError("Quelle weg")))

    assert (root / "app.conf").read_bytes() == b"alt"
    assert _temp_files(root) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["folder", "owner"])
async def test_copy_onto_a_hard_link_of_itself_keeps_the_whole_file(
    client, db_session, tmp_path, test_settings, local_ssh_server, monkeypatch, reason
):
    """Dieselbe Datei unter zwei Pfaden (Hardlink; der Kern erkennt das nicht) und kein Ersetzen
    moeglich: geschrieben wird direkt ins Ziel. Das darf die Quelle nicht auf ihr erstes Stueck
    (64 KiB) kuerzen, weil das Oeffnen zum Schreiben sie abschneidet, bevor alles gelesen ist."""
    if reason == "folder":
        _deny_new_files_in_folder(monkeypatch)
    else:
        _pretend_other_owner(monkeypatch, "hart.txt")
    (sid,), headers = await _setup_api(client, db_session, tmp_path, test_settings, local_ssh_server, 1)
    root = local_ssh_server[4]
    (root / "wichtig.txt").write_bytes(CONTENT)
    (root / "ordner").mkdir()
    os.link(root / "wichtig.txt", root / "ordner" / "hart.txt")
    assert len(CONTENT) > 65536  # mehr als ein Stueck

    res = await _transfer(client, headers, sid, "/wichtig.txt", sid, "/ordner/hart.txt")
    assert res.status_code == 200, res.text
    run = await _finished(db_session, res.json()["run_id"])

    assert run.status == "succeeded", run.error
    assert (root / "wichtig.txt").read_bytes() == CONTENT
    assert (root / "ordner" / "hart.txt").read_bytes() == CONTENT
    assert _temp_files(root) == []


@pytest.mark.asyncio
async def test_same_machine_id_alone_does_not_make_two_files_the_same():
    """Geklonte Rechner teilen oft dieselbe `/etc/machine-id`. /etc/x auf dem einen Klon ist trotzdem
    nicht die Datei auf dem anderen: die Dateiwerte muessen weiter uebereinstimmen."""
    p = PurePosixPath("/etc/hosts")
    vm1 = _IdentitySource("a", _identity(machine="1" * 32))
    for other in ({"size": 11}, {"mtime": 6}, {"uid": 1}, {"gid": 1}, {"mode": 0o100600}):
        assert await _is_same_file(vm1, p, _IdentitySource("b", _identity(machine="1" * 32, **other)), p) is False, other
    # dieselbe Kennung, auch die Dateiwerte gleich: dieselbe Datei (zwei Eintraege fuer einen Rechner)
    assert await _is_same_file(vm1, p, _IdentitySource("b", _identity(machine="1" * 32)), p) is True
    # verschiedene Kennungen bleiben auch bei gleichen Dateiwerten zwei Dateien
    assert await _is_same_file(vm1, p, _IdentitySource("b", _identity(machine="2" * 32)), p) is False
