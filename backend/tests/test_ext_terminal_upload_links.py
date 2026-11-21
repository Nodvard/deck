"""Hochladen auf eine SSH-Quelle folgt Verknüpfungen und nutzt Ordner im Zielpfad nur, wenn sie root oder dem angemeldeten
Konto gehören."""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath

import asyncssh
import pytest
from nodvard_deck.api.v1.files import _authorized_source
from nodvard_deck.ext.context import build_context
from nodvard_deck.ext.runtime import ExtensionRuntime, LoadedExtension, reset_extension_runtime
from nodvard_deck.services import hosts as hosts_service
from nodvard_sdk import ExtensionManifest

TERMINAL_SRC = Path(__file__).resolve().parents[2] / "extensions" / "terminal" / "src"
FOREIGN = 4242
OTHER = 4343
BOB = 4444
USER = 1000


@pytest.fixture(autouse=True)
def _fixed_umask():
    # Die Prüfung lehnt gruppen-schreibbare Ordner ab; mit umask 002 entstünden die Testordner so.
    old = os.umask(0o022)
    yield
    os.umask(old)


@pytest.fixture(autouse=True)
def _terminal_on_path(monkeypatch):
    reset_extension_runtime()
    monkeypatch.syspath_prepend(str(TERMINAL_SRC))
    yield
    reset_extension_runtime()
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_terminal"):
            del sys.modules[name]


async def _source(db_session, settings, server, tmp_path):
    import nodvard_deck_ext_terminal

    perms = ["hosts.read", "hosts.execute"]
    manifest = ExtensionManifest(
        id="terminal", name="Terminal", version="0.1.0", api_version="0.1",
        entrypoint="nodvard_deck_ext_terminal:Extension", permissions=perms,
    )
    loaded = LoadedExtension(manifest=manifest, instance=None, ctx=None, granted_permissions=perms)
    loaded.ctx = build_context(ExtensionRuntime(), loaded, manifest, perms, tmp_path / "data", settings)
    extension = nodvard_deck_ext_terminal.Extension()
    await extension.setup(loaded.ctx)
    address, port, username, password, _root = server
    host = await hosts_service.create_host(db_session, name="pve", address=address)
    await hosts_service.add_credential(
        db_session, settings, host_id=host.id, kind="ssh_password", username=username, port=port, secret_value=password
    )
    await db_session.commit()
    return _authorized_source(extension.file_source_for(host.id))


def _stream(data: bytes):
    async def gen():
        yield data

    return gen()


def _leftovers(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if ".nodvard-" in p.name]


def _owners(
    monkeypatch, root: Path, owners: dict[Path, int | None], *, home: str = "root", login: int | None = None
) -> None:
    """Der Server meldet für jeden Pfad einen Besitzer: aus `owners`, sonst root. SFTP startet in `home`, angemeldet ist
    `login`, ohne Angabe der Besitzer von `home`."""
    if not (root / home).exists():
        (root / home).mkdir(parents=True)
    account = owners.get(root / home, 0) if login is None else login
    real_fstat = asyncssh.SFTPServer.fstat

    def fstat(self, file_obj):
        attrs = asyncssh.SFTPAttrs.from_local(real_fstat(self, file_obj))
        if ".nodvard-konto-" in os.fsdecode(getattr(file_obj, "name", b"")):
            attrs.uid = attrs.gid = account
            attrs.owner = attrs.group = None
        return attrs

    monkeypatch.setattr(asyncssh.SFTPServer, "fstat", fstat)

    def local(server, path: bytes) -> Path:
        return Path(os.fsdecode(server.map_path(path)))

    def with_owner(result, key: Path):
        attrs = asyncssh.SFTPAttrs.from_local(result)
        attrs.uid = attrs.gid = owners.get(key, 0)
        attrs.owner = attrs.group = None
        return attrs

    real_lstat, real_stat, real_realpath = asyncssh.SFTPServer.lstat, asyncssh.SFTPServer.stat, asyncssh.SFTPServer.realpath
    monkeypatch.setattr(asyncssh.SFTPServer, "lstat", lambda self, p: with_owner(real_lstat(self, p), local(self, p)))
    monkeypatch.setattr(
        asyncssh.SFTPServer, "stat",
        lambda self, p: with_owner(real_stat(self, p), Path(os.path.realpath(local(self, p)))),
    )
    monkeypatch.setattr(
        asyncssh.SFTPServer, "realpath",
        lambda self, p: b"/" + os.fsencode(home) if p in (b".", b"") else real_realpath(self, p),
    )


def _one_hop_readlink(monkeypatch) -> None:
    """`readlink` liefert wie OpenSSH nur den nächsten Schritt, nicht das Ende der Kette."""

    def readlink(self, path: bytes) -> bytes:
        return os.fsencode(os.readlink(os.fsdecode(self.map_path(path))))

    monkeypatch.setattr(asyncssh.SFTPServer, "readlink", readlink)


