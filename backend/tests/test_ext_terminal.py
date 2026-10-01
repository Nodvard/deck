"""terminal-Extension: ActionExecutor, TerminalTarget, FileSource -- gegen einen
echten lokalen SSH-Server (docs/02-EXTENSION-API.md §3). Die REFERENZ-Extension fuer
die Ausfuehrungs-Seite, so wie hello-world die Referenz fuer den reinen
Host-Mechanismus ist.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest
from nodvard_sdk import Actor, ActorType, ExtensionManifest
from nodvard_sdk.actions import ActionRequest
from nodvard_sdk.capabilities import ActionExecutor, TerminalTarget

from nodvard_deck.ext.context import build_context
from nodvard_deck.ext.runtime import ExtensionRuntime, LoadedExtension
from nodvard_deck.services import hosts as hosts_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _terminal_extension_class():
    src_dir = REPO_EXTENSIONS_DIR / "terminal" / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    import nodvard_deck_ext_terminal

    return nodvard_deck_ext_terminal.Extension


async def _setup_terminal_extension(tmp_path, settings, permissions):
    manifest = ExtensionManifest(
        id="terminal", name="Terminal", version="0.1.0", api_version="0.1",
        entrypoint="nodvard_deck_ext_terminal:Extension", permissions=permissions,
    )
    runtime = ExtensionRuntime()
    loaded = LoadedExtension(manifest=manifest, instance=None, ctx=None, granted_permissions=permissions)
    ctx = build_context(runtime, loaded, manifest, permissions, tmp_path / "data", settings)
    loaded.ctx = ctx

    extension = _terminal_extension_class()()
    await extension.setup(ctx)
    return runtime, ctx, extension


@pytest.fixture
def settings_bound(test_settings):
    """`ctx.exec`/`ctx.secrets` bekommen die Settings seit dem D-12-Nachbar-Fix ueber
    `build_context()` explizit uebergeben (nicht mehr ueber den globalen
    `get_settings()`-Singleton) -- dieses Fixture existiert nur noch, damit die
    Testfunktionen unten nicht umbenannt werden muessen."""
    return test_settings


async def _make_host_with_credential(db_session, settings_bound, local_ssh_server):
    host_addr, port, username, password, sftp_root = local_ssh_server
    host = await hosts_service.create_host(db_session, name="test-host", address=host_addr)
    await hosts_service.add_credential(
        db_session, settings_bound, host_id=host.id, kind="ssh_password",
        username=username, port=port, secret_value=password,
    )
    await db_session.commit()
    return host, sftp_root


@pytest.mark.asyncio
async def test_setup_registers_shell_exec_action_spec_with_command_field(tmp_path, db_session, settings_bound):
    """Ohne diese Registrierung wuesste das Gate bei `ctx.actions.propose()`
    nicht, welches Payload-Feld gegen die Sperrliste zu pruefen ist -- `shell.exec`
    ist der erste (und bislang einzige) echte Aufrufer dieses Vertrags."""
    runtime, _ctx, _ext = await _setup_terminal_extension(tmp_path, settings_bound, ["hosts.read", "hosts.execute"])
    entry = runtime.actions.get("shell.exec")
    assert entry is not None
    ext_id, spec = entry
    assert ext_id == "terminal"
    assert spec.command_field == "command"
    assert spec.host_bound is True
    assert "hosts.execute" in spec.permissions


@pytest.mark.asyncio
async def test_propose_shell_exec_with_dangerous_command_is_denied_before_touching_ssh(
    tmp_path, db_session, settings_bound
):
    """Ende-zu-Ende gegen die ECHTE terminal-Extension (nicht nur gegen den
    ActionExecutor direkt wie oben): `ctx.actions.propose()` muss die Sperrliste ueber
    das von `setup()` registrierte `command_field` finden, OHNE dass ein Host oder ein
    lokaler SSH-Server existiert -- das Gate lehnt ab, bevor `ctx.exec` je aufgerufen
    wird."""
    _runtime, ctx, _ext = await _setup_terminal_extension(tmp_path, settings_bound, ["hosts.read", "hosts.execute"])

    decision = await ctx.actions.propose(
        ActionRequest(
            action_type="shell.exec",
            host_ref="host-does-not-need-to-exist",
            payload={"command": "rm -rf /"},
            proposed_by=Actor(type=ActorType.AI, id="test-model"),
            reason="Testvorschlag",
        )
    )
    assert decision.outcome.value == "deny"
    assert decision.rule.startswith("deny_pattern:")


@pytest.mark.asyncio
async def test_action_executor_runs_shell_exec(local_ssh_server, db_session, tmp_path, settings_bound):
    host, _ = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)

    runtime, ctx, _ext = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    executor = runtime.capabilities.query(ActionExecutor)[0]
    assert executor.action_types == frozenset({"shell.exec"})

    request = ActionRequest(
        action_type="shell.exec", host_ref=host.id, payload={"command": "echo hallo"},
        proposed_by=Actor(type=ActorType.EXTENSION, id="terminal"), reason="Test",
    )
    result = await executor.execute(request)
    assert result.success is True
    assert result.exit_code == 0
    assert result.output == "ran:echo hallo\n"


@pytest.mark.asyncio
async def test_action_executor_allows_long_running_commands():
    """`ctx.exec.run()` bricht nach der Vorgabe von 60 s ab -- ein
    `apt-get upgrade` oder `docker compose pull` von der Server-Seite stand danach auf
    „fehlgeschlagen – Zeitüberschreitung“, obwohl er auf dem Server noch lief."""
    from types import SimpleNamespace

    from nodvard_sdk.types import ExecResult

    _terminal_extension_class()
    from nodvard_deck_ext_terminal import _ShellActionExecutor

    calls: list[dict] = []

    async def fake_get(host_ref):
        return SimpleNamespace(id=host_ref, name="pi")

    async def fake_run(host, command, **kwargs):
        calls.append({"command": command, **kwargs})
        return ExecResult(exit_code=0, stdout="fertig\n", stderr="", duration_ms=5)

    ctx = SimpleNamespace(hosts=SimpleNamespace(get=fake_get), exec=SimpleNamespace(run=fake_run))
    executor = _ShellActionExecutor(ctx)
    request = ActionRequest(
        action_type="shell.exec", host_ref="h-pi", payload={"command": "apt-get -y upgrade"},
        proposed_by=Actor(type=ActorType.USER, id="u1"), reason="Test",
    )
    result = await executor.execute(request)

    assert result.success is True
    assert calls == [{"command": "apt-get -y upgrade", "timeout_s": 15 * 60}]


@pytest.mark.asyncio
async def test_action_executor_missing_command_fails_cleanly(local_ssh_server, db_session, tmp_path, settings_bound):
    host, _ = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    runtime, ctx, _ext = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    executor = runtime.capabilities.query(ActionExecutor)[0]

    request = ActionRequest(
        action_type="shell.exec", host_ref=host.id, payload={},
        proposed_by=Actor(type=ActorType.EXTENSION, id="terminal"), reason="Test",
    )
    result = await executor.execute(request)
    assert result.success is False
    assert "command" in (result.error or "")


@pytest.mark.asyncio
async def test_terminal_target_open_read_write_close_roundtrip(
    local_ssh_server, db_session, tmp_path, settings_bound
):
    host, _ = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    runtime, ctx, _ext = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    target = runtime.capabilities.query(TerminalTarget)[0]

    from nodvard_deck.services.hosts import host_to_sdk
    from nodvard_deck.models import Host as HostModel
    from nodvard_deck.db import refresh_relationships

    db_host = await db_session.get(HostModel, host.id)
    # host_to_sdk() liest jetzt auch host.credentials (fuer has_credential) --
    # `db_session.get()` auf ein bereits identity-gemapptes Objekt (aus
    # _make_host_with_credential(), nicht ueber eine select()-Query geladen) loest
    # das lazy="selectin" NIE aus, ein direkter synchroner Zugriff wuerde sonst
    # ausserhalb des Async-Kontexts scheitern (MissingGreenlet).
    await refresh_relationships(db_session, db_host, "credentials")
    sdk_host = host_to_sdk(db_host)

    assert await target.can_open(sdk_host) is True
    session = await target.open(sdk_host, user=None, cols=80, rows=24)
    try:
        chunks = []
        async for chunk in session.read():
            chunks.append(chunk)
            if b"shell-ready" in b"".join(chunks):
                break
        assert b"shell-ready" in b"".join(chunks)

        await session.write(b"hallo\n")
        chunks2 = []
        async for chunk in session.read():
            chunks2.append(chunk)
            if b"echo:hallo" in b"".join(chunks2):
                break
        assert b"echo:hallo" in b"".join(chunks2)

        await session.resize(120, 40)  # darf nicht werfen
        await session.write(b"exit\n")
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_file_source_sftp_roundtrip(local_ssh_server, db_session, tmp_path, settings_bound):
    host, sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    _runtime, _ctx, extension = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    source = extension.file_source_for(host.id)
    assert source.caps.write is True

    async def _content():
        yield b"hallo-inhalt"

    entry = await source.open_write(PurePosixPath("test.txt"), _content())
    assert entry.name == "test.txt"
    assert (sftp_root / "test.txt").read_bytes() == b"hallo-inhalt"

    listing = await source.list_dir(PurePosixPath("."))
    assert any(e.name == "test.txt" for e in listing.items)

    stat_entry = await source.stat(PurePosixPath("test.txt"))
    assert stat_entry.size == len(b"hallo-inhalt")

    chunks = []
    async for chunk in source.open_read(PurePosixPath("test.txt")):
        chunks.append(chunk)
    assert b"".join(chunks) == b"hallo-inhalt"

    renamed = await source.rename(PurePosixPath("test.txt"), PurePosixPath("renamed.txt"))
    assert renamed.name == "renamed.txt"

    await source.remove(PurePosixPath("renamed.txt"))
    assert not (sftp_root / "renamed.txt").exists()

    info = await source.info()
    assert info.healthy is True


@pytest.mark.skipif(sys.platform == "win32", reason="symbolische Links brauchen unter Windows Adminrechte")
@pytest.mark.asyncio
async def test_file_source_lists_folder_with_dead_symlink(local_ssh_server, db_session, tmp_path, settings_bound):
    """Ein toter Symlink (z. B. /lib/modules/<ver>/build ohne Header) liess
    `sftp.stat()` mit SFTPNoSuchFile scheitern -- der ganze Ordner war unlesbar (502),
    und die Suche uebersprang ihn still. Ausserdem blieb "Geaendert" immer leer."""
    import os
    from datetime import datetime, timezone

    host, sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    _runtime, _ctx, extension = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    source = extension.file_source_for(host.id)

    (sftp_root / "ok.conf").write_bytes(b"inhalt")
    os.utime(sftp_root / "ok.conf", (1_700_000_000, 1_700_000_000))
    (sftp_root / "docs").mkdir()
    (sftp_root / "kaputter-link").symlink_to("gibt-es-nicht")
    (sftp_root / "docs-link").symlink_to("docs")

    listing = await source.list_dir(PurePosixPath("."))
    by_name = {e.name: e for e in listing.items}
    assert set(by_name) == {"ok.conf", "docs", "kaputter-link", "docs-link"}

    assert by_name["ok.conf"].is_dir is False
    assert by_name["ok.conf"].size == len(b"inhalt")
    assert by_name["ok.conf"].modified_at == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    assert by_name["docs"].is_dir is True
    # Link auf einen Ordner laesst sich weiter oeffnen, der tote Link bleibt sichtbar.
    assert by_name["docs-link"].is_dir is True
    assert by_name["docs-link"].metadata.get("symlink") is True
    assert by_name["kaputter-link"].is_dir is False
    assert by_name["kaputter-link"].size is None
    assert by_name["kaputter-link"].metadata == {"symlink": True, "broken": True}

    hits = [entry async for entry in source.search("ok.conf", root=PurePosixPath("."))]
    assert [h.path for h in hits] == ["ok.conf"]


@pytest.mark.skipif(sys.platform == "win32", reason="symbolische Links brauchen unter Windows Adminrechte")
@pytest.mark.asyncio
async def test_file_source_rename_of_dead_symlink_works(local_ssh_server, db_session, tmp_path, settings_bound):
    """Ein toter Link laesst sich umbenennen: `stat()` nach dem Umbenennen folgte dem
    Link und warf SFTPNoSuchFile (502), obwohl schon umbenannt war."""
    host, sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    _runtime, _ctx, extension = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    source = extension.file_source_for(host.id)
    (sftp_root / "kaputter-link").symlink_to("gibt-es-nicht")

    renamed = await source.rename(PurePosixPath("kaputter-link"), PurePosixPath("neuer-link"))

    assert renamed.name == "neuer-link"
    assert renamed.is_dir is False
    assert renamed.metadata == {"symlink": True, "broken": True}
    assert not (sftp_root / "kaputter-link").is_symlink()
    assert (sftp_root / "neuer-link").is_symlink()


@pytest.mark.asyncio
async def test_file_source_rename_to_missing_target_still_raises(
    local_ssh_server, db_session, tmp_path, settings_bound
):
    """Die Rueckfallebene fuer tote Links darf echte Fehler nicht verschlucken."""
    host, _sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    _runtime, _ctx, extension = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    source = extension.file_source_for(host.id)

    with pytest.raises(Exception):  # noqa: B017 - SFTP-Fehler (Quelle fehlt)
        await source.rename(PurePosixPath("gibt-es-nicht"), PurePosixPath("egal"))


@pytest.mark.skipif(sys.platform == "win32", reason="symbolische Links brauchen unter Windows Adminrechte")
@pytest.mark.asyncio
async def test_file_source_remove_folder_symlink_keeps_target(local_ssh_server, db_session, tmp_path, settings_bound):
    """Ein Link auf einen Ordner: `rmtree` lehnt Links ab ("must not be a symlink", 502).
    Auch mit `recursive=True` (so ruft ihn der Dateimanager fuer Ordner-Links auf) wird
    nur der Link entfernt, das Ziel bleibt."""
    host, sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    _runtime, _ctx, extension = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    source = extension.file_source_for(host.id)
    (sftp_root / "docs").mkdir()
    (sftp_root / "docs" / "a.txt").write_bytes(b"x")
    (sftp_root / "docs-link").symlink_to("docs")

    (sftp_root / "docs-link2").symlink_to("docs")

    await source.remove(PurePosixPath("docs-link"), recursive=True)
    await source.remove(PurePosixPath("docs-link2"), recursive=False)

    assert not (sftp_root / "docs-link").is_symlink()
    assert not (sftp_root / "docs-link2").is_symlink()
    assert (sftp_root / "docs" / "a.txt").read_bytes() == b"x"


@pytest.mark.asyncio
async def test_file_source_remove_recursive_still_removes_real_folder(
    local_ssh_server, db_session, tmp_path, settings_bound
):
    host, sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    _runtime, _ctx, extension = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    source = extension.file_source_for(host.id)
    (sftp_root / "ordner" / "unter").mkdir(parents=True)
    (sftp_root / "ordner" / "unter" / "a.txt").write_bytes(b"x")

    await source.remove(PurePosixPath("ordner"), recursive=True)

    assert not (sftp_root / "ordner").exists()


@pytest.mark.asyncio
async def test_file_source_search_finds_a_file_two_levels_deep(
    local_ssh_server, db_session, tmp_path, settings_bound
):
    """Dasselbe Muster wie nextclouds `search()`: `caps.search`
    war bisher `False` -- die generische Fan-out-Route `GET /files/search`
    existierte schon, aber SFTP beantwortete sie nicht. `beach.jpg` liegt zwei
    Ebenen unter der Wurzel -- ein Treffer hier beweist echte Rekursion ueber
    `listdir()`, nicht nur ein Scan der Wurzel selbst."""
    host, sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    _runtime, _ctx, extension = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    source = extension.file_source_for(host.id)
    assert source.caps.search is True

    (sftp_root / "docs").mkdir()
    (sftp_root / "docs" / "notes.txt").write_bytes(b"hallo")
    (sftp_root / "photos" / "vacation").mkdir(parents=True)
    (sftp_root / "photos" / "vacation" / "beach.jpg").write_bytes(b"jpegbytes")
    (sftp_root / "photos" / "receipt.pdf").write_bytes(b"pdfbytes")

    hits = [entry async for entry in source.search("beach", root=PurePosixPath("."))]
    assert len(hits) == 1
    assert hits[0].path == "photos/vacation/beach.jpg"


@pytest.mark.asyncio
async def test_file_source_search_is_case_insensitive_and_matches_substrings(
    local_ssh_server, db_session, tmp_path, settings_bound
):
    host, sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    _runtime, _ctx, extension = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    source = extension.file_source_for(host.id)

    (sftp_root / "docs").mkdir()
    (sftp_root / "docs" / "notes.txt").write_bytes(b"hallo")
    (sftp_root / "photos" / "vacation").mkdir(parents=True)
    (sftp_root / "photos" / "vacation" / "beach.jpg").write_bytes(b"jpegbytes")

    hits = [entry async for entry in source.search("BEACH", root=PurePosixPath("."))]
    assert {h.name for h in hits} == {"beach.jpg"}

    hits2 = [entry async for entry in source.search("not", root=PurePosixPath("."))]
    assert {h.name for h in hits2} == {"notes.txt"}


@pytest.mark.asyncio
async def test_file_source_search_finds_a_word_that_exists_only_in_file_content(
    local_ssh_server, db_session, tmp_path, settings_bound
):
    """Dasselbe Muster wie
    nextclouds Pendant: search() las bisher NUR Dateinamen. "quetzalcoatl" steht
    ausschliesslich im Inhalt von journal.log -- ein Treffer beweist, dass jetzt
    tatsaechlich Inhalte ueber dieselbe offene SFTP-Sitzung gelesen werden."""
    host, sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    _runtime, _ctx, extension = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    source = extension.file_source_for(host.id)

    (sftp_root / "photos").mkdir()
    (sftp_root / "photos" / "journal.log").write_bytes("Sah heute eine quetzalcoatl-Statue.".encode())
    # Dasselbe Wort auch im Inhalt einer BINAEREN Datei (Endung nicht in der
    # Text-Allowlist) -- darf NICHT gefunden werden.
    (sftp_root / "photos" / "receipt.pdf").write_bytes(b"pdfbytes quetzalcoatl")

    hits = [entry async for entry in source.search("quetzalcoatl", root=PurePosixPath("."))]
    assert [h.path for h in hits] == ["photos/journal.log"]


@pytest.mark.asyncio
async def test_file_source_search_returns_nothing_not_an_error_when_no_match(
    local_ssh_server, db_session, tmp_path, settings_bound
):
    host, _sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    _runtime, _ctx, extension = await _setup_terminal_extension(
        tmp_path, settings_bound, ["hosts.read", "hosts.execute"]
    )
    source = extension.file_source_for(host.id)

    hits = [entry async for entry in source.search("xyz-nichts-passt-hier", root=PurePosixPath("."))]
    assert hits == []


@pytest.mark.asyncio
async def test_file_source_provider_returns_one_source_per_host_with_credentials(
    local_ssh_server, db_session, tmp_path, settings_bound
):
    """`_TerminalFileSourceProvider.file_sources()` ist der einzige Zugang,
    den `GET /files/sources` (api/v1/files.py) tatsaechlich nutzt -- `file_source_for()`
    bleibt nur ein direkter Erweiterungspunkt fuer Aufrufer, die gezielt EINEN Host
    wollen (siehe Docstring dort).

    **Ohne Zugangsdaten:** ein zweiter Host OHNE
    SSH-Zugangsdaten tauchte frueher bewusst trotzdem als Quelle auf (die SDK-Sicht
    auf einen Host kannte Zugangsdaten nicht, docs/03 Paragraph 3 Invariante 1) --
    ihr erster echter Zugriff scheiterte sauber, aber erst NACH dem Klick. Seit
    `host.has_credential` filtert `file_sources()` das jetzt vorab:
    nur der Host MIT Zugangsdaten erscheint ueberhaupt."""
    host_with_creds, _sftp_root = await _make_host_with_credential(db_session, settings_bound, local_ssh_server)
    host_without_creds = await hosts_service.create_host(db_session, name="no-creds", address="10.0.0.9")
    await db_session.commit()

    _runtime, ctx, _ext = await _setup_terminal_extension(tmp_path, settings_bound, ["hosts.read", "hosts.execute"])
    from nodvard_deck_ext_terminal import _TerminalFileSourceProvider

    provider = _TerminalFileSourceProvider(ctx)

    sources = await provider.file_sources()
    assert {s.source_id for s in sources} == {f"ssh-sftp:{host_with_creds.id}"}
    assert f"ssh-sftp:{host_without_creds.id}" not in {s.source_id for s in sources}

    from pathlib import PurePosixPath

    with_creds_source = sources[0]
    listing = await with_creds_source.list_dir(PurePosixPath("."))
    assert listing.items == []  # frisches, leeres sftp_root


@pytest.mark.asyncio
async def test_two_hosts_with_credentials_get_distinct_labels(local_ssh_server, db_session, tmp_path, settings_bound):
    """Live gefunden (Boot-Test, weiterhin gueltig mit dem
    has_credential-Filter oben): ohne host-spezifisches Label zeigten mehrere
    SSH-Quellen in der UI identischen Text ("SSH (SFTP)"), ununterscheidbar."""
    host_addr, port, username, password, _sftp_root = local_ssh_server
    host_a = await hosts_service.create_host(db_session, name="host-a", display_name="Host A", address=host_addr)
    await hosts_service.add_credential(
        db_session, settings_bound, host_id=host_a.id, kind="ssh_password", username=username, port=port, secret_value=password,
    )
    host_b = await hosts_service.create_host(db_session, name="host-b", display_name="Host B", address=host_addr)
    await hosts_service.add_credential(
        db_session, settings_bound, host_id=host_b.id, kind="ssh_password", username=username, port=port, secret_value=password,
    )
    await db_session.commit()

    _runtime, ctx, _ext = await _setup_terminal_extension(tmp_path, settings_bound, ["hosts.read", "hosts.execute"])
    from nodvard_deck_ext_terminal import _TerminalFileSourceProvider

    sources = await (_TerminalFileSourceProvider(ctx)).file_sources()
    assert len(sources) == 2
    assert len({s.label for s in sources}) == 2
    assert all(s.label != "SSH (SFTP)" for s in sources)
