"""Hochladen auf Windows-Server mit OpenSSH: die virtuelle Wurzel gilt nur für Laufwerkspfade als fest."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

TERMINAL_SRC = Path(__file__).resolve().parents[2] / "extensions" / "terminal" / "src"


class _NoSuchFile(Exception):
    code = 2


def _attrs(perm: int, uid: int = 0):
    return SimpleNamespace(uid=uid, permissions=perm)


class _FakeWindowsSftp:
    """Antworten wie am echten Windows Server 2022 mit OpenSSH gemessen."""

    def __init__(self, folders: dict[str, SimpleNamespace] | None = None, links: dict[str, str] | None = None) -> None:
        self.links = links or {}
        self.folders = {
            "/": _attrs(0o40777),
            "/C:": _attrs(0o40700),
            "/C:/valheim": _attrs(0o40700),
            "/C:/Users": _attrs(0o40505),
            "/C:/Users/Administrator": _attrs(0o40700),
            "/home": _attrs(0o40755),
            "/x": _attrs(0o40755),
            "/home/x": _attrs(0o40755),
        }
        self.folders.update(folders or {})
        self.folders.update({name: _attrs(0o120777) for name in self.links})

    async def realpath(self, path):
        return "/C:/Users/Administrator"

    async def lstat(self, path):
        if path in self.folders:
            return self.folders[path]
        raise _NoSuchFile()

    async def open(self, path, mode="r"):
        if path.startswith("/tmp"):
            raise _NoSuchFile()
        return _Probe()

    async def remove(self, path):
        return None

    async def readlink(self, path):
        return self.links[path]


class _Probe:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def stat(self):
        return _attrs(0o100666)


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.syspath_prepend(str(TERMINAL_SRC))
    import nodvard_deck_ext_terminal

    yield nodvard_deck_ext_terminal
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_terminal"):
            del sys.modules[name]


async def test_windows_upload_on_drive_works(terminal):
    target, old = await terminal._write_target(
        _FakeWindowsSftp(), PurePosixPath("/C:/valheim/config/x.txt"), windows=True
    )
    assert target == "/C:/valheim/config/x.txt"
    assert old is None


async def test_windows_relative_path_resolves_to_drive(terminal):
    target, _ = await terminal._write_target(_FakeWindowsSftp(), PurePosixPath("x.txt"), windows=True)
    assert target == "/C:/Users/Administrator/x.txt"


async def test_same_server_without_windows_flag_is_refused(terminal):
    with pytest.raises(terminal._SwappableFolder):
        await terminal._write_target(_FakeWindowsSftp(), PurePosixPath("/C:/valheim/config/x.txt"))


async def test_windows_flag_does_not_relax_non_drive_paths(terminal):
    with pytest.raises(terminal._SwappableFolder):
        await terminal._write_target(_FakeWindowsSftp(), PurePosixPath("/home/x.txt"), windows=True)


async def test_windows_still_checks_folders_below_the_root(terminal):
    sftp = _FakeWindowsSftp({"/C:/valheim": _attrs(0o40777)})
    with pytest.raises(terminal._SwappableFolder):
        await terminal._write_target(sftp, PurePosixPath("/C:/valheim/x.txt"), windows=True)
    sftp = _FakeWindowsSftp({"/C:/valheim": _attrs(0o40700, uid=1000)})
    with pytest.raises(terminal._SwappableFolder):
        await terminal._write_target(sftp, PurePosixPath("/C:/valheim/x.txt"), windows=True)


async def test_windows_message_does_not_recommend_root_folder(terminal):
    sftp = _FakeWindowsSftp({"/C:/valheim": _attrs(0o40777)})
    with pytest.raises(terminal._SwappableFolder) as err:
        await terminal._write_target(sftp, PurePosixPath("/C:/valheim/x.txt"), windows=True)
    assert "root" not in str(err.value)


@pytest.mark.parametrize("path", ["/C:/../home/x/y.txt", "/C:/../x/y.txt", "../../../x/y.txt", "/C:x/y.txt"])
async def test_windows_root_is_not_trusted_after_leaving_the_drive(terminal, path):
    with pytest.raises(terminal._SwappableFolder):
        await terminal._write_target(_FakeWindowsSftp(), PurePosixPath(path), windows=True)


async def test_windows_link_back_to_root_is_not_trusted(terminal):
    sftp = _FakeWindowsSftp(links={"/C:/valheim/zurueck": "/home"})
    with pytest.raises(terminal._SwappableFolder):
        await terminal._write_target(sftp, PurePosixPath("/C:/valheim/zurueck/x.txt"), windows=True)


async def test_windows_lowercase_drive_works(terminal):
    sftp = _FakeWindowsSftp({"/c:": _attrs(0o40700)})
    target, _ = await terminal._write_target(sftp, PurePosixPath("/c:/x.txt"), windows=True)
    assert target == "/c:/x.txt"


async def test_windows_drive_alone_resolves_to_the_drive_folder(terminal):
    target, old = await terminal._write_target(_FakeWindowsSftp(), PurePosixPath("/C:"), windows=True)
    assert target == "/C:"
    assert old is not None


async def test_windows_server_gets_windows_text_for_non_drive_path(terminal):
    with pytest.raises(terminal._SwappableFolder) as err:
        await terminal._write_target(_FakeWindowsSftp(), PurePosixPath("/home/x.txt"), windows=True)
    assert "Windows-Server" in str(err.value)
    assert "root" not in str(err.value)


async def test_refused_drive_path_on_non_windows_server_hints_at_the_setting(terminal):
    with pytest.raises(terminal._SwappableFolder) as err:
        await terminal._write_target(_FakeWindowsSftp(), PurePosixPath("/C:/valheim/x.txt"))
    assert "Betriebssystem auf Windows" in str(err.value)


async def test_refused_non_drive_path_has_no_windows_hint(terminal):
    with pytest.raises(terminal._SwappableFolder) as err:
        await terminal._write_target(_FakeWindowsSftp(), PurePosixPath("/home/x.txt"))
    assert "Betriebssystem" not in str(err.value)


class _Stop(Exception):
    pass


@pytest.mark.parametrize(("os_family", "relaxed"), [("windows", True), ("Windows", False), ("win", False), ("", False), ("linux", False)])
async def test_only_exact_windows_family_relaxes_the_check(terminal, monkeypatch, os_family, relaxed):
    seen = []

    async def fake_target(sftp, path, windows=False):
        seen.append(windows)
        raise _Stop

    class _Sftp:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    class _Ctx:
        exec = SimpleNamespace(sftp=lambda host: _Sftp())
        hosts = SimpleNamespace(get=None)

    async def get(host_id):
        return SimpleNamespace(os_family=os_family)

    ctx = _Ctx()
    ctx.hosts = SimpleNamespace(get=get)
    monkeypatch.setattr(terminal, "_write_target", fake_target)
    source = terminal._SshFileSource(ctx, "h1")
    with contextlib.suppress(_Stop):
        await source.open_write(PurePosixPath("/C:/x.txt"), None)
    assert seen == [relaxed]