@pytest.mark.asyncio
async def test_upload_onto_a_foreign_link_leaves_its_target_alone(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "geheim.txt").write_bytes(b"geheim")
    (root / "bild.png").symlink_to("geheim.txt")
    _owners(monkeypatch, root, {root / "bild.png": FOREIGN})

    with pytest.raises(Exception, match="Verknüpfung"):
        await source.open_write(PurePosixPath("/bild.png"), _stream(b"neu"))

    assert (root / "geheim.txt").read_bytes() == b"geheim"
    assert (root / "bild.png").is_symlink()
    assert _leftovers(root) == []


@pytest.mark.asyncio
async def test_upload_through_a_foreign_dangling_link_creates_nothing(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "cron.d").mkdir()
    (root / "bild.png").symlink_to("cron.d/neu")
    _owners(monkeypatch, root, {root / "bild.png": FOREIGN})

    with pytest.raises(Exception, match="Verknüpfung"):
        await source.open_write(PurePosixPath("/bild.png"), _stream(b"neu"))

    assert list((root / "cron.d").iterdir()) == []


@pytest.mark.asyncio
async def test_link_without_owner_is_not_followed(local_ssh_server, db_session, tmp_path, test_settings, monkeypatch):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "geheim.txt").write_bytes(b"geheim")
    (root / "bild.png").symlink_to("geheim.txt")
    _owners(monkeypatch, root, {root / "bild.png": None, root / "geheim.txt": None})

    with pytest.raises(Exception, match="Verknüpfung"):
        await source.open_write(PurePosixPath("/bild.png"), _stream(b"neu"))

    assert (root / "geheim.txt").read_bytes() == b"geheim"


@pytest.mark.asyncio
async def test_foreign_link_inside_a_chain_is_not_followed(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "geheim.txt").write_bytes(b"geheim")
    (root / "zwischen").symlink_to("geheim.txt")
    (root / "bild.png").symlink_to("zwischen")
    _owners(monkeypatch, root, {root / "bild.png": 0, root / "zwischen": FOREIGN, root / "geheim.txt": 0})
    _one_hop_readlink(monkeypatch)

    with pytest.raises(Exception, match="Verknüpfung"):
        await source.open_write(PurePosixPath("/bild.png"), _stream(b"neu"))

    assert (root / "geheim.txt").read_bytes() == b"geheim"


@pytest.mark.asyncio
@pytest.mark.parametrize(("link_owner", "home"), [(0, "root"), (BOB, "home/bob")])
async def test_link_of_root_or_of_the_login_account_is_followed(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, link_owner, home
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "echt.txt").write_bytes(b"alt")
    (root / "kurz.txt").symlink_to("echt.txt")
    _owners(monkeypatch, root, {root / "home" / "bob": BOB, root / "kurz.txt": link_owner, root / "echt.txt": FOREIGN}, home=home)
    _one_hop_readlink(monkeypatch)

    await source.open_write(PurePosixPath("/kurz.txt"), _stream(b"neu"))

    assert (root / "kurz.txt").is_symlink()
    assert (root / "echt.txt").read_bytes() == b"neu"


@pytest.mark.asyncio
async def test_chain_of_trusted_links_is_followed(local_ssh_server, db_session, tmp_path, test_settings, monkeypatch):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "echt.txt").write_bytes(b"alt")
    (root / "zwischen").symlink_to("echt.txt")
    (root / "kurz.txt").symlink_to("zwischen")
    _owners(monkeypatch, root, {root / "kurz.txt": 0, root / "zwischen": 0, root / "echt.txt": OTHER})
    _one_hop_readlink(monkeypatch)

    await source.open_write(PurePosixPath("/kurz.txt"), _stream(b"neu"))

    assert (root / "echt.txt").read_bytes() == b"neu"


@pytest.mark.asyncio
async def test_link_of_the_file_owner_is_not_followed_when_logged_in_as_root(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "echt.txt").write_bytes(b"alt")
    (root / "kurz.txt").symlink_to("echt.txt")
    _owners(monkeypatch, root, {root / "kurz.txt": FOREIGN, root / "echt.txt": FOREIGN})
    _one_hop_readlink(monkeypatch)

    with pytest.raises(Exception, match="Verknüpfung"):
        await source.open_write(PurePosixPath("/kurz.txt"), _stream(b"neu"))

    assert (root / "echt.txt").read_bytes() == b"alt"


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted_chain", [True, False])
async def test_chain_is_not_followed_when_the_server_only_resolves_it_whole(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, trusted_chain
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "fremd").symlink_to("etc/crontab")
    (root / "bild.png").symlink_to("fremd")
    _owners(monkeypatch, root, {root / "fremd": 0 if trusted_chain else FOREIGN})

    def no_readlink(self, path: bytes) -> bytes:
        raise asyncssh.SFTPOpUnsupported("readlink")

    monkeypatch.setattr(asyncssh.SFTPServer, "readlink", no_readlink)

    with pytest.raises(Exception, match="Verknüpfung"):
        await source.open_write(PurePosixPath("/bild.png"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"
    assert _leftovers(root) == []


@pytest.mark.asyncio
@pytest.mark.needs_root
@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="Besitzer einer Verknüpfung ändern braucht root")
async def test_upload_as_root_onto_a_link_of_another_account_is_refused(
    local_ssh_server, db_session, tmp_path, test_settings
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "shadow").write_bytes(b"root-Inhalt")
    (root / "upload.txt").symlink_to("shadow")
    os.lchown(root / "upload.txt", FOREIGN, FOREIGN)

    with pytest.raises(Exception, match="Verknüpfung"):
        await source.open_write(PurePosixPath("/upload.txt"), _stream(b"neu"))

    assert (root / "shadow").read_bytes() == b"root-Inhalt"


@pytest.mark.asyncio
async def test_endless_chain_of_links_is_not_followed(local_ssh_server, db_session, tmp_path, test_settings, monkeypatch):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "echt.txt").write_bytes(b"alt")
    previous = "echt.txt"
    for i in range(41):
        (root / f"l{i}").symlink_to(previous)
        previous = f"l{i}"
    _one_hop_readlink(monkeypatch)

    with pytest.raises(Exception, match="Verknüpfung"):
        await source.open_write(PurePosixPath(f"/{previous}"), _stream(b"neu"))

    assert (root / "echt.txt").read_bytes() == b"alt"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["crontab", "neu"])
@pytest.mark.parametrize("one_hop", [True, False])
async def test_foreign_folder_link_leaves_the_folder_alone(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, name, one_hop
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "etc").mkdir()
    (root / "etc" / "crontab").write_bytes(b"alt")
    (root / "upload").mkdir()
    (root / "upload" / "ziel").symlink_to("../etc")
    _owners(monkeypatch, root, {root / "upload" / "ziel": FOREIGN, root / "etc": 0, root / "etc" / "crontab": 0})
    if one_hop:
        _one_hop_readlink(monkeypatch)

    with pytest.raises(Exception, match="Verknüpfung"):
        await source.open_write(PurePosixPath(f"/upload/ziel/{name}"), _stream(b"neu"))

    assert sorted(p.name for p in (root / "etc").iterdir()) == ["crontab"]
    assert (root / "etc" / "crontab").read_bytes() == b"alt"


@pytest.mark.asyncio
async def test_foreign_folder_link_inside_a_trusted_chain_is_not_followed(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "etc").mkdir()
    (root / "etc" / "crontab").write_bytes(b"alt")
    (root / "fremd").symlink_to("etc")
    (root / "bild.png").symlink_to("fremd/crontab")
    _owners(monkeypatch, root, {root / "bild.png": 0, root / "fremd": FOREIGN, root / "etc": 0, root / "etc" / "crontab": 0})
    _one_hop_readlink(monkeypatch)

    with pytest.raises(Exception, match="Verknüpfung"):
        await source.open_write(PurePosixPath("/bild.png"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"
    assert _leftovers(root) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("link_owner", "folder_owner", "home"), [(0, 0, "root"), (BOB, BOB, "home/bob"), (0, BOB, "home/bob")]
)
@pytest.mark.parametrize("name", ["index.html", "neu.html"])
async def test_folder_link_of_root_or_of_the_login_account_is_followed(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, link_owner, folder_owner, home, name
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "site").mkdir()
    (root / "site" / "index.html").write_bytes(b"alt")
    (root / "www").symlink_to("site")
    _owners(monkeypatch, root, {
        root / "home" / "bob": BOB,
        root / "www": link_owner, root / "site": folder_owner, root / "site" / "index.html": folder_owner,
    }, home=home)
    _one_hop_readlink(monkeypatch)

    await source.open_write(PurePosixPath(f"/www/{name}"), _stream(b"neu"))

    assert (root / "www").is_symlink()
    assert (root / "site" / name).read_bytes() == b"neu"


@pytest.mark.asyncio
@pytest.mark.parametrize("link_owner", [0, FOREIGN])
async def test_folder_link_to_a_folder_of_another_account_is_not_used_as_root(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, link_owner
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "site").mkdir()
    (root / "site" / "index.html").write_bytes(b"alt")
    (root / "www").symlink_to("site")
    _owners(monkeypatch, root, {
        root / "www": link_owner, root / "site": FOREIGN, root / "site" / "index.html": FOREIGN,
    })
    _one_hop_readlink(monkeypatch)

    with pytest.raises(Exception, match="Verknüpfung|Ordner"):
        await source.open_write(PurePosixPath("/www/index.html"), _stream(b"neu"))

    assert (root / "site" / "index.html").read_bytes() == b"alt"


@pytest.mark.asyncio
async def test_absolute_folder_link_starts_at_the_root(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "site").mkdir()
    (root / "a").mkdir()
    (root / "a" / "www").symlink_to("/site")
    _owners(monkeypatch, root, {root / "a" / "www": 0})
    _one_hop_readlink(monkeypatch)

    await source.open_write(PurePosixPath("/a/www/neu.html"), _stream(b"neu"))

    assert (root / "site" / "neu.html").read_bytes() == b"neu"


@pytest.mark.asyncio
async def test_link_is_not_followed_when_the_server_reports_no_owners(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "geheim.txt").write_bytes(b"geheim")
    (root / "bild.png").symlink_to("geheim.txt")
    _owners(monkeypatch, root, {root: None, root / "bild.png": None, root / "geheim.txt": None})
    _one_hop_readlink(monkeypatch)

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/bild.png"), _stream(b"neu"))

    assert (root / "geheim.txt").read_bytes() == b"geheim"


@pytest.mark.asyncio
async def test_folder_link_the_server_cannot_resolve_is_not_followed(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "etc").mkdir()
    (root / "etc" / "crontab").write_bytes(b"alt")
    (root / "upload").mkdir()
    (root / "upload" / "ziel").symlink_to("../etc")
    # Ohne realpath findet die Probedatei nur hier Platz; sonst wäre das Testkonto unbekannt und schon "/" verdächtig.
    (root / "tmp").mkdir()

    def unresolvable(self, path: bytes) -> bytes:
        raise asyncssh.SFTPNoSuchFile("nicht auflösbar")

    monkeypatch.setattr(asyncssh.SFTPServer, "readlink", unresolvable)
    monkeypatch.setattr(asyncssh.SFTPServer, "realpath", unresolvable)

    with pytest.raises(Exception, match="Ziel einer Verknüpfung im Zielpfad nicht Schritt für Schritt"):
        await source.open_write(PurePosixPath("/upload/ziel/crontab"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [asyncssh.SFTPPermissionDenied("verweigert"), asyncssh.SFTPFailure("kaputt"), asyncssh.SFTPConnectionLost("weg")]
)
async def test_part_of_the_path_that_cannot_be_read_is_not_treated_as_missing(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, error
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "upload").mkdir()
    (root / "upload" / "ziel").symlink_to("../etc")
    real_lstat = asyncssh.SFTPServer.lstat

    def lstat(self, path: bytes):
        if path == b"/upload/ziel":
            raise error
        return real_lstat(self, path)

    monkeypatch.setattr(asyncssh.SFTPServer, "lstat", lstat)

    with pytest.raises(Exception, match="nicht prüfen") as caught:
        await source.open_write(PurePosixPath("/upload/ziel/crontab"), _stream(b"neu"))

    assert str(error.reason) in str(caught.value)
    assert (root / "etc" / "crontab").read_bytes() == b"alt"
    assert _leftovers(root) == []


@pytest.mark.asyncio
async def test_error_text_of_the_server_is_shortened_and_cleaned(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "upload").mkdir()
    (root / "upload" / "ziel").symlink_to("../etc")
    real_lstat = asyncssh.SFTPServer.lstat

    def lstat(self, path: bytes):
        if path == b"/upload/ziel":
            raise asyncssh.SFTPFailure("kaputt\r\nFalsche Zeile\x1b[31m\u202e" + "x" * 500)
        return real_lstat(self, path)

    monkeypatch.setattr(asyncssh.SFTPServer, "lstat", lstat)

    with pytest.raises(Exception, match="nicht prüfen") as caught:
        await source.open_write(PurePosixPath("/upload/ziel/crontab"), _stream(b"neu"))

    message = str(caught.value)
    assert "kaputt Falsche Zeile" in message
    assert not any(ch in message for ch in "\r\n\x1b\u202e")
    assert "x" * 120 not in message
    assert "…" in message
    assert (root / "etc" / "crontab").read_bytes() == b"alt"


@pytest.mark.asyncio
async def test_missing_part_of_the_path_is_still_created(local_ssh_server, db_session, tmp_path, test_settings):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "upload").mkdir()

    await source.open_write(PurePosixPath("/upload/neu.txt"), _stream(b"neu"))

    assert (root / "upload" / "neu.txt").read_bytes() == b"neu"


def _swap_folder_after_check(monkeypatch, folder: Path, target: str) -> None:
    """Tauscht `folder` gleich nach der Prüfung gegen eine Verknüpfung auf `target`, wie ein fremdes Konto im Wettlauf."""
    import nodvard_deck_ext_terminal as terminal

    checked = terminal._write_target

    async def check_then_swap(sftp, path):
        result = await checked(sftp, path)
        folder.rename(folder.with_name(folder.name + ".alt"))
        folder.symlink_to(target)
        return result

    monkeypatch.setattr(terminal, "_write_target", check_then_swap)


def _etc_with_crontab(root: Path) -> None:
    (root / "etc").mkdir()
    (root / "etc" / "crontab").write_bytes(b"alt")


@pytest.mark.asyncio
async def test_folder_in_a_foreign_folder_cannot_be_swapped_for_a_link(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "upload" / "d").mkdir(parents=True)
    (root / "upload" / "d" / "crontab").write_bytes(b"upload")
    _owners(monkeypatch, root, {root / "upload": FOREIGN})
    _swap_folder_after_check(monkeypatch, root / "upload" / "d", "../etc")

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/upload/d/crontab"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"
    assert _leftovers(root) == []


@pytest.mark.asyncio
async def test_folder_right_below_a_foreign_root_cannot_be_swapped(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "d").mkdir()
    _owners(monkeypatch, root, {root: FOREIGN})
    _swap_folder_after_check(monkeypatch, root / "d", "etc")

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/d/crontab"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [0o777, 0o775, 0o757])
async def test_folder_in_a_folder_writable_for_others_cannot_be_swapped(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, mode
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "upload" / "d").mkdir(parents=True)
    (root / "upload").chmod(mode)
    _swap_folder_after_check(monkeypatch, root / "upload" / "d", "../etc")

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/upload/d/crontab"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"


@pytest.mark.asyncio
async def test_foreign_folder_in_a_sticky_folder_cannot_be_swapped(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "tmp" / "d").mkdir(parents=True)
    (root / "tmp" / "d" / "crontab").write_bytes(b"tmp")
    (root / "tmp").chmod(0o1777)
    _owners(monkeypatch, root, {root / "tmp" / "d": FOREIGN})
    _swap_folder_after_check(monkeypatch, root / "tmp" / "d", "../etc")

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/tmp/d/crontab"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"


@pytest.mark.asyncio
@pytest.mark.parametrize(("owner", "home"), [(0, "root"), (BOB, "home/bob")])
async def test_folder_of_root_or_of_the_login_account_in_a_sticky_folder_is_used(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, owner, home
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "tmp" / "d").mkdir(parents=True)
    (root / "tmp").chmod(0o1777)
    (root / "tmp" / "d" / "datei").write_bytes(b"alt")
    _owners(monkeypatch, root, {
        root / "home" / "bob": BOB, root / "tmp" / "d": owner, root / "tmp" / "d" / "datei": owner,
    }, home=home)

    await source.open_write(PurePosixPath("/tmp/d/datei"), _stream(b"neu"))

    assert (root / "tmp" / "d" / "datei").read_bytes() == b"neu"


@pytest.mark.asyncio
async def test_folders_of_the_login_account_are_used(local_ssh_server, db_session, tmp_path, test_settings, monkeypatch):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "home" / "bob" / "d").mkdir(parents=True)
    (root / "home" / "bob" / "d" / "datei").write_bytes(b"alt")
    _owners(monkeypatch, root, {
        root / "home" / "bob": BOB, root / "home" / "bob" / "d": BOB, root / "home" / "bob" / "d" / "datei": BOB,
    }, home="home/bob")

    await source.open_write(PurePosixPath("/home/bob/d/datei"), _stream(b"neu"))

    assert (root / "home" / "bob" / "d" / "datei").read_bytes() == b"neu"


@pytest.mark.asyncio
async def test_folder_of_the_file_owner_cannot_be_swapped_when_logged_in_as_root(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "home" / "u" / "d").mkdir(parents=True)
    (root / "home" / "u" / "d" / "crontab").write_bytes(b"u")
    _owners(monkeypatch, root, {
        root / "home" / "u": FOREIGN, root / "home" / "u" / "d": FOREIGN, root / "home" / "u" / "d" / "crontab": FOREIGN,
    })
    _swap_folder_after_check(monkeypatch, root / "home" / "u" / "d", "../../etc")

    with pytest.raises(Exception, match="Verbinde den Server mit dem Konto, dem der Ordner gehört"):
        await source.open_write(PurePosixPath("/home/u/d/crontab"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"
    assert _leftovers(root) == []


@pytest.mark.asyncio
async def test_folder_of_another_account_holding_the_file_is_not_used(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "d").mkdir()
    (root / "d" / "datei").write_bytes(b"alt")
    _owners(monkeypatch, root, {root / "d": FOREIGN})

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/d/datei"), _stream(b"neu"))

    assert (root / "d" / "datei").read_bytes() == b"alt"


@pytest.mark.asyncio
async def test_folder_holding_the_file_must_not_be_writable_for_others(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "d").mkdir()
    (root / "d" / "datei").write_bytes(b"alt")
    (root / "d").chmod(0o777)
    _owners(monkeypatch, root, {})

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/d/datei"), _stream(b"neu"))

    assert (root / "d" / "datei").read_bytes() == b"alt"


@pytest.mark.asyncio
@pytest.mark.parametrize(("file_owner", "allowed"), [(None, True), (0, True), (FOREIGN, False)])
async def test_file_in_a_sticky_folder_is_only_replaced_when_nobody_else_can_swap_it(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, file_owner, allowed
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "tmp").mkdir()
    (root / "tmp").chmod(0o1777)
    if file_owner is not None:
        (root / "tmp" / "datei").write_bytes(b"alt")
    _owners(monkeypatch, root, {root / "tmp" / "datei": file_owner or 0})

    if allowed:
        await source.open_write(PurePosixPath("/tmp/datei"), _stream(b"neu"))
        assert (root / "tmp" / "datei").read_bytes() == b"neu"
    else:
        with pytest.raises(Exception, match="Ordner"):
            await source.open_write(PurePosixPath("/tmp/datei"), _stream(b"neu"))
        assert (root / "tmp" / "datei").read_bytes() == b"alt"


@pytest.mark.asyncio
@pytest.mark.parametrize("home", ["folder", "file", "missing"])
async def test_owner_of_the_home_counts_as_login_only_for_a_folder(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, home
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "d").mkdir()
    (root / "d" / "datei").write_bytes(b"alt")
    if home == "file":
        (root / "heim").write_bytes(b"")
    _owners(monkeypatch, root, {root / "heim": FOREIGN, root / "d": FOREIGN}, home="heim")
    if home == "missing":
        (root / "heim").rmdir()

    if home == "folder":
        await source.open_write(PurePosixPath("/d/datei"), _stream(b"neu"))
        assert (root / "d" / "datei").read_bytes() == b"neu"
    else:
        with pytest.raises(Exception, match="Ordner"):
            await source.open_write(PurePosixPath("/d/datei"), _stream(b"neu"))
        assert (root / "d" / "datei").read_bytes() == b"alt"


@pytest.mark.asyncio
async def test_foreign_folder_without_owner_information_is_not_used(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "upload" / "d").mkdir(parents=True)
    _owners(monkeypatch, root, {root / "upload": None, root / "upload" / "d": None})
    _swap_folder_after_check(monkeypatch, root / "upload" / "d", "../etc")

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/upload/d/crontab"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"


@pytest.mark.asyncio
async def test_folder_behind_a_link_in_a_foreign_folder_cannot_be_swapped(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "upload" / "d").mkdir(parents=True)
    (root / "web").symlink_to("upload")
    _owners(monkeypatch, root, {root / "upload": FOREIGN})
    _one_hop_readlink(monkeypatch)
    _swap_folder_after_check(monkeypatch, root / "upload" / "d", "../etc")

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/web/d/crontab"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"


@pytest.mark.asyncio
async def test_link_through_a_file_is_not_resolved(local_ssh_server, db_session, tmp_path, test_settings, monkeypatch):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "data").mkdir()
    (root / "data" / "block").write_bytes(b"datei")
    (root / "data" / "ziel.conf").write_bytes(b"alt")
    (root / "data" / "link").symlink_to("block/../ziel.conf")
    _one_hop_readlink(monkeypatch)

    with pytest.raises(Exception, match="kein Ordner"):
        await source.open_write(PurePosixPath("/data/link"), _stream(b"neu"))

    assert (root / "data" / "ziel.conf").read_bytes() == b"alt"
    assert (root / "data" / "block").read_bytes() == b"datei"
    assert (root / "data" / "link").is_symlink()


def _physical_paths(monkeypatch) -> None:
    """Der Server löst `..` wie OpenSSH erst beim Zugriff am echten Ordner auf, nicht vorher im Text."""

    def map_path(self, path: bytes) -> bytes:
        return self._chroot + b"/" + path.lstrip(b"/")

    monkeypatch.setattr(asyncssh.SFTPServer, "map_path", map_path)


@pytest.mark.asyncio
async def test_foreign_folders_left_by_dot_dot_cannot_be_swapped(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "etc" / "ssl" / "certs").mkdir(parents=True)
    (root / "tmp" / "u" / "d").mkdir(parents=True)
    _owners(monkeypatch, root, {root / "tmp" / "u": FOREIGN, root / "tmp" / "u" / "d": FOREIGN})
    _swap_folder_after_check(monkeypatch, root / "tmp" / "u" / "d", "../../etc/ssl/certs")
    _physical_paths(monkeypatch)

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/tmp/u/d/../../crontab"), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"
    assert not (root / "tmp" / "crontab").exists()
    assert _leftovers(root) == []


@pytest.mark.asyncio
async def test_write_goes_to_the_checked_path_not_the_given_one(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "site" / "d").mkdir(parents=True)
    _owners(monkeypatch, root, {})
    _swap_folder_after_check(monkeypatch, root / "site" / "d", "../etc")
    _physical_paths(monkeypatch)

    await source.open_write(PurePosixPath("/site/d/../neu.html"), _stream(b"neu"))

    assert (root / "site" / "neu.html").read_bytes() == b"neu"
    assert not (root / "neu.html").exists()


@pytest.mark.asyncio
async def test_link_in_a_foreign_folder_cannot_be_swapped_before_it_is_read(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    upload = root / "srv" / "upload"
    upload.mkdir(parents=True)
    (upload / "bild-echt.png").write_bytes(b"bild")
    (upload / "bild.png").symlink_to("bild-echt.png")
    _owners(monkeypatch, root, {upload: FOREIGN, upload / "bild-echt.png": FOREIGN})
    read: list[Path] = []

    def swap_then_readlink(self, path: bytes) -> bytes:
        link = Path(os.fsdecode(self.map_path(path)))
        read.append(link)
        if link == upload / "bild.png":
            link.unlink()
            link.symlink_to("../../etc/crontab")
        return os.fsencode(os.readlink(link))

    monkeypatch.setattr(asyncssh.SFTPServer, "readlink", swap_then_readlink)

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/srv/upload/bild.png"), _stream(b"neu"))

    assert read == []
    assert (root / "etc" / "crontab").read_bytes() == b"alt"
    assert _leftovers(root) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("hops", [1, 2])
async def test_folder_holding_a_link_must_not_be_writable_for_others(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch, hops
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "offen").mkdir()
    (root / "offen" / "link").symlink_to("../etc/crontab")
    (root / "bild.png").symlink_to("offen/link")
    (root / "offen").chmod(0o777)
    _owners(monkeypatch, root, {})
    _one_hop_readlink(monkeypatch)

    path = "/bild.png" if hops == 2 else "/offen/link"
    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath(path), _stream(b"neu"))

    assert (root / "etc" / "crontab").read_bytes() == b"alt"


@pytest.mark.asyncio
async def test_relative_path_is_checked_from_the_root(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _owners(monkeypatch, root, {root / "home": FOREIGN, root / "home" / "bob": BOB}, home="home/bob")

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("datei"), _stream(b"neu"))

    assert not (root / "home" / "bob" / "datei").exists()


@pytest.mark.asyncio
async def test_root_without_permissions_is_not_used(local_ssh_server, db_session, tmp_path, test_settings, monkeypatch):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _owners(monkeypatch, root, {})
    lstat = asyncssh.SFTPServer.lstat

    def without_permissions(self, path: bytes):
        attrs = lstat(self, path)
        if path == b"/":
            attrs.permissions = None
        return attrs

    monkeypatch.setattr(asyncssh.SFTPServer, "lstat", without_permissions)

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/datei"), _stream(b"neu"))

    assert not (root / "datei").exists()


@pytest.mark.asyncio
async def test_missing_folder_in_a_sticky_folder_is_not_used(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    import nodvard_deck_ext_terminal as terminal

    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _etc_with_crontab(root)
    (root / "tmp").mkdir()
    (root / "tmp").chmod(0o1777)
    _owners(monkeypatch, root, {})
    checked = terminal._write_target

    async def check_then_create(sftp, path):
        result = await checked(sftp, path)
        (root / "tmp" / "d").symlink_to("../etc")
        return result

    monkeypatch.setattr(terminal, "_write_target", check_then_create)

    with pytest.raises(Exception, match="austauschen"):
        await source.open_write(PurePosixPath("/tmp/d/datei"), _stream(b"neu"))

    assert sorted(p.name for p in (root / "etc").iterdir()) == ["crontab"]


@pytest.mark.asyncio
async def test_missing_file_in_a_sticky_folder_is_not_written_through_a_new_entry(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "etc").mkdir()
    os.mkfifo(root / "etc" / "pipe")
    drop = root / "srv" / "drop"
    drop.mkdir(parents=True)
    drop.chmod(0o1770)
    _owners(monkeypatch, root, {})
    lstat = asyncssh.SFTPServer.lstat

    def missing_then_created(self, path: bytes):
        try:
            return lstat(self, path)
        except OSError:
            if path == b"/srv/drop/neu" and not (drop / "neu").is_symlink():
                (drop / "neu").symlink_to("../../etc/pipe")
            raise

    monkeypatch.setattr(asyncssh.SFTPServer, "lstat", missing_then_created)
    reader = os.open(root / "etc" / "pipe", os.O_RDONLY | os.O_NONBLOCK)
    try:
        await source.open_write(PurePosixPath("/srv/drop/neu"), _stream(b"neu"))
        try:
            leaked = os.read(reader, 100)
        except BlockingIOError:
            leaked = b""
    finally:
        os.close(reader)

    assert leaked == b""
    assert not (drop / "neu").is_symlink()
    assert (drop / "neu").read_bytes() == b"neu"
    assert _leftovers(root) == []


def _upload_folder_of_the_user(root: Path) -> None:
    (root / "tmp").mkdir()
    (root / "tmp").chmod(0o1777)
    (root / "upload").mkdir()
    (root / "upload").chmod(0o755)


@pytest.mark.asyncio
async def test_folder_of_the_login_account_is_used_when_sftp_starts_in_a_folder_of_root(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _upload_folder_of_the_user(root)
    _owners(monkeypatch, root, {root / "upload": USER}, home="", login=USER)

    await source.open_write(PurePosixPath("/upload/datei"), _stream(b"neu"))

    assert (root / "upload" / "datei").read_bytes() == b"neu"
    assert _leftovers(root) == []


def _no_probe_files(monkeypatch) -> None:
    real_open = asyncssh.SFTPServer.open

    def open_(self, path: bytes, *args, **kwargs):
        if b".nodvard-konto-" in path:
            raise asyncssh.SFTPPermissionDenied("nicht beschreibbar")
        return real_open(self, path, *args, **kwargs)

    monkeypatch.setattr(asyncssh.SFTPServer, "open", open_)


@pytest.mark.asyncio
async def test_without_a_known_login_account_only_root_is_trusted_and_the_message_says_why(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _upload_folder_of_the_user(root)
    _owners(monkeypatch, root, {root / "upload": USER}, home="upload")
    _no_probe_files(monkeypatch)

    with pytest.raises(Exception, match="ließ sich nicht feststellen"):
        await source.open_write(PurePosixPath("/upload/datei"), _stream(b"neu"))

    assert not (root / "upload" / "datei").exists()


@pytest.mark.asyncio
async def test_link_message_says_why_only_root_is_trusted(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "echt.txt").write_bytes(b"alt")
    (root / "kurz.txt").symlink_to("echt.txt")
    _owners(monkeypatch, root, {root / "kurz.txt": USER}, home="")
    _no_probe_files(monkeypatch)

    with pytest.raises(Exception, match="Verknüpfung.*ließ sich nicht feststellen"):
        await source.open_write(PurePosixPath("/kurz.txt"), _stream(b"neu"))

    assert (root / "echt.txt").read_bytes() == b"alt"


@pytest.mark.asyncio
async def test_unknown_login_account_still_writes_into_folders_of_root(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _upload_folder_of_the_user(root)
    _owners(monkeypatch, root, {}, home="")
    _no_probe_files(monkeypatch)

    await source.open_write(PurePosixPath("/upload/datei"), _stream(b"neu"))

    assert (root / "upload" / "datei").read_bytes() == b"neu"


@pytest.mark.asyncio
async def test_probe_without_owner_does_not_trust_folders_without_owner(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _upload_folder_of_the_user(root)
    _owners(monkeypatch, root, {root / "upload": None}, home="")
    fstat = asyncssh.SFTPServer.fstat

    def without_owner(self, file_obj):
        attrs = fstat(self, file_obj)
        attrs.uid = attrs.gid = None
        return attrs

    monkeypatch.setattr(asyncssh.SFTPServer, "fstat", without_owner)

    with pytest.raises(Exception, match="Ordner"):
        await source.open_write(PurePosixPath("/upload/datei"), _stream(b"neu"))

    assert not (root / "upload" / "datei").exists()


@pytest.mark.asyncio
async def test_probe_file_is_created_new_and_never_through_an_existing_entry(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    import nodvard_deck_ext_terminal as terminal

    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    _upload_folder_of_the_user(root)
    (root / "geheim").write_bytes(b"geheim")
    (root / "tmp" / ".nodvard-konto-feste").symlink_to("../geheim")
    _owners(monkeypatch, root, {root / "upload": USER}, home="", login=USER)
    monkeypatch.setattr(terminal.secrets, "token_hex", lambda _n: "feste")

    await source.open_write(PurePosixPath("/upload/datei"), _stream(b"neu"))

    assert (root / "geheim").read_bytes() == b"geheim"
    assert (root / "tmp" / ".nodvard-konto-feste").is_symlink()
    assert (root / "upload" / "datei").read_bytes() == b"neu"


@pytest.mark.asyncio
async def test_login_uid_is_read_from_a_new_file_on_the_real_server(local_ssh_server):
    import nodvard_deck_ext_terminal as terminal

    address, port, username, password, root = local_ssh_server
    async with asyncssh.connect(
        address, port=port, username=username, password=password, known_hosts=None
    ) as conn, conn.start_sftp_client() as sftp:
        assert await terminal._login_uid(sftp) == os.getuid()

    assert _leftovers(root) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("link", ["wichtig.conf/.", "wichtig.conf/", "wichtig.conf//", "wichtig.conf/./"])
async def test_link_demanding_a_folder_does_not_replace_a_file(
    link, local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "data").mkdir()
    (root / "data" / "wichtig.conf").write_bytes(b"alt")
    (root / "data" / "upload").symlink_to(link)
    _one_hop_readlink(monkeypatch)

    with pytest.raises(Exception, match="kein Ordner"):
        await source.open_write(PurePosixPath("/data/upload"), _stream(b"neu"))

    assert (root / "data" / "wichtig.conf").read_bytes() == b"alt"
    assert (root / "data" / "upload").is_symlink()
    assert _leftovers(root) == []


@pytest.mark.asyncio
async def test_link_demanding_a_missing_folder_creates_no_file(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "data").mkdir()
    (root / "data" / "upload").symlink_to("fehlt/")
    _one_hop_readlink(monkeypatch)

    with pytest.raises(Exception, match="kein Ordner"):
        await source.open_write(PurePosixPath("/data/upload"), _stream(b"neu"))

    assert not (root / "data" / "fehlt").exists()
    assert _leftovers(root) == []


@pytest.mark.asyncio
async def test_link_demanding_a_folder_leads_into_that_folder(
    local_ssh_server, db_session, tmp_path, test_settings, monkeypatch
):
    source = await _source(db_session, test_settings, local_ssh_server, tmp_path)
    root = local_ssh_server[4]
    (root / "data" / "ablage").mkdir(parents=True)
    (root / "data" / "upload").symlink_to("ablage/.")
    _one_hop_readlink(monkeypatch)

    await source.open_write(PurePosixPath("/data/upload/neu.txt"), _stream(b"neu"))

    assert (root / "data" / "ablage" / "neu.txt").read_bytes() == b"neu"
